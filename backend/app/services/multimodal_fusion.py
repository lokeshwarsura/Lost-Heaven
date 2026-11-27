"""
Lost Heaven — Multimodal Fusion Scorer (`multimodal_fusion.py`).

Synchronizes:
  - Acoustic Prosody Timeline (`0.25s` bins from `audio_analyzer.py`)
  - Vision / Emotion / Motion Timeline (`video_analyzer.py`)
  - Transcript Semantics (`faster-whisper` word segments, hook triggers & punctuation cues)

Produces unified `multimodal_timeline` with:
  `combined_engagement_score = 0.40 * semantic_score + 0.30 * audio_prosody_score + 0.30 * visual_emotion_action_score`
"""

from __future__ import annotations

import math
import re
from typing import Any, Dict, List, Optional

_SEMANTIC_HOOK_RE = re.compile(
    r"\b(how|why|what|secret|truth|never|always|stop|wait|watch|mistake|reason|million|thousand|percent|insane|crazy)\b",
    flags=re.IGNORECASE,
)


def _score_semantic_window(words: List[Dict[str, Any]], t_center: float, duration: float) -> float:
    """Compute local 0..100 semantic density & hook inflection score around `t_center`."""
    if not words:
        return 42.0

    w_s = max(0.0, t_center - 1.5)
    w_e = min(max(duration, t_center + 1.5), t_center + 1.5)
    span = max(0.75, w_e - w_s)

    win_words = [
        w for w in words
        if float(w.get("end", 0.0)) >= w_s and float(w.get("start", 0.0)) <= w_e
    ]
    if not win_words:
        return 18.0

    wpm = len(win_words) * (60.0 / span)
    # Ideal Shorts speaking cadence 125..200 WPM
    cadence_score = min(1.0, wpm / 165.0) * 65.0

    text_joined = " ".join(str(w.get("word", "")).strip() for w in win_words)
    hook_hits = len(_SEMANTIC_HOOK_RE.findall(text_joined))
    punct_bonus = 18.0 if ("?" in text_joined or "!" in text_joined) else 0.0
    number_bonus = 10.0 if re.search(r"\d+", text_joined) else 0.0
    trigger_bonus = min(22.0, hook_hits * 11.0)

    return round(max(15.0, min(100.0, 15.0 + cadence_score + punct_bonus + trigger_bonus + number_bonus)), 1)


