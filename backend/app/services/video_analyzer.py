"""
Lost Heaven — Frame-by-Frame Vision, Aesthetics & Emotion Analyzer (`video_analyzer.py`).

Combines:
  1. Hardware-fast FFmpeg scene cut & black/fade detection (`scdet=t=10,blackdetect=d=0.15:pix_th=0.10`)
  2. Downscaled (`640px` wide) OpenCV image quality metrics (`sharpness_score`, `exposure_score`,
     `action_score`, `composition_score`)
  3. Facial expression & eye-openness analysis (`MediaPipe FaceLandmarker` 478 landmarks + blendshapes,
     with OpenCV Haar face + eye-cascade & ocular contrast fallback)
"""

from __future__ import annotations

import logging
import math
import os
import re
import shutil
import subprocess
import tempfile
import threading
import time
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import cv2
import numpy as np

from app.config import settings
from app.services import face_tracker
from app.services.emotion_analyzer import analyze_facial_emotions
from app.services.proxy import get_ffmpeg_cmd, probe_video

logger = logging.getLogger("lost_heaven.video_analyzer")

_EYE_CASCADE_CACHE: Optional[Tuple[cv2.CascadeClassifier, cv2.CascadeClassifier]] = None
_MP_FULL_FAILED = False


def _get_eye_cascades() -> Tuple[cv2.CascadeClassifier, cv2.CascadeClassifier]:
    """Load OpenCV Haar eye cascades via ASCII temp directory so Windows Unicode paths never break XML reads."""
    global _EYE_CASCADE_CACHE
    if _EYE_CASCADE_CACHE is not None:
        return _EYE_CASCADE_CACHE

    cache_dir = Path(tempfile.gettempdir()) / "lost_heaven_mp_cache"
    cache_dir.mkdir(parents=True, exist_ok=True)
    casc_dir = Path(cv2.data.haarcascades)

    eye_std_dst = cache_dir / "haarcascade_eye.xml"
    eye_glass_dst = cache_dir / "haarcascade_eye_tree_eyeglasses.xml"
    if not eye_std_dst.exists():
        src = casc_dir / "haarcascade_eye.xml"
        if src.exists():
            shutil.copy2(str(src), str(eye_std_dst))
    if not eye_glass_dst.exists():
        src = casc_dir / "haarcascade_eye_tree_eyeglasses.xml"
        if src.exists():
            shutil.copy2(str(src), str(eye_glass_dst))

    c_std = cv2.CascadeClassifier(str(eye_std_dst))
    c_glass = cv2.CascadeClassifier(str(eye_glass_dst))
    _EYE_CASCADE_CACHE = (c_std, c_glass)
    return _EYE_CASCADE_CACHE


def _create_mediapipe_landmarker():
    """Create a reusable MediaPipe Tasks FaceLandmarker if `face_landmarker.task` is present."""
    global _MP_FULL_FAILED
    if _MP_FULL_FAILED:
        return None

    model_path = Path(settings.STORAGE_DIR) / "models" / "face_landmarker.task"
    if not model_path.exists():
        return None

    try:
        import mediapipe as mp
        from mediapipe.tasks import python as mp_python
        from mediapipe.tasks.python import vision as mp_vision

        cache_dir = Path(tempfile.gettempdir()) / "lost_heaven_mp_cache"
        cache_dir.mkdir(parents=True, exist_ok=True)
        ascii_model = cache_dir / "face_landmarker.task"
        if not ascii_model.exists() or ascii_model.stat().st_size != model_path.stat().st_size:
            shutil.copy2(str(model_path), str(ascii_model))

        base_options = mp_python.BaseOptions(model_asset_path=str(ascii_model))
        options = mp_vision.FaceLandmarkerOptions(
            base_options=base_options,
            output_face_blendshapes=True,
            num_faces=4,
            min_face_detection_confidence=0.45,
        )
        return mp, mp_vision.FaceLandmarker.create_from_options(options)
    except Exception as e:
        logger.debug(f"MediaPipe FaceLandmarker init failed, using Haar fallback: {e}")
        _MP_FULL_FAILED = True
        return None


def _run_ffmpeg_scdet_blackdetect(
    video_path: str,
    start_time: float = 0.0,
    duration: Optional[float] = None,
) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]]]:
    """
    Fast FFmpeg `scale=640:-2,scdet=t=10,blackdetect=d=0.15:pix_th=0.10` pass.
    Returns `(scene_cuts, black_intervals)` with absolute video timestamps.
    """
    ffmpeg = get_ffmpeg_cmd()
    abs_video = str(Path(video_path).resolve())
    work_dir = Path(tempfile.mkdtemp(prefix="lost_heaven_vid_"))
    cuts: List[Dict[str, Any]] = []
    blacks: List[Dict[str, Any]] = []
    eff_start = max(0.0, float(start_time or 0.0))

    try:
        cmd = [ffmpeg, "-hide_banner", "-an"]
        if eff_start > 0.0:
            cmd.extend(["-ss", f"{eff_start:.3f}"])
        if duration is not None and duration > 0.1:
            cmd.extend(["-t", f"{duration:.3f}"])
        cmd.extend([
            "-i", abs_video,
            "-vf", "scale=640:-2,scdet=t=10,blackdetect=d=0.15:pix_th=0.10",
            "-f", "null", "-",
        ])
        proc = subprocess.run(cmd, cwd=str(work_dir), capture_output=True, text=True, timeout=180)
        stderr = proc.stderr or ""

        for m in re.finditer(r"lavfi\.scd\.score:\s*([\d.]+),\s*lavfi\.scd\.time:\s*([\d.]+)", stderr):
            sc_score = float(m.group(1))
            sc_time = round(eff_start + float(m.group(2)), 3)
            if not cuts or abs(sc_time - cuts[-1]["t"]) >= 0.25:
                cuts.append({"t": sc_time, "score": round(sc_score, 2)})

        for mb in re.finditer(r"black_start:([\d.]+)\s+black_end:([\d.]+)\s+black_duration:([\d.]+)", stderr):
            bs = round(eff_start + float(mb.group(1)), 3)
            be = round(eff_start + float(mb.group(2)), 3)
            bd = round(float(mb.group(3)), 3)
            blacks.append({"start": bs, "end": be, "duration": bd})
    except Exception as e:
        logger.debug(f"FFmpeg scdet/blackdetect failed: {e}")
    finally:
        shutil.rmtree(work_dir, ignore_errors=True)

    return cuts, blacks


