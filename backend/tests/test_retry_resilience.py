import os
import json
import time
import pytest
from pathlib import Path
from unittest.mock import MagicMock, patch

from sqlmodel import Session, select
from app.database import engine, init_db
from app.models import Project, ViralClip
from app.main import cancel_project_job_endpoint
from app.services.job_manager import JobManager
from app.services.gemini_clipper import (
    DEFAULT_MODEL_CASCADE,
    get_model_cascade,
    format_transcript_as_text,
    extract_viral_clips,
    GeminiAuthError,
    GeminiUnavailableError
)

init_db()

def test_format_transcript_as_text_compact_for_large_transcripts():
    """Verify that a 5,000+ word transcript is compactly formatted into sentence/5-second blocks."""
    sample_words = []
    t = 0.0
    for i in range(5200):
        w = f"word{i}"
        if i % 15 == 14:
            w += "."
        sample_words.append({"word": w, "start": round(t, 2), "end": round(t + 0.3, 2)})
        t += 0.35

    formatted = format_transcript_as_text(sample_words)
    assert len(formatted) > 0
    lines = formatted.split("\n")
    # 5200 words grouped in ~10-15 word lines should yield 300-500 lines, not 5200 lines
    assert 200 < len(lines) < 600
    assert lines[0].startswith("[0.0s -")


def test_retry_pipeline_resumes_5000_words_and_resets_timer(tmp_path):
    """
    Verify retry_pipeline resumes from Stage 4 when transcript.json exists,
    logs the loaded word count, and resets started_at to now.
    """
    jm = JobManager()
    project_id = 9101

    with Session(engine) as session:
        proj = Project(
            id=project_id,
            title="5K Word Resumption Test",
            source_path=str(tmp_path / "dummy.mp4"),
            status="error"
        )
        session.merge(proj)
        session.commit()

    # Create dummy 5,200-word transcript.json
    transcript_path = tmp_path / "transcript.json"
    dummy_words = [{"word": f"word_{i}", "start": float(i), "end": float(i + 0.8)} for i in range(5200)]
    with open(transcript_path, "w", encoding="utf-8") as f:
        json.dump(dummy_words, f)

    audio_path = tmp_path / "audio_16k.wav"
    audio_path.write_bytes(b"RIFF" + b"\x00" * 2000)

    old_time = "2026-11-20T10:00:00"
    jm._jobs[project_id] = {
        "project_id": project_id,
        "title": "5K Word Resumption Test",
        "status": "error",
        "started_at": old_time,
        "stages": [],
        "logs": []
    }

    with patch.object(jm, "_get_project_dir", return_value=tmp_path):
        job = jm.retry_pipeline(project_id, background_tasks=None, force_offline=True)

        assert job["status"] == "processing"
        assert job["current_stage"] == "gemini"
        assert job["percent"] == 68
        assert job["started_at"] != old_time

        # Verify retry log mentions the loaded cached words count
        retry_logs = [log for log in job.get("logs", []) if log.get("stage") == "RETRY"]
        assert len(retry_logs) > 0
        assert "5,200 cached words" in retry_logs[0]["message"]


def test_gemini_timeout_triggers_offline_fallback(tmp_path):
    """
    Verify simulated timeout on Gemini models cascades and completes at 100% via offline_clipper.
    """
    jm = JobManager()
    project_id = 9102

    with Session(engine) as session:
        proj = Project(
            id=project_id,
            title="Timeout Fallback Test",
            source_path=str(tmp_path / "dummy.mp4"),
            status="error"
        )
        session.merge(proj)
        session.commit()

    transcript_path = tmp_path / "transcript.json"
    sample_words = [
        {"word": "Timeout", "start": 0.0, "end": 0.5},
        {"word": "test.", "start": 0.6, "end": 1.2}
    ]
    with open(transcript_path, "w", encoding="utf-8") as f:
        json.dump(sample_words, f)

    audio_path = tmp_path / "audio_16k.wav"
    audio_path.write_bytes(b"RIFF" + b"\x00" * 2000)

    # Mock extract_viral_clips raising GeminiUnavailableError because of timeouts
    with patch.object(jm, "_get_project_dir", return_value=tmp_path), \
         patch("app.services.job_manager.extract_viral_clips", side_effect=GeminiUnavailableError("Exceeded 60s timeout on all models")), \
         patch("app.services.job_manager.get_persisted_settings", return_value={
             "gemini_enabled": True,
             "gemini_api_key": "test-api-key",
             "auto_fallback_offline": True,
             "gemini_analysis_mode": "transcript"
         }), \
         patch("app.services.job_manager.extract_viral_clips_offline") as mock_offline:

        mock_offline.return_value = [{
            "start_time": 0.0,
            "end_time": 1.2,
            "duration": 1.2,
            "virality_score": 90,
            "hook_score": 88,
            "retention_score": 85,
            "emotional_score": 80,
            "standalone_score": 85,
            "viral_rationale": "Offline recovered",
            "hook_headline": "TIMEOUT RECOVERED",
            "cold_open_quote": "Timeout test.",
            "youtube_titles": ["Recovered Short #Shorts"],
            "youtube_description": "Auto fallback #Shorts",
            "pinned_comment": "Did you notice?",
            "analysis_source": "offline_fallback"
        }]

        jm.create_job(project_id, "Timeout Fallback Test", str(tmp_path / "dummy.mp4"))
        with patch("app.services.job_manager.probe_video", return_value={"duration": 10.0, "width": 1920, "height": 1080, "fps": 30.0}):
            jm.run_pipeline(project_id)

        prog = jm.get_progress(project_id)
        assert prog["status"] == "finished"
        assert prog["percent"] == 100
        mock_offline.assert_called_once()


