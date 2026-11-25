import os
import json
import pytest
from pathlib import Path
from unittest.mock import MagicMock, patch

from sqlmodel import Session
from app.database import engine, init_db
init_db()
from app.models import Project, TestGeminiRequest as RequestTestGemini, ProvideKeyRequest, RetryProjectRequest, SettingsUpdateRequest
from app.main import (
    test_gemini_models_endpoint as api_test_gemini_models_endpoint,
    get_studio_settings,
    update_studio_settings,
    provide_key_endpoint,
    retry_project_job_endpoint
)
from app.services.gemini_clipper import (
    DEFAULT_MODEL_CASCADE,
    get_model_cascade,
    extract_viral_clips,
    verify_gemini_api_key,
    test_all_gemini_models as fn_test_all_gemini_models,
    GeminiAuthError,
    GeminiUnavailableError
)
from app.services.offline_clipper import extract_viral_clips_offline, load_audio_energy_profile
from app.services.job_manager import JobManager

MOCK_CLIPS_JSON = json.dumps({
    "clips": [
        {
            "start_time": 10.0,
            "end_time": 45.0,
            "virality_score": 95,
            "hook_score": 92,
            "retention_score": 90,
            "emotional_score": 88,
            "standalone_score": 94,
            "viral_rationale": "Strong curiosity hook",
            "hook_headline": "THE SHOCKING TRUTH",
            "cold_open_quote": "You won't believe this.",
            "youtube_titles": ["The Secret Revealed #Shorts", "Why This Matters #Shorts", "Shocking Truth #Shorts"],
            "youtube_description": "Watch until the end! #Shorts",
            "pinned_comment": "Did you know this?"
        }
    ]
})

def test_model_cascade_priority():
    cascade = get_model_cascade("gemini-3.5-flash-lite")
    assert cascade == ["gemini-3.5-flash-lite", "gemini-3.6-flash", "gemini-4-hydrogen", "gemini-2.5-flash-lite"]

    cascade_pref = get_model_cascade("gemini-4-hydrogen")
    assert cascade_pref[0] == "gemini-4-hydrogen"
    assert "gemini-3.5-flash-lite" in cascade_pref

def test_cascade_moves_from_35_to_36_on_503():
    """
    Verify cascade moves cleanly from gemini-3.5-flash-lite (mocked 503)
    to gemini-3.6-flash (200 OK) without crashing.
    """
    mock_client = MagicMock()

    def mock_generate_content(model, contents, config):
        if model == "gemini-3.5-flash-lite":
            raise Exception("503 UNAVAILABLE: The model is overloaded. Please try again later.")
        elif model == "gemini-3.6-flash":
            resp = MagicMock()
            resp.text = MOCK_CLIPS_JSON
            return resp
        raise Exception(f"Unexpected model: {model}")

    mock_client.models.generate_content.side_effect = mock_generate_content

    with patch("app.config.settings.GEMINI_API_KEY", "test-api-key"), \
         patch("google.genai.Client", return_value=mock_client), \
         patch("time.sleep", return_value=None):

        words = [
            {"word": "Hello", "start": 0.0, "end": 0.5},
            {"word": "world", "start": 0.6, "end": 1.0}
        ]

        clips = extract_viral_clips(
            transcript_words=words,
            gemini_analysis_mode="transcript",
            preferred_model="gemini-3.5-flash-lite",
            clip_count=1
        )

        assert len(clips) == 1
        assert clips[0]["virality_score"] == 95
        assert clips[0]["analysis_source"] == "gemini:gemini-3.6-flash"
        assert clips[0]["hook_headline"] == "THE SHOCKING TRUTH"

def test_total_503_outage_raises_gemini_unavailable():
    """
    When every model in the cascade returns 503, raises typed GeminiUnavailableError.
    """
    mock_client = MagicMock()
    mock_client.models.generate_content.side_effect = Exception("503 UNAVAILABLE: high demand")

    with patch("app.config.settings.GEMINI_API_KEY", "test-api-key"), \
         patch("google.genai.Client", return_value=mock_client), \
         patch("time.sleep", return_value=None):

        words = [{"word": "Hello", "start": 0.0, "end": 0.5}]

        with pytest.raises(GeminiUnavailableError) as exc_info:
            extract_viral_clips(
                transcript_words=words,
                preferred_model="gemini-3.5-flash-lite",
                gemini_analysis_mode="transcript"
            )

        assert "503" in str(exc_info.value)
        assert len(exc_info.value.models_attempted) >= 4

