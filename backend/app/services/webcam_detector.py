"""
Lost Heaven — Multi-Frame Temporal Webcam Boundary Detector.
Samples 16 evenly spaced frames across a clip, runs multi-zone MediaPipe / Haar face consensus
across all 6 creator camera placements + center stage, snaps to persistent rectangular webcam
borders (16:9 / 4:3 / 1:1) via Sobel temporal edge persistence, and expands with ergonomic
head-and-shoulders proportions when unframed (or returns webcam_detected=False for pure gameplay).
"""

from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional, Tuple

import cv2
import numpy as np

from app.services.face_tracker import detect_faces_offline

logger = logging.getLogger(__name__)

STANDARD_CAMERA_ZONES: Dict[str, Tuple[float, float, float, float]] = {
    "top_left": (0.0, 0.0, 0.36, 0.40),
    "top_right": (0.64, 0.0, 0.36, 0.40),
    "bottom_left": (0.0, 0.60, 0.36, 0.40),
    "bottom_right": (0.64, 0.60, 0.36, 0.40),
    "mid_left": (0.0, 0.30, 0.36, 0.40),
    "mid_right": (0.64, 0.30, 0.36, 0.40),
    "center": (0.24, 0.14, 0.52, 0.72),
}


def compute_zone_crop_box(zone: str, src_w: int = 1920, src_h: int = 1080) -> Dict[str, Any]:
    """Return normalized 4:3 (or centered) webcam region coordinates for a named placement zone."""
    key = (zone or "top_left").strip().lower()
    if key not in STANDARD_CAMERA_ZONES:
        key = "top_left"

    zx, zy, zw, zh = STANDARD_CAMERA_ZONES[key]
    nw = 0.40 if key == "center" else 0.30
    # 4:3 aspect in pixel space
    nh = min(zh, max(0.24, (nw * max(1, src_w) * 3.0) / (4.0 * max(1, src_h))))

    if "left" in key:
        nx = 0.015
    elif "right" in key:
        nx = 1.0 - nw - 0.015
    else:
        nx = 0.5 - nw * 0.5

    if key.startswith("top"):
        ny = 0.02
    elif key.startswith("bottom"):
        ny = 1.0 - nh - 0.02
    elif key.startswith("mid"):
        ny = 0.5 - nh * 0.5
    else:  # center
        ny = 0.14

    nx = max(0.0, min(1.0 - nw, nx))
    ny = max(0.0, min(1.0 - nh, ny))
    pix_ar = round((nw * max(1, src_w)) / max(1e-4, nh * max(1, src_h)), 4)
    return {
        "detected": True,
        "webcam_detected": True,
        "source_zone": key,
        "locked_zone": key,
        "x": round(nx, 4),
        "y": round(ny, 4),
        "w": round(nw, 4),
        "h": round(nh, 4),
        "aspect_ratio": pix_ar,
        "face_cx": round(nx + nw * 0.5, 4),
        "face_cy": round(ny + nh * 0.38, 4),
        "action_cx": 0.5,
        "action_cy": 0.48,
    }


def _classify_zone(cx: float, cy: float) -> str:
    if 0.32 <= cx <= 0.68 and 0.16 <= cy <= 0.84:
        return "center"
    horiz = "left" if cx < 0.5 else "right"
    if cy < 0.36:
        vert = "top"
    elif cy > 0.64:
        vert = "bottom"
    else:
        vert = "mid"
    return f"{vert}_{horiz}"


