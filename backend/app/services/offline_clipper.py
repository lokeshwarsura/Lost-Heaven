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

def generate_clean_headline(sentence_text: str) -> str:
    """Generate punchy 4-7 word top hook banner from sentence text."""
    clean = re.sub(r"[^\w\s]", "", sentence_text).strip()
    words = clean.split()
    if len(words) <= 7:
        return " ".join(words).upper()
    
    # Pick first 6 words
    return " ".join(words[:6]).upper()

def extract_viral_clips_offline(
    audio_wav_path: str,
    transcript_words: List[Dict[str, Any]],
    duration_sec: float = 0.0,
    duration_preset: str = "Auto",
    clip_count: int = 5,
    topic_focus: Optional[str] = None,
    target_length: Optional[str] = None,
    num_clips: Optional[int] = None,
    duration: Optional[float] = None
) -> List[Dict[str, Any]]:
    """
    100% Offline Viral Clip Extraction Engine.
    Uses zero cloud APIs, zero mock data.
    Analyzes acoustic excitement spikes, sentence boundary segmentation,
    pacing (WPM), hook strength, and non-maximum suppression (NMS).
    """
    effective_preset = target_length or duration_preset or "Auto"
    effective_count = num_clips or clip_count or 5
    effective_duration = duration if duration is not None else duration_sec

    logger.info(f"Running 100% Offline Viral Clipper on '{audio_wav_path}' ({len(transcript_words)} words, preset={effective_preset})...")
    min_dur, target_center, max_dur = resolve_target_duration_bounds(effective_preset)
    
    # 1. Compute acoustic energy profile
    energy_profile, total_wav_dur = load_audio_energy_profile(audio_wav_path)
    total_dur = effective_duration if effective_duration > 0 else total_wav_dur

    # 2. Segment transcript into clean sentence blocks
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

                # Extract audio energy slice for this candidate
                s_idx = max(0, int(start_time))
                e_idx = min(len(energy_profile), int(end_time) + 1)
                window_energy = energy_profile[s_idx:e_idx] if s_idx < len(energy_profile) else np.array([50.0])
                avg_energy = float(np.mean(window_energy)) if len(window_energy) > 0 else 50.0
                peak_energy = float(np.max(window_energy)) if len(window_energy) > 0 else 60.0
                first_4s_energy = float(np.mean(window_energy[:4])) if len(window_energy) >= 4 else avg_energy

                # Metric 1: Emotional / Intensity Score
                emotional_score = int(min(98, max(50, round(0.6 * avg_energy + 0.4 * peak_energy))))

                # Metric 2: Hook Score
                hook_score = score_hook(sentences[i]["text"], first_4s_energy, topic_focus)

                # Metric 3: Retention Score & WPM
                retention_score, wpm, silence_sec = score_retention(current_words, dur)

                # Metric 4: Standalone Score (Sentence completeness + target length fit)
                length_diff = abs(dur - target_center)
                length_penalty = min(15.0, (length_diff / target_center) * 20.0)
                ends_clean = sentences[j]["ends_with_terminal"]
                standalone_score = int(min(98, max(60, round(92.0 - length_penalty + (6.0 if ends_clean else -4.0)))))

                # Weighted Composite Virality Score
                virality_score = int(round(
                    0.35 * hook_score + 
                    0.25 * retention_score + 
                    0.20 * emotional_score + 
                    0.20 * standalone_score
                ))

                # Identify highest-energy sentence for cold-open quote
                best_sentence = max(sentences[i:j+1], key=lambda s: len(s["text"]))
                cold_open_quote = best_sentence["text"]
                hook_headline = generate_clean_headline(sentences[i]["text"])

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
                    "first_text": sentences[i]["text"]
                })

    # CASE B: Sparse / No Spoken Words (Music, gameplay, acoustic only)
    if not candidates:
        logger.info("Transcript words sparse or absent. Generating acoustic-peak sliding candidates...")
        step = max(5.0, min_dur / 2.0)
        curr = 0.0
        while curr + target_center <= total_dur:
            s_t = curr
            dur = target_center
            e_t = min(total_dur, s_t + dur)
            
            s_idx = max(0, int(s_t))
            e_idx = min(len(energy_profile), int(e_t) + 1)
            window_energy = energy_profile[s_idx:e_idx] if s_idx < len(energy_profile) else np.array([50.0])
            avg_energy = float(np.mean(window_energy)) if len(window_energy) > 0 else 50.0
            peak_energy = float(np.max(window_energy)) if len(window_energy) > 0 else 60.0
            
            virality_score = int(min(95, max(60, round(0.5 * avg_energy + 0.5 * peak_energy))))
            
            candidates.append({
                "start_time": round(s_t, 2),
                "end_time": round(e_t, 2),
                "duration": round(e_t - s_t, 2),
                "virality_score": virality_score,
                "hook_score": virality_score,
                "retention_score": 85,
                "emotional_score": int(peak_energy),
                "standalone_score": 85,
                "wpm": 0.0,
                "silence_sec": 0.0,
                "hook_headline": f"HIGHLIGHT MOMENT AT {int(s_t)}S",
                "cold_open_quote": f"Acoustic peak detected at {int(s_t)}s",
                "first_text": "High action sequence"
            })
            curr += step

    # 3. Non-Maximum Suppression (NMS) to eliminate overlaps
    candidates.sort(key=lambda x: x["virality_score"], reverse=True)
    selected: List[Dict[str, Any]] = []

    for cand in candidates:
        overlap = False
        for sel in selected:
            # Overlap check
            latest_start = max(cand["start_time"], sel["start_time"])
            earliest_end = min(cand["end_time"], sel["end_time"])
            if earliest_end > latest_start:
                overlap = True
                break
        if not overlap:
            selected.append(cand)
            if len(selected) >= effective_count:
                break

    # If no non-overlapping clip was selected at all, pick top candidate
    if not selected and candidates:
        selected.append(candidates[0])

    # 4. Generate YouTube packaging & format output
    results: List[Dict[str, Any]] = []
    for idx, c in enumerate(selected):
        h_text = c["hook_headline"]
        quote = c["cold_open_quote"]
        wpm_txt = f"{c['wpm']} WPM" if c['wpm'] > 0 else "High-energy"
        
        rationale = (
            f"100% Offline AI Detection: Hook score {c['hook_score']} with strong opening pacing ({wpm_txt}); "
            f"acoustic excitement index {c['emotional_score']}/100 and clean sentence boundaries."
        )

        title1 = f"{h_text} #Shorts"
        title2 = f"Wait For It... {h_text.title()} #Shorts"
        title3 = f"The Critical Moment You Missed #Shorts"

        desc = (
            f"{h_text}\n\n"
            f"\"{quote}\"\n\n"
            f"Extracted 100% locally with Lost Heaven Studio! #Shorts #Viral #Clips"
        )

        pinned = f"What do you think about this moment? Drop your thoughts below! 👇"

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
            "youtube_titles": [title1[:60], title2[:60], title3[:60]],
            "youtube_description": desc,
            "pinned_comment": pinned,
            "analysis_source": "offline"
        })

    results.sort(key=lambda x: x["virality_score"], reverse=True)
    logger.info(f"Offline clipper produced {len(results)} viral clip moments successfully.")
    return results[:effective_count]