def compute_frame_sharpness_and_exposure(frame_bgr: np.ndarray) -> Tuple[float, float]:
    """
    Compute `(sharpness_score 0..100, exposure_score 0..100)` on a decoded frame.
    Penalizes blur via Laplacian variance + top-5% Sobel edge energy, and
    crushed blacks (<18 mean luma) / blown highlights (>240 mean luma).
    """
    gray = cv2.cvtColor(frame_bgr, cv2.COLOR_BGR2GRAY)
    lap = cv2.Laplacian(gray, cv2.CV_64F)
    lap_var = float(lap.var())
    abs_lap = np.abs(lap)

    gx = cv2.Sobel(gray, cv2.CV_32F, 1, 0, ksize=3)
    gy = cv2.Sobel(gray, cv2.CV_32F, 0, 1, ksize=3)
    edge_mag = np.sqrt(gx * gx + gy * gy)
    lap_steep = float(np.percentile(abs_lap, 99.8))
    edge_steep = float(np.percentile(edge_mag, 99.8))

    lap_var_norm = min(1.0, math.log1p(lap_var) / math.log1p(750.0))
    lap_steep_norm = min(1.0, lap_steep / 115.0)
    edge_norm = min(1.0, edge_steep / 215.0)
    sharpness = round(100.0 * (0.22 * lap_var_norm + 0.42 * lap_steep_norm + 0.36 * edge_norm), 1)

    mean_luma = float(np.mean(gray))
    p02, p98 = np.percentile(gray, [2, 98])
    dyn_span = float(p98 - p02)

    if mean_luma < 18.0:
        exposure = round(max(0.0, (mean_luma / 18.0) * 22.0), 1)
    elif mean_luma > 240.0:
        exposure = round(max(0.0, ((255.0 - mean_luma) / 15.0) * 22.0), 1)
    else:
        # Optimal brightness centered around ~120
        dist_center = abs(mean_luma - 122.0) / 118.0
        tone_score = max(0.0, 1.0 - (dist_center ** 1.35) * 0.72)
        contrast_score = min(1.0, dyn_span / 80.0)
        exposure = round(100.0 * (0.76 * tone_score + 0.24 * contrast_score), 1)

    return sharpness, exposure


def _compute_optical_flow_action(
    curr_gray: np.ndarray,
    prev_gray: Optional[np.ndarray],
) -> Tuple[float, float, float]:
    """Compute `(action_score 0..100, salient_cx, salient_cy)` via Farneback optical flow + Sobel edges."""
    h, w = curr_gray.shape[:2]
    tw = min(160, w)
    th = max(24, int(round(h * tw / max(1, w))))
    tiny_curr = cv2.resize(curr_gray, (tw, th), interpolation=cv2.INTER_AREA)

    gx = cv2.Sobel(tiny_curr, cv2.CV_32F, 1, 0, ksize=3)
    gy = cv2.Sobel(tiny_curr, cv2.CV_32F, 0, 1, ksize=3)
    edge_mag = np.sqrt(gx * gx + gy * gy)
    edge_score = min(1.0, float(np.mean(edge_mag)) / 55.0)

    motion_score = 0.0
    salient_map = edge_mag
    if prev_gray is not None and prev_gray.shape == curr_gray.shape:
        tiny_prev = cv2.resize(prev_gray, (tw, th), interpolation=cv2.INTER_AREA)
        diff = np.abs(tiny_curr.astype(np.float32) - tiny_prev.astype(np.float32))
        diff_val = 0.5 * float(np.mean(diff)) + 0.5 * float(np.percentile(diff, 92))
        diff_score = min(1.0, diff_val / 52.0)
        flow_score = 0.0
        sal_motion = diff
        try:
            flow = cv2.calcOpticalFlowFarneback(
                tiny_prev, tiny_curr, None,
                pyr_scale=0.5, levels=2, winsize=9, iterations=2, poly_n=5, poly_sigma=1.1, flags=0,
            )
            mag = np.sqrt(flow[:, :, 0] ** 2 + flow[:, :, 1] ** 2)
            # Peak 90th percentile catches fast hand/character action without whole-frame camera requirement
            motion_val = 0.55 * float(np.mean(mag)) + 0.45 * float(np.percentile(mag, 90))
            flow_score = min(1.0, motion_val / 4.2)
            sal_motion = np.maximum(diff, mag * 28.0)
        except Exception:
            pass
        motion_score = max(flow_score, diff_score)
        salient_map = 0.65 * sal_motion + 0.35 * edge_mag

    action_score = round(100.0 * (0.76 * motion_score + 0.24 * edge_score), 1)
    total = float(np.sum(salient_map))
    if total > 1e-3:
        yy, xx = np.mgrid[0:th, 0:tw]
        scx = round(max(0.1, min(0.9, float(np.sum(xx * salient_map) / total) / max(1, tw))), 4)
        scy = round(max(0.1, min(0.9, float(np.sum(yy * salient_map) / total) / max(1, th))), 4)
    else:
        scx, scy = 0.5, 0.45
    return action_score, scx, scy


