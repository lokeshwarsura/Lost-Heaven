import json
import os
import wave
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import cv2
import numpy as np
from sqlmodel import Session, SQLModel, create_engine

import app.database as app_db
import app.main as main_mod
import app.services.job_manager as jm_mod
from app.config import settings
from app.models import Project
from app.services.job_manager import JobManager, resolve_ai_mode
from app.services.transcriber import transcribe_audio_full
from app.services.video_analyzer import analyze_video_track
from tests.test_multimodal_analyzers import _encode_sequence_mp4
from tests.test_unified_reframer import _draw_portrait


def test_missing_key_without_explicit_offline_triggers_choice_state_no_fake_gemini_logs(tmp_path: Path, monkeypatch):
    """
    1. When Gemini API key is empty and `ai_mode` is NOT explicitly set to 'offline',
       job creation and pipeline execution surface `needs_api_key_or_offline_choice`
       and transition cleanly without emitting any fake 'GEMINI' log entries.
    """
    monkeypatch.setattr(settings, "STORAGE_DIR", str(tmp_path / "storage"))
    monkeypatch.setattr(settings, "GEMINI_API_KEY", "")
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)

    db_path = tmp_path / "choice_state.db"
    test_engine = create_engine(f"sqlite:///{db_path}", connect_args={"check_same_thread": False})
    SQLModel.metadata.create_all(test_engine)
    monkeypatch.setattr(app_db, "engine", test_engine)
    monkeypatch.setattr(jm_mod, "engine", test_engine)

    with Session(test_engine) as session:
        proj = Project(id=4101, title="No Key Choice Test", source_path=str(tmp_path / "src.mp4"), status="created")
        session.add(proj)
        session.commit()

    persisted_unconfigured = {"gemini_enabled": True, "gemini_api_key": "", "ai_provider": "gemini"}
    assert resolve_ai_mode(persisted_unconfigured) == "needs_api_key_or_offline_choice"
    assert resolve_ai_mode({"gemini_enabled": False, "ai_mode": "offline"}) == "offline"

    with patch("app.main.get_persisted_settings", return_value=persisted_unconfigured):
        studio_cfg = main_mod.get_studio_settings()
        assert studio_cfg["needs_api_key_or_offline_choice"] is True
        assert studio_cfg["ai_mode"] is None

    # Create cached artifacts so stages 1-3 complete immediately into Stage 4 key check
    proj_dir = tmp_path / "storage" / "projects" / "4101"
    proj_dir.mkdir(parents=True, exist_ok=True)
    (proj_dir / "audio_16k.wav").write_bytes(b"RIFF" + b"\x00" * 2048)
    (proj_dir / "transcript.json").write_text(json.dumps([{"word": "Test", "start": 0.0, "end": 0.4}]), encoding="utf-8")

    jm = JobManager()
    with patch("app.services.job_manager.get_persisted_settings", return_value=persisted_unconfigured), \
         patch("app.services.job_manager.probe_video", return_value={"duration": 6.0, "width": 1280, "height": 720, "fps": 30.0}):
        job = jm.create_job(4101, "No Key Choice Test", str(tmp_path / "src.mp4"))
        assert job["needs_api_key_or_offline_choice"] is True
        assert job["ai_mode"] == "needs_api_key_or_offline_choice"

        jm.run_pipeline(4101)
        prog = jm.get_progress(4101)
        assert prog["status"] == "awaiting_api_key"
        assert prog["needs_api_key_or_offline_choice"] is True
        assert all(log.get("stage") != "GEMINI" for log in prog.get("logs", [])), (
            f"Found misleading GEMINI log when key missing: {prog.get('logs')}"
        )


