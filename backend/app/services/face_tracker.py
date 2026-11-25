import os
import json
import math
import wave
import shutil
import subprocess
import tempfile
import logging
from pathlib import Path
from typing import Dict, Any, List, Optional, Tuple

import cv2
import numpy as np

from app.config import settings
from app.services.proxy import get_ffmpeg_cmd

logger = logging.getLogger(__name__)

_MP_LANDMARKER_FAILED = False
_MP_FACE_DETECTOR_FAILED = False
_HAAR_CACHE: Optional[Tuple[cv2.CascadeClassifier, cv2.CascadeClassifier]] = None


def _detect_faces_mediapipe_landmarker(frame_bgr: np.ndarray) -> Optional[List[Dict[str, float]]]:
    """
    Tier 1: MediaPipe Tasks FaceLandmarker (if local `face_landmarker.task` exists).
    Extracts normalized face bbox + `jawOpen` / lip aperture activity for active speaker scoring.
    """
    global _MP_LANDMARKER_FAILED
    if _MP_LANDMARKER_FAILED:
        return None

    model_path = Path(settings.STORAGE_DIR) / "models" / "face_landmarker.task"
    if not model_path.exists():
        _MP_LANDMARKER_FAILED = True
        return None

    try:
        import mediapipe as mp
        from mediapipe.tasks import python as mp_python
        from mediapipe.tasks.python import vision as mp_vision

        # Copy model to ASCII cache if path contains non-ASCII characters (Windows C++ safety)
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
        rgb = cv2.cvtColor(frame_bgr, cv2.COLOR_BGR2RGB)
        mp_image = mp.Image(image_format=mp.ImageFormat.SRGB, data=rgb)

        with mp_vision.FaceLandmarker.create_from_options(options) as landmarker:
            result = landmarker.detect(mp_image)
            faces: List[Dict[str, float]] = []
            landmarks_list = result.face_landmarks or []
            blendshapes_list = result.face_blendshapes or []
            for idx, lms in enumerate(landmarks_list):
                xs = [float(p.x) for p in lms]
                ys = [float(p.y) for p in lms]
                min_x, max_x = max(0.0, min(xs)), min(1.0, max(xs))
                min_y, max_y = max(0.0, min(ys)), min(1.0, max(ys))
                fw = max(0.02, max_x - min_x)
                fh = max(0.02, max_y - min_y)
                cx = min_x + fw * 0.5
                cy = min_y + fh * 0.5

                jaw_open = 0.0
                if idx < len(blendshapes_list):
                    for cat in blendshapes_list[idx]:
                        if getattr(cat, "category_name", "") == "jawOpen":
                            jaw_open = float(getattr(cat, "score", 0.0))
                            break
                # Fallback lip aperture variance between landmarks 13 & 14
                if len(lms) > 14:
                    lip_gap = abs(float(lms[14].y) - float(lms[13].y)) / fh
                    jaw_open = max(jaw_open, min(1.0, lip_gap * 6.0))

                faces.append({
                    "cx": cx,
                    "cy": cy,
                    "w": fw,
                    "h": fh,
                    "mouth_activity": round(max(0.0, min(1.0, jaw_open)), 4),
                    "tier": 1,
                })
            return faces
    except Exception as e:
        logger.debug(f"MediaPipe FaceLandmarker unavailable, falling back to FaceDetector: {e}")
        _MP_LANDMARKER_FAILED = True
        return None


