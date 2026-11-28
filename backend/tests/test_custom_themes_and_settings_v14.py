"""Phase 1 tests: 18+ Themes, Custom Theme persistence, Render & Audio Mastering defaults, and Reset Defaults."""

import json


def test_custom_themes_and_render_defaults_persistence(tmp_path, monkeypatch):
    from app.config import settings
    import app.main as main_mod
    from app.models import SettingsUpdateRequest

    isolated_storage = tmp_path / "storage"
    isolated_storage.mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr(settings, "STORAGE_DIR", str(isolated_storage))

    for attr in (
        "VIDEO_ENCODER",
        "DEFAULT_RESOLUTION",
        "DEFAULT_FPS",
        "LOUDNESS_TARGET",
        "AUDIO_NORMALIZATION_ENABLED",
        "AUTO_CLEANUP_DAYS",
        "HARDWARE_ACCELERATION",
    ):
        monkeypatch.setattr(settings, attr, getattr(settings, attr))
    for k in ("active_theme", "default_fps", "loudness_target", "auto_cleanup_days", "watch_folder_enabled"):
        monkeypatch.setitem(main_mod.RUNTIME_SETTINGS, k, main_mod.RUNTIME_SETTINGS.get(k))

    custom_theme = {
        "id": "custom-velvet-amber",
        "name": "Velvet Amber",
        "description": "Warm grading suite",
        "isDark": True,
        "category": "custom",
        "isCustom": True,
        "bgCanvas": "#09080A",
        "bgSurface": "#141117",
        "bgElevated": "#1E1922",
        "accentPrimary": "#F59E0B",
        "accentSecondary": "#EC4899",
        "textPrimary": "#FAF8FC",
        "borderSubtle": "#2E2534",
    }

    req = SettingsUpdateRequest(
        active_theme="custom-velvet-amber",
        custom_themes=[custom_theme],
        video_encoder="hevc_nvenc",
        default_resolution="2160x3840",
        default_fps="60",
        loudness_target="-16 LUFS",
        audio_normalization_enabled=False,
        auto_cleanup_days=14,
    )
    res = main_mod.update_studio_settings(req)
    assert res["status"] == "saved"

    fetched = main_mod.get_studio_settings()
    assert fetched["active_theme"] == "custom-velvet-amber"
    assert len(fetched["custom_themes"]) == 1
    assert fetched["custom_themes"][0]["name"] == "Velvet Amber"
    assert fetched["video_encoder"] == "hevc_nvenc"
    assert fetched["default_resolution"] == "2160x3840"
    assert fetched["default_fps"] == "60"
    assert fetched["loudness_target"] == "-16 LUFS"
    assert fetched["audio_normalization_enabled"] is False
    assert fetched["auto_cleanup_days"] == 14

    sys_fetched = main_mod.get_system_settings()
    assert sys_fetched["active_theme"] == "custom-velvet-amber"
    assert len(sys_fetched["custom_themes"]) == 1
    assert sys_fetched["loudness_target"] == "-16 LUFS"
    assert sys_fetched["auto_cleanup_days"] == 14

    settings_file = isolated_storage / "settings.json"
    assert settings_file.exists()
    persisted = json.loads(settings_file.read_text(encoding="utf-8"))
    assert persisted["active_theme"] == "custom-velvet-amber"
    assert persisted["custom_themes"][0]["id"] == "custom-velvet-amber"


def test_reset_studio_settings_preserves_keys_and_custom_themes(tmp_path, monkeypatch):
    from app.config import settings
    import app.main as main_mod
    from app.models import SettingsUpdateRequest

    isolated_storage = tmp_path / "storage_reset"
    isolated_storage.mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr(settings, "STORAGE_DIR", str(isolated_storage))

    for attr in (
        "VIDEO_ENCODER",
        "DEFAULT_RESOLUTION",
        "DEFAULT_FPS",
        "LOUDNESS_TARGET",
        "AUDIO_NORMALIZATION_ENABLED",
        "AUTO_CLEANUP_DAYS",
        "HARDWARE_ACCELERATION",
        "WHISPER_MODEL",
        "GEMINI_API_KEY",
        "GEMINI_ANALYSIS_MODE",
        "AUTO_FALLBACK_OFFLINE",
    ):
        monkeypatch.setattr(settings, attr, getattr(settings, attr))
    for k in ("active_theme", "default_fps", "loudness_target", "auto_cleanup_days", "watch_folder_enabled"):
        monkeypatch.setitem(main_mod.RUNTIME_SETTINGS, k, main_mod.RUNTIME_SETTINGS.get(k))

    custom_theme = {
        "id": "custom-neon-mint",
        "name": "Neon Mint",
        "bgCanvas": "#080C0B",
        "bgSurface": "#101715",
        "bgElevated": "#18221F",
        "accentPrimary": "#10B981",
        "accentSecondary": "#06B6D4",
        "textPrimary": "#F0FDF4",
        "borderSubtle": "#1E3A31",
    }

    main_mod.update_studio_settings(
        SettingsUpdateRequest(
            gemini_api_key="AIzaSyDummyKeyForResetPreservation123",
            active_theme="custom-neon-mint",
            custom_themes=[custom_theme],
            video_encoder="libx265",
            default_fps="24",
            loudness_target="-20 LUFS",
            audio_normalization_enabled=False,
            auto_cleanup_days=90,
            hardware_acceleration="cpu",
        )
    )

    reset_res = main_mod.reset_studio_settings()
    assert reset_res["status"] == "reset"
    s = reset_res["settings"]
    assert s["active_theme"] == "Hicksfield"
    assert s["hardware_acceleration"] == "auto"
    assert s["video_encoder"] == "auto"
    assert s["default_resolution"] == "1080x1920"
    assert s["default_fps"] == "30"
    assert s["loudness_target"] == "-14 LUFS"
    assert s["audio_normalization_enabled"] is True
    assert s["auto_cleanup_days"] == 30
    assert len(s["custom_themes"]) == 1
    assert s["custom_themes"][0]["id"] == "custom-neon-mint"

    saved = json.loads((isolated_storage / "settings.json").read_text(encoding="utf-8"))
    assert saved["gemini_api_key"] == "AIzaSyDummyKeyForResetPreservation123"
    assert len(saved["custom_themes"]) == 1
