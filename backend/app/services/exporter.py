"""
Pro Mastering & Multi-Deliverable Exporter (`exporter.py`).
Supports MP4 H.264, MP4 HEVC/H.265, MOV ProRes 422 LT, WebM VP9, Audio Only WAV 24-bit,
Audio Only MP3 320k, Clean Master vs Burned-In Captions, Full-Res Cover .PNG, and
Multi-Aspect Batch Export (.zip).
"""

import json
import logging
import os
import shutil
import subprocess
import tempfile
import uuid
import zipfile
from pathlib import Path
from typing import Any, Dict, List, Optional

from app.config import settings
from app.models import Project, ViralClip
from app.services.audio_mastering import build_voice_filter_chain
from app.services.nle_exporter import build_srt_content, build_vtt_content
from app.services.preview_frame import render_exact_preview_frame
from app.services.proxy import get_ffmpeg_cmd
from app.services.timeline_editor import compute_kept_segments_and_remapped_words

logger = logging.getLogger(__name__)

EXPORT_FORMAT_SPECS: Dict[str, Dict[str, str]] = {
    "mp4_h264": {"codec": "h264", "ext": ".mp4", "media_type": "video/mp4", "label": "MP4 H.264"},
    "mp4_hevc": {"codec": "hevc", "ext": ".mp4", "media_type": "video/mp4", "label": "MP4 HEVC / H.265"},
    "mov_prores": {"codec": "prores", "ext": ".mov", "media_type": "video/quicktime", "label": "MOV ProRes 422 LT"},
    "webm_vp9": {"codec": "vp9", "ext": ".webm", "media_type": "video/webm", "label": "WebM VP9"},
    "audio_wav": {"codec": "wav", "ext": ".wav", "media_type": "audio/wav", "label": "Audio Only WAV 24-bit"},
    "audio_mp3": {"codec": "mp3", "ext": ".mp3", "media_type": "audio/mpeg", "label": "Audio Only MP3 320k"},
    "cover_png": {"codec": "png", "ext": ".png", "media_type": "image/png", "label": "Full-Res Cover .PNG"},
}


def export_audio_stem(
    clip: ViralClip,
    project: Project,
    format_id: str = "audio_wav",
) -> Dict[str, Any]:
    """Export clean audio deliverable in 24-bit WAV (`pcm_s24le`) or MP3 320k (`libmp3lame`)."""
    fmt_key = "audio_mp3" if str(format_id).strip().lower() in ("audio_mp3", "mp3") else "audio_wav"
    spec = EXPORT_FORMAT_SPECS[fmt_key]
    src = project.source_path
    if not src or not os.path.exists(src):
        raise FileNotFoundError("Project source video not found on disk")

    opts = clip.get_options()
    af = build_voice_filter_chain(
        auto_trim_dead_air=bool(clip.auto_trim_dead_air),
        tighten_pacing=bool(clip.tighten_pacing),
        voice_isolation=bool(clip.voice_isolation),
        vocal_clarity=bool(clip.vocal_clarity),
        voice_enhancer=bool(opts.get("voice_enhancer", False)),
        audio_volume=float(clip.audio_volume or 1.0),
        normalize_audio=bool(clip.normalize_audio),
        voice_eq_preset=str(opts.get("voice_eq_preset", "Raw") or "Raw"),
        de_esser=bool(opts.get("de_esser", False)),
        loudness_target=str(opts.get("loudness_target", settings.LOUDNESS_TARGET or "-14 LUFS") or "-14 LUFS"),
        speed_ramp=float(opts.get("speed_ramp", 1.0) or 1.0),
    )

    exports_dir = Path(settings.STORAGE_DIR) / "renders" / "exports"
    exports_dir.mkdir(parents=True, exist_ok=True)
    out_filename = f"lost_heaven_clip_{clip.id}_master{spec['ext']}"
    out_path = str((exports_dir / f"{uuid.uuid4().hex[:8]}_{out_filename}").resolve())

    ffmpeg = get_ffmpeg_cmd()
    start_sec = max(0.0, float(clip.start_time or 0.0))
    dur_sec = max(0.5, float(clip.duration or 5.0))
    codec_args = (
        ["-c:a", "libmp3lame", "-b:a", "320k", "-ar", "48000"]
        if fmt_key == "audio_mp3"
        else ["-c:a", "pcm_s24le", "-ar", "48000"]
    )
    cmd = [
        ffmpeg,
        "-y",
        "-ss", f"{start_sec:.3f}",
        "-t", f"{dur_sec:.3f}",
        "-i", os.path.abspath(src),
        "-vn",
        "-af", af,
        *codec_args,
        out_path,
    ]
    res = subprocess.run(cmd, capture_output=True, text=True, timeout=60)
    if res.returncode != 0 or not os.path.exists(out_path):
        raise RuntimeError(f"Audio stem export failed ({fmt_key}): {(res.stderr or '')[-400:]}")
    return {
        "file_path": out_path,
        "filename": out_filename,
        "media_type": spec["media_type"],
        "codec": spec["codec"],
    }


