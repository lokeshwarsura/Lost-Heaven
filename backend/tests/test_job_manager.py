import os
import json
import pytest
from app.services.job_manager import JobManager, job_manager
from app.services.hardware import detect_hardware_specs
from app.services.diagnostics import get_machine_specs, get_system_check
from app.services.transcriber import filter_filler_words, is_filler_word

def test_job_manager_lifecycle():
    jm = JobManager()
    job = jm.create_job(
        project_id=999,
        title="Test Ingestion Job",
        source_path="storage/masters/test.mp4",
        source_filename="test.mp4",
        duration_preset="30s",
        aspect_ratio="9:16",
        clip_count=3,
        caption_language="English"
    )

    assert job["project_id"] == 999
    assert job["status"] == "processing"
    assert job["current_stage"] == "ingest"
    assert len(job["stages"]) == 5
    assert len(job["logs"]) >= 1

    # Update progress
    jm.update_progress(999, 45, "transcribe", "Recognizing speech with faster-whisper", "Loaded model on CPU")
    progress = jm.get_progress(999)
    assert progress["percent"] == 45
    assert progress["current_stage"] == "transcribe"
    assert any("Loaded model" in log["message"] for log in progress["logs"])

    # Finish job
    jm.finish_job(999, clips_count=3)
    finished = jm.get_progress(999)
    assert finished["status"] == "finished"
    assert finished["percent"] == 100
    assert finished["clips_count"] == 3

def test_job_manager_cancel():
    jm = JobManager()
    jm.create_job(
        project_id=998,
        title="Cancel Job Test",
        source_path="storage/masters/cancel.mp4"
    )
    assert not jm.is_cancelled(998)
    jm.cancel_job(998)
    assert jm.is_cancelled(998)
    prog = jm.get_progress(998)
    assert prog["status"] == "cancelled"

def test_hardware_and_machine_specs_consistency():
    hw = detect_hardware_specs()
    assert "cpu_model" in hw
    assert hw["cpu_cores"] >= 1
    assert hw["ram_gb"] > 0
    assert "gpu_name" in hw

    machine = get_machine_specs()
    assert "cpu" in machine
    assert "ram" in machine
    assert "gpu" in machine
    assert "binaries" in machine

def test_filter_filler_words_logic():
    words = [
        {"word": "Um", "start": 0.0, "end": 0.5, "is_filler": True, "deleted": False},
        {"word": "welcome", "start": 0.6, "end": 1.2, "is_filler": False, "deleted": False},
        {"word": "to", "start": 1.3, "end": 1.5, "is_filler": False, "deleted": False},
        {"word": "like", "start": 1.6, "end": 1.9, "is_filler": True, "deleted": False},
        {"word": "ClipShip", "start": 2.0, "end": 2.8, "is_filler": False, "deleted": False},
    ]
    cleaned = filter_filler_words(words)
    assert len(cleaned) == 3
    assert [w["word"] for w in cleaned] == ["welcome", "to", "ClipShip"]
