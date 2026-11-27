"""
Lost Heaven — Unified Scene-Aware Visual Analyzer (`vision_analyzer.py`).
Single 5-fps visual pass combining:
  1. Hard shot boundary detection (HSV histogram correlation + HSV pixel delta)
  2. Multi-face + MediaPipe FaceLandmarker (`jawOpen`) / Haar + mouth-motion + audio RMS speaker tracking
  3. Ergonomic Head-and-Shoulders portrait expansion (eyes near ~36% from top, headset/mic/shoulders preserved)
  4. 16-frame multi-zone temporal webcam overlay detection (pip box + aspect)
  5. Zero-face optical-flow / absdiff + Sobel edge-density action saliency tracking
  6. Deadzone-damped spring camera trajectories (instant cut snaps, zero pan drift across cuts)
"""

from __future__ import annotations

import logging
import math
import os
import threading
from collections import OrderedDict
from typing import Any, Dict, List, Optional, Tuple

import cv2
import numpy as np

from app.services import face_tracker
from app.services.webcam_detector import detect_webcam_in_frames

logger = logging.getLogger("lost_heaven.vision_analyzer")

_VISUAL_MAP_CACHE_LOCK = threading.RLock()
_VISUAL_MAP_CACHE: "OrderedDict[Tuple[Any, ...], Dict[str, Any]]" = OrderedDict()
_MAX_CACHE_ENTRIES = 32

CORNER_WEBCAM_ZONES = {
    "top_left",
    "top_right",
    "bottom_left",
    "bottom_right",
    "mid_left",
    "mid_right",
}


def compute_head_and_shoulders_box(
    face: Dict[str, float],
    src_w: int = 1920,
    src_h: int = 1080,
) -> Dict[str, float]:
    """
    Expand a raw face bbox `{cx, cy, w, h}` into an ergonomic Head-and-Shoulders Portrait Frame
    keeping full hair, headset, microphone, and upper chest inside bounds with eyes near ~36% height.
    """
    cx = float(face.get("cx", 0.5))
    cy = float(face.get("cy", 0.4))
    fw = max(0.04, min(0.65, float(face.get("w", 0.14))))
    fh = max(0.05, min(0.75, float(face.get("h", 0.20))))

    eyes_cx = cx
    eyes_cy = max(0.04, min(0.92, cy - 0.10 * fh))

    # Ergonomic portrait margins: generous crown headroom + chin/mic/shoulder breathing room
    box_w = max(fw * 2.25, (fh * 2.35 * max(1, src_h)) / max(1, src_w) * 0.78)
    box_w = max(0.16, min(0.88, box_w))
    box_h = max(fh * 2.45, (box_w * max(1, src_w) * 0.85) / max(1, src_h))
    box_h = max(0.20, min(0.95, box_h))

    nx = max(0.0, min(1.0 - box_w, eyes_cx - box_w * 0.5))
    # Place eye line ~36% down from top edge of portrait box
    ny = max(0.0, min(1.0 - box_h, eyes_cy - box_h * 0.36))

    return {
        "x": round(nx, 4),
        "y": round(ny, 4),
        "w": round(box_w, 4),
        "h": round(box_h, 4),
        "eyes_cx": round(eyes_cx, 4),
        "eyes_cy": round(eyes_cy, 4),
        "raw_cx": round(cx, 4),
        "raw_cy": round(cy, 4),
        "raw_w": round(fw, 4),
        "raw_h": round(fh, 4),
    }


def _damped_spring_step(
    prev_xy: Tuple[float, float],
    target_xy: Tuple[float, float],
    dt: float,
    is_cut: bool,
    deadzone: float = 0.035,
    stiffness: float = 0.26,
    max_speed: float = 0.45,
) -> Tuple[float, float]:
    """
    Deadzone-damped velocity-clamped spring camera:
    - Snaps instantaneously on hard visual scene cuts.
    - Holds completely steady inside `deadzone` (3.5% frame jitter suppression).
    - Smoothly tracks large subject motion within velocity ceiling (`max_speed * dt`).
    """
    if is_cut:
        return (
            max(0.05, min(0.95, float(target_xy[0]))),
            max(0.05, min(0.95, float(target_xy[1]))),
        )

    dx = float(target_xy[0]) - float(prev_xy[0])
    dy = float(target_xy[1]) - float(prev_xy[1])
    dist = math.hypot(dx, dy)
    if dist <= deadzone:
        return prev_xy

    # Smooth pull proportional to excess beyond deadzone, clamped by velocity ceiling
    step_x = dx * stiffness
    step_y = dy * stiffness
    max_step = max(0.015, float(max_speed) * max(0.05, float(dt)))
    step_mag = math.hypot(step_x, step_y)
    if step_mag > max_step:
        ratio = max_step / step_mag
        step_x *= ratio
        step_y *= ratio

    nx = max(0.05, min(0.95, float(prev_xy[0]) + step_x))
    ny = max(0.05, min(0.95, float(prev_xy[1]) + step_y))
    return nx, ny


