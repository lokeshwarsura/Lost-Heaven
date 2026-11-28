import os
import time
import uuid
import shutil
import tempfile
import logging
import subprocess
import threading
from collections import OrderedDict
from pathlib import Path
from typing import Dict, Any, Optional, List, Tuple

from PIL import Image

from app.config import settings
from app.services.proxy import get_ffmpeg_cmd, probe_video
from app.services.reframer import (
    resolve_target_dimensions,
    build_multi_aspect_filter,
    canonicalize_framing_template,
    canonicalize_safe_zone,
    build_filter_complex_args,
    prepare_clip_framing_data,
)
from app.services.face_tracker import (
    track_faces_in_clip,
    detect_faces_offline,
    detect_punch_zoom_peaks,
    compute_punch_zoom_factor_at_t,
    compute_punch_focus_anchor,
)
from app.services.filters import build_advanced_color_filter
from app.services.caption_renderer import render_caption_frame, resolve_hook_banner_state
from app.services.profanity_filter import mask_words_profanity

logger = logging.getLogger(__name__)

# Fast LRU caches for instant (<150ms) live preview responsiveness across slider/template edits
_RAW_FRAME_CACHE: "OrderedDict[Tuple[Any, ...], bytes]" = OrderedDict()
_RAW_FRAME_CACHE_MAX = 16
_FACE_BOX_CACHE: "OrderedDict[Tuple[Any, ...], List[Dict[str, Any]]]" = OrderedDict()
_FACE_BOX_CACHE_MAX = 48
_BEAT_CACHE: "OrderedDict[Tuple[Any, ...], Dict[str, Any]]" = OrderedDict()
_BEAT_CACHE_MAX = 32
_SINGLE_META_CACHE: "OrderedDict[Tuple[Any, ...], Dict[str, Any]]" = OrderedDict()
_SINGLE_META_CACHE_MAX = 48
_PREVIEW_CACHE_LOCK = threading.Lock()


def _video_stat_sig(abs_video: str) -> Tuple[str, int, int]:
    try:
        st = os.stat(abs_video)
        return (abs_video, int(st.st_size), int(st.st_mtime_ns))
    except OSError:
        return (abs_video, 0, 0)


def _extract_single_frame_raw(
    ffmpeg_bin: str,
    abs_video: str,
    seek_ts: float,
    total_dur: float,
    src_w: int,
    src_h: int,
) -> Tuple[bytes, int, int]:
    """
    Decode exactly ONE frame from `abs_video` using fast keyframe seek (`-ss` before `-i`),
    scaling >1280px sources (like 4K 3840x2160) down to 1280px wide even dimensions so live
    preview renders stay under ~150ms, and LRU-cache the raw BGR24 buffer across edits at the
    same timestamp.
    """
    if src_w > 1280:
        dec_w = 1280
        dec_h = max(2, int(round(src_h * 1280.0 / max(1, src_w))))
        if dec_h % 2 != 0:
            dec_h += 1
    else:
        dec_w = src_w if src_w % 2 == 0 else max(2, src_w - 1)
        dec_h = src_h if src_h % 2 == 0 else max(2, src_h - 1)

    eff_seek = max(0.0, float(seek_ts or 0.0))
    if total_dur > 0.2 and eff_seek >= total_dur - 0.05:
        eff_seek = max(0.0, total_dur - 0.08)

    v_sig = _video_stat_sig(abs_video)
    c_key = (*v_sig, round(eff_seek, 2), dec_w, dec_h)
    with _PREVIEW_CACHE_LOCK:
        cached = _RAW_FRAME_CACHE.get(c_key)
        if cached is not None:
            _RAW_FRAME_CACHE.move_to_end(c_key)
            return cached, dec_w, dec_h

    expected_len = dec_w * dec_h * 3
    attempts: List[Tuple[bool, float]] = [(True, eff_seek), (False, eff_seek)]
    if eff_seek > 0.35:
        attempts.append((False, max(0.0, eff_seek - 0.4)))

    raw_bytes = b""
    vf_arg = f"scale={dec_w}:{dec_h},setsar=1"
    for use_hwaccel, ss_val in attempts:
        cmd_dec = [ffmpeg_bin, "-y"]
        if use_hwaccel:
            cmd_dec.extend(["-hwaccel", "auto"])
        cmd_dec.extend([
            "-ss", f"{ss_val:.3f}",
            "-i", abs_video,
            "-frames:v", "1",
            "-vf", vf_arg,
            "-f", "rawvideo",
            "-pix_fmt", "bgr24",
            "-",
        ])
        try:
            proc = subprocess.run(
                cmd_dec,
                stdout=subprocess.PIPE,
                stderr=subprocess.DEVNULL,
                timeout=15,
            )
            if proc.returncode == 0 and len(proc.stdout) >= expected_len:
                raw_bytes = proc.stdout[:expected_len]
                break
        except Exception:
            continue

    if len(raw_bytes) == expected_len:
        with _PREVIEW_CACHE_LOCK:
            _RAW_FRAME_CACHE[c_key] = raw_bytes
            _RAW_FRAME_CACHE.move_to_end(c_key)
            while len(_RAW_FRAME_CACHE) > _RAW_FRAME_CACHE_MAX:
                _RAW_FRAME_CACHE.popitem(last=False)

    return raw_bytes, dec_w, dec_h


