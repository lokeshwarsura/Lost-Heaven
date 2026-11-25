from typing import Any, Dict, List, Optional, Tuple

from app.models import ViralClip


def _pick_peak_window(clip: ViralClip, max_win_sec: float = 7.5, min_win_sec: float = 3.5) -> Tuple[float, float]:
    """Select the tightest high-energy sub-window (`keep_segments` or vocal opening) for trailer cuts."""
    segs = clip.get_keep_segments()
    s0, e0 = float(segs[0][0]), float(segs[0][1])
    span = e0 - s0
    if span <= max_win_sec:
        return round(s0, 3), round(e0, 3)

    # Prefer the window with the densest active words inside the clip's primary segment
    words = [
        w for w in (clip.get_transcript() or [])
        if isinstance(w, dict) and not w.get("deleted") and not w.get("cut_from_video")
    ]
    if words:
        min_w = min(float(w.get("start", 0.0)) for w in words)
        is_abs = float(clip.start_time) > 0.0 and min_w >= max(0.0, float(clip.start_time) - 0.5)
        abs_words = []
        for w in words:
            ws = float(w.get("start", 0.0)) + (0.0 if is_abs else float(clip.start_time))
            we = float(w.get("end", ws + 0.2)) + (0.0 if is_abs else float(clip.start_time))
            if ws >= s0 - 0.2 and we <= e0 + 0.2:
                abs_words.append((max(s0, ws), min(e0, we)))
        if abs_words:
            # Slide window from first word to keep opening punchy
            win_s = max(s0, abs_words[0][0] - 0.15)
            win_e = min(e0, win_s + max_win_sec)
            if win_e - win_s < min_win_sec:
                win_s = max(s0, win_e - min_win_sec)
            return round(win_s, 3), round(win_e, 3)

    return round(s0, 3), round(min(e0, s0 + max_win_sec), 3)