def _evaluate_haar_eyes_and_expression(
    frame_bgr: np.ndarray,
    frame_gray: np.ndarray,
    faces: List[Dict[str, float]],
    prev_mouth_rois: List[Tuple[float, float, np.ndarray]],
) -> Tuple[Optional[Dict[str, float]], Dict[str, float], float, float, float, List[Tuple[float, float, np.ndarray]]]:
    """
    OpenCV Haar face + eye cascade & ocular contrast analyzer when MediaPipe `face_landmarker.task`
    is unavailable or misses a synthetic portrait.
    """
    blend = {
        "jawOpen": 0.0,
        "mouthSmileLeft": 0.0,
        "mouthSmileRight": 0.0,
        "mouthFrownLeft": 0.0,
        "mouthFrownRight": 0.0,
        "browInnerUp": 0.0,
        "browDownLeft": 0.0,
        "browDownRight": 0.0,
        "browOuterUpLeft": 0.0,
        "browOuterUpRight": 0.0,
        "cheekSquintLeft": 0.0,
        "cheekSquintRight": 0.0,
        "eyeWideLeft": 0.0,
        "eyeWideRight": 0.0,
        "eyeBlinkLeft": 0.0,
        "eyeBlinkRight": 0.0,
        "noseSneerLeft": 0.0,
        "noseSneerRight": 0.0,
        "mouthPressLeft": 0.0,
        "mouthPressRight": 0.0,
        "mouthStretchLeft": 0.0,
        "mouthStretchRight": 0.0,
    }
    if not faces:
        return None, blend, 1.0, 0.0, 50.0, []

    scored, next_rois = face_tracker._score_faces_mouth_activity(frame_gray, faces, prev_mouth_rois)
    prim = max(scored, key=lambda f: float(f.get("w", 0.1)) * float(f.get("h", 0.1)) * (1.0 + 0.4 * float(f.get("mouth_activity", 0.0))))

    h, w = frame_gray.shape[:2]
    fx = max(0, int(round((prim["cx"] - prim["w"] * 0.5) * w)))
    fy = max(0, int(round((prim["cy"] - prim["h"] * 0.5) * h)))
    fw = max(24, int(round(prim["w"] * w)))
    fh = max(24, int(round(prim["h"] * h)))
    fx2 = min(w, fx + fw)
    fy2 = min(h, fy + fh)

    face_gray = frame_gray[fy:fy2, fx:fx2]
    upper_face = face_gray[: max(12, int(face_gray.shape[0] * 0.58)), :]

    # Tier A: Haar eye detectors
    eye_count = 0
    if upper_face.size > 0 and min(upper_face.shape[:2]) >= 16:
        c_std, c_glass = _get_eye_cascades()
        try:
            eyes1 = c_glass.detectMultiScale(upper_face, scaleFactor=1.08, minNeighbors=3, minSize=(8, 8))
            eyes2 = c_std.detectMultiScale(upper_face, scaleFactor=1.08, minNeighbors=3, minSize=(8, 8)) if len(eyes1) < 2 else []
            eye_count = max(len(eyes1), len(eyes2))
        except Exception:
            eye_count = 0

    # Tier B: Sclera-iris ocular pocket check below eyebrow ridge (bright sclera > cheek skin + pupil contrast)
    ocular_open_score = 0.50
    if upper_face.shape[0] >= 16 and upper_face.shape[1] >= 24 and face_gray.shape[0] >= 16:
        uh, uw = upper_face.shape[:2]
        cheek_roi = face_gray[int(face_gray.shape[0] * 0.50) : int(face_gray.shape[0] * 0.72), int(face_gray.shape[1] * 0.18) : int(face_gray.shape[1] * 0.36)]
        skin_med = float(np.median(cheek_roi)) if cheek_roi.size >= 8 else float(np.median(face_gray))
        left_pock = upper_face[int(uh * 0.48) : int(uh * 0.84), int(uw * 0.18) : int(uw * 0.44)]
        right_pock = upper_face[int(uh * 0.48) : int(uh * 0.84), int(uw * 0.56) : int(uw * 0.82)]
        pock_scores = []
        for pock in (left_pock, right_pock):
            if pock.size >= 12:
                p_low, p_high = np.percentile(pock, [8, 96])
                sclera_lift = min(1.0, max(0.0, (float(p_high) - skin_med - 4.0) / 26.0))
                iris_drop = min(1.0, max(0.0, (skin_med - float(p_low) - 10.0) / 65.0))
                pock_scores.append(0.68 * sclera_lift + 0.32 * iris_drop)
        if pock_scores:
            ocular_open_score = float(sum(pock_scores) / len(pock_scores))

    if eye_count >= 2:
        eyes_open = round(max(0.84, min(0.99, 0.72 + 0.28 * ocular_open_score)), 4)
    elif eye_count == 1:
        eyes_open = round(max(0.68, min(0.90, 0.55 + 0.35 * ocular_open_score)), 4)
    else:
        eyes_open = round(max(0.18, min(0.90, 0.16 + 0.78 * ocular_open_score)), 4)

    blink_val = round(max(0.0, min(1.0, 1.0 - eyes_open)), 4)
    blend["eyeBlinkLeft"] = blink_val
    blend["eyeBlinkRight"] = blink_val
    blend["jawOpen"] = round(max(0.0, min(1.0, float(prim.get("mouth_activity", 0.0)))), 4)

    # Smile & furrow heuristics from facial zones
    lower_face = face_gray[int(face_gray.shape[0] * 0.58) :, :] if face_gray.shape[0] >= 24 else face_gray
    smile_est = 0.0
    if lower_face.size >= 16:
        gx_m = cv2.Sobel(lower_face, cv2.CV_32F, 1, 0, ksize=3)
        smile_est = round(min(1.0, float(np.percentile(np.abs(gx_m), 90)) / 140.0) * 0.55, 4)
    blend["mouthSmileLeft"] = smile_est
    blend["mouthSmileRight"] = smile_est
    blend["cheekSquintLeft"] = round(smile_est * 0.65, 4)
    blend["cheekSquintRight"] = round(smile_est * 0.65, 4)
    blend["eyeWideLeft"] = round(max(0.0, eyes_open - 0.7), 4)
    blend["eyeWideRight"] = blend["eyeWideLeft"]
    if upper_face.size >= 36:
        glab = upper_face[int(upper_face.shape[0] * 0.22):int(upper_face.shape[0] * 0.52), int(upper_face.shape[1] * 0.35):int(upper_face.shape[1] * 0.65)]
        if glab.size >= 12:
            gx_b = cv2.Sobel(glab, cv2.CV_32F, 1, 0, ksize=3)
            furrow = min(0.65, float(np.mean(np.abs(gx_b))) / 65.0)
            blend["browDownLeft"] = round(furrow, 4)
            blend["browDownRight"] = round(furrow, 4)
    return prim, blend, eyes_open, round(float(max(0.0, min(100.0, 100.0 * (0.4 * blend["jawOpen"] + 0.3 * smile_est + 0.3 * eyes_open)))), 1), round(max(35.0, min(100.0, 100.0 - abs(float(prim.get("cx", 0.5)) - 0.5) * 75.0)), 1), next_rois