def _compute_action_saliency(
    curr_gray: np.ndarray,
    prev_gray: Optional[np.ndarray],
) -> Tuple[float, float]:
    """
    Zero-face action tracker blending temporal frame motion (absdiff) with Sobel edge-density
    across the central gameplay/tutorial viewport.
    """
    h, w = curr_gray.shape[:2]
    gx = cv2.Sobel(curr_gray, cv2.CV_32F, 1, 0, ksize=3)
    gy = cv2.Sobel(curr_gray, cv2.CV_32F, 0, 1, ksize=3)
    edge_mag = np.sqrt(gx * gx + gy * gy)
    edge_max = float(np.percentile(edge_mag, 98)) or 1.0
    edge_norm = np.clip(edge_mag / edge_max, 0.0, 1.0)

    if prev_gray is not None and prev_gray.shape == curr_gray.shape:
        diff = np.abs(curr_gray.astype(np.float32) - prev_gray.astype(np.float32)) / 255.0
        sal = 0.58 * np.clip(diff * 4.0, 0.0, 1.0) + 0.42 * edge_norm
    else:
        sal = edge_norm

    y0, y1 = int(h * 0.08), int(h * 0.92)
    x0, x1 = int(w * 0.08), int(w * 0.92)
    roi = sal[y0:y1, x0:x1]
    total = float(np.sum(roi))
    if total > 1e-3:
        yy, xx = np.mgrid[y0:y1, x0:x1]
        acx = float(np.sum(xx * roi) / total) / float(max(1, w))
        acy = float(np.sum(yy * roi) / total) / float(max(1, h))
        return round(max(0.18, min(0.82, acx)), 4), round(max(0.18, min(0.82, acy)), 4)
    return 0.5, 0.48


def _is_hard_scene_cut(
    curr_small: np.ndarray,
    curr_hist: np.ndarray,
    prev_small: Optional[np.ndarray],
    prev_hist: Optional[np.ndarray],
) -> bool:
    if prev_hist is None or prev_small is None:
        return False
    corr = cv2.compareHist(prev_hist, curr_hist, cv2.HISTCMP_CORREL)
    if corr < 0.65:
        return True
    prev_hsv = cv2.cvtColor(prev_small, cv2.COLOR_BGR2HSV).astype(np.float32)
    curr_hsv = cv2.cvtColor(curr_small, cv2.COLOR_BGR2HSV).astype(np.float32)
    hsv_diff = float(np.mean(np.abs(curr_hsv - prev_hsv)))
    return hsv_diff > 28.0