def compile_project_trailer(
    source_clips: List[ViralClip],
    project_id: int,
    max_moments: int = 4,
    target_duration_sec: float = 35.0,
    custom_title: Optional[str] = None,
) -> ViralClip:
    """
    Assemble an AI Viral Supercut Trailer combining the top 3-5 highest-scoring moments
    (peak opener -> escalating middle highlights -> climax) with smooth crossfades,
    auto micro-SFX riser/whoosh cues, and synchronized stitched captions.
    """
    if not source_clips:
        raise ValueError("Project needs at least 1 clip to compile a Supercut Trailer.")

    ranked = sorted(
        source_clips,
        key=lambda c: (int(getattr(c, "virality_score", 0) or 0), int(getattr(c, "hook_score", 0) or 0)),
        reverse=True,
    )

    desired_count = max(2, min(5, int(max_moments or 4))) if len(ranked) >= 2 else 1
    per_slot = max(2.2, min(8.5, float(target_duration_sec or 32.0) / float(desired_count)))

    chosen_clips: List[ViralClip] = []
    chosen_windows: List[Tuple[float, float]] = []

    def _overlaps(cand: Tuple[float, float]) -> bool:
        return any(min(cand[1], ws[1]) - max(cand[0], ws[0]) > 0.6 for ws in chosen_windows)

    for c in ranked:
        win = _pick_peak_window(c, max_win_sec=per_slot, min_win_sec=1.5)
        if not _overlaps(win):
            chosen_clips.append(c)
            chosen_windows.append(win)
        if len(chosen_clips) >= desired_count:
            break

    # Fallback if clips shared source ranges (e.g. short test clips): split first/other ranges cleanly
    if not chosen_clips:
        chosen_clips = [ranked[0]]
        chosen_windows = [_pick_peak_window(ranked[0], max_win_sec=per_slot, min_win_sec=1.2)]

    # Order: strongest hook first, middle escalations, #2 virality as final climax
    if len(chosen_clips) >= 3:
        order_idx = [0] + list(range(2, len(chosen_clips))) + [1]
        chosen_clips = [chosen_clips[i] for i in order_idx]
        chosen_windows = [chosen_windows[i] for i in order_idx]

    cf = 0.25
    trailer_segs: List[List[float]] = []
    remapped_words: List[Dict[str, Any]] = []
    out_cursor = 0.0

    for idx, (clip_item, (ws_seg, we_seg)) in enumerate(zip(chosen_clips, chosen_windows)):
        seg_len = max(0.4, we_seg - ws_seg)
        step_cf = min(cf, seg_len * 0.25) if idx < len(chosen_clips) - 1 else 0.0
        trailer_segs.append([round(ws_seg, 3), round(we_seg, 3)])

        raw_words = clip_item.get_transcript() or []
        if raw_words:
            max_w = max(float(w.get("end", 0.0)) for w in raw_words)
            min_w = min(float(w.get("start", 0.0)) for w in raw_words)
            src_dur = max(0.5, float(clip_item.end_time) - float(clip_item.start_time))
            is_abs = float(clip_item.start_time) > 0.0 and (
                max_w > src_dur + 1.0 or min_w >= max(0.0, float(clip_item.start_time) - 0.2)
            ) and not any(bool(w.get("timeline_relative")) for w in raw_words)

            for w in raw_words:
                if not isinstance(w, dict) or w.get("deleted") or w.get("cut_from_video"):
                    continue
                ws = float(w.get("start", 0.0)) + (0.0 if is_abs else float(clip_item.start_time))
                we = float(w.get("end", ws + 0.2)) + (0.0 if is_abs else float(clip_item.start_time))
                mid = 0.5 * (ws + we)
                if ws_seg - 0.05 <= mid <= we_seg + 0.05:
                    rel_s = max(0.0, ws - ws_seg)
                    rel_e = min(seg_len, max(rel_s + 0.06, we - ws_seg))
                    nw = dict(w)
                    nw["start"] = round(out_cursor + rel_s, 3)
                    nw["end"] = round(out_cursor + rel_e, 3)
                    nw["timeline_relative"] = True
                    remapped_words.append(nw)

        out_cursor += seg_len - step_cf

    remapped_words.sort(key=lambda w: float(w.get("start", 0.0)))
    lead = chosen_clips[0]
    headline = (custom_title or "").strip() or "SUPERCUT TRAILER: BEST MOMENTS"

    trailer = ViralClip(
        project_id=project_id,
        start_time=min(s[0] for s in trailer_segs),
        end_time=max(s[1] for s in trailer_segs),
        duration=round(max(0.5, out_cursor), 2),
        virality_score=min(100, max(int(c.virality_score or 85) for c in chosen_clips) + 2),
        hook_score=min(100, int(lead.hook_score or 90)),
        retention_score=94,
        emotional_score=92,
        standalone_score=90,
        viral_rationale=f"AI Supercut Trailer combining top {len(trailer_segs)} viral peaks.",
        hook_headline=headline[:68],
        show_hook_banner=True,
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
        font_size_percent=lead.font_size_percent,
        base_color=lead.base_color,
        active_color=lead.active_color,
        outline_color=lead.outline_color,
        hold_back_alpha=lead.hold_back_alpha,
        highlight_shape=lead.highlight_shape,
        bold=lead.bold,
        italic=lead.italic,
        all_caps=lead.all_caps,
        text_align=lead.text_align,
        position_grid=lead.position_grid,
        position_x=lead.position_x,
        position_y=lead.position_y,
        outline_width=lead.outline_width,
        shadow_depth=lead.shadow_depth,
        glow_intensity=lead.glow_intensity,
        letter_spacing=lead.letter_spacing,
        emoji_enabled=lead.emoji_enabled,
        highlight_power_words=bool(getattr(lead, "highlight_power_words", False)),
        how_phrase_arrives=lead.how_phrase_arrives,
        clean_filler_words=lead.clean_filler_words,
        censor_profanity=lead.censor_profanity,
        face_safe_captions=bool(getattr(lead, "face_safe_captions", True)),
        clip_size=lead.clip_size,
        color_preset=lead.color_preset,
        auto_sfx=True,
        dynamic_punch_zoom=True,
        punch_zoom_intensity=float(getattr(lead, "punch_zoom_intensity", 1.14) or 1.14),
        retention_bar_enabled=True,
        retention_bar_color=getattr(lead, "retention_bar_color", "#C6F32F") or "#C6F32F",
        hardware_mode=lead.hardware_mode or "gpu",
        selected_title=f"Supercut Trailer: {lead.selected_title or headline} #Shorts",
        youtube_description=lead.youtube_description,
        pinned_comment="Which moment hit hardest? Drop a timestamp below!",
        privacy_status=lead.privacy_status,
        status="draft",
        render_status="idle",
        analysis_source="supercut_trailer",
    )
    trailer.merge_crossfade_sec = cf
    trailer.keep_segments_json = __import__("json").dumps(trailer_segs)
    trailer.duration = round(max(0.5, out_cursor), 2)
    trailer.set_transcript(remapped_words)
    trailer.set_titles([
        f"Top {len(trailer_segs)} Moments Trailer #Shorts",
        f"Best Of {lead.selected_title or 'Highlights'} #Shorts",
        headline[:58],
    ])
    return trailer