def _evaluate_mediapipe_face(
    mp_pack: Any,
    frame_bgr: np.ndarray,
) -> Optional[Tuple[int, Dict[str, float], Dict[str, float], float, float, float, Any]]:
    """Use MediaPipe FaceLandmarker 478 landmarks + all 52 ARKit blendshapes when model exists."""
    if mp_pack is None:
        return None
    mp, landmarker = mp_pack
    try:
        rgb = cv2.cvtColor(frame_bgr, cv2.COLOR_BGR2RGB)
        mp_img = mp.Image(image_format=mp.ImageFormat.SRGB, data=rgb)
        res = landmarker.detect(mp_img)
        lms_list = res.face_landmarks or []
        if not lms_list:
            return None
        b_list = res.face_blendshapes or []

        # Primary largest face
        best_idx = 0
        best_area = -1.0
        boxes: List[Dict[str, float]] = []
        for idx, lms in enumerate(lms_list):
            xs = [float(p.x) for p in lms]
            ys = [float(p.y) for p in lms]
            x0, x1 = max(0.0, min(xs)), min(1.0, max(xs))
            y0, y1 = max(0.0, min(ys)), min(1.0, max(ys))
            fw = max(0.02, x1 - x0)
            fh = max(0.02, y1 - y0)
            area = fw * fh
            boxes.append({"cx": x0 + fw * 0.5, "cy": y0 + fh * 0.5, "w": fw, "h": fh})
            if area > best_area:
                best_area = area
                best_idx = idx

        b_map: Dict[str, float] = {
            "jawOpen": 0.0, "mouthSmileLeft": 0.0, "mouthSmileRight": 0.0,
            "browInnerUp": 0.0, "eyeWideLeft": 0.0, "eyeWideRight": 0.0,
            "eyeBlinkLeft": 0.0, "eyeBlinkRight": 0.0,
        }
        if best_idx < len(b_list):
            for cat in b_list[best_idx]:
                cname = getattr(cat, "category_name", "")
                if cname:
                    b_map[cname] = round(float(getattr(cat, "score", 0.0)), 4)

        eyes_open = round(max(0.0, min(1.0, 1.0 - max(b_map["eyeBlinkLeft"], b_map["eyeBlinkRight"]))), 4)
        lms_p = lms_list[best_idx]
        # Direct gaze proxy from nose bridge center vs outer eye symmetry
        gaze_score = 88.0
        if len(lms_p) > 263:
            lx = float(lms_p[33].x)
            rx = float(lms_p[263].x)
            nx = float(lms_p[1].x)
            eye_mid = 0.5 * (lx + rx)
            asym = abs(nx - eye_mid) / max(0.02, abs(rx - lx))
            gaze_score = round(max(25.0, min(100.0, 100.0 - asym * 85.0)), 1)

        smile = 0.5 * (b_map["mouthSmileLeft"] + b_map["mouthSmileRight"])
        eye_wide = 0.5 * (b_map["eyeWideLeft"] + b_map["eyeWideRight"])
        expr = 0.36 * b_map["jawOpen"] + 0.30 * smile + 0.18 * b_map["browInnerUp"] + 0.16 * eye_wide
        emotion_intensity = round(max(0.0, min(100.0, (expr * 92.0 + (gaze_score / 100.0) * 32.0) * (0.45 + 0.55 * eyes_open))), 1)
        return len(lms_list), boxes[best_idx], b_map, eyes_open, emotion_intensity, gaze_score, lms_p
    except Exception:
        return None


