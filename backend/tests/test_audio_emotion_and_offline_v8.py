import math
import wave
from pathlib import Path

import numpy as np

from app.services.audio_analyzer import ACOUSTIC_EVENT_LABELS, VOCAL_EMOTIONS, analyze_audio_track
from app.services.gemini_clipper import build_gemini_prompt
from app.services.multimodal_fusion import fuse_multimodal_tracks, score_window_from_fusion
from app.services.offline_clipper import NARRATIVE_ARCS, classify_narrative_arc, extract_viral_clips_offline


def _save_wav(path: Path, samples: np.ndarray, sr: int = 16000) -> Path:
    pcm = np.clip(samples * 32767.0, -32767.0, 32767.0).astype(np.int16)
    path.parent.mkdir(parents=True, exist_ok=True)
    with wave.open(str(path), "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(sr)
        wf.writeframes(pcm.tobytes())
    return path


def test_audio_vocal_emotions_pitch_and_acoustic_events(tmp_path: Path):
    """
    Construct a 9.0s multi-stage 16kHz WAV track testing real DSP vocal emotion & event detection:
      0.0..2.0s: Calm warm harmonic voice (140 Hz + 280 Hz, steady conversational amplitude) -> calm_storytelling
      2.0..2.8s: Abrupt dramatic pause (near-silence right after active voice) -> dramatic_pause
      2.8..4.6s: High-pitch excited surge (285 Hz + 570 Hz + high amplitude) -> excited_hype
      4.6..6.6s: Pulsed 5.5 Hz modulated laughter syllables ("ha-ha-ha") -> laughing + laughter_burst
      6.6..7.2s: Brief quiet breath transition
      7.2..9.0s: Sub-bass music drop (60 Hz + 90 Hz heavy low-end impact) -> bass_music_drop
    """
    sr = 16000
    dur = 9.0
    t = np.linspace(0.0, dur, int(sr * dur), endpoint=False, dtype=np.float32)
    rng = np.random.default_rng(7)
    sig = rng.normal(0.0, 0.00015, size=len(t)).astype(np.float32)

    # Stage 1: Calm conversational voice (140 Hz fundamental + warm 2nd harmonic)
    m_calm = (t >= 0.0) & (t < 2.0)
    sig[m_calm] += (0.14 * np.sin(2 * math.pi * 140.0 * t[m_calm]) + 0.05 * np.sin(2 * math.pi * 280.0 * t[m_calm])).astype(np.float32)

    # Stage 2: 2.0..2.8s near-silence dramatic pause (already near 0)

    # Stage 3: Excited high-pitch surge (285 Hz + 570 Hz + bright harmonic)
    m_hype = (t >= 2.8) & (t < 4.6)
    sig[m_hype] += (0.55 * np.sin(2 * math.pi * 285.0 * t[m_hype]) + 0.25 * np.sin(2 * math.pi * 570.0 * t[m_hype])).astype(np.float32)

    # Stage 4: Rhythmic 5.5 Hz laughter bursts ("ha-ha-ha-ha")
    m_laugh = (t >= 4.6) & (t < 6.6)
    t_l = t[m_laugh] - 4.6
    pulse = np.maximum(0.0, np.sin(2 * math.pi * 5.5 * t_l)) ** 2
    carrier = 0.55 * np.sin(2 * math.pi * 310.0 * t_l) + 0.22 * rng.normal(0.0, 1.0, size=len(t_l))
    sig[m_laugh] += (pulse * carrier).astype(np.float32)

    # Stage 6: Sub-bass music drop (60 Hz + 95 Hz) after brief 6.6..7.2s dip
    m_bass = (t >= 7.2) & (t < 9.0)
    sig[m_bass] += (0.72 * np.sin(2 * math.pi * 62.0 * t[m_bass]) + 0.30 * np.sin(2 * math.pi * 95.0 * t[m_bass])).astype(np.float32)

    wav = _save_wav(tmp_path / "vocal_affect_test.wav", sig, sr=sr)
    words = [
        {"word": "Here", "start": 0.10, "end": 0.40},
        {"word": "is", "start": 0.42, "end": 0.65},
        {"word": "the", "start": 0.68, "end": 0.90},
        {"word": "calm", "start": 0.95, "end": 1.40},
        {"word": "setup.", "start": 1.42, "end": 1.92},
        {"word": "NO", "start": 2.85, "end": 3.20},
        {"word": "WAY!", "start": 3.22, "end": 3.80},
        {"word": "WHAT?!", "start": 3.85, "end": 4.45},
    ]

    res = analyze_audio_track(str(wav), word_segments=words)
    summary = res["summary"]
    tl = res["timeline"]
    ev_types = {e["event_type"] for e in res["acoustic_events"]}

    assert summary["dominant_vocal_emotion"] in VOCAL_EMOTIONS
    assert summary["avg_pitch_hz"] > 100.0
    assert set(VOCAL_EMOTIONS).issubset(summary["vocal_emotion_distribution"].keys())
    assert set(ACOUSTIC_EVENT_LABELS).issubset(summary["acoustic_event_counts"].keys())

    # Calm stage checks
    calm_bins = [pt for pt in tl if 0.50 <= pt["t"] <= 1.50]
    assert calm_bins and any(115.0 <= pt["f0_hz"] <= 175.0 for pt in calm_bins)
    assert any(pt["vocal_emotion"] in ("calm_storytelling", "deadpan") for pt in calm_bins)

    # Excited stage checks
    hype_bins = [pt for pt in tl if 3.0 <= pt["t"] <= 4.25]
    assert hype_bins and any(pt["vocal_emotion"] == "excited_hype" for pt in hype_bins)
    assert any(pt["f0_hz"] >= 230.0 for pt in hype_bins)

    # Laughter stage checks
    laugh_bins = [pt for pt in tl if 4.85 <= pt["t"] <= 6.25]
    assert laugh_bins and any(pt["vocal_emotion"] == "laughing" for pt in laugh_bins)

    # Acoustic event detections
    assert "laughter_burst" in ev_types, f"Expected laughter_burst in {res['acoustic_events']}"
    assert "dramatic_pause" in ev_types, f"Expected dramatic_pause in {res['acoustic_events']}"
    assert "bass_music_drop" in ev_types, f"Expected bass_music_drop in {res['acoustic_events']}"


def test_offline_multimodal_clipper_narrative_arcs_and_gemini_prompt(tmp_path: Path):
    """
    Verify zero-network offline narrative arc classification & peak clip ranking across all 5 arcs
    plus enrichment of `gemini_clipper.build_gemini_prompt` with affective telemetry.
    """
    sr = 16000
    dur = 12.0
    t = np.linspace(0.0, dur, int(sr * dur), endpoint=False, dtype=np.float32)
    sig = (0.08 * np.sin(2 * math.pi * 150.0 * t)).astype(np.float32)
    # Intense climax around 5..10s
    m_pk = (t >= 5.0) & (t <= 10.5)
    sig[m_pk] = (0.55 * np.sin(2 * math.pi * 270.0 * t[m_pk])).astype(np.float32)
    wav = _save_wav(tmp_path / "arc_test.wav", sig, sr=sr)

    # Construct fused multimodal timeline directly using real `fuse_multimodal_tracks`
    aud_tl = []
    vid_frames = []
    for i in range(48):  # 0.0..11.75s @ 0.25s
        ts = round(i * 0.25, 2)
        is_shock = 5.5 <= ts <= 8.5
        aud_tl.append({
            "t": ts,
            "rms": 0.72 if is_shock else 0.22,
            "lufs_m": -12.0 if is_shock else -26.0,
            "wpm": 180.0 if is_shock else 130.0,
            "excitement_score": 86.0 if is_shock else 28.0,
            "vocal_emotion": "excited_hype" if is_shock else "calm_storytelling",
            "f0_hz": 275.0 if is_shock else 140.0,
            "pitch_semitone_delta": 5.5 if is_shock else 0.0,
            "spectral_brightness": 0.45 if is_shock else 0.15,
            "hnr_db": 14.0,
            "vocal_arousal": 85.0 if is_shock else 30.0,
            "vocal_valence": 0.35 if is_shock else 0.10,
            "acoustic_event": "dramatic_pause" if 5.0 <= ts <= 5.25 else None,
        })
        vid_frames.append({
            "t": ts,
            "sharpness_score": 78.0,
            "exposure_score": 82.0,
            "emotion_intensity": 84.0 if is_shock else 18.0,
            "action_score": 52.0 if is_shock else 14.0,
            "face_count": 1,
            "eyes_open_confidence": 0.95,
            "dominant_emotion": "surprise" if is_shock else "neutral",
            "emotion_scores": {"surprise": 0.75, "neutral": 0.15} if is_shock else {"neutral": 0.88},
            "valence": 0.22 if is_shock else 0.0,
            "arousal": 82.0 if is_shock else 15.0,
            "emotion_delta": 42.0 if abs(ts - 5.5) < 0.2 else 4.0,
            "shot_scale": "closeup_reaction" if is_shock else "talking_head",
            "graphic_event_score": 0.0,
        })

    aud_mock = {
        "summary": {
            "duration": dur,
            "integrated_lufs": -17.0,
            "lra": 6.5,
            "true_peak_db": -1.2,
            "avg_wpm": 152.0,
            "peak_excitement_timestamps": [6.5],
            "silence_intervals": [[5.0, 5.45]],
            "acoustic_event_counts": {"dramatic_pause": 1},
            "acoustic_events": [{"t": 5.0, "end": 5.45, "event_type": "dramatic_pause", "confidence": 0.85, "label": "Dramatic Pause"}],
        },
        "acoustic_events": [{"t": 5.0, "end": 5.45, "event_type": "dramatic_pause", "confidence": 0.85, "label": "Dramatic Pause"}],
        "timeline": aud_tl,
    }
    vid_mock = {
        "summary": {
            "duration": dur,
            "avg_sharpness": 78.0,
            "avg_exposure": 82.0,
            "avg_action": 30.0,
            "face_presence_ratio": 1.0,
            "speaker_switch_rate": 0.0,
            "dominant_shot_scale": "closeup_reaction",
        },
        "frames": vid_frames,
        "scene_cuts": [],
        "black_intervals": [],
    }
    words = [
        {"word": "First", "start": 0.2, "end": 0.8},
        {"word": "normal", "start": 1.0, "end": 2.0},
        {"word": "part.", "start": 2.2, "end": 3.5},
        {"word": "Wait", "start": 5.5, "end": 6.2},
        {"word": "did", "start": 6.3, "end": 7.0},
        {"word": "that", "start": 7.1, "end": 8.0},
        {"word": "just", "start": 8.1, "end": 9.0},
        {"word": "happen?!", "start": 9.1, "end": 10.5},
    ]
    fused = fuse_multimodal_tracks(aud_mock, vid_mock, word_segments=words, duration=dur)
    assert fused["emotion_summary"]["dominant_facial_emotion"] == "surprise"
    assert fused["emotion_shifts"]

    clips = extract_viral_clips_offline(
        audio_wav_path=str(wav),
        transcript_words=words,
        duration_sec=dur,
        duration_preset="Auto",
        clip_count=2,
        multimodal_context=fused,
    )
    assert clips
    top = clips[0]
    assert top["narrative_arc"] in NARRATIVE_ARCS
    assert top["narrative_arc"] == "shock_reaction"
    assert top["dominant_emotion"] == "surprise"
    assert "Multimodal" in top["viral_rationale"]
    assert all(len(t) <= 60 for t in top["youtube_titles"])

    # Direct verification of remaining 4 viral narrative arcs
    assert classify_narrative_arc(
        {"dominant_facial_emotion": "joy", "dominant_vocal_emotion": "laughing", "acoustic_events_in_window": ["laughter_burst"], "avg_valence": 0.6},
        "And everyone started cracking up.",
    )["narrative_arc"] == "setup_to_laughter"

    assert classify_narrative_arc(
        {"dominant_facial_emotion": "anger", "dominant_vocal_emotion": "urgent_tense", "shot_scale": "dual_speaker", "acoustic_events_in_window": []},
        "That makes zero sense!",
        speaker_switch_rate=2.8,
        wpm=172.0,
    )["narrative_arc"] == "heated_debate"

    assert classify_narrative_arc(
        {"dominant_facial_emotion": "neutral", "dominant_vocal_emotion": "excited_hype", "shot_scale": "wide_gameplay", "graphic_peak": 55.0, "acoustic_events_in_window": ["bass_music_drop"], "audio_score": 82.0, "visual_score": 76.0},
        "Clutch play!",
    )["narrative_arc"] == "gameplay_climax"

    assert classify_narrative_arc(
        {"dominant_facial_emotion": "neutral", "dominant_vocal_emotion": "calm_storytelling", "shot_scale": "talking_head", "wpm_avg": 155.0, "acoustic_events_in_window": []},
        "Why does nobody talk about this secret?",
        wpm=155.0,
    )["narrative_arc"] == "hook_explanation"

    # Gemini prompt carries facial/vocal emotion telemetry & acoustic events
    prompt = build_gemini_prompt("30-58s", 3, multimodal_context=fused)
    assert "Affective Profile" in prompt
    assert "dramatic_pause" in prompt
    assert "Micro-Expression" in prompt
