import json
import os
import re
import shutil
import tempfile
import uuid
import zipfile
from typing import Any, Dict, List, Optional, Tuple

import cv2
from PIL import Image

from app.models import Project, ViralClip
from app.services.nle_exporter import build_srt_content
from app.services.preview_frame import render_exact_preview_frame
from app.services.profanity_filter import mask_words_profanity
from app.services.renderer import render_clip_ultra
from app.services.social_copywriter import generate_multi_platform_copy
from app.services.timeline_editor import compute_kept_segments_and_remapped_words, extract_waveform_bins


def _safe_filename_slug(text: str, fallback: str = "clip") -> str:
    slug = re.sub(r"[^A-Za-z0-9_-]+", "_", (text or "").strip()).strip("_")
    return slug[:42] or fallback


def _find_best_thumbnail_rel_ts(
    video_path: str,
    clip_start: float,
    clip_end: float,
    words: List[Dict[str, Any]],
) -> float:
    """Select the sharpest, highest-vocal-energy clip-relative timestamp for thumbnail cover."""
    duration = max(0.5, float(clip_end) - float(clip_start))
    default_rel_ts = min(duration * 0.35, max(0.25, duration * 0.2))

    candidate_rel_times: List[float] = []
    try:
        wf = extract_waveform_bins(
            video_or_wav_path=video_path,
            clip_start=clip_start,
            clip_end=clip_end,
            num_bins=24,
            transcript_words=words,
        )
        rms_vals = wf.get("rms") or wf.get("bins") or []
        is_silence = wf.get("is_silence") or []
        if rms_vals:
            scored = []
            for idx, val in enumerate(rms_vals):
                sil_penalty = 0.25 if (idx < len(is_silence) and is_silence[idx]) else 1.0
                rel_t = ((idx + 0.5) / len(rms_vals)) * duration
                scored.append((float(val) * sil_penalty, rel_t))
            scored.sort(key=lambda x: x[0], reverse=True)
            candidate_rel_times = [t for _, t in scored[:5]]
    except Exception:
        pass

    if not candidate_rel_times:
        candidate_rel_times = [
            max(0.2, duration * 0.15),
            max(0.2, duration * 0.35),
            max(0.2, duration * 0.55),
            max(0.2, duration * 0.75),
        ]

    best_rel_ts = candidate_rel_times[0] if candidate_rel_times else default_rel_ts

    if video_path and os.path.exists(video_path):
        cap = cv2.VideoCapture(video_path)
        if cap.isOpened():
            best_score = -1.0
            try:
                for rel_t in candidate_rel_times:
                    abs_ms = max(0.0, (float(clip_start) + rel_t) * 1000.0)
                    cap.set(cv2.CAP_PROP_POS_MSEC, abs_ms)
                    ok, frame = cap.read()
                    if not ok or frame is None:
                        continue
                    gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
                    sharpness = float(cv2.Laplacian(gray, cv2.CV_64F).var())
                    luma = float(gray.mean())
                    exposure_factor = 1.0 if 28.0 <= luma <= 235.0 else 0.4
                    score = sharpness * exposure_factor
                    if score > best_score:
                        best_score = score
                        best_rel_ts = rel_t
            finally:
                cap.release()

    return round(max(0.1, min(duration - 0.1, best_rel_ts)), 2)


