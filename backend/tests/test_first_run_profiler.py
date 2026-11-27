"""
Verification suite: First-Run Hardware Spec Detector, Prerequisite Downloader & Setup Wizard.

Tests:
1. build_first_run_recommendations assigns ultra_gpu (large-v3-turbo + h264_nvenc) for a
   mocked 12 GB RTX GPU, mid_gpu, standard_cpu, and lightweight_cpu (tiny + libx264 + 540p)
   for a mocked 8 GB CPU-only laptop.
2. profile_system_specs falls back safely via stdlib even if optional packages are unavailable.
3. GET /api/system/onboarding and POST /api/system/complete-onboarding persist recommended
   settings and set first_run_completed=True in settings.json.
"""

import builtins
import json
import os
import sys
from pathlib import Path

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

import app.main as main_mod
from app.config import settings
from app.models import CompleteOnboardingRequest
from app.services.system_profiler import (
    build_first_run_recommendations,
    profile_system_specs,
)


def test_tiered_recommendations_ultra_gpu_and_lightweight_cpu():
    # 1. Mocked 12 GB RTX GPU workstation -> ultra_gpu
    specs_ultra = {
        "os": {"name": "Windows", "release": "11", "arch": "x86_64"},
        "cpu": {"model": "AMD Ryzen 9 7950X", "physical_cores": 16, "logical_threads": 32},
        "ram": {"total_gb": 64.0, "available_gb": 48.0},
        "gpu": {"name": "NVIDIA GeForce RTX 5070", "has_cuda": True, "vram_mb": 12288, "vram_gb": 12.0},
        "encoders": {"available": ["h264_nvenc", "libx264"], "preferred": "h264_nvenc"},
        "tools": {"ffmpeg": True, "ffprobe": True, "yt_dlp": True},
        "packages": {"faster_whisper": True, "mediapipe": True, "cv2": True, "scipy": True},
        "cached_whisper_models": [],
        "has_cuda": True,
        "gpu_vram_gb": 12.0,
        "gpu_name": "NVIDIA GeForce RTX 5070",
        "ram_total_gb": 64.0,
        "available_encoders": ["h264_nvenc", "libx264"],
        "preferred_encoder": "h264_nvenc",
    }
    rec_ultra = build_first_run_recommendations(specs_ultra)
    assert rec_ultra["tier"] == "ultra_gpu"
    assert rec_ultra["recommended_settings"]["whisper_model"] == "large-v3-turbo"
    assert rec_ultra["recommended_settings"]["whisper_device"] == "cuda"
    assert rec_ultra["recommended_settings"]["whisper_compute_type"] == "float16"
    assert rec_ultra["recommended_settings"]["whisper_batch_size"] == 16
    assert rec_ultra["recommended_settings"]["video_encoder"] == "h264_nvenc"
    assert rec_ultra["recommended_settings"]["default_resolution"] == "1080p"

    # 2. Mocked 8 GB CPU-only laptop -> lightweight_cpu
    specs_light = {
        "os": {"name": "Windows", "release": "10", "arch": "x86_64"},
        "cpu": {"model": "Intel Core i5-1035G1", "physical_cores": 4, "logical_threads": 8},
        "ram": {"total_gb": 8.0, "available_gb": 4.2},
        "gpu": {"name": "None", "has_cuda": False, "vram_mb": 0, "vram_gb": 0.0},
        "encoders": {"available": ["libx264"], "preferred": "libx264"},
        "tools": {"ffmpeg": False, "ffprobe": False, "yt_dlp": True},
        "packages": {"faster_whisper": True, "mediapipe": True, "cv2": True, "scipy": True},
        "cached_whisper_models": [],
        "has_cuda": False,
        "gpu_vram_gb": 0.0,
        "gpu_name": "None",
        "ram_total_gb": 8.0,
        "available_encoders": ["libx264"],
        "preferred_encoder": "libx264",
    }
    rec_light = build_first_run_recommendations(specs_light)
    assert rec_light["tier"] == "lightweight_cpu"
    assert rec_light["recommended_settings"]["whisper_model"] == "tiny"
    assert rec_light["recommended_settings"]["whisper_device"] == "cpu"
    assert rec_light["recommended_settings"]["whisper_compute_type"] == "int8"
    assert rec_light["recommended_settings"]["whisper_batch_size"] == 2
    assert rec_light["recommended_settings"]["video_encoder"] == "libx264"
    assert rec_light["recommended_settings"]["default_resolution"] == "540p"

    dl_map = {d["id"]: d for d in rec_light["downloads_needed"]}
    assert dl_map["ffmpeg"]["installed"] is False
    assert dl_map["ffmpeg"]["required"] is True
    assert dl_map["whisper_model"]["model_id"] == "tiny"
    assert dl_map["whisper_model"]["size_mb"] == 75
    assert dl_map["whisper_model"]["installed"] is False