def classify_shot_scale(
    face_count: int,
    primary_face: Optional[Dict[str, float]],
    action_score: float = 0.0,
) -> str:
    """
    Classify framing shot scale into `closeup_reaction`, `talking_head`,
    `dual_speaker`, or `wide_gameplay`.
    """
    if int(face_count or 0) >= 2:
        return "dual_speaker"
    if not primary_face or int(face_count or 0) <= 0:
        return "wide_gameplay"
    fw = float(primary_face.get("w", 0.0))
    fh = float(primary_face.get("h", 0.0))
    cx = float(primary_face.get("cx", 0.5))
    cy = float(primary_face.get("cy", 0.45))
    area = fw * fh
    in_corner = (cx < 0.25 or cx > 0.75) and (cy < 0.30 or cy > 0.70)
    if in_corner and area < 0.055:
        return "wide_gameplay"
    if area >= 0.090 or fh >= 0.32:
        return "closeup_reaction"
    if area >= 0.012 or fh >= 0.14:
        return "talking_head"
    return "wide_gameplay"


def compute_graphic_event_score(
    curr_gray: np.ndarray,
    prev_gray: Optional[np.ndarray],
    *,
    is_cut: bool = False,
) -> float:
    """
    Detect sudden on-screen text overlays, meme banners, kill-feeds, or HUD pop-ups
    using localized upper/center ROI edge & luminance delta compared to prior frame.
    """
    if prev_gray is None or prev_gray.shape != curr_gray.shape or is_cut:
        return 0.0
    h, w = curr_gray.shape[:2]
    if h < 24 or w < 32:
        return 0.0

    # Inspect upper banner band (5%..32%) and central card zone (34%..78%)
    zones = [
        (int(h * 0.05), int(h * 0.32), int(w * 0.10), int(w * 0.90)),
        (int(h * 0.34), int(h * 0.78), int(w * 0.15), int(w * 0.85)),
    ]
    best_zone_score = 0.0
    for y0, y1, x0, x1 in zones:
        if y1 <= y0 + 4 or x1 <= x0 + 4:
            continue
        c_roi = curr_gray[y0:y1, x0:x1]
        p_roi = prev_gray[y0:y1, x0:x1]

        luma_diff = np.abs(c_roi.astype(np.float32) - p_roi.astype(np.float32))
        p95_luma = float(np.percentile(luma_diff, 95))
        mean_luma = float(np.mean(luma_diff))
        # Sharp text/box popup has high localized p95 change while rest of background is relatively calmer
        localization = min(1.0, p95_luma / 85.0)

        gx_c = cv2.Sobel(c_roi, cv2.CV_32F, 1, 0, ksize=3)
        gy_c = cv2.Sobel(c_roi, cv2.CV_32F, 0, 1, ksize=3)
        edge_c = np.sqrt(gx_c * gx_c + gy_c * gy_c)

        gx_p = cv2.Sobel(p_roi, cv2.CV_32F, 1, 0, ksize=3)
        gy_p = cv2.Sobel(p_roi, cv2.CV_32F, 0, 1, ksize=3)
        edge_p = np.sqrt(gx_p * gx_p + gy_p * gy_p)

        edge_delta = float(np.mean(np.maximum(0.0, edge_c - edge_p)))
        edge_jump = min(1.0, edge_delta / 18.0)

        # Combine sharp edge introduction with sudden local contrast change
        z_score = 100.0 * (0.58 * edge_jump + 0.42 * localization)
        if mean_luma > 48.0 and edge_delta < 4.0:
            # Whole-frame exposure flash without crisp text edges should score lower
            z_score *= 0.45
        best_zone_score = max(best_zone_score, z_score)

    return round(max(0.0, min(100.0, best_zone_score)), 1)


def _compute_composition_score(
    primary_face: Optional[Dict[str, float]],
    salient_cx: float,
    salient_cy: float,
) -> float:
    """Evaluate rule-of-thirds placement and generous headroom."""
    if primary_face:
        cx = float(primary_face.get("cx", 0.5))
        cy = float(primary_face.get("cy", 0.4))
        fh = float(primary_face.get("h", 0.25))
        top_edge = cy - fh * 0.5
        headroom_penalty = 24.0 if top_edge < 0.04 else (12.0 if top_edge > 0.48 else 0.0)
        targets = [(0.50, 0.36), (0.333, 0.35), (0.667, 0.35)]
        min_dist = min(math.hypot(cx - tx, cy - ty) for tx, ty in targets)
        return round(max(15.0, min(100.0, 96.0 - min_dist * 115.0 - headroom_penalty)), 1)

    targets = [(0.50, 0.45), (0.333, 0.333), (0.667, 0.333), (0.333, 0.667), (0.667, 0.667)]
    min_dist = min(math.hypot(salient_cx - tx, salient_cy - ty) for tx, ty in targets)
    return round(max(20.0, min(100.0, 90.0 - min_dist * 95.0)), 1)


