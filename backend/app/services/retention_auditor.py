import math
import os
from typing import Any, Dict, List, Optional

from app.models import ViralClip
from app.services.face_tracker import track_faces_in_clip
from app.services.timeline_editor import compute_kept_segments_and_remapped_words, extract_waveform_bins


def audit_clip_retention(
    clip: ViralClip,
    audio_path: Optional[str] = None,
    face_track: Optional[Dict[str, Any]] = None,
    video_path: Optional[str] = None,
) -> Dict[str, Any]:
    """
    Analyze a clip second-by-second across 4 engagement pillars:
      1) Hook Velocity (0-3s): time-to-first-word & opening vocal energy
      2) Pacing & Dead-Air Gaps: stretches > 0.6s with zero speech and low RMS
      3) Visual Dynamics: static stretches > 5.0s without zoom/motion or off-center faces
      4) Information Density: sliding-window Words Per Minute (WPM) dips
    Returns {overall_score, retention_curve, drop_off_warnings, summary}.
    """
    keep_segs = clip.get_keep_segments()
    raw_words = clip.get_transcript() or []
    clip_dur = max(1.0, sum(max(0.1, e - s) for s, e in keep_segs))

    tl = compute_kept_segments_and_remapped_words(
        words=raw_words,
        clip_start_time=clip.start_time,
        clip_duration=max(clip_dur, float(clip.end_time) - float(clip.start_time)),
        tighten_pacing=False,
        clean_filler_words=False,
        explicit_keep_segments=[(s, e) for s, e in keep_segs],
        crossfade_sec=float(getattr(clip, "merge_crossfade_sec", 0.03) or 0.03),
    )
    eff_dur = max(1.0, float(tl.get("effective_duration") or clip_dur))
    words = [
        w for w in (tl.get("remapped_words") or [])
        if not w.get("deleted") and not w.get("cut_from_video") and str(w.get("word", "")).strip()
    ]
    n_secs = max(1, int(math.ceil(eff_dur)))

    # 1. Audio RMS profile (1 bin per second across active clip bounds)
    rms_per_sec: List[float] = [0.45] * n_secs
    media_probe = audio_path if (audio_path and os.path.exists(audio_path)) else video_path
    if media_probe and os.path.exists(media_probe):
        try:
            wf = extract_waveform_bins(
                video_or_wav_path=media_probe,
                clip_start=clip.start_time,
                clip_end=clip.end_time,
                num_bins=max(10, n_secs * 2),
                audio_wav_path=audio_path if (audio_path and os.path.exists(audio_path)) else None,
                transcript_words=words,
            )
            wf_rms = wf.get("rms") or wf.get("bins") or []
            if wf_rms:
                # Re-map window bins strictly over [clip.start_time, clip.end_time]
                w_s = float(wf.get("window_start", clip.start_time))
                w_e = max(w_s + 0.5, float(wf.get("window_end", clip.end_time)))
                w_span = w_e - w_s
                for sec in range(n_secs):
                    abs_t = float(clip.start_time) + min(eff_dur - 0.1, sec + 0.5)
                    b_idx = int(((abs_t - w_s) / w_span) * len(wf_rms))
                    b_idx = max(0, min(len(wf_rms) - 1, b_idx))
                    rms_per_sec[sec] = float(wf_rms[b_idx])
        except Exception:
            pass

    # 2. Face dynamics profile
    if face_track is None and video_path and os.path.exists(video_path):
        try:
            face_track = track_faces_in_clip(
                video_path=video_path,
                start_sec=clip.start_time,
                end_sec=clip.end_time,
                mode="single",
                sample_fps=2.0,
            )
        except Exception:
            face_track = None

    keyframes = (face_track or {}).get("keyframes") or []
    face_cx_per_sec: List[float] = []
    face_motion_per_sec: List[float] = []
    for sec in range(n_secs):
        if keyframes:
            closest = min(keyframes, key=lambda kf: abs(float(kf.get("t", 0.0)) - sec))
            cx = float(closest.get("cx", 0.5))
            prev_closest = min(keyframes, key=lambda kf: abs(float(kf.get("t", 0.0)) - max(0, sec - 1)))
            motion = abs(cx - float(prev_closest.get("cx", cx)))
        else:
            cx = 0.5
            motion = 0.0
        face_cx_per_sec.append(cx)
        face_motion_per_sec.append(motion)

    # 3. Analyze Hook Velocity (0-3s)
    warnings: List[Dict[str, Any]] = []
    ttfw = float(words[0]["start"]) if words else eff_dur
    open_rms = sum(rms_per_sec[:min(3, n_secs)]) / max(1, min(3, n_secs))
    if ttfw > 0.75 or (not words and open_rms < 0.25):
        trim_cut = round(max(0.5, min(eff_dur * 0.4, ttfw - 0.15)), 2) if words else round(min(1.5, eff_dur * 0.25), 2)
        seg0_s, seg0_e = keep_segs[0]
        if seg0_e - (seg0_s + trim_cut) >= 0.8:
            new_segs = [[round(seg0_s + trim_cut, 2), seg0_e], *keep_segs[1:]]
            warnings.append({
                "id": "slow_hook_intro",
                "timestamp": 0.0,
                "end_timestamp": trim_cut,
                "severity": "high",
                "category": "Hook Velocity",
                "issue": f"Slow opening: first spoken word starts at {ttfw:.1f}s (risk of 0-3s swipe-away).",
                "fix_label": f"Trim {trim_cut:.1f}s slow intro",
                "fix_action": {"type": "set_segments", "keep_segments": new_segs},
            })

    # 4. Pacing & Dead-Air Gaps (> 0.6s silence with low RMS)
    dead_air_gaps: List[tuple[float, float]] = []
    if words:
        for idx in range(len(words) - 1):
            gap_s = float(words[idx]["end"])
            gap_e = float(words[idx + 1]["start"])
            if gap_e - gap_s > 0.6:
                mid_sec = max(0, min(n_secs - 1, int(0.5 * (gap_s + gap_e))))
                if rms_per_sec[mid_sec] < 0.35:
                    dead_air_gaps.append((gap_s, gap_e))
    elif eff_dur > 2.0:
        # No transcript words: check sustained low-RMS bands
        run_s: Optional[int] = None
        for sec, val in enumerate(rms_per_sec):
            if val < 0.20:
                if run_s is None:
                    run_s = sec
            elif run_s is not None:
                if sec - run_s >= 1:
                    dead_air_gaps.append((float(run_s), float(sec)))
                run_s = None
        if run_s is not None and n_secs - run_s >= 1:
            dead_air_gaps.append((float(run_s), float(n_secs)))

    for g_idx, (gs, ge) in enumerate(dead_air_gaps[:3]):
        gap_len = round(ge - gs, 2)
        warnings.append({
            "id": f"dead_air_{g_idx}",
            "timestamp": round(gs, 2),
            "end_timestamp": round(ge, 2),
            "severity": "high" if gap_len >= 1.2 else "medium",
            "category": "Pacing & Dead Air",
            "issue": f"{gap_len:.1f}s dead-air pause at {int(gs // 60)}:{int(gs % 60):02d} (zero speech & low audio).",
            "fix_label": "Tighten dead air",
            "fix_action": {"type": "patch_clip", "updates": {"tighten_pacing": True, "silence_threshold": 0.45}},
        })

    # 5. Visual Dynamics (static > 5.0s or off-center face)
    static_run = 0
    offcenter_count = 0
    is_static_cfg = float(getattr(clip, "zoom", 1.0) or 1.0) <= 1.02 and not bool(getattr(clip, "dynamic_punch_zoom", False))
    for sec in range(n_secs):
        if abs(face_cx_per_sec[sec] - 0.5) > 0.22:
            offcenter_count += 1
        if is_static_cfg and face_motion_per_sec[sec] < 0.02:
            static_run += 1
            if static_run == 5:
                t_warn = float(max(0, sec - 4))
                mm, ss = int(t_warn // 60), int(t_warn % 60)
                warnings.append({
                    "id": f"static_visual_{sec}",
                    "timestamp": t_warn,
                    "end_timestamp": float(sec + 1),
                    "severity": "medium",
                    "category": "Visual Dynamics",
                    "issue": f"Static framing from {mm}:{ss:02d} for >5s with zero zoom movement.",
                    "fix_label": f"Enable Punch Zoom at {mm}:{ss:02d}",
                    "fix_action": {"type": "patch_clip", "updates": {"dynamic_punch_zoom": True, "punch_zoom_intensity": 1.14}},
                })
        else:
            static_run = 0

    if offcenter_count >= max(2, n_secs // 3) and getattr(clip, "frame_layout", "auto_track") != "auto_track":
        warnings.append({
            "id": "offcenter_face",
            "timestamp": 0.0,
            "end_timestamp": eff_dur,
            "severity": "medium",
            "category": "Visual Dynamics",
            "issue": "Speaker face sits off-center for >30% of clip without Auto-Track framing.",
            "fix_label": "Switch to Auto-Track Speaker",
            "fix_action": {"type": "patch_clip", "updates": {"frame_layout": "auto_track"}},
        })

    # 6. Per-second WPM & Retention Curve (0-100)
    retention_curve: List[Dict[str, Any]] = []
    wpm_dips: List[int] = []
    for sec in range(n_secs):
        w_start = max(0.0, sec - 1.5)
        w_end = min(eff_dur, sec + 1.5)
        win_span = max(0.5, w_end - w_start)
        win_words = [
            w for w in words
            if float(w["end"]) >= w_start and float(w["start"]) <= w_end
        ]
        wpm = (len(win_words) / win_span) * 60.0 if words else (120.0 * rms_per_sec[sec])

        in_gap = any(gs <= sec + 0.5 <= ge for gs, ge in dead_air_gaps)
        rms_val = rms_per_sec[sec]
        face_off = abs(face_cx_per_sec[sec] - 0.5)

        score = 84.0
        if sec < 3 and ttfw > 0.75:
            score -= 24.0
        if in_gap:
            score -= 26.0
        elif wpm < 75.0:
            score -= 14.0
            wpm_dips.append(sec)
        elif wpm > 130.0:
            score += 6.0
        if rms_val > 0.65:
            score += 7.0
        elif rms_val < 0.2:
            score -= 10.0
        if face_off > 0.24 and is_static_cfg:
            score -= 8.0

        clamped_score = int(max(18, min(100, round(score))))
        retention_curve.append({
            "sec": sec,
            "score": clamped_score,
            "wpm": round(wpm, 1),
            "rms": round(rms_val, 3),
            "face_offset": round(face_off, 3),
        })

    if wpm_dips and not dead_air_gaps and len(warnings) < 5:
        first_dip = float(wpm_dips[0])
        mm, ss = int(first_dip // 60), int(first_dip % 60)
        warnings.append({
            "id": "wpm_density_dip",
            "timestamp": first_dip,
            "end_timestamp": min(eff_dur, first_dip + 2.5),
            "severity": "low",
            "category": "Information Density",
            "issue": f"Slow verbal pacing (<75 WPM) around {mm}:{ss:02d}.",
            "fix_label": "Enable Auto-Punch Zoom + Remove Fillers",
            "fix_action": {"type": "patch_clip", "updates": {"clean_filler_words": True, "dynamic_punch_zoom": True}},
        })

    overall_score = int(round(sum(p["score"] for p in retention_curve) / max(1, len(retention_curve))))
    return {
        "clip_id": clip.id,
        "effective_duration": round(eff_dur, 2),
        "overall_score": overall_score,
        "time_to_first_word": round(ttfw, 2),
        "dead_air_count": len(dead_air_gaps),
        "retention_curve": retention_curve,
        "drop_off_warnings": warnings,
    }