def _detect_faces_mediapipe_tasks(frame_bgr: np.ndarray) -> Optional[List[Dict[str, float]]]:
    """
    Tier 2: MediaPipe Tasks Vision FaceDetector if both package and local model file exist.
    Returns None if unavailable so caller cleanly falls back to OpenCV Haar Cascades.
    """
    global _MP_FACE_DETECTOR_FAILED
    if _MP_FACE_DETECTOR_FAILED:
        return None

    model_path = Path(settings.STORAGE_DIR) / "models" / "blaze_face_short_range.tflite"
    if not model_path.exists():
        _MP_FACE_DETECTOR_FAILED = True
        return None

    try:
        import mediapipe as mp
        from mediapipe.tasks import python as mp_python
        from mediapipe.tasks.python import vision as mp_vision

        cache_dir = Path(tempfile.gettempdir()) / "lost_heaven_mp_cache"
        cache_dir.mkdir(parents=True, exist_ok=True)
        ascii_model = cache_dir / "blaze_face_short_range.tflite"
        if not ascii_model.exists() or ascii_model.stat().st_size != model_path.stat().st_size:
            shutil.copy2(str(model_path), str(ascii_model))

        base_options = mp_python.BaseOptions(model_asset_path=str(ascii_model))
        options = mp_vision.FaceDetectorOptions(
            base_options=base_options,
            min_detection_confidence=0.45,
        )
        rgb = cv2.cvtColor(frame_bgr, cv2.COLOR_BGR2RGB)
        mp_image = mp.Image(image_format=mp.ImageFormat.SRGB, data=rgb)
        h, w = frame_bgr.shape[:2]

        with mp_vision.FaceDetector.create_from_options(options) as detector:
            result = detector.detect(mp_image)
            faces: List[Dict[str, float]] = []
            for det in result.detections or []:
                bb = det.bounding_box
                fw = float(bb.width) / w
                fh = float(bb.height) / h
                cx = (float(bb.origin_x) + bb.width / 2.0) / w
                cy = (float(bb.origin_y) + bb.height / 2.0) / h
                faces.append({"cx": cx, "cy": cy, "w": fw, "h": fh, "tier": 2})
            return faces
    except Exception as e:
        logger.debug(f"MediaPipe Tasks FaceDetector unavailable, falling back to OpenCV Haar: {e}")
        _MP_FACE_DETECTOR_FAILED = True
        return None


def _get_haar_cascades() -> Tuple[cv2.CascadeClassifier, cv2.CascadeClassifier]:
    """
    Tier 3: Load OpenCV Haar cascades from an ASCII-only temp cache directory so Windows
    Unicode workspace paths (e.g. ドキュメント) never break OpenCV's C++ XML parser.
    """
    global _HAAR_CACHE
    if _HAAR_CACHE is not None:
        return _HAAR_CACHE

    cache_dir = Path(tempfile.gettempdir()) / "lost_heaven_haar_cache"
    cache_dir.mkdir(parents=True, exist_ok=True)

    xml_names = (
        "haarcascade_frontalface_default.xml",
        "haarcascade_profileface.xml",
    )
    resolved_paths: List[str] = []
    for name in xml_names:
        src = Path(cv2.data.haarcascades) / name
        dst = cache_dir / name
        try:
            if src.exists() and (not dst.exists() or dst.stat().st_size != src.stat().st_size):
                shutil.copy2(str(src), str(dst))
            resolved_paths.append(str(dst if dst.exists() else src))
        except Exception:
            resolved_paths.append(str(src))

    frontal = cv2.CascadeClassifier(resolved_paths[0])
    profile = cv2.CascadeClassifier(resolved_paths[1])
    _HAAR_CACHE = (frontal, profile)
    return _HAAR_CACHE