def test_offline_pipeline_emits_analysis_and_offline_ai_tags_and_dynamic_pills(tmp_path: Path, monkeypatch):
    """
    2. Offline pipeline emits ANALYSIS and OFFLINE AI stage tags (zero GEMINI tags)
       Paired with dynamic Stage 2 (`Fast Audio Extract`) & Stage 4 (`Offline Multimodal AI`) pills.
    """
    monkeypatch.setattr(settings, "STORAGE_DIR", str(tmp_path / "storage"))
    monkeypatch.setattr(settings, "GEMINI_API_KEY", "")
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)

    db_path = tmp_path / "offline_tags.db"
    test_engine = create_engine(f"sqlite:///{db_path}", connect_args={"check_same_thread": False})
    SQLModel.metadata.create_all(test_engine)
    monkeypatch.setattr(app_db, "engine", test_engine)
    monkeypatch.setattr(jm_mod, "engine", test_engine)

    # Real tiny MP4 + WAV so Stage 1, 2, 4 ANALYSIS & OFFLINE AI run cleanly offline
    fr = np.full((720, 1280, 3), (80, 95, 118), dtype=np.uint8)
    _draw_portrait(fr, 640, 340, scale=1.35, shirt_bgr=(60, 110, 210))
    mp4 = _encode_sequence_mp4(tmp_path / "offline_src.mp4", [(fr, 2.5)], fps=8)

    with Session(test_engine) as session:
        proj = Project(id=4102, title="Offline Stage Tag Test", source_path=str(mp4), status="created")
        session.add(proj)
        session.commit()

    proj_dir = tmp_path / "storage" / "projects" / "4102"
    proj_dir.mkdir(parents=True, exist_ok=True)
    # Create valid 2.5s PCM WAV + cached words so whisper download isn't triggered in sandbox
    wav_path = proj_dir / "audio_16k.wav"
    sr = 16000
    pcm = (np.sin(2 * np.pi * 220.0 * np.linspace(0, 2.5, int(sr * 2.5))) * 10000.0).astype(np.int16)
    with wave.open(str(wav_path), "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(sr)
        wf.writeframes(pcm.tobytes())

    words = [
        {"word": "Why", "start": 0.1, "end": 0.4},
        {"word": "this", "start": 0.5, "end": 0.9},
        {"word": "works!", "start": 1.0, "end": 1.8},
    ]
    (proj_dir / "transcript.json").write_text(json.dumps(words), encoding="utf-8")

    persisted_offline = {"ai_mode": "offline", "ai_provider": "offline", "gemini_enabled": False}
    jm = JobManager()
    with patch("app.services.job_manager.get_persisted_settings", return_value=persisted_offline), \
         patch("app.services.renderer.render_clip_ultra", return_value=None):
        job = jm.create_job(4102, "Offline Stage Tag Test", str(mp4), ai_mode="offline")
        assert job["ai_mode"] == "offline"
        assert job["needs_proxy"] is False
        assert job["stages"][1]["name"] == "Fast Audio Extract"
        assert job["stages"][3]["name"] == "Offline Multimodal AI"

        jm.run_pipeline(4102)
        prog = jm.get_progress(4102)
        stages_seen = [log.get("stage") for log in prog.get("logs", [])]
        assert "ANALYSIS" in stages_seen, f"Expected ANALYSIS stage tag in {stages_seen}"
        assert "OFFLINE AI" in stages_seen, f"Expected OFFLINE AI stage tag in {stages_seen}"
        assert "GEMINI" not in stages_seen, f"Offline run emitted forbidden GEMINI tag: {stages_seen}"


def test_transcriber_batched_inference_and_incremental_progress_callbacks(tmp_path: Path):
    """
    3. Verify `transcribe_audio_full` uses `faster_whisper.BatchedInferencePipeline` with dynamic
       `batch_size` (16 GPU / 4 CPU) and emits incremental `on_progress` updates (~15% steps),
       and verify `video_analyzer.analyze_video_track` emits incremental frame scan callbacks.
    """
    wav_path = tmp_path / "whisper_prog.wav"
    sr = 16000
    dur = 10.0
    pcm = np.zeros(int(sr * dur), dtype=np.int16)
    with wave.open(str(wav_path), "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(sr)
        wf.writeframes(pcm.tobytes())

    fake_segments = [
        SimpleNamespace(
            end=1.8,
            words=[SimpleNamespace(word=" One", start=0.2, end=0.8), SimpleNamespace(word=" two", start=0.9, end=1.8)],
        ),
        SimpleNamespace(
            end=4.2,
            words=[SimpleNamespace(word=" three", start=2.1, end=3.1), SimpleNamespace(word=" four", start=3.2, end=4.2)],
        ),
        SimpleNamespace(
            end=7.5,
            words=[SimpleNamespace(word=" five", start=5.0, end=6.2), SimpleNamespace(word=" six", start=6.3, end=7.5)],
        ),
        SimpleNamespace(
            end=10.0,
            words=[SimpleNamespace(word=" seven!", start=8.0, end=10.0)],
        ),
    ]
    captured_calls = []

    class FakeBatchedPipeline:
        def __init__(self, model):
            self.model = model

        def transcribe(self, audio_path, **kwargs):
            captured_calls.append(kwargs)
            return iter(fake_segments), SimpleNamespace(duration=10.0)

    progress_events = []

    def on_prog(pct: int, msg: str):
        progress_events.append((pct, msg))

    with patch("app.services.transcriber.get_whisper_model", return_value=object()), \
         patch(
             "app.services.transcriber.get_whisper_runtime_info",
             return_value={
                 "device": "cuda",
                 "compute_type": "float16",
                 "model_name": "large-v3-turbo",
                 "batch_size": 16,
                 "badge": "GPU: CUDA float16",
             },
         ), \
         patch("faster_whisper.BatchedInferencePipeline", FakeBatchedPipeline):
        words = transcribe_audio_full(str(wav_path), language="English", on_progress=on_prog)

    assert len(words) == 7
    assert captured_calls and captured_calls[0]["batch_size"] == 16
    assert any("CUDA" in msg and "batch_size=16" in msg for _, msg in progress_events)
    assert any(15 <= pct < 100 and "Transcribed" in msg for pct, msg in progress_events)
    assert any(pct == 100 for pct, _ in progress_events)

    # Also verify video_analyzer emits keyframe progress milestones
    fr = np.full((360, 640, 3), 110, dtype=np.uint8)
    mp4 = _encode_sequence_mp4(tmp_path / "scan_prog.mp4", [(fr, 2.0)], fps=8)
    vis_events = []
    analyze_video_track(
        str(mp4),
        sample_fps=4.0,
        on_progress=lambda pct, cur, tot: vis_events.append((pct, cur, tot)),
    )
    assert len(vis_events) >= 3
    assert vis_events[-1][0] == 100 and vis_events[-1][1] == vis_events[-1][2]
