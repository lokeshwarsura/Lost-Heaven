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


def _pip_overlay_coords(corner: str, margin_x: int = 24, margin_y: int = 36) -> str:
    if corner == "top_left":
        return f"{margin_x}:{margin_y}"
    if corner == "bottom_left":
        return f"{margin_x}:H-h-{margin_y}"
    if corner == "bottom_right":
        return f"W-w-{margin_x}:H-h-{margin_y}"
    # top_right default
    return f"W-w-{margin_x}:{margin_y}"


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
    webcam_split_ratio: float = 0.38,
    pip_border_color: str = "#F59E0B",
    zoom: float = 1.0,
    pan_x_offset: float = 0.0,
    pan_x: Optional[float] = None,
    face_track_data: Optional[Dict[str, Any]] = None,
    is_video_render: bool = False,
    preview_rel_t: float = 0.0,
    target_size: Optional[Tuple[int, int]] = None,
    input_label: str = "0:v",
) -> str:
    """
    Generate complete FFmpeg video filtergraph string for all 5 framing templates:
    1. auto_track (smooth dynamic face-tracked crop + zoom + pan)
    2. fit_blur (gblur=sigma=28,eq=brightness=-0.08 background + zoomed/panned foreground)
    3. webcam_gameplay_split (top 38% webcam corner + bottom 62% gameplay, vstack)
    4. gameplay_webcam_pip / webcam_gameplay_pip (full gameplay + corner PiP in webcam_corner)
    5. split_podcast (dual-speaker tracked crops stacked vstack/hstack)
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

    # 1. WEBCAM + GAMEPLAY SPLIT-SCREEN (Top 38% webcam, Bottom 62% gameplay)
    if layout == "webcam_gameplay_split":
        split_ratio = max(0.25, min(0.50, float(webcam_split_ratio or 0.38)))
        top_h = int(target_h * split_ratio)
        top_h = top_h if top_h % 2 == 0 else top_h - 1
        bot_h = target_h - top_h

        wcx, wcy, wcw, wch = get_webcam_crop_coordinates(actual_corner, source_w, source_h)

        # Gameplay crop honors zoom & pan_norm
        game_ratio = target_w / float(bot_h)
        gw, gh = _compute_aspect_crop_dims(source_w, source_h, game_ratio, z)
        gx, gy = compute_clamped_crop_coords(0.5, 0.5, gw, gh, source_w, source_h, pan_norm=pan_norm)

        return (
            f"[{input_label}]split=2[in_cam][in_game];"
            f"[in_cam]crop={wcw}:{wch}:{wcx}:{wcy},scale={target_w}:{top_h}:force_original_aspect_ratio=increase,crop={target_w}:{top_h}[top];"
            f"[in_game]crop={gw}:{gh}:{gx}:{gy},scale={target_w}:{bot_h}[bot];"
            f"[top][bot]vstack=inputs=2[v]"
        )

    # 2. GAMEPLAY + WEBCAM PiP OVERLAY (Corner overlay in webcam_corner)
    if layout == "gameplay_webcam_pip":
        wcx, wcy, wcw, wch = get_webcam_crop_coordinates(actual_corner, source_w, source_h)

        bg_w, bg_h = _compute_aspect_crop_dims(source_w, source_h, target_ratio, z)
        bg_x, bg_y = compute_clamped_crop_coords(0.5, 0.5, bg_w, bg_h, source_w, source_h, pan_norm=pan_norm)

        pip_w = int(target_w * 0.32)
        pip_w = pip_w if pip_w % 2 == 0 else pip_w - 1
        pip_h = int(pip_w * (wch / float(wcw)))
        pip_h = pip_h if pip_h % 2 == 0 else pip_h - 1

        color_hex = hex_to_ffmpeg_color(pip_border_color)
        border_px = 5
        overlay_xy = _pip_overlay_coords(actual_corner, margin_x=24, margin_y=36)

        return (
            f"[{input_label}]split=2[in_base][in_pip];"
            f"[in_base]crop={bg_w}:{bg_h}:{bg_x}:{bg_y},scale={target_w}:{target_h}[bg];"
            f"[in_pip]crop={wcw}:{wch}:{wcx}:{wcy},scale={pip_w}:{pip_h},"
            f"pad={pip_w + border_px * 2}:{pip_h + border_px * 2}:{border_px}:{border_px}:{color_hex}[cam];"
            f"[bg][cam]overlay={overlay_xy}[v]"
        )

    # 3. SPLIT PODCAST (Dual-speaker independent tracking)
    if layout == "split_podcast":
        kfs = (face_track_data or {}).get("keyframes") or []
        is_vertical_layout = target_h >= target_w
        if is_vertical_layout:
            half_h = target_h // 2
            half_h = half_h if half_h % 2 == 0 else half_h - 1
            pane_ratio = target_w / float(half_h)
            cw, ch = _compute_aspect_crop_dims(source_w, source_h, pane_ratio, z)
            cw = min(cw, int(source_w * 0.55) if z <= 1.0 else cw)
            cw = cw if cw % 2 == 0 else max(2, cw - 1)
            stack_filter = "vstack=inputs=2"
            out1, out2 = "top", "bot"
            sw, sh = target_w, half_h
        else:
            half_w = target_w // 2
            half_w = half_w if half_w % 2 == 0 else half_w - 1
            pane_ratio = half_w / float(target_h)
            cw, ch = _compute_aspect_crop_dims(source_w, source_h, pane_ratio, z)
            cw = min(cw, int(source_w * 0.55) if z <= 1.0 else cw)
            cw = cw if cw % 2 == 0 else max(2, cw - 1)
            stack_filter = "hstack=inputs=2"
            out1, out2 = "left", "right"
            sw, sh = half_w, target_h

        max_x = max(0, source_w - cw)
        max_y = max(0, source_h - ch)

        if is_video_render and len(kfs) >= 2:
            pts_x1, pts_y1, pts_x2, pts_y2 = [], [], [], []
            for kf in kfs:
                t_val = float(kf.get("t", 0.0))
                x1, y1 = compute_clamped_crop_coords(
                    float(kf.get("s1_cx", speaker1_cx)),
                    float(kf.get("s1_cy", speaker1_cy)),
                    cw, ch, source_w, source_h, pan_norm=pan_norm
                )
                x2, y2 = compute_clamped_crop_coords(
                    float(kf.get("s2_cx", speaker2_cx)),
                    float(kf.get("s2_cy", speaker2_cy)),
                    cw, ch, source_w, source_h, pan_norm=pan_norm
                )
                pts_x1.append((t_val, x1))
                pts_y1.append((t_val, y1))
                pts_x2.append((t_val, x2))
                pts_y2.append((t_val, y2))

            x1_expr = compile_piecewise_linear_expr(pts_x1, 0, max_x)
            y1_expr = compile_piecewise_linear_expr(pts_y1, 0, max_y)
            x2_expr = compile_piecewise_linear_expr(pts_x2, 0, max_x)
            y2_expr = compile_piecewise_linear_expr(pts_y2, 0, max_y)
            crop1 = f"crop={cw}:{ch}:'{x1_expr}':'{y1_expr}'"
            crop2 = f"crop={cw}:{ch}:'{x2_expr}':'{y2_expr}'"
        else:
            s1x, s1y = speaker1_cx, speaker1_cy
            s2x, s2y = speaker2_cx, speaker2_cy
            if kfs:
                nearest = min(kfs, key=lambda k: abs(float(k.get("t", 0.0)) - preview_rel_t))
                s1x = float(nearest.get("s1_cx", s1x))
                s1y = float(nearest.get("s1_cy", s1y))
                s2x = float(nearest.get("s2_cx", s2x))
                s2y = float(nearest.get("s2_cy", s2y))
            x1, y1 = compute_clamped_crop_coords(s1x, s1y, cw, ch, source_w, source_h, pan_norm=pan_norm)
            x2, y2 = compute_clamped_crop_coords(s2x, s2y, cw, ch, source_w, source_h, pan_norm=pan_norm)
            crop1 = f"crop={cw}:{ch}:{x1}:{y1}"
            crop2 = f"crop={cw}:{ch}:{x2}:{y2}"

        return (
            f"[{input_label}]split=2[p1][p2];"
            f"[p1]{crop1},scale={sw}:{sh}[{out1}];"
            f"[p2]{crop2},scale={sw}:{sh}[{out2}];"
            f"[{out1}][{out2}]{stack_filter}[v]"
        )

    # 4. FIT + BLURRED BACKDROP (respects zoom & pan on foreground)
    if layout == "fit_blur":
        fg_w = int(source_w / z)
        fg_h = int(source_h / z)
        fg_w = fg_w if fg_w % 2 == 0 else max(2, fg_w - 1)
        fg_h = fg_h if fg_h % 2 == 0 else max(2, fg_h - 1)
        fg_x, fg_y = compute_clamped_crop_coords(0.5, 0.5, fg_w, fg_h, source_w, source_h, pan_norm=pan_norm)
        fg_crop = f"crop={fg_w}:{fg_h}:{fg_x}:{fg_y}," if (z > 1.01 or abs(pan_norm) > 0.01) else ""

        return (
            f"[{input_label}]split=2[vbg][vfg];"
            f"[vbg]scale={target_w}:{target_h}:force_original_aspect_ratio=increase,"
            f"crop={target_w}:{target_h},gblur=sigma=28,eq=brightness=-0.08[bg];"
            f"[vfg]{fg_crop}scale={target_w}:{target_h}:force_original_aspect_ratio=decrease[fg];"
            f"[bg][fg]overlay=(W-w)/2:(H-h)/2[v]"
        )

    # 5. AUTO-TRACK SPEAKER (Default) — dynamic face tracking + zoom + pan
    cw, ch = _compute_aspect_crop_dims(source_w, source_h, target_ratio, z)
    max_x = max(0, source_w - cw)
    max_y = max(0, source_h - ch)
    kfs = (face_track_data or {}).get("keyframes") or []

    if is_video_render and len(kfs) >= 2:
        pts_x, pts_y = [], []
        for kf in kfs:
            t_val = float(kf.get("t", 0.0))
            fx, fy = compute_clamped_crop_coords(
                float(kf.get("cx", center_x)),
                float(kf.get("cy", center_y)),
                cw, ch, source_w, source_h, pan_norm=pan_norm
            )
            pts_x.append((t_val, fx))
            pts_y.append((t_val, fy))
        x_expr = compile_piecewise_linear_expr(pts_x, 0, max_x)
        y_expr = compile_piecewise_linear_expr(pts_y, 0, max_y)
        return f"[{input_label}]crop={cw}:{ch}:'{x_expr}':'{y_expr}',scale={target_w}:{target_h}[v]"

    fcx, fcy = center_x, center_y
    if kfs:
        nearest = min(kfs, key=lambda k: abs(float(k.get("t", 0.0)) - preview_rel_t))
        fcx = float(nearest.get("cx", fcx))
        fcy = float(nearest.get("cy", fcy))

    cx, cy = compute_clamped_crop_coords(fcx, fcy, cw, ch, source_w, source_h, pan_norm=pan_norm)
    return f"[{input_label}]crop={cw}:{ch}:{cx}:{cy},scale={target_w}:{target_h}[v]"