def analyze_video_track(
    video_path: str,
    sample_fps: float = 1.2,
    start_time: float = 0.0,
    end_time: Optional[float] = None,
    on_progress: Optional[Any] = None,
    max_frames: int = 360,
    watchdog_sec: float = 45.0,
    cancel_event: Optional[threading.Event] = None,
    on_proc: Optional[Any] = None,
) -> Dict[str, Any]:
    """
    Perform fast single-pass sequential hardware-accelerated FFmpeg rawvideo streaming
    (zero random OpenCV `CAP_PROP_POS_FRAMES` / `POS_MSEC` seeking), capping total scanned
    frames at `max_frames` (default 360), evaluating MediaPipe/Haar faces on every 2nd frame,
    and enforcing a hard `watchdog_sec` (default 45s) timeout so long multi-GB videos never hang at 67%.
    """
    if not video_path or not os.path.exists(video_path):
        raise FileNotFoundError(f"Video source not found: {video_path}")

    abs_path = str(Path(video_path).resolve())
    meta = probe_video(abs_path)
    src_w = int(meta.get("width") or 1920)
    src_h = int(meta.get("height") or 1080)
    total_dur = max(0.25, float(meta.get("duration") or 10.0))

    eff_start = max(0.0, min(total_dur - 0.1, float(start_time or 0.0)))
    eff_end = min(total_dur, float(end_time)) if (end_time is not None and end_time > eff_start) else total_dur
    span_dur = max(0.25, eff_end - eff_start)

    cap_frames = max(4, int(max_frames or 360))
    req_fps = max(0.25, min(12.0, float(sample_fps or 1.2)))
    adaptive_fps = min(req_fps, float(cap_frames) / max(span_dur, 1.0))
    est_total = max(4, min(cap_frames, int(round(span_dur * adaptive_fps))))

    scale_w = 426
    scale_h = max(2, int(round(src_h * (float(scale_w) / max(1, src_w))) // 2 * 2))
    frame_bytes = scale_w * scale_h * 3
    ffmpeg = get_ffmpeg_cmd()

    def _spawn_raw_stream(use_hwaccel: bool) -> subprocess.Popen:
        cmd = [ffmpeg, "-hide_banner", "-loglevel", "error"]
        if use_hwaccel:
            cmd.extend(["-hwaccel", "auto"])
        if eff_start > 0.0:
            cmd.extend(["-ss", f"{eff_start:.3f}"])
        if eff_end < total_dur - 0.08:
            cmd.extend(["-t", f"{span_dur:.3f}"])
        cmd.extend([
            "-i", abs_path,
            "-vf", f"fps={adaptive_fps:.6f},scale={scale_w}:{scale_h}:flags=bilinear",
            "-an", "-sn", "-dn",
            "-f", "rawvideo", "-pix_fmt", "bgr24", "-",
        ])
        proc_obj = subprocess.Popen(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            bufsize=max(frame_bytes * 2, 1 << 20),
        )
        if on_proc:
            try:
                on_proc(proc_obj)
            except Exception:
                pass
        return proc_obj

    frames_out: List[Dict[str, Any]] = []
    scene_cuts: List[Dict[str, Any]] = []
    black_intervals: List[Dict[str, Any]] = []
    mp_pack = _create_mediapipe_landmarker()

    deadline = time.monotonic() + max(2.0, float(watchdog_sec or 45.0))
    timed_out = False
    proc: Optional[subprocess.Popen] = None
    watchdog_timer: Optional[threading.Timer] = None

    def _kill_active_proc():
        nonlocal timed_out
        timed_out = True
        p = proc
        if p is not None and p.poll() is None:
            try:
                p.kill()
            except Exception:
                pass

    try:
        proc = _spawn_raw_stream(use_hwaccel=True)
        watchdog_timer = threading.Timer(max(2.0, float(watchdog_sec or 45.0)), _kill_active_proc)
        watchdog_timer.daemon = True
        watchdog_timer.start()

        prev_gray: Optional[np.ndarray] = None
        prev_hist: Optional[np.ndarray] = None
        prev_mouth_rois: List[Tuple[float, float, np.ndarray]] = []
        emo_state: Dict[str, Any] = {}
        active_speaker_sides: List[Optional[str]] = []
        cached_face_payload: Optional[Tuple[int, Optional[Dict[str, float]], Dict[str, float], float, float, float, Any]] = None
        black_start_t: Optional[float] = None
        next_prog_pct = 15

        def _read_exact_frame(pipe: Any) -> Optional[bytes]:
            chunks: List[bytes] = []
            rem = frame_bytes
            while rem > 0:
                if cancel_event and cancel_event.is_set():
                    return None
                if time.monotonic() >= deadline:
                    return None
                chunk = pipe.read(rem)
                if not chunk:
                    if not chunks:
                        return None
                    return None
                chunks.append(chunk)
                rem -= len(chunk)
            return b"".join(chunks)

        idx = 0
        first_chunk = _read_exact_frame(proc.stdout) if proc.stdout else None
        if first_chunk is None and not timed_out and not (cancel_event and cancel_event.is_set()):
            # Retry once cleanly without `-hwaccel auto` if decoder init rejected hwaccel
            try:
                if proc.poll() is None:
                    proc.kill()
                proc.wait(timeout=1.0)
            except Exception:
                pass
            proc = _spawn_raw_stream(use_hwaccel=False)
            first_chunk = _read_exact_frame(proc.stdout) if proc.stdout else None

        raw_bytes = first_chunk
        while raw_bytes is not None and idx < cap_frames:
            if cancel_event and cancel_event.is_set():
                break
            if time.monotonic() >= deadline:
                timed_out = True
                break

            t_abs = min(eff_end, eff_start + (idx / max(0.1, adaptive_fps)))
            small = np.frombuffer(raw_bytes, dtype=np.uint8).reshape((scale_h, scale_w, 3))
            gray = cv2.cvtColor(small, cv2.COLOR_BGR2GRAY)
            curr_hist = face_tracker._compute_hsv_hist(small)

            is_cut_here = False
            if prev_hist is not None and prev_gray is not None:
                corr = cv2.compareHist(prev_hist, curr_hist, cv2.HISTCMP_CORREL)
                luma_jump = float(np.mean(np.abs(gray.astype(np.float32) - prev_gray.astype(np.float32))))
                if corr < 0.56 or (corr < 0.72 and luma_jump > 36.0):
                    is_cut_here = True
                    t_cut = round(float(t_abs), 3)
                    if not scene_cuts or abs(t_cut - scene_cuts[-1]["t"]) >= 0.25:
                        scene_cuts.append({"t": t_cut, "score": round((1.0 - max(0.0, corr)) * 100.0, 2)})
            prev_hist = curr_hist

            sharpness, exposure = compute_frame_sharpness_and_exposure(small)
            action_score, scx, scy = _compute_optical_flow_action(gray, None if is_cut_here else prev_gray)
            graphic_event_score = compute_graphic_event_score(gray, prev_gray, is_cut=is_cut_here)
            prev_gray = gray

            is_black = float(np.mean(gray)) < 16.0
            if is_black:
                if black_start_t is None:
                    black_start_t = t_abs
            elif black_start_t is not None:
                bd = t_abs - black_start_t
                if bd >= 0.15:
                    black_intervals.append({
                        "start": round(black_start_t, 3),
                        "end": round(t_abs, 3),
                        "duration": round(bd, 3),
                    })
                black_start_t = None

            # Run MediaPipe FaceLandmarker / Haar fallback only on every 2nd sampled frame (or on hard cuts)
            mp_lms = None
            if (idx % 2 == 0) or is_cut_here or cached_face_payload is None:
                mp_res = _evaluate_mediapipe_face(mp_pack, small)
                if mp_res is not None:
                    face_count, primary_face, blendshapes, eyes_open, emotion_intensity, gaze_score, mp_lms = mp_res
                else:
                    faces_raw = face_tracker.detect_faces_offline(small)
                    face_count = len(faces_raw)
                    primary_face, blendshapes, eyes_open, emotion_intensity, gaze_score, prev_mouth_rois = _evaluate_haar_eyes_and_expression(
                        small, gray, faces_raw, prev_mouth_rois
                    )
                cached_face_payload = (face_count, primary_face, blendshapes, eyes_open, emotion_intensity, gaze_score, mp_lms)
            else:
                face_count, primary_face, blendshapes, eyes_open, emotion_intensity, gaze_score, mp_lms = cached_face_payload

            if is_cut_here:
                emo_state.clear()

            if int(face_count or 0) > 0:
                emo = analyze_facial_emotions(
                    landmarks=mp_lms,
                    blendshapes=blendshapes,
                    prev_state=emo_state,
                    timestamp=float(t_abs),
                )
                emotion_intensity = round(max(float(emotion_intensity or 0.0), float(emo["arousal"])), 1)
                active_speaker_sides.append("left" if (primary_face and float(primary_face.get("cx", 0.5)) < 0.48) else "right")
            else:
                emo = {
                    "dominant_emotion": "neutral",
                    "emotion_scores": {"joy": 0.0, "surprise": 0.0, "anger": 0.0, "fear": 0.0, "sadness": 0.0, "disgust": 0.0, "neutral": 1.0},
                    "valence": 0.0,
                    "arousal": round(min(100.0, action_score * 0.25), 1),
                    "emotion_delta": 0.0,
                    "is_micro_expression_spike": False,
                }
                active_speaker_sides.append(None)

            shot_scale = classify_shot_scale(face_count, primary_face, action_score)
            comp_score = _compute_composition_score(primary_face, scx, scy)

            frames_out.append({
                "t": round(float(t_abs), 3),
                "sharpness_score": sharpness,
                "exposure_score": exposure,
                "action_score": action_score,
                "composition_score": comp_score,
                "face_count": int(face_count),
                "primary_face": primary_face,
                "blendshapes": blendshapes,
                "eyes_open_confidence": eyes_open,
                "emotion_intensity": emotion_intensity,
                "dominant_emotion": emo["dominant_emotion"],
                "emotion_scores": emo["emotion_scores"],
                "valence": emo["valence"],
                "arousal": emo["arousal"],
                "emotion_delta": emo["emotion_delta"],
                "shot_scale": shot_scale,
                "graphic_event_score": graphic_event_score,
                "gaze_score": gaze_score,
                "is_black": bool(is_black),
                "is_cut": bool(is_cut_here),
            })

            idx += 1
            if on_progress:
                scan_pct = min(99, int((idx / float(max(1, est_total))) * 100))
                if scan_pct >= next_prog_pct:
                    try:
                        on_progress(scan_pct, idx, max(idx, est_total))
                    except Exception:
                        pass
                    while next_prog_pct <= scan_pct:
                        next_prog_pct += 15

            if idx >= cap_frames or time.monotonic() >= deadline:
                if time.monotonic() >= deadline:
                    timed_out = True
                break
            raw_bytes = _read_exact_frame(proc.stdout) if proc.stdout else None

        if black_start_t is not None:
            bd = eff_end - black_start_t
            if bd >= 0.15:
                black_intervals.append({
                    "start": round(black_start_t, 3),
                    "end": round(eff_end, 3),
                    "duration": round(bd, 3),
                })

        if timed_out:
            logger.warning(
                f"Video scan hit {watchdog_sec:.0f}s watchdog limit after {len(frames_out)} frames on '{Path(abs_path).name}'; continuing cleanly with partial telemetry."
            )

        if on_progress and frames_out:
            try:
                on_progress(100, len(frames_out), len(frames_out))
            except Exception:
                pass
    finally:
        if watchdog_timer is not None:
            watchdog_timer.cancel()
        if proc is not None:
            try:
                if proc.stdout:
                    proc.stdout.close()
                if proc.poll() is None:
                    proc.kill()
                proc.wait(timeout=2.0)
            except Exception:
                pass
        if mp_pack is not None:
            try:
                mp_pack[1].close()
            except Exception:
                pass

    scene_cuts.sort(key=lambda item: item["t"])
    n_f = max(1, len(frames_out))
    avg_sharp = round(sum(f["sharpness_score"] for f in frames_out) / n_f, 1) if frames_out else 0.0
    avg_exp = round(sum(f["exposure_score"] for f in frames_out) / n_f, 1) if frames_out else 0.0
    avg_act = round(sum(f["action_score"] for f in frames_out) / n_f, 1) if frames_out else 0.0
    face_ratio = round(sum(1 for f in frames_out if f["face_count"] > 0) / n_f, 3) if frames_out else 0.0

    # Aggregate advanced shot-scale, emotion, graphic event & speaker switch velocity telemetry
    scale_counts: Dict[str, int] = {"closeup_reaction": 0, "talking_head": 0, "dual_speaker": 0, "wide_gameplay": 0}
    emo_counts: Dict[str, int] = {}
    graphic_events: List[Dict[str, Any]] = []
    micro_expression_spikes: List[Dict[str, Any]] = []
    for f in frames_out:
        sc = str(f.get("shot_scale") or "wide_gameplay")
        scale_counts[sc] = scale_counts.get(sc, 0) + 1
        de = str(f.get("dominant_emotion") or "neutral")
        emo_counts[de] = emo_counts.get(de, 0) + 1
        if float(f.get("graphic_event_score", 0.0)) >= 32.0:
            if not graphic_events or abs(float(f["t"]) - float(graphic_events[-1]["t"])) >= 0.75:
                graphic_events.append({"t": f["t"], "score": f["graphic_event_score"]})
        if float(f.get("emotion_delta", 0.0)) >= 26.0:
            if not micro_expression_spikes or abs(float(f["t"]) - float(micro_expression_spikes[-1]["t"])) >= 0.80:
                micro_expression_spikes.append({
                    "t": f["t"],
                    "dominant_emotion": de,
                    "emotion_delta": f["emotion_delta"],
                    "arousal": f.get("arousal", 0.0),
                })

    shot_scale_dist = {k: round(v / n_f, 3) for k, v in scale_counts.items()}
    dominant_shot_scale = max(scale_counts, key=lambda k: scale_counts[k]) if frames_out else "wide_gameplay"
    non_neutral_emos = {k: v for k, v in emo_counts.items() if k != "neutral"}
    dominant_emotion_summary = (
        max(non_neutral_emos, key=lambda k: non_neutral_emos[k])
        if non_neutral_emos
        else ("neutral" if emo_counts else "neutral")
    )
    peak_arousal = round(max((float(f.get("arousal", 0.0)) for f in frames_out), default=0.0), 1)
    avg_arousal = round(sum(float(f.get("arousal", 0.0)) for f in frames_out) / n_f, 1) if frames_out else 0.0

    # Speaker turn-taking transitions per 10 seconds
    switches = 0
    prev_side: Optional[str] = None
    for side in active_speaker_sides:
        if side is not None:
            if prev_side is not None and side != prev_side:
                switches += 1
            prev_side = side
    speaker_switch_rate = round((switches / max(1.0, float(span_dur))) * 10.0, 2)

    return {
        "summary": {
            "duration": round(float(span_dur), 3),
            "sample_fps": round(float(adaptive_fps), 4),
            "frame_count": len(frames_out),
            "source_w": src_w,
            "source_h": src_h,
            "avg_sharpness": avg_sharp,
            "avg_exposure": avg_exp,
            "avg_action": avg_act,
            "face_presence_ratio": face_ratio,
            "scene_cut_count": len(scene_cuts),
            "black_interval_count": len(black_intervals),
            "dominant_emotion": dominant_emotion_summary,
            "peak_arousal": peak_arousal,
            "avg_arousal": avg_arousal,
            "dominant_shot_scale": dominant_shot_scale,
            "shot_scale_distribution": shot_scale_dist,
            "graphic_event_count": len(graphic_events),
            "micro_expression_count": len(micro_expression_spikes),
            "speaker_switch_rate": speaker_switch_rate,
            "timed_out": timed_out,
        },
        "scene_cuts": scene_cuts,
        "black_intervals": black_intervals,
        "graphic_events": graphic_events,
        "micro_expression_spikes": micro_expression_spikes,
        "frames": frames_out,
    }