def analyze_clip_visuals(
    video_path: str,
    start_time: float = 0.0,
    end_time: Optional[float] = None,
    audio_path: Optional[str] = None,
    sample_fps: float = 5.0,
    num_webcam_samples: int = 16,
    locked_zone: Optional[str] = None,
    use_cache: bool = True,
) -> Dict[str, Any]:
    """
    Unified 5-fps scene-aware visual analyzer for Lost Heaven:
      - Shot boundary cuts (`shot_id`, `cut_timestamps`)
      - Multi-face + MediaPipe FaceLandmarker (`jawOpen`)/Haar + mouth activity + audio RMS
      - Ergonomic head-and-shoulders portrait expansion (`eyes` ~ 36% from top)
      - 16-frame webcam corner overlay detection (`detected`, `source_zone`, `x, y, w, h, aspect`)
      - Zero-face optical-flow / frame-diff + edge-density action saliency (`action_cx, action_cy`)
      - Deadzone-damped (`3.5%`) spring camera trajectory snapping cleanly across cuts
    """
    abs_path = os.path.abspath(str(video_path))
    try:
        st = os.stat(abs_path)
        mtime_ns = int(st.st_mtime_ns)
        fsize = int(st.st_size)
    except Exception:
        mtime_ns = 0
        fsize = 0

    eff_start = max(0.0, float(start_time or 0.0))
    eff_end_hint = float(end_time) if end_time is not None else -1.0
    lock_key = (locked_zone or "auto").strip().lower()
    cache_key = (abs_path, mtime_ns, fsize, round(eff_start, 3), round(eff_end_hint, 3), lock_key)

    if use_cache:
        with _VISUAL_MAP_CACHE_LOCK:
            hit = _VISUAL_MAP_CACHE.get(cache_key)
            if hit is not None:
                _VISUAL_MAP_CACHE.move_to_end(cache_key)
                return hit

    cap = cv2.VideoCapture(abs_path)
    if not cap.isOpened():
        empty_cam = detect_webcam_in_frames([], 1920, 1080, locked_zone=locked_zone)
        empty_cam["aspect"] = empty_cam.get("aspect_ratio", 1.3333)
        return {
            "version": 1,
            "video_path": str(video_path),
            "start_time": eff_start,
            "end_time": eff_start + 5.0,
            "duration": 5.0,
            "source_w": 1920,
            "source_h": 1080,
            "sample_fps": sample_fps,
            "cut_timestamps": [],
            "shots": [{"shot_id": 0, "start_t": 0.0, "end_t": 5.0, "speaker_mode": "zero_face", "face_count_median": 0, "active_speaker_idx": 0, "cx": 0.5, "cy": 0.48, "s1_cx": 0.3, "s1_cy": 0.4, "s2_cx": 0.7, "s2_cy": 0.4}],
            "keyframes": [{"t": 0.0, "shot_id": 0, "is_cut": False, "face_count": 0, "cx": 0.5, "cy": 0.48, "face_h": 0.26, "s1_cx": 0.3, "s1_cy": 0.4, "s2_cx": 0.7, "s2_cy": 0.4, "action_cx": 0.5, "action_cy": 0.48, "active_speaker_idx": 0, "speaker_mode": "zero_face"}],
            "webcam": empty_cam,
            "summary": {"has_corner_webcam": False, "face_presence_ratio": 0.0, "dual_speaker_ratio": 0.0, "dominant_scene_type": "zero_face"},
        }

    try:
        fps = cap.get(cv2.CAP_PROP_FPS) or 30.0
        total_frames = int(cap.get(cv2.CAP_PROP_FRAME_COUNT) or 0)
        src_w = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH) or 1920)
        src_h = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT) or 1080)
        video_dur = total_frames / fps if total_frames > 0 else 30.0

        eff_end = float(end_time) if (end_time is not None and end_time > eff_start) else min(video_dur, eff_start + 15.0)
        eff_end = min(video_dur, max(eff_start + 0.2, eff_end)) if total_frames > 0 else max(eff_start + 0.2, eff_end)
        dur = max(0.2, eff_end - eff_start)

        eff_fps = max(2.0, float(sample_fps or 5.0))
        target_count = max(6, min(150, int(round(dur * eff_fps))))
        # If total_frames available and tiny clip, cap at actual frame count
        sample_times = np.linspace(eff_start, max(eff_start + 0.04, eff_end - 0.04), target_count)

        decoded_frames: List[Tuple[float, np.ndarray]] = []
        for abs_t in sample_times:
            cap.set(cv2.CAP_PROP_POS_MSEC, float(abs_t) * 1000.0)
            ok, fr = cap.read()
            if ok and fr is not None:
                rel_t = round(max(0.0, float(abs_t) - eff_start), 3)
                decoded_frames.append((rel_t, fr))
    finally:
        cap.release()

    if not decoded_frames:
        empty_cam = detect_webcam_in_frames([], src_w, src_h, locked_zone=locked_zone)
        empty_cam["aspect"] = empty_cam.get("aspect_ratio", 1.3333)
        return {
            "version": 1,
            "video_path": str(video_path),
            "start_time": eff_start,
            "end_time": eff_end,
            "duration": round(dur, 3),
            "source_w": src_w,
            "source_h": src_h,
            "sample_fps": eff_fps,
            "cut_timestamps": [],
            "shots": [{"shot_id": 0, "start_t": 0.0, "end_t": round(dur, 3), "speaker_mode": "zero_face", "face_count_median": 0, "active_speaker_idx": 0, "cx": 0.5, "cy": 0.48, "s1_cx": 0.3, "s1_cy": 0.4, "s2_cx": 0.7, "s2_cy": 0.4}],
            "keyframes": [{"t": 0.0, "shot_id": 0, "is_cut": False, "face_count": 0, "cx": 0.5, "cy": 0.48, "face_h": 0.26, "s1_cx": 0.3, "s1_cy": 0.4, "s2_cx": 0.7, "s2_cy": 0.4, "action_cx": 0.5, "action_cy": 0.48, "active_speaker_idx": 0, "speaker_mode": "zero_face"}],
            "webcam": empty_cam,
            "summary": {"has_corner_webcam": False, "face_presence_ratio": 0.0, "dual_speaker_ratio": 0.0, "dominant_scene_type": "zero_face"},
        }

    # Correlate visual speech cues with audio RMS envelope
    audio_rms = face_tracker.extract_audio_rms_profile(
        audio_path or video_path,
        start_sec=eff_start,
        end_sec=eff_end,
        sample_fps=len(decoded_frames) / max(0.2, dur),
    )

    keyframes: List[Dict[str, Any]] = []
    cut_timestamps: List[float] = []

    shot_id = 0
    prev_small: Optional[np.ndarray] = None
    prev_hist: Optional[np.ndarray] = None
    prev_gray: Optional[np.ndarray] = None
    prev_rois: List[Tuple[float, float, np.ndarray]] = []

    cam_xy = (0.5, 0.44)
    has_cam_init = False
    s1_xy = (0.30, 0.40)
    s2_xy = (0.70, 0.40)
    has_s1 = False
    has_s2 = False
    act_xy = (0.5, 0.48)
    has_act = False

    prev_t = 0.0
    switch_cand: Optional[Tuple[float, float]] = None
    switch_streak = 0

    for idx, (rel_t, full_frame) in enumerate(decoded_frames):
        dt = max(0.05, rel_t - prev_t) if idx > 0 else (1.0 / eff_fps)
        prev_t = rel_t

        fh, fw = full_frame.shape[:2]
        if fw > 560:
            sc = 560.0 / float(fw)
            small = cv2.resize(full_frame, (560, max(1, int(round(fh * sc)))))
        else:
            small = full_frame

        curr_hist = face_tracker._compute_hsv_hist(small)
        is_cut = _is_hard_scene_cut(small, curr_hist, prev_small, prev_hist)
        if is_cut:
            shot_id += 1
            cut_timestamps.append(rel_t)
            prev_rois = []
            switch_cand = None
            switch_streak = 0
            has_s1 = False
            has_s2 = False
        prev_small = small
        prev_hist = curr_hist

        small_gray = cv2.cvtColor(small, cv2.COLOR_BGR2GRAY)
        acx_raw, acy_raw = _compute_action_saliency(small_gray, None if is_cut else prev_gray)
        prev_gray = small_gray
        if is_cut or not has_act:
            act_xy = (acx_raw, acy_raw)
            has_act = True
        else:
            act_xy = (
                round(0.24 * acx_raw + 0.76 * act_xy[0], 4),
                round(0.24 * acy_raw + 0.76 * act_xy[1], 4),
            )

        faces_raw = face_tracker.detect_faces_offline(small)
        faces_scored, prev_rois = face_tracker._score_faces_mouth_activity(
            small_gray, faces_raw, prev_rois
        )
        energy_val = audio_rms[min(idx, len(audio_rms) - 1)] if audio_rms else 0.5

        enriched_faces: List[Dict[str, Any]] = []
        for f in faces_scored:
            hs = compute_head_and_shoulders_box(f, src_w, src_h)
            enriched_faces.append({
                "cx": round(float(f["cx"]), 4),
                "cy": round(float(f["cy"]), 4),
                "w": round(float(f["w"]), 4),
                "h": round(float(f["h"]), 4),
                "eyes_cx": hs["eyes_cx"],
                "eyes_cy": hs["eyes_cy"],
                "mouth_activity": float(f.get("mouth_activity", 0.0)),
                "head_shoulders": hs,
            })

        face_count = len(enriched_faces)
        active_idx = 0
        portrait_box: Optional[Dict[str, float]] = None

        if face_count > 0:
            primary = face_tracker._pick_primary_face(
                faces_scored,
                cam_xy[0],
                cam_xy[1],
                has_cam_init and not is_cut,
                audio_energy=energy_val,
            )
            assert primary is not None
            hs_prim = compute_head_and_shoulders_box(primary, src_w, src_h)
            portrait_box = hs_prim
            target_xy = (hs_prim["eyes_cx"], hs_prim["eyes_cy"])

            # Speaker-switch hysteresis within continuous shot
            if has_cam_init and not is_cut and face_count > 1 and math.hypot(target_xy[0] - cam_xy[0], target_xy[1] - cam_xy[1]) > 0.18:
                if switch_cand and math.hypot(target_xy[0] - switch_cand[0], target_xy[1] - switch_cand[1]) < 0.14:
                    switch_streak += 1
                else:
                    switch_cand = target_xy
                    switch_streak = 1
                if switch_streak >= 3:
                    cam_xy = target_xy
                    switch_cand = None
                    switch_streak = 0
            else:
                switch_cand = None
                switch_streak = 0
                cam_xy = _damped_spring_step(
                    cam_xy,
                    target_xy,
                    dt=dt,
                    is_cut=is_cut or not has_cam_init,
                    deadzone=0.035,
                )
            has_cam_init = True
        else:
            # Zero faces: seamlessly switch spring camera to action saliency centroid
            cam_xy = _damped_spring_step(
                cam_xy,
                act_xy,
                dt=dt,
                is_cut=is_cut or not has_cam_init,
                deadzone=0.035,
            )
            has_cam_init = True

        # Dual-speaker separation check
        left_faces = [f for f in faces_scored if f["cx"] < 0.48]
        right_faces = [f for f in faces_scored if f["cx"] >= 0.48]
        is_distinct_dual = (
            bool(left_faces)
            and bool(right_faces)
            and (max(f["cx"] for f in right_faces) - min(f["cx"] for f in left_faces)) >= 0.18
        )
        if left_faces and right_faces and is_distinct_dual:
            p1 = face_tracker._pick_primary_face(left_faces, s1_xy[0], s1_xy[1], has_s1 and not is_cut, audio_energy=energy_val)
            p2 = face_tracker._pick_primary_face(right_faces, s2_xy[0], s2_xy[1], has_s2 and not is_cut, audio_energy=energy_val)
            if p1:
                hs1 = compute_head_and_shoulders_box(p1, src_w, src_h)
                s1_xy = _damped_spring_step(s1_xy, (hs1["eyes_cx"], hs1["eyes_cy"]), dt=dt, is_cut=is_cut or not has_s1, deadzone=0.035)
                has_s1 = True
            if p2:
                hs2 = compute_head_and_shoulders_box(p2, src_w, src_h)
                s2_xy = _damped_spring_step(s2_xy, (hs2["eyes_cx"], hs2["eyes_cy"]), dt=dt, is_cut=is_cut or not has_s2, deadzone=0.035)
                has_s2 = True
            m1 = float(p1.get("mouth_activity", 0.0)) if p1 else 0.0
            m2 = float(p2.get("mouth_activity", 0.0)) if p2 else 0.0
            active_idx = 0 if m1 >= m2 else 1
            speaker_mode = "dual"
        elif face_count == 1:
            speaker_mode = "single"
            active_idx = 0
            # Keep fallback dual coords sane if needed
            hs_only = portrait_box or compute_head_and_shoulders_box(faces_scored[0], src_w, src_h)
            if faces_scored[0]["cx"] < 0.5:
                s1_xy = (hs_only["eyes_cx"], hs_only["eyes_cy"])
            else:
                s2_xy = (hs_only["eyes_cx"], hs_only["eyes_cy"])
        elif face_count >= 2:
            speaker_mode = "dual"
        else:
            speaker_mode = "zero_face"

        face_h_val = float(portrait_box["raw_h"]) if portrait_box else 0.26
        keyframes.append({
            "t": rel_t,
            "shot_id": shot_id,
            "is_cut": bool(is_cut),
            "face_count": face_count,
            "faces": enriched_faces,
            "cx": round(cam_xy[0], 4),
            "cy": round(cam_xy[1], 4),
            "face_h": round(face_h_val, 4),
            "s1_cx": round(s1_xy[0], 4),
            "s1_cy": round(s1_xy[1], 4),
            "s2_cx": round(s2_xy[0], 4),
            "s2_cy": round(s2_xy[1], 4),
            "active_speaker_idx": active_idx,
            "speaker_mode": speaker_mode,
            "action_cx": act_xy[0],
            "action_cy": act_xy[1],
            "audio_rms": round(float(energy_val), 4),
            "portrait_box": portrait_box,
        })

    # Temporal 16-frame webcam overlay consensus using evenly spaced full-res frames
    cam_indices = np.linspace(0, len(decoded_frames) - 1, min(len(decoded_frames), max(2, int(num_webcam_samples))))
    seen_i = set()
    cam_frames: List[np.ndarray] = []
    for ci in cam_indices:
        ii = int(round(float(ci)))
        if ii not in seen_i:
            seen_i.add(ii)
            cam_frames.append(decoded_frames[ii][1])

    webcam = detect_webcam_in_frames(
        cam_frames,
        src_w=src_w,
        src_h=src_h,
        locked_zone=locked_zone,
    )
    webcam["aspect"] = webcam.get("aspect_ratio", 1.3333)
    # Only flag genuine corner/edge streamer webcam overlays when not locked manually to center
    if webcam.get("detected") and webcam.get("source_zone") == "center" and lock_key == "auto":
        # Full-screen centered presenter should route as talking head rather than corner PiP
        face_ratio = sum(1 for k in keyframes if k["face_count"] > 0) / float(max(1, len(keyframes)))
        has_corner_webcam = False
    else:
        face_ratio = sum(1 for k in keyframes if k["face_count"] > 0) / float(max(1, len(keyframes)))
        has_corner_webcam = bool(webcam.get("detected"))

    # Group contiguous keyframes into discrete semantic shots
    shots: List[Dict[str, Any]] = []
    cur_group: List[Dict[str, Any]] = []
    for kf in keyframes:
        if cur_group and cur_group[-1]["shot_id"] != kf["shot_id"]:
            shots.append(_summarize_shot(cur_group, kf["t"]))
            cur_group = [kf]
        else:
            cur_group.append(kf)
    if cur_group:
        shots.append(_summarize_shot(cur_group, round(dur, 3)))

    dual_frames = sum(1 for k in keyframes if k["speaker_mode"] == "dual")
    dual_ratio = dual_frames / float(max(1, len(keyframes)))

    if has_corner_webcam:
        dom_type = "corner_webcam_gameplay"
    elif dual_ratio >= 0.35:
        dom_type = "dual_speaker"
    elif face_ratio >= 0.35:
        dom_type = "single_speaker"
    else:
        dom_type = "zero_face_action"

    result: Dict[str, Any] = {
        "version": 1,
        "video_path": str(video_path),
        "start_time": round(eff_start, 3),
        "end_time": round(eff_end, 3),
        "duration": round(dur, 3),
        "source_w": src_w,
        "source_h": src_h,
        "sample_fps": eff_fps,
        "cut_timestamps": cut_timestamps,
        "shots": shots,
        "keyframes": keyframes,
        "webcam": webcam,
        "summary": {
            "has_corner_webcam": has_corner_webcam,
            "face_presence_ratio": round(face_ratio, 3),
            "dual_speaker_ratio": round(dual_ratio, 3),
            "dominant_scene_type": dom_type,
        },
    }

    if use_cache:
        with _VISUAL_MAP_CACHE_LOCK:
            _VISUAL_MAP_CACHE[cache_key] = result
            _VISUAL_MAP_CACHE.move_to_end(cache_key)
            while len(_VISUAL_MAP_CACHE) > _MAX_CACHE_ENTRIES:
                _VISUAL_MAP_CACHE.popitem(last=False)

    return result