def fuse_multimodal_tracks(
    audio_analysis: Optional[Dict[str, Any]],
    video_analysis: Optional[Dict[str, Any]],
    word_segments: Optional[List[Dict[str, Any]]] = None,
    duration: Optional[float] = None,
    step_sec: float = 0.25,
) -> Dict[str, Any]:
    """
    Fuse `0.25s` Audio Prosody + Vision Frames + Transcript Words into a unified multimodal curve.
    """
    aud_summary = (audio_analysis or {}).get("summary") or {}
    vid_summary = (video_analysis or {}).get("summary") or {}
    aud_tl = (audio_analysis or {}).get("timeline") or []
    vid_frames = (video_analysis or {}).get("frames") or []
    scene_cuts = (video_analysis or {}).get("scene_cuts") or []
    black_intervals = (video_analysis or {}).get("black_intervals") or []

    aud_events = (audio_analysis or {}).get("acoustic_events") or aud_summary.get("acoustic_events") or []
    est_dur = float(
        duration
        or aud_summary.get("duration")
        or vid_summary.get("duration")
        or (max((float(x.get("t", 0.0)) for x in aud_tl), default=0.0) + step_sec)
        or (max((float(x.get("t", 0.0)) for x in vid_frames), default=0.0) + step_sec)
        or 5.0
    )
    est_dur = max(0.25, est_dur)
    step = max(0.10, float(step_sec or 0.25))
    n_bins = max(1, int(math.ceil(est_dur / step)))

    clean_words = [
        w for w in (word_segments or [])
        if str(w.get("word", "")).strip() and not w.get("deleted") and not w.get("cut_from_video")
    ]

    unified: List[Dict[str, Any]] = []
    facial_counts: Dict[str, int] = {}
    vocal_counts: Dict[str, int] = {}
    shot_scale_counts: Dict[str, int] = {}
    emotion_shifts: List[Dict[str, Any]] = []
    prev_dom_emotion: Optional[str] = None
    last_shift_t = -9.0

    for b in range(n_bins):
        t_val = round(b * step, 3)

        # Nearest audio bin
        if aud_tl:
            a_pt = min(aud_tl, key=lambda pt: abs(float(pt.get("t", 0.0)) - t_val))
            audio_score = float(a_pt.get("excitement_score", 50.0))
            rms_val = float(a_pt.get("rms", 0.0))
            lufs_m = float(a_pt.get("lufs_m", -24.0))
            wpm_val = float(a_pt.get("wpm", 0.0))
            vocal_emo = str(a_pt.get("vocal_emotion") or "calm_storytelling")
            f0_hz = float(a_pt.get("f0_hz", 0.0))
            pitch_semi = float(a_pt.get("pitch_semitone_delta", 0.0))
            spec_bright = float(a_pt.get("spectral_brightness", 0.0))
            hnr_db = float(a_pt.get("hnr_db", 0.0))
            vocal_arousal = float(a_pt.get("vocal_arousal", audio_score))
            vocal_valence = float(a_pt.get("vocal_valence", 0.0))
            ac_ev = a_pt.get("acoustic_event")
        else:
            audio_score, rms_val, lufs_m, wpm_val = 50.0, 0.4, -24.0, 0.0
            vocal_emo, f0_hz, pitch_semi, spec_bright, hnr_db = "calm_storytelling", 0.0, 0.0, 0.2, 10.0
            vocal_arousal, vocal_valence, ac_ev = 45.0, 0.0, None

        if not ac_ev and aud_events:
            matched_ev = next(
                (ev for ev in aud_events if float(ev.get("t", 0.0)) <= t_val + 0.05 and t_val <= float(ev.get("end", 0.0)) + 0.05),
                None,
            )
            ac_ev = matched_ev.get("event_type") if matched_ev else None

        # Nearest visual frame
        if vid_frames:
            v_fr = min(vid_frames, key=lambda fr: abs(float(fr.get("t", 0.0)) - t_val))
            sharp = float(v_fr.get("sharpness_score", 60.0))
            exp = float(v_fr.get("exposure_score", 60.0))
            emo = float(v_fr.get("emotion_intensity", 0.0))
            act = float(v_fr.get("action_score", 0.0))
            fc = int(v_fr.get("face_count", 0))
            eyes = float(v_fr.get("eyes_open_confidence", 1.0))
            is_black = bool(v_fr.get("is_black", False))
            dom_facial = str(v_fr.get("dominant_emotion") or "neutral")
            facial_scores = v_fr.get("emotion_scores") or {"neutral": 1.0}
            facial_valence = float(v_fr.get("valence", 0.0))
            facial_arousal = float(v_fr.get("arousal", emo))
            emo_delta = float(v_fr.get("emotion_delta", 0.0))
            shot_scale = str(v_fr.get("shot_scale") or vid_summary.get("dominant_shot_scale") or "talking_head")
            graphic_score = float(v_fr.get("graphic_event_score", 0.0))
            if fc > 0:
                vis_raw = max(emo, 0.55 * emo + 0.45 * act)
            else:
                vis_raw = 0.72 * act + 0.28 * sharp
            if is_black or exp < 25.0:
                vis_raw *= 0.25
            elif sharp < 35.0:
                vis_raw *= 0.65
            visual_score = round(max(0.0, min(100.0, vis_raw)), 1)
        else:
            sharp, exp, emo, act, fc, eyes, is_black = 65.0, 65.0, 50.0, 40.0, 0, 1.0, False
            dom_facial, facial_scores, facial_valence, facial_arousal, emo_delta = "neutral", {"neutral": 1.0}, 0.0, 30.0, 0.0
            shot_scale, graphic_score = "talking_head", 0.0
            visual_score = 50.0

        facial_counts[dom_facial] = facial_counts.get(dom_facial, 0) + 1
        vocal_counts[vocal_emo] = vocal_counts.get(vocal_emo, 0) + 1
        shot_scale_counts[shot_scale] = shot_scale_counts.get(shot_scale, 0) + 1

        if fc > 0:
            fused_arousal = round(max(0.0, min(100.0, 0.52 * facial_arousal + 0.48 * vocal_arousal)), 1)
            fused_valence = round(max(-1.0, min(1.0, 0.58 * facial_valence + 0.42 * vocal_valence)), 3)
        else:
            fused_arousal = round(max(0.0, min(100.0, 0.75 * vocal_arousal + 0.25 * act)), 1)
            fused_valence = round(max(-1.0, min(1.0, vocal_valence)), 3)

        micro_spike = bool(emo_delta >= 26.0)
        if prev_dom_emotion is not None and (
            (dom_facial != prev_dom_emotion and dom_facial != "neutral" and fused_arousal >= 35.0)
            or emo_delta >= 26.0
        ):
            if (t_val - last_shift_t) >= 0.75:
                emotion_shifts.append({
                    "t": t_val,
                    "from_emotion": prev_dom_emotion,
                    "to_emotion": dom_facial,
                    "delta": round(emo_delta, 1),
                    "arousal": fused_arousal,
                    "vocal_emotion": vocal_emo,
                    "source": "facial_micro_expression" if emo_delta >= 26.0 else "emotion_transition",
                })
                last_shift_t = t_val
        prev_dom_emotion = dom_facial

        semantic_score = _score_semantic_window(clean_words, t_val + step * 0.5, est_dur)
        combined = round(
            max(0.0, min(100.0, 0.40 * semantic_score + 0.30 * audio_score + 0.30 * visual_score)),
            1,
        )

        unified.append({
            "t": t_val,
            "combined_engagement_score": combined,
            "semantic_score": semantic_score,
            "audio_prosody_score": round(audio_score, 1),
            "visual_emotion_action_score": visual_score,
            "rms": rms_val,
            "lufs_m": lufs_m,
            "wpm": wpm_val,
            "sharpness_score": sharp,
            "exposure_score": exp,
            "emotion_intensity": emo,
            "action_score": act,
            "face_count": fc,
            "eyes_open_confidence": eyes,
            "dominant_emotion": dom_facial,
            "emotion_scores": facial_scores,
            "facial_valence": facial_valence,
            "facial_arousal": facial_arousal,
            "emotion_delta": emo_delta,
            "micro_expression_spike": micro_spike,
            "shot_scale": shot_scale,
            "graphic_event_score": graphic_score,
            "vocal_emotion": vocal_emo,
            "f0_hz": f0_hz,
            "pitch_semitone_delta": pitch_semi,
            "spectral_brightness": spec_bright,
            "hnr_db": hnr_db,
            "vocal_arousal": vocal_arousal,
            "vocal_valence": vocal_valence,
            "fused_arousal": fused_arousal,
            "fused_valence": fused_valence,
            "acoustic_event": ac_ev,
            "is_cut": any(abs(float(sc.get("t", -9.0)) - t_val) <= step * 0.6 for sc in scene_cuts),
        })

    # Non-overlapping top multimodal peak moments (>= 3.0s gap)
    sorted_pts = sorted(unified, key=lambda pt: pt["combined_engagement_score"], reverse=True)
    peak_moments: List[Dict[str, Any]] = []
    for pt in sorted_pts:
        if all(abs(pt["t"] - prev["t"]) >= 3.0 for prev in peak_moments):
            reasons = []
            if pt["audio_prosody_score"] >= 60.0:
                reasons.append("Vocal/Audio Peak")
            if pt["emotion_intensity"] >= 45.0:
                reasons.append(f"Peak {pt['dominant_emotion'].title()}" if pt["dominant_emotion"] != "neutral" else "Peak Expression")
            if pt["action_score"] >= 45.0:
                reasons.append("High Motion")
            if pt["semantic_score"] >= 65.0:
                reasons.append("Hook Cadence")
            if pt.get("acoustic_event"):
                reasons.append(str(pt["acoustic_event"]).replace("_", " ").title())
            peak_moments.append({
                "t": pt["t"],
                "combined_score": pt["combined_engagement_score"],
                "audio_score": pt["audio_prosody_score"],
                "visual_score": pt["visual_emotion_action_score"],
                "semantic_score": pt["semantic_score"],
                "dominant_emotion": pt["dominant_emotion"],
                "vocal_emotion": pt["vocal_emotion"],
                "arousal": pt["fused_arousal"],
                "reason": " + ".join(reasons) or "Balanced Engagement Peak",
            })
            if len(peak_moments) >= 12:
                break
    peak_moments.sort(key=lambda item: item["t"])

    n_u = max(1, len(unified))
    overall_engagement = round(sum(pt["combined_engagement_score"] for pt in unified) / n_u, 1)
    avg_arousal = round(sum(pt["fused_arousal"] for pt in unified) / n_u, 1)
    peak_arousal = round(max((pt["fused_arousal"] for pt in unified), default=0.0), 1)
    avg_valence = round(sum(pt["fused_valence"] for pt in unified) / n_u, 3)

    non_neutral_facial = {k: v for k, v in facial_counts.items() if k != "neutral"}
    dominant_facial = max(non_neutral_facial, key=lambda k: non_neutral_facial[k]) if non_neutral_facial else max(facial_counts or {"neutral": 1}, key=lambda k: facial_counts[k])
    dominant_vocal = max(vocal_counts or {"calm_storytelling": 1}, key=lambda k: vocal_counts[k])
    facial_dist = {k: round(v / float(n_u), 4) for k, v in facial_counts.items()}
    vocal_dist = {k: round(v / float(n_u), 4) for k, v in vocal_counts.items()}
    shot_dist = {k: round(v / float(n_u), 4) for k, v in shot_scale_counts.items()}
    event_counts = aud_summary.get("acoustic_event_counts") or {}

    emotion_summary = {
        "dominant_facial_emotion": dominant_facial,
        "dominant_vocal_emotion": dominant_vocal,
        "facial_emotion_distribution": facial_dist,
        "vocal_emotion_distribution": vocal_dist,
        "avg_arousal": avg_arousal,
        "peak_arousal": peak_arousal,
        "avg_valence": avg_valence,
        "micro_expression_spike_count": sum(1 for pt in unified if pt.get("micro_expression_spike")),
        "emotion_shift_count": len(emotion_shifts),
        "acoustic_event_counts": event_counts,
        "shot_scale_distribution": shot_dist,
        "speaker_switch_rate": float(vid_summary.get("speaker_switch_rate", 0.0)),
    }

    return {
        "version": 2,
        "summary": {
            "duration": round(est_dur, 3),
            "step_sec": step,
            "overall_engagement_score": overall_engagement,
            "integrated_lufs": float(aud_summary.get("integrated_lufs", -24.0)),
            "lra": float(aud_summary.get("lra", 0.0)),
            "true_peak_db": float(aud_summary.get("true_peak_db", -6.0)),
            "avg_wpm": float(aud_summary.get("avg_wpm", 0.0)),
            "avg_sharpness": float(vid_summary.get("avg_sharpness", 0.0)),
            "avg_exposure": float(vid_summary.get("avg_exposure", 0.0)),
            "avg_action": float(vid_summary.get("avg_action", 0.0)),
            "face_presence_ratio": float(vid_summary.get("face_presence_ratio", 0.0)),
            "scene_cut_count": len(scene_cuts),
            "black_interval_count": len(black_intervals),
            "peak_excitement_timestamps": aud_summary.get("peak_excitement_timestamps") or [],
            "silence_intervals": aud_summary.get("silence_intervals") or [],
            "dominant_facial_emotion": dominant_facial,
            "dominant_vocal_emotion": dominant_vocal,
            "avg_arousal": avg_arousal,
            "peak_arousal": peak_arousal,
            "avg_valence": avg_valence,
            "speaker_switch_rate": float(vid_summary.get("speaker_switch_rate", 0.0)),
            "acoustic_event_counts": event_counts,
        },
        "emotion_summary": emotion_summary,
        "emotion_shifts": emotion_shifts,
        "acoustic_events": aud_events,
        "scene_cuts": scene_cuts,
        "black_intervals": black_intervals,
        "peak_moments": peak_moments,
        "multimodal_timeline": unified,
    }


