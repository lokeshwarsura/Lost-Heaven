import re
import os
import wave
import math
import logging
from typing import List, Dict, Any, Optional, Tuple
import numpy as np

logger = logging.getLogger(__name__)

# Heuristic weights for multi-signal scoring
HOOK_TRIGGERS = [
    r"\b(how to|why|what if|the secret|never|always|do this|stop|watch this|you won't believe|the truth|secret|shocking|mistake|reason)\b",
    r"\b(top \d+|[0-9]+ (ways|things|tips|habits|reasons|steps))\b",
    r"\?$",  # Question
    r"\!$",  # Exclamation
]

def load_audio_energy_profile(audio_wav_path: str, duration: float = 0.0, duration_sec: Optional[float] = None) -> Tuple[np.ndarray, float]:
    """
    Read 16kHz mono WAV audio and compute per-second RMS energy in decibels (dB),
    normalized against median loudness.
    Returns (per_second_normalized_energy_0_to_100, total_duration_seconds).
    """
    if not os.path.exists(audio_wav_path):
        logger.warning(f"Audio file '{audio_wav_path}' not found for acoustic profiling. Returning synthetic profile.")
        return np.ones(60, dtype=np.float32) * 50.0, 60.0

    try:
        with wave.open(audio_wav_path, "rb") as wf:
            n_channels = wf.getnchannels()
            sampwidth = wf.getsampwidth()
            framerate = wf.getframerate()
            n_frames = wf.getnframes()
            
            raw_data = wf.readframes(n_frames)
            total_duration = n_frames / float(framerate) if framerate > 0 else 0.0

        if sampwidth == 2:
            dtype = np.int16
            max_val = 32768.0
        elif sampwidth == 4:
            dtype = np.int32
            max_val = 2147483648.0
        else:
            dtype = np.uint8
            max_val = 255.0

        samples = np.frombuffer(raw_data, dtype=dtype).astype(np.float32)
        if n_channels > 1:
            samples = samples.reshape(-1, n_channels).mean(axis=1)

        samples = samples / max_val

        # Window into 1-second chunks
        chunk_size = framerate
        n_chunks = max(1, int(math.ceil(len(samples) / chunk_size)))
        rms_per_sec = []

        for i in range(n_chunks):
            chunk = samples[i * chunk_size : (i + 1) * chunk_size]
            if len(chunk) == 0:
                continue
            rms = np.sqrt(np.mean(chunk**2)) + 1e-6
            db = 20.0 * np.log10(rms)
            rms_per_sec.append(db)

        rms_arr = np.array(rms_per_sec, dtype=np.float32)
        median_db = float(np.median(rms_arr)) if len(rms_arr) > 0 else -30.0

        # Relative excitement boost: clamp boost between -10 dB and +25 dB -> map to 0-100
        relative_db = rms_arr - median_db
        normalized = np.clip((relative_db + 10.0) / 35.0, 0.0, 1.0) * 100.0
        return normalized, total_duration

    except Exception as e:
        logger.warning(f"Could not compute acoustic energy from '{audio_wav_path}': {e}")
        return np.ones(60, dtype=np.float32) * 50.0, 60.0