def detect_faces_offline(frame_bgr: np.ndarray) -> List[Dict[str, float]]:
    """
    100% offline-safe 3-tier face detection chain:
    1. MediaPipe FaceLandmarker + jawOpen blendshape (if local model exists)
    2. MediaPipe Tasks Vision FaceDetector (if local model exists)
    3. OpenCV Haar Cascades (frontal + profile + mirrored profile)
    """
    mp_lm = _detect_faces_mediapipe_landmarker(frame_bgr)
    if mp_lm is not None and len(mp_lm) > 0:
        return mp_lm

    mp_faces = _detect_faces_mediapipe_tasks(frame_bgr)
    if mp_faces is not None and len(mp_faces) > 0:
        return mp_faces

    h, w = frame_bgr.shape[:2]
    gray = cv2.cvtColor(frame_bgr, cv2.COLOR_BGR2GRAY)
    gray = cv2.equalizeHist(gray)
    frontal, profile = _get_haar_cascades()

    faces: List[Dict[str, float]] = []

    if not frontal.empty():
        det_f = frontal.detectMultiScale(
            gray, scaleFactor=1.12, minNeighbors=4, minSize=(24, 24)
        )
        for (x, y, fw, fh) in det_f:
            faces.append({
                "cx": float(x + fw / 2.0) / w,
                "cy": float(y + fh / 2.0) / h,
                "w": float(fw) / w,
                "h": float(fh) / h,
                "tier": 3,
            })

    if not faces and not profile.empty():
        det_p = profile.detectMultiScale(
            gray, scaleFactor=1.12, minNeighbors=4, minSize=(24, 24)
        )
        for (x, y, fw, fh) in det_p:
            faces.append({
                "cx": float(x + fw / 2.0) / w,
                "cy": float(y + fh / 2.0) / h,
                "w": float(fw) / w,
                "h": float(fh) / h,
                "tier": 3,
            })

        if not faces:
            flipped = cv2.flip(gray, 1)
            det_pf = profile.detectMultiScale(
                flipped, scaleFactor=1.12, minNeighbors=4, minSize=(24, 24)
            )
            for (x, y, fw, fh) in det_pf:
                orig_x = w - (x + fw)
                faces.append({
                    "cx": float(orig_x + fw / 2.0) / w,
                    "cy": float(y + fh / 2.0) / h,
                    "w": float(fw) / w,
                    "h": float(fh) / h,
                    "tier": 3,
                })

    return faces


def _extract_mouth_roi(gray: np.ndarray, face: Dict[str, float]) -> Optional[np.ndarray]:
    """Crop lower-middle mouth ROI from grayscale frame normalized to 24x16."""
    h, w = gray.shape[:2]
    cx, cy, fw, fh = face["cx"], face["cy"], face["w"], face["h"]
    x1 = max(0, int((cx - 0.22 * fw) * w))
    x2 = min(w, int((cx + 0.22 * fw) * w))
    y1 = max(0, int((cy + 0.12 * fh) * h))
    y2 = min(h, int((cy + 0.42 * fh) * h))
    if x2 - x1 < 4 or y2 - y1 < 4:
        return None
    patch = cv2.resize(gray[y1:y2, x1:x2], (24, 16))
    return patch.astype(np.float32)


def _score_faces_mouth_activity(
    gray: np.ndarray,
    faces: List[Dict[str, float]],
    prev_rois: List[Tuple[float, float, np.ndarray]],
) -> Tuple[List[Dict[str, float]], List[Tuple[float, float, np.ndarray]]]:
    """
    Attach a normalized `mouth_activity` score in [0, 1] to every detected face.
    If Tier 1 FaceLandmarker already computed `jawOpen`, blend it with ROI pixel delta.
    """
    new_rois: List[Tuple[float, float, np.ndarray]] = []
    for f in faces:
        roi = _extract_mouth_roi(gray, f)
        pixel_motion = 0.0
        if roi is not None:
            new_rois.append((f["cx"], f["cy"], roi))
            best_dist = 999.0
            best_prev = None
            for px, py, p_roi in prev_rois:
                d = math.hypot(f["cx"] - px, f["cy"] - py)
                if d < best_dist:
                    best_dist = d
                    best_prev = p_roi
            if best_prev is not None and best_dist < 0.15:
                mad = float(np.mean(np.abs(roi - best_prev))) / 255.0
                pixel_motion = min(1.0, mad * 12.0)

        existing = float(f.get("mouth_activity", 0.0))
        f["mouth_activity"] = round(max(existing, pixel_motion), 4)
    return faces, new_rois


