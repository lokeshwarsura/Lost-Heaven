import math
import subprocess
import wave
from pathlib import Path

import cv2
import numpy as np
import pytest
from sqlmodel import Session, SQLModel, create_engine

import app.database as app_db
import app.main as main_mod
from app.config import settings
from app.models import Project, ViralClip
from app.services.audio_analyzer import analyze_audio_track
from app.services.multimodal_fusion import fuse_multimodal_tracks
from app.services.offline_clipper import extract_viral_clips_offline
from app.services.proxy import get_ffmpeg_cmd
from app.services.video_analyzer import analyze_video_track
from tests.test_unified_reframer import _draw_portrait


def _setup_test_env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    storage_dir = tmp_path / "storage"
    for sub in ("previews", "renders", "masters", "assets", "thumbnails", "models", "projects"):
        (storage_dir / sub).mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr(settings, "STORAGE_DIR", str(storage_dir))
    monkeypatch.setattr(settings, "GEMINI_API_KEY", "")
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    monkeypatch.setattr(main_mod, "get_persisted_settings", lambda: {"ai_provider": "offline", "gemini_boost_enabled": False})
    db_path = tmp_path / "v8_e2e_offline.db"
    test_engine = create_engine(f"sqlite:///{db_path}", connect_args={"check_same_thread": False})
    SQLModel.metadata.create_all(test_engine)
    monkeypatch.setattr(app_db, "engine", test_engine)
    return test_engine, storage_dir