def test_startup_orphan_jobs_cleanup():
    """
    Verify cleanup_orphan_jobs finds any project stuck in 'processing' status
    and updates SQLite status to 'error'.
    """
    jm = JobManager()
    project_id = 9103

    with Session(engine) as session:
        proj = Project(
            id=project_id,
            title="Stuck Orphan Job",
            source_path="dummy.mp4",
            status="processing"
        )
        session.merge(proj)
        session.commit()

    # Run cleanup
    jm.cleanup_orphan_jobs()

    with Session(engine) as session:
        p = session.get(Project, project_id)
        assert p is not None
        assert p.status == "error"
        assert "Server restarted" in p.error_message


def test_cancel_endpoint_guarantees_db_cancellation():
    """
    Verify /api/projects/{id}/cancel always updates the SQLite project status to 'cancelled'
    even when no worker thread is in memory.
    """
    project_id = 9104

    with Session(engine) as session:
        proj = Project(
            id=project_id,
            title="Cancellation Test",
            source_path="dummy.mp4",
            status="processing"
        )
        session.merge(proj)
        session.commit()

    res = cancel_project_job_endpoint(project_id)
    assert res["status"] == "cancelled"

    with Session(engine) as session:
        p = session.get(Project, project_id)
        assert p is not None
        assert p.status == "cancelled"


def test_missing_api_key_on_retry_transitions_to_awaiting_key(tmp_path):
    """
    Verify that when gemini is enabled but API key is empty/placeholder on retry,
    the pipeline immediately transitions to awaiting_api_key without hanging.
    """
    jm = JobManager()
    project_id = 9105

    with Session(engine) as session:
        proj = Project(
            id=project_id,
            title="Missing Key Retry Test",
            source_path=str(tmp_path / "dummy.mp4"),
            status="error"
        )
        session.merge(proj)
        session.commit()

    transcript_path = tmp_path / "transcript.json"
    with open(transcript_path, "w", encoding="utf-8") as f:
        json.dump([{"word": "Hello", "start": 0.0, "end": 0.5}], f)

    audio_path = tmp_path / "audio_16k.wav"
    audio_path.write_bytes(b"RIFF" + b"\x00" * 2000)

    with patch.object(jm, "_get_project_dir", return_value=tmp_path), \
         patch("app.config.settings.GEMINI_API_KEY", ""), \
         patch.dict(os.environ, {"GEMINI_API_KEY": ""}, clear=False), \
         patch("app.services.job_manager.get_persisted_settings", return_value={
             "gemini_enabled": True,
             "gemini_api_key": "",  # Empty key
             "ai_provider": "gemini"
         }):

        jm.create_job(project_id, "Missing Key Retry Test", str(tmp_path / "dummy.mp4"))
        with patch("app.services.job_manager.probe_video", return_value={"duration": 5.0, "width": 1920, "height": 1080, "fps": 30.0}):
            jm.run_pipeline(project_id)

        prog = jm.get_progress(project_id)
        assert prog["status"] == "awaiting_api_key"
        assert prog["current_stage"] == "gemini"


def test_server_computed_elapsed_timer_utc_and_freeze(tmp_path):
    """
    Verify UTC ISO-8601 timestamps (+00:00), server-computed elapsed_seconds,
    timer freeze on pause/finish, and clean reset on retry.
    """
    from datetime import datetime, timedelta, timezone
    jm = JobManager()
    project_id = 9106

    with patch.object(jm, "_get_project_dir", return_value=tmp_path), \
         patch.object(jm, "run_pipeline"):
        job = jm.create_job(project_id, "Timer Test", str(tmp_path / "dummy.mp4"))
        assert "+00:00" in job["started_at"] or job["started_at"].endswith("Z")

        # Backdate active_started_at by 12 seconds to verify elapsed_seconds
        past = (datetime.now(timezone.utc) - timedelta(seconds=12)).isoformat()
        job["started_at"] = past
        job["active_started_at"] = past

        prog = jm.get_progress(project_id)
        assert 11 <= prog["elapsed_seconds"] <= 15

        # Pause job -> timer must freeze
        jm.pause_job_for_api_key(project_id, reason="missing")
        frozen = jm.get_progress(project_id)
        assert frozen["active_started_at"] is None
        assert 11 <= frozen["elapsed_seconds"] <= 15

        # Retry job -> timer must reset cleanly to 0
        retried = jm.retry_pipeline(project_id, force_offline=True)
        assert retried["elapsed_seconds"] == 0
        assert retried["accumulated_seconds"] == 0.0
        assert retried["active_started_at"] is not None


def test_hardware_ram_returns_zero_on_detection_failure():
    """Verify detect_hardware_specs never returns fake RAM or fake GPU on probe failure."""
    import builtins
    from app.services.hardware import detect_hardware_specs
    real_import = builtins.__import__

    def fail_psutil(name, *args, **kwargs):
        if name in ("psutil", "ctypes"):
            raise ImportError("mocked failure")
        return real_import(name, *args, **kwargs)

    with patch("builtins.__import__", side_effect=fail_psutil), \
         patch("subprocess.run", side_effect=FileNotFoundError("no nvidia-smi")):
        specs = detect_hardware_specs()
        assert specs["ram_gb"] == 0
        assert specs["gpu_name"] == "None"
        assert specs["has_cuda"] is False
