import re
import subprocess
import cv2
import numpy as np
import logging
from functools import lru_cache
from typing import List, Tuple, Dict, Any, Optional

from app.services.face_tracker import (
    detect_faces_offline,
    compute_clamped_crop_coords,
    compile_piecewise_linear_expr,
    interpolate_piecewise_linear,
)

logger = logging.getLogger(__name__)

ASPECT_RATIO_PRESETS = {
    "9:16": (1080, 1920),
    "16:9": (1920, 1080),
    "1:1": (1080, 1080),
    "4:5": (1080, 1350),
    "4:3": (1440, 1080),
    "21:9": (2560, 1080),
}


def canonicalize_framing_template(layout: Optional[str]) -> str:
    """Normalize layout aliases to canonical framing template names."""
    raw = (layout or "auto_track").strip().lower()
    if raw in ("gameplay_webcam_pip", "webcam_gameplay_pip"):
        return "gameplay_webcam_pip"
    if raw in ("webcam_gameplay_split", "fit_blur", "split_podcast", "auto_track"):
        return raw
    return "auto_track"


@lru_cache(maxsize=4)
def _ffmpeg_major_version(ffmpeg_bin: str) -> int:
    try:
        res = subprocess.run([ffmpeg_bin, "-version"], capture_output=True, text=True, timeout=5)
        first_line = (res.stdout or "").splitlines()[0] if res.stdout else ""
        m = re.search(r"ffmpeg version\s+(\d+)\.", first_line)
        if m:
            return int(m.group(1))
    except Exception:
        pass
    return 6


def build_filter_complex_args(
    ffmpeg_bin: str,
    script_filename: str,
    filter_complex: str,
) -> List[str]:
    """
    Build compatible CLI arguments across FFmpeg 4-6 (-filter_complex_script)
    and FFmpeg 7+/9+ (-filter_complex inline or -/filter_complex <file>).
    Because filter_complex contains strictly relative ASCII names inside cwd=work_dir,
    inline -filter_complex works across all versions up to Windows CLI length limits.
    """
    if len(filter_complex) <= 12000:
        return ["-filter_complex", filter_complex]
    if _ffmpeg_major_version(ffmpeg_bin) >= 7:
        return ["-/filter_complex", script_filename]
    return ["-filter_complex_script", script_filename]


def normalize_pan_ratio(pan_x: Optional[float] = None, pan_x_offset: float = 0.0) -> float:
    """
    Accept either `pan_x` (-100 to 100) or `pan_x_offset` (-1.0 to 1.0 or -0.4 to 0.4).
    Returns normalized value in [-1.0, 1.0].
    """
    if pan_x is not None:
        return max(-1.0, min(1.0, float(pan_x) / 100.0))
    val = float(pan_x_offset or 0.0)
    if abs(val) > 1.0:
        return max(-1.0, min(1.0, val / 100.0))
    return max(-1.0, min(1.0, val))


def resolve_target_dimensions(
    aspect_ratio: str = "9:16",
    custom_width: int = 1080,
    custom_height: int = 1920,
) -> Tuple[int, int]:
    """Resolve target width and height as even integers for codec compatibility."""
    if aspect_ratio in ASPECT_RATIO_PRESETS:
        w, h = ASPECT_RATIO_PRESETS[aspect_ratio]
    else:
        w = max(320, custom_width)
        h = max(320, custom_height)
    w = w if w % 2 == 0 else w - 1
    h = h if h % 2 == 0 else h - 1
    return w, h


def clamp_crop_coordinates(crop_x: float, crop_width: int, video_width: int) -> int:
    """Ensure crop window is within [0, video_width - crop_width] and even."""
    max_x = max(0, video_width - crop_width)
    clamped = int(round(max(0.0, min(float(crop_x), float(max_x)))))
    return clamped if clamped % 2 == 0 else max(0, clamped - 1)


def calculate_ema_smooth(values: List[float], alpha: float = 0.22) -> List[float]:
    """Exponential Moving Average filter."""
    if not values:
        return []
    smoothed = [values[0]]
    for v in values[1:]:
        smoothed.append(alpha * v + (1.0 - alpha) * smoothed[-1])
    return smoothed


def detect_faces_in_frame(frame: np.ndarray) -> List[Dict[str, float]]:
    """Detect faces using offline-safe MediaPipe/Haar detector chain."""
    return detect_faces_offline(frame)


