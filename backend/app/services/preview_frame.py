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
from app.services.face_tracker import track_faces_in_clip, detect_faces_offline
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
    keep_segments: Optional[List[List[float]]] = None,
    merge_crossfade_sec: float = 0.03,
    backdrop_mode: str = "none",
    backdrop_blur_intensity: float = 18.0,
    backdrop_color: str = "#18181B",
    privacy_blur_boxes: Optional[List[Dict[str, Any]]] = None,
    beat_sync_enabled: bool = False,
    spectrum_enabled: bool = False,
    spectrum_position: str = "bottom",
    spectrum_color: str = "#C6F32F",
    chapters_enabled: bool = False,
    chapters: Optional[List[Dict[str, Any]]] = None,
    handle_watermark_enabled: bool = False,
    handle_text: str = "",
    handle_platform_icon: str = "none",
    handle_corner: str = "bottom_right",
    handle_opacity: float = 0.65,
    handle_display_mode: str = "always",
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

        # Multi-segment timeline remap (blade cuts & merged clips)
        preview_words = list(transcript_words or [])
        preview_cap_start = clip_start_time
        preview_cap_dur = clip_duration
        if keep_segments:
            from app.services.timeline_editor import (
                compute_kept_segments_and_remapped_words,
                map_output_time_to_source_time,
            )
            abs_segs = [
                (max(0.0, float(seg[0])), float(seg[1]))
                for seg in keep_segments
                if isinstance(seg, (list, tuple)) and len(seg) >= 2 and float(seg[1]) > float(seg[0]) + 0.05
            ]
            if abs_segs:
                min_s = min(s for s, _ in abs_segs)
                max_e = max(e for _, e in abs_segs)
                tl = compute_kept_segments_and_remapped_words(
                    words=preview_words,
                    clip_start_time=min_s,
                    clip_duration=max(clip_duration, max_e - min_s),
                    explicit_keep_segments=abs_segs,
                    crossfade_sec=float(merge_crossfade_sec or 0.03),
                )
                eff_segs = tl.get("kept_segments_abs") or abs_segs
                if len(eff_segs) > 1 or clip_rel_ts_override is not None:
                    seek_ts = map_output_time_to_source_time(
                        clip_rel_ts,
                        eff_segs,
                        crossfade_sec=float(tl.get("crossfade_sec", merge_crossfade_sec)),
                    )
                preview_words = tl.get("remapped_words") or preview_words
                preview_cap_start = 0.0
                preview_cap_dur = float(tl.get("effective_duration") or clip_duration)

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

        from app.services.bg_segmentation import (
            append_backdrop_filter_stage,
            canonicalize_backdrop_mode,
            composite_foreground_over_backdrop,
            build_backdrop_image,
            segment_foreground_mask,
        )
        from app.services.privacy_blur import build_privacy_blur_filtergraph, normalize_blur_boxes

        eff_backdrop = canonicalize_backdrop_mode(backdrop_mode)
        clean_blur_boxes = normalize_blur_boxes(privacy_blur_boxes)

        post_chain = ",".join(filter_elements) if filter_elements else "null"
        fg_segments: List[str] = [layout_vf, f"[v]{post_chain}[v_graded]"]
        curr_v_lbl = "v_graded"

        input_args: List[str] = [
            "-ss", f"{seek_ts:.3f}",
            "-i", os.path.abspath(video_path),
        ]
        next_in_idx = 1

        # If virtual backdrop active, extract base graded frame first so alphamerge + overlay uses true alpha matte
        if eff_backdrop != "none":
            pre_bg_name = "pre_bg.png"
            pre_fg = f"{layout_vf};[v]{post_chain}[vout]"
            (work_dir / "pre_bg_fg.txt").write_text(pre_fg, encoding="utf-8")
            p1 = subprocess.run(
                [
                    ffmpeg_bin, "-y", *input_args,
                    *build_filter_complex_args(ffmpeg_bin, "pre_bg_fg.txt", pre_fg),
                    "-map", "[vout]", "-vframes", "1", pre_bg_name,
                ],
                capture_output=True, text=True, timeout=20, cwd=str(work_dir),
            )
            if p1.returncode == 0 and (work_dir / pre_bg_name).exists():
                import cv2
                raw_bgr = cv2.imread(str(work_dir / pre_bg_name), cv2.IMREAD_COLOR)
                if raw_bgr is not None:
                    matte_u8 = segment_foreground_mask(raw_bgr)
                    cv2.imwrite(str(work_dir / "fg_matte.png"), matte_u8)
                    input_args = ["-i", pre_bg_name, "-i", "fg_matte.png"]
                    matte_in_idx = 1
                    next_in_idx = 2
                    bg_in_idx: Optional[int] = None
                    if eff_backdrop in ("solid_charcoal", "accent_gradient"):
                        bg_img = build_backdrop_image(
                            raw_bgr,
                            backdrop_mode=eff_backdrop,
                            blur_intensity=backdrop_blur_intensity,
                            backdrop_color=backdrop_color,
                        )
                        cv2.imwrite(str(work_dir / "bg_static.png"), bg_img)
                        input_args.extend(["-i", "bg_static.png"])
                        bg_in_idx = next_in_idx
                        next_in_idx += 1
                    fg_segments = ["[0:v]null[v_graded]"]
                    curr_v_lbl = append_backdrop_filter_stage(
                        fg_segments,
                        curr_v_lbl="v_graded",
                        matte_input_idx=matte_in_idx,
                        backdrop_mode=eff_backdrop,
                        target_w=target_w,
                        target_h=target_h,
                        blur_intensity=backdrop_blur_intensity,
                        static_bg_input_idx=bg_in_idx,
                        out_label="v_backdrop",
                    )

        if clean_blur_boxes:
            blur_frags, curr_v_lbl = build_privacy_blur_filtergraph(
                curr_v_lbl,
                clean_blur_boxes,
                trajectories=None,
                target_w=target_w,
                target_h=target_h,
                is_video_render=False,
                out_label="v_pblur",
            )
            fg_segments.extend(blur_frags)

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
        eff_spectrum_enabled = bool(spectrum_enabled or co.get("spectrum_enabled", False))
        eff_spectrum_pos = str(co.get("spectrum_position", spectrum_position) or spectrum_position or "bottom")
        eff_spectrum_color = str(co.get("spectrum_color", spectrum_color) or spectrum_color or "#C6F32F")
        eff_chapters_enabled = bool(chapters_enabled or co.get("chapters_enabled", False))
        eff_chapters = chapters if chapters is not None else co.get("chapters")
        eff_handle_on = bool(handle_watermark_enabled or co.get("handle_watermark_enabled", False))
        eff_handle_text = str(co.get("handle_text", handle_text) or handle_text or "").strip()
        eff_handle_icon = str(co.get("handle_platform_icon", handle_platform_icon) or handle_platform_icon or "none")
        eff_handle_corner = str(co.get("handle_corner", handle_corner) or handle_corner or "bottom_right")
        eff_handle_op = float(co.get("handle_opacity", handle_opacity) if co.get("handle_opacity") is not None else handle_opacity)
        eff_handle_mode = str(co.get("handle_display_mode", handle_display_mode) or handle_display_mode or "always")
        render_overlay = bool(
            burn_captions
            or co.get("retention_bar_enabled")
            or co.get("outro_cta_enabled")
            or co.get("speaker_lower_thirds")
            or (co.get("auto_callouts") and co.get("callout_cards"))
            or eff_spectrum_enabled
            or (eff_chapters_enabled and eff_chapters)
            or (eff_handle_on and eff_handle_text)
        )
        if render_overlay:
            spec_data = None
            if eff_spectrum_enabled:
                try:
                    from app.services.beat_sync import detect_audio_beats
                    win_s = max(0.0, float(timestamp) - 1.0)
                    raw_spec = detect_audio_beats(
                        video_path,
                        start_sec=win_s,
                        duration_sec=2.0,
                        spectrum_fps=10,
                    )
                    # Re-index local 1.0s position to clip_rel_ts cleanly via offset wrapper
                    bars_at_ts = raw_spec.get("spectrum_frames", [])
                    mid_bars = bars_at_ts[min(len(bars_at_ts) - 1, 10)] if bars_at_ts else []
                    spec_data = {
                        "spectrum_fps": 1,
                        "spectrum_frames": [mid_bars] * max(1, int(round(max(1.0, clip_rel_ts + 2.0)))),
                    }
                except Exception:
                    spec_data = None
            how_arrives = co.get("how_phrase_arrives", co.get("line_mode", "All at once"))
            is_word_by_word = (
                bool(co.get("word_by_word"))
                if co.get("word_by_word") is not None
                else str(how_arrives).lower().replace("-", " ") == "word by word"
            )
            active_cap_words = preview_words
            if co.get("censor_profanity"):
                active_cap_words = mask_words_profanity(active_cap_words)
            with Image.open(out_path) as base_img:
                base_rgba = base_img.convert("RGBA")
                preview_face_kfs: Optional[List[Dict[str, Any]]] = None
                if bool(co.get("face_safe_captions", True)):
                    try:
                        import numpy as np
                        import cv2
                        rgb_np = np.array(base_rgba.convert("RGB"))
                        bgr_np = rgb_np[:, :, ::-1]
                        if bgr_np.shape[1] > 480:
                            sc = 480.0 / bgr_np.shape[1]
                            small_bgr = cv2.resize(bgr_np, (480, max(1, int(bgr_np.shape[0] * sc))))
                        else:
                            small_bgr = bgr_np
                        faces_now = detect_faces_offline(small_bgr)
                        if faces_now:
                            top_f = max(faces_now, key=lambda f: f.get("area", 0.0))
                            preview_face_kfs = [{
                                "t": clip_rel_ts,
                                "cx": float(top_f.get("cx", 0.5)),
                                "cy": float(top_f.get("cy", 0.4)),
                                "face_h": float(top_f.get("h", 0.26)),
                            }]
                    except Exception:
                        preview_face_kfs = None
                cap_rgba = render_caption_frame(
                    t=clip_rel_ts,
                    width=base_rgba.width,
                    height=base_rgba.height,
                    words=active_cap_words,
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
                    clip_start_time=preview_cap_start,
                    clip_duration=preview_cap_dur,
                    project_id=project_id,
                    timing_offset=co.get("timing_offset", 0.0),
                    clean_filler_words=co.get("clean_filler_words", False),
                    speakers=co.get("speakers"),
                    speaker_lower_thirds=bool(co.get("speaker_lower_thirds", False)),
                    face_safe_captions=bool(co.get("face_safe_captions", True)),
                    face_keyframes=preview_face_kfs,
                    callout_cards=co.get("callout_cards"),
                    auto_callouts=bool(co.get("auto_callouts", False)),
                    bilingual_enabled=bool(co.get("bilingual_enabled", False)),
                    secondary_language=str(co.get("secondary_language", "") or ""),
                    spectrum_enabled=eff_spectrum_enabled,
                    spectrum_position=eff_spectrum_pos,
                    spectrum_color=eff_spectrum_color,
                    spectrum_data=spec_data,
                    chapters_enabled=eff_chapters_enabled,
                    chapters=eff_chapters,
                    handle_watermark_enabled=eff_handle_on,
                    handle_text=eff_handle_text,
                    handle_platform_icon=eff_handle_icon,
                    handle_corner=eff_handle_corner,
                    handle_opacity=eff_handle_op,
                    handle_display_mode=eff_handle_mode,
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
