import logging
import shutil
import subprocess
import tempfile
import time
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import cv2
import numpy as np
from PIL import Image, ImageDraw, ImageFilter

from app.config import settings
from app.services.bg_segmentation import segment_foreground_mask
from app.services.caption_renderer import resolve_pillow_font
from app.services.face_tracker import track_faces_in_clip
from app.services.proxy import get_ffmpeg_cmd, probe_video
from app.services.reframer import (
    build_filter_complex_args,
    build_multi_aspect_filter,
    canonicalize_framing_template,
    resolve_target_dimensions,
)

logger = logging.getLogger("lost_heaven.thumbnail_studio")


def _hex_to_rgba(hex_str: str, alpha: int = 255) -> Tuple[int, int, int, int]:
    clean = str(hex_str or "#C6F32F").strip().lstrip("#")
    if len(clean) == 3:
        clean = "".join(c * 2 for c in clean)
    if len(clean) != 6:
        return (198, 243, 47, alpha)
    try:
        return (int(clean[0:2], 16), int(clean[2:4], 16), int(clean[4:6], 16), alpha)
    except Exception:
        return (198, 243, 47, alpha)


def _wrap_headline_lines(
    draw: ImageDraw.ImageDraw,
    headline: str,
    font: Any,
    max_w: int,
) -> List[str]:
    words = [w.strip() for w in (headline or "WATCH THIS").upper().split() if w.strip()]
    if not words:
        words = ["VIRAL", "CLIP"]
    lines: List[str] = []
    cur: List[str] = []
    for w in words:
        cand = " ".join(cur + [w])
        bb = draw.textbbox((0, 0), cand, font=font)
        if cur and (bb[2] - bb[0]) > max_w:
            lines.append(" ".join(cur))
            cur = [w]
        else:
            cur.append(w)
    if cur:
        lines.append(" ".join(cur))
    return lines[:3]