def export_full_res_cover_png(
    clip: ViralClip,
    project: Project,
    resolution: Optional[str] = None,
) -> Dict[str, Any]:
    """Export full target-resolution PNG cover frame cleanly from the clip's best frame / anchor."""
    src = project.source_path
    if not src or not os.path.exists(src):
        raise FileNotFoundError("Project source video not found on disk")

    best_frames = clip.get_best_frames() or []
    ts = float(clip.start_time or 0.0) + max(0.2, float(clip.duration or 4.0) * 0.25)
    if best_frames and isinstance(best_frames[0], dict) and best_frames[0].get("t") is not None:
        ts = float(best_frames[0]["t"])

    opts = clip.get_options()
    res = render_exact_preview_frame(
        video_path=src,
        timestamp=ts,
        aspect_ratio=clip.aspect_ratio or "9:16",
        custom_width=int(clip.custom_width or 1080),
        custom_height=int(clip.custom_height or 1920),
        frame_layout=clip.frame_layout or "auto_track",
        webcam_corner=clip.webcam_corner or "top_left",
        webcam_split_ratio=float(clip.webcam_split_ratio or 0.40),
        source_webcam_region=clip.get_source_webcam_region(),
        visual_map=clip.get_visual_map(),
        framing_override=clip.get_framing_override(),
        best_frames=best_frames,
        pip_border_color=clip.pip_border_color or "#F59E0B",
        zoom=float(clip.zoom or 1.0),
        pan_x_offset=float(clip.pan_x_offset or 0.0),
        color_grading={
            "preset": clip.color_preset or "none",
            "exposure": float(clip.exposure or 0.0),
            "contrast": float(clip.contrast or 1.0),
            "saturation": float(clip.saturation or 1.0),
            "sharpness": float(clip.sharpness or 0.0),
            "vignette": float(clip.vignette or 0.0),
            "horizon_tilt": float(opts.get("horizon_tilt", 0.0) or 0.0),
            "lut_path": opts.get("lut_path"),
        },
        burn_captions=False,
        caption_options={
            "hook_headline": clip.hook_headline or clip.selected_title or "",
            "show_hook_banner": bool(clip.show_hook_banner),
        },
        clip_start_time=float(clip.start_time or 0.0),
        clip_duration=float(clip.duration or 5.0),
        clip_size=resolution or clip.clip_size or "1080p",
        export_resolution=resolution or clip.clip_size or "1080p",
        project_id=project.id,
    )
    png_name = Path(res["frame_url"]).name
    png_path = str((Path(settings.STORAGE_DIR) / "previews" / png_name).resolve())
    return {
        "file_path": png_path,
        "filename": f"lost_heaven_clip_{clip.id}_cover.png",
        "media_type": "image/png",
        "codec": "png",
    }


