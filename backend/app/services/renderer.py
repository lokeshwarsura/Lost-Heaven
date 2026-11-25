import os
import re
import time
import uuid
import shutil
import subprocess
import tempfile
import threading
import logging
from pathlib import Path
from typing import Dict, Any, Optional, Callable, List

from app.config import settings
from app.services.proxy import get_ffmpeg_cmd, detect_hardware_encoder, probe_video
from app.services.caption_renderer import build_caption_ffconcat
from app.services.face_tracker import track_faces_in_clip, detect_punch_zoom_peaks
from app.services.audio_mastering import (
    resolve_local_media_asset,
    stage_ascii_asset,
    build_voice_filter_chain,
    build_ducked_bgm_filter,
    extract_room_tone_sample,
    build_room_tone_bed_filter,
)
from app.services.beat_sync import detect_audio_beats, snap_punch_and_broll_to_beats
from app.services.chapter_engine import normalize_chapters
from app.services.sfx_engine import plan_sfx_events, stage_sfx_inputs, build_sfx_mix_filter
from app.services.dubbing_engine import build_dubbed_audio_mix_filter, dub_clip_audio
from app.services.bg_segmentation import (
    append_backdrop_filter_stage,
    canonicalize_backdrop_mode,
    generate_clip_matte_video_and_backdrop,
)
from app.services.privacy_blur import (
    build_privacy_blur_filtergraph,
    normalize_blur_boxes,
    track_privacy_blur_regions,
)
from app.services.broll_engine import IMAGE_EXTS, build_broll_overlay_filter
from app.services.profanity_filter import detect_profanity_intervals, mask_words_profanity
from app.services.timeline_editor import (
    compute_kept_segments_and_remapped_words,
    build_timeline_trim_concat_filter,
    build_profanity_bleep_filter,
)
from app.services.reframer import (
    resolve_target_dimensions,
    build_multi_aspect_filter,
    canonicalize_framing_template,
    build_filter_complex_args,
)
from app.services.filters import build_advanced_color_filter

logger = logging.getLogger(__name__)

_render_lock = threading.RLock()
_active_renders: Dict[str, subprocess.Popen] = {}
_active_render_tokens: Dict[str, str] = {}
_active_temp_dirs: Dict[str, Path] = {}


class RenderSupersededError(Exception):
    """Raised when an in-flight render is terminated by a newer render request for the same clip."""


def cancel_active_render(clip_key: str) -> bool:
    """Immediately terminate any active FFmpeg process for `clip_key` and clean its temp dir."""
    with _render_lock:
        proc = _active_renders.pop(clip_key, None)
        old_temp = _active_temp_dirs.pop(clip_key, None)
        had_active = proc is not None

    if proc is not None:
        try:
            if proc.poll() is None:
                proc.terminate()
                try:
                    proc.wait(timeout=0.5)
                except Exception:
                    proc.kill()
                    try:
                        proc.wait(timeout=0.5)
                    except Exception:
                        pass
        except Exception as e:
            logger.debug(f"Ignored error terminating superseded FFmpeg for {clip_key}: {e}")

    if old_temp is not None and old_temp.exists():
        shutil.rmtree(old_temp, ignore_errors=True)

    return had_active


def apply_watermark_to_filtergraph(
    watermark_path: Optional[str],
    watermark_corner: str,
    watermark_opacity: float,
    watermark_size: float,
    target_w: int,
    exec_dir: Path,
    input_args: List[str],
    next_in_idx: int,
    curr_v_lbl: str,
    fg_segments: List[str],
) -> tuple[int, str]:
    """
    Stage an image watermark into ASCII `exec_dir` and append FFmpeg overlay onto `curr_v_lbl`.
    Returns updated `(next_in_idx, out_v_lbl)`.
    """
    wm_resolved = resolve_local_media_asset(watermark_path)
    if not wm_resolved or not os.path.exists(wm_resolved):
        return next_in_idx, curr_v_lbl

    staged_wm = stage_ascii_asset(wm_resolved, exec_dir, target_stem="watermark_logo")
    if not staged_wm:
        return next_in_idx, curr_v_lbl

    wm_idx = next_in_idx
    input_args.extend(["-i", staged_wm])

    frac = max(0.05, min(0.50, float(watermark_size or 0.15)))
    wm_w = max(24, int(round(target_w * frac / 2.0) * 2))
    opacity = max(0.05, min(1.0, float(watermark_opacity or 0.85)))

    corner = (watermark_corner or "top_right").strip().lower()
    margin = 24
    if corner == "top_left":
        pos = f"{margin}:{margin}"
    elif corner == "bottom_left":
        pos = f"{margin}:H-h-{margin}"
    elif corner == "bottom_right":
        pos = f"W-w-{margin}:H-h-{margin}"
    else:
        pos = f"W-w-{margin}:{margin}"

    fg_segments.append(
        f"[{wm_idx}:v]format=rgba,scale={wm_w}:-1:flags=lanczos,"
        f"colorchannelmixer=aa={opacity:.2f}[wm_scaled];"
        f"[{curr_v_lbl}][wm_scaled]overlay={pos}:format=auto[v_wm]"
    )
    return wm_idx + 1, "v_wm"


