import os
import time
import uuid
import shutil
import tempfile
import logging
import subprocess
from pathlib import Path
from typing import Dict, Any, Optional, List

from PIL import Image

from app.config import settings
from app.services.proxy import get_ffmpeg_cmd, probe_video
from app.services.reframer import (
    resolve_target_dimensions,
    build_multi_aspect_filter,
    canonicalize_framing_template,
    build_filter_complex_args,
)
from app.services.face_tracker import track_faces_in_clip
from app.services.filters import build_advanced_color_filter
from app.services.caption_renderer import render_caption_frame
from app.services.profanity_filter import mask_words_profanity

logger = logging.getLogger(__name__)


def render_exact_preview_frame(
    video_path: str,
    timestamp: float = 0.0,
    aspect_ratio: str = "9:16",
    custom_width: int = 1080,
    custom_height: int = 1920,
    frame_layout: str = "auto_track",
    webcam_corner: str = "top_left",
    webcam_split_ratio: float = 0.38,
    pip_border_color: str = "#F59E0B",
    zoom: float = 1.0,
    pan_x_offset: float = 0.0,
    pan_x: Optional[float] = None,
    color_grading: Optional[Dict[str, Any]] = None,
    burn_captions: bool = True,
    caption_options: Optional[Dict[str, Any]] = None,
    transcript_words: Optional[List[Dict[str, Any]]] = None,
    clip_start_time: float = 0.0,
    clip_duration: float = 0.0,
    clip_rel_ts_override: Optional[float] = None,
    clip_size: str = "1080p",
    export_resolution: Optional[str] = None,
    watermark_path: Optional[str] = None,
    watermark_corner: str = "top_right",
    watermark_opacity: float = 0.85,
    watermark_size: float = 0.15,
    project_id: Optional[int] = None,
) -> Dict[str, Any]:
    """
    Extract a single frame at `timestamp` using the exact FFmpeg reframing,
    face tracking, color grading, and ASS subtitle pipeline.
    Executes FFmpeg with cwd=work_dir and strictly relative ASCII paths inside filtergraph.txt.
    """
    t0 = time.time()
    ffmpeg_bin = get_ffmpeg_cmd()
    previews_dir = Path(settings.STORAGE_DIR) / "previews"
    previews_dir.mkdir(parents=True, exist_ok=True)

    run_id = uuid.uuid4().hex[:10]
    out_filename = f"preview_{run_id}.png"
    out_path = (previews_dir / out_filename).resolve()

    work_dir = Path(tempfile.mkdtemp(prefix="lost_heaven_prv_"))

    try:
        from app.services.renderer import scale_dimensions_for_clip_size, apply_watermark_to_filtergraph

        base_w, base_h = resolve_target_dimensions(aspect_ratio, custom_width, custom_height)
        target_w, target_h = scale_dimensions_for_clip_size(base_w, base_h, export_resolution or clip_size)

        src_w = 1920
        src_h = 1080
        try:
            meta = probe_video(video_path)
            src_w = meta.get("width") or 1920
            src_h = meta.get("height") or 1080
        except Exception as e:
            logger.warning(f"Could not probe video {video_path} for preview frame, assuming 1920x1080: {e}")

        layout = canonicalize_framing_template(frame_layout)

        # Compute seek timestamp & clip-relative subtitle timestamp unambiguously
        seek_ts = max(0.0, float(timestamp or 0.0))
        if clip_rel_ts_override is not None:
            clip_rel_ts = max(0.0, float(clip_rel_ts_override))
            if clip_start_time > 0 and seek_ts < clip_start_time:
                seek_ts = clip_start_time + clip_rel_ts
        elif clip_start_time > 0 and seek_ts >= clip_start_time - 0.05:
            clip_rel_ts = max(0.0, seek_ts - clip_start_time)
        else:
            clip_rel_ts = seek_ts
            if clip_start_time > 0 and seek_ts < clip_start_time:
                seek_ts = clip_start_time + seek_ts

        # Fast face tracking around timestamp for auto_track / split_podcast
        face_track_data = None
        if layout in ("auto_track", "split_podcast"):
            face_track_data = track_faces_in_clip(
                video_path=video_path,
                start_sec=max(0.0, seek_ts - 0.35),
                end_sec=seek_ts + 0.35,
                mode="dual" if layout == "split_podcast" else "single",
            )

        # 1. Multi-Aspect Framing Filter
        layout_vf = build_multi_aspect_filter(
            aspect_ratio=aspect_ratio,
            frame_layout=layout,
            source_w=src_w,
            source_h=src_h,
            custom_w=target_w,
            custom_h=target_h,
            webcam_corner=webcam_corner,
            webcam_split_ratio=webcam_split_ratio,
            pip_border_color=pip_border_color,
            zoom=zoom,
            pan_x_offset=pan_x_offset,
            pan_x=pan_x,
            face_track_data=face_track_data,
            is_video_render=False,
            preview_rel_t=0.35,
            target_size=(target_w, target_h),
        )

        # 2. Footage Color Grading & FX BEFORE captions
        filter_elements: List[str] = []
        cg = color_grading or {}
        color_f = build_advanced_color_filter(
            preset=cg.get("preset") or cg.get("color_preset") or "none",
            exposure=cg.get("exposure", 0.0),
            brightness=cg.get("brightness", 0.0),
            contrast=cg.get("contrast", 1.0),
            lift_mids=cg.get("lift_mids", 0.0),
            warmth=cg.get("warmth", 0.0),
            saturation=cg.get("saturation", 1.0),
            black_and_white=cg.get("black_and_white", 0.0),
            vintage=cg.get("vintage", 0.0),
            tint_color=cg.get("tint_color", "none"),
            tint_strength=cg.get("tint_strength", 0.0),
            glow=cg.get("glow", 0.0),
            smooth=cg.get("smooth", 0.0),
            sharpness=cg.get("sharpness", 0.0),
            blur=cg.get("blur", 0.0),
            film_grain=cg.get("film_grain", 0.0),
            vignette=cg.get("vignette", 0.0),
            bottom_fade=cg.get("bottom_fade", 0.0),
        )
        if color_f:
            filter_elements.append(color_f)

        post_chain = ",".join(filter_elements) if filter_elements else "null"
        fg_segments: List[str] = [layout_vf, f"[v]{post_chain}[v_graded]"]
        curr_v_lbl = "v_graded"

        input_args: List[str] = [
            "-ss", f"{seek_ts:.3f}",
            "-i", os.path.abspath(video_path),
        ]
        next_in_idx = 1
        next_in_idx, curr_v_lbl = apply_watermark_to_filtergraph(
            watermark_path=watermark_path,
            watermark_corner=watermark_corner,
            watermark_opacity=watermark_opacity,
            watermark_size=watermark_size,
            target_w=target_w,
            exec_dir=work_dir,
            input_args=input_args,
            next_in_idx=next_in_idx,
            curr_v_lbl=curr_v_lbl,
            fg_segments=fg_segments,
        )
        fg_segments.append(f"[{curr_v_lbl}]null[vout]")
        filter_complex = ";".join(fg_segments)

        fg_script = work_dir / "filtergraph.txt"
        fg_script.write_text(filter_complex, encoding="utf-8")

        cmd = [
            ffmpeg_bin,
            "-y",
            *input_args,
            *build_filter_complex_args(ffmpeg_bin, "filtergraph.txt", filter_complex),
            "-map", "[vout]",
            "-vframes", "1",
            "-q:v", "2",
            str(out_path),
        ]

        res = subprocess.run(cmd, capture_output=True, text=True, timeout=20, cwd=str(work_dir))
        if res.returncode != 0:
            logger.error(f"FFmpeg preview frame failed: {res.stderr}")
            raise RuntimeError(f"FFmpeg frame render failed: {res.stderr[-400:]}")

        # 3. Pixel-identical Pillow transparent RGBA Caption & Hook Banner overlay
        co = caption_options or {}
        render_overlay = bool(
            burn_captions
            or co.get("retention_bar_enabled")
            or co.get("outro_cta_enabled")
        )
        if render_overlay:
            how_arrives = co.get("how_phrase_arrives", co.get("line_mode", "All at once"))
            is_word_by_word = (
                bool(co.get("word_by_word"))
                if co.get("word_by_word") is not None
                else str(how_arrives).lower().replace("-", " ") == "word by word"
            )
            preview_words = transcript_words or []
            if co.get("censor_profanity"):
                preview_words = mask_words_profanity(preview_words)
            with Image.open(out_path) as base_img:
                base_rgba = base_img.convert("RGBA")
                cap_rgba = render_caption_frame(
                    t=clip_rel_ts,
                    width=base_rgba.width,
                    height=base_rgba.height,
                    words=preview_words,
                    burn_captions=burn_captions,
                    style_preset=co.get("style_preset", co.get("caption_style", "Karaoke pop")),
                    font_family=co.get("font_family", co.get("caption_font", "Montserrat")),
                    font_size_pct=co.get("font_size_pct", co.get("font_size_percent", 100)),
                    bold=co.get("bold", True),
                    italic=co.get("italic", False),
                    uppercase=co.get("uppercase", co.get("all_caps", True)),
                    alignment=co.get("alignment", co.get("text_align", "Centre")),
                    position_grid=co.get("position_grid", "bottom_center"),
                    pos_x=co.get("pos_x", co.get("position_x", 50.0)),
                    pos_y=co.get("pos_y", co.get("position_y", 74.0)),
                    base_color=co.get("base_color", "#FFFFFF"),
                    highlight_color=co.get("highlight_color", co.get("active_color", "#C6F32F")),
                    outline_color=co.get("outline_color", "#000000"),
                    highlight_shape=co.get("highlight_shape", "colour_only"),
                    hold_back_opacity=co.get("hold_back_opacity", co.get("hold_back_alpha", 1.0)),
                    outline=co.get("outline", co.get("outline_width", 4.0)),
                    shadow=co.get("shadow", co.get("shadow_depth", 3.0)),
                    glow=co.get("glow_intensity", co.get("glow", 0.0)),
                    letter_spacing=co.get("letter_spacing", 0.0),
                    word_by_word=is_word_by_word,
                    auto_emoji=co.get("auto_emoji", co.get("emoji_enabled", False)),
                    highlight_power_words=bool(co.get("highlight_power_words", False)),
                    hook_banner_enabled=co.get("hook_banner_enabled", co.get("show_hook_banner", False)),
                    hook_headline=co.get("hook_headline", ""),
                    retention_bar_enabled=bool(co.get("retention_bar_enabled", False)),
                    retention_bar_color=co.get("retention_bar_color", "#C6F32F"),
                    outro_cta_enabled=bool(co.get("outro_cta_enabled", False)),
                    outro_cta_text=co.get("outro_cta_text", "Subscribe for More"),
                    clip_start_time=clip_start_time,
                    clip_duration=clip_duration,
                    project_id=project_id,
                    timing_offset=co.get("timing_offset", 0.0),
                    clean_filler_words=co.get("clean_filler_words", False),
                )
                composited = Image.alpha_composite(base_rgba, cap_rgba).convert("RGB")
                composited.save(out_path, format="PNG")

        latency_ms = int((time.time() - t0) * 1000)
        return {
            "frame_url": f"/media/previews/{out_filename}",
            "render_time_ms": latency_ms,
            "timestamp": timestamp,
        }
    finally:
        shutil.rmtree(work_dir, ignore_errors=True)