def export_pro_master(
    clip: ViralClip,
    project: Project,
    format_id: str = "mp4_h264",
    resolution: Optional[str] = None,
    fps: Optional[str] = None,
    quality_preset: str = "social_balanced",
    burn_captions: Optional[bool] = None,
    hardware_mode: str = "gpu",
) -> Dict[str, Any]:
    """Export any master deliverable (`mp4_h264`, `mp4_hevc`, `mov_prores`, `webm_vp9`, `audio_wav`, `audio_mp3`, `cover_png`)."""
    fmt_key = str(format_id or "mp4_h264").strip().lower()
    if fmt_key not in EXPORT_FORMAT_SPECS:
        fmt_key = "mp4_h264"

    if fmt_key in ("audio_wav", "audio_mp3"):
        return export_audio_stem(clip, project, format_id=fmt_key)
    if fmt_key == "cover_png":
        return export_full_res_cover_png(clip, project, resolution=resolution)

    from app.services.renderer import render_clip_ultra

    src = project.source_path
    if not src or not os.path.exists(src):
        raise FileNotFoundError("Project source video not found on disk")

    spec = EXPORT_FORMAT_SPECS[fmt_key]
    eff_res = resolution or clip.clip_size or "1080p"
    eff_burn = bool(clip.burn_captions) if burn_captions is None else bool(burn_captions)
    opts = dict(clip.get_options() or {})
    if fps:
        opts["export_fps"] = str(fps)
    opts["export_codec"] = spec["codec"]
    opts["export_quality_preset"] = str(quality_preset or "social_balanced").strip().lower()

    exports_dir = Path(settings.STORAGE_DIR) / "renders" / "exports"
    exports_dir.mkdir(parents=True, exist_ok=True)
    clean_tag = "clean" if not eff_burn else "captioned"
    out_filename = f"lost_heaven_clip_{clip.id}_{eff_res}_{clean_tag}{spec['ext']}"
    out_path = str((exports_dir / f"{uuid.uuid4().hex[:8]}_{out_filename}").resolve())

    words = clip.get_transcript()
    rendered_path = render_clip_ultra(
        master_video_path=src,
        clip_id=int(clip.id),
        start_time=float(clip.start_time or 0.0),
        duration=max(0.5, float(clip.duration or 5.0)),
        words=words,
        project_id=project.id,
        aspect_ratio=clip.aspect_ratio or "9:16",
        custom_width=int(clip.custom_width or 1080),
        custom_height=int(clip.custom_height or 1920),
        frame_layout=clip.frame_layout or "auto_track",
        webcam_corner=clip.webcam_corner or "auto",
        webcam_split_ratio=float(clip.webcam_split_ratio or 0.40),
        source_webcam_region=clip.get_source_webcam_region(),
        visual_map=clip.get_visual_map(),
        framing_override=clip.get_framing_override(),
        best_frames=clip.get_best_frames(),
        camera_motion=clip.camera_motion or "steady",
        whip_pan_cuts=bool(clip.whip_pan_cuts),
        pip_border_color=clip.pip_border_color or "#F59E0B",
        zoom=float(clip.zoom or 1.0),
        pan_x_offset=float(clip.pan_x_offset or 0.0),
        burn_captions=eff_burn,
        timing_offset=float(clip.timing_offset or 0.0),
        caption_style=clip.caption_style or "Karaoke pop",
        caption_font=clip.caption_font or "Montserrat",
        caption_animation=clip.caption_animation or "Karaoke",
        font_size_percent=int(clip.font_size_percent or 100),
        base_color=clip.base_color or "#FFFFFF",
        active_color=clip.active_color or "#C6F32F",
        outline_color=clip.outline_color or "#000000",
        bold=bool(clip.bold),
        italic=bool(clip.italic),
        all_caps=bool(clip.all_caps),
        text_align=clip.text_align or "Centre",
        position_grid=clip.position_grid or "bottom_center",
        position_x=float(clip.position_x or 50.0),
        position_y=float(clip.position_y or 78.0),
        outline_width=float(clip.outline_width or 4.0),
        shadow_depth=float(clip.shadow_depth or 3.0),
        glow_intensity=float(clip.glow_intensity or 0.0),
        letter_spacing=float(clip.letter_spacing or 0.0),
        emoji_enabled=bool(clip.emoji_enabled),
        highlight_power_words=bool(clip.highlight_power_words),
        clean_filler_words=bool(clip.clean_filler_words),
        tighten_pacing=bool(clip.tighten_pacing),
        censor_profanity=bool(clip.censor_profanity),
        clip_size=eff_res,
        color_preset=clip.color_preset or "none",
        exposure=float(clip.exposure or 0.0),
        brightness=float(clip.brightness or 0.0),
        contrast=float(clip.contrast or 1.0),
        saturation=float(clip.saturation or 1.0),
        sharpness=float(clip.sharpness or 0.0),
        vignette=float(clip.vignette or 0.0),
        film_grain=float(clip.film_grain or 0.0),
        bgm_path=clip.bgm_path,
        bgm_volume=float(clip.bgm_volume or 0.20),
        audio_volume=float(clip.audio_volume or 1.0),
        normalize_audio=bool(clip.normalize_audio),
        vocal_clarity=bool(clip.vocal_clarity),
        voice_isolation=bool(clip.voice_isolation),
        auto_sfx=bool(clip.auto_sfx),
        watermark_path=clip.watermark_path,
        watermark_corner=clip.watermark_corner or "top_right",
        watermark_opacity=float(clip.watermark_opacity or 0.85),
        watermark_size=float(clip.watermark_size or 0.15),
        export_resolution=eff_res,
        hardware_mode=hardware_mode or clip.hardware_mode or "gpu",
        hook_headline=clip.hook_headline or "",
        show_hook_banner=bool(clip.show_hook_banner and eff_burn),
        keep_segments=clip.get_keep_segments() or None,
        merge_crossfade_sec=float(clip.merge_crossfade_sec or 0.03),
        use_cold_open=bool(clip.use_cold_open),
        cold_open_quote=clip.cold_open_quote or "",
        auto_trim_dead_air=bool(clip.auto_trim_dead_air),
        dynamic_punch_zoom=bool(clip.dynamic_punch_zoom),
        punch_zoom_intensity=float(clip.punch_zoom_intensity or 1.14),
        options=opts,
        quality_mode="fast_preview" if opts["export_quality_preset"] == "compact" else "final_hq",
        output_path_override=out_path,
        allow_concurrent=True,
    )
    return {
        "file_path": rendered_path,
        "filename": out_filename,
        "media_type": spec["media_type"],
        "codec": spec["codec"],
    }