def find_cold_open_segment(words: list, cold_open_quote: str, clip_duration: float) -> tuple[float, float]:
    """Find the timestamp window for the cold-open climax quote."""
    clean_quote = re.sub(r"[^\w\s]", "", cold_open_quote).lower().strip()
    words_text = [re.sub(r"[^\w\s]", "", w.get("word", "")).lower().strip() for w in words]

    quote_tokens = clean_quote.split()
    if quote_tokens and len(words_text) >= len(quote_tokens):
        for i in range(len(words_text) - len(quote_tokens) + 1):
            if words_text[i:i + len(quote_tokens)] == quote_tokens:
                start = words[i].get("start", 0.0)
                end = words[i + len(quote_tokens) - 1].get("end", start + 2.5)
                if end - start > 3.5:
                    end = start + 3.5
                return start, end

    default_start = max(0.0, clip_duration * 0.65)
    default_end = min(clip_duration, default_start + 2.5)
    return default_start, default_end


def scale_dimensions_for_clip_size(width: int, height: int, clip_size: str) -> tuple[int, int]:
    """Adjust output width and height according to selected clip size (Auto, 1080p, 720p, 540p, 480p)."""
    norm_size = (clip_size or "1080p").strip().lower()
    if norm_size in ("", "auto", "original"):
        w = width if width % 2 == 0 else width - 1
        h = height if height % 2 == 0 else height - 1
        return max(2, w), max(2, h)

    is_vertical = height > width
    size_map = {
        "1080p": 1920 if is_vertical else 1080,
        "720p": 1280 if is_vertical else 720,
        "540p": 960 if is_vertical else 540,
        "480p": 854 if is_vertical else 480,
    }
    target_h = size_map.get(norm_size)
    if not target_h:
        return width, height

    scale = target_h / float(height)
    new_w = int(round(width * scale / 2) * 2)
    return max(2, new_w), target_h