def analyze_video_framing(
    video_path: str,
    start_time: float,
    duration: float,
    source_width: int = 1920,
    source_height: int = 1080,
) -> Dict[str, Any]:
    """Sample frames to determine active speaker and dual-speaker positions."""
    from app.services.face_tracker import track_faces_in_clip

    track = track_faces_in_clip(
        video_path=video_path,
        start_sec=start_time,
        end_sec=start_time + max(0.5, duration),
        mode="dual",
    )
    kfs = track.get("keyframes") or [{"cx": 0.5, "cy": 0.4, "s1_cx": 0.3, "s1_cy": 0.4, "s2_cx": 0.7, "s2_cy": 0.4}]
    mid = kfs[len(kfs) // 2]
    return {
        "center_x": float(mid.get("cx", 0.5)),
        "center_y": float(mid.get("cy", 0.4)),
        "has_dual_speakers": True,
        "speaker1_cx": float(mid.get("s1_cx", 0.3)),
        "speaker1_cy": float(mid.get("s1_cy", 0.4)),
        "speaker2_cx": float(mid.get("s2_cx", 0.7)),
        "speaker2_cy": float(mid.get("s2_cy", 0.4)),
        "detected_webcam_corner": track.get("detected_webcam_corner", "top_left"),
        "face_track_data": track,
    }


def get_webcam_crop_coordinates(corner: str, src_w: int, src_h: int) -> Tuple[int, int, int, int]:
    """
    Returns (crop_x, crop_y, crop_w, crop_h) for webcam region based on corner.
    """
    cw = int(src_w * 0.32)
    cw = cw if cw % 2 == 0 else cw - 1
    ch = int(src_h * 0.42)
    ch = ch if ch % 2 == 0 else ch - 1
    cw = max(16, min(src_w, cw))
    ch = max(16, min(src_h, ch))

    if corner == "top_right":
        cx = src_w - cw
        cy = 0
    elif corner == "bottom_left":
        cx = 0
        cy = src_h - ch
    elif corner == "bottom_right":
        cx = src_w - cw
        cy = src_h - ch
    else:  # top_left (default)
        cx = 0
        cy = 0

    return cx, cy, cw, ch


def hex_to_ffmpeg_color(hex_str: str) -> str:
    """Format hex color like '#10B981' to '0x10B981'."""
    clean = (hex_str or "").strip().lstrip("#")
    return f"0x{clean}" if len(clean) == 6 else "0xF59E0B"


def _compute_aspect_crop_dims(source_w: int, source_h: int, target_ratio: float, zoom: float) -> Tuple[int, int]:
    z = max(1.0, min(2.5, float(zoom or 1.0)))
    base_h = source_h / z
    base_w = base_h * target_ratio

    if base_w > source_w:
        base_w = source_w / z if z > 1.0 and (source_w / target_ratio) > source_h else source_w
        if base_w > source_w:
            base_w = source_w
        base_h = base_w / target_ratio

    if base_h > source_h:
        base_h = source_h
        base_w = base_h * target_ratio

    cw = max(16, int(base_w))
    ch = max(16, int(base_h))
    cw = cw if cw % 2 == 0 else cw - 1
    ch = ch if ch % 2 == 0 else ch - 1
    cw = min(source_w if source_w % 2 == 0 else source_w - 1, cw)
    ch = min(source_h if source_h % 2 == 0 else source_h - 1, ch)
    return max(2, cw), max(2, ch)


def _pip_overlay_coords(corner: str, margin_x: int = 28, margin_y: int = 36, bottom_margin_y: Optional[int] = None) -> str:
    bot_my = bottom_margin_y if bottom_margin_y is not None else margin_y
    if corner == "top_left":
        return f"{margin_x}:{margin_y}"
    if corner == "bottom_left":
        return f"{margin_x}:H-h-{bot_my}"
    if corner == "bottom_right":
        return f"W-w-{margin_x}:H-h-{bot_my}"
    # top_right default
    return f"W-w-{margin_x}:{margin_y}"


def _pip_safe_xy(
    corner: str,
    target_w: int,
    target_h: int,
    card_w: int,
    card_h: int,
) -> Tuple[int, int]:
    """
    Compute realistic platform-safe PiP top-left coordinates clear of top Hook Banner,
    bottom captions (y + h <= 72% height on vertical outputs), and social UI rail:
    - top_left:     x=4%,  y=14%
    - top_right:    x=60%, y=14%
    - bottom_left:  x=4%,  y=54% (clamped so y + card_h <= 0.72 * target_h)
    - bottom_right: x=49%, y=54% (clamped so y + card_h <= 0.72 * target_h)
    """
    c_key = (corner or "top_right").strip().lower()
    is_vertical = target_h > target_w
    max_bot_y = int(round(target_h * 0.72)) - card_h if is_vertical else target_h - card_h - 12

    if c_key == "top_left":
        x = int(round(target_w * 0.04))
        y = min(max_bot_y, int(round(target_h * 0.14))) if is_vertical else int(round(target_h * 0.06))
    elif c_key == "bottom_left":
        x = int(round(target_w * 0.04))
        y = min(max_bot_y, int(round(target_h * 0.54))) if is_vertical else int(round(target_h * 0.54))
    elif c_key == "bottom_right":
        x = int(round(target_w * 0.49))
        y = min(max_bot_y, int(round(target_h * 0.54))) if is_vertical else int(round(target_h * 0.54))
    else:  # top_right
        x = int(round(target_w * 0.60))
        y = min(max_bot_y, int(round(target_h * 0.14))) if is_vertical else int(round(target_h * 0.06))

    x = max(8, min(max(8, target_w - card_w - 8), x))
    y = max(8, min(max(8, target_h - card_h - 8), y))
    return x, y


def prepare_clip_framing_data(
    video_path: str,
    kept_segments_abs: List[Tuple[float, float]],
    frame_layout: str = "auto_track",
    webcam_corner: str = "auto",
    source_webcam_region: Optional[Dict[str, Any]] = None,
    visual_map: Optional[Dict[str, Any]] = None,
    framing_override: Optional[Dict[str, Any]] = None,
    merge_crossfade_sec: float = 0.03,
    best_frames: Optional[List[Dict[str, Any]]] = None,
) -> Tuple[Dict[str, Any], str, Optional[Dict[str, Any]]]:
    """
    Single Source of Truth called by both `preview_frame.py` and `renderer.py` to compute
    unified visual analysis keyframes, scene cuts, active speaker boxes, action saliency,
    and webcam overlays for identical coordinate parity between preview and rendered MP4.
    Returns `(face_track_data, effective_corner, resolved_visual_map)`.
    """
    from app.services.vision_analyzer import (
        analyze_clip_visuals,
        build_face_track_from_visual_map,
    )

    layout = canonicalize_framing_template(frame_layout)
    segs = [
        (max(0.0, float(s)), max(float(s) + 0.2, float(e)))
        for s, e in (kept_segments_abs or [])
        if e > s + 0.05
    ] or [(0.0, 5.0)]

    locked_zone: Optional[str] = None
    if isinstance(source_webcam_region, dict) and source_webcam_region.get("locked_zone"):
        lz = str(source_webcam_region.get("locked_zone")).strip().lower()
        if lz != "auto":
            locked_zone = lz

    resolved_vm: Optional[Dict[str, Any]] = None
    if (
        isinstance(visual_map, dict)
        and visual_map.get("keyframes")
        and len(segs) == 1
        and abs(float(visual_map.get("start_time", -1.0)) - round(segs[0][0], 3)) <= 0.15
        and abs(float(visual_map.get("end_time", -1.0)) - round(segs[0][1], 3)) <= 0.15
        and not locked_zone
    ):
        resolved_vm = visual_map
    elif len(segs) == 1:
        s0, s1 = segs[0]
        if layout == "fit_blur" and not framing_override:
            # fit_blur renders full uncropped width without face crop coordinates; avoid unneeded decode
            resolved_vm = visual_map
        if resolved_vm is None:
            resolved_vm = analyze_clip_visuals(
                video_path=video_path,
                start_time=s0,
                end_time=s1,
                locked_zone=locked_zone,
                use_cache=True,
            )
    else:
        stitched_kfs: List[Dict[str, Any]] = []
        stitched_shots: List[Dict[str, Any]] = []
        stitched_cuts: List[float] = []
        first_vm: Optional[Dict[str, Any]] = None
        out_cursor = 0.0
        shot_offset = 0
        for seg_idx, (seg_s, seg_e) in enumerate(segs):
            seg_len = max(0.2, seg_e - seg_s)
            vm_seg = analyze_clip_visuals(
                video_path=video_path,
                start_time=seg_s,
                end_time=seg_e,
                locked_zone=locked_zone,
                use_cache=True,
            )
            if first_vm is None:
                first_vm = vm_seg
            for k_i, kf in enumerate(vm_seg.get("keyframes") or []):
                n_kf = dict(kf)
                rel_t = round(out_cursor + float(kf.get("t", 0.0)), 3)
                n_kf["t"] = rel_t
                n_kf["shot_id"] = int(kf.get("shot_id", 0)) + shot_offset
                if seg_idx > 0 and k_i == 0:
                    n_kf["is_cut"] = True
                    stitched_cuts.append(rel_t)
                elif kf.get("is_cut"):
                    stitched_cuts.append(rel_t)
                stitched_kfs.append(n_kf)
            for sh in (vm_seg.get("shots") or []):
                n_sh = dict(sh)
                n_sh["shot_id"] = int(sh.get("shot_id", 0)) + shot_offset
                n_sh["start_t"] = round(out_cursor + float(sh.get("start_t", 0.0)), 3)
                n_sh["end_t"] = round(out_cursor + float(sh.get("end_t", seg_len)), 3)
                stitched_shots.append(n_sh)
            shot_offset += max(1, len(vm_seg.get("shots") or []))
            step_cf = min(float(merge_crossfade_sec or 0.03), seg_len * 0.25) if seg_idx < len(segs) - 1 else 0.0
            out_cursor += seg_len - step_cf

        resolved_vm = {
            **(first_vm or {}),
            "keyframes": stitched_kfs,
            "shots": stitched_shots,
            "cut_timestamps": stitched_cuts,
            "duration": round(out_cursor, 3),
        }

    # Prefer clean_anchor Best Frame as initial camera framing hint when no user override exists
    eff_override = framing_override
    if best_frames and isinstance(resolved_vm, dict):
        anchor_bf = next(
            (bf for bf in best_frames if isinstance(bf, dict) and bf.get("role") == "clean_anchor"),
            None,
        ) or next(
            (bf for bf in best_frames if isinstance(bf, dict) and bf.get("role") == "peak_emotion"),
            None,
        )
        if anchor_bf and anchor_bf.get("t") is not None:
            resolved_vm["anchor_t"] = float(anchor_bf["t"])
            if not eff_override and resolved_vm.get("keyframes"):
                a_rel = float(anchor_bf.get("rel_t", max(0.0, float(anchor_bf["t"]) - float(segs[0][0]))))
                near_kf = min(
                    resolved_vm["keyframes"],
                    key=lambda kf: abs(float(kf.get("t", 0.0)) - a_rel),
                )
                if near_kf and near_kf.get("cx") is not None:
                    resolved_vm["preferred_anchor"] = {
                        "x": float(near_kf["cx"]),
                        "y": float(near_kf.get("cy", 0.42)),
                    }

    face_track_data = build_face_track_from_visual_map(
        resolved_vm or {},
        source_webcam_region=source_webcam_region,
        framing_override=eff_override,
        webcam_corner=webcam_corner,
    )
    det_corner = str(face_track_data.get("detected_webcam_corner") or "top_left")
    effective_corner = det_corner if (not webcam_corner or webcam_corner == "auto") else webcam_corner
    return face_track_data, effective_corner, resolved_vm


def _resolve_detected_webcam_crop(
    face_track_data: Optional[Dict[str, Any]],
    fallback_corner: str,
    source_w: int,
    source_h: int,
) -> Tuple[Optional[Tuple[int, int, int, int]], bool, float, float, float, float]:
    """
    Resolve source webcam crop box (x, y, w, h) from multi-frame temporal detection or SQLite cache.
    Returns (crop_box_or_None, has_webcam, action_cx, action_cy, cam_aspect_ratio, face_rel_y).
    """
    cam_det = (face_track_data or {}).get("source_webcam_region") or (face_track_data or {}).get("webcam_detection")
    if isinstance(cam_det, dict):
        acx = float(cam_det.get("action_cx", 0.5))
        acy = float(cam_det.get("action_cy", 0.48))
        has_cam = bool(
            cam_det.get(
                "webcam_detected",
                cam_det.get("detected", cam_det.get("has_webcam", False)),
            )
        )
        if not has_cam:
            return None, False, acx, acy, 1.3333, 0.38

        # Support direct normalized (x, y, w, h) from webcam_detector.py
        if float(cam_det.get("w", 0.0) or 0.0) > 0.02 and float(cam_det.get("h", 0.0) or 0.0) > 0.02:
            cw = max(32, min(source_w, int(round(float(cam_det["w"]) * source_w))))
            ch = max(32, min(source_h, int(round(float(cam_det["h"]) * source_h))))
            cw = cw if cw % 2 == 0 else max(2, cw - 1)
            ch = ch if ch % 2 == 0 else max(2, ch - 1)
            cx = max(0, min(source_w - cw, int(round(float(cam_det.get("x", 0.0)) * source_w))))
            cy = max(0, min(source_h - ch, int(round(float(cam_det.get("y", 0.0)) * source_h))))
            cx = cx if cx % 2 == 0 else max(0, cx - 1)
            cy = cy if cy % 2 == 0 else max(0, cy - 1)
            cam_ar = cw / float(max(1, ch))
            fcy = float(cam_det.get("face_cy", (cy + ch * 0.38) / max(1, source_h)))
            rel_fy = max(0.20, min(0.65, ((fcy * source_h) - cy) / float(max(1, ch))))
            return (cx, cy, cw, ch), True, acx, acy, cam_ar, rel_fy

        # Support legacy norm_box from face_tracker.detect_webcam_corner_in_frame
        nb = cam_det.get("norm_box")
        if isinstance(nb, dict):
            cw = max(32, min(source_w, int(round(float(nb.get("w", 0.28)) * source_w))))
            ch = max(32, min(source_h, int(round(float(nb.get("h", 0.34)) * source_h))))
            cw = cw if cw % 2 == 0 else max(2, cw - 1)
            ch = ch if ch % 2 == 0 else max(2, ch - 1)
            cx = max(0, min(source_w - cw, int(round(float(nb.get("x", 0.0)) * source_w))))
            cy = max(0, min(source_h - ch, int(round(float(nb.get("y", 0.0)) * source_h))))
            cx = cx if cx % 2 == 0 else max(0, cx - 1)
            cy = cy if cy % 2 == 0 else max(0, cy - 1)
            return (cx, cy, cw, ch), True, acx, acy, cw / float(max(1, ch)), 0.38

        det_zone = cam_det.get("source_zone") or cam_det.get("corner") or fallback_corner
        wcx, wcy, wcw, wch = get_webcam_crop_coordinates(det_zone, source_w, source_h)
        return (wcx, wcy, wcw, wch), True, acx, acy, wcw / float(max(1, wch)), 0.38

    wcx, wcy, wcw, wch = get_webcam_crop_coordinates(fallback_corner, source_w, source_h)
    return (wcx, wcy, wcw, wch), True, 0.5, 0.48, wcw / float(max(1, wch)), 0.38


def _rounded_pip_alpha_filter(radius: int = 18) -> str:
    """FFmpeg geq alpha mask carving smooth rounded corners while keeping 100% webcam brightness."""
    r = max(4, int(radius))
    r2 = r * r
    return (
        f"format=rgba,"
        f"geq=r='r(X,Y)':g='g(X,Y)':b='b(X,Y)':"
        f"a='if(gt(pow(max(0,max({r}-X,X-(W-{r + 1}))),2)+pow(max(0,max({r}-Y,Y-(H-{r + 1}))),2),{r2}),0,alpha(X,Y))'"
    )


def _build_crop_filter_or_coords(
    kfs: List[Dict[str, Any]],
    cw: int,
    ch: int,
    source_w: int,
    source_h: int,
    pan_norm: float,
    cx_key: str,
    cy_key: str,
    fallback_xy: Tuple[float, float],
    is_video_render: bool,
    preview_rel_t: float,
) -> str:
    """
    Shared keyframe -> crop compiler ensuring exact pixel coordinate parity between
    single-frame preview (`is_video_render=False`) and full MP4 export (`is_video_render=True`),
    holding pre-cut coordinates cleanly up to `t_cut - 0.004s` before snapping instantaneously.
    """
    max_x = max(0, source_w - cw)
    max_y = max(0, source_h - ch)
    if not kfs:
        px, py = compute_clamped_crop_coords(
            fallback_xy[0], fallback_xy[1], cw, ch, source_w, source_h, pan_norm=pan_norm
        )
        return f"crop={cw}:{ch}:{px}:{py}"

    pts_x: List[Tuple[float, int]] = []
    pts_y: List[Tuple[float, int]] = []
    prev_x: Optional[int] = None
    prev_y: Optional[int] = None
    prev_t: Optional[float] = None
    for kf in kfs:
        t_val = float(kf.get("t", 0.0))
        fx_val = float(kf.get(cx_key, fallback_xy[0]))
        fy_val = float(kf.get(cy_key, fallback_xy[1]))
        px, py = compute_clamped_crop_coords(fx_val, fy_val, cw, ch, source_w, source_h, pan_norm=pan_norm)
        if (
            kf.get("is_cut")
            and prev_x is not None
            and prev_y is not None
            and prev_t is not None
            and (t_val - prev_t) > 0.02
        ):
            snap_t = round(t_val - 0.004, 4)
            pts_x.append((snap_t, prev_x))
            pts_y.append((snap_t, prev_y))
        pts_x.append((t_val, px))
        pts_y.append((t_val, py))
        prev_x, prev_y, prev_t = px, py, t_val

    if is_video_render and len(pts_x) >= 2:
        x_expr = compile_piecewise_linear_expr(pts_x, 0, max_x)
        y_expr = compile_piecewise_linear_expr(pts_y, 0, max_y)
        return f"crop={cw}:{ch}:'{x_expr}':'{y_expr}'"

    ix = interpolate_piecewise_linear(pts_x, preview_rel_t, 0, max_x)
    iy = interpolate_piecewise_linear(pts_y, preview_rel_t, 0, max_y)
    return f"crop={cw}:{ch}:{ix}:{iy}"


def _shot_enable_expr(shots: List[Dict[str, Any]]) -> str:
    terms = [
        f"between(t,{float(sh['start_t']):.3f},{float(sh['end_t']):.3f})"
        for sh in shots
        if float(sh.get("end_t", 0.0)) > float(sh.get("start_t", 0.0)) + 0.02
    ]
    return "+".join(terms) if terms else "0"


def canonicalize_camera_motion(mode: Optional[str]) -> str:
    raw = str(mode or "steady").strip().lower().replace("-", "_").replace(" ", "_")
    mapping = {
        "steady": "steady",
        "none": "steady",
        "dolly_in": "dolly_in",
        "slow_dolly_in": "dolly_in",
        "dolly_out": "dolly_out",
        "beat_punch": "beat_punch",
        "beat_synced_micro_punch": "beat_punch",
    }
    return mapping.get(raw, "steady")


def compute_camera_motion_zoom_at_t(
    camera_motion: str,
    t_sec: float,
    duration: float,
    beat_timestamps: Optional[List[float]] = None,
) -> float:
    mode = canonicalize_camera_motion(camera_motion)
    dur = max(0.5, float(duration or 8.0))
    t = max(0.0, float(t_sec or 0.0))
    frac = min(1.0, t / dur)

    if mode == "dolly_in":
        return 1.0 + 0.12 * frac
    if mode == "dolly_out":
        return 1.12 - 0.12 * frac
    if mode == "beat_punch":
        beats = list(beat_timestamps or [])
        if not beats:
            beats = [round(0.65 * i, 3) for i in range(1, max(2, int(dur / 0.65)))][:16]
        for bt in beats:
            b = float(bt)
            if (b - 0.04) <= t <= (b + 0.20):
                if t <= b + 0.04:
                    p = (t - (b - 0.04)) / 0.08
                else:
                    p = max(0.0, 1.0 - (t - (b + 0.04)) / 0.16)
                return 1.0 + 0.09 * p
        return 1.0
    return 1.0


def build_camera_motion_zoom_expr(
    camera_motion: str,
    duration: float,
    fps: float = 30.0,
    beat_timestamps: Optional[List[float]] = None,
) -> str:
    mode = canonicalize_camera_motion(camera_motion)
    eff_fps = max(15.0, min(60.0, float(fps or 30.0)))
    dur = max(0.5, float(duration or 8.0))
    total_frames = max(2.0, dur * eff_fps)

    if mode == "dolly_in":
        return f"min(1.12,1.0+0.12*(on/{total_frames:.3f}))"
    if mode == "dolly_out":
        return f"max(1.0,1.12-0.12*(on/{total_frames:.3f}))"
    if mode == "beat_punch":
        beats = list(beat_timestamps or [])
        if not beats:
            beats = [round(0.65 * i, 3) for i in range(1, max(2, int(dur / 0.65)))][:16]
        t_var = f"(on/{eff_fps:.3f})"
        expr = "1.0"
        for bt in reversed(beats[:16]):
            b = float(bt)
            t0 = max(0.0, b - 0.04)
            tp = b + 0.04
            t1 = b + 0.20
            seg = (
                f"if(lt({t_var},{tp:.3f}),"
                f"1.0+0.09*(({t_var}-{t0:.3f})/0.080),"
                f"1.09-0.09*(({t_var}-{tp:.3f})/0.160))"
            )
            expr = f"if(between({t_var},{t0:.3f},{t1:.3f}),{seg},{expr})"
        return expr
    return "1.0"


def _apply_whip_pan_cuts(
    filtergraph: str,
    whip_pan_cuts: bool,
    cut_timestamps: List[float],
    clip_duration: float,
    is_video_render: bool,
    preview_rel_t: float,
) -> str:
    if not whip_pan_cuts or not cut_timestamps:
        return filtergraph
    dur_lim = float(clip_duration or 0.0)
    internal = [
        float(tc)
        for tc in cut_timestamps
        if float(tc) >= 0.15 and (dur_lim <= 0.3 or float(tc) <= dur_lim - 0.15)
    ]
    if not internal:
        return filtergraph
    if not is_video_render:
        if any(abs(float(preview_rel_t) - tc) <= 0.10 for tc in internal):
            if filtergraph.endswith("[v]"):
                return f"{filtergraph[:-3]}[v_prewhip];[v_prewhip]avgblur=sizeX=28:sizeY=1[v]"
        return filtergraph

    en = "+".join(
        f"between(t,{max(0.0, tc - 0.10):.3f},{tc + 0.10:.3f})"
        for tc in internal[:12]
    )
    if filtergraph.endswith("[v]"):
        return f"{filtergraph[:-3]}[v_prewhip];[v_prewhip]avgblur=sizeX=28:sizeY=1:enable='{en}'[v]"
    return filtergraph


def build_multi_aspect_filter(
    aspect_ratio: str,
    frame_layout: str,
    source_w: int,
    source_h: int,
    custom_w: int = 1080,
    custom_height: int = 1920,
    custom_h: Optional[int] = None,
    center_x: float = 0.5,
    center_y: float = 0.4,
    speaker1_cx: float = 0.3,
    speaker1_cy: float = 0.4,
    speaker2_cx: float = 0.7,
    speaker2_cy: float = 0.4,
    webcam_corner: str = "auto",
    webcam_split_ratio: float = 0.40,
    pip_border_color: str = "#F59E0B",
    zoom: float = 1.0,
    pan_x_offset: float = 0.0,
    pan_x: Optional[float] = None,
    face_track_data: Optional[Dict[str, Any]] = None,
    is_video_render: bool = False,
    preview_rel_t: float = 0.0,
    target_size: Optional[Tuple[int, int]] = None,
    input_label: str = "0:v",
    camera_motion: str = "steady",
    whip_pan_cuts: bool = False,
    clip_duration: float = 0.0,
    fps: float = 30.0,
    beat_timestamps: Optional[List[float]] = None,
) -> str:
    """
    Generate complete FFmpeg video filtergraph string for all 5 unified framing templates:
    1. auto_track (deadzone-damped spring speaker & zero-face action saliency + cut snaps)
    2. fit_blur (full width fit + gblur=sigma=28,eq=brightness=-0.18:saturation=1.15 + 1px rim highlight)
    3. webcam_gameplay_split (40% top cam/speaker + 3px divider + 60% action-tracked gameplay)
    4. gameplay_webcam_pip (action-tracked gameplay + 36% rounded PiP card, 0 vignette, safe fallback)
    5. split_podcast (cutaway-aware 2-speaker stack with active glow & 1-speaker full-frame cutaways)
    Outputs stream label [v].
    """
    if target_size is not None and target_size[0] > 0 and target_size[1] > 0:
        tw, th = int(target_size[0]), int(target_size[1])
        target_w = tw if tw % 2 == 0 else max(2, tw - 1)
        target_h = th if th % 2 == 0 else max(2, th - 1)
    else:
        eff_custom_h = custom_h if custom_h is not None else custom_height
        target_w, target_h = resolve_target_dimensions(aspect_ratio, custom_w, eff_custom_h)
    target_ratio = target_w / float(target_h)
    layout = canonicalize_framing_template(frame_layout)
    z = max(1.0, min(2.5, float(zoom or 1.0)))
    pan_norm = normalize_pan_ratio(pan_x=pan_x, pan_x_offset=pan_x_offset)
    actual_corner = "top_left" if (not webcam_corner or webcam_corner == "auto") else webcam_corner
    kfs = (face_track_data or {}).get("keyframes") or []
    shots = (face_track_data or {}).get("shots") or []
    cut_ts = (face_track_data or {}).get("cut_timestamps") or []
    eff_motion = canonicalize_camera_motion(camera_motion)
    eff_dur = float(clip_duration or (float(kfs[-1]["t"]) + 0.2 if kfs else 8.0))
    eff_fps = max(15.0, min(60.0, float(fps or 30.0)))
    motion_zoom_mult = (
        compute_camera_motion_zoom_at_t(eff_motion, preview_rel_t, eff_dur, beat_timestamps)
        if (not is_video_render and eff_motion != "steady")
        else 1.0
    )
    motion_z_expr = (
        build_camera_motion_zoom_expr(eff_motion, eff_dur, eff_fps, beat_timestamps)
        if (is_video_render and eff_motion != "steady")
        else ""
    )

    def _finish(fg: str) -> str:
        return _apply_whip_pan_cuts(
            fg,
            whip_pan_cuts=bool(whip_pan_cuts),
            cut_timestamps=cut_ts,
            clip_duration=eff_dur,
            is_video_render=is_video_render,
            preview_rel_t=preview_rel_t,
        )

    # 1. WEBCAM + GAMEPLAY SPLIT-SCREEN (Top 40% cam/speaker + 3px divider, Bottom 60% action gameplay)
    if layout == "webcam_gameplay_split":
        split_ratio = max(0.30, min(0.50, float(webcam_split_ratio if webcam_split_ratio else 0.40)))
        if abs(split_ratio - 0.35) < 0.005:
            split_ratio = 0.40
        top_h = int(target_h * split_ratio)
        top_h = top_h if top_h % 2 == 0 else top_h - 1
        bot_h = target_h - top_h

        cam_box, has_cam, acx, acy, _, _ = _resolve_detected_webcam_crop(
            face_track_data, actual_corner, source_w, source_h
        )
        game_ratio = target_w / float(bot_h)
        gw, gh = _compute_aspect_crop_dims(source_w, source_h, game_ratio, z)
        game_crop = _build_crop_filter_or_coords(
            kfs, gw, gh, source_w, source_h, pan_norm,
            "action_cx", "action_cy", (acx, acy),
            is_video_render, preview_rel_t,
        )

        if not has_cam or cam_box is None:
            top_ratio = target_w / float(top_h)
            aw, ah = _compute_aspect_crop_dims(source_w, source_h, top_ratio, max(1.65, z))
            act_crop = _build_crop_filter_or_coords(
                kfs, aw, ah, source_w, source_h, pan_norm,
                "action_cx", "action_cy", (acx, acy),
                is_video_render, preview_rel_t,
            )
            return _finish(
                f"[{input_label}]split=2[in_act][in_game];"
                f"[in_act]{act_crop},scale={target_w}:{top_h},"
                f"drawbox=x=0:y=ih-3:w=iw:h=3:color=0xFFFFFF@0.28:t=fill[top];"
                f"[in_game]{game_crop},scale={target_w}:{bot_h}[bot];"
                f"[top][bot]vstack=inputs=2[v]"
            )

        wcx, wcy, wcw, wch = cam_box
        top_ar = target_w / float(max(1, top_h))
        cam_crop_h = max(32, int(round(wcw / top_ar)) if int(round(wcw / top_ar)) % 2 == 0 else int(round(wcw / top_ar)) - 1)
        cam_crop_h = min(wch, cam_crop_h)
        face_cy_norm = float(
            ((face_track_data or {}).get("source_webcam_region") or {}).get(
                "face_cy", (wcy + wch * 0.36) / max(1, source_h)
            )
        )
        ideal_cam_y = int(round((face_cy_norm * source_h) - (cam_crop_h * 0.36)))
        adj_wcy = max(wcy, min(wcy + max(0, wch - cam_crop_h), ideal_cam_y))
        adj_wcy = adj_wcy if adj_wcy % 2 == 0 else max(0, adj_wcy - 1)

        return _finish(
            f"[{input_label}]split=2[in_cam][in_game];"
            f"[in_cam]crop={wcw}:{wch}:{wcx}:{wcy},scale={target_w}:{top_h}:force_original_aspect_ratio=increase,"
            f"crop={target_w}:{top_h}:(iw-{target_w})/2:{max(0, min(top_h // 4, adj_wcy - wcy))},"
            f"drawbox=x=0:y=ih-3:w=iw:h=3:color=0xFFFFFF@0.28:t=fill[top];"
            f"[in_game]{game_crop},scale={target_w}:{bot_h}[bot];"
            f"[top][bot]vstack=inputs=2[v]"
        )

    # 2. GAMEPLAY + WEBCAM PiP OVERLAY (Action-tracked base + 36% rounded PiP card, 0 vignette)
    if layout == "gameplay_webcam_pip":
        cam_box, has_cam, acx, acy, cam_ar, _ = _resolve_detected_webcam_crop(
            face_track_data, actual_corner, source_w, source_h
        )
        if not has_cam or cam_box is None:
            z_fb = max(1.24, z) * motion_zoom_mult if not is_video_render else max(1.24, z)
            gw, gh = _compute_aspect_crop_dims(source_w, source_h, target_ratio, z_fb)
            fallback_crop = _build_crop_filter_or_coords(
                kfs, gw, gh, source_w, source_h, pan_norm,
                "cx", "cy", (acx, acy),
                is_video_render, preview_rel_t,
            )
            if is_video_render and motion_z_expr:
                return _finish(
                    f"[{input_label}]fps={eff_fps:.3f},{fallback_crop},"
                    f"zoompan=z='{motion_z_expr}':d=1:x='iw/2-(iw/zoom/2)':y='ih/2-(ih/zoom/2)':"
                    f"s={target_w}x{target_h}:fps={eff_fps:.3f}[v]"
                )
            return _finish(f"[{input_label}]{fallback_crop},scale={target_w}:{target_h}[v]")

        wcx, wcy, wcw, wch = cam_box
        z_bg = z * motion_zoom_mult if not is_video_render else z
        bg_w, bg_h = _compute_aspect_crop_dims(source_w, source_h, target_ratio, z_bg)
        bg_crop = _build_crop_filter_or_coords(
            kfs, bg_w, bg_h, source_w, source_h, pan_norm,
            "action_cx", "action_cy", (acx, acy),
            is_video_render, preview_rel_t,
        )
        bg_scale = (
            f"fps={eff_fps:.3f},zoompan=z='{motion_z_expr}':d=1:x='iw/2-(iw/zoom/2)':y='ih/2-(ih/zoom/2)':"
            f"s={target_w}x{target_h}:fps={eff_fps:.3f}"
            if (is_video_render and motion_z_expr)
            else f"scale={target_w}:{target_h}"
        )

        pip_w = int(round(target_w * 0.36))
        pip_w = pip_w if pip_w % 2 == 0 else pip_w - 1
        safe_ar = max(0.95, min(1.85, float(cam_ar)))
        pip_h = int(round(pip_w / safe_ar))
        pip_h = max(48, pip_h if pip_h % 2 == 0 else pip_h - 1)

        border_px = 2
        card_w = pip_w + border_px * 2
        card_h = pip_h + border_px * 2
        out_corner = actual_corner if (webcam_corner and webcam_corner != "auto") else "top_right"
        px, py = _pip_safe_xy(out_corner, target_w, target_h, card_w, card_h)
        sh_x = max(0, px - 6)
        sh_y = max(0, py - 4)
        round_mask_f = _rounded_pip_alpha_filter(radius=18)

        clean_hex = (pip_border_color or "").strip().upper()
        border_col_ff = "0xFFFFFF38" if clean_hex in ("", "#FFFFFF", "FFFFFF") else f"{hex_to_ffmpeg_color(pip_border_color)}B8"

        return _finish(
            f"[{input_label}]split=2[in_base][in_pip];"
            f"[in_base]{bg_crop},{bg_scale}[bg];"
            f"[in_pip]crop={wcw}:{wch}:{wcx}:{wcy},scale={pip_w}:{pip_h}:force_original_aspect_ratio=increase,"
            f"crop={pip_w}:{pip_h},format=rgba,"
            f"pad={card_w}:{card_h}:{border_px}:{border_px}:{border_col_ff},"
            f"{round_mask_f},split=2[cam_fg][cam_sh];"
            f"[cam_sh]colorchannelmixer=rr=0:gg=0:bb=0:aa=0.40,pad=iw+16:ih+16:8:8:0x00000000,gblur=sigma=5[sh_blur];"
            f"[bg][sh_blur]overlay={sh_x}:{sh_y}[bg_s];"
            f"[bg_s][cam_fg]overlay={px}:{py}[v]"
        )

    # 3. SPLIT PODCAST (Cutaway-aware Dual-Speaker Stack + active-speaker glow)
    if layout == "split_podcast":
        single_shots = [s for s in shots if s.get("speaker_mode") == "single_speaker"]
        dual_shots = [s for s in shots if s.get("speaker_mode") != "single_speaker"]
        s1_active_shots = [s for s in dual_shots if int(s.get("active_speaker_idx", 0)) == 0]
        s2_active_shots = [s for s in dual_shots if int(s.get("active_speaker_idx", 0)) == 1]

        single_cw, single_ch = _compute_aspect_crop_dims(source_w, source_h, target_ratio, z)
        single_crop_base = _build_crop_filter_or_coords(
            kfs, single_cw, single_ch, source_w, source_h, pan_norm,
            "cx", "cy", (center_x, center_y),
            is_video_render, preview_rel_t,
        )

        # If preview frame lands inside a 1-speaker close-up cutaway, return full-frame centered speaker
        if not is_video_render and shots:
            cur_shot = min(
                shots,
                key=lambda s: 0.0
                if float(s.get("start_t", 0.0)) <= preview_rel_t <= float(s.get("end_t", 0.0))
                else min(
                    abs(preview_rel_t - float(s.get("start_t", 0.0))),
                    abs(preview_rel_t - float(s.get("end_t", 0.0))),
                ),
            )
            if cur_shot.get("speaker_mode") == "single_speaker":
                return _finish(f"[{input_label}]{single_crop_base},scale={target_w}:{target_h}[v]")
        elif is_video_render and shots and single_shots and not dual_shots:
            return _finish(f"[{input_label}]{single_crop_base},scale={target_w}:{target_h}[v]")

        is_vertical_layout = target_h >= target_w
        if is_vertical_layout:
            half_h = target_h // 2
            half_h = half_h if half_h % 2 == 0 else half_h - 1
            pane_ratio = target_w / float(half_h)
            cw, ch = _compute_aspect_crop_dims(source_w, source_h, pane_ratio, z)
            if z <= 1.0 and cw > int(source_w * 0.56):
                cw = int(source_w * 0.56)
                cw = cw if cw % 2 == 0 else max(2, cw - 1)
                ch = int(round(cw / pane_ratio))
                ch = min(source_h if source_h % 2 == 0 else source_h - 1, ch if ch % 2 == 0 else max(2, ch - 1))
            stack_filter = "vstack=inputs=2"
            out1, out2 = "top", "bot"
            sw, sh = target_w, half_h
        else:
            half_w = target_w // 2
            half_w = half_w if half_w % 2 == 0 else half_w - 1
            pane_ratio = half_w / float(target_h)
            cw, ch = _compute_aspect_crop_dims(source_w, source_h, pane_ratio, z)
            if z <= 1.0 and cw > int(source_w * 0.56):
                cw = int(source_w * 0.56)
                cw = cw if cw % 2 == 0 else max(2, cw - 1)
                ch = int(round(cw / pane_ratio))
                ch = min(source_h if source_h % 2 == 0 else source_h - 1, ch if ch % 2 == 0 else max(2, ch - 1))
            stack_filter = "hstack=inputs=2"
            out1, out2 = "left", "right"
            sw, sh = half_w, target_h

        crop1 = _build_crop_filter_or_coords(
            kfs, cw, ch, source_w, source_h, pan_norm,
            "s1_cx", "s1_cy", (speaker1_cx, speaker1_cy),
            is_video_render, preview_rel_t,
        )
        crop2 = _build_crop_filter_or_coords(
            kfs, cw, ch, source_w, source_h, pan_norm,
            "s2_cx", "s2_cy", (speaker2_cx, speaker2_cy),
            is_video_render, preview_rel_t,
        )

        # Subtle active-speaker border glow on talking speaker pane + 2px pane divider
        div1 = f",drawbox=x=0:y=ih-2:w=iw:h=2:color=0xFFFFFF@0.25:t=fill" if is_vertical_layout else ""
        glow1_fx = ""
        glow2_fx = ""
        if is_video_render and dual_shots:
            if s1_active_shots:
                en1 = _shot_enable_expr(s1_active_shots)
                glow1_fx = f",drawbox=x=2:y=2:w=iw-4:h=ih-4:color=0xFFFFFF@0.42:t=4:enable='{en1}'"
            if s2_active_shots:
                en2 = _shot_enable_expr(s2_active_shots)
                glow2_fx = f",drawbox=x=2:y=2:w=iw-4:h=ih-4:color=0xFFFFFF@0.42:t=4:enable='{en2}'"
        elif not is_video_render and dual_shots:
            cur_d = min(
                dual_shots,
                key=lambda s: 0.0
                if float(s.get("start_t", 0.0)) <= preview_rel_t <= float(s.get("end_t", 0.0))
                else min(
                    abs(preview_rel_t - float(s.get("start_t", 0.0))),
                    abs(preview_rel_t - float(s.get("end_t", 0.0))),
                ),
            )
            if int(cur_d.get("active_speaker_idx", 0)) == 0:
                glow1_fx = ",drawbox=x=2:y=2:w=iw-4:h=ih-4:color=0xFFFFFF@0.42:t=4"
            else:
                glow2_fx = ",drawbox=x=2:y=2:w=iw-4:h=ih-4:color=0xFFFFFF@0.42:t=4"

        if is_video_render and single_shots and dual_shots:
            cut_expr = _shot_enable_expr(single_shots)
            return _finish(
                f"[{input_label}]split=3[p1][p2][pcut];"
                f"[p1]{crop1},scale={sw}:{sh}{div1}{glow1_fx}[{out1}];"
                f"[p2]{crop2},scale={sw}:{sh}{glow2_fx}[{out2}];"
                f"[{out1}][{out2}]{stack_filter}[dual_v];"
                f"[pcut]{single_crop_base},scale={target_w}:{target_h}[single_v];"
                f"[dual_v][single_v]overlay=0:0:enable='{cut_expr}'[v]"
            )

        return _finish(
            f"[{input_label}]split=2[p1][p2];"
            f"[p1]{crop1},scale={sw}:{sh}{div1}{glow1_fx}[{out1}];"
            f"[p2]{crop2},scale={sw}:{sh}{glow2_fx}[{out2}];"
            f"[{out1}][{out2}]{stack_filter}[v]"
        )

    # 4. FIT + ADAPTIVE AMBIENT BLURRED BACKDROP (full uncropped center + brightness=-0.18 + 1px rim highlight)
    if layout == "fit_blur":
        z_fg = z * motion_zoom_mult if not is_video_render else z
        fg_w = int(source_w / z_fg)
        fg_h = int(source_h / z_fg)
        fg_w = fg_w if fg_w % 2 == 0 else max(2, fg_w - 1)
        fg_h = fg_h if fg_h % 2 == 0 else max(2, fg_h - 1)
        fg_x, fg_y = compute_clamped_crop_coords(0.5, 0.5, fg_w, fg_h, source_w, source_h, pan_norm=pan_norm)
        fg_crop = f"crop={fg_w}:{fg_h}:{fg_x}:{fg_y}," if (z_fg > 1.01 or abs(pan_norm) > 0.01) else ""

        if is_video_render and motion_z_expr:
            src_ar = fg_w / float(max(1, fg_h))
            if src_ar >= target_ratio:
                fit_w = target_w
                fit_h = max(2, int(round(target_w / src_ar)) // 2 * 2)
            else:
                fit_h = target_h
                fit_w = max(2, int(round(target_h * src_ar)) // 2 * 2)
            fg_scale = (
                f"fps={eff_fps:.3f},zoompan=z='{motion_z_expr}':d=1:"
                f"x='iw/2-(iw/zoom/2)':y='ih/2-(ih/zoom/2)':s={fit_w}x{fit_h}:fps={eff_fps:.3f}"
            )
        else:
            fg_scale = f"scale={target_w}:{target_h}:force_original_aspect_ratio=decrease"

        return _finish(
            f"[{input_label}]split=2[vbg][vfg];"
            f"[vbg]scale={target_w}:{target_h}:force_original_aspect_ratio=increase,"
            f"crop={target_w}:{target_h},gblur=sigma=28,eq=brightness=-0.18:saturation=1.15[bg];"
            f"[vfg]{fg_crop}{fg_scale},"
            f"format=rgba,drawbox=x=0:y=0:w=iw:h=1:color=0xFFFFFF@0.22:t=fill,"
            f"drawbox=x=0:y=ih-1:w=iw:h=1:color=0xFFFFFF@0.22:t=fill[fg];"
            f"[bg][fg]overlay=(W-w)/2:(H-h)/2[v]"
        )

    # 5. AUTO-TRACK SPEAKER & ACTION CINEMA CAM (deadzone-damped spring + instant scene cut snaps)
    z_auto = max(1.0, min(2.5, z * motion_zoom_mult)) if not is_video_render else z
    cw, ch = _compute_aspect_crop_dims(source_w, source_h, target_ratio, z_auto)
    auto_crop = _build_crop_filter_or_coords(
        kfs, cw, ch, source_w, source_h, pan_norm,
        "cx", "cy", (center_x, center_y),
        is_video_render, preview_rel_t,
    )
    if is_video_render and motion_z_expr:
        return _finish(
            f"[{input_label}]fps={eff_fps:.3f},{auto_crop},"
            f"zoompan=z='{motion_z_expr}':d=1:x='iw/2-(iw/zoom/2)':y='ih/2-(ih/zoom/2)':"
            f"s={target_w}x{target_h}:fps={eff_fps:.3f}[v]"
        )
    return _finish(f"[{input_label}]{auto_crop},scale={target_w}:{target_h}[v]")
