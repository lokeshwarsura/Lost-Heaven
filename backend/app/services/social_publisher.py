import os
import re
import shutil
import tempfile
import uuid
import zipfile
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from PIL import Image
from sqlmodel import Session

from app.models import Project, ViralClip
from app.services.exporter import export_full_res_cover_png
from app.services.nle_exporter import build_srt_content
from app.services.profanity_filter import mask_words_profanity
from app.services.renderer import render_clip_ultra
from app.services.social_copywriter import generate_multi_platform_copy
from app.services.timeline_editor import compute_kept_segments_and_remapped_words
from app.services.youtube_publisher import YOUTUBE_CATEGORIES


def _safe_clip_slug(text: str, fallback: str = "clip") -> str:
    slug = re.sub(r"[^A-Za-z0-9_-]+", "_", (text or "").strip()).strip("_").lower()
    return slug[:44] or fallback


def generate_all_platform_social_copy(
    clip: ViralClip,
    project: Optional[Project] = None,
    api_key: Optional[str] = None,
    preferred_model: Optional[str] = None,
) -> Dict[str, Any]:
    """
    Generate tailored 4-platform copy (`youtube`, `instagram`, `tiktok`, `x` + legacy `platforms`)
    using Gemini AI when configured or offline NLP extraction when offline.
    """
    res = generate_multi_platform_copy(
        transcript_words=clip.get_transcript(),
        hook_headline=clip.hook_headline or "",
        selected_title=clip.selected_title or "",
        existing_description=clip.youtube_description or "",
        api_key=api_key,
        preferred_model=preferred_model,
        viral_rationale=getattr(clip, "viral_rationale", "") or "",
        emotional_score=getattr(clip, "emotional_score", None),
    )
    return {
        "clip_id": clip.id,
        "project_id": clip.project_id,
        "categories": YOUTUBE_CATEGORIES,
        **res,
    }


def _format_youtube_txt(yt: Dict[str, Any]) -> str:
    cat_map = {c["id"]: c["label"] for c in YOUTUBE_CATEGORIES}
    cat_id = str(yt.get("category_id") or "22")
    tags = [str(t).strip() for t in (yt.get("tags") or []) if str(t).strip()]
    return (
        "YOUTUBE SHORTS\n"
        "==============\n"
        f"Title:\n{yt.get('title') or ''}\n\n"
        f"Category: {cat_map.get(cat_id, 'People & Blogs')} (ID {cat_id})\n\n"
        f"Description:\n{yt.get('description') or ''}\n\n"
        f"Comma-Separated Tags:\n{', '.join(tags)}\n\n"
        f"Pinned Comment:\n{yt.get('pinned_comment') or ''}\n"
    )


def _format_instagram_txt(ig: Dict[str, Any]) -> str:
    tags = " ".join(str(t).strip() for t in (ig.get("hashtags") or []) if str(t).strip())
    caption = str(ig.get("caption") or ig.get("description") or "").strip()
    return (
        "INSTAGRAM REELS\n"
        "===============\n"
        f"Caption & Hashtags:\n{caption}\n\n"
        + (f"Hashtags:\n{tags}\n\n" if tags and tags not in caption else "")
        + f"Pinned Comment:\n{ig.get('pinned_comment') or ''}\n"
    )


def _format_tiktok_txt(tt: Dict[str, Any]) -> str:
    tags = " ".join(str(t).strip() for t in (tt.get("hashtags") or []) if str(t).strip())
    caption = str(tt.get("caption") or tt.get("description") or "").strip()
    return (
        "TIKTOK\n"
        "======\n"
        f"Caption & Tags:\n{caption}\n\n"
        + (f"Hashtags:\n{tags}\n\n" if tags and tags not in caption else "")
        + f"Pinned Comment:\n{tt.get('pinned_comment') or ''}\n"
    )


def _ensure_rendered_mp4_for_pack(
    clip: ViralClip,
    project: Project,
    session: Optional[Session] = None,
) -> Optional[str]:
    """Return existing rendered MP4 on disk or auto-render it seamlessly first."""
    for attr in ("rendered_video_path", "rendered_path"):
        cand = getattr(clip, attr, None)
        if cand and os.path.exists(cand) and os.path.getsize(cand) > 0:
            return cand

    master_video = getattr(project, "source_path", "") or ""
    if not master_video or not os.path.exists(master_video):
        return None

    opts = clip.get_options()
    raw_words = clip.get_transcript() or []
    keep_segs = clip.get_keep_segments()
    merge_cf = float(getattr(clip, "merge_crossfade_sec", 0.03) or 0.03)
    render_duration = max(0.5, float(clip.end_time) - float(clip.start_time))

    out_mp4 = render_clip_ultra(
        master_video_path=master_video,
        clip_id=int(clip.id or 0),
        start_time=float(clip.start_time or 0.0),
        duration=render_duration,
        words=raw_words,
        project_id=int(project.id or 0),
        aspect_ratio=clip.aspect_ratio or "9:16",
        custom_width=int(clip.custom_width or 1080),
        custom_height=int(clip.custom_height or 1920),
        frame_layout=clip.frame_layout or "auto_track",
        webcam_corner=clip.webcam_corner or "auto",
        webcam_split_ratio=float(clip.webcam_split_ratio or 0.38),
        source_webcam_region=clip.get_source_webcam_region(),
        visual_map=clip.get_visual_map(),
        framing_override=clip.get_framing_override(),
        best_frames=clip.get_best_frames(),
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
        clip_size=clip.clip_size or "1080p",
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
        censor_profanity=bool(getattr(clip, "censor_profanity", False)),
        quality_mode="final_hq",
        allow_concurrent=True,
        keep_segments=keep_segs,
        merge_crossfade_sec=merge_cf,
        options=opts,
    )
    if out_mp4 and os.path.exists(out_mp4):
        if session is not None:
            try:
                clip.rendered_video_path = out_mp4
                clip.rendered_path = out_mp4
                clip.status = "ready"
                clip.render_status = "completed"
                session.add(clip)
                session.commit()
            except Exception:
                pass
        return out_mp4
    return None


