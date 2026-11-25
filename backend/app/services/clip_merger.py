import json
from typing import Any, Dict, List, Optional

from app.models import ViralClip


def build_merged_clip_model(
    source_clips: List[ViralClip],
    project_id: int,
    smooth_transition: bool = True,
    transition_duration: float = 0.35,
    custom_title: Optional[str] = None,
) -> ViralClip:
    """
    Combine 2+ ViralClip rows into a unified multi-segment ViralClip:
      - Flattens each source clip's kept segments sequentially
      - Remaps every source clip's transcript words onto the stitched timeline
      - Sets merge_crossfade_sec (0.35s crossfade when smooth_transition=True, else 0.03s)
    """
    if len(source_clips) < 2:
        raise ValueError("Select at least 2 clips to merge.")

    cf = max(0.0, min(0.6, float(transition_duration or 0.35))) if smooth_transition else 0.03

    merged_segments: List[List[float]] = []
    merged_words: List[Dict[str, Any]] = []
    out_cursor = 0.0
    segment_index = 0

    for c_idx, src in enumerate(source_clips):
        segs = src.get_keep_segments()
        src_words = src.get_transcript() or []

        # Detect whether src_words are master-absolute or clip-relative
        src_span = max(0.2, float(src.end_time) - float(src.start_time))
        max_w_end = max((float(w.get("end", 0.0)) for w in src_words), default=0.0)
        is_abs = src.start_time > 0.0 and (max_w_end > src_span + 1.0 or (src_words and min(float(w.get("start", 0.0)) for w in src_words) >= max(0.0, src.start_time - 0.5)))

        # Compute output start time of each sub-segment of this clip
        sub_out_starts: List[float] = []
        sub_spans: List[tuple[float, float]] = []
        for s, e in segs:
            if e <= s + 0.1:
                continue
            merged_segments.append([round(s, 3), round(e, 3)])
            sub_out_starts.append(out_cursor)
            sub_spans.append((s, e))
            seg_len = e - s
            # Overlap crossfade with subsequent segment (clamped for short segments)
            step_cf = min(cf, seg_len * 0.25)
            out_cursor += seg_len - step_cf
            segment_index += 1

        # Remap words falling into src's kept segments
        for w in src_words:
            if w.get("cut_from_video") or w.get("deleted"):
                continue
            ws = float(w.get("start", 0.0))
            we = float(w.get("end", ws + 0.2))
            if not is_abs:
                ws += float(src.start_time)
                we += float(src.start_time)
            mid = 0.5 * (ws + we)
            for idx_seg, (s, e) in enumerate(sub_spans):
                if s - 0.04 <= mid <= e + 0.04:
                    rel_s = max(0.0, ws - s)
                    rel_e = min(e - s, max(rel_s + 0.06, we - s))
                    nw = dict(w)
                    nw["start"] = round(sub_out_starts[idx_seg] + rel_s, 3)
                    nw["end"] = round(sub_out_starts[idx_seg] + rel_e, 3)
                    nw["timeline_relative"] = True
                    merged_words.append(nw)
                    break

    # Adjust out_cursor for final segment having no trailing overlap
    if merged_segments:
        last_len = merged_segments[-1][1] - merged_segments[-1][0]
        last_cf = min(cf, last_len * 0.25)
        out_cursor += last_cf

    merged_words.sort(key=lambda w: float(w.get("start", 0.0)))
    lead = source_clips[0]
    titles = [(c.hook_headline or c.selected_title or f"Clip {c.id}").strip() for c in source_clips]
    merged_title = (custom_title or "").strip() or " + ".join(titles[:3])
    if len(source_clips) > 3 and not custom_title:
        merged_title += f" (+{len(source_clips) - 3})"

    avg_score = lambda attr, fallback=80: int(
        round(sum(int(getattr(c, attr, fallback) or fallback) for c in source_clips) / len(source_clips))
    )

    merged_clip = ViralClip(
        project_id=project_id,
        start_time=merged_segments[0][0],
        end_time=max(seg[1] for seg in merged_segments),
        duration=round(max(0.5, out_cursor), 2),
        virality_score=min(100, max(avg_score("virality_score"), max(c.virality_score for c in source_clips))),
        hook_score=avg_score("hook_score"),
        retention_score=avg_score("retention_score"),
        emotional_score=avg_score("emotional_score"),
        standalone_score=avg_score("standalone_score"),
        viral_rationale="Merged highlights: " + " -> ".join(titles),
        hook_headline=merged_title[:70],
        show_hook_banner=lead.show_hook_banner,
        aspect_ratio=lead.aspect_ratio,
        custom_width=lead.custom_width,
        custom_height=lead.custom_height,
        frame_layout=lead.frame_layout,
        webcam_corner=lead.webcam_corner,
        webcam_split_ratio=lead.webcam_split_ratio,
        pip_border_color=lead.pip_border_color,
        zoom=lead.zoom,
        pan_x_offset=lead.pan_x_offset,
        burn_captions=lead.burn_captions,
        timing_offset=lead.timing_offset,
        caption_style=lead.caption_style,
        caption_font=lead.caption_font,
        caption_animation=lead.caption_animation,
        base_color=lead.base_color,
        active_color=lead.active_color,
        outline_color=lead.outline_color,
        font_size=lead.font_size,
        font_size_percent=lead.font_size_percent,
        bold=lead.bold,
        italic=lead.italic,
        all_caps=lead.all_caps,
        text_align=lead.text_align,
        position_grid=lead.position_grid,
        position_x=lead.position_x,
        position_y=lead.position_y,
        hold_back_alpha=lead.hold_back_alpha,
        highlight_shape=lead.highlight_shape,
        emoji_enabled=lead.emoji_enabled,
        highlight_power_words=bool(getattr(lead, "highlight_power_words", False)),
        how_phrase_arrives=lead.how_phrase_arrives,
        outline_width=lead.outline_width,
        shadow_depth=lead.shadow_depth,
        glow_intensity=lead.glow_intensity,
        letter_spacing=lead.letter_spacing,
        clip_size=lead.clip_size,
        color_preset=lead.color_preset,
        exposure=lead.exposure,
        brightness=lead.brightness,
        contrast=lead.contrast,
        lift_mids=lead.lift_mids,
        warmth=lead.warmth,
        saturation=lead.saturation,
        black_and_white=lead.black_and_white,
        vintage=lead.vintage,
        tint_color=lead.tint_color,
        tint_strength=lead.tint_strength,
        glow=lead.glow,
        smooth=lead.smooth,
        sharpness=lead.sharpness,
        blur=lead.blur,
        film_grain=lead.film_grain,
        vignette=lead.vignette,
        bottom_fade=lead.bottom_fade,
        watermark_path=lead.watermark_path,
        watermark_corner=lead.watermark_corner,
        watermark_opacity=lead.watermark_opacity,
        watermark_size=lead.watermark_size,
        bgm_path=lead.bgm_path,
        bgm_volume=lead.bgm_volume,
        audio_volume=getattr(lead, "audio_volume", 1.0),
        normalize_audio=getattr(lead, "normalize_audio", True),
        vocal_clarity=getattr(lead, "vocal_clarity", False),
        voice_isolation=getattr(lead, "voice_isolation", False),
        auto_sfx=getattr(lead, "auto_sfx", False),
        retention_bar_enabled=getattr(lead, "retention_bar_enabled", False),
        retention_bar_color=getattr(lead, "retention_bar_color", "#C6F32F"),
        outro_cta_enabled=getattr(lead, "outro_cta_enabled", False),
        outro_cta_text=getattr(lead, "outro_cta_text", "Subscribe for More"),
        hardware_mode=lead.hardware_mode or "gpu",
        selected_title=merged_title[:90],
        youtube_titles=json.dumps([merged_title[:90]]),
        youtube_description="Merged compilation created with Lost Heaven Studio.",
        pinned_comment="Which moment was your favorite? Comment below!",
        keep_segments_json=json.dumps(merged_segments),
        merge_crossfade_sec=round(cf, 3),
        transcript_json=json.dumps(merged_words),
        status="draft",
        render_status="idle",
    )
    return merged_clip