def score_window_from_fusion(
    multimodal_context: Optional[Dict[str, Any]],
    start_t: float,
    end_t: float,
) -> Optional[Dict[str, Any]]:
    """Summarize audio, visual, emotion, acoustic events, and combined scores for `[start_t, end_t]`."""
    if not isinstance(multimodal_context, dict):
        return None
    tl = multimodal_context.get("multimodal_timeline") or []
    if not tl:
        return None
    pts = [pt for pt in tl if start_t - 0.15 <= float(pt.get("t", -1.0)) <= end_t + 0.15]
    if not pts:
        return None
    n = float(len(pts))
    avg_aud = sum(float(p.get("audio_prosody_score", 50.0)) for p in pts) / n
    peak_aud = max(float(p.get("audio_prosody_score", 50.0)) for p in pts)
    avg_vis = sum(float(p.get("visual_emotion_action_score", 50.0)) for p in pts) / n
    peak_vis = max(float(p.get("visual_emotion_action_score", 50.0)) for p in pts)
    avg_sharp = sum(float(p.get("sharpness_score", 60.0)) for p in pts) / n
    avg_comb = sum(float(p.get("combined_engagement_score", 50.0)) for p in pts) / n

    avg_arousal = sum(float(p.get("fused_arousal", 40.0)) for p in pts) / n
    peak_arousal = max(float(p.get("fused_arousal", 40.0)) for p in pts)
    avg_valence = sum(float(p.get("fused_valence", 0.0)) for p in pts) / n
    emo_delta_peak = max(float(p.get("emotion_delta", 0.0)) for p in pts)
    graphic_peak = max(float(p.get("graphic_event_score", 0.0)) for p in pts)
    wpm_avg = sum(float(p.get("wpm", 0.0)) for p in pts) / n

    f_counts: Dict[str, int] = {}
    v_counts: Dict[str, int] = {}
    s_counts: Dict[str, int] = {}
    for p in pts:
        fe = str(p.get("dominant_emotion") or "neutral")
        ve = str(p.get("vocal_emotion") or "calm_storytelling")
        sc = str(p.get("shot_scale") or "talking_head")
        f_counts[fe] = f_counts.get(fe, 0) + 1
        v_counts[ve] = v_counts.get(ve, 0) + 1
        s_counts[sc] = s_counts.get(sc, 0) + 1

    nn_facial = {k: v for k, v in f_counts.items() if k != "neutral"}
    dom_facial = max(nn_facial, key=lambda k: nn_facial[k]) if nn_facial else max(f_counts, key=lambda k: f_counts[k])
    dom_vocal = max(v_counts, key=lambda k: v_counts[k])
    dom_shot = max(s_counts, key=lambda k: s_counts[k])

    ev_types: List[str] = []
    for ev in multimodal_context.get("acoustic_events") or []:
        ev_t = float(ev.get("t", -1.0))
        ev_end = float(ev.get("end", ev_t))
        if ev_end >= start_t - 0.25 and ev_t <= end_t + 0.25:
            et = str(ev.get("event_type") or "")
            if et and et not in ev_types:
                ev_types.append(et)

    return {
        "audio_score": round(0.65 * avg_aud + 0.35 * peak_aud, 1),
        "visual_score": round(0.65 * avg_vis + 0.35 * peak_vis, 1),
        "sharpness_avg": round(avg_sharp, 1),
        "combined_score": round(avg_comb, 1),
        "avg_arousal": round(avg_arousal, 1),
        "peak_arousal": round(peak_arousal, 1),
        "avg_valence": round(avg_valence, 3),
        "dominant_facial_emotion": dom_facial,
        "dominant_vocal_emotion": dom_vocal,
        "emotion_delta_peak": round(emo_delta_peak, 1),
        "graphic_peak": round(graphic_peak, 1),
        "shot_scale": dom_shot,
        "wpm_avg": round(wpm_avg, 1),
        "acoustic_events_in_window": ev_types,
    }
