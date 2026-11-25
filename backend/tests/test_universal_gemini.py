import json
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

from app.config import settings
from app.models import GeminiAnalysisResponse
from app.services.gemini_clipper import (
    GeminiAuthError,
    GeminiUnavailableError,
    _invoke_model_compatible,
    extract_viral_clips,
    get_model_cascade,
    list_available_gemini_models,
)
from app.services.job_manager import get_persisted_settings


VALID_SAMPLE_CLIPS = {
    "clips": [
        {
            "start_time": "00:05",
            "end_time": "00:38",
            "start_seconds": 5.0,
            "end_seconds": 38.0,
            "virality_score": 94,
            "hook_score": 91,
            "retention_score": 89,
            "emotional_score": 87,
            "standalone_score": 92,
            "viral_rationale": "Immediate high-stakes hook followed by crisp payoff.",
            "hook_headline": "THE HIDDEN LEVER",
            "cold_open_quote": "Most creators miss this in the first three seconds.",
            "youtube_titles": [
                "The 3-Second Hook Rule #Shorts",
                "Why Creators Miss This #Shorts",
                "Secret Retention Lever #Shorts",
            ],
            "youtube_description": "Full breakdown of the retention lever. #Shorts #Editing",
            "pinned_comment": "Have you tested this hook style yet?",
        }
    ]
}


def _make_model(name: str, actions=None, legacy_methods=None, display_name: str = ""):
    ns = SimpleNamespace(
        name=name,
        display_name=display_name or name.removeprefix("models/"),
        description=f"Test model {name}",
    )
    if actions is not None:
        ns.supported_actions = actions
    if legacy_methods is not None:
        ns.supported_generation_methods = legacy_methods
    return ns


def test_list_available_gemini_models_filters_and_strips_prefix():
    """
    1. list_available_gemini_models filters client.models.list() to generateContent models,
       strips 'models/' prefix, and excludes TTS/image/embedding/Veo/Live models.
    """
    mock_models = [
        _make_model("models/gemma-3-27b-it", legacy_methods=["generateContent"]),
        _make_model("models/gemini-4-hydrogen", actions=["generateContent"]),
        _make_model("models/gemini-3.8-flash", actions=["generateContent"]),
        _make_model("models/gemini-2.5-flash-preview-tts", actions=["generateContent"]),
        _make_model("models/gemini-2.5-flash-image", actions=["generateContent"]),
        _make_model("models/text-embedding-004", actions=["embedContent"]),
        _make_model("models/veo-2.0-generate-001", actions=["generateContent"]),
        _make_model("models/imagen-3.0-generate-002", actions=["generateContent"]),
        _make_model("models/gemini-2.0-flash-live-001", actions=["generateContent"]),
    ]

    mock_client = MagicMock()
    mock_client.models.list.return_value = mock_models

    with patch("google.genai.Client", return_value=mock_client):
        discovered = list_available_gemini_models("test-api-key")

    ids = [m["id"] for m in discovered]
    assert ids == ["gemini-3.8-flash", "gemini-4-hydrogen", "gemma-3-27b-it"]
    assert all(not mid.startswith("models/") for mid in ids)


def test_invoke_model_compatible_falls_back_on_http_400():
    """
    2. _invoke_model_compatible falls back cleanly from structured response_schema
       (mocked HTTP 400 INVALID_ARGUMENT) to plain JSON extraction on the same model.
    """
    call_configs = []
    plain_response_text = f"Here are the viral moments:\n```json\n{json.dumps(VALID_SAMPLE_CLIPS)}\n```"

    def fake_generate_content(model, contents, config=None):
        call_configs.append((model, config))
        if getattr(config, "response_schema", None) is not None:
            raise Exception("400 INVALID_ARGUMENT: response_schema is not enabled for this model")
        return SimpleNamespace(text=plain_response_text)

    mock_client = MagicMock()
    mock_client.models.generate_content.side_effect = fake_generate_content

    result_json = _invoke_model_compatible(
        client=mock_client,
        model_id="models/gemini-4-neon",
        contents=["Analyze transcript"],
        system_instruction="You are a virality director.",
    )

    # Same model called twice with "models/" stripped
    assert [m for m, _ in call_configs] == ["gemini-4-neon", "gemini-4-neon"]
    # Attempt A had response_schema, Attempt B did not
    assert getattr(call_configs[0][1], "response_schema", None) is GeminiAnalysisResponse
    assert getattr(call_configs[1][1], "response_schema", None) is None
    assert getattr(call_configs[1][1], "response_mime_type", None) is None
    assert getattr(call_configs[1][1], "system_instruction", None) is None

    validated = GeminiAnalysisResponse.model_validate_json(result_json)
    assert len(validated.clips) == 1
    assert validated.clips[0].hook_headline == "THE HIDDEN LEVER"