def extract_audio_rms_profile(
    video_path: str,
    start_sec: float,
    end_sec: float,
    sample_fps: float = 6.0,
) -> List[float]:
    """
    Extract normalized [0.0, 1.0] per-sample audio RMS energy across [start_sec, end_sec].
    Returns empty list gracefully on silent or video-only inputs.
    """
    dur = max(0.2, float(end_sec) - float(start_sec))
    n_samples = max(1, int(round(dur * sample_fps)))
    tmp_dir = Path(tempfile.mkdtemp(prefix="lost_heaven_trk_"))
    wav_path = tmp_dir / "trk_16k.wav"
    try:
        cmd = [
            get_ffmpeg_cmd(),
            "-y",
            "-ss", f"{max(0.0, start_sec):.3f}",
            "-t", f"{dur:.3f}",
            "-i", os.path.abspath(video_path),
            "-vn", "-ac", "1", "-ar", "16000", "-c:a", "pcm_s16le",
            str(wav_path),
        ]
        res = subprocess.run(cmd, capture_output=True, text=True, timeout=20)
        if res.returncode != 0 or not wav_path.exists():
            return []
        with wave.open(str(wav_path), "rb") as wf:
            raw = wf.readframes(wf.getnframes())
            pcm = np.frombuffer(raw, dtype=np.int16).astype(np.float32) / 32768.0
        if pcm.size == 0:
            return []
        chunks = np.array_split(pcm, n_samples)
        rms = np.array([float(np.sqrt(np.mean(c * c))) if c.size > 0 else 0.0 for c in chunks])
        peak = float(np.max(rms)) if rms.size > 0 else 0.0
        if peak <= 1e-5:
            return [0.0] * len(rms)
        return [round(float(v / peak), 4) for v in rms]
    except Exception:
        return []
    finally:
        shutil.rmtree(tmp_dir, ignore_errors=True)


def detect_punch_zoom_peaks(
    video_path: str,
    start_sec: float,
    end_sec: float,
    base_zoom: float = 1.0,
    punch_intensity: float = 1.14,
) -> Dict[str, Any]:
    """
    Detect vocal excitement / acoustic energy peaks inside [start_sec, end_sec]
    and construct smooth Punch-In Zoom FX keyframes & trigger timestamps.
    """
    dur = max(0.5, float(end_sec) - float(start_sec))
    base_z = max(1.0, min(2.5, float(base_zoom or 1.0)))
    peak_z = max(1.04, min(2.5, max(base_z + 0.06, float(punch_intensity or 1.14))))
    sample_fps = 4.0
    rms_list = extract_audio_rms_profile(video_path, start_sec, end_sec, sample_fps=sample_fps)

    punch_times: List[float] = []
    if len(rms_list) >= 4:
        arr = np.array(rms_list)
        mean_val = float(np.mean(arr))
        std_val = float(np.std(arr))
        thresh = max(0.45, mean_val + 0.65 * std_val)
        last_t = -3.0
        for i in range(1, len(arr) - 1):
            t = i / sample_fps
            if t < 0.8 or t > dur - 0.6:
                continue
            if arr[i] >= thresh and arr[i] >= arr[i - 1] and arr[i] >= arr[i + 1]:
                if t - last_t >= 3.2:
                    punch_times.append(round(t, 2))
                    last_t = t
            if len(punch_times) >= 5:
                break

    # Always add subtle emphasis near the opening 0.6s hook if audio is quiet or no peaks found
    if not punch_times and dur >= 2.5:
        punch_times.append(0.8)

    # Build piecewise punch intervals [(t_start, t_end, zoom_target)]
    intervals: List[Tuple[float, float, float]] = []
    for pt in punch_times:
        intervals.append((round(max(0.0, pt - 0.12), 2), round(min(dur, pt + 1.15), 2), round(peak_z, 3)))

    return {
        "punch_timestamps": punch_times,
        "punch_intervals": intervals,
        "base_zoom": base_z,
        "peak_zoom": round(peak_z, 3),
    }