def _ergonomic_portrait_box(
    avg_cx: float,
    avg_cy: float,
    avg_fw: float,
    avg_fh: float,
    src_w: int,
    src_h: int,
) -> Tuple[float, float, float, float]:
    """
    Expand from face center/box with natural head-and-shoulders portrait proportions:
    top: 0.45x face height, bottom: 0.85x face height, left/right: 0.75x face width.
    Maintains readable >= 4:3 framing cleanly clamped inside frame bounds.
    """
    fw = max(0.055, min(0.32, float(avg_fw)))
    fh = max(0.075, min(0.40, float(avg_fh)))

    x0 = avg_cx - (0.5 + 0.75) * fw
    x1 = avg_cx + (0.5 + 0.75) * fw
    y0 = avg_cy - (0.5 + 0.45) * fh
    y1 = avg_cy + (0.5 + 0.85) * fh

    nw = max(0.20, min(0.48, x1 - x0))
    # Target clean 4:3 minimum height/width balance so shoulders stay well framed
    desired_nh = max(y1 - y0, (nw * max(1, src_w) * 3.0) / (4.0 * max(1, src_h)))
    nh = max(0.22, min(0.55, desired_nh))
    nw = max(nw, (nh * max(1, src_h)) / max(1.0, max(1, src_w)))
    nw = min(0.50, nw)

    nx = max(0.0, min(1.0 - nw, avg_cx - nw * 0.5))
    # Place face eyes around ~38% down from top of webcam crop
    ny = max(0.0, min(1.0 - nh, avg_cy - nh * 0.38))
    return nx, ny, nw, nh


def _snap_to_persistent_overlay_border(
    frames: List[np.ndarray],
    zone: str,
    erg_box: Tuple[float, float, float, float],
    face_cx: float,
    face_cy: float,
    src_w: int,
    src_h: int,
) -> Optional[Tuple[float, float, float, float]]:
    """
    Use temporal Sobel edge persistence across sampled frames to detect sharp rectangular
    streamer overlay boundaries (16:9, 4:3, 1:1) enclosing the detected face.
    Returns normalized (x, y, w, h) if a strong rectangle exists, else None.
    """
    if not frames or zone == "center":
        return None

    # Downsample to compact analysis size for sub-15ms edge map averaging
    aw, ah = 320, max(120, int(round(320 * (src_h / float(max(1, src_w))))))
    gx_acc = np.zeros((ah, aw), dtype=np.float32)
    gy_acc = np.zeros((ah, aw), dtype=np.float32)
    used = 0
    for fr in frames[:16]:
        if fr is None or fr.size == 0:
            continue
        gray = cv2.cvtColor(cv2.resize(fr, (aw, ah)), cv2.COLOR_BGR2GRAY)
        gx_acc += np.abs(cv2.Sobel(gray, cv2.CV_32F, 1, 0, ksize=3))
        gy_acc += np.abs(cv2.Sobel(gray, cv2.CV_32F, 0, 1, ksize=3))
        used += 1

    if used == 0:
        return None

    gx_mean = gx_acc / float(used)
    gy_mean = gy_acc / float(used)
    gx_norm = gx_mean / max(1e-4, float(np.percentile(gx_mean, 98)))
    gy_norm = gy_mean / max(1e-4, float(np.percentile(gy_mean, 98)))

    ex, ey, ew, eh = erg_box
    best_score = 0.0
    best_rect: Optional[Tuple[float, float, float, float]] = None

    # Search realistic webcam overlay ratios (16:9, 4:3, 1:1) anchored cleanly to corner/edge
    for ar in (16.0 / 9.0, 4.0 / 3.0, 1.0):
        for cand_w in (0.22, 0.26, 0.30, 0.34, 0.38):
            cand_h = (cand_w * src_w) / (ar * max(1, src_h))
            if cand_h < 0.16 or cand_h > 0.52:
                continue

            if "left" in zone:
                x0_cands = [0.0, max(0.0, face_cx - cand_w * 0.5)]
            else:
                x0_cands = [1.0 - cand_w, min(1.0 - cand_w, face_cx - cand_w * 0.5)]

            if zone.startswith("top"):
                y0_cands = [0.0, max(0.0, face_cy - cand_h * 0.38)]
            elif zone.startswith("bottom"):
                y0_cands = [1.0 - cand_h, min(1.0 - cand_h, face_cy - cand_h * 0.38)]
            else:
                y0_cands = [max(0.0, min(1.0 - cand_h, face_cy - cand_h * 0.38))]

            for cx0 in x0_cands:
                for cy0 in y0_cands:
                    cx1 = cx0 + cand_w
                    cy1 = cy0 + cand_h
                    # Must generously keep face & upper head comfortably inside box
                    if not (cx0 <= face_cx - 0.03 and cx1 >= face_cx + 0.03 and cy0 <= face_cy - 0.03 and cy1 >= face_cy + 0.03):
                        continue
                    # Do not clip ergonomic width tightly if border doesn't justify it
                    ix0 = max(1, min(aw - 2, int(round(cx0 * aw))))
                    ix1 = max(ix0 + 4, min(aw - 2, int(round(cx1 * aw))))
                    iy0 = max(1, min(ah - 2, int(round(cy0 * ah))))
                    iy1 = max(iy0 + 4, min(ah - 2, int(round(cy1 * ah))))

                    edges: List[float] = []
                    if cx0 > 0.015:
                        edges.append(float(np.mean(gx_norm[iy0:iy1, max(0, ix0 - 1):min(aw, ix0 + 2)])))
                    if cx1 < 0.985:
                        edges.append(float(np.mean(gx_norm[iy0:iy1, max(0, ix1 - 1):min(aw, ix1 + 2)])))
                    if cy0 > 0.015:
                        edges.append(float(np.mean(gy_norm[max(0, iy0 - 1):min(ah, iy0 + 2), ix0:ix1])))
                    if cy1 < 0.985:
                        edges.append(float(np.mean(gy_norm[max(0, iy1 - 1):min(ah, iy1 + 2), ix0:ix1])))

                    if not edges:
                        continue
                    border_score = float(np.mean(edges))
                    # Reward covering ergonomic box cleanly
                    cover_bonus = 0.08 if (cand_w >= ew * 0.85 and cand_h >= eh * 0.80) else 0.0
                    total = border_score + cover_bonus
                    if border_score >= 0.42 and total > best_score:
                        best_score = total
                        best_rect = (cx0, cy0, cand_w, cand_h)

    return best_rect


