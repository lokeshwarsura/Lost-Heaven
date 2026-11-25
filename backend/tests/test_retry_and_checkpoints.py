import os
import json
import pytest
from unittest.mock import MagicMock, patch
from pathlib import Path
from app.services.job_manager import JobManager
from app.services.transcriber import resolve_whisper_device_and_compute, is_filler_word
from app.services.gemini_clipper import format_transcript_as_text, extract_viral_clips

def test_whisper_vram_scaling_dynamic():
    with patch("app.config.settings.WHISPER_DEVICE", "auto"):
        # 1. Test VRAM >= 8GB -> float16 large-v3-turbo
        with patch("app.services.hardware.detect_hardware_specs") as mock_hw:
            mock_hw.return_value = {"has_cuda": True, "vram_mb": 12288, "gpu_name": "NVIDIA RTX 5070"}
            dev, compute, model = resolve_whisper_device_and_compute()
            assert dev == "cuda"
            assert compute == "float16"
            assert model == "large-v3-turbo"

        # 2. Test VRAM < 8GB -> int8_float16
        with patch("app.services.hardware.detect_hardware_specs") as mock_hw:
            mock_hw.return_value = {"has_cuda": True, "vram_mb": 4096, "gpu_name": "NVIDIA RTX 3050"}
            dev, compute, model = resolve_whisper_device_and_compute()
            assert dev == "cuda"
            assert compute == "int8_float16"

        # 3. Test CPU fallback
        with patch("app.services.hardware.detect_hardware_specs") as mock_hw:
            mock_hw.return_value = {"has_cuda": False, "vram_mb": 0, "gpu_name": "None"}
            dev, compute, model = resolve_whisper_device_and_compute()
            assert dev == "cpu"
            assert compute == "int8"

def test_format_transcript_as_text():
    words = [
        {"word": "Welcome", "start": 0.0, "end": 0.5},
        {"word": "to", "start": 0.6, "end": 0.8},
        {"word": "ClipShip.", "start": 0.9, "end": 1.5},
        {"word": "Let's", "start": 1.6, "end": 2.0},
        {"word": "create", "start": 2.1, "end": 2.5},
        {"word": "viral", "start": 2.6, "end": 2.9},
        {"word": "Shorts!", "start": 3.0, "end": 3.5},
    ]
    formatted = format_transcript_as_text(words)
    assert "[0.0s" in formatted
    assert "Welcome to ClipShip." in formatted
    assert "Shorts!" in formatted

def test_job_manager_checkpoint_detection_on_retry(tmp_path):
    jm = JobManager()
    project_id = 8888

    # Mock project dir to tmp_path
    with patch.object(jm, "_get_project_dir", return_value=tmp_path):
        # Create fresh job
        job = jm.create_job(
            project_id=project_id,
            title="Checkpoint Test",
            source_path=str(tmp_path / "source.mp4")
        )

        # 1. No checkpoints: retry resumes at ingest (5%)
        retry_job = jm.retry_pipeline(project_id)
        assert retry_job["current_stage"] == "ingest"
        assert retry_job["percent"] == 5

        # 2. Add audio_16k.wav checkpoint: retry resumes at audio (20%)
        audio_file = tmp_path / "audio_16k.wav"
        audio_file.write_bytes(b"RIFF" + b"0" * 2000)
        retry_job = jm.retry_pipeline(project_id)
        assert retry_job["current_stage"] == "audio"
        assert retry_job["percent"] == 20

        # 3. Add proxy_480p.mp4 checkpoint: retry resumes at transcribe (35%)
        proxy_file = tmp_path / "proxy_480p.mp4"
        proxy_file.write_bytes(b"\x00\x00\x00\x18ftypmp42" + b"0" * 3000)
        retry_job = jm.retry_pipeline(project_id)
        assert retry_job["current_stage"] == "transcribe"
        assert retry_job["percent"] == 35

        # 4. Add transcript.json checkpoint: retry resumes at gemini (68%)
        transcript_file = tmp_path / "transcript.json"
        transcript_file.write_text(json.dumps([
            {"word": "Test", "start": 0.0, "end": 0.5, "is_filler": False, "deleted": False}
        ]))
        retry_job = jm.retry_pipeline(project_id)
        assert retry_job["current_stage"] == "gemini"
        assert retry_job["percent"] == 68

def test_gemini_503_retry_and_transcript_fallback():
    # Mock GenAI client to simulate 503 UNAVAILABLE followed by success
    mock_client = MagicMock()
    mock_response = MagicMock()
    mock_response.text = json.dumps({
        "clips": [
            {
                "start_time": "00:05",
                "end_time": "00:45",
                "virality_score": 95,
                "hook_score": 92,
                "retention_score": 90,
                "emotional_score": 88,
                "standalone_score": 95,
                "viral_rationale": "High virality hook",
                "hook_headline": "MASSIVE BREAKTHROUGH",
                "youtube_titles": ["Insane Breakthrough #Shorts"],
                "youtube_description": "Watch this!",
                "pinned_comment": "Thoughts?"
            }
        ]
    })

    # Call 1: raises 503 error; Call 2: succeeds
    mock_client.models.generate_content.side_effect = [
        Exception("503 UNAVAILABLE: The model is overloaded. Please try again later."),
        mock_response
    ]

    logs = []
    def log_cb(stage, msg):
        logs.append(msg)

    with patch("google.genai.Client", return_value=mock_client), \
         patch("time.sleep", return_value=None), \
         patch("app.config.settings.GEMINI_API_KEY", "test-key-valid"):
        
        clips = extract_viral_clips(
            proxy_path=None,
            total_duration=60.0,
            duration_preset="30-58s",
            clip_count=1,
            transcript_words=[{"word": "Testing", "start": 0.0, "end": 1.0}],
            on_log=log_cb
        )

        assert len(clips) == 1
        assert clips[0]["virality_score"] == 95
        assert clips[0]["hook_headline"] == "MASSIVE BREAKTHROUGH"
        # Verify 503 retry log was emitted
        assert any("503" in l or "retrying" in l for l in logs)
