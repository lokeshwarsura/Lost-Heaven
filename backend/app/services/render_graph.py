"""
Unified Render Graph Compiler (`render_graph.py`) — Single Source of Truth for Lost Heaven.

Both `preview_frame.py` (sub-200ms single-frame preview) and `renderer.py` (MP4 export)
MUST compile their filtergraphs, timeline cuts, 4K early downscales, framing templates,
safe zones, color grades, watermarks, hook banner gating, and remapped caption words
through `compile_clip_render_plan()`.
"""

from __future__ import annotations

import hashlib
import json
import os
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple

from app.services.caption_renderer import resolve_hook_banner_state
from app.services.filters import (
    build_advanced_color_filter,
    build_depth_pop_foreground_filter,
)
from app.services.proxy import probe_video
from app.services.reframer import (
    build_multi_aspect_filter,
    canonicalize_framing_template,
    canonicalize_safe_zone,
    compute_top_safe_zone_y,
    normalize_pan_ratio,
    resolve_target_dimensions,
)
from app.services.timeline_editor import (
    TimelineEditPlan,
    build_profanity_bleep_filter,
    build_timeline_edit_plan,
    build_timeline_trim_concat_filter,
    sanitize_timeline_intervals,
)

CLIP_SIZE_SHORT_SIDE: Dict[str, int] = {
    "360p": 360,
    "480p": 480,
    "540p": 540,
    "720p": 720,
    "1080p": 1080,
    "1440p": 1440,
    "2k": 1440,
    "4k": 2160,
    "2160p": 2160,
}

COLOR_GRADE_ALIASES: Dict[str, str] = {
    "": "none",
    "original": "none",
    "raw": "none",
    "default": "none",
    "teal_and_orange": "teal_orange",
    "teal-orange": "teal_orange",
    "portra_400": "kodak_portra_400",
    "kodak_portra": "kodak_portra_400",
    "cyberpunk": "cyberpunk_neon",
    "noir": "moody_noir",
    "16mm_bw": "bw_16mm",
    "rec709": "clean_rec709",
    "velvia": "fuji_velvia_50",
    "velvia_50": "fuji_velvia_50",
    "cinestill": "cinestill_800t",
    "vhs": "vhs_90s_tape",
    "podcast_studio": "warm_podcast_studio",
    "emerald_and_gold": "emerald_gold",
}


def even_dim(val: float, minimum: int = 2) -> int:
    """Round any dimension to the nearest even integer (`mod 2`, e.g. 853.33 -> 854)."""
    return max(minimum, int(round(float(val) / 2.0) * 2))


def _canonicalize_corner(val: Any, default: str = "top_left") -> str:
    norm = str(val or default).strip().lower().replace("-", "_").replace(" ", "_")
    if norm in ("top_left", "top_right", "bottom_left", "bottom_right", "center", "auto"):
        return norm
    return default


def canonicalize_color_preset(preset: Any) -> str:
    norm = str(preset or "none").strip().lower().replace(" ", "_").replace("-", "_")
    return COLOR_GRADE_ALIASES.get(norm, norm or "none")


def scale_dimensions_for_size(base_w: int, base_h: int, clip_size: Optional[str]) -> Tuple[int, int]:
    """Scale `(base_w, base_h)` to a preset short-side height while preserving aspect ratio and mod-2 alignment."""
    bw = even_dim(base_w)
    bh = even_dim(base_h)
    cs = str(clip_size or "").strip().lower()
    target_short = CLIP_SIZE_SHORT_SIDE.get(cs)
    if not target_short and cs.endswith("p") and cs[:-1].isdigit():
        target_short = max(120, min(4320, int(cs[:-1])))
    if not target_short:
        return bw, bh
    cur_short = min(bw, bh)
    if cur_short == target_short:
        return bw, bh
    ratio = target_short / float(max(2, cur_short))
    return even_dim(bw * ratio), even_dim(bh * ratio)


def _escape_drawtext(text: str) -> str:
    return (
        str(text or "")
        .replace("\\", "\\\\")
        .replace("'", "\u2019")
        .replace(":", "\\:")
        .replace("%", "\\%")
        .replace(",", "\\,")
    )


def _build_watermark_filter(text: str, opacity: float = 0.45, font_size: int = 24) -> str:
    clean = str(text or "").strip()
    if not clean:
        return ""
    esc = _escape_drawtext(clean)
    alpha = max(0.08, min(1.0, float(opacity if opacity is not None else 0.45)))
    px = max(14, int(round(float(font_size or 24))))
    return (
        f"drawtext=text='{esc}':x=w-tw-28:y=h-th-28:"
        f"fontsize={px}:fontcolor=white@{alpha:.2f}:shadowcolor=black@0.55:shadowx=2:shadowy=2"
    )


def _pick_opt(direct: Any, opts: Dict[str, Any], *keys: str, default: Any = None) -> Any:
    if direct is not None:
        return direct
    for k in keys:
        if k in opts and opts[k] is not None:
            return opts[k]
    return default


def _to_bool(val: Any, default: bool = False) -> bool:
    if val is None:
        return default
    if isinstance(val, bool):
        return val
    if isinstance(val, (int, float)):
        return bool(val)
    s = str(val).strip().lower()
    if s in ("false", "0", "no", "off", ""):
        return False
    if s in ("true", "1", "yes", "on"):
        return True
    return default


@dataclass
class ClipRenderSpec:
    """Canonicalized clip render & preview specification."""

    master_video_path: str
    clip_id: int
    project_id: int
    start_time: float
    duration: float
    aspect_ratio: str
    clip_size: str
    out_w: int
    out_h: int
    framing_template: str
    webcam_corner: str
    source_corner: str
    webcam_split_ratio: float
    pip_border_color: str
    zoom: float
    pan_x: float
    camera_motion: str
    whip_pan_cuts: bool
    safe_zone: str
    face_track_data: Dict[str, Any]
    beat_timestamps: List[float]
    frame_crop_override: Optional[Dict[str, Any]]
    cold_open: bool
    cold_open_quote: str
    climax_badge_enabled: bool
    trim_dead_air: bool
    tighten_pacing: bool
    silence_threshold: float
    clean_filler_words: bool
    punch_zoom: bool
    punch_zoom_intensity: float
    explicit_keep_segments: Optional[List[Tuple[float, float]]]
    crossfade_sec: float
    color_preset: str
    preset_intensity: float
    color_kwargs: Dict[str, Any]
    depth_pop: float
    watermark_text: str
    watermark_opacity: float
    burn_captions: bool
    caption_style: str
    caption_x: float
    caption_y: float
    caption_custom_position: bool
    show_hook: bool
    hook_text: str
    hardware_mode: str
    quality_mode: str
    codec_family: str
    quality_preset: str
    target_fps: float
    audio_mastering_preset: str
    de_ess: bool
    mute_words: List[str]
    bleep_profanity: bool
    bgm_mood: str
    bgm_volume: float
    auto_dub_lang: str
    words: List[Dict[str, Any]]
    raw_options: Dict[str, Any] = field(default_factory=dict)