def render_depth_thumbnail_image(
    frame_bgr: np.ndarray,
    headline: str,
    target_w: int = 1080,
    target_h: int = 1920,
    *,
    depth_layer: bool = True,
    accent_color: str = "#C6F32F",
    rim_glow: bool = True,
    badge_text: str = "",
) -> Image.Image:
    """
    Compose a layered cover thumbnail:
      - Contrast-graded framed background
      - Ultra-bold multi-line headline text layer
      - Optional edge rim glow around subject silhouette
      - Foreground segmented person composited ON TOP of headline (`depth_layer=True`)
      - Optional crisp corner badge sticker
    """
    if frame_bgr.shape[1] != target_w or frame_bgr.shape[0] != target_h:
        frame_bgr = cv2.resize(frame_bgr, (target_w, target_h), interpolation=cv2.INTER_LANCZOS4)

    rgb = cv2.cvtColor(frame_bgr, cv2.COLOR_BGR2RGB)
    base_rgba = Image.fromarray(rgb, mode="RGB").convert("RGBA")
    matte_u8 = segment_foreground_mask(frame_bgr)

    # Subtle background cinematic darkening wash so big cover text pops
    wash = Image.new("RGBA", (target_w, target_h), (0, 0, 0, 0))
    w_draw = ImageDraw.Draw(wash)
    top_band = int(target_h * 0.42)
    for y in range(top_band):
        alpha = int(140 * (1.0 - (y / max(1, top_band)) ** 1.25))
        w_draw.line([(0, y), (target_w, y)], fill=(8, 10, 14, alpha))
    bg_comp = Image.alpha_composite(base_rgba, wash)

    # Headline Typography Layer
    text_layer = Image.new("RGBA", (target_w, target_h), (0, 0, 0, 0))
    t_draw = ImageDraw.Draw(text_layer)

    is_landscape = target_w > target_h
    font_px = max(56, int(round(target_h * (0.14 if is_landscape else 0.088))))
    font = resolve_pillow_font("Impact", font_px, bold=True)
    lines = _wrap_headline_lines(t_draw, headline, font, max_w=int(target_w * 0.88))

    accent_rgba = _hex_to_rgba(accent_color, 255)
    line_heights: List[int] = []
    line_widths: List[int] = []
    for ln in lines:
        bb = t_draw.textbbox((0, 0), ln, font=font)
        line_widths.append(bb[2] - bb[0])
        line_heights.append(bb[3] - bb[1])

    gap = max(10, int(font_px * 0.16))
    y_cursor = int(target_h * (0.09 if is_landscape else 0.085))
    stroke_px = max(3, int(round(font_px * 0.06)))

    for idx, ln in enumerate(lines):
        lw = line_widths[idx]
        lh = line_heights[idx]
        x0 = max(24, (target_w - lw) // 2)
        fill_col = accent_rgba if (len(lines) > 1 and idx == len(lines) - 1) else (255, 255, 255, 255)
        # Drop shadow + crisp stroke
        t_draw.text((x0 + stroke_px * 2, y_cursor + stroke_px * 2), ln, font=font, fill=(0, 0, 0, 210))
        t_draw.text(
            (x0, y_cursor),
            ln,
            font=font,
            fill=fill_col,
            stroke_width=stroke_px,
            stroke_fill=(10, 10, 14, 245),
        )
        y_cursor += lh + gap

    if depth_layer:
        out_canvas = Image.alpha_composite(bg_comp, text_layer)
        # Optional colored rim halo from dilated minus eroded alpha matte
        if rim_glow:
            k = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (17, 17))
            dil = cv2.dilate(matte_u8, k)
            ero = cv2.erode(matte_u8, cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (7, 7)))
            edge = np.clip(dil.astype(np.int16) - ero.astype(np.int16), 0, 255).astype(np.uint8)
            glow_img = Image.new("RGBA", (target_w, target_h), (accent_rgba[0], accent_rgba[1], accent_rgba[2], 0))
            glow_arr = np.array(glow_img)
            glow_arr[:, :, 3] = np.clip(edge.astype(np.float32) * 0.78, 0, 220).astype(np.uint8)
            glow_rgba = Image.fromarray(glow_arr, mode="RGBA").filter(ImageFilter.GaussianBlur(radius=6.0))
            out_canvas = Image.alpha_composite(out_canvas, glow_rgba)

        fg_cutout = base_rgba.copy()
        fg_cutout.putalpha(Image.fromarray(matte_u8, mode="L"))
        out_canvas = Image.alpha_composite(out_canvas, fg_cutout)
    else:
        # Flat mode: subject first, text on top
        out_canvas = Image.alpha_composite(bg_comp, text_layer)

    # Optional crisp corner badge sticker
    clean_badge = str(badge_text or "").strip().upper()
    if clean_badge:
        b_px = max(24, int(round(target_h * (0.042 if is_landscape else 0.024))))
        b_font = resolve_pillow_font("Montserrat", b_px, bold=True)
        b_draw = ImageDraw.Draw(out_canvas, "RGBA")
        bb = b_draw.textbbox((0, 0), clean_badge, font=b_font)
        bw, bh = bb[2] - bb[0], bb[3] - bb[1]
        pad_x, pad_y = max(16, int(target_w * 0.02)), max(10, int(target_h * 0.01))
        bx0 = int(target_w * 0.05)
        by0 = int(target_h * 0.035)
        r, g, b, _ = accent_rgba
        lum = 0.299 * r + 0.587 * g + 0.114 * b
        ink = (12, 12, 16, 255) if lum > 145 else (255, 255, 255, 255)
        b_draw.rounded_rectangle(
            [bx0, by0, bx0 + bw + pad_x * 2, by0 + bh + pad_y * 2],
            radius=12,
            fill=accent_rgba,
        )
        b_draw.text((bx0 + pad_x - bb[0], by0 + pad_y - bb[1]), clean_badge, font=b_font, fill=ink)

    return out_canvas