def _format_social_copy_text(clip: ViralClip, copy_map: Dict[str, Dict[str, Any]]) -> str:
    """Format multi-platform social copy into a structured creator text sheet."""
    title = clip.selected_title or clip.hook_headline or f"Clip #{clip.id}"
    lines = [
        "LOST HEAVEN STUDIO - MULTI-PLATFORM PUBLISH COPY",
        "=" * 54,
        f"Clip Title: {title}",
        f"Hook Headline: {clip.hook_headline or title}",
        f"Viral Score: {getattr(clip, 'virality_score', 0) or 0}/100",
        "",
    ]
    labels = [
        ("youtube_shorts", "YOUTUBE SHORTS"),
        ("tiktok", "TIKTOK"),
        ("instagram_reels", "INSTAGRAM REELS"),
        ("x_twitter", "X (TWITTER)"),
    ]
    for key, heading in labels:
        item = copy_map.get(key) or {}
        tags = " ".join(str(t) for t in (item.get("hashtags") or []))
        lines.extend([
            f"[{heading}]",
            f"Title/Hook: {item.get('title') or title}",
            f"Caption: {item.get('description') or item.get('caption') or ''}",
            f"Pinned Comment: {item.get('pinned_comment') or ''}",
            f"Hashtags: {tags}",
            "-" * 42,
            "",
        ])
    return "\n".join(lines).strip() + "\n"