@dataclass
class ClipRenderPlan:
    """Compiled Single-Source-of-Truth render graph and seek plan."""

    spec: ClipRenderSpec
    source_meta: Dict[str, Any]
    out_w: int
    out_h: int
    target_fps: float
    eff_source_w: int
    eff_source_h: int
    early_downscale_filter: str
    media_ceiling: float
    has_audio: bool
    timeline_plan: TimelineEditPlan
    input_segments: List[Tuple[float, float]]
    segment_is_teaser: List[bool]
    is_multi_segment: bool
    effective_duration: float
    remapped_words: List[Dict[str, Any]]
    reframer_filter_video: str
    color_filter: str
    watermark_filter: str
    show_hook: bool
    hook_text: str
    caption_anchor_x: float
    caption_anchor_y: float
    render_plan_hash: str

    def build_preview_seek_and_filter(
        self,
        timestamp: Optional[float] = None,
        clip_rel_ts_override: Optional[float] = None,
        decoded_source_size: Optional[Tuple[int, int]] = None,
    ) -> Dict[str, Any]:
        """
        Resolve exact source seek timestamp and compile the sub-200ms single-frame FFmpeg filtergraph
        using the exact same reframer, early 4K downscale, safe zones, punch zoom, and color grade as export.
        """
        tl = self.timeline_plan
        anchor_s = max(0.0, float(self.spec.start_time))
        teaser_dur = float(tl.teaser_duration or 0.0) if tl.cold_open_active else 0.0
        kept_rel = list(tl.keep_intervals or [(0.0, max(0.5, self.spec.duration))])
        cf = max(0.0, float(tl.crossfade_sec or 0.0))

        # Compute output timeline relative timestamp
        if clip_rel_ts_override is not None:
            out_rel_t = max(0.0, float(clip_rel_ts_override))
        elif timestamp is not None:
            raw_t = float(timestamp)
            if anchor_s > 0.0 and raw_t >= anchor_s - 0.05:
                out_rel_t = max(0.0, raw_t - anchor_s) + teaser_dur
            else:
                out_rel_t = max(0.0, raw_t)
        else:
            out_rel_t = 0.5 if teaser_dur > 0.6 else min(0.5, self.effective_duration * 0.25)

        out_rel_t = max(0.0, min(max(0.05, self.effective_duration - 0.02), out_rel_t))

        is_climax_teaser = False
        flash_alpha = 0.0
        if tl.cold_open_active and tl.climax_window is not None and out_rel_t < teaser_dur:
            is_climax_teaser = True
            co_s, co_e = tl.climax_window
            src_seek_ts = min(co_e - 0.02, co_s + out_rel_t)
            if tl.flash_duration > 0.0 and out_rel_t <= tl.flash_duration:
                flash_alpha = round(max(0.0, 0.85 * (1.0 - out_rel_t / max(0.01, tl.flash_duration))), 3)
        else:
            main_t = max(0.0, out_rel_t - teaser_dur)
            # Map output main timeline time back to source segment timestamp
            src_rel_t = main_t
            cursor = 0.0
            for idx, (ks, ke) in enumerate(kept_rel):
                seg_len = max(0.05, ke - ks)
                step_cf = min(cf, seg_len * 0.20) if idx > 0 else 0.0
                eff_len = max(0.02, seg_len - step_cf)
                if main_t <= cursor + eff_len or idx == len(kept_rel) - 1:
                    src_rel_t = min(ke - 0.01, ks + max(0.0, main_t - cursor))
                    break
                cursor += eff_len
            src_seek_ts = anchor_s + max(0.0, src_rel_t)
            if (
                tl.cold_open_active
                and tl.flash_duration > 0.0
                and 0.0 <= main_t <= min(0.16, tl.flash_duration)
            ):
                flash_alpha = round(max(0.0, 0.85 * (1.0 - main_t / max(0.01, tl.flash_duration))), 3)

        src_seek_ts = round(max(0.0, min(self.media_ceiling, src_seek_ts)), 3)

        # Compute active punch zoom factor at out_rel_t if punch_zoom enabled
        eff_zoom = float(self.spec.zoom)
        if self.spec.punch_zoom and tl.punch_intervals:
            for ps, pe, pfac in tl.punch_intervals:
                if ps <= out_rel_t <= pe:
                    eff_zoom = round(eff_zoom * max(1.0, float(pfac)), 4)
                    break

        src_w_for_rf = even_dim(decoded_source_size[0]) if decoded_source_size else self.eff_source_w
        src_h_for_rf = even_dim(decoded_source_size[1]) if decoded_source_size else self.eff_source_h
        use_early_ds = bool(self.early_downscale_filter and not decoded_source_size)

        reframer_prev = build_multi_aspect_filter(
            aspect_ratio=self.spec.aspect_ratio,
            frame_layout=self.spec.framing_template,
            framing_template=self.spec.framing_template,
            source_w=src_w_for_rf,
            source_h=src_h_for_rf,
            target_size=(self.out_w, self.out_h),
            webcam_corner=self.spec.webcam_corner,
            webcam_split_ratio=self.spec.webcam_split_ratio,
            pip_border_color=self.spec.pip_border_color,
            zoom=eff_zoom,
            pan_x_offset=self.spec.pan_x,
            face_track_data=self.spec.face_track_data,
            is_video_render=False,
            preview_rel_t=out_rel_t,
            input_label="v_ds" if use_early_ds else "0:v",
            camera_motion=self.spec.camera_motion,
            whip_pan_cuts=self.spec.whip_pan_cuts,
            clip_duration=self.effective_duration,
            fps=self.target_fps,
            beat_timestamps=self.spec.beat_timestamps,
            source_corner=self.spec.source_corner,
            caption_y=self.caption_anchor_y,
            safe_zone=self.spec.safe_zone,
        )

        # Rename reframer output `[v]` -> stage pipeline cleanly
        if reframer_prev.endswith("[v]"):
            prev_body = reframer_prev[:-3]
        else:
            prev_body = reframer_prev

        stages: List[str] = []
        if use_early_ds:
            stages.append(f"[0:v]{self.early_downscale_filter}[v_ds]")
        stages.append(f"{prev_body}[v_rf]")
        curr_lbl = "v_rf"

        if flash_alpha > 0.02:
            stages.append(
                f"[{curr_lbl}]drawbox=x=0:y=0:w=iw:h=ih:color=white@{flash_alpha:.2f}:t=fill[v_fl]"
            )
            curr_lbl = "v_fl"

        if self.color_filter:
            stages.append(f"[{curr_lbl}]{self.color_filter}[v_cg]")
            curr_lbl = "v_cg"

        if self.watermark_filter:
            stages.append(f"[{curr_lbl}]{self.watermark_filter}[v_wm]")
            curr_lbl = "v_wm"

        stages.append(f"[{curr_lbl}]scale=trunc(iw/2)*2:trunc(ih/2)*2,format=rgb24[vout]")

        return {
            "filter_complex": ";".join(stages),
            "out_label": "vout",
            "src_seek_ts": src_seek_ts,
            "out_rel_t": round(out_rel_t, 3),
            "is_climax_teaser": is_climax_teaser,
            "flash_alpha": flash_alpha,
            "eff_zoom": eff_zoom,
        }

    def build_export_filtergraph(
        self,
        *,
        caption_input_idx: Optional[int] = None,
        broll_specs: Optional[List[Dict[str, Any]]] = None,
        use_single_segment_fallback: bool = False,
    ) -> Dict[str, Any]:
        """
        Build full FFmpeg export inputs + filtergraph for `ffmpeg_runner.py`.
        When `use_single_segment_fallback=True`, collapses timeline to a single safe `[start, end]` window
        with reframer + captions + safe audio as an automatic recovery fallback.
        """
        if use_single_segment_fallback:
            s0 = max(0.0, float(self.spec.start_time))
            e0 = min(self.media_ceiling, s0 + max(0.5, float(self.spec.duration)))
            if e0 - s0 < 0.25:
                s0 = max(0.0, self.media_ceiling - 0.5)
                e0 = max(s0 + 0.25, self.media_ceiling)
            segs = [(round(s0, 3), round(e0, 3))]
            is_teaser_flags = [False]
        else:
            segs = list(self.input_segments)
            is_teaser_flags = list(self.segment_is_teaser)

        tl = self.timeline_plan
        stages: List[str] = []
        v_seg_labels: List[str] = []
        a_seg_labels: List[str] = []

        # Early 4K downscale & per-segment PTS reset BEFORE reframer / xfade so every buffer stays <= 1080p
        for idx in range(len(segs)):
            in_v = f"[{idx}:v]setpts=PTS-STARTPTS"
            if self.early_downscale_filter:
                in_v += f",{self.early_downscale_filter}"
            # Consistent CFR & timebase across segments prior to xfade/concat
            if len(segs) > 1:
                in_v += f",fps={self.target_fps:.3f},settb=1/90000"
            # Apply Cold-Open white flash on teaser segment
            if is_teaser_flags[idx] and tl.flash_duration > 0.0:
                fd = min(0.25, max(0.05, float(tl.flash_duration)))
                in_v += f",drawbox=x=0:y=0:w=iw:h=ih:color=white@0.85:t=fill:enable='between(t,0.000,{fd:.3f})'"
            v_lbl = f"tv{idx}"
            stages.append(f"{in_v}[{v_lbl}]")
            v_seg_labels.append(v_lbl)

            if self.has_audio:
                a_lbl = f"ta{idx}"
                stages.append(
                    f"[{idx}:a]asetpts=PTS-STARTPTS,aresample=async=1:first_pts=0,"
                    f"aformat=sample_rates=48000:channel_layouts=stereo[{a_lbl}]"
                )
                a_seg_labels.append(a_lbl)

        # Join segments cleanly (xfade + acrossfade when multiple)
        if len(segs) == 1:
            stages.append(f"[{v_seg_labels[0]}]null[v_tl]")
            v_tl = "v_tl"
            a_tl = a_seg_labels[0] if self.has_audio else None
        else:
            cf = max(0.0, min(0.08, float(tl.crossfade_sec or 0.03)))
            if cf > 0.005:
                curr_v = v_seg_labels[0]
                curr_a = a_seg_labels[0] if self.has_audio else None
                running_dur = max(0.15, segs[0][1] - segs[0][0])
                for idx in range(1, len(segs)):
                    seg_dur = max(0.15, segs[idx][1] - segs[idx][0])
                    step_cf = min(cf, running_dur * 0.20, seg_dur * 0.20)
                    offset = max(0.02, running_dur - step_cf)
                    next_v = "v_tl" if idx == len(segs) - 1 else f"vx{idx}"
                    stages.append(
                        f"[{curr_v}][{v_seg_labels[idx]}]xfade=transition=fade:duration={step_cf:.3f}:offset={offset:.3f}[{next_v}]"
                    )
                    curr_v = next_v
                    if self.has_audio and curr_a:
                        next_a = "a_tl" if idx == len(segs) - 1 else f"ax{idx}"
                        stages.append(
                            f"[{curr_a}][{a_seg_labels[idx]}]acrossfade=d={step_cf:.3f}:c1=qsin:c2=qsin[{next_a}]"
                        )
                        curr_a = next_a
                    running_dur = offset + seg_dur
                v_tl = "v_tl"
                a_tl = "a_tl" if self.has_audio else None
            else:
                if self.has_audio:
                    pairs = "".join(f"[{v_seg_labels[i]}][{a_seg_labels[i]}]" for i in range(len(segs)))
                    stages.append(f"{pairs}concat=n={len(segs)}:v=1:a=1[v_tl][a_tl]")
                    a_tl = "a_tl"
                else:
                    vs = "".join(f"[{v_seg_labels[i]}]" for i in range(len(segs)))
                    stages.append(f"{vs}concat=n={len(segs)}:v=1:a=0[v_tl]")
                    a_tl = None
                v_tl = "v_tl"

        # Framing Template Filter (100% shared reframer module)
        rf_filter = build_multi_aspect_filter(
            aspect_ratio=self.spec.aspect_ratio,
            frame_layout=self.spec.framing_template,
            framing_template=self.spec.framing_template,
            source_w=self.eff_source_w,
            source_h=self.eff_source_h,
            target_size=(self.out_w, self.out_h),
            webcam_corner=self.spec.webcam_corner,
            webcam_split_ratio=self.spec.webcam_split_ratio,
            pip_border_color=self.spec.pip_border_color,
            zoom=self.spec.zoom,
            pan_x_offset=self.spec.pan_x,
            face_track_data=self.spec.face_track_data,
            is_video_render=True,
            preview_rel_t=0.0,
            input_label=v_tl,
            camera_motion=self.spec.camera_motion,
            whip_pan_cuts=self.spec.whip_pan_cuts,
            clip_duration=self.effective_duration,
            fps=self.target_fps,
            beat_timestamps=self.spec.beat_timestamps,
            source_corner=self.spec.source_corner,
            caption_y=self.caption_anchor_y,
            safe_zone=self.spec.safe_zone,
        )
        rf_body = rf_filter[:-3] if rf_filter.endswith("[v]") else rf_filter
        stages.append(f"{rf_body}[v_rf]")
        curr_v = "v_rf"

        # Punch zoom crop windows on output timeline if enabled (not in fallback)
        if not use_single_segment_fallback and self.spec.punch_zoom and tl.punch_intervals:
            enable_terms = "+".join(f"between(t,{ps:.3f},{pe:.3f})" for ps, pe, _ in tl.punch_intervals)
            pz_fac = max(1.06, min(1.45, float(tl.punch_intervals[0][2] or self.spec.punch_zoom_intensity or 1.16)))
            cw_expr = f"if(gt({enable_terms},0),iw/{pz_fac:.3f},iw)"
            ch_expr = f"if(gt({enable_terms},0),ih/{pz_fac:.3f},ih)"
            stages.append(
                f"[{curr_v}]crop=w='{cw_expr}':h='{ch_expr}':x='(iw-ow)/2':y='(ih-oh)/2',"
                f"scale={self.out_w}:{self.out_h}:flags=lanczos[v_pz]"
            )
            curr_v = "v_pz"

        # Color Grade Look
        if self.color_filter:
            stages.append(f"[{curr_v}]{self.color_filter}[v_cg]")
            curr_v = "v_cg"

        # B-Roll PIP / Fullscreen cutaway inserts if passed
        if not use_single_segment_fallback and broll_specs:
            for bi, bsp in enumerate(broll_specs):
                b_idx = int(bsp["input_idx"])
                b_s = max(0.0, float(bsp.get("timeline_start", bsp.get("start", 0.0))))
                b_e = max(b_s + 0.25, float(bsp.get("timeline_end", bsp.get("end", b_s + 2.0))))
                b_pip = bool(bsp.get("pip", False))
                bw = even_dim(self.out_w * 0.36) if b_pip else self.out_w
                bh = even_dim(self.out_h * 0.22) if b_pip else self.out_h
                bx = f"W-w-28" if b_pip else "0"
                by = f"{compute_top_safe_zone_y(self.out_h, self.spec.safe_zone)}" if b_pip else "0"
                stages.append(
                    f"[{b_idx}:v]scale={bw}:{bh}:force_original_aspect_ratio=increase,"
                    f"crop={bw}:{bh},setpts=PTS-STARTPTS+{b_s:.3f}/TB[br_{bi}]"
                )
                stages.append(
                    f"[{curr_v}][br_{bi}]overlay=x={bx}:y={by}:eof_action=pass:enable='between(t,{b_s:.3f},{b_e:.3f})'[v_br_{bi}]"
                )
                curr_v = f"v_br_{bi}"

        # Burned-in RGBA Captions / Hook Banner / Climax Badge input
        if caption_input_idx is not None:
            stages.append(
                f"[{curr_v}][{caption_input_idx}:v]overlay=0:0:shortest=1:format=auto[v_cap]"
            )
            curr_v = "v_cap"

        # Watermark
        if self.watermark_filter:
            stages.append(f"[{curr_v}]{self.watermark_filter}[v_wm]")
            curr_v = "v_wm"

        # Strict mod-2 pixel format output guard
        pix_fmt_out = "yuv422p10le" if str(self.spec.codec_family or "").lower() == "prores" else "yuv420p"
        stages.append(f"[{curr_v}]scale=trunc(iw/2)*2:trunc(ih/2)*2,format={pix_fmt_out}[vout]")

        # Audio post-chain (profanity bleep etc.) — NO loudnorm when silent/missing audio!
        a_out_label = a_tl
        if self.has_audio and a_out_label and not use_single_segment_fallback:
            profanity_windows = _collect_profanity_intervals(self.remapped_words, self.spec.mute_words, self.spec.bleep_profanity)
            if profanity_windows:
                bleep_fg = build_profanity_bleep_filter(
                    in_audio_label=a_out_label,
                    profanity_intervals=profanity_windows,
                    total_duration=self.effective_duration,
                    out_audio_label="a_bleep",
                )
                if bleep_fg:
                    stages.append(bleep_fg)
                    a_out_label = "a_bleep"

        return {
            "input_segments": segs,
            "filter_complex": ";".join(stages),
            "v_out_label": "vout",
            "a_out_label": a_out_label,
            "has_audio": self.has_audio and bool(a_out_label),
            "effective_duration": self.effective_duration,
        }