def segment_transcript_into_sentences(transcript_words: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """
    Group words into complete spoken sentences split by punctuation (. ? !)
    or silence gaps >= 0.65s.
    """
    if not transcript_words:
        return []

    sentences = []
    current_words: List[Dict[str, Any]] = []
    
    for i, w in enumerate(transcript_words):
        word_text = str(w.get("word", "")).strip()
        if not word_text:
            continue
            
        current_words.append(w)
        
        # Check boundary condition 1: terminal punctuation
        ends_with_terminal = bool(re.search(r"[\.\?\!\;]$", word_text))
        
        # Check boundary condition 2: silence gap to next word
        has_silence_gap = False
        if i + 1 < len(transcript_words):
            next_start = float(transcript_words[i + 1].get("start", 0.0))
            curr_end = float(w.get("end", 0.0))
            if next_start - curr_end >= 0.65:
                has_silence_gap = True

        if ends_with_terminal or has_silence_gap or len(current_words) >= 22:
            s_start = float(current_words[0].get("start", 0.0))
            s_end = float(current_words[-1].get("end", s_start + 1.0))
            sentence_text = " ".join(cw.get("word", "").strip() for cw in current_words)
            
            sentences.append({
                "start": round(s_start, 2),
                "end": round(s_end, 2),
                "duration": round(s_end - s_start, 2),
                "text": sentence_text,
                "words": current_words,
                "ends_with_terminal": ends_with_terminal
            })
            current_words = []

    if current_words:
        s_start = float(current_words[0].get("start", 0.0))
        s_end = float(current_words[-1].get("end", s_start + 1.0))
        sentence_text = " ".join(cw.get("word", "").strip() for cw in current_words)
        sentences.append({
            "start": round(s_start, 2),
            "end": round(s_end, 2),
            "duration": round(s_end - s_start, 2),
            "text": sentence_text,
            "words": current_words,
            "ends_with_terminal": bool(re.search(r"[\.\?\!]$", sentence_text))
        })

    return sentences

def resolve_target_duration_bounds(preset: str) -> Tuple[float, float, float]:
    """Returns (min_duration, target_center_duration, max_duration)."""
    p = (preset or "Auto").strip().lower()
    if "15" in p:
        return 15.0, 20.0, 28.0
    elif "30" in p and "58" not in p and "55" not in p:
        return 28.0, 32.0, 42.0
    elif "45" in p:
        return 38.0, 45.0, 52.0
    elif "60" in p:
        return 48.0, 56.0, 60.0
    elif "90" in p or "180" in p:
        return 65.0, 85.0, 110.0
    else:  # Auto (Default Shorts 30-55s)
        return 30.0, 42.0, 55.0

def score_hook(first_sentence_text: str, first_4s_energy: float, topic_focus: Optional[str] = None) -> int:
    """Calculate 0-100 hook score based on opening words, questions/exclamations, and energy."""
    score = 65.0
    text_lower = first_sentence_text.lower()

    # Trigger patterns
    for pat in HOOK_TRIGGERS:
        if re.search(pat, text_lower):
            score += 10.0
            break

    # Topic focus match (check opening hook and overall clip text)
    if topic_focus and topic_focus.strip():
        keywords = [k.strip().lower() for k in re.split(r"[\s,;]+", topic_focus) if len(k) > 2]
        if any(k in text_lower for k in keywords):
            score += 15.0

    # Initial energy boost
    if first_4s_energy > 70.0:
        score += 10.0
    elif first_4s_energy < 30.0:
        score -= 8.0

    return int(min(98, max(50, round(score))))

def score_retention(words: List[Dict[str, Any]], duration: float) -> Tuple[int, float, float]:
    """
    Calculate retention based on Words-Per-Minute (WPM) and dead-air silence.
    Returns (retention_score, wpm, total_silence_sec).
    """
    if duration <= 0:
        return 75, 140.0, 0.0

    word_count = len(words)
    wpm = (word_count / (duration / 60.0)) if duration > 0 else 140.0

    # Optimal Shorts pacing: 130 - 185 WPM
    if 130.0 <= wpm <= 185.0:
        pacing_score = 92.0
    elif 100.0 <= wpm < 130.0 or 185.0 < wpm <= 220.0:
        pacing_score = 82.0
    else:
        pacing_score = 68.0

    # Calculate dead air (silence gaps > 0.75s)
    total_silence = 0.0
    for i in range(len(words) - 1):
        gap = float(words[i + 1].get("start", 0.0)) - float(words[i].get("end", 0.0))
        if gap >= 0.75:
            total_silence += gap

    silence_ratio = total_silence / duration if duration > 0 else 0.0
    silence_penalty = min(25.0, silence_ratio * 70.0)

    final_score = int(min(98, max(55, round(pacing_score - silence_penalty))))
    return final_score, round(wpm, 1), round(total_silence, 2)

NARRATIVE_ARCS: Tuple[str, ...] = (
    "shock_reaction",
    "setup_to_laughter",
    "heated_debate",
    "gameplay_climax",
    "hook_explanation",
)

NARRATIVE_ARC_LABELS: Dict[str, str] = {
    "shock_reaction": "Shock & Reaction",
    "setup_to_laughter": "Setup to Laughter",
    "heated_debate": "Heated Debate",
    "gameplay_climax": "Gameplay / Action Climax",
    "hook_explanation": "Hook Explanation",
}

_FILLER_OPENERS = {"and", "so", "but", "well", "um", "uh", "like", "basically", "literally", "anyway"}


def classify_narrative_arc(
    mm_win: Optional[Dict[str, Any]],
    first_sentence_text: str,
    speaker_switch_rate: float = 0.0,
    wpm: float = 0.0,
) -> Dict[str, Any]:
    """
    Classify a candidate window into one of the 5 viral narrative arcs:
      shock_reaction | setup_to_laughter | heated_debate | gameplay_climax | hook_explanation
    """
    if not mm_win:
        txt_l = (first_sentence_text or "").lower()
        is_q = "?" in txt_l or any(w in txt_l for w in ("how ", "why ", "secret", "truth", "never", "mistake"))
        arc = "hook_explanation" if is_q else "shock_reaction"
        return {
            "narrative_arc": arc,
            "arc_label": NARRATIVE_ARC_LABELS[arc],
            "dominant_emotion": "surprise" if arc == "shock_reaction" else "neutral",
            "vocal_emotion": "excited_hype" if arc == "shock_reaction" else "calm_storytelling",
            "emotion_arousal": 62.0 if arc == "shock_reaction" else 48.0,
            "emotion_valence": 0.1,
            "acoustic_events": [],
        }

    facial = str(mm_win.get("dominant_facial_emotion") or "neutral")
    vocal = str(mm_win.get("dominant_vocal_emotion") or "calm_storytelling")
    delta_pk = float(mm_win.get("emotion_delta_peak", 0.0))
    peak_arousal = float(mm_win.get("peak_arousal", 45.0))
    avg_arousal = float(mm_win.get("avg_arousal", 40.0))
    valence = float(mm_win.get("avg_valence", 0.0))
    graphic_pk = float(mm_win.get("graphic_peak", 0.0))
    shot_scale = str(mm_win.get("shot_scale") or "talking_head")
    ac_events = list(mm_win.get("acoustic_events_in_window") or [])
    aud_s = float(mm_win.get("audio_score", 50.0))
    vis_s = float(mm_win.get("visual_score", 50.0))

    scores = {
        "shock_reaction": 0.0,
        "setup_to_laughter": 0.0,
        "heated_debate": 0.0,
        "gameplay_climax": 0.0,
        "hook_explanation": 0.0,
    }

    # 1. Shock reaction: sudden surprise/fear delta or dramatic pause + surge
    if facial in ("surprise", "fear"):
        scores["shock_reaction"] += 38.0
    if delta_pk >= 22.0:
        scores["shock_reaction"] += min(40.0, delta_pk)
    if "dramatic_pause" in ac_events:
        scores["shock_reaction"] += 26.0
    if vocal == "excited_hype" and peak_arousal >= 55.0:
        scores["shock_reaction"] += 16.0

    # 2. Setup to laughter: laughter burst / laughing vocal / positive joy payoff
    if "laughter_burst" in ac_events:
        scores["setup_to_laughter"] += 52.0
    if vocal == "laughing":
        scores["setup_to_laughter"] += 38.0
    if facial == "joy" and valence >= 0.20:
        scores["setup_to_laughter"] += 24.0

    # 3. Heated debate: rapid speaker turns, negative/urgent tension, fast WPM
    if speaker_switch_rate >= 1.8:
        scores["heated_debate"] += 42.0
    if shot_scale == "dual_speaker":
        scores["heated_debate"] += 28.0
    if facial in ("anger", "disgust"):
        scores["heated_debate"] += 34.0
    if vocal == "urgent_tense":
        scores["heated_debate"] += 30.0
    if wpm >= 155.0:
        scores["heated_debate"] += 12.0

    # 4. Gameplay / Action climax: wide shot, HUD/graphic popups, bass drop, crowd roar
    if shot_scale == "wide_gameplay":
        scores["gameplay_climax"] += 34.0
    if graphic_pk >= 35.0:
        scores["gameplay_climax"] += 26.0
    if "bass_music_drop" in ac_events or "crowd_cheering" in ac_events:
        scores["gameplay_climax"] += 40.0
    if aud_s >= 58.0 and vis_s >= 55.0 and facial == "neutral":
        scores["gameplay_climax"] += 20.0

    # 5. Hook explanation: question/secret opener + steady storytelling cadence
    txt_l = (first_sentence_text or "").lower()
    if any(re.search(pat, txt_l) for pat in HOOK_TRIGGERS):
        scores["hook_explanation"] += 34.0
    if vocal == "calm_storytelling" and 105.0 <= max(wpm, float(mm_win.get("wpm_avg", 0.0))) <= 195.0:
        scores["hook_explanation"] += 22.0
    if "?" in txt_l:
        scores["hook_explanation"] += 14.0

    arc = max(NARRATIVE_ARCS, key=lambda k: scores[k])
    dom_emo = facial if facial != "neutral" else (
        "surprise" if arc == "shock_reaction"
        else "joy" if arc == "setup_to_laughter"
        else "anger" if arc == "heated_debate"
        else ("excited" if aud_s >= 60.0 else "neutral")
    )
    return {
        "narrative_arc": arc,
        "arc_label": NARRATIVE_ARC_LABELS[arc],
        "dominant_emotion": dom_emo,
        "vocal_emotion": vocal,
        "emotion_arousal": round(peak_arousal if peak_arousal > avg_arousal + 12.0 else avg_arousal, 1),
        "emotion_valence": round(valence, 3),
        "acoustic_events": ac_events,
    }


def generate_clean_headline(
    sentence_text: str,
    *,
    arc: Optional[str] = None,
    emotion: Optional[str] = None,
) -> str:
    """Generate punchy 4-7 word top hook banner from sentence text, stripping weak filler openers."""
    clean = re.sub(r"[^\w\s]", "", sentence_text or "").strip()
    words = clean.split()
    while len(words) > 4 and words[0].lower() in _FILLER_OPENERS:
        words = words[1:]
    if words:
        if len(words) <= 6:
            return " ".join(words).upper()
        return " ".join(words[:6]).upper()

    arc_fallbacks = {
        "shock_reaction": "UNBELIEVABLE REACTION CAUGHT LIVE",
        "setup_to_laughter": "TRY NOT TO LAUGH MOMENT",
        "heated_debate": "THE ARGUMENT NOBODY EXPECTED",
        "gameplay_climax": "INSANE CLUTCH PLAY BREAKDOWN",
        "hook_explanation": "THE SECRET NOBODY TELLS YOU",
    }
    if arc and arc in arc_fallbacks:
        return arc_fallbacks[arc]
    if emotion and emotion != "neutral":
        return f"PEAK {emotion.upper()} MOMENT"
    return "THE CRITICAL MOMENT"


def _build_smart_packaging(
    *,
    headline: str,
    quote: str,
    arc_info: Dict[str, Any],
    hook_score: int,
    wpm: float,
    mm_tag: str,
    emo_score: int,
) -> Tuple[List[str], str, str, str]:
    """Generate arc-aware YouTube titles (<=60 chars), description, pinned comment, and rich rationale."""
    arc = arc_info["narrative_arc"]
    arc_lbl = arc_info["arc_label"]
    dom_emo = str(arc_info["dominant_emotion"]).title()
    voc_emo = str(arc_info["vocal_emotion"]).replace("_", " ").title()
    arousal = float(arc_info.get("emotion_arousal", 50.0))
    ac_evs = [str(e).replace("_", " ").title() for e in (arc_info.get("acoustic_events") or [])]

    ev_clause = f" Events: {', '.join(ac_evs)}." if ac_evs else ""
    wpm_txt = f"{wpm} WPM" if wpm > 0 else "High-energy"
    rationale = (
        f"Offline Multimodal AI ({arc_lbl}): {dom_emo} expression + {voc_emo} delivery "
        f"(arousal {arousal:.0f}/100, hook {hook_score}, {wpm_txt}).{ev_clause} "
        f"Audio+video engagement index {emo_score}/100.{mm_tag}"
    )

    short_h = headline[:42].strip()
    arc_titles = {
        "shock_reaction": [
            f"{short_h} #Shorts",
            f"Wait For That {dom_emo} Reaction... #Shorts",
            f"Nobody Saw This Twist Coming #Shorts",
        ],
        "setup_to_laughter": [
            f"{short_h} #Shorts",
            f"Pure Chaos Until Everyone Cracked Up #Shorts",
            f"Watch Until The Laughter Hits #Shorts",
        ],
        "heated_debate": [
            f"{short_h} #Shorts",
            f"Things Got Intense Fast... #Shorts",
            f"Who Was Right In This Debate? #Shorts",
        ],
        "gameplay_climax": [
            f"{short_h} #Shorts",
            f"Unreal Climax Sequence Caught Live #Shorts",
            f"Watch Every Frame Of This Play #Shorts",
        ],
        "hook_explanation": [
            f"{short_h} #Shorts",
            f"Wait For The Real Answer... #Shorts",
            f"The Insight Most People Miss #Shorts",
        ],
    }
    raw_titles = arc_titles.get(arc, [f"{short_h} #Shorts", f"Wait For It... {short_h.title()} #Shorts", "Unbelievable Moment #Shorts"])
    titles = [t[:60] for t in raw_titles]

    desc = (
        f"{headline}\n\n"
        f"\"{quote}\"\n\n"
        f"Detected locally via Lost Heaven Offline Multimodal AI ({arc_lbl} · {dom_emo}). #Shorts #Viral #Clips"
    )
    pinned_map = {
        "shock_reaction": "What was your exact reaction to this twist? Drop it below!",
        "setup_to_laughter": "Which second made you laugh the hardest? Comment the timestamp!",
        "heated_debate": "Who made the stronger point here — Side A or Side B?",
        "gameplay_climax": "Rate this clutch moment from 1 to 10!",
        "hook_explanation": "Did you already know this trick or was this new to you?",
    }
    pinned = pinned_map.get(arc, "What do you think about this moment? Drop your thoughts below!")
    return titles, desc, pinned, rationale


def extract_viral_clips_offline(
    audio_wav_path: str,
    transcript_words: List[Dict[str, Any]],
    duration_sec: float = 0.0,
    duration_preset: str = "Auto",
    clip_count: int = 5,
    topic_focus: Optional[str] = None,
    target_length: Optional[str] = None,
    num_clips: Optional[int] = None,
    duration: Optional[float] = None,
    formula_weights: Optional[Dict[str, Any]] = None,
    multimodal_context: Optional[Dict[str, Any]] = None,
) -> List[Dict[str, Any]]:
    """
    100% Offline Multimodal Viral Clip & Narrative Arc Engine.
    Uses zero cloud APIs or external network requests. Combines acoustic DSP bursts,
    facial/vocal emotion trajectories, acoustic events, and sentence boundary segmentation.
    """
    effective_preset = target_length or duration_preset or "Auto"
    effective_count = num_clips or clip_count or 5
    effective_duration = duration if duration is not None else duration_sec

    logger.info(f"Running 100% Offline Multimodal Clipper on '{audio_wav_path}' ({len(transcript_words)} words, preset={effective_preset})...")
    min_dur, target_center, max_dur = resolve_target_duration_bounds(effective_preset)

    energy_profile, total_wav_dur = load_audio_energy_profile(audio_wav_path)
    total_dur = effective_duration if effective_duration > 0 else total_wav_dur

    mm_summary = (multimodal_context or {}).get("summary") or {}
    switch_rate = float(mm_summary.get("speaker_switch_rate", 0.0))

    sentences = segment_transcript_into_sentences(transcript_words)
    candidates: List[Dict[str, Any]] = []

    # CASE A: Real spoken sentences exist
    if len(sentences) >= 2:
        for i in range(len(sentences)):
            start_time = sentences[i]["start"]
            current_words: List[Dict[str, Any]] = []

            for j in range(i, len(sentences)):
                end_time = sentences[j]["end"]
                dur = end_time - start_time
                current_words.extend(sentences[j]["words"])

                if dur < min_dur:
                    continue
                if dur > max_dur:
                    break

                s_idx = max(0, int(start_time))
                e_idx = min(len(energy_profile), int(end_time) + 1)
                window_energy = energy_profile[s_idx:e_idx] if s_idx < len(energy_profile) else np.array([50.0])
                avg_energy = float(np.mean(window_energy)) if len(window_energy) > 0 else 50.0
                peak_energy = float(np.max(window_energy)) if len(window_energy) > 0 else 60.0
                first_4s_energy = float(np.mean(window_energy[:4])) if len(window_energy) >= 4 else avg_energy

                emotional_score = int(min(98, max(50, round(0.6 * avg_energy + 0.4 * peak_energy))))
                hook_score = score_hook(sentences[i]["text"], first_4s_energy, topic_focus)
                retention_score, wpm, silence_sec = score_retention(current_words, dur)

                length_diff = abs(dur - target_center)
                length_penalty = min(15.0, (length_diff / target_center) * 20.0)
                ends_clean = sentences[j]["ends_with_terminal"]
                standalone_score = int(min(98, max(60, round(92.0 - length_penalty + (6.0 if ends_clean else -4.0)))))

                text_sem = round(0.55 * hook_score + 0.45 * standalone_score, 1)
                mm_tag = ""
                mm_win = None
                if multimodal_context:
                    from app.services.multimodal_fusion import score_window_from_fusion
                    mm_win = score_window_from_fusion(multimodal_context, start_time, end_time)
                    if mm_win:
                        audio_p = float(mm_win["audio_score"])
                        visual_p = float(mm_win["visual_score"])
                        arousal_pk = float(mm_win.get("peak_arousal", 45.0))
                        delta_pk = float(mm_win.get("emotion_delta_peak", 0.0))
                        ev_list = mm_win.get("acoustic_events_in_window") or []

                        fused_0_100 = 0.40 * text_sem + 0.30 * audio_p + 0.30 * visual_p
                        arc_bonus = min(10.0, (0.20 * max(0.0, delta_pk - 18.0)) + (4.5 * len(ev_list)))
                        emotional_score = int(min(99, max(50, round(0.42 * audio_p + 0.48 * visual_p + 0.10 * arousal_pk))))
                        virality_score = int(min(99, max(50, round(
                            0.62 * fused_0_100
                            + 0.38 * (0.35 * hook_score + 0.25 * retention_score + 0.20 * emotional_score + 0.20 * standalone_score)
                            + arc_bonus * 0.45
                        ))))
                        mm_tag = f" [MM: audio {audio_p:.0f}, visual {visual_p:.0f}, sharp {mm_win['sharpness_avg']:.0f}]"
                    else:
                        virality_score = int(round(0.35 * hook_score + 0.25 * retention_score + 0.20 * emotional_score + 0.20 * standalone_score))
                else:
                    virality_score = int(round(
                        0.35 * hook_score
                        + 0.25 * retention_score
                        + 0.20 * emotional_score
                        + 0.20 * standalone_score
                    ))

                if formula_weights and formula_weights.get("has_data"):
                    from app.services.analytics_ledger import apply_formula_boost_to_score
                    virality_score = apply_formula_boost_to_score(virality_score, dur, hook_score, formula_weights)

                arc_info = classify_narrative_arc(mm_win, sentences[i]["text"], switch_rate, wpm)
                best_sentence = max(sentences[i:j + 1], key=lambda s: len(s["text"]))
                cold_open_quote = best_sentence["text"]
                hook_headline = generate_clean_headline(
                    sentences[i]["text"],
                    arc=arc_info["narrative_arc"],
                    emotion=arc_info["dominant_emotion"],
                )

                candidates.append({
                    "start_time": round(start_time, 2),
                    "end_time": round(end_time, 2),
                    "duration": round(dur, 2),
                    "virality_score": virality_score,
                    "hook_score": hook_score,
                    "retention_score": retention_score,
                    "emotional_score": emotional_score,
                    "standalone_score": standalone_score,
                    "wpm": wpm,
                    "silence_sec": silence_sec,
                    "hook_headline": hook_headline,
                    "cold_open_quote": cold_open_quote,
                    "first_text": sentences[i]["text"],
                    "mm_tag": mm_tag,
                    "arc_info": arc_info,
                })

    # CASE B: Sparse / No Spoken Words (Music, gameplay, acoustic only)
    if not candidates:
        logger.info("Transcript words sparse or absent. Generating multimodal/acoustic-peak sliding candidates...")
        step = max(4.0, min_dur / 2.5)
        win_len = min(total_dur, max(min_dur if total_dur >= min_dur else total_dur, target_center))
        anchor_starts: List[float] = []
        if multimodal_context:
            for pk in multimodal_context.get("peak_moments") or []:
                pk_t = float(pk.get("t", 0.0))
                anchor_starts.append(round(max(0.0, min(max(0.0, total_dur - win_len), pk_t - win_len * 0.35)), 2))
        curr = 0.0
        while curr + min(win_len, total_dur) <= total_dur + 0.01:
            anchor_starts.append(round(curr, 2))
            curr += step
        # Dedup nearby start times (>= 2.5s apart)
        dedup_starts: List[float] = []
        for s_cand in sorted(anchor_starts):
            if not dedup_starts or (s_cand - dedup_starts[-1]) >= 2.5:
                dedup_starts.append(s_cand)

        for s_t in dedup_starts:
            e_t = min(total_dur, s_t + win_len)
            dur_w = max(0.8, e_t - s_t)
            if dur_w < min_dur and total_dur >= min_dur:
                continue

            s_idx = max(0, int(s_t))
            e_idx = min(len(energy_profile), int(e_t) + 1)
            window_energy = energy_profile[s_idx:e_idx] if s_idx < len(energy_profile) else np.array([50.0])
            avg_energy = float(np.mean(window_energy)) if len(window_energy) > 0 else 50.0
            peak_energy = float(np.max(window_energy)) if len(window_energy) > 0 else 60.0

            mm_tag = ""
            mm_win = None
            emotional_b = int(peak_energy)
            virality_score = int(min(95, max(60, round(0.5 * avg_energy + 0.5 * peak_energy))))
            if multimodal_context:
                from app.services.multimodal_fusion import score_window_from_fusion
                mm_win = score_window_from_fusion(multimodal_context, s_t, e_t)
                if mm_win:
                    ev_bonus = min(8.0, 4.0 * len(mm_win.get("acoustic_events_in_window") or []))
                    virality_score = int(min(98, max(55, round(0.48 * mm_win["audio_score"] + 0.52 * mm_win["visual_score"] + ev_bonus))))
                    emotional_b = int(min(98, max(50, round(0.45 * mm_win["visual_score"] + 0.35 * mm_win.get("peak_arousal", 50.0) + 0.20 * mm_win["audio_score"]))))
                    mm_tag = f" [MM: audio {mm_win['audio_score']:.0f}, visual {mm_win['visual_score']:.0f}]"
            if formula_weights and formula_weights.get("has_data"):
                from app.services.analytics_ledger import apply_formula_boost_to_score
                virality_score = apply_formula_boost_to_score(virality_score, dur_w, virality_score, formula_weights)

            arc_info = classify_narrative_arc(mm_win, "", switch_rate, 0.0)
            hook_headline = generate_clean_headline("", arc=arc_info["narrative_arc"], emotion=arc_info["dominant_emotion"])
            candidates.append({
                "start_time": round(s_t, 2),
                "end_time": round(e_t, 2),
                "duration": round(dur_w, 2),
                "virality_score": virality_score,
                "hook_score": virality_score,
                "retention_score": 85,
                "emotional_score": emotional_b,
                "standalone_score": 85,
                "wpm": 0.0,
                "silence_sec": 0.0,
                "hook_headline": hook_headline,
                "cold_open_quote": f"{arc_info['arc_label']} peak at {int(round(s_t))}s",
                "first_text": hook_headline,
                "mm_tag": mm_tag,
                "arc_info": arc_info,
            })

    # CASE C: Ultra-short videos shorter than preset target_center (< 15s)
    if not candidates and total_dur >= 0.8:
        s_t = float(sentences[0]["start"]) if sentences else 0.0
        e_t = min(total_dur, max(s_t + 0.8, float(sentences[-1]["end"]) if sentences else total_dur))
        dur = max(0.8, e_t - s_t)
        all_txt = " ".join(s["text"] for s in sentences) if sentences else "Key Highlight Moment"
        hook_sc = score_hook(sentences[0]["text"], float(np.max(energy_profile)) if len(energy_profile) else 65.0, topic_focus) if sentences else 78
        ret_sc, wpm_v, sil_v = score_retention(transcript_words, dur) if transcript_words else (82, 0.0, 0.0)
        emo_sc = 80
        mm_tag = ""
        mm_win = None
        v_score = int(round(0.45 * hook_sc + 0.30 * ret_sc + 0.25 * emo_sc))
        if multimodal_context:
            from app.services.multimodal_fusion import score_window_from_fusion
            mm_win = score_window_from_fusion(multimodal_context, s_t, e_t)
            if mm_win:
                emo_sc = int(min(98, max(50, round(0.45 * mm_win["audio_score"] + 0.55 * mm_win["visual_score"]))))
                v_score = int(min(98, max(55, round(0.40 * hook_sc + 0.30 * mm_win["audio_score"] + 0.30 * mm_win["visual_score"]))))
                mm_tag = f" [MM: audio {mm_win['audio_score']:.0f}, visual {mm_win['visual_score']:.0f}]"
        arc_info = classify_narrative_arc(mm_win, all_txt, switch_rate, wpm_v)
        candidates.append({
            "start_time": round(s_t, 2),
            "end_time": round(e_t, 2),
            "duration": round(dur, 2),
            "virality_score": v_score,
            "hook_score": hook_sc,
            "retention_score": ret_sc,
            "emotional_score": emo_sc,
            "standalone_score": 86,
            "wpm": wpm_v,
            "silence_sec": sil_v,
            "hook_headline": generate_clean_headline(all_txt, arc=arc_info["narrative_arc"], emotion=arc_info["dominant_emotion"]),
            "cold_open_quote": all_txt,
            "first_text": all_txt,
            "mm_tag": mm_tag,
            "arc_info": arc_info,
        })

    # 3. Non-Maximum Suppression (NMS) to eliminate overlaps
    candidates.sort(key=lambda x: x["virality_score"], reverse=True)
    selected: List[Dict[str, Any]] = []

    for cand in candidates:
        overlap = False
        for sel in selected:
            latest_start = max(cand["start_time"], sel["start_time"])
            earliest_end = min(cand["end_time"], sel["end_time"])
            if earliest_end > latest_start:
                overlap = True
                break
        if not overlap:
            selected.append(cand)
            if len(selected) >= effective_count:
                break

    if not selected and candidates:
        selected.append(candidates[0])

    # 4. Generate smart arc-aware YouTube packaging & format output
    results: List[Dict[str, Any]] = []
    for c in selected:
        h_text = c["hook_headline"]
        quote = c["cold_open_quote"]
        arc_info = c.get("arc_info") or classify_narrative_arc(None, c.get("first_text", ""), switch_rate, c.get("wpm", 0.0))
        titles, desc, pinned, rationale = _build_smart_packaging(
            headline=h_text,
            quote=quote,
            arc_info=arc_info,
            hook_score=c["hook_score"],
            wpm=c["wpm"],
            mm_tag=c.get("mm_tag", ""),
            emo_score=c["emotional_score"],
        )

        results.append({
            "start_time": c["start_time"],
            "end_time": c["end_time"],
            "duration": c["duration"],
            "virality_score": c["virality_score"],
            "hook_score": c["hook_score"],
            "retention_score": c["retention_score"],
            "emotional_score": c["emotional_score"],
            "standalone_score": c["standalone_score"],
            "viral_rationale": rationale,
            "hook_headline": h_text,
            "cold_open_quote": quote,
            "youtube_titles": titles,
            "youtube_description": desc,
            "pinned_comment": pinned,
            "narrative_arc": arc_info["narrative_arc"],
            "arc_label": arc_info["arc_label"],
            "dominant_emotion": arc_info["dominant_emotion"],
            "vocal_emotion": arc_info["vocal_emotion"],
            "emotion_arousal": arc_info["emotion_arousal"],
            "emotion_valence": arc_info["emotion_valence"],
            "acoustic_events": arc_info["acoustic_events"],
            "analysis_source": "offline",
        })

    results.sort(key=lambda x: x["virality_score"], reverse=True)
    logger.info(f"Offline multimodal clipper produced {len(results)} viral clip moments successfully.")
    return results[:effective_count]