def snap_clip_boundaries_to_scenes(
    video_path: str,
    raw_clips: List[Dict[str, Any]],
    total_duration: float,
    snap_window: float = 0.5,
) -> List[Dict[str, Any]]:
    """
    Scan ±0.5s around each AI-selected clip's start_time and end_time using OpenCV
    HSV histogram correlation (< 0.65) and snap boundaries cleanly onto exact visual shot cuts.
    """
    if not raw_clips or not video_path or not os.path.exists(video_path):
        return raw_clips

    cap = cv2.VideoCapture(str(video_path))
    if not cap.isOpened():
        return raw_clips

    def _find_nearest_cut(target_t: float) -> Optional[float]:
        lo = max(0.0, target_t - snap_window)
        hi = min(total_duration if total_duration > 0 else target_t + snap_window, target_t + snap_window)
        if hi - lo < 0.15:
            return None
        cap.set(cv2.CAP_PROP_POS_MSEC, lo * 1000.0)
        prev_h = None
        best_cut_t = None
        min_corr = 0.65
        while True:
            ret, frame = cap.read()
            if not ret or frame is None:
                break
            pos_ms = cap.get(cv2.CAP_PROP_POS_MSEC)
            frame_t = (pos_ms / 1000.0) if pos_ms and pos_ms >= 0 else target_t
            if frame_t > hi + 0.05:
                break
            small = cv2.resize(frame, (160, 90))
            curr_h = _compute_hsv_hist(small)
            if prev_h is not None:
                corr = cv2.compareHist(prev_h, curr_h, cv2.HISTCMP_CORREL)
                if corr < min_corr:
                    min_corr = corr
                    best_cut_t = round(frame_t, 2)
            prev_h = curr_h
        return best_cut_t

    try:
        for item in raw_clips:
            s0 = float(item.get("start_time", 0.0))
            e0 = float(item.get("end_time", s0 + 10.0))
            snap_s = _find_nearest_cut(s0)
            snap_e = _find_nearest_cut(e0)
            if snap_s is not None and snap_s < e0 - 3.0:
                item["start_time"] = snap_s
            if snap_e is not None and snap_e > float(item["start_time"]) + 3.0:
                item["end_time"] = snap_e
            item["duration"] = round(float(item["end_time"]) - float(item["start_time"]), 2)
    finally:
        cap.release()

    return raw_clips


def _pick_primary_face(
    faces: List[Dict[str, float]],
    prev_cx: float,
    prev_cy: float,
    has_prev: bool,
    audio_energy: float = 0.5,
) -> Optional[Dict[str, float]]:
    if not faces:
        return None
    if len(faces) == 1:
        return faces[0]

    max_area = max((f["w"] * f["h"] for f in faces), default=1e-4) or 1e-4

    def score(f: Dict[str, float]) -> float:
        area_norm = (f["w"] * f["h"]) / max_area
        mouth = float(f.get("mouth_activity", 0.0))
        speech_boost = mouth * (0.65 + 0.85 * max(0.0, audio_energy))
        prox = 1.0 / (1.0 + 3.5 * math.hypot(f["cx"] - prev_cx, f["cy"] - prev_cy)) if has_prev else 1.0
        return (0.45 * area_norm * prox) + (0.55 * speech_boost)

    return max(faces, key=score)


def _compute_hsv_hist(frame_bgr: np.ndarray) -> np.ndarray:
    hsv = cv2.cvtColor(frame_bgr, cv2.COLOR_BGR2HSV)
    hist = cv2.calcHist([hsv], [0, 1], None, [32, 32], [0, 180, 0, 256])
    cv2.normalize(hist, hist, alpha=0, beta=1, norm_type=cv2.NORM_MINMAX)
    return hist


def _smooth_step(
    prev_val: Tuple[float, float],
    raw_val: Tuple[float, float],
    is_cut: bool,
    deadzone: float = 0.035,
    alpha: float = 0.22,
) -> Tuple[float, float]:
    if is_cut:
        return raw_val
    dx = raw_val[0] - prev_val[0]
    dy = raw_val[1] - prev_val[1]
    if math.hypot(dx, dy) < deadzone:
        return prev_val
    return (
        prev_val[0] + alpha * dx,
        prev_val[1] + alpha * dy,
    )