def _collect_profanity_intervals(
    words: List[Dict[str, Any]],
    mute_words: List[str],
    bleep_profanity: bool,
) -> List[Tuple[float, float]]:
    targets = {str(w).strip().lower() for w in (mute_words or []) if str(w).strip()}
    if not targets and not bleep_profanity:
        return []
    default_profane = {"fuck", "shit", "bitch", "asshole", "damn", "hell"} if bleep_profanity else set()
    check_set = targets | default_profane
    intervals: List[Tuple[float, float]] = []
    for w in words or []:
        tok = str(w.get("word") or "").strip(".,!?;:\"'").lower()
        if w.get("muted") or (tok and tok in check_set):
            ws = max(0.0, float(w.get("start", 0.0)))
            we = max(ws + 0.08, float(w.get("end", ws + 0.25)))
            intervals.append((round(ws, 3), round(we, 3)))
    return intervals


def compile_clip_render_plan(
    *,
    master_video_path: str,
    clip_id: int = 0,
    project_id: int = 0,
    start_time: float = 0.0,
    duration: float = 5.0,
    words: Optional[List[Dict[str, Any]]] = None,
    aspect_ratio: Optional[str] = None,
    clip_size: Optional[str] = None,
    target_size: Optional[Tuple[int, int]] = None,
    custom_w: Optional[int] = None,
    custom_h: Optional[int] = None,
    frame_layout: Optional[str] = None,
    framing_template: Optional[str] = None,
    webcam_corner: Optional[str] = None,
    source_corner: Optional[str] = None,
    webcam_split_ratio: Optional[float] = None,
    pip_border_color: Optional[str] = None,
    zoom: Optional[float] = None,
    pan_x: Optional[float] = None,
    camera_motion: Optional[str] = None,
    whip_pan_cuts: Optional[bool] = None,
    safe_zone: Optional[str] = None,
    face_track_data: Optional[Dict[str, Any]] = None,
    beat_timestamps: Optional[List[float]] = None,
    frame_crop_override: Optional[Dict[str, Any]] = None,
    source_webcam_region: Optional[Dict[str, Any]] = None,
    use_cold_open: Optional[bool] = None,
    cold_open: Optional[bool] = None,
    cold_open_quote: Optional[str] = None,
    climax_badge_enabled: Optional[bool] = None,
    auto_trim_dead_air: Optional[bool] = None,
    trim_dead_air: Optional[bool] = None,
    tighten_pacing: Optional[bool] = None,
    silence_threshold: Optional[float] = None,
    clean_filler_words: Optional[bool] = None,
    punch_zoom: Optional[bool] = None,
    dynamic_punch_zoom: Optional[bool] = None,
    punch_zoom_intensity: Optional[float] = None,
    keep_segments: Optional[List[Tuple[float, float]]] = None,
    explicit_keep_segments: Optional[List[Tuple[float, float]]] = None,
    crossfade_sec: float = 0.03,
    color_preset: Optional[str] = None,
    color_grade: Optional[str] = None,
    preset_intensity: Optional[float] = None,
    depth_pop: Optional[float] = None,
    watermark_text: Optional[str] = None,
    watermark_opacity: Optional[float] = None,
    burn_captions: Optional[bool] = None,
    caption_style: Optional[str] = None,
    caption_x: Optional[float] = None,
    caption_y: Optional[float] = None,
    caption_custom_position: Optional[bool] = None,
    show_hook: Optional[bool] = None,
    show_hook_banner: Optional[bool] = None,
    hook_banner_enabled: Optional[bool] = None,
    hook_text: Optional[str] = None,
    hook_headline: Optional[str] = None,
    hardware_mode: Optional[str] = None,
    quality_mode: Optional[str] = None,
    codec_family: Optional[str] = None,
    quality_preset: Optional[str] = None,
    target_fps: Optional[float] = None,
    options: Optional[Dict[str, Any]] = None,
    **extra_kwargs: Any,
) -> ClipRenderPlan:
    """
    Single authoritative compiler producing a deterministic `ClipRenderPlan` shared identically
    by `preview_frame.py` (single-frame FFmpeg preview) and `renderer.py` (full MP4 export).
    """
    opts: Dict[str, Any] = dict(options or {})

    # Merge webcam/ROI overrides cleanly without mutating caller dicts
    ft_data: Dict[str, Any] = dict(face_track_data or opts.get("face_track_data") or {})
    eff_src_webcam = source_webcam_region if source_webcam_region is not None else opts.get("source_webcam_region")
    if isinstance(eff_src_webcam, dict) and eff_src_webcam:
        ft_data["webcam_detection"] = dict(eff_src_webcam)
    eff_crop_ovr = frame_crop_override if frame_crop_override is not None else opts.get("frame_crop_override")
    if isinstance(eff_crop_ovr, dict) and eff_crop_ovr:
        ft_data["frame_crop_override"] = dict(eff_crop_ovr)

    # Probe source media via stat-cached ffprobe (includes video_duration & audio_duration)
    meta = probe_video(master_video_path) if (master_video_path and os.path.exists(master_video_path)) else {
        "duration": max(1.0, float(duration or 5.0)),
        "video_duration": max(1.0, float(duration or 5.0)),
        "audio_duration": max(1.0, float(duration or 5.0)),
        "width": 1920,
        "height": 1080,
        "fps": 30.0,
        "has_audio": True,
    }
    src_w = even_dim(meta.get("width") or 1920)
    src_h = even_dim(meta.get("height") or 1080)
    has_audio = bool(meta.get("has_audio", False))

    cand_durs = [
        float(meta.get(k) or 0.0)
        for k in ("duration", "video_duration")
        if float(meta.get(k) or 0.0) > 0.25
    ]
    if has_audio and float(meta.get("audio_duration") or 0.0) > 0.25:
        cand_durs.append(float(meta["audio_duration"]))
    media_ceiling = max(0.2, round(min(cand_durs) - 0.05, 3)) if cand_durs else max(0.5, float(start_time or 0.0) + float(duration or 5.0))

    # Canonical geometry & output dimensions
    eff_ar = str(_pick_opt(aspect_ratio, opts, "aspect_ratio", default="9:16") or "9:16")
    eff_clip_size = str(_pick_opt(clip_size, opts, "clip_size", "export_resolution", default="1080p") or "1080p").lower()

    if target_size and len(target_size) >= 2:
        out_w, out_h = even_dim(target_size[0]), even_dim(target_size[1])
    else:
        base_w, base_h = resolve_target_dimensions(eff_ar, int(custom_w or 1080), int(custom_h or 1920))
        out_w, out_h = scale_dimensions_for_size(base_w, base_h, eff_clip_size)

    # Early 4K downscale rule: shrink >1080p input early before heavy reframing / blur filters
    is_hi_res_export = eff_clip_size in ("2k", "4k", "1440p", "2160p") or max(out_w, out_h) > 1920
    if src_w > 1920 and not is_hi_res_export:
        early_downscale_filter = "scale='min(1920,iw)':-2"
        eff_src_w = 1920
        eff_src_h = even_dim(src_h * (1920.0 / float(src_w)))
    else:
        early_downscale_filter = ""
        eff_src_w = src_w
        eff_src_h = src_h

    # Canonical framing & layout
    raw_tpl = _pick_opt(framing_template, opts, "framing_template") or _pick_opt(frame_layout, opts, "frame_layout", default="auto_track")
    eff_template = canonicalize_framing_template(raw_tpl)
    eff_corner = _canonicalize_corner(_pick_opt(webcam_corner, opts, "webcam_corner", default="top_left"), default="top_left")
    eff_src_corner = _canonicalize_corner(_pick_opt(source_corner, opts, "source_corner", default="auto"), default="auto")
    eff_split_ratio = max(0.15, min(0.75, float(_pick_opt(webcam_split_ratio, opts, "webcam_split_ratio", default=0.40))))
    eff_border_color = str(_pick_opt(pip_border_color, opts, "pip_border_color", default="#F59E0B") or "#F59E0B")
    eff_zoom = max(0.5, min(4.0, float(_pick_opt(zoom, opts, "zoom", default=1.0))))
    eff_pan_x = normalize_pan_ratio(_pick_opt(pan_x, opts, "pan_x", default=None), float(opts.get("pan_x_offset", 0.0) or 0.0))
    eff_cam_motion = str(_pick_opt(camera_motion, opts, "camera_motion", default="steady") or "steady")
    eff_whip = _to_bool(_pick_opt(whip_pan_cuts, opts, "whip_pan_cuts", default=False), False)
    eff_safe_zone = canonicalize_safe_zone(_pick_opt(safe_zone, opts, "safe_zone", default="none"))
    eff_beats = [float(b) for b in (_pick_opt(beat_timestamps, opts, "beat_timestamps", default=[]) or [])]

    # Timeline FX options
    eff_cold_open = _to_bool(
        cold_open if cold_open is not None else (use_cold_open if use_cold_open is not None else opts.get("cold_open", opts.get("use_cold_open"))),
        False,
    )
    eff_co_quote = str(_pick_opt(cold_open_quote, opts, "cold_open_quote", default="") or "")
    eff_climax_badge = _to_bool(_pick_opt(climax_badge_enabled, opts, "climax_badge_enabled", default=True), True)
    eff_trim_da = _to_bool(
        trim_dead_air if trim_dead_air is not None else (auto_trim_dead_air if auto_trim_dead_air is not None else opts.get("trim_dead_air", opts.get("auto_trim_dead_air"))),
        False,
    )
    eff_tighten = _to_bool(_pick_opt(tighten_pacing, opts, "tighten_pacing", default=False), False)
    eff_sil_thresh = float(_pick_opt(silence_threshold, opts, "silence_threshold", default=0.55) or 0.55)
    eff_clean_fillers = _to_bool(_pick_opt(clean_filler_words, opts, "clean_filler_words", default=False), False)
    eff_punch = _to_bool(
        punch_zoom if punch_zoom is not None else (dynamic_punch_zoom if dynamic_punch_zoom is not None else opts.get("punch_zoom", opts.get("dynamic_punch_zoom"))),
        False,
    )
    eff_punch_int = float(_pick_opt(punch_zoom_intensity, opts, "punch_zoom_intensity", default=1.16) or 1.16)
    raw_keeps = explicit_keep_segments if explicit_keep_segments is not None else (keep_segments if keep_segments is not None else opts.get("keep_segments"))
    eff_keeps: Optional[List[Tuple[float, float]]] = None
    if raw_keeps:
        eff_keeps = [(float(s[0]), float(s[1])) for s in raw_keeps if isinstance(s, (list, tuple)) and len(s) >= 2]

    # Color options
    raw_preset = color_grade if color_grade is not None else (color_preset if color_preset is not None else opts.get("color_grade", opts.get("color_preset", "none")))
    eff_color_preset = canonicalize_color_preset(raw_preset)
    eff_preset_int = float(_pick_opt(preset_intensity, opts, "preset_intensity", default=1.0) or 1.0)
    eff_depth_pop = float(_pick_opt(depth_pop, opts, "depth_pop", default=0.0) or 0.0)
    color_fields = (
        "exposure", "brightness", "contrast", "lift_mids", "highlights", "shadows",
        "warmth", "color_temp", "tint_shift", "vibrance", "matte_fade", "halation_glow",
        "lut_path", "chroma_split", "letterbox", "saturation", "black_and_white",
        "vintage", "tint_color", "tint_strength", "glow", "smooth", "sharpness",
        "blur", "film_grain", "vignette", "bottom_fade", "horizon_tilt",
    )
    c_kwargs: Dict[str, Any] = {}
    for cf in color_fields:
        val = extra_kwargs.get(cf, opts.get(cf))
        if val is not None:
            c_kwargs[cf] = val
    color_parts: List[str] = []
    adv_c = build_advanced_color_filter(
        preset=eff_color_preset,
        preset_intensity=eff_preset_int,
        target_w=out_w,
        target_h=out_h,
        **c_kwargs,
    )
    if adv_c:
        color_parts.append(adv_c)
    dp_f = build_depth_pop_foreground_filter(eff_depth_pop)
    if dp_f:
        color_parts.append(dp_f)
    eff_color_filter = ",".join(p for p in color_parts if p)

    # Watermark
    eff_wm_text = str(_pick_opt(watermark_text, opts, "watermark_text", default="") or "").strip()
    eff_wm_opacity = float(_pick_opt(watermark_opacity, opts, "watermark_opacity", default=0.45) or 0.45)
    eff_wm_filter = _build_watermark_filter(eff_wm_text, opacity=eff_wm_opacity)

    # Captions & Top Hook Banner strict boolean guard
    eff_burn_captions = _to_bool(_pick_opt(burn_captions, opts, "burn_captions", default=True), True)
    eff_caption_style = str(_pick_opt(caption_style, opts, "caption_style", default="viral_pop") or "viral_pop")
    eff_cap_x = float(_pick_opt(caption_x, opts, "caption_x", "pos_x", default=0.5) or 0.5)
    eff_cap_y = float(_pick_opt(caption_y, opts, "caption_y", "pos_y", default=0.78) or 0.78)
    eff_cap_custom = _to_bool(_pick_opt(caption_custom_position, opts, "caption_custom_position", default=False), False)

    eff_show_hook, eff_hook_text = resolve_hook_banner_state(
        show_hook=show_hook,
        show_hook_banner=show_hook_banner,
        hook_banner_enabled=hook_banner_enabled,
        hook_text=hook_text,
        hook_headline=hook_headline,
        options=opts,
    )
    eff_hook_text = eff_hook_text if eff_show_hook is True else ""

    # Hardware / Codec / FPS
    eff_hw = str(_pick_opt(hardware_mode, opts, "hardware_mode", default="gpu") or "gpu").lower()
    eff_qmode = str(_pick_opt(quality_mode, opts, "quality_mode", default="final_hq") or "final_hq").lower()
    eff_codec_fam = str(_pick_opt(codec_family, opts, "codec_family", default="h264") or "h264").lower()
    eff_qpreset = str(_pick_opt(quality_preset, opts, "quality_preset", default="social_balanced") or "social_balanced").lower()
    raw_fps = float(_pick_opt(target_fps, opts, "target_fps", default=0.0) or 0.0)
    src_fps = float(meta.get("fps") or 30.0)
    eff_fps = max(15.0, min(60.0, raw_fps if raw_fps > 0 else (30.0 if eff_qmode == "fast_preview" else min(60.0, max(24.0, src_fps)))))

    # Clamp anchor & span safely within source media bounds
    safe_start = round(max(0.0, min(float(start_time or 0.0), max(0.0, media_ceiling - 0.2))), 3)
    safe_dur = round(max(0.2, min(float(duration or 5.0), max(0.2, media_ceiling - safe_start))), 3)

    src_words = [dict(w) for w in (words or []) if isinstance(w, dict)]
    tl_plan = build_timeline_edit_plan(
        video_path=master_video_path,
        clip_start_time=safe_start,
        clip_duration=safe_dur,
        words=src_words,
        cold_open=eff_cold_open,
        cold_open_quote=eff_co_quote,
        trim_dead_air=eff_trim_da,
        tighten_pacing=eff_tighten,
        silence_threshold=eff_sil_thresh,
        clean_filler_words=eff_clean_fillers,
        punch_zoom=eff_punch,
        punch_zoom_intensity=eff_punch_int,
        explicit_keep_segments=eff_keeps,
        crossfade_sec=crossfade_sec,
    )

    # Assemble sanitized FFmpeg input segments: teaser first (if active) + main kept segments
    seg_pairs: List[Tuple[float, float]] = []
    teaser_flags: List[bool] = []
    if tl_plan.cold_open_active and tl_plan.climax_window is not None:
        cs, ce = tl_plan.climax_window
        cs = max(0.0, float(cs))
        ce = min(media_ceiling, float(ce))
        if ce - cs >= 0.5:
            seg_pairs.append((round(cs, 3), round(ce, 3)))
            teaser_flags.append(True)

    main_segs = sanitize_timeline_intervals(
        list(tl_plan.kept_segments_abs or [(safe_start, round(safe_start + safe_dur, 3))]),
        min_bound=0.0,
        max_bound=media_ceiling,
        min_slice_sec=0.12,
        fallback_interval=(safe_start, round(min(media_ceiling, safe_start + safe_dur), 3)),
    )
    for ks, ke in main_segs:
        seg_pairs.append((ks, ke))
        teaser_flags.append(False)

    if not seg_pairs:
        seg_pairs = [(safe_start, round(max(safe_start + 0.2, min(media_ceiling, safe_start + safe_dur)), 3))]
        teaser_flags = [False]

    eff_dur = round(max(0.2, float(tl_plan.effective_duration or safe_dur)), 3)
    remapped_words = []
    for w in (tl_plan.remapped_words or []):
        nw = dict(w)
        ws = max(0.0, min(eff_dur - 0.02, float(nw.get("start", 0.0))))
        we = max(ws + 0.02, min(eff_dur, float(nw.get("end", ws + 0.1))))
        nw["start"] = round(ws, 3)
        nw["end"] = round(we, 3)
        remapped_words.append(nw)

    audio_master_preset = str(_pick_opt(extra_kwargs.get("audio_mastering_preset"), opts, "audio_mastering_preset", default="none") or "none")
    eff_de_ess = _to_bool(_pick_opt(extra_kwargs.get("de_ess"), opts, "de_ess", default=False), False)
    eff_mute_words = list(_pick_opt(extra_kwargs.get("mute_words"), opts, "mute_words", default=[]) or [])
    eff_bleep = _to_bool(_pick_opt(extra_kwargs.get("bleep_profanity"), opts, "bleep_profanity", default=False), False)
    eff_bgm_mood = str(_pick_opt(extra_kwargs.get("bgm_mood"), opts, "bgm_mood", default="none") or "none")
    eff_bgm_vol = float(_pick_opt(extra_kwargs.get("bgm_volume"), opts, "bgm_volume", default=0.15) or 0.15)
    eff_dub = str(_pick_opt(extra_kwargs.get("auto_dub_lang"), opts, "auto_dub_lang", default="") or "")

    spec = ClipRenderSpec(
        master_video_path=str(master_video_path or ""),
        clip_id=int(clip_id or 0),
        project_id=int(project_id or 0),
        start_time=safe_start,
        duration=safe_dur,
        aspect_ratio=eff_ar,
        clip_size=eff_clip_size,
        out_w=out_w,
        out_h=out_h,
        framing_template=eff_template,
        webcam_corner=eff_corner,
        source_corner=eff_src_corner,
        webcam_split_ratio=eff_split_ratio,
        pip_border_color=eff_border_color,
        zoom=eff_zoom,
        pan_x=eff_pan_x,
        camera_motion=eff_cam_motion,
        whip_pan_cuts=eff_whip,
        safe_zone=eff_safe_zone,
        face_track_data=ft_data,
        beat_timestamps=eff_beats,
        frame_crop_override=eff_crop_ovr if isinstance(eff_crop_ovr, dict) else None,
        cold_open=eff_cold_open,
        cold_open_quote=eff_co_quote,
        climax_badge_enabled=eff_climax_badge,
        trim_dead_air=eff_trim_da,
        tighten_pacing=eff_tighten,
        silence_threshold=eff_sil_thresh,
        clean_filler_words=eff_clean_fillers,
        punch_zoom=eff_punch,
        punch_zoom_intensity=eff_punch_int,
        explicit_keep_segments=eff_keeps,
        crossfade_sec=float(tl_plan.crossfade_sec or crossfade_sec),
        color_preset=eff_color_preset,
        preset_intensity=eff_preset_int,
        color_kwargs=c_kwargs,
        depth_pop=eff_depth_pop,
        watermark_text=eff_wm_text,
        watermark_opacity=eff_wm_opacity,
        burn_captions=eff_burn_captions,
        caption_style=eff_caption_style,
        caption_x=eff_cap_x,
        caption_y=eff_cap_y,
        caption_custom_position=eff_cap_custom,
        show_hook=bool(eff_show_hook is True),
        hook_text=eff_hook_text,
        hardware_mode=eff_hw,
        quality_mode=eff_qmode,
        codec_family=eff_codec_fam,
        quality_preset=eff_qpreset,
        target_fps=eff_fps,
        audio_mastering_preset=audio_master_preset,
        de_ess=eff_de_ess,
        mute_words=eff_mute_words,
        bleep_profanity=eff_bleep,
        bgm_mood=eff_bgm_mood,
        bgm_volume=eff_bgm_vol,
        auto_dub_lang=eff_dub,
        words=src_words,
        raw_options=opts,
    )

    reframer_video = build_multi_aspect_filter(
        aspect_ratio=eff_ar,
        frame_layout=eff_template,
        framing_template=eff_template,
        source_w=eff_src_w,
        source_h=eff_src_h,
        target_size=(out_w, out_h),
        webcam_corner=eff_corner,
        webcam_split_ratio=eff_split_ratio,
        pip_border_color=eff_border_color,
        zoom=eff_zoom,
        pan_x_offset=eff_pan_x,
        face_track_data=ft_data,
        is_video_render=True,
        preview_rel_t=0.0,
        input_label="v_tl",
        camera_motion=eff_cam_motion,
        whip_pan_cuts=eff_whip,
        clip_duration=eff_dur,
        fps=eff_fps,
        beat_timestamps=eff_beats,
        source_corner=eff_src_corner,
        caption_y=eff_cap_y,
        safe_zone=eff_safe_zone,
    )

    sig_payload = {
        "clip_id": spec.clip_id,
        "project_id": spec.project_id,
        "start_time": spec.start_time,
        "duration": spec.duration,
        "out_size": [out_w, out_h],
        "fps": round(eff_fps, 2),
        "framing_template": eff_template,
        "webcam_corner": eff_corner,
        "source_corner": eff_src_corner,
        "webcam_split_ratio": round(eff_split_ratio, 3),
        "pip_border_color": eff_border_color,
        "zoom": round(eff_zoom, 3),
        "pan_x": round(eff_pan_x, 3),
        "camera_motion": eff_cam_motion,
        "whip_pan_cuts": eff_whip,
        "safe_zone": eff_safe_zone,
        "frame_crop_override": eff_crop_ovr,
        "source_webcam_region": eff_src_webcam,
        "cold_open": eff_cold_open,
        "cold_open_quote": eff_co_quote,
        "trim_dead_air": eff_trim_da,
        "tighten_pacing": eff_tighten,
        "clean_filler_words": eff_clean_fillers,
        "punch_zoom": eff_punch,
        "punch_zoom_intensity": round(eff_punch_int, 3),
        "segments": seg_pairs,
        "color_preset": eff_color_preset,
        "preset_intensity": round(eff_preset_int, 3),
        "color_kwargs": c_kwargs,
        "depth_pop": round(eff_depth_pop, 3),
        "watermark_text": eff_wm_text,
        "watermark_opacity": round(eff_wm_opacity, 2),
        "burn_captions": eff_burn_captions,
        "caption_style": eff_caption_style,
        "caption_x": round(eff_cap_x, 3),
        "caption_y": round(eff_cap_y, 3),
        "caption_custom_position": eff_cap_custom,
        "show_hook": spec.show_hook,
        "hook_text": spec.hook_text,
        "hardware_mode": eff_hw,
        "quality_mode": eff_qmode,
        "codec_family": eff_codec_fam,
        "quality_preset": eff_qpreset,
        "audio": [audio_master_preset, eff_de_ess, eff_mute_words, eff_bleep, eff_bgm_mood, round(eff_bgm_vol, 2), eff_dub],
        "words": [(w.get("word"), round(float(w.get("start", 0.0)), 3), round(float(w.get("end", 0.0)), 3)) for w in remapped_words],
    }
    plan_hash = hashlib.sha256(
        json.dumps(sig_payload, sort_keys=True, separators=(",", ":"), default=str).encode("utf-8")
    ).hexdigest()[:24]

    return ClipRenderPlan(
        spec=spec,
        source_meta=meta,
        out_w=out_w,
        out_h=out_h,
        target_fps=eff_fps,
        eff_source_w=eff_src_w,
        eff_source_h=eff_src_h,
        early_downscale_filter=early_downscale_filter,
        media_ceiling=media_ceiling,
        has_audio=has_audio,
        timeline_plan=tl_plan,
        input_segments=seg_pairs,
        segment_is_teaser=teaser_flags,
        is_multi_segment=len(seg_pairs) > 1,
        effective_duration=eff_dur,
        remapped_words=remapped_words,
        reframer_filter_video=reframer_video,
        color_filter=eff_color_filter,
        watermark_filter=eff_wm_filter,
        show_hook=spec.show_hook,
        hook_text=spec.hook_text,
        caption_anchor_x=eff_cap_x,
        caption_anchor_y=eff_cap_y,
        render_plan_hash=plan_hash,
    )
