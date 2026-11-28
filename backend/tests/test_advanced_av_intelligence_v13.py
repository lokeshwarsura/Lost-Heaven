import subprocess
import time
import wave
from pathlib import Path
from typing import Dict, List

import cv2
import numpy as np
import pytest
from sqlmodel import SQLModel, Session, create_engine

import app.main as main_mod
from app.models import Project, ViralClip
from app.services.audio_analyzer import analyze_audio_track
from app.services.emotion_analyzer import compute_head_pose_and_eye_contact
from app.services.multimodal_fusion import evaluate_clip_obstruction_zones, fuse_multimodal_tracks
from app.services.video_analyzer import analyze_video_track


def _write_wav(out_path: Path, samples: np.ndarray, sr: int = 16000) -> Path:
    out_path.parent.mkdir(parents=True, exist_ok=True)
    pcm = np.clip(samples * 32767.0, -32767, 32767).astype(np.int16)
    with wave.open(str(out_path), "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(sr)
        wf.writeframes(pcm.tobytes())
    return out_path


def _synth_voice(t: np.ndarray, f0: float, bright: bool = False, amp: float = 0.25) -> np.ndarray:
    """Synthesize distinct vocal-like harmonic timbre (`Speaker A` warm low F0 vs `Speaker B` bright high F0)."""
    if bright:
        sig = (
            0.40 * np.sin(2.0 * np.pi * f0 * t)
            + 0.45 * np.sin(2.0 * np.pi * (f0 * 2.0) * t)
            + 0.55 * np.sin(2.0 * np.pi * (f0 * 3.0) * t)
            + 0.40 * np.sin(2.0 * np.pi * (f0 * 5.0) * t)
            + 0.28 * np.sin(2.0 * np.pi * (f0 * 7.0) * t)
        )
    else:
        sig = (
            0.90 * np.sin(2.0 * np.pi * f0 * t)
            + 0.35 * np.sin(2.0 * np.pi * (f0 * 2.0) * t)
            + 0.15 * np.sin(2.0 * np.pi * (f0 * 3.0) * t)
        )
    return (sig / max(1e-6, float(np.max(np.abs(sig))))) * amp


def test_phase1_speaker_diarization_dominance_and_crosstalk(tmp_path: Path):
    sr = 16000
    dur = 12.0
    n = int(sr * dur)
    t_arr = np.arange(n, dtype=np.float32) / float(sr)
    sig = np.zeros(n, dtype=np.float32)

    # 0.2..3.6s: Speaker A (115 Hz warm bassy voice)
    m_a1 = (t_arr >= 0.2) & (t_arr < 3.6)
    sig[m_a1] = _synth_voice(t_arr[m_a1], 115.0, bright=False, amp=0.35)

    # 4.0..7.2s: Speaker B (235 Hz bright tenor/soprano voice)
    m_b1 = (t_arr >= 4.0) & (t_arr < 7.2)
    sig[m_b1] = _synth_voice(t_arr[m_b1], 235.0, bright=True, amp=0.35)

    # 7.4..9.4s: Both Speaker A (118 Hz) and Speaker B (212 Hz) debating simultaneously -> Cross-Talk!
    m_ct = (t_arr >= 7.4) & (t_arr < 9.4)
    rng = np.random.default_rng(42)
    ct_mix = (
        _synth_voice(t_arr[m_ct], 118.0, bright=False, amp=0.32)
        + _synth_voice(t_arr[m_ct], 212.0, bright=True, amp=0.32)
        + rng.normal(0.0, 0.04, size=int(np.sum(m_ct))).astype(np.float32)
    )
    sig[m_ct] = ct_mix

    # 9.8..11.6s: Speaker A resumes
    m_a2 = (t_arr >= 9.8) & (t_arr < 11.6)
    sig[m_a2] = _synth_voice(t_arr[m_a2], 115.0, bright=False, amp=0.35)

    words: List[Dict] = [
        {"word": "First", "start": 0.3, "end": 0.8},
        {"word": "speaker", "start": 0.9, "end": 1.6},
        {"word": "talks", "start": 1.8, "end": 2.6},
        {"word": "clearly.", "start": 2.7, "end": 3.5},
        {"word": "Second", "start": 4.1, "end": 4.8},
        {"word": "speaker", "start": 4.9, "end": 5.7},
        {"word": "replies", "start": 5.8, "end": 6.5},
        {"word": "fast.", "start": 6.6, "end": 7.1},
        {"word": "Wait", "start": 7.5, "end": 8.0},
        {"word": "hold", "start": 8.2, "end": 8.7},
        {"word": "on!", "start": 8.8, "end": 9.3},
        {"word": "Closing", "start": 9.9, "end": 10.6},
        {"word": "point.", "start": 10.7, "end": 11.5},
    ]

    wav = _write_wav(tmp_path / "diar_crosstalk.wav", sig, sr=sr)
    res = analyze_audio_track(str(wav), word_segments=words)

    diar = res["speaker_diarization"]
    assert diar["speaker_count"] >= 2
    assert "Speaker A" in diar["dominance_ratios"]
    assert "Speaker B" in diar["dominance_ratios"]
    assert pytest.approx(sum(diar["dominance_ratios"].values()), abs=0.02) == 1.0

    # Solo Speaker A (0.5..3.0s) vs Solo Speaker B (4.5..6.8s) assigned to different IDs
    sp_a_bins = [pt["speaker_id"] for pt in res["timeline"] if 0.5 <= pt["t"] <= 3.0 and pt["speaker_id"]]
    sp_b_bins = [pt["speaker_id"] for pt in res["timeline"] if 4.5 <= pt["t"] <= 6.8 and pt["speaker_id"]]
    assert sp_a_bins and sp_b_bins
    assert sp_a_bins[0] != sp_b_bins[0]

    # Cross-talk window around 7.4..9.4s detected cleanly
    ct_list = diar["crosstalk_overlaps"]
    assert diar["crosstalk_count"] >= 1
    assert any(ov["t"] <= 9.2 and ov["end"] >= 7.5 for ov in ct_list), f"Expected crosstalk in 7.4..9.4, got {ct_list}"


def test_phase1_speech_fluency_fillers_and_clean_hook_candidates(tmp_path: Path):
    sr = 16000
    dur = 10.0
    n = int(sr * dur)
    t_arr = np.arange(n, dtype=np.float32) / float(sr)
    sig = np.zeros(n, dtype=np.float32)

    # Moderate halting speech 0..4s, strong energetic crisp delivery 5.0..9.2s
    m_hes = (t_arr >= 0.1) & (t_arr <= 4.0)
    sig[m_hes] = _synth_voice(t_arr[m_hes], 145.0, amp=0.18)
    m_hook = (t_arr >= 5.0) & (t_arr <= 9.2)
    sig[m_hook] = _synth_voice(t_arr[m_hook], 165.0, amp=0.45)

    words: List[Dict] = [
        # Hesitation + filler segment
        {"word": "Um", "start": 0.1, "end": 0.35, "probability": 0.55},
        {"word": "uh", "start": 0.85, "end": 1.1, "probability": 0.52},
        {"word": "like", "start": 1.75, "end": 2.0, "probability": 0.62},
        {"word": "you", "start": 2.45, "end": 2.65, "probability": 0.65},
        {"word": "know", "start": 2.7, "end": 2.95, "probability": 0.64},
        {"word": "basically", "start": 3.55, "end": 3.95, "probability": 0.60},
        # Clean punchy viral hook segment (zero fillers, high probability, steady fast gaps)
        {"word": "Stop", "start": 5.0, "end": 5.3, "probability": 0.97},
        {"word": "wasting", "start": 5.35, "end": 5.75, "probability": 0.96},
        {"word": "hours", "start": 5.8, "end": 6.15, "probability": 0.95},
        {"word": "editing", "start": 6.2, "end": 6.6, "probability": 0.96},
        {"word": "boring", "start": 6.65, "end": 7.0, "probability": 0.94},
        {"word": "intros", "start": 7.05, "end": 7.45, "probability": 0.95},
        {"word": "every", "start": 7.5, "end": 7.8, "probability": 0.97},
        {"word": "single", "start": 7.85, "end": 8.2, "probability": 0.96},
        {"word": "day!", "start": 8.25, "end": 8.65, "probability": 0.98},
    ]

    wav = _write_wav(tmp_path / "fluency.wav", sig, sr=sr)
    res = analyze_audio_track(str(wav), word_segments=words)

    fl = res["speech_fluency"]
    assert fl["filler_count"] >= 5
    hes_scores = [pt["fluency_score"] for pt in res["timeline"] if 0.5 <= pt["t"] <= 3.5]
    hook_scores = [pt["fluency_score"] for pt in res["timeline"] if 5.25 <= pt["t"] <= 8.25]
    assert sum(hes_scores) / len(hes_scores) < 58.0
    assert sum(hook_scores) / len(hook_scores) >= 84.0

    cands = fl["clean_hook_candidates"]
    assert len(cands) >= 1
    assert any(4.8 <= c["t"] <= 5.3 and c["fluency_score"] >= 82.0 for c in cands)
    assert all(c["t"] >= 4.0 for c in cands), f"Filler opening leaked into clean_hook_candidates: {cands}"


def test_phase1_music_bed_detection_and_metadata_persistence(tmp_path: Path):
    sr = 16000
    dur = 8.0
    n = int(sr * dur)
    t_arr = np.arange(n, dtype=np.float32) / float(sr)

    # Case A: Clean speech with pure silent pauses -> has_existing_music MUST be False
    clean_sig = np.zeros(n, dtype=np.float32)
    m_sp1 = (t_arr >= 0.4) & (t_arr <= 2.4)
    m_sp2 = (t_arr >= 5.0) & (t_arr <= 7.2)
    clean_sig[m_sp1] = _synth_voice(t_arr[m_sp1], 140.0, amp=0.30)
    clean_sig[m_sp2] = _synth_voice(t_arr[m_sp2], 145.0, amp=0.30)
    words = [
        {"word": "Solo", "start": 0.5, "end": 1.1},
        {"word": "voice", "start": 1.2, "end": 2.2},
        {"word": "after", "start": 5.1, "end": 5.8},
        {"word": "pause.", "start": 5.9, "end": 7.0},
    ]
    wav_clean = _write_wav(tmp_path / "clean_speech.wav", clean_sig, sr=sr)
    res_clean = analyze_audio_track(str(wav_clean), word_segments=words)
    assert res_clean["music_bed_analysis"]["has_existing_music"] is False

    # Case B: Rhythmic 120 BPM (2 Hz) 90 Hz bass beat + ambient chord bed during pauses & speech
    beat_env = 0.5 + 0.5 * np.maximum(0.0, np.sin(2.0 * np.pi * 2.0 * t_arr)) ** 2
    music_bed = (
        0.16 * beat_env * np.sin(2.0 * np.pi * 90.0 * t_arr)
        + 0.08 * np.sin(2.0 * np.pi * 261.6 * t_arr)
        + 0.06 * np.sin(2.0 * np.pi * 329.6 * t_arr)
    ).astype(np.float32)
    music_sig = clean_sig + music_bed
    wav_music = _write_wav(tmp_path / "with_music.wav", music_sig, sr=sr)
    res_music = analyze_audio_track(str(wav_music), word_segments=words)

    mb = res_music["music_bed_analysis"]
    assert mb["has_existing_music"] is True
    assert res_music["summary"]["has_existing_music"] is True
    assert mb["beat_periodicity"] > 0.15
    assert len(mb["music_intensity_timeline"]) == len(res_music["timeline"])

    # Verify multimodal fusion & clip serialization preserve has_existing_music
    fused = fuse_multimodal_tracks(res_music, None, word_segments=words, duration=dur)
    assert fused["summary"]["has_existing_music"] is True

    local_db = tmp_path / "phase1_music_meta.db"
    local_engine = create_engine(f"sqlite:///{local_db.as_posix()}", connect_args={"check_same_thread": False})
    SQLModel.metadata.create_all(local_engine)
    with Session(local_engine) as session:
        proj = Project(
            title="Music Bed Meta",
            source_path=str(wav_music),
            status="completed",
            duration=dur,
        )
        proj.set_multimodal_analysis(fused)
        session.add(proj)
        session.commit()
        session.refresh(proj)

        clip = ViralClip(
            project_id=proj.id,
            start_time=0.0,
            end_time=dur,
            duration=dur,
            bgm_path=None,
        )
        clip.set_options({"has_existing_music": fused["summary"]["has_existing_music"]})
        session.add(clip)
        session.commit()
        session.refresh(clip)

        ser = main_mod.get_clip(clip.id, session=session)
        assert ser["has_existing_music"] is True
        assert ser["bgm_path"] is None


def test_phase1_ten_minute_audio_vectorized_speed_benchmark(tmp_path: Path):
    """Guarantee a 10-minute (600s) 16 kHz WAV completes full Phase 1 DSP analysis well under 10s."""
    sr = 16000
    dur = 600.0
    n = int(sr * dur)
    t_arr = np.arange(n, dtype=np.float32) / float(sr)
    sig = (0.20 * np.sin(2.0 * np.pi * 135.0 * t_arr) + 0.08 * np.sin(2.0 * np.pi * 80.0 * t_arr)).astype(np.float32)
    wav_10m = _write_wav(tmp_path / "bench_10m.wav", sig, sr=sr)

    t0 = time.perf_counter()
    res = analyze_audio_track(str(wav_10m), word_segments=[])
    elapsed = time.perf_counter() - t0

    assert len(res["timeline"]) >= 2300
    assert elapsed < 10.0, f"10-minute audio analysis took {elapsed:.2f}s (exceeds 10s limit!)"


def _encode_frames_to_mp4(frames: List[np.ndarray], out_path: Path, fps: float = 4.0) -> Path:
    """Encode BGR NumPy frames into a real H.264 MP4 using FFmpeg rawvideo stdin pipe."""
    from app.services.proxy import get_ffmpeg_cmd

    out_path.parent.mkdir(parents=True, exist_ok=True)
    h, w = frames[0].shape[:2]
    cmd = [
        get_ffmpeg_cmd(),
        "-y",
        "-f", "rawvideo",
        "-vcodec", "rawvideo",
        "-pix_fmt", "bgr24",
        "-s", f"{w}x{h}",
        "-r", f"{fps:.2f}",
        "-i", "-",
        "-an",
        "-c:v", "libx264",
        "-preset", "ultrafast",
        "-pix_fmt", "yuv420p",
        str(out_path),
    ]
    raw_bytes = b"".join(f.tobytes() for f in frames)
    proc = subprocess.run(cmd, input=raw_bytes, capture_output=True, timeout=60)
    assert proc.returncode == 0, f"Failed encoding synthetic video: {proc.stderr.decode(errors='ignore')}"
    return out_path


def _make_blank_landmarks(yaw_shift: float = 0.0, pitch_shift: float = 0.0, iris_shift: float = 0.0) -> List[Dict[str, float]]:
    """Build 478 synthetic MediaPipe landmarks with configurable yaw, pitch, and iris offset."""
    pts = [{"x": 0.5, "y": 0.5, "z": 0.0} for _ in range(478)]
    pts[10] = {"x": 0.50, "y": 0.25, "z": 0.0}      # forehead
    pts[152] = {"x": 0.50, "y": 0.75, "z": 0.0}     # chin (face_h = 0.50)
    pts[33] = {"x": 0.36, "y": 0.42, "z": 0.0}      # L outer
    pts[133] = {"x": 0.44, "y": 0.42, "z": 0.0}     # L inner
    pts[362] = {"x": 0.56, "y": 0.42, "z": 0.0}     # R inner
    pts[263] = {"x": 0.64, "y": 0.42, "z": 0.0}     # R outer
    pts[1] = {"x": 0.50 + yaw_shift, "y": 0.53 + pitch_shift, "z": -0.05}  # nose tip
    pts[468] = {"x": 0.40 + iris_shift, "y": 0.42, "z": 0.0}  # L iris center
    pts[473] = {"x": 0.60 + iris_shift, "y": 0.42, "z": 0.0}  # R iris center
    return pts


def test_phase2_eye_contact_head_pose_and_emphasis_spike():
    head_state: Dict = {}
    # Straight-ahead direct-to-camera eye contact
    lms_direct = _make_blank_landmarks(yaw_shift=0.0, pitch_shift=0.0, iris_shift=0.0)
    face_base = {"cx": 0.5, "cy": 0.48, "w": 0.30, "h": 0.40}
    direct_res = compute_head_pose_and_eye_contact(
        lms_direct, face_base, prev_head_state=head_state, timestamp=0.5
    )
    assert direct_res["eye_contact_score"] >= 88.0
    assert abs(direct_res["head_yaw_deg"]) <= 5.0

    # Baseline second sample + sudden lean-in & head nod emphasis at t=1.2s
    compute_head_pose_and_eye_contact(lms_direct, face_base, prev_head_state=head_state, timestamp=0.8)
    lms_nod = _make_blank_landmarks(yaw_shift=0.0, pitch_shift=0.04, iris_shift=0.0)
    face_lean_in = {"cx": 0.5, "cy": 0.48, "w": 0.36, "h": 0.48}
    nod_res = compute_head_pose_and_eye_contact(
        lms_nod, face_lean_in, prev_head_state=head_state, timestamp=1.2
    )
    assert nod_res["head_emphasis_spike"] is True
    assert nod_res["gaze_engagement_score"] >= direct_res["eye_contact_score"] * 0.85

    # Side-profile yaw away from camera drops eye_contact_score steeply
    lms_away = _make_blank_landmarks(yaw_shift=0.11, pitch_shift=0.0, iris_shift=0.025)
    away_res = compute_head_pose_and_eye_contact(lms_away, face_base, timestamp=2.0)
    assert away_res["eye_contact_score"] <= 55.0
    assert abs(away_res["head_yaw_deg"]) >= 20.0


def test_phase2_visual_novelty_spike_and_multi_roi_saliency(tmp_path: Path):
    w, h = 426, 240
    frames: List[np.ndarray] = []

    # 0.0..2.0s (8 frames @ 4fps): single speaker region on left
    fr_a = np.full((h, w, 3), 24, dtype=np.uint8)
    cv2.circle(fr_a, (105, 110), 42, (200, 170, 145), -1)
    cv2.rectangle(fr_a, (60, 155), (150, 230), (70, 110, 180), -1)
    for _ in range(8):
        frames.append(fr_a.copy())

    # 2.0..4.0s (8 frames @ 4fps): cutaway showing speaker on left + bright slide chart on right (secondary_roi)
    fr_b = np.full((h, w, 3), 210, dtype=np.uint8)
    cv2.circle(fr_b, (78, 120), 32, (90, 70, 60), -1)
    cv2.rectangle(fr_b, (185, 34), (400, 200), (20, 40, 130), -1)
    cv2.line(fr_b, (205, 175), (380, 60), (0, 240, 220), 5)
    cv2.putText(fr_b, "CHART +94%", (205, 95), cv2.FONT_HERSHEY_SIMPLEX, 0.75, (255, 255, 255), 2)
    for _ in range(8):
        frames.append(fr_b.copy())

    mp4 = _encode_frames_to_mp4(frames, tmp_path / "novelty_saliency.mp4", fps=4.0)
    res = analyze_video_track(str(mp4), sample_fps=4.0)

    v_frames = res["frames"]
    assert len(v_frames) >= 12
    # Static pre-cut frames (index 2..5) have low novelty vs sharp novelty spike at layout switch (~2.0s)
    static_novs = [v_frames[i]["visual_novelty_score"] for i in range(2, min(6, len(v_frames)))]
    switch_novs = [v_frames[i]["visual_novelty_score"] for i in range(6, min(11, len(v_frames)))]
    assert max(static_novs) < 25.0
    assert max(switch_novs) >= 40.0, f"Expected novelty spike on cutaway, got {switch_novs}"

    # Verify multi-ROI captures both primary_roi & spatially distinct secondary_roi on dual layout
    post_rois = [f.get("saliency_rois") or {} for f in v_frames[8:]]
    assert any(r.get("primary_roi") and r.get("secondary_roi") for r in post_rois), f"Expected dual ROIs: {post_rois[:2]}"


def test_phase2_hud_obstruction_mapper_and_recommended_caption_y(tmp_path: Path):
    w, h = 426, 240

    # Video A: Clean background with NO burned-in bottom subtitles/HUD -> rec_y == 0.78
    clean_frames = []
    for _ in range(8):
        fr = np.full((h, w, 3), 38, dtype=np.uint8)
        cv2.circle(fr, (213, 96), 36, (170, 145, 130), -1)
        clean_frames.append(fr)
    mp4_clean = _encode_frames_to_mp4(clean_frames, tmp_path / "clean_hud.mp4", fps=4.0)
    res_clean = analyze_video_track(str(mp4_clean), sample_fps=4.0)
    assert res_clean["obstruction_zones"]["bottom_hud_occupied"] is False
    assert res_clean["obstruction_zones"]["recommended_caption_y"] == 0.78

    # Video B: Bold burned-in bottom subtitles/lower-third banner across bottom band (y=75..88%)
    hud_frames = []
    for k in range(8):
        fr = np.full((h, w, 3), 38, dtype=np.uint8)
        cv2.circle(fr, (213, 92), 34, (170, 145, 130), -1)
        # Simulated dark lower-third strip + crisp high-contrast subtitle letters
        cv2.rectangle(fr, (42, 174), (384, 214), (10, 10, 10), -1)
        sub_txt = f"BREAKING SUBTITLE LINE {k + 1}"
        cv2.putText(fr, sub_txt, (56, 202), cv2.FONT_HERSHEY_SIMPLEX, 0.62, (255, 255, 255), 2)
        hud_frames.append(fr)
    mp4_hud = _encode_frames_to_mp4(hud_frames, tmp_path / "bottom_hud.mp4", fps=4.0)
    res_hud = analyze_video_track(str(mp4_hud), sample_fps=4.0)

    oz = res_hud["obstruction_zones"]
    assert oz["bottom_hud_occupied"] is True
    assert oz["recommended_caption_y"] <= 0.30, f"Expected top-safe caption Y <= 0.30, got {oz}"

    # Verify fusion & clip window helper automatically recommends top-safe caption Y
    fused = fuse_multimodal_tracks(None, res_hud, word_segments=[], duration=2.0)
    clip_oz = evaluate_clip_obstruction_zones(fused, 0.0, 2.0)
    assert clip_oz["bottom_hud_occupied"] is True
    assert clip_oz["recommended_caption_y"] <= 0.30

