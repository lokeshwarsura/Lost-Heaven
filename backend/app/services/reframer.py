import math
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
    "2.39:1": (1920, 804),
}


ALL_FRAMING_TEMPLATES = (
    "auto_track",
    "fit_blur",
    "webcam_gameplay_split",
    "gameplay_webcam_pip",
    "split_podcast",
    "cinema_letterbox_blur",
    "triple_gaming_hud",
    "portrait_pip_badge",
    "dynamic_punch_crop",
    "side_by_side_50_50",
)


def canonicalize_framing_template(layout: Optional[str]) -> str:
    """Normalize layout aliases to canonical framing template names across all 10 templates."""
    raw = (layout or "auto_track").strip().lower().replace("-", "_").replace(" ", "_")
    alias_map = {
        "gameplay_webcam_pip": "gameplay_webcam_pip",
        "webcam_gameplay_pip": "gameplay_webcam_pip",
        "webcam_gameplay_split": "webcam_gameplay_split",
        "fit_blur": "fit_blur",
        "split_podcast": "split_podcast",
        "auto_track": "auto_track",
        "cinema_letterbox_blur": "cinema_letterbox_blur",
        "cinema_letterbox": "cinema_letterbox_blur",
        "letterbox_blur": "cinema_letterbox_blur",
        "cinema_239": "cinema_letterbox_blur",
        "triple_gaming_hud": "triple_gaming_hud",
        "triple_stack": "triple_gaming_hud",
        "triple_hud": "triple_gaming_hud",
        "portrait_pip_badge": "portrait_pip_badge",
        "portrait_pip": "portrait_pip_badge",
        "reaction_pip_card": "portrait_pip_badge",
        "dynamic_punch_crop": "dynamic_punch_crop",
        "action_punch_crop": "dynamic_punch_crop",
        "punch_crop": "dynamic_punch_crop",
        "side_by_side_50_50": "side_by_side_50_50",
        "side_by_side": "side_by_side_50_50",
        "split_50_50": "side_by_side_50_50",
    }
    return alias_map.get(raw, "auto_track")


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


def _even_int(val: float, minimum: int = 2) -> int:
    """Round an integer pixel dimension/offset down to an even number (minimum >= 0/2)."""
    iv = int(val)
    iv = iv if iv % 2 == 0 else iv - 1
    return max(minimum, iv)


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


def canonicalize_safe_zone(safe_zone: Any = None) -> str:
    """Normalize platform safe-zone identifiers across UI and backend callers."""
    norm = str(safe_zone or "").strip().lower().replace("-", " ").replace("_", " ")
    norm = " ".join(norm.split())
    if norm in ("tiktok", "tik tok", "tt"):
        return "tiktok"
    if norm in ("reels", "reel", "instagram", "instagram reels", "ig", "ig reels"):
        return "reels"
    if norm in ("shorts", "short", "yt shorts", "youtube shorts", "youtube"):
        return "shorts"
    if norm == "all":
        return "all"
    return "none"


def compute_top_safe_zone_y(target_h: int, safe_zone: Any = None) -> int:
    """Return even pixel Y >= 0.13 * target_h when a social safe zone is active."""
    sz = canonicalize_safe_zone(safe_zone)
    if sz not in ("tiktok", "reels", "shorts", "all"):
        return 0
    raw = max(0, int(math.ceil(max(2, int(target_h or 0)) * 0.13)))
    return raw if raw % 2 == 0 else raw + 1


def _nudge_pip_y_clear_of_captions(
    y: int,
    target_h: int,
    card_h: int,
    corner: str,
    caption_y: Optional[float],
    min_margin: int,
    min_top_margin: Optional[int] = None,
) -> int:
    """Shift PiP card vertically if user-placed captions inhabit the same vertical band."""
    eff_top = min_top_margin if min_top_margin is not None else min_margin
    if caption_y is None:
        return max(eff_top, min(max(eff_top, target_h - card_h - min_margin), y))
    try:
        cy_norm = max(0.05, min(0.95, float(caption_y)))
    except (TypeError, ValueError):
        return max(eff_top, min(max(eff_top, target_h - card_h - min_margin), y))
    cap_top = int(round(max(0.02, cy_norm - 0.11) * target_h))
    cap_bot = int(round(min(0.98, cy_norm + 0.11) * target_h))
    buf = max(8, _even_int(round(target_h * 0.01), 2))
    if y < (cap_bot + buf) and (y + card_h) > (cap_top - buf):
        cand_above = cap_top - card_h - buf
        cand_below = cap_bot + buf
        can_above = cand_above >= eff_top
        can_below = (cand_below + card_h) <= (target_h - min_margin)
        if "bottom" in corner or cy_norm >= 0.48:
            if can_above:
                y = cand_above
            elif can_below:
                y = cand_below
        else:
            if can_below:
                y = cand_below
            elif can_above:
                y = cand_above
    return max(eff_top, min(max(eff_top, target_h - card_h - min_margin), y))