def track_faces_in_clip(
    video_path: str,
    start_sec: float = 0.0,
    end_sec: Optional[float] = None,
    mode: str = "single",
    cache_dir: Optional[str] = None,
    sample_fps: float = 6.0,
) -> Dict[str, Any]:
    """
    Smooth dynamic Active Speaker Tracking (3-Tier offline-safe):
    1. MediaPipe FaceLandmarker (jawOpen blendshape) -> 2. FaceDetector -> 3. OpenCV Haar Cascades
    - Combines mouth-motion / jawOpen activity + audio RMS envelope + proximity hysteresis
    - Hard scene-cut snap via HSV histogram correlation (< 0.65)
    - 3.5% deadzone + EMA (alpha=0.22) smoothing
    - Caches results to face_track.json in cache_dir
    """
    cache_path = Path(cache_dir) / "face_track.json" if cache_dir else None
    if cache_path and cache_path.exists():
        try:
            with open(cache_path, "r", encoding="utf-8") as f:
                cached = json.load(f)
            if (
                cached.get("video_path") == str(video_path)
                and abs(float(cached.get("start_sec", -1)) - float(start_sec)) < 0.05
                and cached.get("mode") == mode
                and (end_sec is None or abs(float(cached.get("end_sec", -1)) - float(end_sec)) < 0.1)
                and cached.get("keyframes")
            ):
                return cached
        except Exception:
            pass

    cap = cv2.VideoCapture(str(video_path))
    if not cap.isOpened():
        return {
            "video_path": str(video_path),
            "start_sec": start_sec,
            "end_sec": end_sec or start_sec + 5.0,
            "mode": mode,
            "source_w": 1920,
            "source_h": 1080,
            "keyframes": [{"t": 0.0, "cx": 0.5, "cy": 0.4, "s1_cx": 0.3, "s1_cy": 0.4, "s2_cx": 0.7, "s2_cy": 0.4}],
            "detected_webcam_corner": "top_left",
        }

    src_fps = cap.get(cv2.CAP_PROP_FPS) or 30.0
    total_frames = cap.get(cv2.CAP_PROP_FRAME_COUNT) or 0
    total_dur = total_frames / src_fps if total_frames > 0 else 30.0
    src_w = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH) or 1920)
    src_h = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT) or 1080)

    eff_start = max(0.0, float(start_sec))
    eff_end = float(end_sec) if end_sec is not None else total_dur
    eff_end = max(eff_start + 0.1, min(total_dur if total_dur > 0 else eff_end, eff_end))

    audio_rms = extract_audio_rms_profile(str(video_path), eff_start, eff_end, sample_fps=sample_fps)

    dt = 1.0 / max(1.0, sample_fps)
    keyframes: List[Dict[str, float]] = []

    prev_single = (0.5, 0.4)
    has_single = False
    switch_candidate: Optional[Tuple[float, float]] = None
    switch_streak = 0
    prev_s1 = (0.3, 0.4)
    has_s1 = False
    prev_s2 = (0.7, 0.4)
    has_s2 = False
    prev_hist = None
    prev_rois: List[Tuple[float, float, np.ndarray]] = []
    corner_votes: List[str] = []

    if eff_start > 0.05:
        cap.set(cv2.CAP_PROP_POS_MSEC, eff_start * 1000.0)

    frames_per_sample = max(1.0, src_fps / max(1.0, sample_fps))
    max_samples = 360
    max_raw_frames = int(math.ceil((eff_end - eff_start + 0.5) * src_fps)) + 5
    count = 0
    frame_idx = 0
    next_sample_idx = 0.0

    while count < max_samples and frame_idx <= max_raw_frames:
        grabbed = cap.grab()
        if not grabbed:
            break

        if frame_idx + 1e-6 >= next_sample_idx:
            ret, frame = cap.retrieve()
            if not ret or frame is None:
                break

            pos_msec = cap.get(cv2.CAP_PROP_POS_MSEC)
            t_curr = (pos_msec / 1000.0) if pos_msec and pos_msec >= eff_start * 1000.0 - 200 else (eff_start + count * dt)
            if t_curr > eff_end + dt * 0.5 and count > 0:
                break

            fh, fw = frame.shape[:2]
            if fw > 480:
                scale = 480.0 / fw
                small = cv2.resize(frame, (480, max(1, int(fh * scale))))
            else:
                small = frame

            curr_hist = _compute_hsv_hist(small)
            is_cut = False
            if prev_hist is not None:
                corr = cv2.compareHist(prev_hist, curr_hist, cv2.HISTCMP_CORREL)
                if corr < 0.65:
                    is_cut = True
            prev_hist = curr_hist

            faces = detect_faces_offline(small)
            small_gray = cv2.cvtColor(small, cv2.COLOR_BGR2GRAY)
            faces, prev_rois = _score_faces_mouth_activity(small_gray, faces, [] if is_cut else prev_rois)

            rel_t = round(max(0.0, t_curr - eff_start), 3)
            energy_val = audio_rms[min(count, len(audio_rms) - 1)] if audio_rms else 0.5

            primary = _pick_primary_face(
                faces,
                prev_single[0],
                prev_single[1],
                has_single and not is_cut,
                audio_energy=energy_val,
            )
            if primary:
                cand_pt = (
                    max(0.05, min(0.95, primary["cx"])),
                    max(0.05, min(0.95, primary["cy"])),
                )
                # Speaker-switch hysteresis when multiple faces exist and no scene cut happened
                if has_single and not is_cut and len(faces) > 1 and math.hypot(cand_pt[0] - prev_single[0], cand_pt[1] - prev_single[1]) > 0.20:
                    if switch_candidate and math.hypot(cand_pt[0] - switch_candidate[0], cand_pt[1] - switch_candidate[1]) < 0.14:
                        switch_streak += 1
                    else:
                        switch_candidate = cand_pt
                        switch_streak = 1
                    if switch_streak >= 3:
                        prev_single = cand_pt
                        switch_candidate = None
                        switch_streak = 0
                else:
                    switch_candidate = None
                    switch_streak = 0
                    prev_single = _smooth_step(prev_single, cand_pt, is_cut or not has_single)
                has_single = True

                if primary["cy"] < 0.45:
                    corner_votes.append("top_left" if primary["cx"] < 0.5 else "top_right")
                else:
                    corner_votes.append("bottom_left" if primary["cx"] < 0.5 else "bottom_right")

            if mode == "dual":
                left_faces = [f for f in faces if f["cx"] < 0.5]
                right_faces = [f for f in faces if f["cx"] >= 0.5]
                p1 = _pick_primary_face(left_faces, prev_s1[0], prev_s1[1], has_s1 and not is_cut, audio_energy=energy_val)
                p2 = _pick_primary_face(right_faces, prev_s2[0], prev_s2[1], has_s2 and not is_cut, audio_energy=energy_val)
                if p1:
                    prev_s1 = _smooth_step(prev_s1, (p1["cx"], p1["cy"]), is_cut or not has_s1)
                    has_s1 = True
                if p2:
                    prev_s2 = _smooth_step(prev_s2, (p2["cx"], p2["cy"]), is_cut or not has_s2)
                    has_s2 = True

            keyframes.append({
                "t": rel_t,
                "cx": round(prev_single[0], 4),
                "cy": round(prev_single[1], 4),
                "face_h": round(float(primary.get("h", 0.26)) if primary else 0.26, 4),
                "s1_cx": round(prev_s1[0], 4),
                "s1_cy": round(prev_s1[1], 4),
                "s2_cx": round(prev_s2[0], 4),
                "s2_cy": round(prev_s2[1], 4),
            })

            count += 1
            next_sample_idx += frames_per_sample

        frame_idx += 1

    cap.release()

    if not keyframes:
        keyframes = [{"t": 0.0, "cx": 0.5, "cy": 0.4, "face_h": 0.26, "s1_cx": 0.3, "s1_cy": 0.4, "s2_cx": 0.7, "s2_cy": 0.4}]

    detected_corner = max(set(corner_votes), key=corner_votes.count) if corner_votes else "top_left"

    result = {
        "video_path": str(video_path),
        "start_sec": eff_start,
        "end_sec": eff_end,
        "mode": mode,
        "source_w": src_w,
        "source_h": src_h,
        "keyframes": keyframes,
        "detected_webcam_corner": detected_corner,
    }

    if cache_path:
        try:
            cache_path.parent.mkdir(parents=True, exist_ok=True)
            with open(cache_path, "w", encoding="utf-8") as f:
                json.dump(result, f, indent=2)
        except Exception as e:
            logger.warning(f"Could not write face_track.json cache: {e}")

    return result