def _summarize_shot(group: List[Dict[str, Any]], end_t: float) -> Dict[str, Any]:
    start_t = float(group[0]["t"])
    counts = sorted(int(k["face_count"]) for k in group)
    med_count = counts[len(counts) // 2]
    dual_votes = sum(1 for k in group if k["speaker_mode"] == "dual")
    zero_votes = sum(1 for k in group if k["face_count"] == 0)

    if dual_votes >= max(1, len(group) // 2):
        mode = "two_speaker"
    elif zero_votes > len(group) // 2:
        mode = "zero_face"
    else:
        mode = "single_speaker"

    act_votes = [int(k.get("active_speaker_idx", 0)) for k in group]
    act_idx = 1 if act_votes.count(1) > act_votes.count(0) else 0

    return {
        "shot_id": int(group[0]["shot_id"]),
        "start_t": round(start_t, 3),
        "end_t": round(max(start_t + 0.1, end_t), 3),
        "speaker_mode": mode,
        "face_count_median": med_count,
        "active_speaker_idx": act_idx,
        "cx": round(float(np.median([k["cx"] for k in group])), 4),
        "cy": round(float(np.median([k["cy"] for k in group])), 4),
        "s1_cx": round(float(np.median([k["s1_cx"] for k in group])), 4),
        "s1_cy": round(float(np.median([k["s1_cy"] for k in group])), 4),
        "s2_cx": round(float(np.median([k["s2_cx"] for k in group])), 4),
        "s2_cy": round(float(np.median([k["s2_cy"] for k in group])), 4),
        "action_cx": round(float(np.median([k["action_cx"] for k in group])), 4),
        "action_cy": round(float(np.median([k["action_cy"] for k in group])), 4),
    }


def build_face_track_from_visual_map(
    visual_map: Dict[str, Any],
    source_webcam_region: Optional[Dict[str, Any]] = None,
    framing_override: Optional[Dict[str, Any]] = None,
    webcam_corner: str = "auto",
) -> Dict[str, Any]:
    """Build canonical `face_track_data` payload consumed identically by `preview_frame.py` and `renderer.py`."""
    webcam = dict(visual_map.get("webcam") or {})
    if isinstance(source_webcam_region, dict) and (
        "webcam_detected" in source_webcam_region
        or "detected" in source_webcam_region
        or (source_webcam_region.get("locked_zone") and source_webcam_region.get("locked_zone") != "auto")
    ):
        webcam = dict(source_webcam_region)
        if "aspect" not in webcam:
            webcam["aspect"] = webcam.get("aspect_ratio", 1.3333)

    if isinstance(framing_override, dict):
        wb = framing_override.get("webcam_box")
        if isinstance(wb, dict) and float(wb.get("w", 0.0) or 0.0) > 0.05 and float(wb.get("h", 0.0) or 0.0) > 0.05:
            webcam = {
                **webcam,
                "detected": True,
                "webcam_detected": True,
                "source_zone": webcam.get("source_zone") or "custom",
                "locked_zone": "custom",
                "x": round(max(0.0, min(0.95, float(wb["x"]))), 4),
                "y": round(max(0.0, min(0.95, float(wb["y"]))), 4),
                "w": round(max(0.06, min(1.0, float(wb["w"]))), 4),
                "h": round(max(0.06, min(1.0, float(wb["h"]))), 4),
            }
            sw = int(visual_map.get("source_w") or 1920)
            sh = int(visual_map.get("source_h") or 1080)
            ar = round((webcam["w"] * sw) / max(1e-4, webcam["h"] * sh), 4)
            webcam["aspect"] = ar
            webcam["aspect_ratio"] = ar

    kfs = list(visual_map.get("keyframes") or [])
    if isinstance(framing_override, dict):
        anchor = framing_override.get("focus_anchor")
        lock_cam = bool(framing_override.get("lock_camera", False))
        if isinstance(anchor, dict) and "x" in anchor and "y" in anchor and (lock_cam or framing_override.get("apply_focus_anchor", True)):
            ax = max(0.05, min(0.95, float(anchor["x"])))
            ay = max(0.05, min(0.95, float(anchor["y"])))
            if lock_cam or not visual_map.get("summary", {}).get("face_presence_ratio"):
                kfs = [{**k, "cx": ax, "cy": ay, "action_cx": ax, "action_cy": ay} for k in kfs]
            elif kfs:
                # Gently bias subject center when not locked full static
                kfs = [
                    {**k, "cx": round(ax, 4), "cy": round(ay, 4)} if k.get("face_count", 0) == 0 else k
                    for k in kfs
                ]

    det_zone = webcam.get("source_zone") if webcam.get("detected") else None
    if not det_zone or det_zone in ("none", "center", "custom"):
        det_zone = "top_left" if (not webcam_corner or webcam_corner == "auto") else webcam_corner

    return {
        "video_path": visual_map.get("video_path", ""),
        "source_w": int(visual_map.get("source_w") or 1920),
        "source_h": int(visual_map.get("source_h") or 1080),
        "keyframes": kfs,
        "shots": visual_map.get("shots") or [],
        "cut_timestamps": visual_map.get("cut_timestamps") or [],
        "detected_webcam_corner": det_zone,
        "webcam_detection": webcam,
        "source_webcam_region": webcam,
        "framing_override": framing_override or {},
        "visual_map_summary": visual_map.get("summary") or {},
    }