def build_social_pack_zip(
    clip: ViralClip,
    project: Project,
    session: Optional[Session] = None,
    api_key: Optional[str] = None,
    gemini_model: Optional[str] = None,
) -> Tuple[str, str]:
    """
    Build `<clip_slug>_social_pack.zip` bundling:
      - `<clip_slug>_final.mp4` (auto-renders first if missing on disk)
      - `cover_thumbnail.png`
      - `captions.srt`
      - `youtube_shorts.txt`
      - `instagram_reels.txt`
      - `tiktok.txt`
    """
    clip_slug = _safe_clip_slug(
        clip.selected_title or clip.hook_headline or f"clip_{clip.id}",
        fallback=f"clip_{clip.id}",
    )
    zip_filename = f"{clip_slug}_social_pack.zip"

    packs_root = os.path.join(tempfile.gettempdir(), "lost_heaven_social_packs")
    os.makedirs(packs_root, exist_ok=True)
    zip_path = os.path.join(packs_root, f"{clip_slug}_{clip.id}_{uuid.uuid4().hex[:8]}.zip")

    work_dir = tempfile.mkdtemp(prefix="lost_heaven_spack_")
    try:
        mp4_path = _ensure_rendered_mp4_for_pack(clip, project, session=session)
        if not mp4_path or not os.path.exists(mp4_path):
            raise FileNotFoundError("Could not locate rendered MP4 or master video to bundle social pack.")

        # Cover PNG
        cover_png_path = os.path.join(work_dir, "cover_thumbnail.png")
        try:
            cov = export_full_res_cover_png(clip, project)
            src_cov = cov.get("file_path")
            if src_cov and os.path.exists(src_cov):
                shutil.copy2(src_cov, cover_png_path)
        except Exception:
            pass
        if not os.path.exists(cover_png_path):
            Image.new("RGB", (1080, 1920), (16, 18, 28)).save(cover_png_path, format="PNG")

        # Captions SRT
        raw_words = clip.get_transcript() or []
        keep_segs = clip.get_keep_segments()
        merge_cf = float(getattr(clip, "merge_crossfade_sec", 0.03) or 0.03)
        clip_dur = max(0.5, float(clip.end_time) - float(clip.start_time))
        tl_plan = compute_kept_segments_and_remapped_words(
            words=raw_words,
            clip_start_time=float(clip.start_time or 0.0),
            clip_duration=clip_dur,
            tighten_pacing=bool(getattr(clip, "tighten_pacing", False)),
            silence_threshold=float(getattr(clip, "silence_threshold", 0.50)),
            clean_filler_words=bool(clip.clean_filler_words),
            crossfade_sec=merge_cf,
            explicit_keep_segments=[(s, e) for s, e in keep_segs],
        )
        remapped_words = tl_plan.get("remapped_words") or raw_words
        is_censored = bool(getattr(clip, "censor_profanity", False))
        export_words = mask_words_profanity(remapped_words) if is_censored else remapped_words
        srt_text = build_srt_content(export_words)
        srt_path = os.path.join(work_dir, "captions.srt")
        Path(srt_path).write_text(srt_text, encoding="utf-8")

        # Saved edited social copy OR freshly generated dual-mode copy
        opts = clip.get_options()
        saved_copy = opts.get("social_copy") if isinstance(opts.get("social_copy"), dict) else None
        gen_res = generate_all_platform_social_copy(
            clip=clip,
            project=project,
            api_key=api_key,
            preferred_model=gemini_model,
        )
        yt_data = {**(gen_res.get("youtube") or {})}
        ig_data = {**(gen_res.get("instagram") or {})}
        tt_data = {**(gen_res.get("tiktok") or {})}
        if saved_copy:
            if isinstance(saved_copy.get("youtube"), dict):
                yt_data.update({k: v for k, v in saved_copy["youtube"].items() if v is not None})
            if isinstance(saved_copy.get("instagram"), dict):
                ig_data.update({k: v for k, v in saved_copy["instagram"].items() if v is not None})
            if isinstance(saved_copy.get("tiktok"), dict):
                tt_data.update({k: v for k, v in saved_copy["tiktok"].items() if v is not None})

        yt_txt = _format_youtube_txt(yt_data)
        ig_txt = _format_instagram_txt(ig_data)
        tt_txt = _format_tiktok_txt(tt_data)

        with zipfile.ZipFile(zip_path, "w", compression=zipfile.ZIP_DEFLATED) as zf:
            zf.write(mp4_path, arcname=f"{clip_slug}_final.mp4")
            zf.write(cover_png_path, arcname="cover_thumbnail.png")
            zf.write(srt_path, arcname="captions.srt")
            zf.writestr("youtube_shorts.txt", yt_txt)
            zf.writestr("instagram_reels.txt", ig_txt)
            zf.writestr("tiktok.txt", tt_txt)

        return zip_path, zip_filename
    finally:
        shutil.rmtree(work_dir, ignore_errors=True)