def compute_clamped_crop_coords(
    face_cx: float,
    face_cy: float,
    crop_w: int,
    crop_h: int,
    src_w: int,
    src_h: int,
    pan_norm: float = 0.0,
) -> Tuple[int, int]:
    """
    Place face horizontally around face_cx + pan offset, and vertically at ~40% from top
    for natural headroom. Strictly clamped inside [0, src_w - crop_w] and [0, src_h - crop_h].
    """
    max_x = max(0, src_w - crop_w)
    max_y = max(0, src_h - crop_h)

    raw_x = (face_cx * src_w) + (pan_norm * src_w * 0.40) - (crop_w / 2.0)
    raw_y = (face_cy * src_h) - (0.40 * crop_h)

    cx = int(round(max(0.0, min(float(max_x), raw_x))))
    cy = int(round(max(0.0, min(float(max_y), raw_y))))
    cx = cx if cx % 2 == 0 else max(0, cx - 1)
    cy = cy if cy % 2 == 0 else max(0, cy - 1)
    return cx, cy


def compile_piecewise_linear_expr(
    points: List[Tuple[float, int]],
    min_val: int,
    max_val: int,
) -> str:
    """
    Compile [(t0, v0), (t1, v1), ...] into a piecewise-linear FFmpeg expression
    safe for filtergraph_script (`crop=w:h:x_expr:y_expr`).
    Subsamples redundant stationary segments to keep expression concise.
    """
    if not points:
        return str(min_val)
    if len(points) == 1:
        return str(max(min_val, min(max_val, points[0][1])))

    compact: List[Tuple[float, int]] = [points[0]]
    for i in range(1, len(points) - 1):
        prev_v = compact[-1][1]
        curr_v = points[i][1]
        next_v = points[i + 1][1]
        if curr_v != prev_v or curr_v != next_v:
            compact.append(points[i])
    compact.append(points[-1])

    if len(compact) > 60:
        step = max(1, len(compact) // 60)
        sampled = compact[::step]
        if sampled[-1] != compact[-1]:
            sampled.append(compact[-1])
        compact = sampled

    if len(compact) == 1 or all(v == compact[0][1] for _, v in compact):
        return str(max(min_val, min(max_val, compact[0][1])))

    expr = str(max(min_val, min(max_val, compact[-1][1])))
    for i in range(len(compact) - 2, -1, -1):
        t0, v0 = compact[i]
        t1, v1 = compact[i + 1]
        v0 = max(min_val, min(max_val, v0))
        v1 = max(min_val, min(max_val, v1))
        if t1 <= t0 + 1e-4 or v0 == v1:
            seg = str(v0)
        else:
            dt = t1 - t0
            dv = v1 - v0
            seg = f"({v0}+({dv})*(t-{t0:.3f})/{dt:.3f})"
        expr = f"if(lt(t,{t1:.3f}),{seg},{expr})"

    return f"clip({expr},{min_val},{max_val})"