def _pip_safe_xy(
    corner: str,
    target_w: int,
    target_h: int,
    card_w: int,
    card_h: int,
    caption_y: Optional[float] = None,
    safe_zone: Any = "none",
) -> Tuple[int, int]:
    """
    Compute realistic platform-safe PiP top-left coordinates clear of top Hook Banner,
    bottom captions (y + h <= 72% height on vertical outputs), and social UI rail:
    - top_left:     x=4%,  y=14% (>= 13% when social safe zone is active)
    - top_right:    x=60%, y=14% (>= 13% when social safe zone is active)
    - bottom_left:  x=4%,  y=54% (clamped so y + card_h <= 0.72 * target_h)
    - bottom_right: x=49%, y=54% (clamped so y + card_h <= 0.72 * target_h)
    """
    c_key = (corner or "top_right").strip().lower()
    is_vertical = target_h > target_w
    max_bot_y = int(round(target_h * 0.72)) - card_h if is_vertical else target_h - card_h - 12
    safe_top_y = compute_top_safe_zone_y(target_h, safe_zone)

    if c_key == "top_left":
        x = int(round(target_w * 0.04))
        base_top = min(max_bot_y, int(round(target_h * 0.14))) if is_vertical else int(round(target_h * 0.06))
        y = max(safe_top_y, base_top) if safe_top_y > 0 else base_top
    elif c_key == "bottom_left":
        x = int(round(target_w * 0.04))
        y = min(max_bot_y, int(round(target_h * 0.54))) if is_vertical else int(round(target_h * 0.54))
    elif c_key == "bottom_right":
        x = int(round(target_w * 0.49))
        y = min(max_bot_y, int(round(target_h * 0.54))) if is_vertical else int(round(target_h * 0.54))
    else:  # top_right
        x = int(round(target_w * 0.60))
        base_top = min(max_bot_y, int(round(target_h * 0.14))) if is_vertical else int(round(target_h * 0.06))
        y = max(safe_top_y, base_top) if safe_top_y > 0 else base_top

    x = max(8, min(max(8, target_w - card_w - 8), x))
    min_top_eff = max(8, safe_top_y) if ("top" in c_key and safe_top_y > 0) else 8
    y = _nudge_pip_y_clear_of_captions(y, target_h, card_h, c_key, caption_y, 8, min_top_margin=min_top_eff)
    x = _even_int(x, 8)
    y = _even_int(y, 8)
    if "top" in c_key and safe_top_y > 0 and y < safe_top_y:
        y = min(max(safe_top_y, target_h - card_h - 8), safe_top_y)
    return x, y


def _portrait_pip_safe_xy(
    corner: str,
    target_w: int,
    target_h: int,
    card_w: int,
    card_h: int,
    caption_y: Optional[float] = None,
    safe_zone: Any = "none",
) -> Tuple[int, int]:
    """
    Anchor compact 3:4 portrait facecam card cleanly inside requested output corner
    with 28px safe margin (and >= 13% top clearance when a social safe zone is active),
    automatically nudging vertically if captions share the same vertical zone.
    """
    c_key = (corner or "top_right").strip().lower()
    mx = _even_int(max(12, min(28, round(target_w * 28.0 / 1080.0))), 2)
    my = _even_int(max(12, min(28, round(target_h * 28.0 / 1920.0))), 2)
    safe_top_y = compute_top_safe_zone_y(target_h, safe_zone)
    top_my = max(my, safe_top_y) if safe_top_y > 0 else my

    if c_key == "top_left":
        x, y = mx, top_my
    elif c_key == "bottom_left":
        x, y = mx, max(my, target_h - card_h - my)
    elif c_key == "bottom_right":
        x, y = max(mx, target_w - card_w - mx), max(my, target_h - card_h - my)
    else:  # top_right
        x, y = max(mx, target_w - card_w - mx), top_my

    x = max(mx, min(max(mx, target_w - card_w - mx), x))
    min_top_eff = top_my if ("top" in c_key and safe_top_y > 0) else my
    y = _nudge_pip_y_clear_of_captions(y, target_h, card_h, c_key, caption_y, my, min_top_margin=min_top_eff)
    y_even = _even_int(y, 2)
    if "top" in c_key and safe_top_y > 0 and y_even < safe_top_y:
        y_even = safe_top_y
    return _even_int(x, 2), y_even


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
    skip_video_scan: bool = False,
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
    if skip_video_scan:
        # Fast live-preview mode: reuse cached visual_map/metadata in <1ms without multi-frame video decoding
        resolved_vm = visual_map if isinstance(visual_map, dict) else None
    elif (
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
        if layout in ("fit_blur", "cinema_letterbox_blur") and not framing_override:
            # Wide center-fit + ambient blur renders full width without face crop coordinates
            resolved_vm = visual_map
        else:
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
    source_corner: str = "auto",
) -> Tuple[Tuple[int, int, int, int], bool, float, float, float, float]:
    """
    Resolve source secondary/webcam crop box (x, y, w, h) deterministically for BOTH
    detected-webcam footage AND pure gameplay/screen recordings (never returning None).
    Returns (crop_box, has_detected_face_cam, action_cx, action_cy, cam_aspect_ratio, face_rel_y).
    """
    from app.services.webcam_detector import compute_zone_crop_box, STANDARD_CAMERA_ZONES

    cam_det = (face_track_data or {}).get("source_webcam_region") or (face_track_data or {}).get("webcam_detection")
    acx = float((cam_det or {}).get("action_cx", 0.5))
    acy = float((cam_det or {}).get("action_cy", 0.48))

    explicit_src_zone = str(source_corner or "").strip().lower()
    if explicit_src_zone in ("", "auto"):
        explicit_src_zone = str((cam_det or {}).get("locked_zone") or "auto").strip().lower()

    if explicit_src_zone in STANDARD_CAMERA_ZONES:
        zbox = compute_zone_crop_box(explicit_src_zone, source_w, source_h)
        cw = _even_int(max(32, min(source_w, int(round(float(zbox["w"]) * source_w)))), 16)
        ch = _even_int(max(32, min(source_h, int(round(float(zbox["h"]) * source_h)))), 16)
        cx = _even_int(max(0, min(source_w - cw, int(round(float(zbox["x"]) * source_w)))), 0)
        cy = _even_int(max(0, min(source_h - ch, int(round(float(zbox["y"]) * source_h)))), 0)
        return (cx, cy, cw, ch), True, acx, acy, cw / float(max(1, ch)), 0.38

    if isinstance(cam_det, dict):
        has_cam = bool(
            cam_det.get(
                "webcam_detected",
                cam_det.get("detected", cam_det.get("has_webcam", False)),
            )
        )
        # Support direct normalized (x, y, w, h) from webcam_detector.py / custom ROI
        if (has_cam or explicit_src_zone == "custom") and float(cam_det.get("w", 0.0) or 0.0) > 0.02 and float(cam_det.get("h", 0.0) or 0.0) > 0.02:
            cw = _even_int(max(32, min(source_w, int(round(float(cam_det["w"]) * source_w)))), 16)
            ch = _even_int(max(32, min(source_h, int(round(float(cam_det["h"]) * source_h)))), 16)
            cx = _even_int(max(0, min(source_w - cw, int(round(float(cam_det.get("x", 0.0)) * source_w)))), 0)
            cy = _even_int(max(0, min(source_h - ch, int(round(float(cam_det.get("y", 0.0)) * source_h)))), 0)
            cam_ar = cw / float(max(1, ch))
            fcy = float(cam_det.get("face_cy", (cy + ch * 0.38) / max(1, source_h)))
            rel_fy = max(0.20, min(0.65, ((fcy * source_h) - cy) / float(max(1, ch))))
            return (cx, cy, cw, ch), True, acx, acy, cam_ar, rel_fy

        # Support legacy norm_box
        nb = cam_det.get("norm_box")
        if has_cam and isinstance(nb, dict):
            cw = _even_int(max(32, min(source_w, int(round(float(nb.get("w", 0.28)) * source_w)))), 16)
            ch = _even_int(max(32, min(source_h, int(round(float(nb.get("h", 0.34)) * source_h)))), 16)
            cx = _even_int(max(0, min(source_w - cw, int(round(float(nb.get("x", 0.0)) * source_w)))), 0)
            cy = _even_int(max(0, min(source_h - ch, int(round(float(nb.get("y", 0.0)) * source_h)))), 0)
            return (cx, cy, cw, ch), True, acx, acy, cw / float(max(1, ch)), 0.38

        if has_cam:
            det_zone = str(cam_det.get("source_zone") or cam_det.get("corner") or fallback_corner or "top_left").strip().lower()
            if det_zone in STANDARD_CAMERA_ZONES:
                zbox = compute_zone_crop_box(det_zone, source_w, source_h)
                cw = _even_int(max(32, min(source_w, int(round(float(zbox["w"]) * source_w)))), 16)
                ch = _even_int(max(32, min(source_h, int(round(float(zbox["h"]) * source_h)))), 16)
                cx = _even_int(max(0, min(source_w - cw, int(round(float(zbox["x"]) * source_w)))), 0)
                cy = _even_int(max(0, min(source_h - ch, int(round(float(zbox["y"]) * source_h)))), 0)
                return (cx, cy, cw, ch), True, acx, acy, cw / float(max(1, ch)), 0.38

    # Deterministic zero-face corner crop so multi-region templates NEVER silently collapse to 1 pane
    auto_corner = str((cam_det or {}).get("best_corner") or "top_left").strip().lower()
    if auto_corner not in STANDARD_CAMERA_ZONES:
        auto_corner = "top_left"
    zbox = compute_zone_crop_box(auto_corner, source_w, source_h)
    cw = _even_int(max(32, min(source_w, int(round(float(zbox["w"]) * source_w)))), 16)
    ch = _even_int(max(32, min(source_h, int(round(float(zbox["h"]) * source_h)))), 16)
    cx = _even_int(max(0, min(source_w - cw, int(round(float(zbox["x"]) * source_w)))), 0)
    cy = _even_int(max(0, min(source_h - ch, int(round(float(zbox["y"]) * source_h)))), 0)
    return (cx, cy, cw, ch), False, acx, acy, cw / float(max(1, ch)), 0.38


