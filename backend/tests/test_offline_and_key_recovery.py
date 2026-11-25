import os
import json
import wave
import struct
import numpy as np
import pytest
from pathlib import Path
from unittest.mock import patch, MagicMock

from fastapi import BackgroundTasks, HTTPException
from app.main import provide_key_endpoint, retry_project_job_endpoint
from app.models import ProvideKeyRequest, RetryProjectRequest
from app.services.offline_clipper import (
    load_audio_energy_profile,
    segment_transcript_into_sentences,
    extract_viral_clips_offline,
    resolve_target_duration_bounds,
    score_hook,
    score_retention
)
from app.services.gemini_clipper import verify_gemini_api_key
from app.services.job_manager import JobManager, get_persisted_settings, save_persisted_settings

def create_synthetic_wav(path: Path, duration_sec: float = 10.0, sample_rate: int = 16000):
    """Generate a clean synthetic 16kHz mono WAV file for testing."""
    num_samples = int(duration_sec * sample_rate)
    with wave.open(str(path), "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(sample_rate)
        t = np.linspace(0, duration_sec, num_samples, endpoint=False)
        signal = 0.2 * np.sin(2 * np.pi * 440 * t)
        signal[(t >= 4.0) & (t <= 6.0)] *= 3.0
        pcm = (signal * 32767).astype(np.int16)
        wf.writeframes(pcm.tobytes())

def test_offline_audio_profiling(tmp_path):
    wav_path = tmp_path / "test_audio.wav"
    create_synthetic_wav(wav_path, duration_sec=10.0)

    db_rel, dur = load_audio_energy_profile(str(wav_path))
    assert len(db_rel) == 10
    assert db_rel[5] > db_rel[1]

def test_offline_sentence_segmentation():
    words = [
        {"word": "This", "start": 0.0, "end": 0.3},
        {"word": "is", "start": 0.35, "end": 0.5},
        {"word": "sentence", "start": 0.55, "end": 0.9},
        {"word": "one.", "start": 0.95, "end": 1.4},
        {"word": "Sentence", "start": 1.5, "end": 1.9},
        {"word": "two", "start": 1.95, "end": 2.2},
        {"word": "Sentence", "start": 3.0, "end": 3.4},
        {"word": "three", "start": 3.45, "end": 3.8},
        {"word": "ends", "start": 3.85, "end": 4.1},
        {"word": "now!", "start": 4.15, "end": 4.5},
    ]

    sentences = segment_transcript_into_sentences(words)
    assert len(sentences) == 3
    assert sentences[0]["text"] == "This is sentence one."
    assert sentences[0]["end"] == 1.4
    assert sentences[1]["text"] == "Sentence two"
    assert sentences[2]["text"] == "Sentence three ends now!"

def test_extract_viral_clips_offline_nms_and_metadata(tmp_path):
    wav_path = tmp_path / "test_energy.wav"
    create_synthetic_wav(wav_path, duration_sec=60.0)

    words = []
    current_time = 0.5
    for i in range(120):
        w = f"word{i}"
        if i == 0:
            w = "Secret"
        elif i == 1:
            w = "behind"
        elif i == 2:
            w = "greatness."
        elif i == 50:
            w = "Shocking!"
        words.append({
            "word": w,
            "start": round(current_time, 2),
            "end": round(current_time + 0.35, 2)
        })
        current_time += 0.45

    clips = extract_viral_clips_offline(
        audio_wav_path=str(wav_path),
        transcript_words=words,
        duration_sec=60.0,
        target_length="30s",
        num_clips=2,
        topic_focus="greatness"
    )

    assert len(clips) >= 1
    for c in clips:
        assert "start_time" in c
        assert "end_time" in c
        assert c["start_time"] < c["end_time"]
        assert 0 <= c["virality_score"] <= 100
        assert 0 <= c["hook_score"] <= 100
        assert 0 <= c["retention_score"] <= 100
        assert 0 <= c["emotional_score"] <= 100
        assert len(c["hook_headline"]) > 0
        assert len(c["youtube_titles"]) == 3
        assert "#Shorts" in c["youtube_description"]
        assert len(c["pinned_comment"]) > 0

    if len(clips) > 1:
        assert clips[0]["end_time"] <= clips[1]["start_time"] or clips[1]["end_time"] <= clips[0]["start_time"]

def test_verify_gemini_api_key_validation():
    ok, msg = verify_gemini_api_key("")
    assert not ok
    assert "empty" in msg.lower() or "too short" in msg.lower()

    ok, msg = verify_gemini_api_key("your_gemini_api_key_here")
    assert not ok

    ok, msg = verify_gemini_api_key("short")
    assert not ok

def test_job_manager_pause_and_resume_offline(tmp_path):
    jm = JobManager()
    project_id = 9999

    # Write cached transcript checkpoint so retry resumes at Stage 4 (gemini)
    (tmp_path / "transcript.json").write_text(json.dumps([
        {"word": "Hello", "start": 0.0, "end": 0.4},
        {"word": "world!", "start": 0.5, "end": 1.0}
    ]))

    with patch.object(jm, "_get_project_dir", return_value=tmp_path), \
         patch.object(jm, "run_pipeline"):
        job = jm.create_job(
            project_id=project_id,
            title="Pause Test",
            source_path=str(tmp_path / "source.mp4")
        )
        assert job["status"] == "processing"

        paused = jm.pause_job_for_api_key(
            project_id=project_id,
            reason="missing",
            message="No Gemini API key found in configuration."
        )
        assert paused is not None
        assert paused["status"] == "awaiting_api_key"
        assert paused["api_key_reason"] == "missing"
        assert "No Gemini API key" in paused["api_key_message"]

        retried = jm.retry_pipeline(project_id, force_offline=True)
        assert retried["status"] == "processing"
        assert retried.get("api_key_reason") is None
        assert retried["current_stage"] == "gemini"

def test_api_retry_and_provide_key_endpoints(tmp_path):
    bg = BackgroundTasks()

    # 1. Test provide-key with invalid key raises HTTPException 400
    with patch("app.main.verify_gemini_api_key", return_value=(False, "Invalid key")):
        with pytest.raises(HTTPException) as exc_info:
            provide_key_endpoint(9999, ProvideKeyRequest(api_key="invalid_too_short"), bg)
        assert exc_info.value.status_code == 400
        assert exc_info.value.detail == "Invalid key"

    # 2. Test provide-key with valid key returns resumed
    with patch("app.main.verify_gemini_api_key", return_value=(True, "Key is valid")), \
         patch("app.main.save_persisted_settings"), \
         patch("app.main.job_manager.retry_pipeline", return_value={"status": "processing", "current_stage": "gemini"}):
        res = provide_key_endpoint(9999, ProvideKeyRequest(api_key="test-api-key"), bg)
        assert res["status"] == "resumed"
        assert res["resumed_stage"] == "gemini"

    # 3. Test retry endpoint with force_offline=True
    with patch("app.main.job_manager.retry_pipeline") as mock_retry:
        mock_retry.return_value = {"status": "processing", "current_stage": "gemini"}
        res = retry_project_job_endpoint(9999, RetryProjectRequest(force_offline=True), bg)
        assert res["status"] == "processing"
        assert res["force_offline"] is True
        mock_retry.assert_called_once()