def _build_av_sample(out_mp4: Path, tmp_path: Path) -> tuple[Path, Path]:
    """Create a real 6.0s 1280x720 MP4 + 16kHz WAV with calm intro, dramatic pause, and excited/laughing surge."""
    sr = 16000
    dur = 6.0
    t = np.linspace(0.0, dur, int(sr * dur), endpoint=False, dtype=np.float32)
    rng = np.random.default_rng(99)
    sig = rng.normal(0.0, 0.00015, size=len(t)).astype(np.float32)

    m1 = (t >= 0.0) & (t < 2.0)
    sig[m1] += (0.14 * np.sin(2 * math.pi * 145.0 * t[m1])).astype(np.float32)
    # 2.0..2.7s dramatic pause near zero
    m2 = (t >= 2.7) & (t < 4.5)
    sig[m2] += (0.58 * np.sin(2 * math.pi * 270.0 * t[m2]) + 0.22 * np.sin(2 * math.pi * 540.0 * t[m2])).astype(np.float32)
    m3 = (t >= 4.5) & (t <= 6.0)
    tl = t[m3] - 4.5
    pulse = np.maximum(0.0, np.sin(2 * math.pi * 5.2 * tl)) ** 2
    sig[m3] += (pulse * (0.52 * np.sin(2 * math.pi * 300.0 * tl) + 0.15 * rng.normal(0.0, 1.0, size=len(tl)))).astype(np.float32)

    wav_path = tmp_path / "e2e_audio.wav"
    pcm = np.clip(sig * 32767.0, -32767.0, 32767.0).astype(np.int16)
    with wave.open(str(wav_path), "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(sr)
        wf.writeframes(pcm.tobytes())

    calm_fr = np.full((720, 1280, 3), (75, 88, 112), dtype=np.uint8)
    _draw_portrait(calm_fr, 640, 340, scale=1.35, shirt_bgr=(55, 95, 195))

    react_fr = calm_fr.copy()
    cv2.putText(react_fr, "OMG SCORE +999!", (840, 130), cv2.FONT_HERSHEY_SIMPLEX, 1.0, (40, 230, 255), 3)
    cv2.rectangle(react_fr, (825, 85), (1180, 155), (255, 255, 255), 3)

    seq_dir = tmp_path / "v8_seq"
    seq_dir.mkdir(parents=True, exist_ok=True)
    p1 = seq_dir / "f1.png"
    p2 = seq_dir / "f2.png"
    cv2.imwrite(str(p1), calm_fr)
    cv2.imwrite(str(p2), react_fr)
    c_file = seq_dir / "seq.ffconcat"
    c_file.write_text(
        "ffconcat version 1.0\n"
        f"file '{p1.name}'\nduration 2.700\n"
        f"file '{p2.name}'\nduration 3.300\n"
        f"file '{p2.name}'\n",
        encoding="utf-8",
    )

    ffmpeg = get_ffmpeg_cmd()
    cmd = [
        ffmpeg, "-y", "-hide_banner",
        "-f", "concat", "-safe", "0", "-i", c_file.name,
        "-i", str(wav_path.resolve()),
        "-vf", "fps=8",
        "-c:v", "libx264", "-preset", "ultrafast", "-pix_fmt", "yuv420p",
        "-c:a", "aac", "-shortest",
        str(out_mp4.resolve()),
    ]
    proc = subprocess.run(cmd, cwd=str(seq_dir), capture_output=True, text=True, timeout=60)
    assert proc.returncode == 0, proc.stderr
    return out_mp4, wav_path


def test_end_to_end_offline_multimodal_emotion_serialization_and_inspector_api(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
):
    test_engine, _ = _setup_test_env(tmp_path, monkeypatch)
    mp4_path, wav_path = _build_av_sample(tmp_path / "offline_v8_e2e.mp4", tmp_path)

    words = [
        {"word": "Here", "start": 0.15, "end": 0.45},
        {"word": "is", "start": 0.50, "end": 0.80},
        {"word": "the", "start": 0.85, "end": 1.15},
        {"word": "setup.", "start": 1.20, "end": 1.80},
        {"word": "WAIT", "start": 2.75, "end": 3.20},
        {"word": "WHAT?!", "start": 3.25, "end": 3.95},
        {"word": "HAHAHA!", "start": 4.55, "end": 5.80},
    ]

    aud = analyze_audio_track(str(wav_path), word_segments=words)
    vis = analyze_video_track(str(mp4_path), sample_fps=4.0)
    fused = fuse_multimodal_tracks(aud, vis, word_segments=words, duration=6.0)
    assert fused["version"] == 2
    assert fused["emotion_summary"]["dominant_vocal_emotion"]
    assert fused["acoustic_events"]

    clips = extract_viral_clips_offline(
        audio_wav_path=str(wav_path),
        transcript_words=words,
        duration_sec=6.0,
        duration_preset="Auto",
        clip_count=2,
        multimodal_context=fused,
    )
    assert clips
    top = clips[0]
    assert top["analysis_source"] == "offline"
    assert top["narrative_arc"]
    assert top["dominant_emotion"]
    assert top["vocal_emotion"]

    with Session(test_engine) as session:
        proj = Project(
            title="Offline Multimodal Affective Test",
            source_path=str(mp4_path),
            duration=6.0,
            status="finished",
        )
        proj.set_multimodal_analysis(fused)
        session.add(proj)
        session.commit()
        session.refresh(proj)

        clip = ViralClip(
            project_id=proj.id,
            start_time=float(top["start_time"]),
            end_time=float(top["end_time"]),
            duration=float(top["duration"]),
            virality_score=int(top["virality_score"]),
            hook_score=int(top["hook_score"]),
            retention_score=int(top["retention_score"]),
            emotional_score=int(top["emotional_score"]),
            standalone_score=int(top["standalone_score"]),
            viral_rationale=str(top["viral_rationale"]),
            hook_headline=str(top["hook_headline"]),
            analysis_source="offline",
        )
        clip.set_options({
            "narrative_arc": top["narrative_arc"],
            "arc_label": top["arc_label"],
            "dominant_emotion": top["dominant_emotion"],
            "vocal_emotion": top["vocal_emotion"],
            "emotion_arousal": top["emotion_arousal"],
            "emotion_valence": top["emotion_valence"],
            "acoustic_events": top["acoustic_events"],
        })
        clip.set_transcript(words)
        session.add(clip)
        session.commit()
        session.refresh(clip)

        serialized = main_mod._serialize_clip(clip, project=proj)
        assert serialized["narrative_arc"] == top["narrative_arc"]
        assert serialized["dominant_emotion"] == top["dominant_emotion"]
        assert serialized["vocal_emotion"] == top["vocal_emotion"]
        assert serialized["analysis_source"] == "offline"
        assert serialized["emotion_summary"]

        bf_resp = main_mod.get_clip_best_frames_endpoint(proj.id, clip.id, session=session)
        assert bf_resp["frames"]
        assert bf_resp["multimodal_timeline"]
        assert bf_resp["clip_emotion_breakdown"]["dominant_facial_emotion"]
        assert bf_resp["clip_emotion_breakdown"]["dominant_vocal_emotion"]
        assert bf_resp["clip_emotion_breakdown"]["narrative_arc"] == top["narrative_arc"]
        assert isinstance(bf_resp["acoustic_events"], list)
        assert bf_resp["acoustic_events"]