def resolve_template_regions(
    frame_w: int,
    frame_h: int,
    template_id: str,
    analysis_data: Optional[Dict[str, Any]] = None,
    source_corner: str = "auto",
    output_corner: str = "top_right",
) -> Dict[str, Any]:
    """
    Shared deterministic region resolver used by BOTH `preview_frame.py` and `renderer.py`
    across all 10 framing templates — guaranteed valid `mod 2` `primary_box`, `secondary_box`,
    `hud_box`, and `output_corner` on webcam footage as well as pure gameplay (`face_detected=False`).
    """
    tpl = canonicalize_framing_template(template_id)
    src_w = _even_int(max(16, int(frame_w or 1920)), 16)
    src_h = _even_int(max(16, int(frame_h or 1080)), 16)
    data = analysis_data or {}
    fo = data.get("framing_override") or {}
    kfs = data.get("keyframes") or []
    cam_det = data.get("source_webcam_region") or data.get("webcam_detection") or {}

    # Output corner normalization (top_left / top_right / bottom_left / bottom_right)
    eff_out = str(output_corner or "top_right").strip().lower()
    if eff_out not in ("top_left", "top_right", "bottom_left", "bottom_right"):
        eff_out = "top_right"

    # Explicit source corner check
    eff_src = str(source_corner or "auto").strip().lower()
    if eff_src in ("", "auto"):
        lz = str(cam_det.get("locked_zone") or "auto").strip().lower()
        if lz in ("top_left", "top_right", "bottom_left", "bottom_right", "mid_left", "mid_right", "center"):
            eff_src = lz

    sec_box, has_det_cam, acx, acy, sec_ar, face_rel_y = _resolve_detected_webcam_crop(
        data,
        fallback_corner="top_left",
        source_w=src_w,
        source_h=src_h,
        source_corner=eff_src,
    )

    # Primary focal anchor: speaker/face anchor or action saliency or frame center
    fa = fo.get("focus_anchor") if isinstance(fo.get("focus_anchor"), dict) else None
    if fa and fa.get("x") is not None:
        prim_cx = max(0.05, min(0.95, float(fa["x"])))
        prim_cy = max(0.05, min(0.95, float(fa.get("y", 0.45))))
    elif tpl in ("gameplay_webcam_pip", "webcam_gameplay_split", "triple_gaming_hud", "portrait_pip_badge"):
        prim_cx = max(0.10, min(0.90, float(kfs[0].get("action_cx", acx) if kfs else acx)))
        prim_cy = max(0.10, min(0.90, float(kfs[0].get("action_cy", acy) if kfs else acy)))
    elif tpl == "side_by_side_50_50":
        prim_cx = max(0.05, min(0.48, float(kfs[0].get("s1_cx", 0.25) if kfs else 0.25)))
        prim_cy = max(0.10, min(0.90, float(kfs[0].get("s1_cy", 0.45) if kfs else 0.45)))
    else:
        prim_cx = max(0.05, min(0.95, float(kfs[0].get("cx", 0.5) if kfs else 0.5)))
        prim_cy = max(0.05, min(0.95, float(kfs[0].get("cy", 0.44) if kfs else 0.44)))

    pw = _even_int(min(src_w, max(64, int(round(src_h * 9.0 / 16.0)))), 16)
    ph = _even_int(src_h, 16)
    px, py = compute_clamped_crop_coords(prim_cx, prim_cy, pw, ph, src_w, src_h)
    primary_box = (px, py, pw, ph)

    # Override secondary_box right-half for side_by_side_50_50 when source_corner is auto and no locked corner
    if tpl == "side_by_side_50_50" and eff_src == "auto":
        sec_cx = max(0.52, min(0.95, float(kfs[0].get("s2_cx", 0.75) if kfs else 0.75)))
        sec_cy = max(0.10, min(0.90, float(kfs[0].get("s2_cy", 0.45) if kfs else 0.45)))
        sw2 = _even_int(src_w // 2, 16)
        sh2 = _even_int(src_h, 16)
        sx2, sy2 = compute_clamped_crop_coords(sec_cx, sec_cy, sw2, sh2, src_w, src_h)
        sec_box = (sx2, sy2, sw2, sh2)
        sec_ar = sw2 / float(max(1, sh2))

    # Mini-map / HUD corner crop (bottom_left default) for triple_gaming_hud bottom 22% strip
    hud_w = _even_int(min(src_w, max(64, int(round(src_w * 0.34)))), 16)
    hud_h = _even_int(min(src_h, max(48, int(round(src_h * 0.34)))), 16)
    hud_x = 0
    hud_y = _even_int(max(0, src_h - hud_h), 0)
    hud_box = (hud_x, hud_y, hud_w, hud_h)

    return {
        "template_id": tpl,
        "primary_box": primary_box,
        "primary_center": (round(prim_cx, 4), round(prim_cy, 4)),
        "secondary_box": sec_box,
        "secondary_aspect": round(sec_ar, 4),
        "hud_box": hud_box,
        "has_detected_cam": has_det_cam,
        "source_corner": eff_src,
        "output_corner": eff_out,
        "face_rel_y": face_rel_y,
    }


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
    center_vertical: bool = False,
) -> str:
    """
    Shared keyframe -> crop compiler ensuring exact pixel coordinate parity between
    single-frame preview (`is_video_render=False`) and full MP4 export (`is_video_render=True`),
    holding pre-cut coordinates cleanly up to `t_cut - 0.004s` before snapping instantaneously.
    """
    max_x = max(0, source_w - cw)
    max_y = max(0, source_h - ch)
    overscan_y = _even_int(max(0, round(max_y * 0.35)), 0) if center_vertical else 0

    def _resolve_xy(fx_v: float, fy_v: float) -> Tuple[int, int]:
        px, py = compute_clamped_crop_coords(fx_v, fy_v, cw, ch, source_w, source_h, pan_norm=pan_norm)
        if center_vertical:
            centered_y = int(round(max(0.0, min(1.0, fy_v)) * source_h - ch * 0.5))
            py = _even_int(max(overscan_y, min(max(overscan_y, max_y - overscan_y), centered_y)), 0)
        return px, py

    if not kfs:
        px, py = _resolve_xy(fallback_xy[0], fallback_xy[1])
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
        px, py = _resolve_xy(fx_val, fy_val)
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
    source_corner: str = "auto",
    caption_y: Optional[float] = None,
    safe_zone: str = "none",
    framing_template: Optional[str] = None,
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
    layout = canonicalize_framing_template(framing_template if framing_template is not None else frame_layout)
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

    # Support explicit normalized custom crop box and Start->End keyframe framing
    fo = (face_track_data or {}).get("framing_override") or {}
    if isinstance(fo, dict) and not bool(fo.get("disabled", False)):
        kf_start_box = fo.get("keyframe_start_crop") if isinstance(fo.get("keyframe_start_crop"), dict) else None
        kf_end_box = fo.get("keyframe_end_crop") if isinstance(fo.get("keyframe_end_crop"), dict) else None
        static_frame_crop = fo.get("frame_crop") if isinstance(fo.get("frame_crop"), dict) else None
    else:
        kf_start_box = kf_end_box = static_frame_crop = None
    if (
        (kf_start_box or kf_end_box or static_frame_crop)
        and layout in ("auto_track", "dynamic_punch_crop")
    ):
        s_box = kf_start_box or static_frame_crop or kf_end_box
        e_box = kf_end_box or kf_start_box or static_frame_crop
        if isinstance(s_box, dict) and isinstance(e_box, dict):
            def _box_to_px(bx: Dict[str, Any]) -> Tuple[int, int, int, int]:
                bw = _even_int(max(0.10, min(1.0, float(bx.get("w", 0.5)))) * source_w, 16)
                bh = _even_int(max(0.10, min(1.0, float(bx.get("h", 0.9)))) * source_h, 16)
                bw = min(_even_int(source_w, 16), bw)
                bh = min(_even_int(source_h, 16), bh)
                cx_px = clamp_crop_coordinates(float(bx.get("x", 0.25)) * source_w, bw, source_w)
                cy_px = int(round(max(0.0, min(float(source_h - bh), float(bx.get("y", 0.05)) * source_h))))
                cy_px = cy_px if cy_px % 2 == 0 else max(0, cy_px - 1)
                return cx_px, cy_px, bw, bh

            sx, sy, sw_px, sh_px = _box_to_px(s_box)
            ex, ey, ew_px, eh_px = _box_to_px(e_box)
            dur_safe = max(0.2, float(eff_dur or 8.0))
            if not is_video_render or (abs(sx - ex) <= 2 and abs(sy - ey) <= 2 and abs(sw_px - ew_px) <= 4 and abs(sh_px - eh_px) <= 4):
                alpha = max(0.0, min(1.0, float(preview_rel_t or 0.0) / dur_safe)) if not is_video_render else 0.0
                ix = sx + (ex - sx) * alpha
                iy = sy + (ey - sy) * alpha
                iw_f = sw_px + (ew_px - sw_px) * alpha
                ih_f = sh_px + (eh_px - sh_px) * alpha
                cw_p = min(_even_int(source_w, 16), _even_int(iw_f, 16))
                ch_p = min(_even_int(source_h, 16), _even_int(ih_f, 16))
                cx_p = clamp_crop_coordinates(ix, cw_p, source_w)
                cy_p = int(round(max(0.0, min(float(source_h - ch_p), iy))))
                cy_p = cy_p if cy_p % 2 == 0 else max(0, cy_p - 1)
                return _finish(f"[{input_label}]crop={cw_p}:{ch_p}:{cx_p}:{cy_p},scale={target_w}:{target_h},setsar=1[v]")
            elif abs(sw_px - ew_px) <= 4 and abs(sh_px - eh_px) <= 4:
                max_cx = max(0, source_w - sw_px)
                max_cy = max(0, source_h - sh_px)
                x_expr = f"max(0,min({max_cx},{sx}+({ex - sx})*clip(t/{dur_safe:.3f},0,1)))"
                y_expr = f"max(0,min({max_cy},{sy}+({ey - sy})*clip(t/{dur_safe:.3f},0,1)))"
                return _finish(f"[{input_label}]crop={sw_px}:{sh_px}:'{x_expr}':'{y_expr}',scale={target_w}:{target_h},setsar=1[v]")
            else:
                # Start->End box size/position change: per-frame dynamic scale + exact fixed crop (preserves aspect geometry)
                prog = f"clip(t/{dur_safe:.3f},0,1)"
                w_expr = f"({sw_px}+({ew_px - sw_px})*{prog})"
                h_expr = f"({sh_px}+({eh_px - sh_px})*{prog})"
                x_expr = f"({sx}+({ex - sx})*{prog})"
                y_expr = f"({sy}+({ey - sy})*{prog})"
                sw_expr = f"max({target_w},trunc(iw*{target_w}/{w_expr}/2)*2)"
                sh_expr = f"max({target_h},trunc(ih*{target_h}/{h_expr}/2)*2)"
                cx_expr = f"max(0,min(iw-{target_w},{x_expr}*{target_w}/{w_expr}))"
                cy_expr = f"max(0,min(ih-{target_h},{y_expr}*{target_h}/{h_expr}))"
                return _finish(
                    f"[{input_label}]scale=w='{sw_expr}':h='{sh_expr}':eval=frame:flags=lanczos,"
                    f"crop={target_w}:{target_h}:'{cx_expr}':'{cy_expr}',setsar=1[v]"
                )

    # Shared dual-region geometry (works identically on webcam footage & zero-face gameplay)
    regions = resolve_template_regions(
        source_w,
        source_h,
        layout,
        analysis_data=face_track_data,
        source_corner=source_corner,
        output_corner=webcam_corner if (webcam_corner and webcam_corner != "auto") else "top_right",
    )
    prim_cx, prim_cy = regions["primary_center"]

    # 1. WEBCAM + GAMEPLAY SPLIT-SCREEN (Top 38% secondary + 2px divider, Bottom 62% primary action)
    if layout == "webcam_gameplay_split":
        raw_sr = float(webcam_split_ratio if webcam_split_ratio is not None else 0.38)
        split_ratio = 0.38 if abs(raw_sr - 0.40) < 0.005 else max(0.28, min(0.50, raw_sr))
        top_h = _even_int(target_h * split_ratio, 16)
        bot_h = _even_int(target_h - top_h, 16)

        wcx, wcy, wcw, wch = regions["secondary_box"]
        game_ratio = target_w / float(max(2, bot_h))
        gw, gh = _compute_aspect_crop_dims(source_w, source_h, game_ratio, z)
        game_crop = _build_crop_filter_or_coords(
            kfs, gw, gh, source_w, source_h, pan_norm,
            "action_cx", "action_cy", (prim_cx, prim_cy),
            is_video_render, preview_rel_t,
        )

        top_ar = target_w / float(max(2, top_h))
        cam_crop_h = _even_int(max(32, round(wcw / top_ar)), 16)
        cam_crop_h = min(wch, cam_crop_h)
        face_cy_norm = float(
            ((face_track_data or {}).get("source_webcam_region") or {}).get(
                "face_cy", (wcy + wch * 0.36) / max(1, source_h)
            )
        )
        ideal_cam_y = int(round((face_cy_norm * source_h) - (cam_crop_h * 0.36)))
        adj_wcy = max(wcy, min(wcy + max(0, wch - cam_crop_h), ideal_cam_y))
        adj_wcy = _even_int(max(0, adj_wcy), 0)

        return _finish(
            f"[{input_label}]split=2[in_cam][in_game];"
            f"[in_cam]crop={wcw}:{wch}:{wcx}:{wcy},scale={target_w}:{top_h}:force_original_aspect_ratio=increase,"
            f"crop={target_w}:{top_h}:(iw-{target_w})/2:{max(0, min(top_h // 4, adj_wcy - wcy))},"
            f"drawbox=x=0:y=ih-3:w=iw:h=3:color=0xFFFFFF@0.28:t=fill[top];"
            f"[in_game]{game_crop},scale={target_w}:{bot_h}[bot];"
            f"[top][bot]vstack=inputs=2[v]"
        )

    # 2. GAMEPLAY + WEBCAM PiP OVERLAY (Primary base + 30% wide rounded PiP card of secondary_box, 2px border)
    if layout == "gameplay_webcam_pip":
        wcx, wcy, wcw, wch = regions["secondary_box"]
        cam_ar = float(regions["secondary_aspect"] or 1.3333)
        z_bg = z * motion_zoom_mult if not is_video_render else z
        bg_w, bg_h = _compute_aspect_crop_dims(source_w, source_h, target_ratio, z_bg)
        bg_crop = _build_crop_filter_or_coords(
            kfs, bg_w, bg_h, source_w, source_h, pan_norm,
            "action_cx", "action_cy", (prim_cx, prim_cy),
            is_video_render, preview_rel_t,
        )
        bg_scale = (
            f"fps={eff_fps:.3f},zoompan=z='{motion_z_expr}':d=1:x='iw/2-(iw/zoom/2)':y='ih/2-(ih/zoom/2)':"
            f"s={target_w}x{target_h}:fps={eff_fps:.3f}"
            if (is_video_render and motion_z_expr)
            else f"scale={target_w}:{target_h}"
        )

        pip_w = _even_int(round(target_w * 0.30), 2)
        safe_ar = max(0.95, min(1.85, float(cam_ar)))
        pip_h = _even_int(round(pip_w / safe_ar), 48)

        border_px = 2
        card_w = _even_int(pip_w + border_px * 2, 2)
        card_h = _even_int(pip_h + border_px * 2, 2)
        out_corner = regions["output_corner"]
        px, py = _pip_safe_xy(
            out_corner, target_w, target_h, card_w, card_h,
            caption_y=caption_y, safe_zone=safe_zone,
        )
        sh_x = _even_int(max(0, px - 6), 0)
        sh_y = _even_int(max(0, py - 4), 0)
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
            half_h = _even_int(target_h // 2, 2)
            rem_h = _even_int(max(2, target_h - half_h), 2)
            pane_ratio = target_w / float(half_h)
            cw, ch = _compute_aspect_crop_dims(source_w, source_h, pane_ratio, z)
            if z <= 1.0 and cw > int(source_w * 0.56):
                cw = int(source_w * 0.56)
                cw = cw if cw % 2 == 0 else max(2, cw - 1)
                ch = int(round(cw / pane_ratio))
                ch = min(source_h if source_h % 2 == 0 else source_h - 1, ch if ch % 2 == 0 else max(2, ch - 1))
            stack_filter = "vstack=inputs=2"
            out1, out2 = "top", "bot"
            sw1, sh1 = target_w, half_h
            sw2, sh2 = target_w, rem_h
        else:
            half_w = _even_int(target_w // 2, 2)
            rem_w = _even_int(max(2, target_w - half_w), 2)
            pane_ratio = half_w / float(target_h)
            cw, ch = _compute_aspect_crop_dims(source_w, source_h, pane_ratio, z)
            if z <= 1.0 and cw > int(source_w * 0.56):
                cw = int(source_w * 0.56)
                cw = cw if cw % 2 == 0 else max(2, cw - 1)
                ch = int(round(cw / pane_ratio))
                ch = min(source_h if source_h % 2 == 0 else source_h - 1, ch if ch % 2 == 0 else max(2, ch - 1))
            stack_filter = "hstack=inputs=2"
            out1, out2 = "left", "right"
            sw1, sh1 = half_w, target_h
            sw2, sh2 = rem_w, target_h

        crop1 = _build_crop_filter_or_coords(
            kfs, cw, ch, source_w, source_h, pan_norm,
            "s1_cx", "s1_cy", (0.25 if abs(speaker1_cx - 0.3) < 1e-6 else speaker1_cx, speaker1_cy),
            is_video_render, preview_rel_t,
        )
        crop2 = _build_crop_filter_or_coords(
            kfs, cw, ch, source_w, source_h, pan_norm,
            "s2_cx", "s2_cy", (0.75 if abs(speaker2_cx - 0.7) < 1e-6 else speaker2_cx, speaker2_cy),
            is_video_render, preview_rel_t,
        )

        # Subtle active-speaker border glow on talking speaker pane + 3px pane divider
        div1 = f",drawbox=x=0:y=ih-3:w=iw:h=3:color=0xFFFFFF@0.28:t=fill" if is_vertical_layout else ""
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

        if regions["source_corner"] != "auto":
            sx, sy, sw_r, sh_r = regions["secondary_box"]
            top_pod_chain = (
                f"crop={sw_r}:{sh_r}:{sx}:{sy},"
                f"scale={sw1}:{sh1}:force_original_aspect_ratio=increase,crop={sw1}:{sh1}{div1}{glow1_fx}"
            )
            bot_pod_chain = (
                f"{_build_crop_filter_or_coords(kfs, cw, ch, source_w, source_h, pan_norm, 'action_cx', 'action_cy', (prim_cx, prim_cy), is_video_render, preview_rel_t)},"
                f"scale={sw2}:{sh2}{glow2_fx}"
            )
            return _finish(
                f"[{input_label}]split=2[p1][p2];"
                f"[p1]{top_pod_chain}[{out1}];"
                f"[p2]{bot_pod_chain}[{out2}];"
                f"[{out1}][{out2}]{stack_filter}[v]"
            )

        if is_video_render and single_shots and dual_shots:
            cut_expr = _shot_enable_expr(single_shots)
            return _finish(
                f"[{input_label}]split=3[p1][p2][pcut];"
                f"[p1]{crop1},scale={sw1}:{sh1}{div1}{glow1_fx}[{out1}];"
                f"[p2]{crop2},scale={sw2}:{sh2}{glow2_fx}[{out2}];"
                f"[{out1}][{out2}]{stack_filter}[dual_v];"
                f"[pcut]{single_crop_base},scale={target_w}:{target_h}[single_v];"
                f"[dual_v][single_v]overlay=0:0:enable='{cut_expr}'[v]"
            )

        return _finish(
            f"[{input_label}]split=2[p1][p2];"
            f"[p1]{crop1},scale={sw1}:{sh1}{div1}{glow1_fx}[{out1}];"
            f"[p2]{crop2},scale={sw2}:{sh2}{glow2_fx}[{out2}];"
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
            fg_scale = f"scale={target_w}:{target_h}:force_original_aspect_ratio=decrease,scale=trunc(iw/2)*2:trunc(ih/2)*2"

        return _finish(
            f"[{input_label}]split=2[vbg][vfg];"
            f"[vbg]scale={target_w}:{target_h}:force_original_aspect_ratio=increase,"
            f"crop={target_w}:{target_h},gblur=sigma=28,eq=brightness=-0.18:saturation=1.15[bg];"
            f"[vfg]{fg_crop}{fg_scale},"
            f"format=rgba,drawbox=x=0:y=0:w=iw:h=1:color=0xFFFFFF@0.22:t=fill,"
            f"drawbox=x=0:y=ih-1:w=iw:h=1:color=0xFFFFFF@0.22:t=fill[fg];"
            f"[bg][fg]overlay=(W-w)/2:(H-h)/2[v]"
        )

    # 5. CINEMA 2.39:1 + AMBIENT BLUR (wide cinematic strip + crisp 2px cinema bars + blurred top/bottom fill)
    if layout == "cinema_letterbox_blur":
        cin_w = _even_int(source_w / z, 16)
        cin_h = _even_int(min(source_h, max(16, int(round(cin_w / 2.39)))), 16)
        cin_x, cin_y = compute_clamped_crop_coords(prim_cx, prim_cy, cin_w, cin_h, source_w, source_h, pan_norm=pan_norm)
        fg_w = target_w
        fg_h = _even_int(min(target_h, max(32, int(round(target_w / 2.39)))), 16)
        matte_bar = 2
        return _finish(
            f"[{input_label}]split=2[cbg][cfg];"
            f"[cbg]scale={target_w}:{target_h}:force_original_aspect_ratio=increase,"
            f"crop={target_w}:{target_h},gblur=sigma=30,eq=brightness=-0.22:saturation=1.10[bg];"
            f"[cfg]crop={cin_w}:{cin_h}:{cin_x}:{cin_y},scale={fg_w}:{fg_h},"
            f"drawbox=x=0:y=0:w=iw:h={matte_bar}:color=black@1.0:t=fill,"
            f"drawbox=x=0:y=ih-{matte_bar}:w=iw:h={matte_bar}:color=black@1.0:t=fill,"
            f"drawbox=x=0:y={matte_bar}:w=iw:h=1:color=0xFFFFFF@0.22:t=fill,"
            f"drawbox=x=0:y=ih-{matte_bar + 1}:w=iw:h=1:color=0xFFFFFF@0.22:t=fill[fg];"
            f"[bg][fg]overlay=(W-w)/2:(H-h)/2[v]"
        )

    # 6. TRIPLE GAMING HUD (26% top secondary_box + 52% middle primary_box + 22% bottom-left HUD/map strip)
    if layout == "triple_gaming_hud":
        top_h = _even_int(target_h * 0.26, 16)
        bot_h = _even_int(target_h * 0.22, 16)
        mid_h = _even_int(target_h - top_h - bot_h, 16)
        if top_h + mid_h + bot_h != target_h:
            mid_h = _even_int(max(16, target_h - top_h - bot_h), 16)

        wcx, wcy, wcw, wch = regions["secondary_box"]
        mid_ratio = target_w / float(mid_h)
        mw, mh = _compute_aspect_crop_dims(source_w, source_h, mid_ratio, z)
        mid_crop = _build_crop_filter_or_coords(
            kfs, mw, mh, source_w, source_h, pan_norm,
            "action_cx", "action_cy", (prim_cx, prim_cy),
            is_video_render, preview_rel_t,
        )

        top_filter = (
            f"crop={wcw}:{wch}:{wcx}:{wcy},"
            f"scale={target_w}:{top_h}:force_original_aspect_ratio=increase,"
            f"crop={target_w}:{top_h}:(iw-{target_w})/2:(ih-{top_h})/3"
        )

        # Bottom-left mini-map / HUD corner zoom (bottom 22% strip)
        hx, hy, hw_r, hh_r = regions["hud_box"]
        hud_ratio = target_w / float(bot_h)
        hw = _even_int(min(source_w, max(hw_r, int(round(source_w * 0.34)))), 16)
        hh = _even_int(min(source_h, max(32, int(round(hw / hud_ratio)))), 16)
        hw = _even_int(min(source_w, int(round(hh * hud_ratio))), 16)
        hx = _even_int(max(0, min(source_w - hw, hx)), 0)
        hy = _even_int(max(0, source_h - hh), 0)

        return _finish(
            f"[{input_label}]split=3[t_cam][t_mid][t_hud];"
            f"[t_cam]{top_filter},drawbox=x=0:y=ih-2:w=iw:h=2:color=0xFFFFFF@0.28:t=fill[top];"
            f"[t_mid]{mid_crop},scale={target_w}:{mid_h},drawbox=x=0:y=ih-2:w=iw:h=2:color=0xFFFFFF@0.28:t=fill[mid];"
            f"[t_hud]crop={hw}:{hh}:{hx}:{hy},scale={target_w}:{bot_h}[bot];"
            f"[top][mid][bot]vstack=inputs=3[v]"
        )

    # 7. PORTRAIT FACECAM CARD + FULL SCREEN (Full-bleed 9:16 fill + 26% 3:4 rounded corner card)
    if layout == "portrait_pip_badge":
        wcx, wcy, wcw, wch = regions["secondary_box"]
        # Slight vertical fill zoom (>= 1.08x) + centered overscan crop eliminates top/bottom letterbox bars cleanly
        z_bg = max(1.08, float(z))
        if not is_video_render:
            z_bg = max(1.08, z_bg * motion_zoom_mult)
        bg_w, bg_h = _compute_aspect_crop_dims(source_w, source_h, target_ratio, z_bg)
        bg_crop = _build_crop_filter_or_coords(
            kfs, bg_w, bg_h, source_w, source_h, pan_norm,
            "action_cx", "action_cy", (prim_cx, prim_cy),
            is_video_render, preview_rel_t,
            center_vertical=True,
        )
        bg_scale = (
            f"fps={eff_fps:.3f},zoompan=z='{motion_z_expr}':d=1:x='iw/2-(iw/zoom/2)':y='ih/2-(ih/zoom/2)':"
            f"s={target_w}x{target_h}:fps={eff_fps:.3f}"
            if (is_video_render and motion_z_expr)
            else f"scale={target_w}:{target_h}:force_original_aspect_ratio=increase,crop={target_w}:{target_h}"
        )

        pip_w = _even_int(round(target_w * 0.27), 2)
        pip_h = _even_int(round(pip_w * 4.0 / 3.0), 48)
        if pip_h > int(target_h * 0.44):
            pip_h = _even_int(round(target_h * 0.44), 48)
            pip_w = _even_int(round(pip_h * 3.0 / 4.0), 2)

        border_px = 2
        card_w = _even_int(pip_w + border_px * 2, 2)
        card_h = _even_int(pip_h + border_px * 2, 2)
        out_corner = regions["output_corner"]
        px, py = _portrait_pip_safe_xy(
            out_corner, target_w, target_h, card_w, card_h,
            caption_y=caption_y, safe_zone=safe_zone,
        )
        sh_x = _even_int(max(0, px - 6), 0)
        sh_y = _even_int(max(0, py - 4), 0)
        round_mask_f = _rounded_pip_alpha_filter(radius=20)
        clean_hex = (pip_border_color or "").strip().upper()
        border_col_ff = "0xFFFFFF44" if clean_hex in ("", "#FFFFFF", "FFFFFF") else f"{hex_to_ffmpeg_color(pip_border_color)}C4"

        return _finish(
            f"[{input_label}]split=2[p_base][p_pip];"
            f"[p_base]{bg_crop},{bg_scale}[bg];"
            f"[p_pip]crop={wcw}:{wch}:{wcx}:{wcy},scale={pip_w}:{pip_h}:force_original_aspect_ratio=increase,"
            f"crop={pip_w}:{pip_h},format=rgba,"
            f"pad={card_w}:{card_h}:{border_px}:{border_px}:{border_col_ff},"
            f"{round_mask_f},split=2[cam_fg][cam_sh];"
            f"[cam_sh]colorchannelmixer=rr=0:gg=0:bb=0:aa=0.40,pad=iw+16:ih+16:8:8:0x00000000,gblur=sigma=5[sh_blur];"
            f"[bg][sh_blur]overlay={sh_x}:{sh_y}[bg_s];"
            f"[bg_s][cam_fg]overlay={px}:{py}[v]"
        )

    # 8. ACTION PUNCH CROP (1.28x Smart Focus tight fill without blur bars)
    if layout == "dynamic_punch_crop":
        z_punch = max(1.28, min(2.5, z * 1.28))
        if not is_video_render:
            z_punch = max(1.28, min(2.5, z_punch * motion_zoom_mult))
        pcw, pch = _compute_aspect_crop_dims(source_w, source_h, target_ratio, z_punch)
        punch_crop = _build_crop_filter_or_coords(
            kfs, pcw, pch, source_w, source_h, pan_norm,
            "cx", "cy", (prim_cx, prim_cy),
            is_video_render, preview_rel_t,
        )
        if is_video_render and motion_z_expr:
            return _finish(
                f"[{input_label}]fps={eff_fps:.3f},{punch_crop},"
                f"zoompan=z='{motion_z_expr}':d=1:x='iw/2-(iw/zoom/2)':y='ih/2-(ih/zoom/2)':"
                f"s={target_w}x{target_h}:fps={eff_fps:.3f}[v]"
            )
        return _finish(f"[{input_label}]{punch_crop},scale={target_w}:{target_h}[v]")

    # 9. SIDE-BY-SIDE 50/50 VERTICAL SPLIT (Left 50% primary/left + 2px divider + Right 50% secondary/right)
    if layout == "side_by_side_50_50":
        left_w = _even_int(target_w // 2, 16)
        right_w = _even_int(target_w - left_w, 16)
        if left_w + right_w != target_w:
            right_w = _even_int(max(16, target_w - left_w), 16)
        left_ratio = left_w / float(max(2, target_h))
        cw, ch = _compute_aspect_crop_dims(source_w, source_h, left_ratio, z)
        if z <= 1.0 and cw > int(source_w * 0.56):
            cw = _even_int(source_w * 0.56, 16)
            ch = _even_int(min(source_h, round(cw / left_ratio)), 16)
        crop_l = _build_crop_filter_or_coords(
            kfs, cw, ch, source_w, source_h, pan_norm,
            "s1_cx", "s1_cy", (prim_cx, prim_cy),
            is_video_render, preview_rel_t,
        )
        if regions["source_corner"] != "auto":
            sx, sy, sw_r, sh_r = regions["secondary_box"]
            right_chain = (
                f"crop={sw_r}:{sh_r}:{sx}:{sy},"
                f"scale={right_w}:{target_h}:force_original_aspect_ratio=increase,"
                f"crop={right_w}:{target_h}"
            )
        else:
            crop_r = _build_crop_filter_or_coords(
                kfs, cw, ch, source_w, source_h, pan_norm,
                "s2_cx", "s2_cy", (speaker2_cx if speaker2_cx != 0.7 else 0.75, speaker2_cy),
                is_video_render, preview_rel_t,
            )
            right_chain = f"{crop_r},scale={right_w}:{target_h}"
        return _finish(
            f"[{input_label}]split=2[s_left][s_right];"
            f"[s_left]{crop_l},scale={left_w}:{target_h},drawbox=x=iw-3:y=0:w=3:h=ih:color=0xFFFFFF@0.30:t=fill[left];"
            f"[s_right]{right_chain}[right];"
            f"[left][right]hstack=inputs=2[v]"
        )

    # 10. AUTO-TRACK SPEAKER & ACTION CINEMA CAM (deadzone-damped spring + instant scene cut snaps)
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