def test_profile_system_specs_safe_without_optional_libs(tmp_path, monkeypatch):
    storage_dir = tmp_path / "storage"
    storage_dir.mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr(settings, "STORAGE_DIR", str(storage_dir))

    real_import = builtins.__import__

    def _mock_import(name, *args, **kwargs):
        if name in ("psutil", "torch", "ctranslate2"):
            raise ImportError(f"Simulated missing optional dependency: {name}")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", _mock_import)

    specs = profile_system_specs()
    assert specs["cpu"]["logical_threads"] >= 1
    assert specs["ram"]["total_gb"] >= 0.0
    assert isinstance(specs["encoders"]["available"], list)
    assert "libx264" in specs["encoders"]["available"]
    assert "ffmpeg" in specs["tools"]


def test_onboarding_and_complete_onboarding_persists_settings_json(tmp_path, monkeypatch):
    storage_dir = tmp_path / "storage"
    storage_dir.mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr(settings, "STORAGE_DIR", str(storage_dir))

    # Preserve runtime settings across test
    saved_runtime = dict(main_mod.RUNTIME_SETTINGS)
    saved_model = settings.WHISPER_MODEL
    saved_dev = settings.WHISPER_DEVICE
    saved_comp = settings.WHISPER_COMPUTE_TYPE
    saved_batch = settings.WHISPER_BATCH_SIZE
    saved_enc = settings.VIDEO_ENCODER
    saved_res = settings.DEFAULT_RESOLUTION
    saved_fps = settings.VISION_SCAN_FPS
    saved_ai_provider = settings.AI_PROVIDER
    saved_hw = settings.HARDWARE_ACCELERATION
    saved_key = settings.GEMINI_API_KEY
    try:
        # Initially settings.json doesn't exist -> first_run_completed must be False
        onboarding = main_mod.get_system_onboarding_endpoint()
        assert onboarding["first_run_completed"] is False
        assert onboarding["tier"] in ("ultra_gpu", "mid_gpu", "standard_cpu", "lightweight_cpu")
        assert len(onboarding["downloads_needed"]) == 3

        req = CompleteOnboardingRequest(
            whisper_model="small",
            whisper_device="cuda",
            whisper_compute_type="int8_float16",
            whisper_batch_size=8,
            video_encoder="h264_nvenc",
            default_resolution="1080p",
            vision_scan_fps=1.5,
            ai_mode="offline",
            hardware_acceleration="gpu",
        )
        res = main_mod.complete_onboarding_endpoint(req)
        assert res["status"] == "completed"
        assert res["first_run_completed"] is True

        settings_file = storage_dir / "settings.json"
        assert settings_file.exists()
        saved_disk = json.loads(settings_file.read_text(encoding="utf-8"))
        assert saved_disk["first_run_completed"] is True
        assert saved_disk["whisper_model"] == "small"
        assert saved_disk["whisper_device"] == "cuda"
        assert saved_disk["whisper_compute_type"] == "int8_float16"
        assert saved_disk["whisper_batch_size"] == 8
        assert saved_disk["video_encoder"] == "h264_nvenc"
        assert saved_disk["default_resolution"] == "1080p"
        assert saved_disk["ai_mode"] == "offline"

        # Subsequent GET /api/system/onboarding sees first_run_completed=True
        onboarding_after = main_mod.get_system_onboarding_endpoint()
        assert onboarding_after["first_run_completed"] is True
    finally:
        main_mod.RUNTIME_SETTINGS.clear()
        main_mod.RUNTIME_SETTINGS.update(saved_runtime)
        settings.WHISPER_MODEL = saved_model
        settings.WHISPER_DEVICE = saved_dev
        settings.WHISPER_COMPUTE_TYPE = saved_comp
        settings.WHISPER_BATCH_SIZE = saved_batch
        settings.VIDEO_ENCODER = saved_enc
        settings.DEFAULT_RESOLUTION = saved_res
        settings.VISION_SCAN_FPS = saved_fps
        settings.AI_PROVIDER = saved_ai_provider
        settings.HARDWARE_ACCELERATION = saved_hw
        settings.GEMINI_API_KEY = saved_key