def export_multi_aspect_batch_zip(
    clip: ViralClip,
    project: Project,
    aspects: Optional[List[str]] = None,
    resolution: str = "720p",
    fps: Optional[str] = None,
    quality_preset: str = "social_balanced",
    burn_captions: bool = True,
    hardware_mode: str = "gpu",
) -> Dict[str, Any]:
    """Render multiple aspect deliverables (e.g. 9:16, 4:5, 1:1, 16:9) + captions into a single `.zip`."""
    from app.services.renderer import render_clip_ultra

    src = project.source_path
    if not src or not os.path.exists(src):
        raise FileNotFoundError("Project source video not found on disk")

    target_aspects = [a for a in (aspects or ["9:16", "4:5", "1:1", "16:9"]) if a in ("9:16", "4:5", "1:1", "16:9", "4:3")]
    if not target_aspects:
        target_aspects = ["9:16", "4:5", "16:9"]

    exports_dir = Path(settings.STORAGE_DIR) / "renders" / "exports"
    exports_dir.mkdir(parents=True, exist_ok=True)
    zip_filename = f"lost_heaven_clip_{clip.id}_multi_aspect.zip"
    zip_path = str((exports_dir / f"{uuid.uuid4().hex[:8]}_{zip_filename}").resolve())

    words = clip.get_transcript()
    tl = compute_kept_segments_and_remapped_words(
        words=words,
        clip_start_time=float(clip.start_time or 0.0),
        clip_duration=max(0.5, float(clip.duration or 5.0)),
        tighten_pacing=bool(clip.tighten_pacing),
        clean_filler_words=bool(clip.clean_filler_words),
    )
    remapped_words = list(tl.get("remapped_words") or words)

    opts = dict(clip.get_options() or {})
    if fps:
        opts["export_fps"] = str(fps)
    opts["export_quality_preset"] = str(quality_preset or "social_balanced")

    work_dir = Path(tempfile.mkdtemp(prefix="lost_heaven_batch_"))
    try:
        rendered_files: List[tuple[str, str]] = []
        for idx, ar in enumerate(target_aspects):
            ar_slug = ar.replace(":", "x")
            out_file = str(work_dir / f"clip_{clip.id}_{ar_slug}.mp4")
            r_path = render_clip_ultra(
                master_video_path=src,
                clip_id=int(clip.id),
                start_time=float(clip.start_time or 0.0),
                duration=max(0.5, float(clip.duration or 5.0)),
                words=words,
                project_id=project.id,
                aspect_ratio=ar,
                frame_layout=clip.frame_layout or "auto_track",
                webcam_corner=clip.webcam_corner or "auto",
                source_webcam_region=clip.get_source_webcam_region(),
                visual_map=clip.get_visual_map(),
                framing_override=clip.get_framing_override(),
                burn_captions=burn_captions,
                caption_style=clip.caption_style or "Karaoke pop",
                caption_font=clip.caption_font or "Montserrat",
                clip_size=resolution or clip.clip_size or "720p",
                export_resolution=resolution or clip.clip_size or "720p",
                hardware_mode=hardware_mode or clip.hardware_mode or "gpu",
                hook_headline=clip.hook_headline or "",
                show_hook_banner=bool(clip.show_hook_banner and burn_captions),
                keep_segments=clip.get_keep_segments() or None,
                options=opts,
                quality_mode="fast_preview" if quality_preset == "compact" else "final_hq",
                output_path_override=out_file,
                allow_concurrent=True,
            )
            rendered_files.append((f"clip_{clip.id}_{ar_slug}.mp4", r_path))

        srt_txt = build_srt_content(remapped_words, bilingual=bool(clip.bilingual_enabled))
        vtt_txt = build_vtt_content(remapped_words, bilingual=bool(clip.bilingual_enabled))
        manifest = {
            "clip_id": clip.id,
            "project_id": project.id,
            "title": clip.selected_title or clip.hook_headline,
            "aspects": target_aspects,
            "resolution": resolution,
            "fps": fps or "source",
            "burn_captions": burn_captions,
        }

        with zipfile.ZipFile(zip_path, "w", compression=zipfile.ZIP_DEFLATED) as zf:
            for arc_name, fp in rendered_files:
                if os.path.exists(fp):
                    zf.write(fp, arcname=arc_name)
            zf.writestr(f"clip_{clip.id}.srt", srt_txt.encode("utf-8"))
            zf.writestr(f"clip_{clip.id}.vtt", vtt_txt.encode("utf-8"))
            zf.writestr("manifest.json", json.dumps(manifest, indent=2).encode("utf-8"))

        return {
            "file_path": zip_path,
            "filename": zip_filename,
            "media_type": "application/zip",
            "aspects": target_aspects,
        }
    finally:
        shutil.rmtree(work_dir, ignore_errors=True)