def test_job_manager_auto_fallback_to_offline_on_503(tmp_path):
    """
    Verify total 503 outage across all 4 models seamlessly falls back to offline_clipper.py
    and finishes at 100%.
    """
    jm = JobManager()
    project_id = 7777

    # Ensure Project exists in DB
    with Session(engine) as session:
        proj = Project(
            id=project_id,
            title="503 Resilience Test",
            source_path=str(tmp_path / "dummy.mp4")
        )
        session.merge(proj)
        session.commit()

    # Create dummy audio file
    audio_path = tmp_path / "audio_16k.wav"
    audio_path.write_bytes(b"RIFF" + b"\x00" * 2000)

    # Cached transcript
    transcript_path = tmp_path / "transcript.json"
    sample_words = [
        {"word": "The", "start": 0.0, "end": 0.3},
        {"word": "future", "start": 0.35, "end": 0.8},
        {"word": "is", "start": 0.85, "end": 1.0},
        {"word": "now.", "start": 1.05, "end": 1.5},
    ]
    with open(transcript_path, "w") as f:
        json.dump(sample_words, f)

    with patch.object(jm, "_get_project_dir", return_value=tmp_path), \
         patch("app.services.job_manager.extract_viral_clips", side_effect=GeminiUnavailableError("All models 503")), \
         patch("app.services.job_manager.get_persisted_settings", return_value={
             "gemini_enabled": True,
             "gemini_api_key": "test-api-key",
             "auto_fallback_offline": True,
             "gemini_analysis_mode": "transcript"
         }), \
         patch("app.services.job_manager.extract_viral_clips_offline") as mock_offline:

        mock_offline.return_value = [{
            "start_time": 0.0,
            "end_time": 1.5,
            "duration": 1.5,
            "virality_score": 88,
            "hook_score": 85,
            "retention_score": 85,
            "emotional_score": 80,
            "standalone_score": 85,
            "viral_rationale": "Offline fallback detected",
            "hook_headline": "THE FUTURE IS NOW",
            "cold_open_quote": "The future is now.",
            "youtube_titles": ["Future Is Now #Shorts"],
            "youtube_description": "Offline extracted #Shorts",
            "pinned_comment": "Thoughts?",
            "analysis_source": "offline_fallback"
        }]

        # Create job
        jm.create_job(project_id, "503 Resilience Test", str(tmp_path / "dummy.mp4"))

        # Run pipeline
        with patch("app.services.job_manager.probe_video", return_value={"duration": 10.0, "width": 1920, "height": 1080, "fps": 30.0}):
            jm.run_pipeline(project_id)

        progress = jm.get_progress(project_id)
        assert progress["status"] == "finished"
        assert progress["percent"] == 100
        mock_offline.assert_called_once()

def test_transcript_first_skips_proxy_encoding(tmp_path):
    """
    Verify transcript-first mode skips 480p proxy encoding completely.
    """
    jm = JobManager()
    project_id = 6666

    with Session(engine) as session:
        proj = Project(
            id=project_id,
            title="Transcript First Test",
            source_path=str(tmp_path / "dummy.mp4")
        )
        session.merge(proj)
        session.commit()

    audio_path = tmp_path / "audio_16k.wav"
    audio_path.write_bytes(b"RIFF" + b"\x00" * 2000)

    transcript_path = tmp_path / "transcript.json"
    with open(transcript_path, "w") as f:
        json.dump([{"word": "Hi", "start": 0.0, "end": 0.5}], f)

    with patch.object(jm, "_get_project_dir", return_value=tmp_path), \
         patch("app.services.job_manager.create_gemini_proxy") as mock_create_proxy, \
         patch("app.services.job_manager.extract_viral_clips", return_value=[]), \
         patch("app.services.job_manager.get_persisted_settings", return_value={
             "gemini_enabled": True,
             "gemini_api_key": "test-api-key",
             "gemini_analysis_mode": "transcript"
         }), \
         patch("app.services.job_manager.probe_video", return_value={"duration": 10.0, "width": 1920, "height": 1080, "fps": 30.0}):

        jm.create_job(project_id, "Transcript First Test", str(tmp_path / "dummy.mp4"))
        jm.run_pipeline(project_id)

        # Proxy encoding must NOT have been called!
        mock_create_proxy.assert_not_called()

def test_api_test_gemini_endpoint_direct():
    """
    Verify test_gemini_models_endpoint returns 4 model diagnostics.
    """
    mock_results = [
        {"model": "gemini-3.5-flash-lite", "status": "OK", "latency_ms": 150, "detail": "Operational"},
        {"model": "gemini-3.6-flash", "status": "OK", "latency_ms": 180, "detail": "Operational"},
        {"model": "gemini-4-hydrogen", "status": "503 High Demand", "latency_ms": 220, "detail": "Overloaded"},
        {"model": "gemini-2.5-flash-lite", "status": "OK", "latency_ms": 210, "detail": "Operational"}
    ]

    with patch("app.main.test_all_gemini_models", return_value=mock_results):
        data = api_test_gemini_models_endpoint(RequestTestGemini(api_key="test-api-key"))
        assert data["status"] == "completed"
        assert len(data["models"]) == 4
        assert data["models"][0]["model"] == "gemini-3.5-flash-lite"
        assert data["models"][0]["status"] == "OK"
        assert data["models"][2]["status"] == "503 High Demand"