def test_custom_model_tried_first_in_cascade():
    """
    3. Any custom user-specified model string ('gemini-custom-test-model') is tried first
       before discovered fallback models and baseline defaults.
    """
    attempted_models = []
    mock_models = [
        _make_model("models/gemini-3.8-flash", actions=["generateContent"]),
        _make_model("models/gemini-4-hydrogen", actions=["generateContent"]),
    ]

    def fake_generate_content(model, contents, config=None):
        attempted_models.append(model)
        return SimpleNamespace(text=json.dumps(VALID_SAMPLE_CLIPS))

    mock_client = MagicMock()
    mock_client.models.list.return_value = mock_models
    mock_client.models.generate_content.side_effect = fake_generate_content

    with patch.object(settings, "GEMINI_API_KEY", "test-api-key"), \
         patch("google.genai.Client", return_value=mock_client):
        cascade = get_model_cascade("gemini-custom-test-model", api_key="test-api-key")
        assert cascade[0] == "gemini-custom-test-model"
        assert "gemini-3.8-flash" in cascade

        clips = extract_viral_clips(
            transcript_words=[
                {"word": "Hook", "start": 0.0, "end": 0.4},
                {"word": "now.", "start": 0.5, "end": 1.0},
            ],
            gemini_analysis_mode="transcript",
            preferred_model="gemini-custom-test-model",
            clip_count=1,
        )

    assert attempted_models[0] == "gemini-custom-test-model"
    assert clips[0]["analysis_source"] == "gemini:gemini-custom-test-model"


def test_invalid_api_key_raises_auth_error_not_503():
    """
    4. Invalid API key raises GeminiAuthError instead of logging a false 503 outage.
    """
    mock_client = MagicMock()
    mock_client.models.list.side_effect = Exception("401 UNAUTHENTICATED: API_KEY_INVALID")

    with patch.object(settings, "GEMINI_API_KEY", "test-api-key"), \
         patch("google.genai.Client", return_value=mock_client):
        with pytest.raises(GeminiAuthError) as exc_info:
            extract_viral_clips(
                transcript_words=[{"word": "Hello", "start": 0.0, "end": 0.5}],
                preferred_model="gemini-4-hydrogen",
                gemini_analysis_mode="transcript",
            )
    assert "503" not in str(exc_info.value)

    # Also verify non-503 failure does not mislabel failure as "503/429 high demand"
    logs = []
    mock_client_404 = MagicMock()
    mock_client_404.models.list.return_value = []
    mock_client_404.models.generate_content.side_effect = Exception("404 NOT_FOUND: model removed")

    with patch.object(settings, "GEMINI_API_KEY", "test-api-key"), \
         patch("google.genai.Client", return_value=mock_client_404):
        with pytest.raises(GeminiUnavailableError) as unavail_exc:
            extract_viral_clips(
                transcript_words=[{"word": "Hello", "start": 0.0, "end": 0.5}],
                preferred_model="gemini-4-hydrogen",
                gemini_analysis_mode="transcript",
                on_log=lambda _stage, msg: logs.append(msg),
            )
    assert "503" not in unavail_exc.value.message
    assert any("HTTP 404" in m for m in logs)


def test_legacy_clipship_key_migration_and_gemini_models_endpoint(tmp_path, monkeypatch):
    """
    Verify %APPDATA%/ClipShip/settings.json key migration into STORAGE_DIR/settings.json
    and /api/system/gemini-models endpoint merging discovered + curated + active custom model.
    """
    appdata_dir = tmp_path / "appdata"
    legacy_dir = appdata_dir / "ClipShip"
    legacy_dir.mkdir(parents=True)
    legacy_settings = {"gemini_api_key": "test-api-key"}
    (legacy_dir / "settings.json").write_text(json.dumps(legacy_settings), encoding="utf-8")

    data_dir = tmp_path / "lost_heaven_data"
    data_dir.mkdir(parents=True)
    (data_dir / "settings.json").write_text(
        json.dumps({"gemini_api_key": "", "gemini_model": "gemini-4-neon"}),
        encoding="utf-8",
    )

    monkeypatch.setenv("APPDATA", str(appdata_dir))
    monkeypatch.setattr(settings, "STORAGE_DIR", str(data_dir))

    migrated = get_persisted_settings()
    assert migrated["gemini_api_key"] == "test-api-key"
    # Verify persisted atomically to target data dir
    reloaded = json.loads((data_dir / "settings.json").read_text(encoding="utf-8"))
    assert reloaded["gemini_api_key"] == "test-api-key"

    from app.main import get_gemini_models_endpoint

    mock_models = [_make_model("models/gemma-3-12b-it", actions=["generateContent"])]
    mock_client = MagicMock()
    mock_client.models.list.return_value = mock_models

    with patch("google.genai.Client", return_value=mock_client):
        resp = get_gemini_models_endpoint(api_key="test-api-key")

    model_ids = [m["id"] for m in resp["models"]]
    assert "gemma-3-12b-it" in model_ids
    assert "gemini-4-neon" in model_ids
    assert "gemini-3.5-flash-lite" in model_ids
    assert resp["discovered_count"] == 1