def generate_depth_thumbnail(
    video_path: str,
    clip_id: int,
    project_id: int,
    timestamp: Optional[float] = None,
    clip_start_time: float = 0.0,
    clip_duration: float = 5.0,
    headline: str = "",
    aspect_ratio: str = "9:16",
    frame_layout: str = "auto_track",
    zoom: float = 1.0,
    pan_x_offset: float = 0.0,
    depth_layer: bool = True,
    accent_color: str = "#C6F32F",
    rim_glow: bool = True,
    badge_text: str = "",
) -> Dict[str, Any]:
    """
    Extract sharpest frame (or specified `timestamp`), apply framing crop via FFmpeg inside ASCII temp dir,
    render depth-layered `headline` behind segmented subject, and persist PNG + JPG in `storage/thumbnails/`.
    """
    target_w, target_h = resolve_target_dimensions("16:9" if aspect_ratio == "16:9" else "9:16")
    if aspect_ratio == "16:9":
        target_w, target_h = 1280, 720

    meta = probe_video(video_path)
    src_w = int(meta.get("width") or 1920)
    src_h = int(meta.get("height") or 1080)
    src_dur = float(meta.get("duration") or 10.0)

    if timestamp is not None:
        seek_ts = max(0.0, min(max(0.0, src_dur - 0.1), float(timestamp)))
    else:
        # Auto-pick sharpest frame inside opening third of clip
        best_ts = clip_start_time + min(1.5, max(0.2, clip_duration * 0.25))
        best_var = -1.0
        cap = cv2.VideoCapture(video_path)
        if cap.isOpened():
            try:
                for step in range(5):
                    cand = clip_start_time + step * (max(0.5, clip_duration) / 5.0)
                    cap.set(cv2.CAP_PROP_POS_MSEC, cand * 1000.0)
                    ok, fr = cap.read()
                    if not ok or fr is None:
                        continue
                    gray = cv2.cvtColor(fr, cv2.COLOR_BGR2GRAY)
                    sharp = float(cv2.Laplacian(gray, cv2.CV_64F).var())
                    if sharp > best_var:
                        best_var = sharp
                        best_ts = cand
            finally:
                cap.release()
        seek_ts = max(0.0, min(max(0.0, src_dur - 0.1), best_ts))

    layout = canonicalize_framing_template(frame_layout)
    ft_data = (
        track_faces_in_clip(video_path, start_sec=max(0.0, seek_ts - 0.25), end_sec=seek_ts + 0.25)
        if layout in ("auto_track", "split_podcast")
        else None
    )

    work_dir = Path(tempfile.mkdtemp(prefix="lost_heaven_thumb_"))
    try:
        ffmpeg_bin = get_ffmpeg_cmd()
        raw_png = work_dir / "framed.png"
        layout_vf = build_multi_aspect_filter(
            aspect_ratio="16:9" if target_w > target_h else "9:16",
            frame_layout=layout,
            source_w=src_w,
            source_h=src_h,
            zoom=zoom,
            pan_x_offset=pan_x_offset,
            face_track_data=ft_data,
            is_video_render=False,
            preview_rel_t=0.25,
            target_size=(target_w, target_h),
        )
        fg = f"{layout_vf};[v]null[vout]"
        cmd = [
            ffmpeg_bin, "-y",
            "-ss", f"{seek_ts:.3f}",
            "-i", str(Path(video_path).resolve()),
            *build_filter_complex_args(ffmpeg_bin, "thumb_fg.txt", fg),
            "-map", "[vout]",
            "-vframes", "1",
            raw_png.name,
        ]
        (work_dir / "thumb_fg.txt").write_text(fg, encoding="utf-8")
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=25, cwd=str(work_dir))
        if proc.returncode != 0 or not raw_png.exists():
            raise RuntimeError(f"Thumbnail frame extraction failed: {proc.stderr[-300:]}")

        frame_bgr = cv2.imread(str(raw_png), cv2.IMREAD_COLOR)
        if frame_bgr is None:
            raise RuntimeError("Could not read extracted thumbnail frame")

        out_img = render_depth_thumbnail_image(
            frame_bgr=frame_bgr,
            headline=headline or "WATCH THIS",
            target_w=target_w,
            target_h=target_h,
            depth_layer=depth_layer,
            accent_color=accent_color,
            rim_glow=rim_glow,
            badge_text=badge_text,
        )

        thumbs_dir = Path(settings.STORAGE_DIR) / "thumbnails"
        thumbs_dir.mkdir(parents=True, exist_ok=True)
        stamp = int(time.time() * 1000)
        ar_tag = f"{target_w}x{target_h}"
        png_name = f"thumb_{project_id}_{clip_id}_{ar_tag}_{stamp}.png"
        jpg_name = f"thumb_{project_id}_{clip_id}_{ar_tag}_{stamp}.jpg"
        png_path = thumbs_dir / png_name
        jpg_path = thumbs_dir / jpg_name

        out_img.save(png_path, format="PNG")
        out_img.convert("RGB").save(jpg_path, format="JPEG", quality=93)

        return {
            "thumbnail_url": f"/media/thumbnails/{png_name}",
            "jpg_url": f"/media/thumbnails/{jpg_name}",
            "png_path": str(png_path.resolve()),
            "jpg_path": str(jpg_path.resolve()),
            "width": target_w,
            "height": target_h,
            "timestamp": round(seek_ts, 2),
            "depth_layer": bool(depth_layer),
        }
    finally:
        shutil.rmtree(work_dir, ignore_errors=True)