def build_publish_pack_zip(
    clip: ViralClip,
    project: Project,
    api_key: Optional[str] = None,
    gemini_model: Optional[str] = None,
    quality_mode: str = "fast_preview",
) -> Tuple[str, str]:
    """
    Build a multi-platform Publish Pack .zip containing:
      - 01_Shorts_TikTok_9x16.mp4 (1080x1920)
      - 02_Instagram_Feed_4x5.mp4 (1080x1350)
      - 03_YouTube_X_16x9.mp4 (1920x1080)
      - 04_Thumbnail_Cover.jpg (AI-selected vocal peak + sharpest frame + Hook Banner)
      - 05_Captions.srt (profanity-aware remapped SubRip captions)
      - 06_Social_Copy.txt (YouTube Shorts, TikTok, Instagram Reels, X copy)
    Returns (zip_file_path, download_filename).
    """
    master_video = getattr(project, "source_path", "") or ""
    if not master_video or not os.path.exists(master_video):
        raise FileNotFoundError(f"Master video missing for project {project.id}")

    raw_words = clip.get_transcript() or []
    clip_dur = max(0.5, float(clip.end_time) - float(clip.start_time))
    tl_plan = compute_kept_segments_and_remapped_words(
        words=raw_words,
        clip_start_time=clip.start_time,
        clip_duration=clip_dur,
        tighten_pacing=bool(getattr(clip, "tighten_pacing", False)),
        silence_threshold=float(getattr(clip, "silence_threshold", 0.50)),
        clean_filler_words=bool(clip.clean_filler_words),
    )
    remapped_words = tl_plan.get("remapped_words") or raw_words
    effective_duration = float(tl_plan.get("effective_duration") or clip_dur)
    is_censored = bool(getattr(clip, "censor_profanity", False))
    export_words = mask_words_profanity(remapped_words) if is_censored else remapped_words

    duration = max(0.5, effective_duration)
    slug = _safe_filename_slug(clip.selected_title or clip.hook_headline or f"clip_{clip.id}")
    download_filename = f"LostHeaven_PublishPack_Clip{clip.id}_{slug}.zip"

    packs_root = os.path.join(tempfile.gettempdir(), "lost_heaven_packs")
    os.makedirs(packs_root, exist_ok=True)
    zip_path = os.path.join(packs_root, f"pack_{project.id}_{clip.id}_{uuid.uuid4().hex[:8]}.zip")

    work_dir = tempfile.mkdtemp(prefix="lost_heaven_pack_")
    try:
        aspect_targets = [
            ("9:16", "01_Shorts_TikTok_9x16.mp4", 1080, 1920),
            ("4:5", "02_Instagram_Feed_4x5.mp4", 1080, 1350),
            ("16:9", "03_YouTube_X_16x9.mp4", 1920, 1080),
        ]
        rendered_files: List[Tuple[str, str]] = []
        render_duration = max(0.5, float(clip.end_time) - float(clip.start_time))

        for ratio, arcname, cw, ch in aspect_targets:
            out_mp4 = os.path.join(work_dir, arcname)
            rendered_out = render_clip_ultra(
                master_video_path=master_video,
                clip_id=clip.id,
                start_time=clip.start_time,
                duration=render_duration,
                words=raw_words,
                project_id=project.id,
                aspect_ratio=ratio,
                custom_width=cw,
                custom_height=ch,
                frame_layout=clip.frame_layout or "auto_track",
                webcam_corner=clip.webcam_corner or "auto",
                webcam_split_ratio=float(clip.webcam_split_ratio or 0.38),
                pip_border_color=clip.pip_border_color or "#F59E0B",
                zoom=float(clip.zoom or 1.0),
                pan_x_offset=float(clip.pan_x_offset or 0.0),
                burn_captions=bool(clip.burn_captions),
                timing_offset=float(clip.timing_offset or 0.0),
                caption_style=clip.caption_style or "Karaoke pop",
                caption_font=clip.caption_font or "Montserrat",
                caption_animation=clip.caption_animation or "Karaoke",
                font_size_percent=int(clip.font_size_percent or 100),
                base_color=clip.base_color or "#FFFFFF",
                active_color=clip.active_color or "#C6F32F",
                outline_color=clip.outline_color or "#000000",
                font_size=int(clip.font_size or 64),
                bold=bool(clip.bold),
                italic=bool(clip.italic),
                all_caps=bool(clip.all_caps),
                text_align=clip.text_align or "center",
                position_grid=clip.position_grid or "bottom_center",
                position_x=float(clip.position_x or 50.0),
                position_y=float(clip.position_y or 78.0),
                hold_back_alpha=float(clip.hold_back_alpha if clip.hold_back_alpha is not None else 1.0),
                highlight_shape=clip.highlight_shape or "Colour only",
                emoji_enabled=bool(clip.emoji_enabled),
                highlight_power_words=bool(getattr(clip, "highlight_power_words", False)),
                how_phrase_arrives=clip.how_phrase_arrives or "All at once",
                outline_width=float(clip.outline_width if clip.outline_width is not None else 4.0),
                shadow_depth=float(clip.shadow_depth if clip.shadow_depth is not None else 3.0),
                glow_intensity=float(clip.glow_intensity or 0.0),
                letter_spacing=float(clip.letter_spacing or 0.0),
                clean_filler_words=bool(clip.clean_filler_words),
                clip_size="Auto",
                color_preset=clip.color_preset or "none",
                exposure=float(clip.exposure or 0.0),
                brightness=float(clip.brightness or 0.0),
                contrast=float(clip.contrast if clip.contrast is not None else 1.0),
                lift_mids=float(clip.lift_mids or 0.0),
                warmth=float(clip.warmth or 0.0),
                saturation=float(clip.saturation if clip.saturation is not None else 1.0),
                black_and_white=float(clip.black_and_white or 0.0),
                vintage=float(clip.vintage or 0.0),
                tint_color=clip.tint_color or "none",
                tint_strength=float(clip.tint_strength or 0.0),
                glow=float(clip.glow or 0.0),
                smooth=float(clip.smooth or 0.0),
                sharpness=float(clip.sharpness or 0.0),
                blur=float(clip.blur or 0.0),
                film_grain=float(clip.film_grain or 0.0),
                vignette=float(clip.vignette or 0.0),
                bottom_fade=float(clip.bottom_fade or 0.0),
                watermark_path=clip.watermark_path,
                watermark_corner=clip.watermark_corner or "top_right",
                watermark_opacity=float(clip.watermark_opacity if clip.watermark_opacity is not None else 0.85),
                watermark_size=float(clip.watermark_size if clip.watermark_size is not None else 0.15),
                outro_path=clip.outro_path,
                bgm_path=clip.bgm_path,
                bgm_volume=float(clip.bgm_volume if clip.bgm_volume is not None else 0.18),
                audio_volume=float(getattr(clip, "audio_volume", 1.0)),
                normalize_audio=bool(getattr(clip, "normalize_audio", True)),
                vocal_clarity=bool(getattr(clip, "vocal_clarity", False)),
                voice_isolation=bool(getattr(clip, "voice_isolation", False)),
                auto_sfx=bool(getattr(clip, "auto_sfx", False)),
                retention_bar_enabled=bool(getattr(clip, "retention_bar_enabled", False)),
                retention_bar_color=getattr(clip, "retention_bar_color", "#C6F32F"),
                outro_cta_enabled=bool(getattr(clip, "outro_cta_enabled", False)),
                outro_cta_text=getattr(clip, "outro_cta_text", "Subscribe for More"),
                hardware_mode=clip.hardware_mode or "gpu",
                hook_headline=clip.hook_headline or "",
                show_hook_banner=bool(clip.show_hook_banner),
                cold_open_quote=clip.cold_open_quote or "",
                use_cold_open=bool(clip.use_cold_open),
                auto_trim_dead_air=bool(clip.auto_trim_dead_air),
                dynamic_punch_zoom=bool(clip.dynamic_punch_zoom),
                punch_zoom_intensity=float(getattr(clip, "punch_zoom_intensity", 1.14)),
                tighten_pacing=bool(getattr(clip, "tighten_pacing", False)),
                silence_threshold=float(getattr(clip, "silence_threshold", 0.50)),
                censor_profanity=is_censored,
                quality_mode=quality_mode,
                output_path_override=out_mp4,
                allow_concurrent=True,
            )
            if rendered_out and os.path.exists(rendered_out):
                rendered_files.append((rendered_out, arcname))

        # 4: Generate 04_Thumbnail_Cover.jpg (Laplacian sharpness + vocal energy peak + Hook Banner)
        thumb_jpg = os.path.join(work_dir, "04_Thumbnail_Cover.jpg")
        thumb_png = os.path.join(work_dir, "thumb_cover.png")
        best_rel_ts = _find_best_thumbnail_rel_ts(master_video, clip.start_time, clip.end_time, raw_words)
        cover_hook = clip.hook_headline or clip.selected_title or f"VIRAL CLIP #{clip.id}"
        try:
            render_exact_preview_frame(
                video_path=master_video,
                timestamp=float(clip.start_time) + best_rel_ts,
                output_path=thumb_png,
                aspect_ratio="9:16",
                custom_width=1080,
                custom_height=1920,
                frame_layout=clip.frame_layout or "auto_track",
                webcam_corner=clip.webcam_corner or "top_left",
                webcam_split_ratio=float(clip.webcam_split_ratio or 0.38),
                pip_border_color=clip.pip_border_color or "#F59E0B",
                zoom=float(clip.zoom or 1.0),
                pan_x_offset=float(clip.pan_x_offset or 0.0),
                color_grading={
                    "preset": clip.color_preset or "none",
                    "exposure": float(clip.exposure or 0.0),
                    "brightness": float(clip.brightness or 0.0),
                    "contrast": float(clip.contrast if clip.contrast is not None else 1.0),
                    "lift_mids": float(clip.lift_mids or 0.0),
                    "warmth": float(clip.warmth or 0.0),
                    "saturation": float(clip.saturation if clip.saturation is not None else 1.0),
                    "black_and_white": float(clip.black_and_white or 0.0),
                    "vintage": float(clip.vintage or 0.0),
                    "tint_color": clip.tint_color or "none",
                    "tint_strength": float(clip.tint_strength or 0.0),
                    "glow": float(clip.glow or 0.0),
                    "smooth": float(clip.smooth or 0.0),
                    "sharpness": float(clip.sharpness or 0.0),
                    "blur": float(clip.blur or 0.0),
                    "film_grain": float(clip.film_grain or 0.0),
                    "vignette": float(clip.vignette or 0.0),
                    "bottom_fade": float(clip.bottom_fade or 0.0),
                },
                burn_captions=bool(clip.burn_captions),
                caption_options={
                    "style_name": clip.caption_style or "Karaoke pop",
                    "font_name": clip.caption_font or "Montserrat",
                    "font_size_percent": int(clip.font_size_percent or 100),
                    "base_color": clip.base_color or "#FFFFFF",
                    "active_color": clip.active_color or "#C6F32F",
                    "outline_color": clip.outline_color or "#000000",
                    "all_caps": bool(clip.all_caps),
                    "text_align": clip.text_align or "center",
                    "position_grid": clip.position_grid or "bottom_center",
                    "position_x": float(clip.position_x or 50.0),
                    "position_y": float(clip.position_y or 78.0),
                    "hold_back_alpha": float(clip.hold_back_alpha if clip.hold_back_alpha is not None else 1.0),
                    "highlight_shape": clip.highlight_shape or "Colour only",
                    "outline_width": float(clip.outline_width if clip.outline_width is not None else 4.0),
                    "shadow_depth": float(clip.shadow_depth if clip.shadow_depth is not None else 3.0),
                    "glow_intensity": float(clip.glow_intensity or 0.0),
                    "letter_spacing": float(clip.letter_spacing or 0.0),
                    "word_by_word": str(clip.how_phrase_arrives or "").lower().replace("-", " ") == "word by word",
                    "auto_emoji": bool(clip.emoji_enabled),
                    "highlight_power_words": bool(getattr(clip, "highlight_power_words", False)),
                    "hook_banner_enabled": True,
                    "hook_headline": cover_hook,
                    "retention_bar_enabled": False,
                    "outro_cta_enabled": False,
                },
                transcript_words=export_words,
                clip_start_time=clip.start_time,
                clip_duration=duration,
                clip_rel_ts_override=0.25 if best_rel_ts < 0.15 else best_rel_ts,
                clip_size="1080p",
                watermark_path=clip.watermark_path,
                watermark_corner=clip.watermark_corner or "top_right",
                watermark_opacity=float(clip.watermark_opacity if clip.watermark_opacity is not None else 0.85),
                watermark_size=float(clip.watermark_size if clip.watermark_size is not None else 0.15),
            )
            if os.path.exists(thumb_png):
                with Image.open(thumb_png) as img:
                    img.convert("RGB").save(thumb_jpg, "JPEG", quality=92)
        except Exception:
            fallback_img = Image.new("RGB", (1080, 1920), (16, 18, 28))
            fallback_img.save(thumb_jpg, "JPEG", quality=90)

        # 5: 05_Captions.srt
        srt_path = os.path.join(work_dir, "05_Captions.srt")
        srt_content = build_srt_content(export_words)
        with open(srt_path, "w", encoding="utf-8") as f:
            f.write(srt_content)

        # 6: 06_Social_Copy.txt
        copy_path = os.path.join(work_dir, "06_Social_Copy.txt")
        copy_res = generate_multi_platform_copy(
            transcript_words=export_words,
            hook_headline=clip.hook_headline or "",
            selected_title=clip.selected_title or "",
            existing_description=clip.youtube_description or "",
            api_key=api_key,
            preferred_model=gemini_model,
        )
        copy_map = copy_res.get("platforms") if isinstance(copy_res, dict) and "platforms" in copy_res else copy_res
        copy_text = _format_social_copy_text(clip, copy_map or {})
        with open(copy_path, "w", encoding="utf-8") as f:
            f.write(copy_text)

        with zipfile.ZipFile(zip_path, "w", compression=zipfile.ZIP_DEFLATED) as zf:
            for src_path, arcname in rendered_files:
                zf.write(src_path, arcname=arcname)
            if os.path.exists(thumb_jpg):
                zf.write(thumb_jpg, arcname="04_Thumbnail_Cover.jpg")
            zf.write(srt_path, arcname="05_Captions.srt")
            zf.write(copy_path, arcname="06_Social_Copy.txt")

        return zip_path, download_filename
    finally:
        shutil.rmtree(work_dir, ignore_errors=True)