def _compute_action_focal_point(frame_bgr: np.ndarray) -> Tuple[float, float]:
    h, w = frame_bgr.shape[:2]
    gray = cv2.cvtColor(frame_bgr, cv2.COLOR_BGR2GRAY)
    gx = cv2.Sobel(gray, cv2.CV_32F, 1, 0, ksize=3)
    gy = cv2.Sobel(gray, cv2.CV_32F, 0, 1, ksize=3)
    mag = np.sqrt(gx * gx + gy * gy)
    y0, y1 = int(h * 0.15), int(h * 0.85)
    x0, x1 = int(w * 0.15), int(w * 0.85)
    roi = mag[y0:y1, x0:x1]
    total = float(np.sum(roi))
    if total > 1e-3:
        yy, xx = np.mgrid[y0:y1, x0:x1]
        acx = float(np.sum(xx * roi) / total) / float(max(1, w))
        acy = float(np.sum(yy * roi) / total) / float(max(1, h))
        return round(max(0.25, min(0.75, acx)), 4), round(max(0.25, min(0.75, acy)), 4)
    return 0.5, 0.48


def detect_webcam_in_frames(
    frames: List[np.ndarray],
    src_w: Optional[int] = None,
    src_h: Optional[int] = None,
    locked_zone: Optional[str] = None,
) -> Dict[str, Any]:
    """
    Analyze sampled BGR frames for face consensus across all 6 corner/edge placements + center.
    Returns canonical webcam region dict with webcam_detected / detected flags and crop coords.
    """
    valid_frames = [f for f in (frames or []) if f is not None and getattr(f, "size", 0) > 0]
    if not valid_frames:
        if locked_zone and locked_zone != "auto":
            return compute_zone_crop_box(locked_zone, src_w or 1920, src_h or 1080)
        return {
            "detected": False,
            "webcam_detected": False,
            "source_zone": "none",
            "locked_zone": "auto",
            "x": 0.0,
            "y": 0.0,
            "w": 0.0,
            "h": 0.0,
            "aspect_ratio": 1.3333,
            "action_cx": 0.5,
            "action_cy": 0.48,
            "frames_sampled": 0,
            "face_frames": 0,
        }

    fh, fw = valid_frames[0].shape[:2]
    eff_w = int(src_w or fw)
    eff_h = int(src_h or fh)

    norm_lock = (locked_zone or "auto").strip().lower()
    if norm_lock not in STANDARD_CAMERA_ZONES and norm_lock != "auto":
        norm_lock = "auto"

    # Zones to scan
    scan_zones = (
        {norm_lock: STANDARD_CAMERA_ZONES[norm_lock]}
        if norm_lock != "auto"
        else STANDARD_CAMERA_ZONES
    )

    zone_detections: Dict[str, List[Dict[str, float]]] = {z: [] for z in scan_zones}
    face_frame_indices: set[int] = set()

    for f_idx, frame in enumerate(valid_frames):
        # First check downscaled full frame for large / centered or prominent corner faces
        scale = min(1.0, 640.0 / float(max(1, frame.shape[1])))
        small_full = (
            cv2.resize(frame, (max(64, int(round(frame.shape[1] * scale))), max(64, int(round(frame.shape[0] * scale)))))
            if scale < 0.99
            else frame
        )
        full_faces = detect_faces_offline(small_full)
        matched_in_frame = False
        for ff in full_faces:
            z_hit = _classify_zone(float(ff["cx"]), float(ff["cy"]))
            if z_hit in zone_detections:
                zone_detections[z_hit].append({
                    "cx": float(ff["cx"]),
                    "cy": float(ff["cy"]),
                    "w": float(ff["w"]),
                    "h": float(ff["h"]),
                })
                face_frame_indices.add(f_idx)
                matched_in_frame = True

        # Also inspect corner/edge ROIs upscaled so small streamer facecams are detected accurately
        if not matched_in_frame or norm_lock != "auto":
            for z_name, (zx, zy, zw, zh) in scan_zones.items():
                if z_name == "center":
                    continue
                rx0 = max(0, int(round(zx * frame.shape[1])))
                ry0 = max(0, int(round(zy * frame.shape[0])))
                rw = max(24, int(round(zw * frame.shape[1])))
                rh = max(24, int(round(zh * frame.shape[0])))
                roi = frame[ry0:min(frame.shape[0], ry0 + rh), rx0:min(frame.shape[1], rx0 + rw)]
                if roi.size == 0:
                    continue
                det_roi = roi
                if roi.shape[1] < 280:
                    up = 280.0 / float(max(1, roi.shape[1]))
                    det_roi = cv2.resize(roi, (280, max(32, int(round(roi.shape[0] * up)))))
                r_faces = detect_faces_offline(det_roi)
                if r_faces:
                    best_f = max(r_faces, key=lambda item: item.get("w", 0.0) * item.get("h", 0.0))
                    abs_cx = (rx0 + float(best_f["cx"]) * roi.shape[1]) / float(max(1, frame.shape[1]))
                    abs_cy = (ry0 + float(best_f["cy"]) * roi.shape[0]) / float(max(1, frame.shape[0]))
                    abs_fw = (float(best_f["w"]) * roi.shape[1]) / float(max(1, frame.shape[1]))
                    abs_fh = (float(best_f["h"]) * roi.shape[0]) / float(max(1, frame.shape[0]))
                    # Deduplicate if already captured via full-frame scan in this frame
                    already = any(
                        abs(d["cx"] - abs_cx) < 0.05 and abs(d["cy"] - abs_cy) < 0.05
                        for d in zone_detections[z_name][-2:]
                    )
                    if not already:
                        zone_detections[z_name].append({
                            "cx": abs_cx,
                            "cy": abs_cy,
                            "w": abs_fw,
                            "h": abs_fh,
                        })
                    face_frame_indices.add(f_idx)

    # Rank zones by temporal consensus count and average face prominence
    best_zone: Optional[str] = None
    best_zone_score = 0.0
    for z_name, dets in zone_detections.items():
        if not dets:
            continue
        mean_area = float(np.mean([d["w"] * d["h"] for d in dets]))
        z_score = len(dets) * 10.0 + mean_area * 120.0
        # Slight preference for streamer corner overlays vs accidental center when tied
        if z_name != "center":
            z_score += 2.0
        if z_score > best_zone_score:
            best_zone_score = z_score
            best_zone = z_name

    # Smart "No Webcam Detected" Guard when auto mode finds zero confirmed faces
    if best_zone is None:
        if norm_lock != "auto":
            return compute_zone_crop_box(norm_lock, eff_w, eff_h)
        mid_fr = valid_frames[len(valid_frames) // 2]
        acx, acy = _compute_action_focal_point(mid_fr)
        return {
            "detected": False,
            "webcam_detected": False,
            "source_zone": "none",
            "locked_zone": "auto",
            "x": 0.0,
            "y": 0.0,
            "w": 0.0,
            "h": 0.0,
            "aspect_ratio": 1.3333,
            "action_cx": acx,
            "action_cy": acy,
            "frames_sampled": len(valid_frames),
            "face_frames": 0,
        }

    dets = zone_detections[best_zone]
    avg_cx = float(np.median([d["cx"] for d in dets]))
    avg_cy = float(np.median([d["cy"] for d in dets]))
    avg_fw = float(np.median([d["w"] for d in dets]))
    avg_fh = float(np.median([d["h"] for d in dets]))

    erg_box = _ergonomic_portrait_box(avg_cx, avg_cy, avg_fw, avg_fh, eff_w, eff_h)
    snapped = _snap_to_persistent_overlay_border(
        valid_frames, best_zone, erg_box, avg_cx, avg_cy, eff_w, eff_h
    )
    nx, ny, nw, nh = snapped if snapped is not None else erg_box
    pix_ar = round((nw * eff_w) / max(1e-4, nh * eff_h), 4)

    return {
        "detected": True,
        "webcam_detected": True,
        "source_zone": best_zone,
        "locked_zone": norm_lock if norm_lock != "auto" else "auto",
        "x": round(max(0.0, min(1.0 - nw, nx)), 4),
        "y": round(max(0.0, min(1.0 - nh, ny)), 4),
        "w": round(nw, 4),
        "h": round(nh, 4),
        "aspect_ratio": pix_ar,
        "face_cx": round(avg_cx, 4),
        "face_cy": round(avg_cy, 4),
        "action_cx": 0.5,
        "action_cy": 0.48,
        "frames_sampled": len(valid_frames),
        "face_frames": len(face_frame_indices),
    }


def detect_source_webcam(
    video_path: str,
    start_time: float = 0.0,
    end_time: Optional[float] = None,
    num_samples: int = 16,
    locked_zone: Optional[str] = None,
) -> Dict[str, Any]:
    """
    Sample 16 evenly spaced frames across [start_time, end_time] and locate the streamer webcam
    boundary (with smart no-webcam fallback when zero faces exist).
    """
    cap = cv2.VideoCapture(str(video_path))
    if not cap.isOpened():
        if locked_zone and locked_zone != "auto":
            return compute_zone_crop_box(locked_zone, 1920, 1080)
        return {
            "detected": False,
            "webcam_detected": False,
            "source_zone": "none",
            "locked_zone": "auto",
            "x": 0.0,
            "y": 0.0,
            "w": 0.0,
            "h": 0.0,
            "aspect_ratio": 1.3333,
            "action_cx": 0.5,
            "action_cy": 0.48,
            "frames_sampled": 0,
            "face_frames": 0,
        }

    try:
        fps = cap.get(cv2.CAP_PROP_FPS) or 30.0
        total_frames = int(cap.get(cv2.CAP_PROP_FRAME_COUNT) or 0)
        src_w = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH) or 1920)
        src_h = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT) or 1080)
        total_dur = total_frames / fps if total_frames > 0 else 10.0

        s0 = max(0.0, float(start_time or 0.0))
        s1 = min(total_dur, float(end_time)) if end_time is not None else total_dur
        if s1 <= s0 + 0.1:
            s1 = min(total_dur, s0 + max(1.0, total_dur * 0.5))

        count = max(2, int(num_samples or 16))
        sample_times = np.linspace(s0, max(s0 + 0.05, s1 - 0.05), count)
        sampled_frames: List[np.ndarray] = []

        for t_sec in sample_times:
            cap.set(cv2.CAP_PROP_POS_MSEC, float(t_sec) * 1000.0)
            ok, fr = cap.read()
            if ok and fr is not None:
                sampled_frames.append(fr)

        return detect_webcam_in_frames(
            sampled_frames,
            src_w=src_w,
            src_h=src_h,
            locked_zone=locked_zone,
        )
    finally:
        cap.release()