def render_clip_ultra(
    master_video_path: str,
    clip_id: int,
    start_time: float,
    duration: float,
    words: list,
    project_id: Optional[int] = None,
    # Layout & aspect
    aspect_ratio: str = "9:16",
    custom_width: int = 1080,
    custom_height: int = 1920,
    frame_layout: str = "auto_track",
    webcam_corner: str = "auto",
    webcam_split_ratio: float = 0.38,
    pip_border_color: str = "#F59E0B",
    zoom: float = 1.0,
    pan_x_offset: float = 0.0,
    pan_x: Optional[float] = None,
    # Step 2: Pro Captions & Typography
    burn_captions: bool = True,
    timing_offset: float = 0.0,
    caption_style: str = "Karaoke pop",
    caption_font: str = "Montserrat",
    caption_animation: str = "Karaoke",
    font_size_percent: int = 100,
    base_color: str = "#FFFFFF",
    active_color: str = "#C6F32F",
    outline_color: str = "#000000",
    font_size: int = 64,
    bold: bool = True,
    italic: bool = False,
    all_caps: bool = True,
    text_align: str = "Centre",
    position_grid: str = "bottom_center",
    position_x: float = 50.0,
    position_y: float = 78.0,
    hold_back_alpha: float = 1.0,
    highlight_shape: str = "Colour only",
    emoji_enabled: bool = False,
    highlight_power_words: bool = False,
    how_phrase_arrives: str = "All at once",
    outline_width: float = 4.0,
    shadow_depth: float = 3.0,
    glow_intensity: float = 0.0,
    letter_spacing: float = 0.0,
    clean_filler_words: bool = False,
    tighten_pacing: bool = False,
    silence_threshold: float = 0.45,
    censor_profanity: bool = False,
    # Step 3: Color Grading & Visual FX
    clip_size: str = "1080p",
    color_preset: str = "none",
    exposure: float = 0.0,
    brightness: float = 0.0,
    contrast: float = 1.0,
    lift_mids: float = 0.0,
    warmth: float = 0.0,
    saturation: float = 1.0,
    black_and_white: float = 0.0,
    vintage: float = 0.0,
    tint_color: str = "none",
    tint_strength: float = 0.0,
    glow: float = 0.0,
    smooth: float = 0.0,
    sharpness: float = 0.0,
    blur: float = 0.0,
    film_grain: float = 0.0,
    vignette: float = 0.0,
    bottom_fade: float = 0.0,
    # Branding, Outro & Audio
    watermark_path: Optional[str] = None,
    watermark_corner: str = "top_right",
    watermark_opacity: float = 0.85,
    watermark_size: float = 0.15,
    outro_path: Optional[str] = None,
    bgm_path: Optional[str] = None,
    bgm_volume: float = 0.20,
    audio_volume: float = 1.0,
    normalize_audio: bool = True,
    vocal_clarity: bool = False,
    voice_isolation: bool = False,
    auto_sfx: bool = False,
    retention_bar_enabled: bool = False,
    retention_bar_color: str = "#C6F32F",
    outro_cta_enabled: bool = False,
    outro_cta_text: str = "Subscribe for More",
    broll_segments: Optional[List[Dict[str, Any]]] = None,
    export_resolution: Optional[str] = None,
    hardware_mode: str = "gpu",
    # Hooks & Retention FX
    hook_headline: str = "",
    show_hook_banner: bool = True,
    cold_open_quote: str = "",
    use_cold_open: bool = False,
    auto_trim_dead_air: bool = False,
    dynamic_punch_zoom: bool = False,
    punch_zoom_intensity: float = 1.14,
    quality_mode: str = "final_hq",
    progress_callback: Optional[Callable[[int, str], None]] = None,
    output_path_override: Optional[str] = None,
    allow_concurrent: bool = False,
    keep_segments: Optional[List[List[float]]] = None,
    merge_crossfade_sec: float = 0.03,
    speakers: Optional[Dict[str, Any]] = None,
    speaker_lower_thirds: bool = False,
    face_safe_captions: bool = True,
    callout_cards: Optional[List[Dict[str, Any]]] = None,
    auto_callouts: bool = False,
    bilingual_enabled: bool = False,
    secondary_language: str = "",
    dubbing_enabled: bool = False,
    dubbed_audio_path: Optional[str] = None,
    dubbing_bed_volume: float = 0.15,
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
) -> str:
    """
    Complete ClipShip studio rendering pipeline:
    - Cancellable per-clip Popen queue (`_active_renders` guarded by `_render_lock`)
    - Versioned output filenames (`clip_{id}_r{ms}.mp4`) to prevent Windows file-lock crashes
    - Fast auto-render (`fast_preview`) vs high quality (`final_hq`) presets
    - Dynamic face tracking + 5 framing templates + Step 3 grading + Pillow/ffconcat captions
    """
    ffmpeg = get_ffmpeg_cmd()
    renders_dir = Path(settings.STORAGE_DIR) / "renders"
    renders_dir.mkdir(parents=True, exist_ok=True)

    my_token = uuid.uuid4().hex
    clip_key = (
        f"pack_{project_id if project_id is not None else 0}_{clip_id}_{my_token}"
        if allow_concurrent
        else f"{project_id if project_id is not None else 0}:{clip_id}"
    )
    with _render_lock:
        cancel_active_render(clip_key)
        _active_render_tokens[clip_key] = my_token

    def _ensure_not_superseded():
        with _render_lock:
            if _active_render_tokens.get(clip_key) != my_token:
                raise RenderSupersededError(f"Render for {clip_key} was superseded by a newer request.")

    output_mp4 = (
        os.path.abspath(output_path_override)
        if output_path_override
        else str((renders_dir / f"clip_{clip_id}_r{int(time.time() * 1000)}.mp4").resolve())
    )

    work_dir = (
        Path(settings.STORAGE_DIR)
        / "projects"
        / str(project_id if project_id is not None else "standalone")
        / f"clip_{clip_id}"
    )
    work_dir.mkdir(parents=True, exist_ok=True)

    if progress_callback:
        progress_callback(10, "Probing master video & tracking speaker faces...")

    meta = probe_video(master_video_path)
    src_w = meta["width"]
    src_h = meta["height"]

    base_w, base_h = resolve_target_dimensions(aspect_ratio, custom_width, custom_height)
    eff_resolution = export_resolution or clip_size
    target_w, target_h = scale_dimensions_for_clip_size(base_w, base_h, eff_resolution)

    layout = canonicalize_framing_template(frame_layout)

    # Explicit keep_segments normalization (supports multi-segment blade cuts & merged clips)
    explicit_abs_segs: Optional[List[tuple[float, float]]] = None
    if keep_segments:
        parsed_abs: List[tuple[float, float]] = []
        for seg in keep_segments:
            if isinstance(seg, (list, tuple)) and len(seg) >= 2:
                s_val, e_val = max(0.0, float(seg[0])), float(seg[1])
                if e_val > s_val + 0.1:
                    parsed_abs.append((round(s_val, 3), round(e_val, 3)))
        if parsed_abs:
            explicit_abs_segs = parsed_abs

    source_start_anchor = min(s for s, _ in explicit_abs_segs) if explicit_abs_segs else start_time
    source_span = (
        max(0.5, max(e for _, e in explicit_abs_segs) - source_start_anchor)
        if explicit_abs_segs
        else max(0.5, float(duration))
    )

    render_words = [dict(w) for w in (words or []) if not w.get("muted", False)]
    cold_open_offset = 0.0
    if use_cold_open and cold_open_quote:
        co_start, co_end = find_cold_open_segment(render_words, cold_open_quote, duration)
        cold_open_offset = co_end - co_start + 0.3
        for w in render_words:
            w["start"] = round(float(w.get("start", 0.0)) + cold_open_offset, 3)
            w["end"] = round(float(w.get("end", 0.0)) + cold_open_offset, 3)

    # Plan transcript-native & blade-split kept segments cleanly before tracking faces
    tl_plan = compute_kept_segments_and_remapped_words(
        words=render_words,
        clip_start_time=source_start_anchor,
        clip_duration=source_span,
        tighten_pacing=tighten_pacing,
        silence_threshold=silence_threshold,
        clean_filler_words=clean_filler_words,
        crossfade_sec=(
            max(0.04, float(merge_crossfade_sec or 0.04))
            if tighten_pacing
            else float(merge_crossfade_sec or 0.03)
        ),
        explicit_keep_segments=explicit_abs_segs,
    )
    kept_segments = tl_plan["kept_segments"]
    kept_segments_abs = tl_plan.get("kept_segments_abs") or [
        (round(source_start_anchor + s, 3), round(source_start_anchor + e, 3))
        for s, e in kept_segments
    ]
    has_cuts = tl_plan["has_cuts"]
    effective_duration = float(tl_plan["effective_duration"])
    post_cut_words = tl_plan["remapped_words"]
    use_multi_input = len(kept_segments_abs) > 1

    # Track faces dynamically (stitching per-segment keyframes cleanly for multi-segment/merged clips)
    if use_multi_input:
        stitched_kfs: List[Dict[str, float]] = []
        out_t_cursor = 0.0
        cf_track = float(tl_plan.get("crossfade_sec", merge_crossfade_sec or 0.03))
        detected_corner = "top_left"
        for seg_idx, (seg_s, seg_e) in enumerate(kept_segments_abs):
            seg_len = max(0.2, seg_e - seg_s)
            seg_ft = track_faces_in_clip(
                video_path=master_video_path,
                start_sec=seg_s,
                end_sec=seg_e,
                mode="dual" if layout == "split_podcast" else "single",
            )
            if seg_idx == 0:
                detected_corner = seg_ft.get("detected_webcam_corner", "top_left")
            for kf in (seg_ft.get("keyframes") or []):
                n_kf = dict(kf)
                n_kf["t"] = round(out_t_cursor + float(kf.get("t", 0.0)), 3)
                stitched_kfs.append(n_kf)
            step_cf = min(cf_track, seg_len * 0.25) if seg_idx < len(kept_segments_abs) - 1 else 0.0
            out_t_cursor += seg_len - step_cf
        face_track_data = {
            "video_path": master_video_path,
            "source_w": src_w,
            "source_h": src_h,
            "keyframes": stitched_kfs or [{"t": 0.0, "cx": 0.5, "cy": 0.4}],
            "detected_webcam_corner": detected_corner,
        }
    else:
        ft_s = kept_segments_abs[0][0]
        ft_e = kept_segments_abs[0][1]
        face_track_data = track_faces_in_clip(
            video_path=master_video_path,
            start_sec=ft_s,
            end_sec=ft_e,
            mode="dual" if layout == "split_podcast" else "single",
            cache_dir=str(work_dir),
        )
        detected_corner = face_track_data.get("detected_webcam_corner", "top_left")

    effective_corner = detected_corner if (not webcam_corner or webcam_corner == "auto") else webcam_corner

    if progress_callback:
        progress_callback(28, "Generating transparent RGBA caption frames...")

    has_audio = bool(meta.get("has_audio", True))
    prof_intervals = (
        detect_profanity_intervals(post_cut_words, clip_start_time=0.0, clip_duration=effective_duration)
        if (censor_profanity and has_audio)
        else []
    )
    if censor_profanity:
        post_cut_words = mask_words_profanity(post_cut_words)

    # Drop deleted (caption-hidden) words before generating Pillow caption frames
    caption_words = [w for w in post_cut_words if not w.get("deleted", False)]

    clean_chapters = normalize_chapters(chapters, clip_duration=effective_duration) if (chapters_enabled and chapters) else []
    beat_data: Dict[str, Any] = {}
    if (beat_sync_enabled or spectrum_enabled) and has_audio:
        try:
            beat_data = detect_audio_beats(
                master_video_path,
                start_sec=source_start_anchor,
                duration_sec=effective_duration,
                spectrum_fps=10,
            )
        except Exception as exc:
            logger.debug("Beat/spectrum analysis skipped: %s", exc)
            beat_data = {}

    render_overlay = bool(
        burn_captions
        or retention_bar_enabled
        or outro_cta_enabled
        or speaker_lower_thirds
        or (auto_callouts and callout_cards)
        or (spectrum_enabled and beat_data.get("spectrum_frames"))
        or (chapters_enabled and clean_chapters)
        or (handle_watermark_enabled and str(handle_text or "").strip())
    )
    cap_temp_dir: Optional[Path] = None
    try:
        _ensure_not_superseded()
        if render_overlay:
            is_word_by_word = str(how_phrase_arrives or "All at once").lower().replace("-", " ") == "word by word"
            cap_temp_dir, _, _ = build_caption_ffconcat(
                words=caption_words,
                clip_duration=effective_duration,
                width=target_w,
                height=target_h,
                burn_captions=burn_captions,
                hook_banner_enabled=bool(show_hook_banner and hook_headline),
                hook_headline=hook_headline or "",
                retention_bar_enabled=retention_bar_enabled,
                retention_bar_color=retention_bar_color,
                outro_cta_enabled=outro_cta_enabled,
                outro_cta_text=outro_cta_text,
                clip_start_time=0.0,
                project_id=project_id,
                timing_offset=timing_offset,
                clean_filler_words=False,
                word_by_word=is_word_by_word,
                style_preset=caption_style,
                font_family=caption_font,
                font_size_pct=font_size_percent,
                bold=bold,
                italic=italic,
                uppercase=all_caps,
                alignment=text_align,
                position_grid=position_grid,
                pos_x=position_x,
                pos_y=position_y,
                base_color=base_color,
                highlight_color=active_color,
                outline_color=outline_color,
                highlight_shape=highlight_shape,
                hold_back_opacity=hold_back_alpha,
                outline=outline_width,
                shadow=shadow_depth,
                glow=glow_intensity,
                letter_spacing=letter_spacing,
                auto_emoji=emoji_enabled,
                highlight_power_words=highlight_power_words,
                speakers=speakers,
                speaker_lower_thirds=speaker_lower_thirds,
                face_safe_captions=face_safe_captions,
                face_keyframes=face_track_data.get("keyframes") or [],
                callout_cards=callout_cards,
                auto_callouts=auto_callouts,
                bilingual_enabled=bilingual_enabled,
                secondary_language=secondary_language,
                spectrum_enabled=spectrum_enabled,
                spectrum_position=spectrum_position,
                spectrum_color=spectrum_color,
                spectrum_data=beat_data,
                chapters_enabled=chapters_enabled,
                chapters=clean_chapters,
                handle_watermark_enabled=handle_watermark_enabled,
                handle_text=handle_text,
                handle_platform_icon=handle_platform_icon,
                handle_corner=handle_corner,
                handle_opacity=handle_opacity,
                handle_display_mode=handle_display_mode,
            )
            exec_dir = cap_temp_dir
        else:
            cap_temp_dir = Path(tempfile.mkdtemp(prefix="lost_heaven_rnd_"))
            exec_dir = cap_temp_dir

        with _render_lock:
            _active_temp_dirs[clip_key] = cap_temp_dir

        _ensure_not_superseded()

        if progress_callback:
            progress_callback(42, "Building FFmpeg filtergraph & encoding MP4...")

        v_in_lbl = "0:v"
        a_in_lbl: Optional[str] = "0:a" if has_audio else None
        fg_segments: List[str] = []
        tl_fg = ""

        if has_cuts:
            tl_fg, v_in_lbl, a_in_lbl = build_timeline_trim_concat_filter(
                kept_segments_abs if use_multi_input else kept_segments,
                crossfade_sec=float(tl_plan.get("crossfade_sec", 0.03)),
                has_audio=has_audio,
                multi_input=use_multi_input,
            )
            fg_segments.append(tl_fg)

        layout_vf = build_multi_aspect_filter(
            aspect_ratio=aspect_ratio,
            frame_layout=layout,
            source_w=src_w,
            source_h=src_h,
            custom_w=target_w,
            custom_h=target_h,
            webcam_corner=effective_corner,
            webcam_split_ratio=webcam_split_ratio,
            pip_border_color=pip_border_color,
            zoom=zoom,
            pan_x_offset=pan_x_offset,
            pan_x=pan_x,
            face_track_data=face_track_data,
            is_video_render=True,
            target_size=(target_w, target_h),
            input_label=v_in_lbl,
        )
        fg_segments.append(layout_vf)

        color_vf = build_advanced_color_filter(
            preset=color_preset,
            exposure=exposure,
            brightness=brightness,
            contrast=contrast,
            lift_mids=lift_mids,
            warmth=warmth,
            saturation=saturation,
            black_and_white=black_and_white,
            vintage=vintage,
            tint_color=tint_color,
            tint_strength=tint_strength,
            glow=glow,
            smooth=smooth,
            sharpness=sharpness,
            blur=blur,
            film_grain=film_grain,
            vignette=vignette,
            bottom_fade=bottom_fade,
        )

        punch_data: Dict[str, Any] = {"punch_timestamps": [], "punch_intervals": [], "peak_zoom": punch_zoom_intensity}
        if dynamic_punch_zoom or auto_sfx:
            punch_data = detect_punch_zoom_peaks(
                master_video_path,
                start_sec=source_start_anchor,
                end_sec=source_start_anchor + max(0.5, effective_duration),
                base_zoom=1.0,
                punch_intensity=punch_zoom_intensity,
            )

        active_broll_segments = list(broll_segments or [])
        if beat_sync_enabled and beat_data.get("beats"):
            snapped_punch, active_broll_segments = snap_punch_and_broll_to_beats(
                punch_data if punch_data.get("punch_intervals") else None,
                active_broll_segments,
                beat_data["beats"],
                max_delta=0.25,
            )
            if snapped_punch:
                punch_data = snapped_punch

        punch_zoom_vf = ""
        if dynamic_punch_zoom and punch_data.get("punch_intervals"):
            src_fps = max(15.0, min(60.0, float(meta.get("fps") or 30.0)))
            t_var = f"(on/{src_fps:.3f})"
            z_expr = "1.0"
            for t0, t1, pz in reversed(punch_data["punch_intervals"]):
                dz = max(0.02, float(pz) - 1.0)
                ramp_in = min(0.18, max(0.05, (t1 - t0) * 0.25))
                ramp_out = min(0.22, max(0.05, (t1 - t0) * 0.25))
                seg = (
                    f"if(lt({t_var},{t0 + ramp_in:.3f}),"
                    f"1.0+{dz:.3f}*(({t_var}-{t0:.3f})/{ramp_in:.3f}),"
                    f"if(lt({t_var},{t1 - ramp_out:.3f}),{1.0 + dz:.3f},"
                    f"{1.0 + dz:.3f}-{dz:.3f}*(({t_var}-{t1 - ramp_out:.3f})/{ramp_out:.3f})))"
                )
                z_expr = f"if(between({t_var},{t0:.3f},{t1:.3f}),{seg},{z_expr})"
            punch_zoom_vf = (
                f"zoompan=z='{z_expr}':d=1:x='iw/2-(iw/zoom/2)':y='ih/2-(ih/zoom/2)':"
                f"s={target_w}x{target_h}:fps={src_fps:.3f}"
            )

        # Stage optional BGM & Micro-SFX assets into ASCII temp exec_dir
        input_args: List[str] = []
        if use_multi_input:
            for seg_s, seg_e in kept_segments_abs:
                input_args.extend([
                    "-ss", f"{max(0.0, seg_s):.3f}",
                    "-t", f"{max(0.1, seg_e - seg_s):.3f}",
                    "-i", os.path.abspath(master_video_path),
                ])
            next_in_idx = len(kept_segments_abs)
        else:
            single_s = kept_segments_abs[0][0] if kept_segments_abs else max(0.0, start_time)
            single_t = (
                max(0.1, kept_segments_abs[0][1] - kept_segments_abs[0][0])
                if (kept_segments_abs and not has_cuts)
                else max(0.1, source_span)
            )
            input_args.extend([
                "-ss", f"{max(0.0, single_s):.3f}",
                "-t", f"{single_t:.3f}",
                "-i", os.path.abspath(master_video_path),
            ])
            next_in_idx = 1
        base_input_args = list(input_args)
        pre_fg_chain = f"{tl_fg};{layout_vf}" if has_cuts else layout_vf
        cap_in_idx: Optional[int] = None
        if render_overlay:
            cap_in_idx = next_in_idx
            input_args.extend(["-f", "concat", "-safe", "0", "-i", "captions.ffconcat"])
            next_in_idx += 1

        broll_specs: List[tuple[int, float, float, bool]] = []
        for b_idx, bseg in enumerate(active_broll_segments):
            if not isinstance(bseg, dict) or not bseg.get("enabled", True):
                continue
            b_path = resolve_local_media_asset(bseg.get("asset_path"))
            if not b_path or not os.path.exists(b_path):
                continue
            bs = max(0.0, float(bseg.get("start", 0.0)))
            be = min(effective_duration, float(bseg.get("end", bs + 2.0)))
            if be <= bs + 0.3:
                continue
            staged_b = stage_ascii_asset(b_path, exec_dir, target_stem=f"broll_{b_idx}")
            if not staged_b:
                continue
            is_img = Path(b_path).suffix.lower() in IMAGE_EXTS
            if is_img:
                input_args.extend(["-loop", "1", "-t", f"{(be - bs + 0.5):.3f}", "-i", staged_b])
            else:
                input_args.extend(["-i", staged_b])
            broll_specs.append((next_in_idx, bs, be, is_img))
            next_in_idx += 1

        staged_bgm_name: Optional[str] = None
        bgm_in_idx: Optional[int] = None
        bgm_resolved = resolve_local_media_asset(bgm_path)
        if bgm_resolved:
            staged_bgm_name = stage_ascii_asset(bgm_resolved, exec_dir, target_stem="bgm_track")
            if staged_bgm_name:
                bgm_in_idx = next_in_idx
                input_args.extend(["-stream_loop", "-1", "-i", staged_bgm_name])
                next_in_idx += 1

        sfx_input_specs: List[tuple[int, float, float]] = []
        callout_sfx_times = (
            [
                float(c.get("start", 0.0))
                for c in (callout_cards or [])
                if isinstance(c, dict) and c.get("enabled", True)
            ]
            if auto_callouts
            else []
        )
        if auto_sfx or callout_sfx_times:
            sfx_events = plan_sfx_events(
                effective_duration,
                show_hook_banner=bool(auto_sfx and show_hook_banner and hook_headline),
                hook_headline=hook_headline or "",
                punch_timestamps=punch_data.get("punch_timestamps") if auto_sfx else [],
                callout_timestamps=callout_sfx_times,
            )
            staged_sfx = stage_sfx_inputs(sfx_events, exec_dir)
            for sfx_ev in staged_sfx:
                sfx_input_specs.append((next_in_idx, float(sfx_ev["time"]), float(sfx_ev["gain"])))
                input_args.extend(["-i", sfx_ev["filename"]])
                next_in_idx += 1

        dub_in_idx: Optional[int] = None
        if dubbing_enabled:
            dub_resolved = resolve_local_media_asset(dubbed_audio_path) if dubbed_audio_path else None
            if not dub_resolved or not os.path.exists(dub_resolved):
                try:
                    auto_dub_wav = exec_dir / "dub_auto.wav"
                    dub_clip_audio(
                        words=post_cut_words,
                        clip_duration=effective_duration,
                        output_wav_path=str(auto_dub_wav),
                        clip_start_time=0.0,
                        target_language=secondary_language or "Spanish",
                        prefer_online_tts=False,
                    )
                    if auto_dub_wav.exists():
                        dub_resolved = str(auto_dub_wav)
                except Exception as exc:
                    logger.warning(f"On-demand dub fallback skipped: {exc}")
            if dub_resolved and os.path.exists(dub_resolved):
                staged_dub = stage_ascii_asset(dub_resolved, exec_dir, target_stem="dub_track")
                if staged_dub:
                    dub_in_idx = next_in_idx
                    input_args.extend(["-i", staged_dub])
                    next_in_idx += 1

        # Stage AI foreground matte + static backdrop + tracked privacy blur trajectories
        eff_backdrop = canonicalize_backdrop_mode(backdrop_mode)
        matte_in_idx: Optional[int] = None
        bg_static_in_idx: Optional[int] = None
        if eff_backdrop != "none":
            matte_name, bg_png_name = generate_clip_matte_video_and_backdrop(
                exec_dir=exec_dir,
                base_input_args=base_input_args,
                pre_fg_chain=pre_fg_chain,
                target_w=target_w,
                target_h=target_h,
                fps=float(meta.get("fps") or 24.0),
                duration=effective_duration,
                backdrop_mode=eff_backdrop,
                blur_intensity=backdrop_blur_intensity,
                backdrop_color=backdrop_color,
            )
            if matte_name and (exec_dir / matte_name).exists():
                matte_in_idx = next_in_idx
                input_args.extend(["-i", matte_name])
                next_in_idx += 1
                if bg_png_name and (exec_dir / bg_png_name).exists():
                    bg_static_in_idx = next_in_idx
                    input_args.extend(["-loop", "1", "-t", f"{effective_duration + 0.5:.3f}", "-i", bg_png_name])
                    next_in_idx += 1

        clean_blur_boxes = normalize_blur_boxes(privacy_blur_boxes)
        blur_trajectories: Dict[str, List[Dict[str, float]]] = {}
        if clean_blur_boxes:
            blur_trajectories = track_privacy_blur_regions(
                exec_dir=exec_dir,
                base_input_args=base_input_args,
                pre_blur_chain=pre_fg_chain,
                target_w=target_w,
                target_h=target_h,
                duration=effective_duration,
                boxes=clean_blur_boxes,
            )

        # Footage filters BEFORE Virtual Backdrop, B-Roll, Privacy Blur & transparent PNG overlay
        filter_elements: List[str] = ["setpts=PTS-STARTPTS"]
        if punch_zoom_vf:
            filter_elements.append(punch_zoom_vf)
        if color_vf:
            filter_elements.append(color_vf)

        post_chain = ",".join(filter_elements) if filter_elements else "null"
        fg_segments.append(f"[v]{post_chain}[v_graded]")
        curr_v_lbl = "v_graded"
        if matte_in_idx is not None:
            curr_v_lbl = append_backdrop_filter_stage(
                fg_segments,
                curr_v_lbl=curr_v_lbl,
                matte_input_idx=matte_in_idx,
                backdrop_mode=eff_backdrop,
                target_w=target_w,
                target_h=target_h,
                blur_intensity=backdrop_blur_intensity,
                static_bg_input_idx=bg_static_in_idx,
                out_label="v_backdrop",
            )
        if broll_specs:
            src_fps_b = max(15.0, min(60.0, float(meta.get("fps") or 30.0)))
            broll_fg, curr_v_lbl = build_broll_overlay_filter(
                curr_v_lbl, broll_specs, target_w, target_h, fps=src_fps_b, out_label="v_broll"
            )
            if broll_fg:
                fg_segments.append(broll_fg)
        if clean_blur_boxes:
            blur_frags, curr_v_lbl = build_privacy_blur_filtergraph(
                curr_v_lbl,
                clean_blur_boxes,
                trajectories=blur_trajectories,
                target_w=target_w,
                target_h=target_h,
                is_video_render=True,
                out_label="v_pblur",
            )
            fg_segments.extend(blur_frags)

        if render_overlay and cap_in_idx is not None:
            next_in_idx, curr_v_lbl = apply_watermark_to_filtergraph(
                watermark_path=watermark_path,
                watermark_corner=watermark_corner,
                watermark_opacity=watermark_opacity,
                watermark_size=watermark_size,
                target_w=target_w,
                exec_dir=exec_dir,
                input_args=input_args,
                next_in_idx=next_in_idx,
                curr_v_lbl=curr_v_lbl,
                fg_segments=fg_segments,
            )
            fg_segments.append(f"[{cap_in_idx}:v]format=rgba[cap]")
            fg_segments.append(f"[{curr_v_lbl}][cap]overlay=0:0:eof_action=pass:shortest=1[v_out]")
        else:
            next_in_idx, curr_v_lbl = apply_watermark_to_filtergraph(
                watermark_path=watermark_path,
                watermark_corner=watermark_corner,
                watermark_opacity=watermark_opacity,
                watermark_size=watermark_size,
                target_w=target_w,
                exec_dir=exec_dir,
                input_args=input_args,
                next_in_idx=next_in_idx,
                curr_v_lbl=curr_v_lbl,
                fg_segments=fg_segments,
            )
            fg_segments.append(f"[{curr_v_lbl}]null[v_out]")

        # Build audio chain inside filter_complex when cuts, profanity bleeps, BGM, or SFX apply
        audio_af = build_voice_filter_chain(
            auto_trim_dead_air=auto_trim_dead_air,
            tighten_pacing=tighten_pacing,
            voice_isolation=voice_isolation,
            vocal_clarity=vocal_clarity,
            audio_volume=audio_volume,
            normalize_audio=normalize_audio,
        )

        use_fc_audio = has_audio and (
            has_cuts
            or len(prof_intervals) > 0
            or (bgm_in_idx is not None)
            or len(sfx_input_specs) > 0
            or (dub_in_idx is not None)
        )
        rt_in_idx: Optional[int] = None
        if has_cuts and has_audio:
            rt_path = extract_room_tone_sample(
                master_video_path,
                exec_dir=str(exec_dir),
                start_sec=source_start_anchor,
                duration_sec=effective_duration,
                sample_len=0.50,
            )
            if rt_path and os.path.exists(rt_path):
                rt_in_idx = next_in_idx
                input_args.extend(["-i", os.path.basename(rt_path)])
                next_in_idx += 1

        if use_fc_audio and a_in_lbl:
            curr_a = a_in_lbl
            if prof_intervals:
                fg_segments.append(
                    build_profanity_bleep_filter(curr_a, prof_intervals, effective_duration, out_audio_label="a_bleeped")
                )
                curr_a = "a_bleeped"
            fg_segments.append(f"[{curr_a}]{audio_af}[a_voice]")
            curr_a = "a_voice"
            if rt_in_idx is not None:
                rt_frag, curr_a = build_room_tone_bed_filter(
                    curr_a, rt_in_idx, effective_duration, out_label="a_room"
                )
                fg_segments.append(rt_frag)
            if dub_in_idx is not None:
                dub_frag, curr_a = build_dubbed_audio_mix_filter(
                    curr_a, dub_in_idx, effective_duration, bed_volume=dubbing_bed_volume, out_label="a_dub"
                )
                fg_segments.append(dub_frag)
            if bgm_in_idx is not None:
                bgm_frag, curr_a = build_ducked_bgm_filter(
                    curr_a, bgm_in_idx, effective_duration, bgm_volume=bgm_volume, out_label="a_bgm"
                )
                fg_segments.append(bgm_frag)
            if sfx_input_specs:
                sfx_frag, curr_a = build_sfx_mix_filter(
                    curr_a, sfx_input_specs, effective_duration, out_label="a_sfx"
                )
                fg_segments.append(sfx_frag)
            fg_segments.append(f"[{curr_a}]anull[a_out]")
            audio_cli_args = ["-map", "[a_out]", "-c:a", "aac", "-b:a", "192k"]
        else:
            audio_cli_args = ["-map", "0:a?", "-af", audio_af, "-c:a", "aac", "-b:a", "192k"]

        full_filter_complex = ";".join(fg_segments)
        fg_script = exec_dir / "filtergraph.txt"
        fg_script.write_text(full_filter_complex, encoding="utf-8")

        is_fast_preview = (quality_mode or "final_hq").strip().lower() == "fast_preview"
        detected_encoder = detect_hardware_encoder()
        if is_fast_preview:
            cpu_enc_args = ["-c:v", "libx264", "-preset", "ultrafast", "-crf", "26"]
            enc_args = (
                ["-c:v", "h264_nvenc", "-preset", "p1", "-tune", "ull", "-cq", "26"]
                if (hardware_mode == "gpu" and detected_encoder == "h264_nvenc")
                else cpu_enc_args
            )
        else:
            cpu_enc_args = ["-c:v", "libx264", "-preset", "fast", "-crf", "20"]
            enc_args = (
                ["-c:v", "h264_nvenc", "-preset", "p4", "-cq", "20"]
                if (hardware_mode == "gpu" and detected_encoder == "h264_nvenc")
                else cpu_enc_args
            )

        fg_args = build_filter_complex_args(ffmpeg, "filtergraph.txt", full_filter_complex)

        def _run_cancellable_popen(ffmpeg_cmd: List[str]) -> tuple[int, str]:
            with _render_lock:
                _ensure_not_superseded()
                proc = subprocess.Popen(
                    ffmpeg_cmd,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                    text=True,
                    cwd=str(exec_dir),
                )
                _active_renders[clip_key] = proc

            try:
                _, stderr_out = proc.communicate()
            finally:
                with _render_lock:
                    if _active_renders.get(clip_key) is proc:
                        _active_renders.pop(clip_key, None)

            if _active_render_tokens.get(clip_key) != my_token:
                try:
                    if os.path.exists(output_mp4):
                        os.remove(output_mp4)
                except OSError:
                    pass
                raise RenderSupersededError(f"Render for {clip_key} was superseded.")

            return proc.returncode, (stderr_out or "")

        def _build_cmd(video_enc_args: List[str]) -> List[str]:
            return [
                ffmpeg,
                "-y",
                *input_args,
                *fg_args,
                "-map", "[v_out]",
                *video_enc_args,
                "-pix_fmt", "yuv420p",
                *audio_cli_args,
                "-movflags", "+faststart",
                output_mp4,
            ]

        cmd = _build_cmd(enc_args)
        logger.info(f"Executing Lost Heaven render ({quality_mode}) in {exec_dir}: {' '.join(cmd)}")

        rc, stderr_text = _run_cancellable_popen(cmd)
        if rc != 0:
            if "nvenc" in " ".join(enc_args):
                logger.warning(f"NVENC render failed, retrying with libx264 keeping full filtergraph: {stderr_text[-300:]}")
                rc2, stderr2 = _run_cancellable_popen(_build_cmd(cpu_enc_args))
                if rc2 != 0:
                    raise RuntimeError(f"FFmpeg render failed: {stderr2[-500:]}")
            else:
                raise RuntimeError(f"FFmpeg render failed: {stderr_text[-500:]}")

        # Best-effort clean up older versioned renders for this clip (Windows file-lock safe)
        if not output_path_override:
            resolved_out = Path(output_mp4).resolve()
            for old_file in renders_dir.glob(f"clip_{clip_id}_*.mp4"):
                if old_file.resolve() != resolved_out:
                    try:
                        old_file.unlink()
                    except (PermissionError, OSError):
                        pass

        if progress_callback:
            progress_callback(100, "Render complete! Ready for download and publishing.")

        return output_mp4
    finally:
        with _render_lock:
            if _active_temp_dirs.get(clip_key) == cap_temp_dir:
                _active_temp_dirs.pop(clip_key, None)
        if cap_temp_dir is not None and cap_temp_dir.exists():
            shutil.rmtree(cap_temp_dir, ignore_errors=True)