def render_exact_preview_frame(
    video_path: str,
    timestamp: float = 0.0,
    aspect_ratio: str = "9:16",
    custom_width: int = 1080,
    custom_height: int = 1920,
    frame_layout: str = "auto_track",
    webcam_corner: str = "top_left",
    source_corner: str = "auto",
    webcam_split_ratio: float = 0.40,
    source_webcam_region: Optional[Dict[str, Any]] = None,
    visual_map: Optional[Dict[str, Any]] = None,
    framing_override: Optional[Dict[str, Any]] = None,
    best_frames: Optional[List[Dict[str, Any]]] = None,
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
    depth_pop: float = 0.0,
    camera_motion: str = "steady",
    whip_pan_cuts: bool = False,
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
    use_cold_open: bool = False,
    cold_open: Optional[bool] = None,
    cold_open_quote: str = "",
    auto_trim_dead_air: bool = False,
    trim_dead_air: Optional[bool] = None,
    silence_threshold: float = 0.55,
    dynamic_punch_zoom: bool = False,
    punch_zoom: Optional[bool] = None,
    punch_zoom_intensity: float = 1.16,
    show_hook: Optional[bool] = None,
    show_hook_banner: Optional[bool] = None,
    hook_banner_enabled: Optional[bool] = None,
    hook_text: Optional[str] = None,
    hook_headline: Optional[str] = None,
    safe_zone: Optional[str] = None,
    framing_template: Optional[str] = None,
    frame_crop_override: Any = "__UNSET__",
    clear_framing_override: bool = False,
) -> Dict[str, Any]:
    """
    Extract a single frame at `timestamp` using the exact FFmpeg reframing,
    face tracking, color grading, and ASS subtitle pipeline.
    Executes FFmpeg with cwd=work_dir and strictly relative ASCII paths inside filtergraph.txt.
    """
    t0 = time.perf_counter()
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
        src_fps = 30.0
        try:
            meta = probe_video(video_path)
            src_w = meta.get("width") or 1920
            src_h = meta.get("height") or 1080
            src_fps = float(meta.get("fps") or 30.0)
        except Exception as e:
            logger.warning(f"Could not probe video {video_path} for preview frame, assuming 1920x1080: {e}")

        layout = canonicalize_framing_template(framing_template if framing_template is not None else frame_layout)

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

        # Multi-segment timeline edit plan (trim_dead_air, cold_open 2.8s climax, & punch_zoom 6 peaks)
        from app.services.timeline_editor import (
            build_timeline_edit_plan,
            map_output_time_to_source_time,
        )

        preview_words = [dict(w) for w in (transcript_words or []) if not w.get("muted", False)]
        co = caption_options or {}
        eff_tighten = bool(co.get("tighten_pacing", False))
        eff_trim_dead_air = bool(
            trim_dead_air
            if trim_dead_air is not None
            else (auto_trim_dead_air or co.get("trim_dead_air", False) or co.get("auto_trim_dead_air", False))
        )
        eff_cold_open = bool(
            cold_open
            if cold_open is not None
            else (use_cold_open or co.get("cold_open", False) or co.get("use_cold_open", False))
        )
        eff_punch_zoom = bool(
            punch_zoom
            if punch_zoom is not None
            else (dynamic_punch_zoom or co.get("punch_zoom", False) or co.get("dynamic_punch_zoom", False))
        )
        eff_punch_intensity = float(punch_zoom_intensity if punch_zoom_intensity is not None else 1.16)
        eff_silence_thresh = float(co.get("silence_threshold", silence_threshold) or 0.55)

        total_vid_dur = float(meta.get("duration") or 0.0) if "meta" in locals() and isinstance(meta, dict) else 0.0
        abs_segs: Optional[List[tuple[float, float]]] = None
        if keep_segments:
            parsed_abs = [
                (max(0.0, float(seg[0])), float(seg[1]))
                for seg in keep_segments
                if isinstance(seg, (list, tuple)) and len(seg) >= 2 and float(seg[1]) > float(seg[0]) + 0.05
            ]
            if parsed_abs:
                abs_segs = parsed_abs

        min_s = min(s for s, _ in abs_segs) if abs_segs else max(0.0, float(clip_start_time or 0.0))
        inferred_span = (
            max(e for _, e in abs_segs) - min_s
            if abs_segs
            else max(
                float(clip_duration or 0.0),
                max(0.5, total_vid_dur - min_s) if total_vid_dur > min_s + 0.5 else 4.0,
            )
        )
        tl_plan = build_timeline_edit_plan(
            video_path=video_path,
            clip_start_time=min_s,
            clip_duration=inferred_span,
            words=preview_words,
            cold_open=eff_cold_open,
            cold_open_quote=cold_open_quote,
            trim_dead_air=eff_trim_dead_air,
            tighten_pacing=eff_tighten,
            silence_threshold=eff_silence_thresh,
            clean_filler_words=bool(co.get("clean_filler_words", False)),
            punch_zoom=eff_punch_zoom,
            punch_zoom_intensity=eff_punch_intensity,
            explicit_keep_segments=abs_segs,
            crossfade_sec=float(merge_crossfade_sec or 0.03),
        )
        eff_segs = tl_plan.kept_segments_abs
        preview_words = [dict(w) for w in tl_plan.remapped_words]
        preview_cap_start = 0.0 if (eff_segs or tl_plan.cold_open_active or tl_plan.has_cuts) else clip_start_time
        preview_cap_dur = tl_plan.effective_duration if (tl_plan.cold_open_active or tl_plan.has_cuts or abs_segs) else clip_duration

        if clip_duration and clip_duration > 0.2:
            if clip_start_time <= 0.0 and clip_rel_ts_override is None:
                clip_rel_ts = seek_ts
            main_framing_segs = list(eff_segs) if eff_segs else [(min_s, min_s + float(clip_duration))]
        else:
            s_anchor = max(0.0, float(seek_ts) - 2.0)
            e_anchor = float(seek_ts) + 3.0
            if total_vid_dur > 0.2:
                e_anchor = min(total_vid_dur, max(s_anchor + 1.0, e_anchor))
            if not tl_plan.cold_open_active and not tl_plan.has_cuts and not abs_segs:
                clip_rel_ts = max(0.0, float(seek_ts) - s_anchor)
                main_framing_segs = [(s_anchor, e_anchor)]
            else:
                main_framing_segs = list(eff_segs)

        cold_open_active = tl_plan.cold_open_active
        cold_open_net_offset = tl_plan.teaser_duration
        main_dur_eval = tl_plan.main_duration
        framing_segs = list(main_framing_segs)
        framing_rel_t = clip_rel_ts

        if cold_open_active and tl_plan.climax_window is not None:
            co_s, co_e = tl_plan.climax_window
            if clip_rel_ts < cold_open_net_offset:
                seek_ts = co_s + clip_rel_ts
                framing_segs = [(co_s, co_e)]
                framing_rel_t = clip_rel_ts
            else:
                eff_main_t = max(0.0, clip_rel_ts - cold_open_net_offset)
                seek_ts = map_output_time_to_source_time(
                    eff_main_t,
                    main_framing_segs,
                    crossfade_sec=float(tl_plan.crossfade_sec),
                )
                framing_rel_t = eff_main_t
        elif (tl_plan.has_cuts or (abs_segs and (len(abs_segs) > 1 or clip_rel_ts_override is not None))) and eff_segs:
            seek_ts = map_output_time_to_source_time(
                clip_rel_ts,
                eff_segs,
                crossfade_sec=float(tl_plan.crossfade_sec),
            )
            framing_rel_t = clip_rel_ts

        abs_video = os.path.abspath(video_path)
        v_sig = _video_stat_sig(abs_video)
        raw_bgr_bytes, dec_w, dec_h = _extract_single_frame_raw(
            ffmpeg_bin=ffmpeg_bin,
            abs_video=abs_video,
            seek_ts=seek_ts,
            total_dur=total_vid_dur,
            src_w=int(src_w),
            src_h=int(src_h),
        )
        eff_src_w = dec_w if raw_bgr_bytes else int(src_w)
        eff_src_h = dec_h if raw_bgr_bytes else int(src_h)

        co = caption_options or {}
        eff_cam_motion = str(camera_motion or co.get("camera_motion") or "steady")
        eff_whip_pan = bool(whip_pan_cuts or co.get("whip_pan_cuts", False))
        eff_dur_val = float(main_dur_eval if framing_rel_t != clip_rel_ts or not cold_open_active else (framing_segs[-1][1] - framing_segs[0][0]))
        eff_source_corner = str(
            source_corner
            if str(source_corner or "").strip().lower() not in ("", "auto")
            else ((source_webcam_region or {}).get("locked_zone") if isinstance(source_webcam_region, dict) else None)
            or "auto"
        )

        # Single-frame fast vision resolver (<6ms, LRU-cached): detects corner webcam & speaker cuts
        # from the ONE decoded BGR frame in memory whenever SQLite lacks cached visual_map/webcam data,
        # without ever opening/scanning the video file via OpenCV/analyze_clip_visuals.
        eff_visual_map = visual_map
        eff_webcam_reg = source_webcam_region
        needs_single_webcam = (
            eff_webcam_reg is None
            and eff_source_corner == "auto"
            and layout in (
                "webcam_gameplay_split",
                "gameplay_webcam_pip",
                "portrait_pip_badge",
                "triple_gaming_hud",
                "side_by_side_50_50",
            )
        )
        needs_single_faces = (
            not isinstance(eff_visual_map, dict)
            and layout in ("split_podcast", "auto_track", "dynamic_punch_crop", "side_by_side_50_50")
        )
        if raw_bgr_bytes and (needs_single_webcam or needs_single_faces):
            m_key = (
                *v_sig,
                round(seek_ts, 2),
                eff_src_w,
                eff_src_h,
                bool(needs_single_webcam),
                bool(needs_single_faces),
                eff_source_corner,
            )
            with _PREVIEW_CACHE_LOCK:
                cached_meta = _SINGLE_META_CACHE.get(m_key)
                if cached_meta is not None:
                    _SINGLE_META_CACHE.move_to_end(m_key)
            if cached_meta is None:
                cached_meta = {}
                try:
                    import numpy as np
                    import cv2
                    from app.services.webcam_detector import detect_webcam_in_frames, _compute_action_focal_point

                    frame_bgr = np.frombuffer(raw_bgr_bytes, dtype=np.uint8).reshape((eff_src_h, eff_src_w, 3))
                    if needs_single_webcam:
                        cached_meta["webcam_reg"] = detect_webcam_in_frames(
                            [frame_bgr],
                            src_w=eff_src_w,
                            src_h=eff_src_h,
                        )
                    if needs_single_faces:
                        if eff_src_w > 480:
                            sc_f = 480.0 / float(eff_src_w)
                            small_f = cv2.resize(frame_bgr, (480, max(2, int(round(eff_src_h * sc_f)))))
                        else:
                            small_f = frame_bgr
                        faces_single = detect_faces_offline(small_f)
                        if len(faces_single) >= 2:
                            f_lr = sorted(faces_single, key=lambda f: float(f.get("cx", 0.5)))
                            s1_cx, s1_cy = float(f_lr[0].get("cx", 0.3)), float(f_lr[0].get("cy", 0.4))
                            s2_cx, s2_cy = float(f_lr[-1].get("cx", 0.7)), float(f_lr[-1].get("cy", 0.4))
                            top_f = max(faces_single, key=lambda f: float(f.get("area", 0.0)))
                            kf = {
                                "t": float(framing_rel_t),
                                "cx": float(top_f.get("cx", s1_cx)),
                                "cy": float(top_f.get("cy", s1_cy)),
                                "face_h": float(top_f.get("h", 0.26)),
                                "s1_cx": s1_cx,
                                "s1_cy": s1_cy,
                                "s2_cx": s2_cx,
                                "s2_cy": s2_cy,
                                "shot_id": 0,
                            }
                            shot = {
                                "shot_id": 0,
                                "start_t": 0.0,
                                "end_t": max(1.0, eff_dur_val),
                                "speaker_mode": "two_speaker",
                                "active_speaker_idx": 0,
                            }
                        elif len(faces_single) == 1:
                            f0 = faces_single[0]
                            fcx, fcy = float(f0.get("cx", 0.5)), float(f0.get("cy", 0.4))
                            kf = {
                                "t": float(framing_rel_t),
                                "cx": fcx,
                                "cy": fcy,
                                "face_h": float(f0.get("h", 0.26)),
                                "s1_cx": fcx,
                                "s1_cy": fcy,
                                "s2_cx": min(0.85, fcx + 0.35),
                                "s2_cy": fcy,
                                "shot_id": 0,
                            }
                            shot = {
                                "shot_id": 0,
                                "start_t": 0.0,
                                "end_t": max(1.0, eff_dur_val),
                                "speaker_mode": "single_speaker",
                                "active_speaker_idx": 0,
                            }
                        else:
                            acx, acy = _compute_action_focal_point(small_f)
                            kf = {
                                "t": float(framing_rel_t),
                                "cx": float(acx),
                                "cy": float(acy),
                                "action_cx": float(acx),
                                "action_cy": float(acy),
                                "shot_id": 0,
                            }
                            shot = {
                                "shot_id": 0,
                                "start_t": 0.0,
                                "end_t": max(1.0, eff_dur_val),
                                "speaker_mode": "zero_face",
                                "active_speaker_idx": 0,
                            }
                        cached_meta["visual_map"] = {
                            "start_time": round(framing_segs[0][0], 3),
                            "end_time": round(framing_segs[-1][1], 3),
                            "keyframes": [kf],
                            "shots": [shot],
                            "cut_timestamps": [],
                        }
                except Exception:
                    pass
                with _PREVIEW_CACHE_LOCK:
                    _SINGLE_META_CACHE[m_key] = cached_meta
                    _SINGLE_META_CACHE.move_to_end(m_key)
                    while len(_SINGLE_META_CACHE) > _SINGLE_META_CACHE_MAX:
                        _SINGLE_META_CACHE.popitem(last=False)
            if needs_single_webcam and isinstance(cached_meta.get("webcam_reg"), dict):
                eff_webcam_reg = cached_meta["webcam_reg"]
            if needs_single_faces and isinstance(cached_meta.get("visual_map"), dict):
                eff_visual_map = cached_meta["visual_map"]

        eff_framing_override = (
            None
            if (clear_framing_override or frame_crop_override is None)
            else framing_override
        )
        face_track_data, eff_webcam_corner, _ = prepare_clip_framing_data(
            video_path=video_path,
            kept_segments_abs=framing_segs,
            frame_layout=layout,
            webcam_corner=webcam_corner,
            source_webcam_region=eff_webcam_reg,
            visual_map=eff_visual_map,
            framing_override=eff_framing_override,
            best_frames=best_frames,
            merge_crossfade_sec=float(merge_crossfade_sec or 0.03),
            skip_video_scan=True,
        )

        beat_ts_prev: Optional[List[float]] = None
        if eff_cam_motion == "beat_punch":
            try:
                from app.services.beat_sync import detect_audio_beats
                b_key = (*v_sig, round(framing_segs[0][0], 1), round(max(1.0, eff_dur_val), 1), 8, "beats")
                with _PREVIEW_CACHE_LOCK:
                    bd = _BEAT_CACHE.get(b_key)
                if bd is None:
                    bd = detect_audio_beats(
                        video_path,
                        start_sec=framing_segs[0][0],
                        duration_sec=max(1.0, eff_dur_val),
                        spectrum_fps=8,
                    )
                    with _PREVIEW_CACHE_LOCK:
                        _BEAT_CACHE[b_key] = bd
                        _BEAT_CACHE.move_to_end(b_key)
                        while len(_BEAT_CACHE) > _BEAT_CACHE_MAX:
                            _BEAT_CACHE.popitem(last=False)
                beat_ts_prev = bd.get("beats") or None
            except Exception:
                beat_ts_prev = None

        eff_cap_y = (
            float(co["caption_y"])
            if co.get("caption_y") is not None
            else (float(co["position_y"]) / 100.0 if co.get("position_y") is not None else None)
        )
        eff_safe_zone = canonicalize_safe_zone(
            safe_zone if safe_zone is not None else co.get("safe_zone", co.get("safe_zone_guide"))
        )
        # 1. Multi-Aspect Framing Filter
        layout_vf = build_multi_aspect_filter(
            aspect_ratio=aspect_ratio,
            frame_layout=layout,
            framing_template=layout,
            source_w=eff_src_w,
            source_h=eff_src_h,
            custom_w=target_w,
            custom_h=target_h,
            webcam_corner=eff_webcam_corner,
            webcam_split_ratio=webcam_split_ratio,
            pip_border_color=pip_border_color,
            zoom=zoom,
            pan_x_offset=pan_x_offset,
            pan_x=pan_x,
            face_track_data=face_track_data,
            is_video_render=False,
            preview_rel_t=framing_rel_t,
            target_size=(target_w, target_h),
            camera_motion=eff_cam_motion,
            whip_pan_cuts=eff_whip_pan,
            clip_duration=eff_dur_val,
            fps=src_fps,
            beat_timestamps=beat_ts_prev,
            source_corner=eff_source_corner,
            caption_y=(eff_cap_y if burn_captions else None),
            safe_zone=eff_safe_zone,
        )

        # 2. Punch Zoom FX + Footage Color Grading & FX BEFORE captions
        filter_elements: List[str] = []
        if eff_punch_zoom and tl_plan.punch_intervals:
            pz_now = compute_punch_zoom_factor_at_t(clip_rel_ts, tl_plan.punch_intervals)
            if pz_now > 1.005:
                fx, fy = (0.5, 0.5)
                if isinstance(framing_override, dict) and isinstance(framing_override.get("focus_anchor"), dict):
                    fx, fy = compute_punch_focus_anchor(face_track_data, t_rel=clip_rel_ts, framing_override=framing_override)
                pw = max(target_w, int(round(target_w * pz_now / 2.0) * 2))
                ph = max(target_h, int(round(target_h * pz_now / 2.0) * 2))
                cx = int(round((pw - target_w) * fx))
                cy = int(round((ph - target_h) * fy))
                filter_elements.append(f"scale={pw}:{ph}:flags=lanczos,crop={target_w}:{target_h}:{cx}:{cy},setsar=1")
        cg = color_grading or {}
        if bool(cg.get("bypass_color", False)):
            color_f = ""
        else:
            staged_lut_rel = None
            if cg.get("lut_path"):
                from app.services.audio_mastering import resolve_local_media_asset, stage_ascii_asset
                lut_abs = resolve_local_media_asset(str(cg.get("lut_path")))
                if lut_abs and os.path.exists(lut_abs):
                    staged_lut_rel = stage_ascii_asset(lut_abs, Path(work_dir), target_stem="preview_lut")
            color_f = build_advanced_color_filter(
                preset=cg.get("preset") or cg.get("color_preset") or "none",
                preset_intensity=cg.get("preset_intensity", 100.0) if cg.get("preset_intensity") is not None else 100.0,
                exposure=cg.get("exposure", 0.0),
                brightness=cg.get("brightness", 0.0),
                contrast=cg.get("contrast", 1.0),
                lift_mids=cg.get("lift_mids", 0.0),
                highlights=cg.get("highlights", 0.0),
                shadows=cg.get("shadows", 0.0),
                warmth=cg.get("warmth", 0.0),
                color_temp=cg.get("color_temp", 0.0),
                tint_shift=cg.get("tint_shift", 0.0),
                vibrance=cg.get("vibrance", 0.0),
                matte_fade=cg.get("matte_fade", 0.0),
                halation_glow=cg.get("halation_glow", 0.0),
                lut_path=staged_lut_rel,
                chroma_split=cg.get("chroma_split", 0.0),
                letterbox=cg.get("letterbox", "none"),
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
                # Never apply dark radial vignette onto PiP webcam / triple HUD layouts
                vignette=(
                    0.0
                    if layout in ("gameplay_webcam_pip", "portrait_pip_badge", "triple_gaming_hud")
                    else cg.get("vignette", 0.0)
                ),
                bottom_fade=cg.get("bottom_fade", 0.0),
                horizon_tilt=cg.get("horizon_tilt", 0.0),
                target_w=target_w,
                target_h=target_h,
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
        eff_depth_pop = max(0.0, min(1.0, float(depth_pop or 0.0)))
        clean_blur_boxes = normalize_blur_boxes(privacy_blur_boxes)

        post_chain = ",".join(filter_elements) if filter_elements else "null"
        fg_segments: List[str] = [layout_vf, f"[v]{post_chain}[v_graded]"]
        curr_v_lbl = "v_graded"

        feed_stdin = False
        if raw_bgr_bytes:
            input_args: List[str] = [
                "-f", "rawvideo",
                "-pix_fmt", "bgr24",
                "-s", f"{dec_w}x{dec_h}",
                "-i", "-",
            ]
            feed_stdin = True
        else:
            input_args = [
                "-ss", f"{seek_ts:.3f}",
                "-i", abs_video,
            ]
        next_in_idx = 1

        # If virtual backdrop or Subject Depth Pop (f/1.8) active, extract matte first
        if eff_backdrop != "none" or eff_depth_pop > 0.01:
            pre_bg_name = "pre_bg.png"
            pre_fg = f"{layout_vf};[v]{post_chain}[vout]"
            (work_dir / "pre_bg_fg.txt").write_text(pre_fg, encoding="utf-8")
            p1 = subprocess.run(
                [
                    ffmpeg_bin, "-y", *input_args,
                    *build_filter_complex_args(ffmpeg_bin, "pre_bg_fg.txt", pre_fg),
                    "-map", "[vout]", "-vframes", "1", "-pred", "none", "-compression_level", "1", pre_bg_name,
                ],
                input=(raw_bgr_bytes if feed_stdin else None),
                capture_output=True, timeout=20, cwd=str(work_dir),
            )
            if p1.returncode == 0 and (work_dir / pre_bg_name).exists():
                import cv2
                raw_bgr = cv2.imread(str(work_dir / pre_bg_name), cv2.IMREAD_COLOR)
                if raw_bgr is not None:
                    matte_u8 = segment_foreground_mask(raw_bgr)
                    cv2.imwrite(str(work_dir / "fg_matte.png"), matte_u8)
                    input_args = ["-i", pre_bg_name, "-i", "fg_matte.png"]
                    feed_stdin = False
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
                        depth_pop=eff_depth_pop,
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

        co = caption_options or {}
        eff_show_hook, eff_hook_text = resolve_hook_banner_state(
            show_hook=show_hook if show_hook is not None else co.get("show_hook"),
            show_hook_banner=show_hook_banner if show_hook_banner is not None else co.get("show_hook_banner"),
            hook_banner_enabled=hook_banner_enabled if hook_banner_enabled is not None else co.get("hook_banner_enabled"),
            enable_hook_banner=co.get("enable_hook_banner"),
            top_hook_banner=co.get("top_hook_banner"),
            hook_text=hook_text if hook_text is not None else co.get("hook_text"),
            hook_headline=hook_headline if hook_headline is not None else co.get("hook_headline"),
            options=co,
        )
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
            or (eff_show_hook and bool(eff_hook_text))
            or cold_open_active
            or co.get("retention_bar_enabled")
            or co.get("outro_cta_enabled")
            or co.get("speaker_lower_thirds")
            or (co.get("auto_callouts") and co.get("callout_cards"))
            or eff_spectrum_enabled
            or (eff_chapters_enabled and eff_chapters)
            or (eff_handle_on and eff_handle_text)
        )

        fg_script = work_dir / "filtergraph.txt"
        fg_script.write_text(filter_complex, encoding="utf-8")

        if render_overlay:
            # Pipe raw RGB24 straight to Pillow (eliminates intermediate PNG encode + disk decode)
            cmd = [
                ffmpeg_bin,
                "-y",
                *input_args,
                *build_filter_complex_args(ffmpeg_bin, "filtergraph.txt", filter_complex),
                "-map", "[vout]",
                "-vframes", "1",
                "-f", "rawvideo",
                "-pix_fmt", "rgb24",
                "-",
            ]
        else:
            cmd = [
                ffmpeg_bin,
                "-y",
                *input_args,
                *build_filter_complex_args(ffmpeg_bin, "filtergraph.txt", filter_complex),
                "-map", "[vout]",
                "-vframes", "1",
                "-pred", "none",
                "-compression_level", "1",
                str(out_path),
            ]

        res = subprocess.run(
            cmd,
            input=(raw_bgr_bytes if feed_stdin else None),
            capture_output=True,
            timeout=20,
            cwd=str(work_dir),
        )
        if res.returncode != 0:
            err_txt = (res.stderr or b"").decode("utf-8", errors="ignore")
            logger.error(f"FFmpeg preview frame failed: {err_txt}")
            raise RuntimeError(f"FFmpeg frame render failed: {err_txt[-400:]}")

        # 3. Pixel-identical Pillow transparent RGBA Caption & Hook Banner overlay
        if render_overlay:
            spec_data = None
            if eff_spectrum_enabled:
                try:
                    from app.services.beat_sync import detect_audio_beats
                    win_s = max(0.0, float(timestamp) - 1.0)
                    s_key = (*v_sig, round(win_s, 1), 2.0, 10, "spec")
                    with _PREVIEW_CACHE_LOCK:
                        raw_spec = _BEAT_CACHE.get(s_key)
                    if raw_spec is None:
                        raw_spec = detect_audio_beats(
                            video_path,
                            start_sec=win_s,
                            duration_sec=2.0,
                            spectrum_fps=10,
                        )
                        with _PREVIEW_CACHE_LOCK:
                            _BEAT_CACHE[s_key] = raw_spec
                            _BEAT_CACHE.move_to_end(s_key)
                            while len(_BEAT_CACHE) > _BEAT_CACHE_MAX:
                                _BEAT_CACHE.popitem(last=False)
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
            expected_rgb_len = target_w * target_h * 3
            if len(res.stdout) >= expected_rgb_len:
                base_rgba = Image.frombytes("RGB", (target_w, target_h), res.stdout[:expected_rgb_len]).convert("RGBA")
            else:
                # Defensive fallback if resolution inside filtergraph differed
                fallback_png = work_dir / "fallback_base.png"
                subprocess.run(
                    [
                        ffmpeg_bin, "-y", *input_args,
                        *build_filter_complex_args(ffmpeg_bin, "filtergraph.txt", filter_complex),
                        "-map", "[vout]", "-vframes", "1", "-pred", "none", "-compression_level", "1", str(fallback_png),
                    ],
                    input=(raw_bgr_bytes if feed_stdin else None),
                    capture_output=True, timeout=20, cwd=str(work_dir),
                )
                with Image.open(fallback_png) as fb:
                    base_rgba = fb.convert("RGBA")
            if True:
                preview_face_kfs: Optional[List[Dict[str, Any]]] = None
                custom_cap_pos = bool(co.get("caption_custom_position", False))
                eff_face_safe_prev = bool(co.get("face_safe_captions", True)) and not custom_cap_pos
                if eff_face_safe_prev:
                    try:
                        f_key = (*v_sig, round(seek_ts, 2), layout, eff_source_corner, base_rgba.width, base_rgba.height)
                        with _PREVIEW_CACHE_LOCK:
                            cached_faces = _FACE_BOX_CACHE.get(f_key)
                        if cached_faces is not None:
                            preview_face_kfs = cached_faces
                        elif isinstance(visual_map, dict) and visual_map.get("keyframes"):
                            vm_kfs = [k for k in visual_map["keyframes"] if isinstance(k, dict)]
                            near_k = min(vm_kfs, key=lambda k: abs(float(k.get("t", 0.0)) - clip_rel_ts), default=None)
                            if near_k and near_k.get("cx") is not None:
                                preview_face_kfs = [{
                                    "t": clip_rel_ts,
                                    "cx": float(near_k.get("cx", 0.5)),
                                    "cy": float(near_k.get("cy", 0.4)),
                                    "face_h": float(near_k.get("face_h", 0.26)),
                                }]
                        if preview_face_kfs is None:
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
                            else:
                                preview_face_kfs = []
                            with _PREVIEW_CACHE_LOCK:
                                _FACE_BOX_CACHE[f_key] = preview_face_kfs
                                _FACE_BOX_CACHE.move_to_end(f_key)
                                while len(_FACE_BOX_CACHE) > _FACE_BOX_CACHE_MAX:
                                    _FACE_BOX_CACHE.popitem(last=False)
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
                    pos_y=co.get("pos_y", co.get("position_y", 78.0)),
                    caption_x=co.get("caption_x"),
                    caption_y=co.get("caption_y"),
                    caption_custom_position=custom_cap_pos,
                    caption_rotation=float(co.get("caption_rotation", 0.0) or 0.0),
                    caption_max_width=float(co.get("caption_max_width", 0.82) or 0.82),
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
                    hook_banner_enabled=eff_show_hook,
                    show_hook=eff_show_hook,
                    hook_headline=eff_hook_text,
                    hook_text=eff_hook_text,
                    retention_bar_enabled=bool(co.get("retention_bar_enabled", False)),
                    retention_bar_color=co.get("retention_bar_color", "#C6F32F"),
                    outro_cta_enabled=bool(co.get("outro_cta_enabled", False)),
                    outro_cta_text=co.get("outro_cta_text", "Subscribe for More"),
                    outro_cta_headline=str(co.get("outro_cta_headline", "") or ""),
                    outro_cta_handle=str(co.get("outro_cta_handle", "") or ""),
                    outro_logo_path=co.get("outro_logo_path"),
                    clip_start_time=preview_cap_start,
                    clip_duration=preview_cap_dur,
                    project_id=project_id,
                    timing_offset=co.get("timing_offset", 0.0),
                    clean_filler_words=co.get("clean_filler_words", False),
                    speakers=co.get("speakers"),
                    speaker_lower_thirds=bool(co.get("speaker_lower_thirds", False)),
                    face_safe_captions=eff_face_safe_prev,
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
                    cold_open_active=cold_open_active,
                    cold_open_teaser_dur=cold_open_net_offset if cold_open_active else 2.8,
                    safe_zone=eff_safe_zone,
                    caption_animation=co.get("caption_animation", co.get("animation", "Pop Bounce")),
                    pill_color=co.get("pill_color"),
                    pacing_density=co.get("pacing_density", "3-4 Words Viral"),
                    text_case=co.get("text_case", "ALL CAPS" if co.get("uppercase", co.get("all_caps", True)) else "As Spoken"),
                )
                composited = Image.alpha_composite(base_rgba, cap_rgba).convert("RGB")
                composited.save(out_path, format="PNG", compress_level=1)

        latency_ms = max(1, int(round((time.perf_counter() - t0) * 1000)))
        return {
            "frame_url": f"/media/previews/{out_filename}",
            "render_time_ms": latency_ms,
            "timestamp": timestamp,
            "timeline_fx_summary": tl_plan.timeline_fx_summary,
        }
    finally:
        shutil.rmtree(work_dir, ignore_errors=True)
