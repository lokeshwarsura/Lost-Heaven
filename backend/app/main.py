import os
import re
import json
import time
import asyncio
import logging
import threading
from pathlib import Path
from typing import Optional, List, Dict, Any

from fastapi import FastAPI, UploadFile, File, Form, Depends, HTTPException, BackgroundTasks, Body
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse, JSONResponse
from sqlmodel import Session, select
from sse_starlette.sse import EventSourceResponse

from app.config import settings
from app.database import init_db, get_session
from app.models import (
    Project, ViralClip, BrandPreset, CaptionStylePreset,
    YouTubeImportRequest, UpdateClipRequest, UpdateClipMetadataRequest, PublishYouTubeRequest,
    RenderClipRequest, TrimClipRequest, UpdateSegmentsRequest, MergeClipsRequest,
    DiarizeClipRequest, UpdateSpeakersRequest, ExtractCalloutsRequest,
    TranslateClipRequest, DubClipRequest, CompileTrailerRequest, GenerateThumbnailRequest,
    GenerateChaptersRequest, ImportAnalyticsCsvRequest, ExportEvent, UpdateWebcamRegionRequest,
    FramingOverrideRequest,
    SettingsUpdateRequest, PreviewFrameRequest, PreviewFrameResponse,
    CreateBrandPresetRequest, CreateCaptionStyleRequest,
    ProvideKeyRequest, RetryProjectRequest, TestGeminiRequest
)
from app.services.proxy import probe_video, create_gemini_proxy, get_ffmpeg_cmd
from app.services.gemini_clipper import (
    extract_viral_clips,
    verify_gemini_api_key,
    test_all_gemini_models,
    list_available_gemini_models,
    DEFAULT_MODEL_CASCADE
)
from app.services.transcriber import transcribe_clip, filter_filler_words
from app.services.renderer import render_clip_ultra, RenderSupersededError
from app.services.preview_frame import render_exact_preview_frame
from app.services.timeline_editor import extract_waveform_bins
from app.services.broll_engine import suggest_broll_cutaways
from app.services.nle_exporter import export_clip_nle
from app.services.hook_generator import generate_hook_variants
from app.services.social_copywriter import generate_multi_platform_copy
from app.services.publish_pack import build_publish_pack_zip
from app.services.retention_auditor import audit_clip_retention
from app.services.diarizer import diarize_clip_speakers
from app.services.callout_engine import extract_callout_cards
from app.services.translator import translate_clip_transcript
from app.services.dubbing_engine import dub_clip_audio
from app.services.trailer_compiler import compile_project_trailer
from app.services.thumbnail_studio import generate_depth_thumbnail
from app.services.clip_merger import build_merged_clip_model
from app.services.profile_manager import list_profiles, create_profile, delete_profile
from app.services.batch_queue import list_batch_queue, enqueue_batch_project, scan_watch_folder
from fastapi.responses import Response
from app.services.hardware import detect_hardware_specs
from app.services.diagnostics import get_system_check, get_machine_specs, update_ytdlp_package
from app.services.model_manager import list_whisper_models, download_whisper_model, delete_whisper_model
from app.services.captions import CAPTION_STYLES_DEF, FONT_MAP
from app.services.youtube_uploader import publish_video_to_youtube, is_youtube_authenticated
from app.services.job_manager import job_manager, get_persisted_settings, save_persisted_settings

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("lost_heaven_studio")

app = FastAPI(
    title="Lost Heaven Studio API",
    version="4.0.0",
    description="Local-First AI Video Clipping & Post-Production Studio"
)

# CORS
app.add_middleware(
    CORSMiddleware,
    allow_origin_regex=r"^https?://(localhost|127\.0\.0\.1|192\.168\.\d+\.\d+|10\.\d+\.\d+\.\d+|172\.(1[6-9]|2\d|3[0-1])\.\d+\.\d+)(:\d+)?$",
    allow_origins=[
        "http://localhost:5173",
        "http://127.0.0.1:5173",
        "http://localhost:8000",
        "http://127.0.0.1:8000"
    ],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# In-Memory Runtime Settings State
RUNTIME_SETTINGS = {
    "gemini_boost_enabled": True,
    "active_theme": "Hicksfield",
    "hardware_acceleration": "auto",
    "gemini_model": settings.GEMINI_MODEL,
    "whisper_model": settings.WHISPER_MODEL
}

# SSE Event Queues
event_subscribers: List[asyncio.Queue] = []

async def broadcast_event(event_type: str, data: Dict[str, Any]):
    payload = json.dumps({"type": event_type, "data": data})
    for queue in list(event_subscribers):
        try:
            await queue.put(payload)
        except Exception:
            pass

# Ensure Storage Directories & Static Mounts
storage_path = Path(settings.STORAGE_DIR)
storage_renders = storage_path / "renders"
storage_masters = storage_path / "masters"
storage_previews = storage_path / "previews"
storage_assets = storage_path / "assets"
storage_fonts = storage_path / "fonts"
storage_models = storage_path / "models"
storage_projects = storage_path / "projects"
storage_thumbnails = storage_path / "thumbnails"
storage_luts = storage_path / "luts"

for p in [storage_renders, storage_masters, storage_previews, storage_assets, storage_fonts, storage_models, storage_projects, storage_thumbnails, storage_luts]:
    p.mkdir(parents=True, exist_ok=True)

app.mount("/media/renders", StaticFiles(directory=str(storage_renders)), name="renders")
app.mount("/media/masters", StaticFiles(directory=str(storage_masters)), name="masters")
app.mount("/media/previews", StaticFiles(directory=str(storage_previews)), name="previews")
app.mount("/media/assets", StaticFiles(directory=str(storage_assets)), name="assets")
app.mount("/media/thumbnails", StaticFiles(directory=str(storage_thumbnails)), name="thumbnails")
app.mount("/media/luts", StaticFiles(directory=str(storage_luts)), name="luts")

def _hydrate_settings_from_disk() -> None:
    """Hydrate runtime Settings & RUNTIME_SETTINGS from persisted settings.json on startup."""
    persisted = get_persisted_settings()
    if persisted.get("gemini_api_key") and str(persisted["gemini_api_key"]).strip():
        k = str(persisted["gemini_api_key"]).strip()
        settings.GEMINI_API_KEY = k
        os.environ["GEMINI_API_KEY"] = k
    if persisted.get("gemini_model"):
        settings.GEMINI_MODEL = str(persisted["gemini_model"])
        RUNTIME_SETTINGS["gemini_model"] = settings.GEMINI_MODEL
    if persisted.get("gemini_fallback_model"):
        settings.GEMINI_FALLBACK_MODEL = str(persisted["gemini_fallback_model"])
    if persisted.get("gemini_analysis_mode"):
        settings.GEMINI_ANALYSIS_MODE = str(persisted["gemini_analysis_mode"])
    if "auto_fallback_offline" in persisted:
        settings.AUTO_FALLBACK_OFFLINE = bool(persisted["auto_fallback_offline"])
    if persisted.get("ai_provider"):
        settings.AI_PROVIDER = str(persisted["ai_provider"])
    if persisted.get("whisper_model"):
        settings.WHISPER_MODEL = str(persisted["whisper_model"])
        RUNTIME_SETTINGS["whisper_model"] = settings.WHISPER_MODEL
    if persisted.get("hardware_acceleration"):
        settings.HARDWARE_ACCELERATION = str(persisted["hardware_acceleration"])
        RUNTIME_SETTINGS["hardware_acceleration"] = settings.HARDWARE_ACCELERATION
    if "gemini_enabled" in persisted:
        RUNTIME_SETTINGS["gemini_boost_enabled"] = bool(persisted["gemini_enabled"])
    if persisted.get("active_theme"):
        RUNTIME_SETTINGS["active_theme"] = str(persisted["active_theme"])


@app.on_event("startup")
def on_startup():
    init_db()
    _hydrate_settings_from_disk()
    job_manager.cleanup_orphan_jobs()
    logger.info("Database initialized, persisted settings hydrated, and Lost Heaven storage directories mounted.")

def format_bytes(b: int) -> str:
    if b < 1024:
        return f"{b} B"
    elif b < 1024**2:
        return f"{b / 1024:.1f} KB"
    elif b < 1024**3:
        return f"{b / (1024**2):.1f} MB"
    else:
        return f"{b / (1024**3):.2f} GB"

# -------------------------------------------------------------
# System Diagnostics & Diagnostics Suite Endpoints
# -------------------------------------------------------------

@app.get("/api/health")
def health_endpoint():
    """Health check endpoint returning 200 OK and model configurations."""
    return {
        "status": "ok",
        "app": "Lost Heaven Studio API",
        "gemini_model": settings.GEMINI_MODEL,
        "gemini_fallback_model": settings.GEMINI_FALLBACK_MODEL,
        "whisper_model": settings.WHISPER_MODEL
    }

@app.get("/api/system/check")
def system_check_endpoint():
    """8-point live health check: Video engine, features, encoder, whisper, speed, memory, free space."""
    return get_system_check()

@app.get("/api/system/info")
def system_info_endpoint():
    """Machine specifications, hardware capabilities, and bundled binary details."""
    return get_machine_specs()

@app.get("/api/system/machine")
def system_machine_endpoint():
    """Detailed machine specifications and bundled binaries report."""
    return get_machine_specs()

@app.get("/api/system-info")
def legacy_system_info():
    """Backward compatibility for bottom telemetry bar."""
    return detect_hardware_specs()

@app.post("/api/system/update-ytdlp")
def update_ytdlp_endpoint():
    """Update yt-dlp package in the active Python environment."""
    return update_ytdlp_package()

CURATED_DEFAULT_GEMINI_MODELS = [
    {"id": "gemini-3.5-flash-lite", "display_name": "gemini-3.5-flash-lite (Fastest / Recommended)", "description": "Fastest high-throughput 1M context default"},
    {"id": "gemini-3.8-flash", "display_name": "gemini-3.8-flash (Balanced Next-Gen)", "description": "High-speed Gemini 3.8 balanced multimodal"},
    {"id": "gemini-4-hydrogen", "display_name": "gemini-4-hydrogen (Maximum Reasoning)", "description": "Gemini 4 flagship viral blueprint engine"},
    {"id": "gemini-3.6-flash", "display_name": "gemini-3.6-flash (Multimodal)", "description": "Balanced multimodal model"},
    {"id": "gemini-4-carbon", "display_name": "gemini-4-carbon", "description": "Gemini 4 Carbon"},
    {"id": "gemini-4-neon", "display_name": "gemini-4-neon", "description": "Gemini 4 Neon"},
    {"id": "gemini-4-argon", "display_name": "gemini-4-argon", "description": "Gemini 4 Argon"},
    {"id": "gemini-3.1-pro-preview", "display_name": "gemini-3.1-pro-preview", "description": "Gemini 3.1 Pro Preview"},
    {"id": "gemini-2.5-flash-lite", "display_name": "gemini-2.5-flash-lite (Legacy High-Availability)", "description": "Legacy high-availability fallback"},
]


@app.get("/api/system/gemini-models")
def get_gemini_models_endpoint(api_key: Optional[str] = None):
    """
    Discover live generateContent models available to the user's Gemini API key,
    merged cleanly with curated 2026 baseline defaults so Settings always lists all options.
    """
    persisted = get_persisted_settings()
    key = (
        api_key
        or persisted.get("gemini_api_key")
        or settings.GEMINI_API_KEY
        or os.environ.get("GEMINI_API_KEY", "")
    ).strip()

    discovered: List[Dict[str, Any]] = []
    auth_error: Optional[str] = None
    if key and key != "your_gemini_api_key_here":
        try:
            discovered = list_available_gemini_models(key)
        except Exception as e:
            auth_error = str(e)[:140]

    # Merge discovered first (preserving priority sort), then curated defaults not yet listed
    seen = {m["id"] for m in discovered}
    merged = list(discovered)
    for d in CURATED_DEFAULT_GEMINI_MODELS:
        if d["id"] not in seen:
            seen.add(d["id"])
            merged.append(d)

    # Also ensure currently persisted custom model stays in the list
    active_model = str(persisted.get("gemini_model") or settings.GEMINI_MODEL or "").strip()
    if active_model and active_model not in seen:
        merged.insert(0, {"id": active_model, "display_name": f"{active_model} (Custom Selected)", "description": "Custom saved model"})

    return {
        "models": merged,
        "discovered_count": len(discovered),
        "auth_error": auth_error,
    }


@app.post("/api/system/test-gemini")
def test_gemini_models_endpoint(req: Optional[TestGeminiRequest] = None):
    """
    Live diagnostic test: Pings live-discovered (or fallback baseline) Gemini models
    and reports live latency and status (OK, 503 High Demand, 404 Unavailable, Invalid Key).
    """
    persisted = get_persisted_settings()
    api_key = (req.api_key if req and req.api_key else "").strip()
    active_key = api_key or str(persisted.get("gemini_api_key") or settings.GEMINI_API_KEY or "").strip()
    results = test_all_gemini_models(active_key or None)
    has_key = bool(active_key and active_key != "your_gemini_api_key_here")
    return {
        "status": "completed",
        "api_key_configured": has_key,
        "models": results,
        "default_cascade": DEFAULT_MODEL_CASCADE
    }

@app.get("/api/system/settings")
def get_system_settings():
    """Return runtime system settings including Gemini AI Boost and UI theme."""
    persisted = get_persisted_settings()
    active_key = str(persisted.get("gemini_api_key") or settings.GEMINI_API_KEY or os.environ.get("GEMINI_API_KEY", "")).strip()
    RUNTIME_SETTINGS["gemini_configured"] = bool(active_key and active_key != "your_gemini_api_key_here")
    RUNTIME_SETTINGS["gemini_model"] = persisted.get("gemini_model", settings.GEMINI_MODEL)
    RUNTIME_SETTINGS["gemini_fallback_model"] = persisted.get("gemini_fallback_model", settings.GEMINI_FALLBACK_MODEL)
    RUNTIME_SETTINGS["gemini_analysis_mode"] = persisted.get("gemini_analysis_mode", settings.GEMINI_ANALYSIS_MODE)
    RUNTIME_SETTINGS["auto_fallback_offline"] = persisted.get("auto_fallback_offline", settings.AUTO_FALLBACK_OFFLINE)
    RUNTIME_SETTINGS["ai_provider"] = persisted.get("ai_provider", settings.AI_PROVIDER)
    RUNTIME_SETTINGS["ollama_endpoint"] = persisted.get("ollama_endpoint", settings.OLLAMA_ENDPOINT)
    RUNTIME_SETTINGS["ollama_model"] = persisted.get("ollama_model", settings.OLLAMA_MODEL)
    RUNTIME_SETTINGS["whisper_model"] = persisted.get("whisper_model", settings.WHISPER_MODEL)
    gemini_enabled = persisted.get("gemini_enabled", RUNTIME_SETTINGS.get("gemini_boost_enabled", True))
    RUNTIME_SETTINGS["gemini_enabled"] = gemini_enabled
    RUNTIME_SETTINGS["gemini_boost_enabled"] = gemini_enabled
    raw_ai_mode = persisted.get("ai_mode")
    if raw_ai_mode in ("gemini", "offline"):
        eff_ai_mode = raw_ai_mode
    elif persisted.get("ai_provider") == "offline" or gemini_enabled is False:
        eff_ai_mode = "offline"
    elif RUNTIME_SETTINGS["gemini_configured"]:
        eff_ai_mode = "gemini"
    else:
        eff_ai_mode = "needs_api_key_or_offline_choice"
    RUNTIME_SETTINGS["ai_mode"] = raw_ai_mode
    RUNTIME_SETTINGS["effective_ai_mode"] = eff_ai_mode
    RUNTIME_SETTINGS["needs_api_key_or_offline_choice"] = (eff_ai_mode == "needs_api_key_or_offline_choice")
    try:
        from app.services.transcriber import get_whisper_runtime_info
        RUNTIME_SETTINGS["whisper_badge"] = get_whisper_runtime_info().get("badge", "CPU int8")
    except Exception:
        RUNTIME_SETTINGS["whisper_badge"] = "CPU int8"
    RUNTIME_SETTINGS["active_theme"] = persisted.get("active_theme", RUNTIME_SETTINGS.get("active_theme", "Hicksfield"))
    RUNTIME_SETTINGS["pexels_api_key"] = persisted.get("pexels_api_key", "")
    RUNTIME_SETTINGS["watch_folder_path"] = persisted.get("watch_folder_path", "")
    RUNTIME_SETTINGS["watch_folder_enabled"] = bool(persisted.get("watch_folder_enabled", False))
    RUNTIME_SETTINGS["model_cascade"] = DEFAULT_MODEL_CASCADE
    return RUNTIME_SETTINGS

@app.post("/api/system/settings")
def update_system_settings(req: Dict[str, Any]):
    """Update runtime system settings and persist to disk."""
    persisted = get_persisted_settings()
    if "gemini_boost_enabled" in req:
        val = bool(req["gemini_boost_enabled"])
        RUNTIME_SETTINGS["gemini_boost_enabled"] = val
        RUNTIME_SETTINGS["gemini_enabled"] = val
        persisted["gemini_enabled"] = val
        persisted["gemini_boost_enabled"] = val
    if "gemini_enabled" in req:
        val = bool(req["gemini_enabled"])
        RUNTIME_SETTINGS["gemini_boost_enabled"] = val
        RUNTIME_SETTINGS["gemini_enabled"] = val
        persisted["gemini_enabled"] = val
        persisted["gemini_boost_enabled"] = val
    if "active_theme" in req:
        theme = str(req["active_theme"])
        RUNTIME_SETTINGS["active_theme"] = theme
        persisted["active_theme"] = theme
    if "hardware_acceleration" in req:
        hw = str(req["hardware_acceleration"])
        RUNTIME_SETTINGS["hardware_acceleration"] = hw
        settings.HARDWARE_ACCELERATION = hw
        persisted["hardware_acceleration"] = hw
    if "gemini_api_key" in req:
        k = str(req["gemini_api_key"] or "").strip()
        settings.GEMINI_API_KEY = k
        os.environ["GEMINI_API_KEY"] = k
        persisted["gemini_api_key"] = k
    if "gemini_model" in req:
        m = str(req["gemini_model"] or "").strip().removeprefix("models/")
        if m:
            settings.GEMINI_MODEL = m
            RUNTIME_SETTINGS["gemini_model"] = m
            persisted["gemini_model"] = m
    if "gemini_fallback_model" in req:
        fm = str(req["gemini_fallback_model"])
        settings.GEMINI_FALLBACK_MODEL = fm
        persisted["gemini_fallback_model"] = fm
    if "gemini_analysis_mode" in req:
        am = str(req["gemini_analysis_mode"])
        settings.GEMINI_ANALYSIS_MODE = am
        persisted["gemini_analysis_mode"] = am
    if "auto_fallback_offline" in req:
        afo = bool(req["auto_fallback_offline"])
        settings.AUTO_FALLBACK_OFFLINE = afo
        persisted["auto_fallback_offline"] = afo
    if "ai_provider" in req:
        ap = str(req["ai_provider"])
        settings.AI_PROVIDER = ap
        persisted["ai_provider"] = ap
    if "ai_mode" in req:
        am_req = str(req["ai_mode"] or "").strip().lower()
        if am_req in ("gemini", "offline"):
            persisted["ai_mode"] = am_req
            RUNTIME_SETTINGS["ai_mode"] = am_req
            if am_req == "offline":
                persisted["ai_provider"] = "offline"
                settings.AI_PROVIDER = "offline"
                persisted["gemini_enabled"] = False
                persisted["gemini_boost_enabled"] = False
                RUNTIME_SETTINGS["gemini_enabled"] = False
                RUNTIME_SETTINGS["gemini_boost_enabled"] = False
            else:
                persisted["ai_provider"] = "gemini"
                settings.AI_PROVIDER = "gemini"
                persisted["gemini_enabled"] = True
                persisted["gemini_boost_enabled"] = True
                RUNTIME_SETTINGS["gemini_enabled"] = True
                RUNTIME_SETTINGS["gemini_boost_enabled"] = True
    if "ollama_endpoint" in req:
        oe = str(req["ollama_endpoint"])
        settings.OLLAMA_ENDPOINT = oe
        persisted["ollama_endpoint"] = oe
    if "ollama_model" in req:
        om = str(req["ollama_model"])
        settings.OLLAMA_MODEL = om
        persisted["ollama_model"] = om
    if "whisper_model" in req:
        w = str(req["whisper_model"])
        settings.WHISPER_MODEL = w
        persisted["whisper_model"] = w
    if "pexels_api_key" in req:
        pk = str(req["pexels_api_key"] or "").strip()
        persisted["pexels_api_key"] = pk
        RUNTIME_SETTINGS["pexels_api_key"] = pk
    if "watch_folder_path" in req:
        wf = str(req["watch_folder_path"] or "").strip()
        persisted["watch_folder_path"] = wf
        RUNTIME_SETTINGS["watch_folder_path"] = wf
    if "watch_folder_enabled" in req:
        we = bool(req["watch_folder_enabled"])
        persisted["watch_folder_enabled"] = we
        RUNTIME_SETTINGS["watch_folder_enabled"] = we

    save_persisted_settings(persisted)
    return {"status": "saved", "settings": get_system_settings()}

# -------------------------------------------------------------
# Models Manager API
# -------------------------------------------------------------

@app.get("/api/models/status")
def models_status_endpoint():
    """Return all catalog models, downloaded state, sizes, and RAM-aware recommendations."""
    return list_whisper_models()

@app.post("/api/models/download")
def download_model_endpoint(req: Dict[str, str]):
    """Download a faster-whisper model."""
    model_name = req.get("model_name", "base")
    return download_whisper_model(model_name)

@app.delete("/api/models/{model_name}")
def delete_model_endpoint(model_name: str):
    """Delete a faster-whisper model from disk."""
    return delete_whisper_model(model_name)

# -------------------------------------------------------------
# SSE Stream Endpoint
# -------------------------------------------------------------

@app.get("/api/sse/events")
async def sse_events():
    """Server-Sent Events endpoint for real-time progress updates."""
    queue = asyncio.Queue()
    event_subscribers.append(queue)

    async def event_generator():
        try:
            yield {"event": "ping", "data": json.dumps({"status": "connected"})}
            while True:
                data = await queue.get()
                yield {"event": "message", "data": data}
        except asyncio.CancelledError:
            pass
        finally:
            if queue in event_subscribers:
                event_subscribers.remove(queue)

    return EventSourceResponse(event_generator())

# -------------------------------------------------------------
# Exact Single-Frame FFmpeg Preview Endpoint
# -------------------------------------------------------------

@app.post("/api/preview-frame")
def preview_frame_endpoint(req: PreviewFrameRequest, session: Session = Depends(get_session)):
    """
    Extracts the exact video frame at `timestamp` with all FFmpeg reframing,
    face tracking, lens grading filters, hook banner, and .ass subtitles applied.
    Returns frame PNG URL and execution latency in ms.
    """
    target_video_path = None
    transcript_words = list(req.words) if req.words is not None else []
    clip_start_time = 0.0
    clip_duration = 0.0
    clip_keep_segments = req.keep_segments
    clip_merge_cf = float(req.merge_crossfade_sec) if req.merge_crossfade_sec is not None else 0.03
    clip_webcam_region = req.source_webcam_region
    clip_visual_map = None
    clip_framing_override = None
    clip_best_frames = None
    proj_id = req.project_id

    if req.clip_id:
        clip = session.get(ViralClip, req.clip_id)
        if clip:
            if not transcript_words:
                transcript_words = clip.get_transcript()
            if not clip_keep_segments:
                clip_keep_segments = clip.get_keep_segments()
            if req.merge_crossfade_sec is None:
                clip_merge_cf = float(getattr(clip, "merge_crossfade_sec", 0.03) or 0.03)
            if clip_webcam_region is None:
                clip_webcam_region = clip.get_source_webcam_region()
            clip_visual_map = clip.get_visual_map()
            clip_framing_override = clip.get_framing_override()
            clip_best_frames = clip.get_best_frames()
            clip_start_time = float(clip.start_time or 0.0)
            clip_duration = float(clip.duration or 0.0)
            proj_id = clip.project_id
            project = session.get(Project, clip.project_id)
            if project and project.source_path and os.path.exists(project.source_path):
                target_video_path = project.source_path

    clip_visual_map = clip_visual_map if "clip_visual_map" in locals() else None
    clip_obj = clip if req.clip_id and "clip" in locals() and clip else None
    rflags = req.resolved_retention_flags(clip=clip_obj)
    clip_opts = clip_obj.get_options() if clip_obj else {}
    if isinstance(req.options, dict):
        clip_opts = {**clip_opts, **req.options}
    if req.framing_override is not None:
        clip_framing_override = req.framing_override

    if not target_video_path and proj_id:
        project = session.get(Project, proj_id)
        if project and project.source_path and os.path.exists(project.source_path):
            target_video_path = project.source_path

    if not target_video_path:
        masters = list((Path(settings.STORAGE_DIR) / "masters").glob("*.mp4"))
        if masters:
            target_video_path = str(masters[0])
        else:
            raise HTTPException(status_code=404, detail="No source video found for preview frame.")

    color_grading = {
        "preset": req.color_preset,
        "preset_intensity": req.preset_intensity if req.preset_intensity is not None else clip_opts.get("preset_intensity", 100.0),
        "exposure": req.exposure,
        "brightness": req.brightness,
        "contrast": req.contrast,
        "lift_mids": req.lift_mids,
        "highlights": req.highlights if req.highlights is not None else clip_opts.get("highlights", 0.0),
        "shadows": req.shadows if req.shadows is not None else clip_opts.get("shadows", 0.0),
        "warmth": req.warmth,
        "saturation": req.saturation,
        "black_and_white": req.black_and_white,
        "vintage": req.vintage,
        "tint_color": req.color_tint or req.tint_color,
        "tint_strength": req.tint_strength,
        "glow": req.glow,
        "smooth": req.smooth,
        "sharpness": req.sharpness,
        "blur": req.blur,
        "film_grain": req.film_grain,
        "vignette": req.vignette,
        "bottom_fade": req.bottom_fade,
        "color_temp": req.color_temp if req.color_temp is not None else clip_opts.get("color_temp", 0.0),
        "tint_shift": req.tint_shift if req.tint_shift is not None else clip_opts.get("tint_shift", 0.0),
        "vibrance": req.vibrance if req.vibrance is not None else clip_opts.get("vibrance", 0.0),
        "matte_fade": req.matte_fade if req.matte_fade is not None else clip_opts.get("matte_fade", 0.0),
        "halation_glow": req.halation_glow if req.halation_glow is not None else clip_opts.get("halation_glow", 0.0),
        "lut_path": req.lut_path if req.lut_path is not None else clip_opts.get("lut_path"),
        "chroma_split": req.chroma_split if req.chroma_split is not None else clip_opts.get("chroma_split", 0.0),
        "letterbox": req.letterbox if req.letterbox is not None else clip_opts.get("letterbox", "none"),
        "bypass_color": bool(req.bypass_color),
    }

    caption_options = {
        "caption_style": req.style_preset or req.caption_style,
        "caption_font": req.font_family or req.caption_font,
        "caption_animation": req.animation or req.caption_animation,
        "font_size_percent": req.font_size_pct if req.font_size_pct is not None else req.font_size_percent,
        "base_color": req.base_color,
        "active_color": req.highlight_color or req.active_color,
        "outline_color": req.outline_color,
        "pill_color": req.pill_color if req.pill_color is not None else (clip_obj.get_options().get("pill_color") if clip_obj else None),
        "pacing_density": req.pacing_density if req.pacing_density is not None else (clip_obj.get_options().get("pacing_density", "3-4 Words Viral") if clip_obj else "3-4 Words Viral"),
        "text_case": req.text_case if req.text_case is not None else (clip_obj.get_options().get("text_case") if clip_obj else None),
        "hold_back_alpha": req.hold_back_opacity if req.hold_back_opacity is not None else req.hold_back_alpha,
        "highlight_shape": req.highlight_shape,
        "position_grid": req.position_grid,
        "position_x": req.pos_x if req.pos_x is not None else req.position_x,
        "position_y": req.pos_y if req.pos_y is not None else req.position_y,
        "caption_x": req.caption_x if req.caption_x is not None else clip_opts.get("caption_x"),
        "caption_y": req.caption_y if req.caption_y is not None else clip_opts.get("caption_y"),
        "caption_rotation": req.caption_rotation if req.caption_rotation is not None else clip_opts.get("caption_rotation", 0.0),
        "caption_max_width": req.caption_max_width if req.caption_max_width is not None else clip_opts.get("caption_max_width", 0.82),
        "outline_width": req.outline if req.outline is not None else req.outline_width,
        "shadow_depth": req.shadow if req.shadow is not None else req.shadow_depth,
        "glow_intensity": req.glow_intensity,
        "letter_spacing": req.letter_spacing,
        "bold": req.bold,
        "italic": req.italic,
        "all_caps": req.uppercase if req.uppercase is not None else req.all_caps,
        "text_align": req.alignment or req.text_align,
        "timing_offset": req.timing_offset,
        "emoji_enabled": req.auto_emoji if req.auto_emoji is not None else req.emoji_enabled,
        "highlight_power_words": bool(req.highlight_power_words),
        "how_phrase_arrives": req.line_mode or req.how_phrase_arrives,
        "word_by_word": req.word_by_word,
        "clean_filler_words": req.clean_filler_words,
        "censor_profanity": req.censor_profanity,
        "show_hook_banner": req.hook_banner_enabled if req.hook_banner_enabled is not None else req.show_hook_banner,
        "hook_headline": req.hook_headline,
        "retention_bar_enabled": req.retention_bar_enabled,
        "retention_bar_color": req.retention_bar_color,
        "outro_cta_enabled": req.outro_cta_enabled,
        "outro_cta_text": req.outro_cta_text,
        "outro_cta_headline": req.outro_cta_headline if req.outro_cta_headline is not None else clip_opts.get("outro_cta_headline", ""),
        "outro_cta_handle": req.outro_cta_handle if req.outro_cta_handle is not None else clip_opts.get("outro_cta_handle", ""),
        "outro_logo_path": req.outro_logo_path if req.outro_logo_path is not None else clip_opts.get("outro_logo_path"),
        "speed_ramp": req.speed_ramp if req.speed_ramp is not None else clip_opts.get("speed_ramp", 1.0),
        "speakers": req.speakers,
        "speaker_lower_thirds": req.speaker_lower_thirds,
        "face_safe_captions": req.face_safe_captions,
        "callout_cards": req.callout_cards,
        "auto_callouts": req.auto_callouts,
        "bilingual_enabled": req.bilingual_enabled,
        "secondary_language": req.secondary_language,
        "backdrop_mode": req.backdrop_mode,
        "backdrop_blur_intensity": req.backdrop_blur_intensity,
        "backdrop_color": req.backdrop_color,
        "camera_motion": req.camera_motion,
        "whip_pan_cuts": req.whip_pan_cuts,
        "depth_pop": req.depth_pop,
        "privacy_blur_boxes": req.privacy_blur_boxes,
        "beat_sync_enabled": req.beat_sync_enabled,
        "spectrum_enabled": req.spectrum_enabled,
        "spectrum_position": req.spectrum_position,
        "spectrum_color": req.spectrum_color,
        "chapters_enabled": req.chapters_enabled,
        "chapters": req.chapters,
        "handle_watermark_enabled": req.handle_watermark_enabled,
        "handle_text": req.handle_text,
        "handle_platform_icon": req.handle_platform_icon,
        "handle_corner": req.handle_corner,
        "handle_opacity": req.handle_opacity,
        "handle_display_mode": req.handle_display_mode,
        "tighten_pacing": bool(getattr(clip_obj, "tighten_pacing", False)) if clip_obj else False,
        "clean_filler_words": req.clean_filler_words,
        "auto_trim_dead_air": rflags["auto_trim_dead_air"],
        "silence_threshold": rflags["silence_threshold"],
    }

    try:
        result = render_exact_preview_frame(
            video_path=target_video_path,
            timestamp=req.timestamp,
            aspect_ratio=req.aspect_ratio,
            custom_width=req.custom_width,
            custom_height=req.custom_height,
            frame_layout=req.framing_template or req.frame_layout,
            webcam_corner=req.webcam_corner,
            webcam_split_ratio=req.webcam_split_ratio,
            source_webcam_region=clip_webcam_region,
            visual_map=clip_visual_map,
            framing_override=clip_framing_override,
            best_frames=clip_best_frames,
            camera_motion=req.camera_motion,
            whip_pan_cuts=req.whip_pan_cuts,
            pip_border_color=req.pip_border_color,
            zoom=req.zoom,
            pan_x_offset=req.pan_x_offset,
            pan_x=req.pan_x,
            color_grading=color_grading,
            burn_captions=req.burn_captions,
            caption_options=caption_options,
            transcript_words=transcript_words,
            clip_start_time=clip_start_time,
            clip_duration=clip_duration,
            clip_rel_ts_override=req.clip_rel_ts,
            clip_size=req.clip_size,
            export_resolution=req.export_resolution,
            watermark_path=req.watermark_path,
            watermark_corner=req.watermark_corner,
            watermark_opacity=req.watermark_opacity,
            watermark_size=req.watermark_size,
            project_id=proj_id,
            keep_segments=clip_keep_segments,
            merge_crossfade_sec=clip_merge_cf,
            backdrop_mode=req.backdrop_mode,
            backdrop_blur_intensity=req.backdrop_blur_intensity,
            backdrop_color=req.backdrop_color,
            depth_pop=req.depth_pop,
            privacy_blur_boxes=req.privacy_blur_boxes,
            beat_sync_enabled=req.beat_sync_enabled,
            spectrum_enabled=req.spectrum_enabled,
            spectrum_position=req.spectrum_position,
            spectrum_color=req.spectrum_color,
            chapters_enabled=req.chapters_enabled,
            chapters=req.chapters,
            handle_watermark_enabled=req.handle_watermark_enabled,
            handle_text=req.handle_text,
            handle_platform_icon=req.handle_platform_icon,
            handle_corner=req.handle_corner,
            handle_opacity=req.handle_opacity,
            handle_display_mode=req.handle_display_mode,
            use_cold_open=rflags["use_cold_open"],
            cold_open_quote=rflags["cold_open_quote"],
            auto_trim_dead_air=rflags["auto_trim_dead_air"],
            silence_threshold=rflags["silence_threshold"],
            dynamic_punch_zoom=rflags["dynamic_punch_zoom"],
            punch_zoom_intensity=rflags["punch_zoom_intensity"],
        )
        return result
    except Exception as e:
        logger.error(f"Error rendering preview frame: {e}")
        raise HTTPException(status_code=500, detail=str(e))

# -------------------------------------------------------------
# Brand Kits & Caption Style Presets API
# -------------------------------------------------------------

@app.get("/api/presets/brand")
def list_brand_presets(session: Session = Depends(get_session)):
    presets = session.exec(select(BrandPreset).order_by(BrandPreset.id.desc())).all()
    if not presets:
        default_preset = BrandPreset(
            name="Obsidian Amber Brand",
            caption_style_name="Karaoke pop",
            watermark_corner="top_right",
            watermark_opacity=0.85,
            aspect_ratio="9:16",
            safe_zone_guide="TikTok"
        )
        session.add(default_preset)
        session.commit()
        session.refresh(default_preset)
        presets = [default_preset]
    return presets

@app.post("/api/presets/brand")
def create_brand_preset(req: CreateBrandPresetRequest, session: Session = Depends(get_session)):
    preset = BrandPreset(
        name=req.name,
        caption_style_name=req.caption_style_name or "Karaoke pop",
        watermark_url=req.watermark_url,
        watermark_corner=req.watermark_corner or "top_right",
        watermark_opacity=req.watermark_opacity or 0.85,
        watermark_size=req.watermark_size or 0.15,
        outro_url=req.outro_url,
        bgm_url=req.bgm_url,
        bgm_volume=req.bgm_volume or 0.20,
        aspect_ratio=req.aspect_ratio or "9:16",
        safe_zone_guide=req.safe_zone_guide or "TikTok"
    )
    session.add(preset)
    session.commit()
    session.refresh(preset)
    return {"status": "created", "preset": preset}

@app.get("/api/presets/caption-style")
def list_caption_styles(session: Session = Depends(get_session)):
    built_in = []
    for name, s in CAPTION_STYLES_DEF.items():
        built_in.append({
            "id": f"builtin_{name.lower().replace(' ', '_')}",
            "name": name,
            "font_family": s.get("font", "Montserrat"),
            "font_size_percent": 100,
            "base_color": s.get("base_color", "#FFFFFF"),
            "active_color": s.get("active_color", "#C6F32F"),
            "outline_color": s.get("outline_color", "#000000"),
            "outline_width": s.get("outline_width", 4.0),
            "shadow_depth": s.get("shadow_depth", 3.0),
            "border_style": s.get("border_style", 1),
            "all_caps": s.get("all_caps", True),
            "is_builtin": True
        })

    custom = session.exec(select(CaptionStylePreset).order_by(CaptionStylePreset.id.desc())).all()
    custom_list = [c.model_dump() for c in custom]
    for c in custom_list:
        c["is_builtin"] = False

    return built_in + custom_list

@app.post("/api/presets/caption-style")
def create_caption_style(req: CreateCaptionStyleRequest, session: Session = Depends(get_session)):
    style = CaptionStylePreset(
        name=req.name,
        font_family=req.font_family or "Montserrat",
        font_size_percent=req.font_size_percent or 100,
        bold=req.bold if req.bold is not None else True,
        italic=req.italic if req.italic is not None else False,
        uppercase=req.uppercase if req.uppercase is not None else True,
        text_align=req.text_align or "Centre",
        base_color=req.base_color or "#FFFFFF",
        active_color=req.active_color or "#C6F32F",
        hold_back_alpha=req.hold_back_alpha if req.hold_back_alpha is not None else 1.0,
        highlight_shape=req.highlight_shape or "Colour only",
        animation_type=req.animation_type or "Karaoke pop",
        position_grid=req.position_grid or "bottom_center",
        position_x=req.position_x or 50.0,
        position_y=req.position_y or 78.0,
        outline_width=req.outline_width or 4.0,
        shadow_depth=req.shadow_depth or 3.0,
        glow_intensity=req.glow_intensity or 0.0,
        letter_spacing=req.letter_spacing or 0.0,
        timing_offset=req.timing_offset or 0.0,
        emoji_enabled=req.emoji_enabled if req.emoji_enabled is not None else False,
        how_phrase_arrives=req.how_phrase_arrives or "All at once"
    )
    session.add(style)
    session.commit()
    session.refresh(style)
    return {"status": "created", "style": style}

# -------------------------------------------------------------
# Fonts & Brand Assets API
# -------------------------------------------------------------

@app.get("/api/fonts")
@app.get("/api/system/fonts")
def list_fonts():
    fonts = list(FONT_MAP.keys())
    fonts_dir = Path(settings.STORAGE_DIR) / "fonts"
    if fonts_dir.exists():
        for f in fonts_dir.glob("*.[tT][tT][fF]"):
            if f.stem not in fonts:
                fonts.append(f.stem)
        for f in fonts_dir.glob("*.[oO][tT][fF]"):
            if f.stem not in fonts:
                fonts.append(f.stem)
    return sorted(list(set(fonts)))

@app.post("/api/fonts/upload")
@app.post("/api/system/fonts")
async def upload_font(file: UploadFile = File(...)):
    fonts_dir = Path(settings.STORAGE_DIR) / "fonts"
    fonts_dir.mkdir(parents=True, exist_ok=True)

    safe_name = Path(file.filename or "custom_font.ttf").name
    dest = fonts_dir / safe_name
    with open(dest, "wb") as buffer:
        while chunk := await file.read(1024 * 512):
            buffer.write(chunk)

    font_name = Path(safe_name).stem
    FONT_MAP[font_name] = font_name
    return {"status": "uploaded", "font_name": font_name, "filename": safe_name}

@app.post("/api/assets/upload")
async def upload_asset(file: UploadFile = File(...)):
    assets_dir = Path(settings.STORAGE_DIR) / "assets"
    assets_dir.mkdir(parents=True, exist_ok=True)

    filename = f"{Path(file.filename).stem}_{int(time.time())}{Path(file.filename).suffix}"
    dest = assets_dir / filename
    with open(dest, "wb") as buffer:
        while chunk := await file.read(1024 * 1024 * 2):
            buffer.write(chunk)

    return {
        "status": "uploaded",
        "url": f"/media/assets/{filename}",
        "filename": filename,
        "path": str(dest)
    }


@app.get("/api/system/luts")
def list_luts():
    luts_dir = Path(settings.STORAGE_DIR) / "luts"
    luts_dir.mkdir(parents=True, exist_ok=True)
    return [
        {"name": f.stem, "filename": f.name, "path": str(f)}
        for f in sorted(luts_dir.glob("*.cube"))
    ]


@app.post("/api/system/luts")
async def upload_lut(file: UploadFile = File(...)):
    luts_dir = Path(settings.STORAGE_DIR) / "luts"
    luts_dir.mkdir(parents=True, exist_ok=True)
    safe_name = f"{Path(file.filename or 'lut.cube').stem}_{int(time.time())}.cube"
    dest = luts_dir / safe_name
    with open(dest, "wb") as buffer:
        while chunk := await file.read(1024 * 512):
            buffer.write(chunk)
    return {
        "status": "uploaded",
        "name": Path(file.filename or safe_name).stem,
        "filename": safe_name,
        "path": str(dest),
        "url": f"/media/luts/{safe_name}",
    }


@app.post("/api/system/brand-logo")
async def upload_brand_logo(file: UploadFile = File(...)):
    assets_dir = Path(settings.STORAGE_DIR) / "assets"
    assets_dir.mkdir(parents=True, exist_ok=True)
    ext = Path(file.filename or "logo.png").suffix or ".png"
    filename = f"brand_logo_{int(time.time())}{ext}"
    dest = assets_dir / filename
    with open(dest, "wb") as buffer:
        while chunk := await file.read(1024 * 512):
            buffer.write(chunk)
    return {
        "status": "uploaded",
        "filename": filename,
        "url": f"/media/assets/{filename}",
        "path": str(dest),
    }

# -------------------------------------------------------------
# Pipeline Background Worker
# -------------------------------------------------------------

def process_video_pipeline(project_id: int):
    """
    ClipShlip background worker:
    1. Probes video duration, resolution, fps
    2. If Gemini AI Boost is ON: transcode 480p proxy, upload to Gemini API, extract blueprints.
       If Gemini AI Boost is OFF: generate local intelligent heuristic blueprints (100% private, zero upload).
    3. Runs Faster-Whisper transcription for each extracted clip
    4. Saves clips to SQLite and cleans proxy
    """
    from app.database import engine
    with Session(engine) as session:
        project = session.get(Project, project_id)
        if not project:
            return

        try:
            # 1. Probing
            meta = probe_video(project.source_path)
            project.duration = meta["duration"]
            project.resolution_w = meta["width"]
            project.resolution_h = meta["height"]
            project.fps = meta["fps"]
            session.add(project)
            session.commit()

            # 2. Transcode proxy
            proxy_filename = f"proxy_{project.id}.mp4"
            proxy_path = str(Path(settings.STORAGE_DIR) / "proxies" / proxy_filename)
            project.status = "proxy_ready"
            project.proxy_path = proxy_path
            session.add(project)
            session.commit()

            create_gemini_proxy(project.source_path, proxy_path)

            # 3. Gemini Multimodal Analysis
            project.status = "analyzing"
            session.add(project)
            session.commit()

            try:
                raw_clips = extract_viral_clips(
                    proxy_path=proxy_path,
                    total_duration=project.duration or 120.0,
                    duration_preset=project.duration_preset or "Auto",
                    clip_count=project.clip_count or 5,
                    topic_focus=project.topic_focus
                )
            finally:
                if os.path.exists(proxy_path):
                    try:
                        os.remove(proxy_path)
                    except Exception:
                        pass

            # 4. Transcribe clips & populate database
            for item in raw_clips:
                start_t = float(item["start_time"])
                dur_t = float(item["duration"])
                end_t = float(item["end_time"])

                words = transcribe_clip(project.source_path, start_t, dur_t, language=project.caption_language)

                clip = ViralClip(
                    project_id=project.id,
                    start_time=start_t,
                    end_time=end_t,
                    duration=dur_t,
                    virality_score=item.get("virality_score", 85),
                    hook_score=item.get("hook_score", 85),
                    retention_score=item.get("retention_score", 85),
                    emotional_score=item.get("emotional_score", 80),
                    standalone_score=item.get("standalone_score", 85),
                    viral_rationale=item.get("viral_rationale", ""),
                    hook_headline=item.get("hook_headline", "THE CRITICAL BREAKTHROUGH"),
                    show_hook_banner=True,
                    cold_open_quote=item.get("cold_open_quote", ""),
                    use_cold_open=False,
                    auto_trim_dead_air=False,
                    dynamic_punch_zoom=False,
                    aspect_ratio=project.default_aspect_ratio or "9:16",
                    frame_layout="auto_track",
                    webcam_corner="auto",
                    webcam_split_ratio=0.35,
                    pip_border_color="#F59E0B",
                    zoom=1.0,
                    pan_x_offset=0.0,
                    caption_style="Karaoke pop",
                    caption_font="Montserrat",
                    caption_animation="Karaoke",
                    base_color="#FFFFFF",
                    active_color="#C6F32F",
                    outline_color="#000000",
                    font_size=64,
                    font_size_percent=100,
                    bold=True,
                    italic=False,
                    all_caps=True,
                    text_align="Centre",
                    position_grid="bottom_center",
                    position_x=50.0,
                    position_y=78.0,
                    hold_back_alpha=1.0,
                    highlight_shape="Colour only",
                    emoji_enabled=False,
                    clean_filler_words=False,
                    clip_size="1080p",
                    color_preset="none",
                    exposure=0.0,
                    brightness=0.0,
                    contrast=1.0,
                    lift_mids=0.0,
                    warmth=0.0,
                    saturation=1.0,
                    black_and_white=0.0,
                    vintage=0.0,
                    tint_color="none",
                    tint_strength=0.0,
                    glow=0.0,
                    smooth=0.0,
                    sharpness=0.0,
                    blur=0.0,
                    film_grain=0.0,
                    vignette=0.0,
                    bottom_fade=0.0,
                    hardware_mode=project.hardware_mode or "gpu",
                    selected_title=item.get("youtube_titles", ["Viral Short #Shorts"])[0],
                    youtube_description=item.get("youtube_description", ""),
                    pinned_comment=item.get("pinned_comment", ""),
                    privacy_status="private",
                    render_status="idle",
                    render_progress=0
                )
                clip.set_titles(item.get("youtube_titles", []))
                clip.set_transcript(words)

                session.add(clip)

            project.status = "analyzed"
            session.add(project)
            session.commit()

        except Exception as e:
            logger.error(f"Pipeline error for project {project_id}: {e}")
            project.status = "error"
            project.error_message = str(e)
            session.add(project)
            session.commit()

# -------------------------------------------------------------
# Projects API (My Clips Manager)
# -------------------------------------------------------------

@app.get("/api/projects")
def list_projects(session: Session = Depends(get_session)):
    """List all projects with exact calculated disk usage and total summary."""
    projects = session.exec(select(Project).order_by(Project.id.desc())).all()
    project_list = []
    total_disk_bytes = 0

    for p in projects:
        clips = session.exec(select(ViralClip).where(ViralClip.project_id == p.id)).all()
        p_bytes = 0
        if p.source_path and os.path.exists(p.source_path):
            p_bytes += os.path.getsize(p.source_path)
        if p.proxy_path and os.path.exists(p.proxy_path):
            p_bytes += os.path.getsize(p.proxy_path)
        for c in clips:
            if c.rendered_video_path and os.path.exists(c.rendered_video_path):
                p_bytes += os.path.getsize(c.rendered_video_path)

        total_disk_bytes += p_bytes

        p_dict = p.model_dump()
        p_dict["disk_size_bytes"] = p_bytes
        p_dict["disk_size_formatted"] = format_bytes(p_bytes)
        p_dict["clips_count"] = len(clips)

        # Best available thumbnail for Quick-Resume strip
        thumb_url = None
        for c in clips:
            bfs = c.get_best_frames()
            if bfs and isinstance(bfs, list) and bfs[0].get("url"):
                raw_u = str(bfs[0]["url"])
                thumb_url = f"/media{raw_u}" if raw_u.startswith("/assets/") else raw_u
                break
        if not thumb_url:
            bf_dir = storage_assets / "best_frames" / str(p.id)
            if bf_dir.exists():
                for jpg in sorted(bf_dir.glob("*.jpg")):
                    thumb_url = f"/media/assets/best_frames/{p.id}/{jpg.name}"
                    break
        p_dict["thumbnail_url"] = thumb_url

        # Explicit AI Mode badge for Quick-Resume cards
        proj_ai_mode = getattr(p, "ai_mode", None)
        if proj_ai_mode not in ("gemini", "offline"):
            pjson_path = storage_projects / str(p.id) / "project.json"
            if pjson_path.exists():
                try:
                    pdata = json.loads(pjson_path.read_text(encoding="utf-8"))
                    cand = pdata.get("ai_mode")
                    if cand in ("gemini", "offline"):
                        proj_ai_mode = cand
                except Exception:
                    pass
        if proj_ai_mode not in ("gemini", "offline"):
            first_src = ""
            for c in clips:
                opts = c.get_options()
                src_tag = str(opts.get("analysis_source") or "").lower()
                if src_tag:
                    first_src = src_tag
                    break
            if first_src.startswith("offline"):
                proj_ai_mode = "offline"
            elif first_src:
                proj_ai_mode = "gemini"
            else:
                proj_ai_mode = "gemini" if RUNTIME_SETTINGS.get("gemini_configured") and RUNTIME_SETTINGS.get("ai_provider", "gemini") != "offline" else "offline"
        p_dict["ai_mode"] = proj_ai_mode
        p_dict["default_framing"] = getattr(p, "default_framing", "smart_auto") or "smart_auto"
        project_list.append(p_dict)

    return {
        "projects": project_list,
        "total_projects": len(project_list),
        "total_disk_bytes": total_disk_bytes,
        "total_disk_formatted": format_bytes(total_disk_bytes)
    }

def _serialize_clip(clip: ViralClip, project: Optional[Project] = None) -> Dict[str, Any]:
    """Unified ViralClip serializer exposing canonical status, rendered_path, preview_url, and video_url."""
    cdict = clip.model_dump()
    cdict["youtube_titles"] = clip.get_titles()
    cdict["transcript_words"] = clip.get_transcript()
    cdict["keep_segments"] = clip.get_keep_segments()
    cdict["merge_crossfade_sec"] = float(getattr(clip, "merge_crossfade_sec", 0.03) or 0.03)
    cdict["speakers"] = clip.get_speakers()
    cdict["speaker_lower_thirds"] = bool(getattr(clip, "speaker_lower_thirds", False))
    cdict["face_safe_captions"] = bool(getattr(clip, "face_safe_captions", True))
    cdict["callout_cards"] = clip.get_callout_cards()
    cdict["auto_callouts"] = bool(getattr(clip, "auto_callouts", False))
    cdict["bilingual_enabled"] = bool(getattr(clip, "bilingual_enabled", False))
    cdict["secondary_language"] = str(getattr(clip, "secondary_language", "") or "")
    cdict["dubbing_enabled"] = bool(getattr(clip, "dubbing_enabled", False))
    cdict["dubbing_language"] = str(getattr(clip, "dubbing_language", "Spanish") or "Spanish")
    cdict["dubbing_voice"] = str(getattr(clip, "dubbing_voice", "default") or "default")
    cdict["dubbing_bed_volume"] = float(getattr(clip, "dubbing_bed_volume", 0.15) or 0.15)
    cdict["dubbed_audio_path"] = getattr(clip, "dubbed_audio_path", None)
    cdict["backdrop_mode"] = str(getattr(clip, "backdrop_mode", "none") or "none")
    cdict["backdrop_blur_intensity"] = float(getattr(clip, "backdrop_blur_intensity", 18.0) or 18.0)
    cdict["backdrop_color"] = str(getattr(clip, "backdrop_color", "#18181B") or "#18181B")
    cdict["camera_motion"] = str(getattr(clip, "camera_motion", "steady") or "steady")
    cdict["whip_pan_cuts"] = bool(getattr(clip, "whip_pan_cuts", False))
    cdict["broll_layout"] = str(getattr(clip, "broll_layout", "fullscreen") or "fullscreen")
    cdict["depth_pop"] = float(getattr(clip, "depth_pop", 0.0) or 0.0)
    cdict["privacy_blur_boxes"] = clip.get_privacy_blur_boxes()
    cdict["beat_sync_enabled"] = bool(getattr(clip, "beat_sync_enabled", False))
    cdict["spectrum_enabled"] = bool(getattr(clip, "spectrum_enabled", False))
    cdict["spectrum_position"] = str(getattr(clip, "spectrum_position", "bottom") or "bottom")
    cdict["spectrum_color"] = str(getattr(clip, "spectrum_color", "#C6F32F") or "#C6F32F")
    cdict["chapters_enabled"] = bool(getattr(clip, "chapters_enabled", False))
    cdict["chapters"] = clip.get_chapters()
    cdict["handle_watermark_enabled"] = bool(getattr(clip, "handle_watermark_enabled", False))
    cdict["handle_text"] = str(getattr(clip, "handle_text", "") or "")
    cdict["handle_platform_icon"] = str(getattr(clip, "handle_platform_icon", "none") or "none")
    cdict["handle_corner"] = str(getattr(clip, "handle_corner", "bottom_right") or "bottom_right")
    cdict["handle_opacity"] = float(getattr(clip, "handle_opacity", 0.65) or 0.65)
    cdict["handle_display_mode"] = str(getattr(clip, "handle_display_mode", "always") or "always")
    cdict["source_webcam_region"] = clip.get_source_webcam_region()
    cdict["framing_override"] = clip.get_framing_override()
    cdict["best_frames"] = clip.get_best_frames()
    opts = clip.get_options()
    cdict["options"] = opts
    cdict["pill_color"] = opts.get("pill_color", "")
    cdict["pacing_density"] = opts.get("pacing_density", "3-4 Words Viral")
    cdict["text_case"] = opts.get("text_case", "ALL CAPS" if clip.all_caps else "As Spoken")
    for k_opt, k_def in (
        ("caption_x", None),
        ("caption_y", None),
        ("caption_rotation", 0.0),
        ("caption_max_width", 0.82),
        ("preset_intensity", 100.0),
        ("highlights", 0.0),
        ("shadows", 0.0),
        ("export_fps", "source"),
        ("color_temp", 0.0),
        ("tint_shift", 0.0),
        ("vibrance", 0.0),
        ("matte_fade", 0.0),
        ("halation_glow", 0.0),
        ("lut_path", None),
        ("voice_eq_preset", "Raw"),
        ("de_esser", False),
        ("loudness_target", "-14 LUFS"),
        ("speed_ramp", 1.0),
        ("chroma_split", 0.0),
        ("letterbox", "none"),
        ("outro_cta_headline", ""),
        ("outro_cta_handle", ""),
        ("outro_logo_path", None),
    ):
        cdict[k_opt] = opts.get(k_opt, k_def)
    cdict["caption_x"] = float(opts["caption_x"]) if opts.get("caption_x") is not None else round(float(clip.position_x or 50.0) / 100.0, 4)
    cdict["caption_y"] = float(opts["caption_y"]) if opts.get("caption_y") is not None else round(float(clip.position_y or 76.0) / 100.0, 4)
    cdict["narrative_arc"] = opts.get("narrative_arc")
    cdict["arc_label"] = opts.get("arc_label")
    cdict["dominant_emotion"] = opts.get("dominant_emotion")
    cdict["vocal_emotion"] = opts.get("vocal_emotion")
    cdict["emotion_arousal"] = opts.get("emotion_arousal")
    cdict["emotion_valence"] = opts.get("emotion_valence")
    cdict["acoustic_events"] = opts.get("acoustic_events") or []
    vmap = clip.get_visual_map()
    cdict["visual_map_summary"] = vmap.get("summary") if isinstance(vmap, dict) else None
    if project is not None:
        mm = project.get_multimodal_analysis()
        if isinstance(mm, dict):
            cdict["multimodal_summary"] = mm.get("summary")
            cdict["emotion_summary"] = mm.get("emotion_summary")
    try:
        cdict["broll_segments"] = json.loads(getattr(clip, "broll_json", "[]") or "[]")
    except Exception:
        cdict["broll_segments"] = []

    rendered_file = clip.rendered_video_path or getattr(clip, "rendered_path", None)
    has_rendered = bool(rendered_file and os.path.exists(rendered_file))

    if has_rendered:
        try:
            ver = int(os.path.getmtime(rendered_file) * 1000)
        except OSError:
            ver = int(time.time() * 1000)
        stream_url = f"/api/clips/{clip.project_id}/{clip.id}/video?v={ver}"
        cdict["rendered_path"] = rendered_file
        cdict["rendered_video_path"] = rendered_file
        cdict["status"] = "ready"
        cdict["render_status"] = "completed"
        cdict["render_progress"] = 100
        cdict["preview_url"] = stream_url
        cdict["video_url"] = stream_url
    else:
        raw_status = getattr(clip, "status", None) or "draft"
        is_rendering = clip.render_status == "rendering" or raw_status == "rendering"
        is_failed = clip.render_status == "failed" or raw_status == "failed"
        cdict["rendered_path"] = None
        cdict["rendered_video_path"] = None
        cdict["status"] = "rendering" if is_rendering else ("failed" if is_failed else "draft")
        cdict["render_status"] = "rendering" if is_rendering else ("failed" if is_failed else "idle")
        cdict["preview_url"] = None
        cdict["video_url"] = None

    return cdict


@app.get("/api/projects/{project_id}")
def get_project(project_id: int, session: Session = Depends(get_session)):
    project = session.get(Project, project_id)
    if not project:
        raise HTTPException(status_code=404, detail="Project not found")
    clips = session.exec(select(ViralClip).where(ViralClip.project_id == project_id).order_by(ViralClip.virality_score.desc())).all()

    return {
        "project": project,
        "clips": [_serialize_clip(c, project) for c in clips],
    }

@app.patch("/api/projects/{project_id}")
def update_project(
    project_id: int,
    req: Dict[str, Any],
    session: Session = Depends(get_session)
):
    """Update project configurations (aspect ratio, duration preset, language, hardware mode)."""
    project = session.get(Project, project_id)
    if not project:
        raise HTTPException(status_code=404, detail="Project not found")

    if "title" in req and req["title"] is not None:
        project.title = str(req["title"])
    if "default_aspect_ratio" in req and req["default_aspect_ratio"] is not None:
        project.default_aspect_ratio = str(req["default_aspect_ratio"])
    if "duration_preset" in req and req["duration_preset"] is not None:
        project.duration_preset = str(req["duration_preset"])
    if "clip_count" in req and req["clip_count"] is not None:
        project.clip_count = int(req["clip_count"])
    if "caption_language" in req and req["caption_language"] is not None:
        project.caption_language = str(req["caption_language"])
    if "hardware_mode" in req and req["hardware_mode"] is not None:
        project.hardware_mode = str(req["hardware_mode"])

    session.add(project)
    session.commit()
    session.refresh(project)
    return {"status": "updated", "project": project}

@app.delete("/api/projects/{project_id}")
def delete_project(project_id: int, session: Session = Depends(get_session)):
    """Delete all project master, proxy, and rendered files from disk and remove records."""
    project = session.get(Project, project_id)
    if not project:
        raise HTTPException(status_code=404, detail="Project not found")

    reclaimed_bytes = 0

    # Delete master video
    if project.source_path and os.path.exists(project.source_path):
        try:
            reclaimed_bytes += os.path.getsize(project.source_path)
            os.remove(project.source_path)
        except Exception:
            pass

    # Delete proxy video
    if project.proxy_path and os.path.exists(project.proxy_path):
        try:
            reclaimed_bytes += os.path.getsize(project.proxy_path)
            os.remove(project.proxy_path)
        except Exception:
            pass

    # Delete clips & rendered videos
    clips = session.exec(select(ViralClip).where(ViralClip.project_id == project_id)).all()
    for c in clips:
        if c.rendered_video_path and os.path.exists(c.rendered_video_path):
            try:
                reclaimed_bytes += os.path.getsize(c.rendered_video_path)
                os.remove(c.rendered_video_path)
            except Exception:
                pass
        session.delete(c)

    session.delete(project)
    session.commit()

    return {
        "status": "deleted",
        "project_id": project_id,
        "reclaimed_bytes": reclaimed_bytes,
        "reclaimed_formatted": format_bytes(reclaimed_bytes)
    }

@app.get("/api/projects/{project_id}/logs")
def get_project_pipeline_logs(project_id: int, session: Session = Depends(get_session)):
    """Return timestamped pipeline execution logs for the Progress modal."""
    project = session.get(Project, project_id)
    if not project:
        raise HTTPException(status_code=404, detail="Project not found")

    clips = session.exec(select(ViralClip).where(ViralClip.project_id == project_id)).all()

    created_iso = project.created_at
    logs = [
        {"timestamp": created_iso, "stage": "INGESTION", "level": "INFO", "message": f"Source video '{project.source_filename or project.title}' ingested. Duration: {project.duration:.1f}s."},
        {"timestamp": created_iso, "stage": "PROBING", "level": "INFO", "message": f"FFprobe probed stream: {project.resolution_w}x{project.resolution_h} @ {project.fps} fps."},
        {"timestamp": created_iso, "stage": "PROXY", "level": "INFO", "message": "FFmpeg generated lightweight 480p @ 1 FPS proxy for multimodal analysis."},
        {"timestamp": created_iso, "stage": "ANALYSIS", "level": "INFO", "message": f"Multimodal analysis completed. Extracted {len(clips)} viral moment blueprints."},
        {"timestamp": created_iso, "stage": "TRANSCRIPTION", "level": "INFO", "message": "faster-whisper generated millisecond word timestamps (VRAM-safe compute)."},
    ]
    for c in clips:
        if c.render_status == "completed":
            logs.append({
                "timestamp": c.created_at,
                "stage": "RENDER",
                "level": "SUCCESS",
                "message": f"Clip #{c.id} rendered successfully ({c.aspect_ratio}, NVENC hardware encoding)."
            })
        elif c.render_status == "rendering":
            logs.append({
                "timestamp": c.created_at,
                "stage": "RENDER",
                "level": "INFO",
                "message": f"Clip #{c.id} currently rendering: {c.render_progress}%."
            })
        elif c.render_status == "failed":
            logs.append({
                "timestamp": c.created_at,
                "stage": "RENDER",
                "level": "ERROR",
                "message": f"Clip #{c.id} render error: {c.render_message}."
            })

    return {"project_id": project_id, "logs": logs}

# -------------------------------------------------------------
# Background Job Ingestion & Real-Time Tracking Endpoints
# -------------------------------------------------------------

@app.get("/api/jobs")
def list_jobs_endpoint(limit: int = 20):
    """Return active running jobs, queued batch jobs, and recent completed/failed jobs."""
    return job_manager.list_jobs(limit=limit)

@app.get("/api/projects/{project_id}/progress")
def get_project_progress_endpoint(project_id: int):
    """Return real-time job progress, percentage, stage milestones, and timestamped logs."""
    return job_manager.get_progress(project_id)

@app.post("/api/projects/{project_id}/cancel")
def cancel_project_job_endpoint(project_id: int):
    """Abort an active background ingestion or analysis job."""
    job_manager.cancel_job(project_id)
    return {"status": "cancelled", "project_id": project_id}

@app.post("/api/projects/{project_id}/retry")
def retry_project_job_endpoint(
    project_id: int,
    req: Optional[RetryProjectRequest] = None,
    background_tasks: BackgroundTasks = BackgroundTasks()
):
    """Retry a failed or cancelled pipeline job, optionally forcing 100% offline mode."""
    force_offline = req.force_offline if req else False
    job = job_manager.retry_pipeline(project_id, background_tasks, force_offline=force_offline)
    return {
        "status": "processing",
        "project_id": project_id,
        "resumed_stage": job.get("current_stage"),
        "force_offline": force_offline
    }

@app.post("/api/projects/{project_id}/provide-key")
def provide_key_endpoint(
    project_id: int,
    req: ProvideKeyRequest,
    background_tasks: BackgroundTasks = BackgroundTasks()
):
    """
    Validate provided Google Gemini API key live, persist it to storage,
    and resume the paused pipeline from Stage 4 (Gemini moment extraction).
    """
    target_model = req.model or settings.GEMINI_MODEL
    is_valid, msg = verify_gemini_api_key(req.api_key, model=target_model)
    if not is_valid:
        raise HTTPException(status_code=400, detail=msg)

    # Key is valid: update runtime and persist to storage/settings.json
    settings.GEMINI_API_KEY = req.api_key
    os.environ["GEMINI_API_KEY"] = req.api_key
    persisted = get_persisted_settings()
    persisted["gemini_api_key"] = req.api_key
    persisted["gemini_enabled"] = True
    persisted["gemini_boost_enabled"] = True
    persisted["ai_mode"] = "gemini"
    persisted["ai_provider"] = "gemini"
    settings.AI_PROVIDER = "gemini"
    if req.model:
        settings.GEMINI_MODEL = req.model
        persisted["gemini_model"] = req.model
    save_persisted_settings(persisted)

    # Resume the project job with force_offline=False
    job = job_manager.retry_pipeline(project_id, background_tasks, force_offline=False)
    return {
        "status": "resumed",
        "project_id": project_id,
        "message": msg,
        "resumed_stage": job.get("current_stage")
    }

@app.get("/api/ingest/youtube-info")
@app.get("/api/projects/youtube-info")
def get_youtube_info_endpoint(url: str):
    """Inspect a YouTube video URL instantly without downloading via yt-dlp --dump-json semantics."""
    clean_url = (url or "").strip()
    if not clean_url or not (
        "youtube.com" in clean_url.lower()
        or "youtu.be" in clean_url.lower()
        or clean_url.lower().startswith("http")
    ):
        raise HTTPException(status_code=400, detail="Please enter a valid YouTube or video URL.")

    try:
        import yt_dlp
        ydl_opts = {
            "quiet": True,
            "no_warnings": True,
            "skip_download": True,
        }
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(clean_url, download=False)
    except Exception as exc:
        logger.warning(f"yt-dlp metadata lookup failed for '{clean_url}': {exc}")
        raise HTTPException(status_code=400, detail=f"Could not fetch video metadata: {str(exc)}")

    if not isinstance(info, dict):
        raise HTTPException(status_code=404, detail="Video metadata unavailable for this URL.")

    width = int(info.get("width") or 0)
    height = int(info.get("height") or 0)
    if (not width or not height) and isinstance(info.get("formats"), list):
        for fmt in reversed(info["formats"]):
            if fmt.get("width") and fmt.get("height"):
                width = int(fmt["width"])
                height = int(fmt["height"])
                break

    thumb = info.get("thumbnail")
    if not thumb and isinstance(info.get("thumbnails"), list) and info["thumbnails"]:
        thumb = info["thumbnails"][-1].get("url")

    resolution = f"{width}x{height}" if (width and height) else str(info.get("resolution") or "1080p")
    return {
        "title": info.get("title") or "YouTube Video",
        "thumbnail": thumb,
        "duration": float(info.get("duration") or 0.0),
        "width": width or 1920,
        "height": height or 1080,
        "resolution": resolution,
        "uploader": info.get("uploader") or info.get("channel") or "",
        "webpage_url": info.get("webpage_url") or clean_url,
        "filesize_approx": int(info.get("filesize_approx") or info.get("filesize") or 0),
    }


@app.post("/api/ingest/upload")
@app.post("/api/projects/upload")
async def upload_video(
    file: UploadFile = File(...),
    aspect_ratio: Optional[str] = Form("9:16"),
    duration_preset: Optional[str] = Form("Auto"),
    clip_count: Optional[int] = Form(5),
    topic_focus: Optional[str] = Form(None),
    caption_language: Optional[str] = Form("As spoken"),
    hardware_mode: Optional[str] = Form("gpu"),
    default_framing: Optional[str] = Form("smart_auto"),
    ai_mode: Optional[str] = Form(None),
    batch_queue: Optional[bool] = Form(False),
    background_tasks: BackgroundTasks = BackgroundTasks(),
    session: Session = Depends(get_session)
):
    """Upload master video with Step 1 setup parameters and launch background pipeline."""
    masters_dir = Path(settings.STORAGE_DIR) / "masters"
    masters_dir.mkdir(parents=True, exist_ok=True)

    dest_filename = f"{Path(file.filename).stem}_{int(time.time() * 1000)}{Path(file.filename).suffix}"
    dest_path = masters_dir / dest_filename

    with open(dest_path, "wb") as buffer:
        while chunk := await file.read(1024 * 1024 * 4):
            buffer.write(chunk)

    from app.services.job_manager import resolve_ai_mode
    clean_ai_mode = ai_mode if ai_mode in ("gemini", "offline") else None
    resolved_mode = resolve_ai_mode(get_persisted_settings(), explicit_ai_mode=clean_ai_mode)
    stored_ai_mode = resolved_mode if resolved_mode in ("gemini", "offline") else clean_ai_mode
    framing_choice = default_framing or "smart_auto"

    title = Path(file.filename).stem.replace("_", " ").title()
    project = Project(
        title=title,
        source_type="local",
        source_filename=file.filename,
        source_path=str(dest_path),
        status="created",
        default_aspect_ratio=aspect_ratio or "9:16",
        duration_preset=duration_preset or "Auto",
        clip_count=clip_count or 5,
        caption_language=caption_language or "As spoken",
        hardware_mode=hardware_mode or "gpu",
        default_framing=framing_choice,
        ai_mode=stored_ai_mode,
        topic_focus=topic_focus
    )
    session.add(project)
    session.commit()
    session.refresh(project)

    # Initialize Job in JobManager & persist initial project.json
    job_manager.create_job(
        project_id=project.id,
        title=project.title,
        source_path=str(dest_path),
        source_filename=file.filename,
        duration_preset=duration_preset or "Auto",
        aspect_ratio=aspect_ratio or "9:16",
        clip_count=clip_count or 5,
        caption_language=caption_language or "As spoken",
        topic_focus=topic_focus,
        hardware_mode=hardware_mode or "gpu",
        ai_mode=clean_ai_mode,
        default_framing=framing_choice,
    )

    if batch_queue:
        enqueue_batch_project(project.id, project.title, source="upload", auto_start=True)
        return {
            "project_id": project.id,
            "title": project.title,
            "status": "queued",
            "ai_mode": stored_ai_mode,
            "default_framing": framing_choice,
        }

    background_tasks.add_task(job_manager.run_pipeline, project.id)

    return {
        "project_id": project.id,
        "title": project.title,
        "status": "processing",
        "ai_mode": stored_ai_mode,
        "default_framing": framing_choice,
    }

@app.post("/api/ingest/youtube")
@app.post("/api/projects/youtube")
async def import_youtube(
    req: YouTubeImportRequest,
    background_tasks: BackgroundTasks = BackgroundTasks(),
    session: Session = Depends(get_session)
):
    """Import video from YouTube with Step 1 setup parameters and launch background pipeline."""
    masters_dir = Path(settings.STORAGE_DIR) / "masters"
    masters_dir.mkdir(parents=True, exist_ok=True)

    try:
        import yt_dlp
        ydl_opts = {
            "format": "bestvideo[ext=mp4]+bestaudio[ext=m4a]/best[ext=mp4]/best",
            "outtmpl": str(masters_dir / "%(id)s.%(ext)s"),
            "quiet": True,
            "no_warnings": True
        }
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(req.url, download=True)
            video_filename = ydl.prepare_filename(info)
            video_title = info.get("title", "YouTube Video")
    except Exception as e:
        logger.error(f"yt-dlp download failed: {e}")
        raise HTTPException(status_code=400, detail=f"Failed to import YouTube video: {str(e)}")

    from app.services.job_manager import resolve_ai_mode
    clean_ai_mode = req.ai_mode if req.ai_mode in ("gemini", "offline") else None
    resolved_mode = resolve_ai_mode(get_persisted_settings(), explicit_ai_mode=clean_ai_mode)
    stored_ai_mode = resolved_mode if resolved_mode in ("gemini", "offline") else clean_ai_mode
    framing_choice = req.default_framing or "smart_auto"

    project = Project(
        title=video_title,
        source_type="youtube",
        source_filename=os.path.basename(video_filename),
        source_path=str(video_filename),
        status="created",
        default_aspect_ratio=req.aspect_ratio or "9:16",
        duration_preset=req.duration_preset or "Auto",
        clip_count=req.clip_count or 5,
        caption_language=req.caption_language or "As spoken",
        hardware_mode=req.hardware_mode or "gpu",
        default_framing=framing_choice,
        ai_mode=stored_ai_mode,
        topic_focus=req.topic_focus
    )
    session.add(project)
    session.commit()
    session.refresh(project)

    # Initialize Job in JobManager & persist initial project.json
    job_manager.create_job(
        project_id=project.id,
        title=project.title,
        source_path=str(video_filename),
        source_filename=os.path.basename(video_filename),
        duration_preset=req.duration_preset or "Auto",
        aspect_ratio=req.aspect_ratio or "9:16",
        clip_count=req.clip_count or 5,
        caption_language=req.caption_language or "As spoken",
        topic_focus=req.topic_focus,
        hardware_mode=req.hardware_mode or "gpu",
        ai_mode=clean_ai_mode,
        default_framing=framing_choice,
    )

    if req.batch_queue:
        enqueue_batch_project(project.id, project.title, source="youtube", auto_start=True)
        return {
            "project_id": project.id,
            "title": project.title,
            "status": "queued",
            "ai_mode": stored_ai_mode,
            "default_framing": framing_choice,
        }

    background_tasks.add_task(job_manager.run_pipeline, project.id)

    return {
        "project_id": project.id,
        "title": project.title,
        "status": "processing",
        "ai_mode": stored_ai_mode,
        "default_framing": framing_choice,
    }

# -------------------------------------------------------------
# Clips Voice Recognition & Captions API
# -------------------------------------------------------------

@app.post("/api/clips/{clip_id}/transcribe")
@app.post("/api/clips/{project_id}/{clip_id}/transcribe")
def retranscribe_clip_voice(
    clip_id: int,
    project_id: Optional[int] = None,
    session: Session = Depends(get_session)
):
    """
    Re-transcribe the real spoken voice for a specific clip using faster-whisper.
    """
    clip = session.get(ViralClip, clip_id)
    if not clip:
        raise HTTPException(status_code=404, detail="Clip not found")

    p_id = project_id or clip.project_id
    project = session.get(Project, p_id)
    if not project or not project.source_path or not os.path.exists(project.source_path):
        raise HTTPException(status_code=404, detail="Source video not found for clip audio re-transcription")

    try:
        words = transcribe_clip(
            project.source_path,
            clip.start_time,
            clip.duration,
            language=project.caption_language
        )
        clip.set_transcript(words)
        session.add(clip)
        session.commit()
        session.refresh(clip)
        return {
            "status": "success",
            "clip_id": clip.id,
            "project_id": project.id,
            "transcript_words": words,
            "words_count": len(words)
        }
    except Exception as e:
        logger.error(f"Re-transcribe failed for clip {clip_id}: {e}")
        raise HTTPException(status_code=500, detail=str(e))

# -------------------------------------------------------------
# Clips API
# -------------------------------------------------------------

@app.get("/api/clips/{clip_id}")
def get_clip(clip_id: int, session: Session = Depends(get_session)):
    clip = session.get(ViralClip, clip_id)
    if not clip:
        raise HTTPException(status_code=404, detail="Clip not found")
    project = session.get(Project, clip.project_id)
    return _serialize_clip(clip, project)

@app.patch("/api/clips/{clip_id}")
def update_clip(clip_id: int, req: UpdateClipRequest, session: Session = Depends(get_session)):
    clip = session.get(ViralClip, clip_id)
    if not clip:
        raise HTTPException(status_code=404, detail="Clip not found")

    # Time boundaries & multi-segment blade cuts
    if req.merge_crossfade_sec is not None:
        clip.merge_crossfade_sec = max(0.0, min(0.6, float(req.merge_crossfade_sec)))
    if req.keep_segments is not None:
        clip.set_keep_segments(req.keep_segments)
    elif req.start_time is not None or req.end_time is not None:
        if req.start_time is not None:
            clip.start_time = req.start_time
        if req.end_time is not None:
            clip.end_time = req.end_time
        segs = clip.get_keep_segments()
        if len(segs) <= 1:
            clip.set_keep_segments([[clip.start_time, clip.end_time]])
        else:
            clamped = [
                [max(clip.start_time, s), min(clip.end_time, e)]
                for s, e in segs
                if min(clip.end_time, e) > max(clip.start_time, s) + 0.1
            ]
            clip.set_keep_segments(clamped or [[clip.start_time, clip.end_time]])

    # Hooks & Retention
    if req.hook_headline is not None:
        clip.hook_headline = req.hook_headline
    if req.show_hook_banner is not None:
        clip.show_hook_banner = req.show_hook_banner
    if req.cold_open_quote is not None:
        clip.cold_open_quote = req.cold_open_quote
    if req.cold_open_climax is not None:
        clip.use_cold_open = bool(req.cold_open_climax)
    elif req.cold_open is not None:
        clip.use_cold_open = bool(req.cold_open)
    elif req.use_cold_open is not None:
        clip.use_cold_open = bool(req.use_cold_open)
    if req.trim_dead_air is not None:
        clip.auto_trim_dead_air = bool(req.trim_dead_air)
    elif req.auto_trim_dead_air is not None:
        clip.auto_trim_dead_air = bool(req.auto_trim_dead_air)
    if req.punch_zoom_fx is not None:
        clip.dynamic_punch_zoom = bool(req.punch_zoom_fx)
    elif req.punch_zoom is not None:
        clip.dynamic_punch_zoom = bool(req.punch_zoom)
    elif req.dynamic_punch_zoom is not None:
        clip.dynamic_punch_zoom = bool(req.dynamic_punch_zoom)
    if req.options is not None and isinstance(req.options, dict):
        clip.set_options(req.options)

    # Step 1: Layout & Framing
    if req.aspect_ratio is not None:
        clip.aspect_ratio = req.aspect_ratio
    if req.custom_width is not None:
        clip.custom_width = req.custom_width
    if req.custom_height is not None:
        clip.custom_height = req.custom_height
    if req.frame_layout is not None:
        clip.frame_layout = req.frame_layout
    elif req.framing_template is not None:
        clip.frame_layout = req.framing_template
    if req.webcam_corner is not None:
        clip.webcam_corner = req.webcam_corner
    if req.webcam_split_ratio is not None:
        clip.webcam_split_ratio = req.webcam_split_ratio
    if req.source_webcam_region is not None:
        clip.set_source_webcam_region(req.source_webcam_region)
    if req.framing_override is not None:
        clip.set_framing_override(req.framing_override)
    if req.pip_border_color is not None:
        clip.pip_border_color = req.pip_border_color
    if req.zoom is not None:
        clip.zoom = req.zoom
    if req.pan_x_offset is not None:
        clip.pan_x_offset = req.pan_x_offset
    elif req.pan_x is not None:
        clip.pan_x_offset = max(-1.0, min(1.0, float(req.pan_x) / 100.0))

    # Step 2: Pro Captions
    if req.burn_captions is not None:
        clip.burn_captions = req.burn_captions
    if req.timing_offset is not None:
        clip.timing_offset = req.timing_offset
    if req.caption_style is not None:
        clip.caption_style = req.caption_style
    if req.caption_font is not None:
        clip.caption_font = req.caption_font
    if req.caption_animation is not None:
        clip.caption_animation = req.caption_animation
    if req.base_color is not None:
        clip.base_color = req.base_color
    if req.active_color is not None:
        clip.active_color = req.active_color
    if req.outline_color is not None:
        clip.outline_color = req.outline_color
    if req.pill_color is not None:
        clip.set_options({"pill_color": req.pill_color})
    if req.pacing_density is not None:
        clip.set_options({"pacing_density": req.pacing_density})
    if req.text_case is not None:
        clip.set_options({"text_case": req.text_case})
        clip.all_caps = req.text_case.strip().lower() in ("all caps", "all_caps", "uppercase")
    if req.font_size is not None:
        clip.font_size = req.font_size
    if req.font_size_percent is not None:
        clip.font_size_percent = req.font_size_percent
    if req.bold is not None:
        clip.bold = req.bold
    if req.italic is not None:
        clip.italic = req.italic
    if req.all_caps is not None and req.text_case is None:
        clip.all_caps = req.all_caps
        clip.set_options({"text_case": "ALL CAPS" if req.all_caps else "As Spoken"})
    if req.text_align is not None:
        clip.text_align = req.text_align
    if req.position_grid is not None:
        clip.position_grid = req.position_grid
    if req.caption_x is not None:
        cx_norm = max(0.05, min(0.95, float(req.caption_x)))
        clip.position_x = round(cx_norm * 100.0, 2)
        clip.set_options({"caption_x": round(cx_norm, 4)})
    elif req.position_x is not None:
        px = max(5.0, min(95.0, float(req.position_x)))
        clip.position_x = px
        clip.set_options({"caption_x": round(px / 100.0, 4)})
    if req.caption_y is not None:
        cy_norm = max(0.08, min(0.92, float(req.caption_y)))
        clip.position_y = round(cy_norm * 100.0, 2)
        clip.set_options({"caption_y": round(cy_norm, 4)})
    elif req.position_y is not None:
        py = max(8.0, min(92.0, float(req.position_y)))
        clip.position_y = py
        clip.set_options({"caption_y": round(py / 100.0, 4)})
    if req.caption_rotation is not None:
        clip.set_options({"caption_rotation": max(-15.0, min(15.0, float(req.caption_rotation)))})
    if req.caption_max_width is not None:
        clip.set_options({"caption_max_width": max(0.45, min(0.90, float(req.caption_max_width)))})
    if req.hold_back_alpha is not None:
        clip.hold_back_alpha = req.hold_back_alpha
    if req.highlight_shape is not None:
        clip.highlight_shape = req.highlight_shape
    if req.emoji_enabled is not None:
        clip.emoji_enabled = req.emoji_enabled
    if req.highlight_power_words is not None:
        clip.highlight_power_words = req.highlight_power_words
    if req.how_phrase_arrives is not None:
        clip.how_phrase_arrives = req.how_phrase_arrives
    if req.outline_width is not None:
        clip.outline_width = req.outline_width
    if req.shadow_depth is not None:
        clip.shadow_depth = req.shadow_depth
    if req.glow_intensity is not None:
        clip.glow_intensity = req.glow_intensity
    if req.letter_spacing is not None:
        clip.letter_spacing = req.letter_spacing
    if req.clean_filler_words is not None:
        clip.clean_filler_words = req.clean_filler_words
    if req.tighten_pacing is not None:
        clip.tighten_pacing = req.tighten_pacing
    if req.silence_threshold is not None:
        clip.silence_threshold = max(0.25, min(2.0, float(req.silence_threshold)))
    if req.censor_profanity is not None:
        clip.censor_profanity = req.censor_profanity

    # Step 3: Color Grading & Effects
    if req.clip_size is not None:
        clip.clip_size = req.clip_size
    elif req.export_resolution is not None:
        clip.clip_size = req.export_resolution
    if req.color_preset is not None:
        clip.color_preset = req.color_preset
    if req.exposure is not None:
        clip.exposure = req.exposure
    if req.brightness is not None:
        clip.brightness = req.brightness
    if req.contrast is not None:
        clip.contrast = req.contrast
    if req.lift_mids is not None:
        clip.lift_mids = req.lift_mids
    if req.warmth is not None:
        clip.warmth = req.warmth
    if req.saturation is not None:
        clip.saturation = req.saturation
    if req.black_and_white is not None:
        clip.black_and_white = req.black_and_white
    if req.vintage is not None:
        clip.vintage = req.vintage
    if req.tint_color is not None:
        clip.tint_color = req.tint_color
    elif req.color_tint is not None:
        clip.tint_color = req.color_tint
    if req.tint_strength is not None:
        clip.tint_strength = req.tint_strength
    if req.glow is not None:
        clip.glow = req.glow
    if req.smooth is not None:
        clip.smooth = req.smooth
    if req.sharpness is not None:
        clip.sharpness = req.sharpness
    if req.blur is not None:
        clip.blur = req.blur
    if req.film_grain is not None:
        clip.film_grain = req.film_grain
    if req.vignette is not None:
        clip.vignette = req.vignette
    if req.bottom_fade is not None:
        clip.bottom_fade = req.bottom_fade
    for opt_field in (
        "preset_intensity",
        "highlights",
        "shadows",
        "export_fps",
        "color_temp",
        "tint_shift",
        "vibrance",
        "matte_fade",
        "halation_glow",
        "lut_path",
        "chroma_split",
        "letterbox",
        "speed_ramp",
        "voice_eq_preset",
        "de_esser",
        "loudness_target",
        "outro_cta_headline",
        "outro_cta_handle",
        "outro_logo_path",
    ):
        val = getattr(req, opt_field, None)
        if val is not None:
            clip.set_options({opt_field: val})

    # Step 4: Branding & Audio
    if req.watermark_path is not None:
        clip.watermark_path = req.watermark_path
    if req.watermark_corner is not None:
        clip.watermark_corner = req.watermark_corner
    if req.watermark_opacity is not None:
        clip.watermark_opacity = req.watermark_opacity
    if req.watermark_size is not None:
        clip.watermark_size = req.watermark_size
    if req.outro_path is not None:
        clip.outro_path = req.outro_path
    if req.bgm_path is not None:
        clip.bgm_path = req.bgm_path
    if req.bgm_volume is not None:
        clip.bgm_volume = req.bgm_volume
    if req.audio_volume is not None:
        clip.audio_volume = req.audio_volume
    if req.normalize_audio is not None:
        clip.normalize_audio = req.normalize_audio
    if req.vocal_clarity is not None:
        clip.vocal_clarity = req.vocal_clarity
    if req.voice_isolation is not None:
        clip.voice_isolation = req.voice_isolation
    if req.auto_sfx is not None:
        clip.auto_sfx = req.auto_sfx
    if req.retention_bar_enabled is not None:
        clip.retention_bar_enabled = req.retention_bar_enabled
    if req.retention_bar_color is not None:
        clip.retention_bar_color = req.retention_bar_color
    if req.outro_cta_enabled is not None:
        clip.outro_cta_enabled = req.outro_cta_enabled
    if req.outro_cta_text is not None:
        clip.outro_cta_text = req.outro_cta_text
    if req.broll_segments is not None:
        clip.broll_json = json.dumps(req.broll_segments)
    if req.speakers is not None:
        clip.set_speakers(req.speakers)
    if req.speaker_lower_thirds is not None:
        clip.speaker_lower_thirds = req.speaker_lower_thirds
    if req.face_safe_captions is not None:
        clip.face_safe_captions = req.face_safe_captions
    if req.callout_cards is not None:
        clip.set_callout_cards(req.callout_cards)
    if req.auto_callouts is not None:
        clip.auto_callouts = req.auto_callouts
    if req.bilingual_enabled is not None:
        clip.bilingual_enabled = req.bilingual_enabled
    if req.secondary_language is not None:
        clip.secondary_language = req.secondary_language
    if req.dubbing_enabled is not None:
        clip.dubbing_enabled = req.dubbing_enabled
    if req.dubbing_language is not None:
        clip.dubbing_language = req.dubbing_language
    if req.dubbing_voice is not None:
        clip.dubbing_voice = req.dubbing_voice
    if req.dubbing_bed_volume is not None:
        clip.dubbing_bed_volume = max(0.0, min(1.0, float(req.dubbing_bed_volume)))
    if req.dubbed_audio_path is not None:
        clip.dubbed_audio_path = req.dubbed_audio_path
    if req.backdrop_mode is not None:
        clip.backdrop_mode = req.backdrop_mode
    if req.backdrop_blur_intensity is not None:
        clip.backdrop_blur_intensity = max(2.0, min(55.0, float(req.backdrop_blur_intensity)))
    if req.backdrop_color is not None:
        clip.backdrop_color = req.backdrop_color
    if req.camera_motion is not None:
        clip.camera_motion = str(req.camera_motion)
    if req.whip_pan_cuts is not None:
        clip.whip_pan_cuts = bool(req.whip_pan_cuts)
    if req.broll_layout is not None:
        clip.broll_layout = str(req.broll_layout)
    if req.depth_pop is not None:
        clip.depth_pop = float(req.depth_pop)
    if req.privacy_blur_boxes is not None:
        clip.set_privacy_blur_boxes(req.privacy_blur_boxes)
    if req.beat_sync_enabled is not None:
        clip.beat_sync_enabled = bool(req.beat_sync_enabled)
    if req.spectrum_enabled is not None:
        clip.spectrum_enabled = bool(req.spectrum_enabled)
    if req.spectrum_position is not None:
        clip.spectrum_position = req.spectrum_position
    if req.spectrum_color is not None:
        clip.spectrum_color = req.spectrum_color
    if req.chapters_enabled is not None:
        clip.chapters_enabled = bool(req.chapters_enabled)
    if req.chapters is not None:
        clip.set_chapters(req.chapters)
    if req.handle_watermark_enabled is not None:
        clip.handle_watermark_enabled = bool(req.handle_watermark_enabled)
    if req.handle_text is not None:
        clip.handle_text = req.handle_text
    if req.handle_platform_icon is not None:
        clip.handle_platform_icon = req.handle_platform_icon
    if req.handle_corner is not None:
        clip.handle_corner = req.handle_corner
    if req.handle_opacity is not None:
        clip.handle_opacity = max(0.20, min(0.90, float(req.handle_opacity)))
    if req.handle_display_mode is not None:
        clip.handle_display_mode = req.handle_display_mode
    if req.punch_zoom_intensity is not None:
        clip.punch_zoom_intensity = max(1.04, min(1.35, float(req.punch_zoom_intensity)))
    if req.hardware_mode is not None:
        clip.hardware_mode = req.hardware_mode

    # Publishing metadata & transcript
    if req.selected_title is not None:
        clip.selected_title = req.selected_title
    if req.youtube_description is not None:
        clip.youtube_description = req.youtube_description
    if req.hashtags is not None:
        clip.hashtags = req.hashtags
    if req.pinned_comment is not None:
        clip.pinned_comment = req.pinned_comment
    if req.privacy_status is not None:
        clip.privacy_status = req.privacy_status
    if req.transcript_words is not None:
        clip.set_transcript(req.transcript_words)

    session.add(clip)
    session.commit()
    session.refresh(clip)

    return {"status": "updated", "clip_id": clip.id}


@app.get("/api/clips/{project_id}/{clip_id}/waveform")
def get_clip_waveform(
    project_id: int,
    clip_id: int,
    bins: int = 150,
    session: Session = Depends(get_session),
):
    """Return 150 normalized RMS amplitude bins over [start_time - 5s, end_time + 5s]."""
    clip = session.get(ViralClip, clip_id)
    if not clip or clip.project_id != project_id:
        raise HTTPException(status_code=404, detail="Clip not found")
    project = session.get(Project, project_id)
    if not project:
        raise HTTPException(status_code=404, detail="Project not found")

    proj_dir = Path(settings.STORAGE_DIR) / "projects" / str(project_id)
    audio_wav_path = proj_dir / "audio_16k.wav"
    transcript_words = clip.get_transcript() or []
    if not transcript_words:
        t_json = proj_dir / "transcript.json"
        if t_json.exists():
            try:
                transcript_words = json.loads(t_json.read_text(encoding="utf-8"))
            except Exception:
                transcript_words = []
    wf = extract_waveform_bins(
        video_or_wav_path=project.source_path or str(audio_wav_path),
        clip_start=clip.start_time,
        clip_end=clip.end_time,
        num_bins=bins,
        audio_wav_path=str(audio_wav_path) if audio_wav_path.exists() else None,
        transcript_words=transcript_words,
    )
    wf["keep_segments"] = clip.get_keep_segments()
    wf["merge_crossfade_sec"] = float(getattr(clip, "merge_crossfade_sec", 0.03) or 0.03)
    return wf


@app.patch("/api/clips/{project_id}/{clip_id}/trim")
def trim_clip_endpoint(
    project_id: int,
    clip_id: int,
    req: TrimClipRequest,
    session: Session = Depends(get_session),
):
    """Update clip in/out boundaries and re-slice transcript words while preserving edits."""
    clip = session.get(ViralClip, clip_id)
    if not clip or clip.project_id != project_id:
        raise HTTPException(status_code=404, detail="Clip not found")
    project = session.get(Project, project_id)
    if not project:
        raise HTTPException(status_code=404, detail="Project not found")

    src_dur = float(project.duration or 0.0)
    if src_dur <= 0 and project.source_path and os.path.exists(project.source_path):
        try:
            src_dur = float(probe_video(project.source_path).get("duration") or 0.0)
        except Exception:
            src_dur = 0.0

    new_start = max(0.0, float(req.start_time))
    new_end = float(req.end_time)
    if src_dur > 0:
        new_end = min(src_dur, new_end)
    if new_end < new_start + 1.0:
        raise HTTPException(status_code=400, detail="Clip duration after trim must be at least 1.0 second.")

    existing_words = clip.get_transcript()
    edit_flags_by_key: Dict[tuple, Dict[str, Any]] = {}
    for w in existing_words:
        if not isinstance(w, dict):
            continue
        key = (round(float(w.get("start", 0.0)), 2), str(w.get("word", "")).strip().lower())
        edit_flags_by_key[key] = w

    project_dir = Path(settings.STORAGE_DIR) / "projects" / str(project_id)
    full_transcript_path = project_dir / "transcript.json"
    sliced_words: List[Dict[str, Any]] = []

    if full_transcript_path.exists():
        try:
            all_words = json.loads(full_transcript_path.read_text(encoding="utf-8"))
            for w in all_words:
                if not isinstance(w, dict):
                    continue
                ws = float(w.get("start", 0.0))
                we = float(w.get("end", ws + 0.1))
                if we >= new_start - 0.1 and ws <= new_end + 0.1:
                    item = dict(w)
                    key = (round(ws, 2), str(w.get("word", "")).strip().lower())
                    if key in edit_flags_by_key:
                        prev = edit_flags_by_key[key]
                        if "deleted" in prev:
                            item["deleted"] = prev["deleted"]
                        if "cut_from_video" in prev:
                            item["cut_from_video"] = prev["cut_from_video"]
                        if prev.get("word"):
                            item["word"] = prev["word"]
                    sliced_words.append(item)
        except Exception as e:
            logger.warning(f"Failed to re-slice transcript.json during trim: {e}")

    if not sliced_words and existing_words:
        # Preserve existing words within the new range
        max_existing_end = max((float(w.get("end", 0.0)) for w in existing_words if isinstance(w, dict)), default=0.0)
        is_relative = max_existing_end <= (clip.duration + 1.0)
        for w in existing_words:
            if not isinstance(w, dict):
                continue
            ws = float(w.get("start", 0.0)) + (clip.start_time if is_relative else 0.0)
            we = float(w.get("end", ws + 0.1)) + (clip.start_time if is_relative else 0.0)
            if we >= new_start - 0.1 and ws <= new_end + 0.1:
                item = dict(w)
                if is_relative:
                    item["start"] = round(ws, 3)
                    item["end"] = round(we, 3)
                sliced_words.append(item)

    clip.start_time = round(new_start, 3)
    clip.end_time = round(new_end, 3)
    clip.set_keep_segments([[clip.start_time, clip.end_time]])
    clip.duration = round(new_end - new_start, 3)
    clip.set_transcript(sliced_words)

    session.add(clip)
    session.commit()
    session.refresh(clip)

    return {"status": "trimmed", "clip": _serialize_clip(clip, project)}


@app.patch("/api/clips/{project_id}/{clip_id}/segments")
def update_clip_segments_endpoint(
    project_id: int,
    clip_id: int,
    req: UpdateSegmentsRequest,
    session: Session = Depends(get_session),
):
    """Persist multi-segment blade cuts (`keep_segments`) and update clip duration."""
    clip = session.get(ViralClip, clip_id)
    if not clip or clip.project_id != project_id:
        raise HTTPException(status_code=404, detail="Clip not found")
    project = session.get(Project, project_id)
    if not project:
        raise HTTPException(status_code=404, detail="Project not found")

    cleaned: List[List[float]] = []
    for item in req.keep_segments or []:
        if isinstance(item, (list, tuple)) and len(item) >= 2:
            s, e = max(0.0, float(item[0])), float(item[1])
            if e > s + 0.2:
                cleaned.append([round(s, 3), round(e, 3)])
    if not cleaned:
        raise HTTPException(status_code=400, detail="At least one valid kept segment (> 0.2s) is required.")

    if req.crossfade_sec is not None:
        clip.merge_crossfade_sec = max(0.0, min(0.6, float(req.crossfade_sec)))
    clip.set_keep_segments(cleaned)
    # Invalidate rendered file so next play / auto-render rebuilds with new segments
    clip.status = "draft"
    clip.render_status = "idle"
    session.add(clip)
    session.commit()
    session.refresh(clip)
    return {"status": "updated", "clip": _serialize_clip(clip, project)}


@app.post("/api/projects/{project_id}/clips/merge")
def merge_project_clips_endpoint(
    project_id: int,
    req: MergeClipsRequest,
    session: Session = Depends(get_session),
):
    """Merge 2 or more clips into one unified multi-segment clip (with optional 0.35s crossfade)."""
    project = session.get(Project, project_id)
    if not project:
        raise HTTPException(status_code=404, detail="Project not found")
    if not req.clip_ids or len(req.clip_ids) < 2:
        raise HTTPException(status_code=400, detail="Select at least 2 clips to merge.")

    by_id = {
        c.id: c
        for c in session.exec(select(ViralClip).where(ViralClip.project_id == project_id)).all()
    }
    ordered_clips: List[ViralClip] = []
    seen = set()
    for cid in req.clip_ids:
        if cid in by_id and cid not in seen:
            ordered_clips.append(by_id[cid])
            seen.add(cid)

    if len(ordered_clips) < 2:
        raise HTTPException(status_code=404, detail="Could not find 2 valid clips to merge in this project.")

    merged = build_merged_clip_model(
        source_clips=ordered_clips,
        project_id=project_id,
        smooth_transition=bool(req.smooth_transition),
        transition_duration=float(req.transition_duration or 0.35),
        custom_title=req.title,
    )
    session.add(merged)
    session.commit()
    session.refresh(merged)

    # Pre-render fast preview synchronously if master source exists so the merged clip plays right away
    if project.source_path and os.path.exists(project.source_path):
        try:
            snap = _snapshot_clip(merged)
            rendered_out = _render_clip_from_snapshot(snap, project.source_path, quality_mode="fast_preview")
            if rendered_out and os.path.exists(rendered_out):
                merged.rendered_path = rendered_out
                merged.rendered_video_path = rendered_out
                merged.status = "ready"
                merged.render_status = "completed"
                merged.render_progress = 100
                merged.render_message = "Merged clip preview ready"
                session.add(merged)
                session.commit()
                session.refresh(merged)
        except Exception as exc:
            logger.warning(f"Inline preview render after clip merge skipped (will render on-demand): {exc}")

    return {"status": "merged", "clip": _serialize_clip(merged, project)}


@app.get("/api/clips/{project_id}/{clip_id}/retention-audit")
def retention_audit_endpoint(
    project_id: int,
    clip_id: int,
    session: Session = Depends(get_session),
):
    """Audit clip second-by-second for hook velocity, dead air, static framing, and WPM dips."""
    clip = session.get(ViralClip, clip_id)
    if not clip or clip.project_id != project_id:
        raise HTTPException(status_code=404, detail="Clip not found")
    project = session.get(Project, project_id)
    if not project:
        raise HTTPException(status_code=404, detail="Project not found")

    proj_dir = Path(settings.STORAGE_DIR) / "projects" / str(project_id)
    audio_wav = proj_dir / "audio_16k.wav"
    face_cache = proj_dir / f"clip_{clip_id}" / "face_track.json"
    face_track_data = None
    if face_cache.exists():
        try:
            face_track_data = json.loads(face_cache.read_text(encoding="utf-8"))
        except Exception:
            face_track_data = None

    return audit_clip_retention(
        clip=clip,
        audio_path=str(audio_wav) if audio_wav.exists() else None,
        face_track=face_track_data,
        video_path=project.source_path if (project.source_path and os.path.exists(project.source_path)) else None,
    )


@app.post("/api/clips/{project_id}/{clip_id}/diarize")
def diarize_clip_endpoint(
    project_id: int,
    clip_id: int,
    req: Optional[DiarizeClipRequest] = Body(default=None),
    session: Session = Depends(get_session),
):
    """Run pure-numpy local acoustic + spatial speaker diarization and assign speaker IDs."""
    clip = session.get(ViralClip, clip_id)
    if not clip or clip.project_id != project_id:
        raise HTTPException(status_code=404, detail="Clip not found")
    project = session.get(Project, project_id)
    if not project:
        raise HTTPException(status_code=404, detail="Project not found")

    words = clip.get_transcript() or []
    proj_dir = Path(settings.STORAGE_DIR) / "projects" / str(project_id)
    audio_wav = proj_dir / "audio_16k.wav"
    media_src = str(audio_wav) if audio_wav.exists() else (project.source_path or "")

    face_cache = proj_dir / f"clip_{clip_id}" / "face_track.json"
    face_track_data = None
    if face_cache.exists():
        try:
            face_track_data = json.loads(face_cache.read_text(encoding="utf-8"))
        except Exception:
            face_track_data = None

    num_spk = int(req.num_speakers) if req else 0
    diar = diarize_clip_speakers(
        audio_or_video_path=media_src,
        words=words,
        face_track=face_track_data,
        clip_start_time=clip.start_time,
        num_speakers=num_spk,
    )
    clip.set_speakers(diar["speakers"])
    clip.set_transcript(diar["words"])
    session.add(clip)
    session.commit()
    session.refresh(clip)

    return {
        "status": "diarized",
        "speakers": diar["speakers"],
        "words": diar["words"],
        "speaker_count": diar["speaker_count"],
        "clip": _serialize_clip(clip, project),
    }


@app.patch("/api/clips/{project_id}/{clip_id}/speakers")
def update_clip_speakers_endpoint(
    project_id: int,
    clip_id: int,
    req: UpdateSpeakersRequest,
    session: Session = Depends(get_session),
):
    """Update speaker names/colors and optional manual per-word speaker assignments."""
    clip = session.get(ViralClip, clip_id)
    if not clip or clip.project_id != project_id:
        raise HTTPException(status_code=404, detail="Clip not found")
    project = session.get(Project, project_id)
    if not project:
        raise HTTPException(status_code=404, detail="Project not found")

    clip.set_speakers(req.speakers)
    if req.transcript_words is not None:
        clip.set_transcript(req.transcript_words)
    session.add(clip)
    session.commit()
    session.refresh(clip)
    return {"status": "saved", "clip": _serialize_clip(clip, project)}


@app.post("/api/clips/{project_id}/{clip_id}/callouts")
def extract_clip_callouts_endpoint(
    project_id: int,
    clip_id: int,
    req: Optional[ExtractCalloutsRequest] = Body(default=None),
    session: Session = Depends(get_session),
):
    """Detect stats, percentages, currencies & multipliers and persist editable Pop-Up Callout Cards."""
    clip = session.get(ViralClip, clip_id)
    if not clip or clip.project_id != project_id:
        raise HTTPException(status_code=404, detail="Clip not found")
    project = session.get(Project, project_id)
    if not project:
        raise HTTPException(status_code=404, detail="Project not found")

    cards = extract_callout_cards(
        words=clip.get_transcript() or [],
        clip_start_time=clip.start_time,
        clip_duration=float(clip.duration or (clip.end_time - clip.start_time)),
        max_cards=int(req.max_cards) if req else 3,
    )
    clip.set_callout_cards(cards)
    clip.auto_callouts = True
    session.add(clip)
    session.commit()
    session.refresh(clip)
    return {
        "status": "generated",
        "callout_cards": cards,
        "clip": _serialize_clip(clip, project),
    }


@app.post("/api/clips/{project_id}/{clip_id}/translate")
def translate_clip_endpoint(
    project_id: int,
    clip_id: int,
    req: TranslateClipRequest,
    session: Session = Depends(get_session),
):
    """Translate clip transcript chunks for Bilingual Stacked Captions (Gemini + offline lexicon)."""
    clip = session.get(ViralClip, clip_id)
    if not clip or clip.project_id != project_id:
        raise HTTPException(status_code=404, detail="Clip not found")
    project = session.get(Project, project_id)
    if not project:
        raise HTTPException(status_code=404, detail="Project not found")

    persisted = get_persisted_settings()
    api_key = str(persisted.get("gemini_api_key") or getattr(settings, "GEMINI_API_KEY", "") or "").strip()
    model = str(persisted.get("gemini_model") or "").strip() or None

    tr = translate_clip_transcript(
        words=clip.get_transcript() or [],
        target_language=req.target_language or "Spanish",
        api_key=api_key,
        model=model,
    )
    clip.set_transcript(tr["words"])
    clip.bilingual_enabled = True
    clip.secondary_language = tr["target_language"]
    session.add(clip)
    session.commit()
    session.refresh(clip)
    return {
        "status": "translated",
        "target_language": tr["target_language"],
        "source": tr["source"],
        "translations": tr["translations"],
        "clip": _serialize_clip(clip, project),
    }


@app.post("/api/clips/{project_id}/{clip_id}/dub")
def dub_clip_endpoint(
    project_id: int,
    clip_id: int,
    req: DubClipRequest,
    session: Session = Depends(get_session),
):
    """Generate 1-click local AI dubbed voice WAV aligned via FFmpeg atempo and ducked original bed."""
    clip = session.get(ViralClip, clip_id)
    if not clip or clip.project_id != project_id:
        raise HTTPException(status_code=404, detail="Clip not found")
    project = session.get(Project, project_id)
    if not project:
        raise HTTPException(status_code=404, detail="Project not found")

    persisted = get_persisted_settings()
    api_key = str(persisted.get("gemini_api_key") or getattr(settings, "GEMINI_API_KEY", "") or "").strip()
    model = str(persisted.get("gemini_model") or "").strip() or None

    lang_slug = re.sub(r"[^a-z0-9]+", "_", (req.target_language or "spanish").lower()).strip("_") or "dub"
    dub_out = Path(settings.STORAGE_DIR) / "projects" / str(project_id) / "dubs" / f"dub_{clip_id}_{lang_slug}.wav"

    eff_dur = max(1.0, float(clip.duration or (clip.end_time - clip.start_time) or 5.0))
    dub_res = dub_clip_audio(
        words=clip.get_transcript() or [],
        clip_duration=eff_dur,
        output_wav_path=str(dub_out),
        clip_start_time=float(clip.start_time),
        target_language=req.target_language or "Spanish",
        voice=req.voice or "default",
        api_key=api_key,
        model=model,
    )
    clip.set_transcript(dub_res["words"])
    clip.dubbing_enabled = True
    clip.dubbing_language = dub_res["target_language"]
    clip.dubbing_voice = dub_res["voice"]
    clip.dubbing_bed_volume = max(0.0, min(1.0, float(req.original_bed_volume)))
    clip.dubbed_audio_path = dub_res["dubbed_audio_path"]
    session.add(clip)
    session.commit()
    session.refresh(clip)
    return {
        "status": "dubbed",
        "dubbed_audio_path": dub_res["dubbed_audio_path"],
        "tts_backend": dub_res["tts_backend"],
        "segment_count": dub_res["segment_count"],
        "clip": _serialize_clip(clip, project),
    }


@app.post("/api/projects/{project_id}/compile-trailer")
def compile_project_trailer_endpoint(
    project_id: int,
    req: Optional[CompileTrailerRequest] = Body(default=None),
    session: Session = Depends(get_session),
):
    """Compile top 3-5 project viral moments into an AI Supercut Trailer clip with fast preview."""
    project = session.get(Project, project_id)
    if not project:
        raise HTTPException(status_code=404, detail="Project not found")

    all_clips = session.exec(
        select(ViralClip).where(ViralClip.project_id == project_id)
    ).all()
    if not all_clips:
        raise HTTPException(status_code=400, detail="Project has no clips to compile into a trailer.")

    # Exclude any earlier supercut trailer from source pool when possible
    non_trailer = [c for c in all_clips if getattr(c, "analysis_source", "") != "supercut_trailer"]
    pool = non_trailer if non_trailer else all_clips

    trailer = compile_project_trailer(
        source_clips=pool,
        project_id=project_id,
        max_moments=int(req.max_moments) if req else 4,
        target_duration_sec=float(req.target_duration_sec) if req else 32.0,
        custom_title=req.title if req else None,
    )
    session.add(trailer)
    session.commit()
    session.refresh(trailer)

    if project.source_path and os.path.exists(project.source_path):
        try:
            snap = _snapshot_clip(trailer)
            rendered_out = _render_clip_from_snapshot(snap, project.source_path, quality_mode="fast_preview")
            if rendered_out and os.path.exists(rendered_out):
                trailer.rendered_path = rendered_out
                trailer.rendered_video_path = rendered_out
                trailer.status = "ready"
                trailer.render_status = "completed"
                trailer.render_progress = 100
                trailer.render_message = "Supercut Trailer ready"
                session.add(trailer)
                session.commit()
                session.refresh(trailer)
        except Exception as exc:
            logger.warning(f"Trailer inline fast preview skipped: {exc}")

    return {"status": "compiled", "clip": _serialize_clip(trailer, project)}


@app.post("/api/clips/{project_id}/{clip_id}/generate-thumbnail")
def generate_thumbnail_endpoint(
    project_id: int,
    clip_id: int,
    req: GenerateThumbnailRequest,
    session: Session = Depends(get_session),
):
    """Generate depth-layered 1080x1920 or 1280x720 AI cover thumbnail with subject-over-text compositing."""
    clip = session.get(ViralClip, clip_id)
    if not clip or clip.project_id != project_id:
        raise HTTPException(status_code=404, detail="Clip not found")
    project = session.get(Project, project_id)
    if not project or not project.source_path or not os.path.exists(project.source_path):
        raise HTTPException(status_code=404, detail="Project source video not found")

    fmt = "16:9" if (req.format or "").strip() == "16:9" else "9:16"
    headline = (req.headline or "").strip() or (clip.hook_headline or clip.selected_title or "WATCH THIS")
    res = generate_depth_thumbnail(
        video_path=project.source_path,
        clip_id=clip_id,
        project_id=project_id,
        timestamp=req.timestamp,
        clip_start_time=float(clip.start_time or 0.0),
        clip_duration=float(clip.duration or 5.0),
        headline=headline,
        aspect_ratio=fmt,
        frame_layout=clip.frame_layout or "auto_track",
        zoom=float(clip.zoom or 1.0),
        pan_x_offset=float(clip.pan_x_offset or 0.0),
        depth_layer=bool(req.depth_layer),
        accent_color=req.accent_color or "#C6F32F",
        rim_glow=bool(req.rim_glow),
        badge_text=req.badge_text or "",
        best_frames=clip.get_best_frames(),
    )
    return {"status": "generated", **res}


@app.post("/api/clips/{project_id}/{clip_id}/generate-chapters")
def generate_chapters_endpoint(
    project_id: int,
    clip_id: int,
    req: GenerateChaptersRequest = GenerateChaptersRequest(),
    session: Session = Depends(get_session),
):
    """Auto-detect 2-4 crisp timeline mini-chapters from timed words (Gemini + deterministic fallback)."""
    from app.services.chapter_engine import generate_clip_chapters

    clip = session.get(ViralClip, clip_id)
    if not clip or clip.project_id != project_id:
        raise HTTPException(status_code=404, detail="Clip not found")

    persisted = get_persisted_settings()
    api_key = str(persisted.get("gemini_api_key") or "").strip() or None
    model = str(persisted.get("gemini_model") or "").strip() or None

    chapters = generate_clip_chapters(
        words=clip.get_transcript(),
        clip_start_time=float(clip.start_time or 0.0),
        clip_duration=max(1.0, float(clip.duration or 30.0)),
        max_chapters=max(2, min(6, int(req.max_chapters or 4))),
        api_key=api_key,
        model=model,
    )
    clip.set_chapters(chapters)
    if req.enable:
        clip.chapters_enabled = True
    session.add(clip)
    session.commit()
    session.refresh(clip)
    return {
        "status": "generated",
        "clip_id": clip.id,
        "chapters_enabled": bool(clip.chapters_enabled),
        "chapters": chapters,
    }


@app.get("/api/clips/{project_id}/{clip_id}/preflight")
def clip_preflight_endpoint(
    project_id: int,
    clip_id: int,
    session: Session = Depends(get_session),
):
    """Run platform demonetization, audio clipping, and safe-zone UI overlap audit with 1-Click Fix patches."""
    from app.services.safety_scanner import scan_clip_preflight

    clip = session.get(ViralClip, clip_id)
    if not clip or clip.project_id != project_id:
        raise HTTPException(status_code=404, detail="Clip not found")
    project = session.get(Project, project_id)
    media_path = project.source_path if (project and project.source_path and os.path.exists(project.source_path)) else None
    return scan_clip_preflight(clip, media_path=media_path)


@app.post("/api/analytics/import-csv")
def import_analytics_csv_endpoint(
    req: ImportAnalyticsCsvRequest,
    session: Session = Depends(get_session),
):
    """Import YouTube Studio Analytics CSV rows to correlate views/retention with export styles."""
    from app.services.analytics_ledger import import_youtube_analytics_csv

    try:
        return import_youtube_analytics_csv(session, req.csv_text)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))


@app.get("/api/analytics/formula")
def get_analytics_formula_endpoint(session: Session = Depends(get_session)):
    """Return Your Top Performing Formula aggregated from export telemetry & imported CSV analytics."""
    from app.services.analytics_ledger import compute_top_performing_formula

    return compute_top_performing_formula(session)


@app.get("/api/clips/{project_id}/{clip_id}/webcam-region")
def get_clip_webcam_region_endpoint(
    project_id: int,
    clip_id: int,
    refresh: bool = False,
    session: Session = Depends(get_session),
):
    """Return SQLite-cached or freshly detected 16-frame source webcam crop box."""
    from app.services.webcam_detector import detect_source_webcam

    clip = session.get(ViralClip, clip_id)
    if not clip or clip.project_id != project_id:
        raise HTTPException(status_code=404, detail="Clip not found")

    cached = clip.get_source_webcam_region()
    if cached and not refresh:
        return {"clip_id": clip.id, "project_id": project_id, **cached}

    project = session.get(Project, project_id)
    if not project or not project.source_path or not os.path.exists(project.source_path):
        raise HTTPException(status_code=404, detail="Project source video not found")

    locked = (cached or {}).get("locked_zone") if cached else None
    region = detect_source_webcam(
        video_path=project.source_path,
        start_time=float(clip.start_time or 0.0),
        end_time=float(clip.end_time or (float(clip.start_time or 0.0) + max(1.0, float(clip.duration or 5.0)))),
        num_samples=16,
        locked_zone=locked if (locked and locked != "auto") else None,
    )
    clip.set_source_webcam_region(region)
    session.add(clip)
    session.commit()
    session.refresh(clip)
    return {"clip_id": clip.id, "project_id": project_id, **region}


@app.patch("/api/clips/{project_id}/{clip_id}/webcam-region")
def update_clip_webcam_region_endpoint(
    project_id: int,
    clip_id: int,
    req: UpdateWebcamRegionRequest,
    session: Session = Depends(get_session),
):
    """Update or lock source camera zone (Auto / Top Left / Top Right / Bottom Left / Bottom Right / Center)."""
    from app.services.webcam_detector import detect_source_webcam, compute_zone_crop_box
    from app.services.proxy import probe_video

    clip = session.get(ViralClip, clip_id)
    if not clip or clip.project_id != project_id:
        raise HTTPException(status_code=404, detail="Clip not found")
    project = session.get(Project, project_id)
    src_path = project.source_path if (project and project.source_path and os.path.exists(project.source_path)) else None

    existing = clip.get_source_webcam_region() or {}
    zone = (req.source_zone or existing.get("locked_zone") or "auto").strip().lower()

    if req.x is not None and req.y is not None and req.w is not None and req.h is not None:
        rw = max(0.10, min(1.0, float(req.w)))
        rh = max(0.10, min(1.0, float(req.h)))
        rx = max(0.0, min(1.0 - rw, float(req.x)))
        ry = max(0.0, min(1.0 - rh, float(req.y)))
        src_w, src_h = 1920, 1080
        if src_path:
            try:
                m = probe_video(src_path)
                src_w, src_h = m.get("width") or 1920, m.get("height") or 1080
            except Exception:
                pass
        region = {
            "detected": True,
            "webcam_detected": True,
            "source_zone": zone if zone != "auto" else "custom",
            "locked_zone": zone if zone != "auto" else "custom",
            "x": round(rx, 4),
            "y": round(ry, 4),
            "w": round(rw, 4),
            "h": round(rh, 4),
            "aspect_ratio": round((rw * src_w) / max(1e-4, rh * src_h), 4),
            "face_cx": round(rx + rw * 0.5, 4),
            "face_cy": round(ry + rh * 0.38, 4),
            "action_cx": 0.5,
            "action_cy": 0.48,
        }
    elif src_path:
        region = detect_source_webcam(
            video_path=src_path,
            start_time=float(clip.start_time or 0.0),
            end_time=float(clip.end_time or (float(clip.start_time or 0.0) + max(1.0, float(clip.duration or 5.0)))),
            num_samples=16,
            locked_zone=None if zone == "auto" else zone,
        )
    else:
        region = compute_zone_crop_box("top_left" if zone == "auto" else zone, 1920, 1080)

    clip.set_source_webcam_region(region)
    session.add(clip)
    session.commit()
    session.refresh(clip)
    return {"clip_id": clip.id, "project_id": project_id, **region}


@app.get("/api/clips/{project_id}/{clip_id}/framing-map")
def get_clip_framing_map_endpoint(
    project_id: int,
    clip_id: int,
    refresh: bool = False,
    session: Session = Depends(get_session),
):
    """Return SQLite-cached or freshly analyzed 5-fps scene/shot/face/webcam visual map & AI framing recommendation."""
    from app.services.vision_analyzer import analyze_clip_visuals
    from app.services.auto_framer import recommend_optimal_framing

    clip = session.get(ViralClip, clip_id)
    if not clip or clip.project_id != project_id:
        raise HTTPException(status_code=404, detail="Clip not found")

    project = session.get(Project, project_id)
    if not project or not project.source_path or not os.path.exists(project.source_path):
        raise HTTPException(status_code=404, detail="Project source video not found")

    vmap = clip.get_visual_map()
    if not vmap or refresh:
        cached_cam = clip.get_source_webcam_region() or {}
        locked_z = cached_cam.get("locked_zone")
        vmap = analyze_clip_visuals(
            video_path=project.source_path,
            start_time=float(clip.start_time or 0.0),
            end_time=float(clip.end_time or (float(clip.start_time or 0.0) + max(1.0, float(clip.duration or 5.0)))),
            locked_zone=locked_z if (locked_z and locked_z not in ("auto", "custom")) else None,
            use_cache=not refresh,
        )
        clip.set_visual_map(vmap)
        if not cached_cam and vmap.get("webcam"):
            clip.set_source_webcam_region(vmap["webcam"])
        session.add(clip)
        session.commit()
        session.refresh(clip)

    return {
        "clip_id": clip.id,
        "project_id": project_id,
        "visual_map": vmap,
        "framing_override": clip.get_framing_override(),
        "source_webcam_region": clip.get_source_webcam_region(),
        "recommended": recommend_optimal_framing(vmap),
    }


@app.patch("/api/clips/{project_id}/{clip_id}/framing-override")
def update_clip_framing_override_endpoint(
    project_id: int,
    clip_id: int,
    req: FramingOverrideRequest,
    session: Session = Depends(get_session),
):
    """Persist custom interactive webcam crop box, subject focus anchor, and camera lock state."""
    from app.services.proxy import probe_video

    clip = session.get(ViralClip, clip_id)
    if not clip or clip.project_id != project_id:
        raise HTTPException(status_code=404, detail="Clip not found")
    project = session.get(Project, project_id)
    src_path = project.source_path if (project and project.source_path and os.path.exists(project.source_path)) else None

    if req.reset:
        clip.set_framing_override(None)
        session.add(clip)
        session.commit()
        session.refresh(clip)
        return {
            "clip_id": clip.id,
            "project_id": project_id,
            "framing_override": None,
            "source_webcam_region": clip.get_source_webcam_region(),
            "visual_map": clip.get_visual_map(),
        }

    existing = clip.get_framing_override() or {}
    updated = dict(existing)

    if req.webcam_box is not None and isinstance(req.webcam_box, dict):
        bw = max(0.08, min(1.0, float(req.webcam_box.get("w", 0.30))))
        bh = max(0.08, min(1.0, float(req.webcam_box.get("h", 0.30))))
        bx = max(0.0, min(1.0 - bw, float(req.webcam_box.get("x", 0.0))))
        by = max(0.0, min(1.0 - bh, float(req.webcam_box.get("y", 0.0))))
        clean_box = {
            "x": round(bx, 4),
            "y": round(by, 4),
            "w": round(bw, 4),
            "h": round(bh, 4),
        }
        updated["webcam_box"] = clean_box

        src_w, src_h = 1920, 1080
        if src_path:
            try:
                m = probe_video(src_path)
                src_w, src_h = m.get("width") or 1920, m.get("height") or 1080
            except Exception:
                pass
        pix_ar = round((bw * src_w) / max(1e-4, bh * src_h), 4)
        cam_reg = {
            "detected": True,
            "webcam_detected": True,
            "source_zone": "custom",
            "locked_zone": "custom",
            "x": clean_box["x"],
            "y": clean_box["y"],
            "w": clean_box["w"],
            "h": clean_box["h"],
            "aspect": pix_ar,
            "aspect_ratio": pix_ar,
            "face_cx": round(clean_box["x"] + clean_box["w"] * 0.5, 4),
            "face_cy": round(clean_box["y"] + clean_box["h"] * 0.38, 4),
            "action_cx": 0.5,
            "action_cy": 0.48,
        }
        clip.set_source_webcam_region(cam_reg)

    if req.focus_anchor is not None and isinstance(req.focus_anchor, dict):
        ax = max(0.05, min(0.95, float(req.focus_anchor.get("x", 0.5))))
        ay = max(0.05, min(0.95, float(req.focus_anchor.get("y", 0.42))))
        updated["focus_anchor"] = {"x": round(ax, 4), "y": round(ay, 4)}

    if req.lock_camera is not None:
        updated["lock_camera"] = bool(req.lock_camera)

    clip.set_framing_override(updated)
    session.add(clip)
    session.commit()
    session.refresh(clip)
    return {
        "clip_id": clip.id,
        "project_id": project_id,
        "framing_override": clip.get_framing_override(),
        "source_webcam_region": clip.get_source_webcam_region(),
        "visual_map": clip.get_visual_map(),
    }


@app.post("/api/projects/{project_id}/auto-frame-all")
def auto_frame_all_clips_endpoint(
    project_id: int,
    session: Session = Depends(get_session),
):
    """Run unified visual analyzer + Smart Auto-Frame AI Router across every clip in a project."""
    from app.services.vision_analyzer import analyze_clip_visuals
    from app.services.auto_framer import recommend_optimal_framing

    project = session.get(Project, project_id)
    if not project:
        raise HTTPException(status_code=404, detail="Project not found")
    if not project.source_path or not os.path.exists(project.source_path):
        raise HTTPException(status_code=404, detail="Project source video not found")

    project_clips = session.exec(
        select(ViralClip).where(ViralClip.project_id == project_id)
    ).all()
    updated_count = 0
    for c in project_clips:
        vmap = analyze_clip_visuals(
            video_path=project.source_path,
            start_time=float(c.start_time or 0.0),
            end_time=float(c.end_time or (float(c.start_time or 0.0) + max(1.0, float(c.duration or 5.0)))),
            use_cache=True,
        )
        rec = recommend_optimal_framing(vmap)
        c.frame_layout = rec["frame_layout"]
        c.webcam_corner = rec["webcam_corner"]
        c.set_visual_map(vmap)
        if rec.get("source_webcam_region"):
            c.set_source_webcam_region(rec["source_webcam_region"])
        session.add(c)
        updated_count += 1

    session.commit()
    for c in project_clips:
        session.refresh(c)

    return {
        "status": "completed",
        "project_id": project_id,
        "updated_count": updated_count,
        "clips": [_serialize_clip(c, project) for c in project_clips],
    }


def _ensure_clip_multimodal_and_best_frames(
    project: Project,
    clip: ViralClip,
    session: Session,
    *,
    force_reanalyze: bool = False,
) -> Dict[str, Any]:
    """
    Lazy-compute or refresh Project multimodal analysis (`audio_analyzer` + `video_analyzer` +
    `multimodal_fusion`) and Clip Top 6 Best Frames, persisting both to SQLite.
    """
    from app.services.audio_analyzer import analyze_audio_track
    from app.services.video_analyzer import analyze_video_track
    from app.services.multimodal_fusion import fuse_multimodal_tracks
    from app.services.best_frame_engine import extract_best_frames

    project_id = int(project.id or 0)
    clip_id = int(clip.id or 0)
    mm_data = project.get_multimodal_analysis() if not force_reanalyze else None
    vid_analysis: Optional[Dict[str, Any]] = None
    aud_analysis: Optional[Dict[str, Any]] = None

    p_dir = Path(settings.STORAGE_DIR) / "projects" / str(project_id)
    audio_wav = p_dir / "audio_16k.wav"
    words_all: List[Dict[str, Any]] = []
    tr_json = p_dir / "transcript.json"
    if tr_json.exists():
        try:
            loaded_w = json.loads(tr_json.read_text(encoding="utf-8"))
            if isinstance(loaded_w, list):
                words_all = loaded_w
        except Exception:
            words_all = []
    if not words_all:
        # Reconstruct project-level words from clip transcript shifted by clip start_time
        for w in clip.get_transcript():
            words_all.append({
                "word": w.get("word", ""),
                "start": float(w.get("start", 0.0)) + float(clip.start_time or 0.0),
                "end": float(w.get("end", 0.0)) + float(clip.start_time or 0.0),
            })

    if force_reanalyze or not mm_data or not mm_data.get("multimodal_timeline"):
        audio_src = str(audio_wav) if audio_wav.exists() else project.source_path
        aud_analysis = analyze_audio_track(audio_src, word_segments=words_all)
        vid_analysis = analyze_video_track(project.source_path, sample_fps=4.0)
        mm_data = fuse_multimodal_tracks(
            aud_analysis,
            vid_analysis,
            word_segments=words_all,
            duration=float(project.duration or vid_analysis.get("summary", {}).get("duration") or 10.0),
        )
        project.set_multimodal_analysis(mm_data)
        session.add(project)
        session.commit()
        session.refresh(project)
    else:
        aud_analysis = {
            "summary": mm_data.get("summary") or {},
            "timeline": mm_data.get("multimodal_timeline") or [],
        }

    existing_bf = clip.get_best_frames() if not force_reanalyze else []
    imgs_valid = bool(existing_bf) and all(
        bf.get("image_path") and os.path.exists(str(bf["image_path"]))
        for bf in existing_bf
    )

    suggested_start: Optional[float] = None
    if not imgs_valid or force_reanalyze:
        bf_dir = Path(settings.STORAGE_DIR) / "assets" / "best_frames" / str(project_id)
        bf_dir.mkdir(parents=True, exist_ok=True)
        bf_res = extract_best_frames(
            video_path=project.source_path,
            video_analysis=vid_analysis,
            audio_analysis=aud_analysis,
            start_time=float(clip.start_time or 0.0),
            end_time=float(clip.end_time or (float(clip.start_time or 0.0) + max(1.0, float(clip.duration or 5.0)))),
            output_dir=str(bf_dir),
            clip_id=clip_id,
            project_id=project_id,
        )
        existing_bf = bf_res.get("frames") or []
        suggested_start = bf_res.get("suggested_start_time")
        clip.set_best_frames(existing_bf)
        session.add(clip)
        session.commit()
        session.refresh(clip)

    # Filter multimodal timeline to clip window while preserving full project context summary
    clip_s = float(clip.start_time or 0.0)
    clip_e = float(clip.end_time or (clip_s + max(1.0, float(clip.duration or 5.0))))
    full_tl = mm_data.get("multimodal_timeline") or []
    clip_tl = [
        pt for pt in full_tl
        if (clip_s - 0.15) <= float(pt.get("t", -1.0)) <= (clip_e + 0.15)
    ] or full_tl

    clip_shifts = [
        sh for sh in (mm_data.get("emotion_shifts") or [])
        if (clip_s - 0.15) <= float(sh.get("t", -1.0)) <= (clip_e + 0.15)
    ]
    clip_ac_events = [
        ev for ev in (mm_data.get("acoustic_events") or [])
        if float(ev.get("end", ev.get("t", -1.0))) >= (clip_s - 0.25) and float(ev.get("t", -1.0)) <= (clip_e + 0.25)
    ]

    # Build localized window emotion breakdown for Emotion Breakdown Card
    from app.services.multimodal_fusion import score_window_from_fusion
    win_emo = score_window_from_fusion(mm_data, clip_s, clip_e) or {}
    f_dist: Dict[str, int] = {}
    v_dist: Dict[str, int] = {}
    for pt in clip_tl:
        fe = str(pt.get("dominant_emotion") or "neutral")
        ve = str(pt.get("vocal_emotion") or "calm_storytelling")
        f_dist[fe] = f_dist.get(fe, 0) + 1
        v_dist[ve] = v_dist.get(ve, 0) + 1
    n_tl = max(1, len(clip_tl))
    facial_dist_clip = {k: round(v / float(n_tl), 3) for k, v in sorted(f_dist.items(), key=lambda x: -x[1])}
    vocal_dist_clip = {k: round(v / float(n_tl), 3) for k, v in sorted(v_dist.items(), key=lambda x: -x[1])}
    opts = clip.get_options()

    return {
        "clip_id": clip_id,
        "project_id": project_id,
        "start_time": clip_s,
        "end_time": clip_e,
        "suggested_start_time": suggested_start,
        "frames": existing_bf,
        "multimodal_summary": mm_data.get("summary") or {},
        "emotion_summary": mm_data.get("emotion_summary") or {},
        "emotion_shifts": clip_shifts,
        "acoustic_events": clip_ac_events,
        "clip_emotion_breakdown": {
            "dominant_facial_emotion": win_emo.get("dominant_facial_emotion") or opts.get("dominant_emotion") or "neutral",
            "dominant_vocal_emotion": win_emo.get("dominant_vocal_emotion") or opts.get("vocal_emotion") or "calm_storytelling",
            "avg_arousal": win_emo.get("avg_arousal", opts.get("emotion_arousal", 40.0)),
            "peak_arousal": win_emo.get("peak_arousal", opts.get("emotion_arousal", 45.0)),
            "avg_valence": win_emo.get("avg_valence", opts.get("emotion_valence", 0.0)),
            "emotion_delta_peak": win_emo.get("emotion_delta_peak", 0.0),
            "narrative_arc": opts.get("narrative_arc"),
            "arc_label": opts.get("arc_label"),
            "facial_distribution": facial_dist_clip,
            "vocal_distribution": vocal_dist_clip,
        },
        "multimodal_timeline": clip_tl,
        "scene_cuts": [
            c for c in (mm_data.get("scene_cuts") or [])
            if clip_s <= float(c.get("t", -1.0)) <= clip_e
        ],
        "black_intervals": mm_data.get("black_intervals") or [],
        "peak_moments": mm_data.get("peak_moments") or [],
    }


@app.get("/api/clips/{project_id}/{clip_id}/best-frames")
def get_clip_best_frames_endpoint(
    project_id: int,
    clip_id: int,
    session: Session = Depends(get_session),
):
    """Return Top 6 Best Frames and synchronized multimodal telemetry for the clip."""
    clip = session.get(ViralClip, clip_id)
    if not clip or clip.project_id != project_id:
        raise HTTPException(status_code=404, detail="Clip not found")
    project = session.get(Project, project_id)
    if not project or not project.source_path or not os.path.exists(project.source_path):
        raise HTTPException(status_code=404, detail="Project source video not found")
    return _ensure_clip_multimodal_and_best_frames(project, clip, session, force_reanalyze=False)


@app.get("/api/clips/{project_id}/{clip_id}/best-frames/{frame_index}/image")
def get_clip_best_frame_image_endpoint(
    project_id: int,
    clip_id: int,
    frame_index: int,
    session: Session = Depends(get_session),
):
    """Serve high-res JPEG still for Best Frame `frame_index` (0..5)."""
    clip = session.get(ViralClip, clip_id)
    if not clip or clip.project_id != project_id:
        raise HTTPException(status_code=404, detail="Clip not found")
    project = session.get(Project, project_id)
    if not project or not project.source_path or not os.path.exists(project.source_path):
        raise HTTPException(status_code=404, detail="Project source video not found")

    data = _ensure_clip_multimodal_and_best_frames(project, clip, session, force_reanalyze=False)
    frames = data.get("frames") or []
    match = next((bf for bf in frames if int(bf.get("index", -1)) == int(frame_index)), None)
    if match is None and 0 <= int(frame_index) < len(frames):
        match = frames[int(frame_index)]
    if not match or not match.get("image_path") or not os.path.exists(str(match["image_path"])):
        raise HTTPException(status_code=404, detail="Best frame image not found")
    return FileResponse(str(match["image_path"]), media_type="image/jpeg")


@app.get("/api/clips/{project_id}/{clip_id}/preset-previews")
def get_clip_preset_previews_endpoint(
    project_id: int,
    clip_id: int,
    lut_path: Optional[str] = None,
    force: bool = False,
    session: Session = Depends(get_session),
):
    """Return cached 160x90 real FFmpeg-graded thumbnail previews for all 9 Studio Color Presets + custom LUT."""
    from app.services.filters import generate_clip_preset_previews

    clip = session.get(ViralClip, clip_id)
    if not clip or clip.project_id != project_id:
        raise HTTPException(status_code=404, detail="Clip not found")
    project = session.get(Project, project_id)
    if not project or not project.source_path or not os.path.exists(project.source_path):
        raise HTTPException(status_code=404, detail="Project source video not found")

    clip_opts = clip.get_options()
    eff_lut = lut_path if lut_path is not None else clip_opts.get("lut_path")

    # Prefer the top-ranked clearest reference frame from best_frames if cached, otherwise clip midpoint
    ref_ts = (float(clip.start_time) + float(clip.end_time)) * 0.5
    best_frames = clip.get_best_frames()
    if isinstance(best_frames, list) and best_frames:
        first_bf = best_frames[0]
        if isinstance(first_bf, dict):
            cand_ts = first_bf.get("timestamp", first_bf.get("t"))
            if cand_ts is not None:
                try:
                    t_val = float(cand_ts)
                    if float(clip.start_time) <= t_val <= float(clip.end_time):
                        ref_ts = t_val
                except (TypeError, ValueError):
                    pass

    return generate_clip_preset_previews(
        video_path=project.source_path,
        project_id=project_id,
        clip_id=clip_id,
        ref_timestamp=ref_ts,
        lut_path=eff_lut,
        force=force,
    )


@app.post("/api/clips/{project_id}/{clip_id}/reanalyze")
def reanalyze_clip_multimodal_endpoint(
    project_id: int,
    clip_id: int,
    session: Session = Depends(get_session),
):
    """Force fresh acoustic prosody, frame-quality vision analysis, and Best Frame extraction."""
    clip = session.get(ViralClip, clip_id)
    if not clip or clip.project_id != project_id:
        raise HTTPException(status_code=404, detail="Clip not found")
    project = session.get(Project, project_id)
    if not project or not project.source_path or not os.path.exists(project.source_path):
        raise HTTPException(status_code=404, detail="Project source video not found")
    payload = _ensure_clip_multimodal_and_best_frames(project, clip, session, force_reanalyze=True)
    return {"status": "reanalyzed", **payload}


@app.get("/api/clips/{project_id}/{clip_id}/broll-suggestions")
def get_broll_suggestions_endpoint(
    project_id: int,
    clip_id: int,
    session: Session = Depends(get_session),
):
    """Extract transcript keywords and match local `storage/broll/` or optional Pexels B-Roll clips."""
    clip = session.get(ViralClip, clip_id)
    if not clip or clip.project_id != project_id:
        raise HTTPException(status_code=404, detail="Clip not found")
    persisted = get_persisted_settings()
    pexels_key = str(persisted.get("pexels_api_key") or "").strip()
    suggestions = suggest_broll_cutaways(
        words=clip.get_transcript(),
        clip_start_time=float(clip.start_time),
        clip_duration=max(1.0, float(clip.duration)),
        pexels_api_key=pexels_key,
    )
    return {"clip_id": clip.id, "suggestions": suggestions}


@app.get("/api/clips/{project_id}/{clip_id}/export-nle")
def export_clip_nle_endpoint(
    project_id: int,
    clip_id: int,
    format: str = "fcpxml",
    session: Session = Depends(get_session),
):
    """Export clip timeline & captions to `.fcpxml`, Premiere `.xml`, `.srt`, or `.zip` Studio Bundle."""
    clip = session.get(ViralClip, clip_id)
    if not clip or clip.project_id != project_id:
        raise HTTPException(status_code=404, detail="Clip not found")
    project = session.get(Project, project_id)
    if not project:
        raise HTTPException(status_code=404, detail="Project not found")

    res = export_clip_nle(clip, project, format_name=format)
    return Response(
        content=res["content"],
        media_type=res["media_type"],
        headers={"Content-Disposition": f'attachment; filename="{res["filename"]}"'},
    )


def _resolve_gemini_runtime() -> tuple[Optional[str], Optional[str]]:
    """Resolve persisted or env Gemini API key and preferred model."""
    persisted = get_persisted_settings()
    key = str(
        persisted.get("gemini_api_key")
        or settings.GEMINI_API_KEY
        or os.environ.get("GEMINI_API_KEY", "")
    ).strip()
    if not key or key == "your_gemini_api_key_here":
        key = ""
    model = str(persisted.get("gemini_model") or settings.GEMINI_MODEL or "").strip() or None
    return (key or None), model


@app.post("/api/clips/{project_id}/{clip_id}/generate-hooks")
def generate_clip_hooks_endpoint(
    project_id: int,
    clip_id: int,
    session: Session = Depends(get_session),
):
    """Generate 3 AI + NLP viral hook banner & opening line variants for A/B selection."""
    clip = session.get(ViralClip, clip_id)
    project = session.get(Project, project_id)
    if not clip or not project or clip.project_id != project_id:
        raise HTTPException(status_code=404, detail="Clip not found")
    api_key, gemini_model = _resolve_gemini_runtime()
    res = generate_hook_variants(
        transcript_words=clip.get_transcript(),
        current_headline=clip.hook_headline or "",
        topic_focus=getattr(project, "topic_focus", None),
        api_key=api_key,
        preferred_model=gemini_model,
    )
    return {"clip_id": clip.id, "project_id": project_id, **res}


@app.post("/api/clips/{project_id}/{clip_id}/generate-social-copy")
def generate_clip_social_copy_endpoint(
    project_id: int,
    clip_id: int,
    session: Session = Depends(get_session),
):
    """Generate tailored YouTube Shorts, TikTok, Instagram Reels, and X copy + hashtags."""
    clip = session.get(ViralClip, clip_id)
    project = session.get(Project, project_id)
    if not clip or not project or clip.project_id != project_id:
        raise HTTPException(status_code=404, detail="Clip not found")
    api_key, gemini_model = _resolve_gemini_runtime()
    res = generate_multi_platform_copy(
        transcript_words=clip.get_transcript(),
        hook_headline=clip.hook_headline or "",
        selected_title=clip.selected_title or "",
        existing_description=clip.youtube_description or "",
        api_key=api_key,
        preferred_model=gemini_model,
    )
    return {"clip_id": clip.id, "project_id": project_id, **res}


@app.post("/api/clips/{project_id}/{clip_id}/export-publish-pack")
def export_publish_pack_endpoint(
    project_id: int,
    clip_id: int,
    req: Optional[Dict[str, Any]] = Body(default=None),
    session: Session = Depends(get_session),
):
    """Build and download a multi-platform Publish Pack .zip (9:16, 4:5, 16:9, Cover, SRT, Social Copy)."""
    clip = session.get(ViralClip, clip_id)
    project = session.get(Project, project_id)
    if not clip or not project or clip.project_id != project_id:
        raise HTTPException(status_code=404, detail="Clip not found")
    api_key, gemini_model = _resolve_gemini_runtime()
    quality_mode = str((req or {}).get("quality_mode") or "fast_preview")
    try:
        zip_path, zip_filename = build_publish_pack_zip(
            clip=clip,
            project=project,
            api_key=api_key,
            gemini_model=gemini_model,
            quality_mode=quality_mode,
        )
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc))
    try:
        from app.services.analytics_ledger import record_export_event
        record_export_event(session, clip, event_type="publish_pack")
    except Exception:
        pass
    return FileResponse(
        path=zip_path,
        media_type="application/zip",
        filename=zip_filename,
        headers={"Content-Disposition": f'attachment; filename="{zip_filename}"'},
    )


@app.get("/api/projects/{project_id}/transcript")
def search_project_transcript_endpoint(
    project_id: int,
    q: str = "",
    session: Session = Depends(get_session),
):
    """Search the full project transcript (`transcript.json`) and return grouped matching hits."""
    project = session.get(Project, project_id)
    if not project:
        raise HTTPException(status_code=404, detail="Project not found")

    proj_dir = Path(settings.STORAGE_DIR) / "projects" / str(project_id)
    t_path = proj_dir / "transcript.json"
    all_words: List[Dict[str, Any]] = []
    if t_path.exists():
        try:
            all_words = json.loads(t_path.read_text(encoding="utf-8"))
        except Exception:
            all_words = []

    if not all_words:
        clips = session.exec(select(ViralClip).where(ViralClip.project_id == project_id)).all()
        for c in clips:
            all_words.extend(c.get_transcript())

    # Group words into ~8-word searchable windows
    blocks: List[Dict[str, Any]] = []
    step = 8
    query_low = (q or "").strip().lower()
    for i in range(0, len(all_words), step):
        chunk = [w for w in all_words[i:i + step] if isinstance(w, dict)]
        if not chunk:
            continue
        text = " ".join(str(w.get("word", "")).strip() for w in chunk).strip()
        if not text:
            continue
        if query_low and query_low not in text.lower():
            continue
        s_t = round(float(chunk[0].get("start", 0.0)), 2)
        e_t = round(max(s_t + 0.5, float(chunk[-1].get("end", s_t + 2.0))), 2)
        blocks.append({"start": s_t, "end": e_t, "text": text})

    return {
        "project_id": project_id,
        "total_words": len(all_words),
        "matches": blocks[:120],
    }


@app.post("/api/projects/{project_id}/clips/manual")
def create_manual_clip_endpoint(
    project_id: int,
    req: Dict[str, Any],
    background_tasks: BackgroundTasks = BackgroundTasks(),
    session: Session = Depends(get_session),
):
    """Create a manual ViralClip in SQLite from any [start_time, end_time] interval and pre-render fast preview."""
    project = session.get(Project, project_id)
    if not project:
        raise HTTPException(status_code=404, detail="Project not found")

    start_t = max(0.0, float(req.get("start_time", 0.0)))
    end_t = float(req.get("end_time", start_t + 15.0))
    if end_t < start_t + 1.0:
        raise HTTPException(status_code=400, detail="Manual clip duration must be at least 1.0 second.")

    proj_dir = Path(settings.STORAGE_DIR) / "projects" / str(project_id)
    t_path = proj_dir / "transcript.json"
    clip_words: List[Dict[str, Any]] = []
    if t_path.exists():
        try:
            all_words = json.loads(t_path.read_text(encoding="utf-8"))
            for w in all_words:
                if not isinstance(w, dict):
                    continue
                ws = float(w.get("start", 0.0))
                we = float(w.get("end", ws + 0.1))
                if we >= start_t - 0.1 and ws <= end_t + 0.1:
                    rel_w = dict(w)
                    rel_w["start"] = round(max(0.0, ws - start_t), 3)
                    rel_w["end"] = round(max(0.0, we - start_t), 3)
                    clip_words.append(rel_w)
        except Exception:
            clip_words = []

    has_source = bool(project.source_path and os.path.exists(project.source_path))
    custom_title = str(req.get("title") or "").strip() or f"Manual Cut {int(start_t)}s-{int(end_t)}s"
    clip = ViralClip(
        project_id=project_id,
        start_time=round(start_t, 3),
        end_time=round(end_t, 3),
        duration=round(end_t - start_t, 3),
        virality_score=85,
        hook_score=85,
        retention_score=85,
        emotional_score=80,
        standalone_score=85,
        viral_rationale="Custom timeline selection created manually from Full Transcript Search.",
        hook_headline=custom_title.upper()[:42],
        show_hook_banner=True,
        aspect_ratio=project.default_aspect_ratio or "9:16",
        selected_title=f"{custom_title} #Shorts"[:60],
        youtube_titles=json.dumps([f"{custom_title} #Shorts"[:60]]),
        transcript_json=json.dumps(clip_words),
        hardware_mode=project.hardware_mode or "gpu",
        analysis_source="manual",
        status="rendering" if has_source else "draft",
        render_status="rendering" if has_source else "idle",
        render_progress=5 if has_source else 0,
        render_message="Preparing clip preview..." if has_source else None,
    )
    session.add(clip)
    session.commit()
    session.refresh(clip)

    if has_source and clip.id is not None:
        background_tasks.add_task(render_worker_ultra, clip.id, "fast_preview")

    return {"status": "created", "clip": _serialize_clip(clip, project)}


@app.get("/api/profiles")
def get_profiles_endpoint():
    return list_profiles()


@app.post("/api/profiles")
def create_profile_endpoint(req: Dict[str, Any]):
    return create_profile(
        name=str(req.get("name") or "Custom Profile"),
        description=str(req.get("description") or ""),
        settings_payload=dict(req.get("settings") or {}),
    )


@app.delete("/api/profiles/{profile_id}")
def delete_profile_endpoint(profile_id: str):
    ok = delete_profile(profile_id)
    if not ok:
        raise HTTPException(status_code=400, detail="Cannot delete built-in or unknown profile")
    return {"status": "deleted", "id": profile_id}


@app.get("/api/batch/queue")
def get_batch_queue_endpoint():
    return {"items": list_batch_queue()}


@app.post("/api/batch/enqueue")
def enqueue_batch_endpoint(req: Dict[str, Any], session: Session = Depends(get_session)):
    pid = int(req.get("project_id", 0))
    proj = session.get(Project, pid)
    if not proj:
        raise HTTPException(status_code=404, detail="Project not found")
    item = enqueue_batch_project(pid, proj.title, source="manual")
    return {"status": "enqueued", "item": item}


@app.post("/api/batch/scan-watch-folder")
def scan_watch_folder_endpoint(req: Optional[Dict[str, Any]] = None):
    persisted = get_persisted_settings()
    folder = (req or {}).get("folder_path") or persisted.get("watch_folder_path") or ""
    return scan_watch_folder(
        folder_path=str(folder),
        aspect_ratio=str((req or {}).get("aspect_ratio") or "9:16"),
        duration_preset=str((req or {}).get("duration_preset") or "Auto"),
        clip_count=int((req or {}).get("clip_count") or 5),
        hardware_mode=str(persisted.get("hardware_acceleration") or "gpu"),
        auto_start=bool((req or {}).get("auto_start", True)),
    )


@app.patch("/api/clips/{project_id}/{clip_id}/metadata")
def update_clip_metadata(
    project_id: int,
    clip_id: int,
    req: UpdateClipMetadataRequest,
    session: Session = Depends(get_session),
):
    """Persist YouTube metadata edits (title, description, hashtags, pinned comment, privacy) cleanly."""
    clip = session.get(ViralClip, clip_id)
    if not clip or clip.project_id != project_id:
        raise HTTPException(status_code=404, detail="Clip not found")

    if req.selected_title is not None:
        clip.selected_title = req.selected_title
    if req.youtube_description is not None:
        clip.youtube_description = req.youtube_description
    if req.hashtags is not None:
        clip.hashtags = req.hashtags
    if req.pinned_comment is not None:
        clip.pinned_comment = req.pinned_comment
    if req.privacy_status is not None:
        clip.privacy_status = req.privacy_status

    session.add(clip)
    session.commit()
    session.refresh(clip)

    return {
        "status": "saved",
        "clip_id": clip.id,
        "project_id": clip.project_id,
        "selected_title": clip.selected_title,
        "youtube_description": clip.youtube_description,
        "hashtags": clip.hashtags,
        "pinned_comment": clip.pinned_comment,
        "privacy_status": clip.privacy_status,
    }

def _render_clip_from_snapshot(
    clip_data: Dict[str, Any],
    source_path: str,
    quality_mode: str = "final_hq",
    progress_callback: Optional[Any] = None,
) -> str:
    """Invoke render_clip_ultra from an immutable snapshot of clip & project data."""
    words = list(clip_data.get("words") or [])

    return render_clip_ultra(
        master_video_path=source_path,
        clip_id=clip_data["id"],
        project_id=clip_data["project_id"],
        start_time=clip_data["start_time"],
        duration=clip_data["duration"],
        words=words,
        aspect_ratio=clip_data["aspect_ratio"],
        custom_width=clip_data["custom_width"],
        custom_height=clip_data["custom_height"],
        frame_layout=clip_data["frame_layout"],
        webcam_corner=clip_data["webcam_corner"],
        webcam_split_ratio=clip_data["webcam_split_ratio"],
        source_webcam_region=clip_data.get("source_webcam_region"),
        visual_map=clip_data.get("visual_map"),
        framing_override=clip_data.get("framing_override"),
        best_frames=clip_data.get("best_frames"),
        pip_border_color=clip_data["pip_border_color"],
        zoom=clip_data["zoom"],
        pan_x_offset=clip_data["pan_x_offset"],
        burn_captions=clip_data["burn_captions"],
        timing_offset=clip_data["timing_offset"],
        caption_style=clip_data["caption_style"],
        caption_font=clip_data["caption_font"],
        caption_animation=clip_data["caption_animation"],
        font_size_percent=clip_data["font_size_percent"],
        base_color=clip_data["base_color"],
        active_color=clip_data["active_color"],
        outline_color=clip_data["outline_color"],
        font_size=clip_data["font_size"],
        bold=clip_data["bold"],
        italic=clip_data["italic"],
        all_caps=clip_data["all_caps"],
        text_align=clip_data["text_align"],
        position_grid=clip_data["position_grid"],
        position_x=clip_data["position_x"],
        position_y=clip_data["position_y"],
        hold_back_alpha=clip_data["hold_back_alpha"],
        highlight_shape=clip_data["highlight_shape"],
        emoji_enabled=clip_data["emoji_enabled"],
        highlight_power_words=bool(clip_data.get("highlight_power_words", False)),
        how_phrase_arrives=clip_data["how_phrase_arrives"],
        outline_width=clip_data["outline_width"],
        shadow_depth=clip_data["shadow_depth"],
        glow_intensity=clip_data["glow_intensity"],
        letter_spacing=clip_data["letter_spacing"],
        clean_filler_words=clip_data["clean_filler_words"],
        clip_size=clip_data["clip_size"],
        color_preset=clip_data["color_preset"],
        exposure=clip_data["exposure"],
        brightness=clip_data["brightness"],
        contrast=clip_data["contrast"],
        lift_mids=clip_data["lift_mids"],
        warmth=clip_data["warmth"],
        saturation=clip_data["saturation"],
        black_and_white=clip_data["black_and_white"],
        vintage=clip_data["vintage"],
        tint_color=clip_data["tint_color"],
        tint_strength=clip_data["tint_strength"],
        glow=clip_data["glow"],
        smooth=clip_data["smooth"],
        sharpness=clip_data["sharpness"],
        blur=clip_data["blur"],
        film_grain=clip_data["film_grain"],
        vignette=clip_data["vignette"],
        bottom_fade=clip_data["bottom_fade"],
        watermark_path=clip_data["watermark_path"],
        watermark_corner=clip_data["watermark_corner"],
        watermark_opacity=clip_data["watermark_opacity"],
        watermark_size=clip_data["watermark_size"],
        outro_path=clip_data["outro_path"],
        bgm_path=clip_data["bgm_path"],
        bgm_volume=clip_data["bgm_volume"],
        audio_volume=clip_data["audio_volume"],
        normalize_audio=clip_data["normalize_audio"],
        vocal_clarity=clip_data["vocal_clarity"],
        voice_isolation=clip_data.get("voice_isolation", False),
        auto_sfx=clip_data.get("auto_sfx", False),
        retention_bar_enabled=clip_data.get("retention_bar_enabled", False),
        retention_bar_color=clip_data.get("retention_bar_color", "#C6F32F"),
        outro_cta_enabled=clip_data.get("outro_cta_enabled", False),
        outro_cta_text=clip_data.get("outro_cta_text", "Subscribe for More"),
        broll_segments=clip_data.get("broll_segments") or [],
        hardware_mode=clip_data["hardware_mode"],
        hook_headline=clip_data["hook_headline"],
        show_hook_banner=clip_data["show_hook_banner"],
        cold_open_quote=clip_data["cold_open_quote"],
        use_cold_open=clip_data["use_cold_open"],
        auto_trim_dead_air=clip_data["auto_trim_dead_air"],
        dynamic_punch_zoom=clip_data["dynamic_punch_zoom"],
        punch_zoom_intensity=clip_data.get("punch_zoom_intensity", 1.14),
        tighten_pacing=clip_data.get("tighten_pacing", False),
        silence_threshold=clip_data.get("silence_threshold", 0.50),
        censor_profanity=clip_data.get("censor_profanity", False),
        quality_mode=quality_mode,
        progress_callback=progress_callback,
        keep_segments=clip_data.get("keep_segments"),
        merge_crossfade_sec=float(clip_data.get("merge_crossfade_sec", 0.03) or 0.03),
        speakers=clip_data.get("speakers"),
        speaker_lower_thirds=bool(clip_data.get("speaker_lower_thirds", False)),
        face_safe_captions=bool(clip_data.get("face_safe_captions", True)),
        callout_cards=clip_data.get("callout_cards"),
        auto_callouts=bool(clip_data.get("auto_callouts", False)),
        bilingual_enabled=bool(clip_data.get("bilingual_enabled", False)),
        secondary_language=str(clip_data.get("secondary_language", "") or ""),
        dubbing_enabled=bool(clip_data.get("dubbing_enabled", False)),
        dubbed_audio_path=clip_data.get("dubbed_audio_path"),
        dubbing_bed_volume=float(clip_data.get("dubbing_bed_volume", 0.15) or 0.15),
        backdrop_mode=str(clip_data.get("backdrop_mode", "none") or "none"),
        backdrop_blur_intensity=float(clip_data.get("backdrop_blur_intensity", 18.0) or 18.0),
        backdrop_color=str(clip_data.get("backdrop_color", "#18181B") or "#18181B"),
        camera_motion=str(clip_data.get("camera_motion", "steady") or "steady"),
        whip_pan_cuts=bool(clip_data.get("whip_pan_cuts", False)),
        broll_layout=str(clip_data.get("broll_layout", "fullscreen") or "fullscreen"),
        depth_pop=float(clip_data.get("depth_pop", 0.0) or 0.0),
        privacy_blur_boxes=clip_data.get("privacy_blur_boxes") or [],
        beat_sync_enabled=bool(clip_data.get("beat_sync_enabled", False)),
        spectrum_enabled=bool(clip_data.get("spectrum_enabled", False)),
        spectrum_position=str(clip_data.get("spectrum_position", "bottom") or "bottom"),
        spectrum_color=str(clip_data.get("spectrum_color", "#C6F32F") or "#C6F32F"),
        chapters_enabled=bool(clip_data.get("chapters_enabled", False)),
        chapters=clip_data.get("chapters") or [],
        handle_watermark_enabled=bool(clip_data.get("handle_watermark_enabled", False)),
        handle_text=str(clip_data.get("handle_text", "") or ""),
        handle_platform_icon=str(clip_data.get("handle_platform_icon", "none") or "none"),
        handle_corner=str(clip_data.get("handle_corner", "bottom_right") or "bottom_right"),
        handle_opacity=float(clip_data.get("handle_opacity", 0.65) or 0.65),
        handle_display_mode=str(clip_data.get("handle_display_mode", "always") or "always"),
        options=clip_data.get("options") or {},
    )


def _snapshot_clip(clip: ViralClip) -> Dict[str, Any]:
    snap = clip.model_dump()
    snap["words"] = clip.get_transcript()
    snap["keep_segments"] = clip.get_keep_segments()
    snap["merge_crossfade_sec"] = float(getattr(clip, "merge_crossfade_sec", 0.03) or 0.03)
    snap["audio_volume"] = getattr(clip, "audio_volume", 1.0)
    snap["normalize_audio"] = getattr(clip, "normalize_audio", True)
    snap["vocal_clarity"] = getattr(clip, "vocal_clarity", False)
    snap["voice_isolation"] = getattr(clip, "voice_isolation", False)
    snap["auto_sfx"] = getattr(clip, "auto_sfx", False)
    snap["retention_bar_enabled"] = getattr(clip, "retention_bar_enabled", False)
    snap["retention_bar_color"] = getattr(clip, "retention_bar_color", "#C6F32F")
    snap["outro_cta_enabled"] = getattr(clip, "outro_cta_enabled", False)
    snap["outro_cta_text"] = getattr(clip, "outro_cta_text", "Subscribe for More")
    try:
        snap["broll_segments"] = json.loads(getattr(clip, "broll_json", "[]") or "[]")
    except Exception:
        snap["broll_segments"] = []
    snap["punch_zoom_intensity"] = getattr(clip, "punch_zoom_intensity", 1.14)
    snap["tighten_pacing"] = getattr(clip, "tighten_pacing", False)
    snap["silence_threshold"] = getattr(clip, "silence_threshold", 0.50)
    snap["censor_profanity"] = getattr(clip, "censor_profanity", False)
    snap["highlight_power_words"] = bool(getattr(clip, "highlight_power_words", False))
    snap["speakers"] = clip.get_speakers()
    snap["speaker_lower_thirds"] = bool(getattr(clip, "speaker_lower_thirds", False))
    snap["face_safe_captions"] = bool(getattr(clip, "face_safe_captions", True))
    snap["callout_cards"] = clip.get_callout_cards()
    snap["auto_callouts"] = bool(getattr(clip, "auto_callouts", False))
    snap["bilingual_enabled"] = bool(getattr(clip, "bilingual_enabled", False))
    snap["secondary_language"] = str(getattr(clip, "secondary_language", "") or "")
    snap["dubbing_enabled"] = bool(getattr(clip, "dubbing_enabled", False))
    snap["dubbed_audio_path"] = getattr(clip, "dubbed_audio_path", None)
    snap["dubbing_bed_volume"] = float(getattr(clip, "dubbing_bed_volume", 0.15) or 0.15)
    snap["backdrop_mode"] = str(getattr(clip, "backdrop_mode", "none") or "none")
    snap["backdrop_blur_intensity"] = float(getattr(clip, "backdrop_blur_intensity", 18.0) or 18.0)
    snap["backdrop_color"] = str(getattr(clip, "backdrop_color", "#18181B") or "#18181B")
    snap["camera_motion"] = str(getattr(clip, "camera_motion", "steady") or "steady")
    snap["whip_pan_cuts"] = bool(getattr(clip, "whip_pan_cuts", False))
    snap["broll_layout"] = str(getattr(clip, "broll_layout", "fullscreen") or "fullscreen")
    snap["depth_pop"] = float(getattr(clip, "depth_pop", 0.0) or 0.0)
    snap["privacy_blur_boxes"] = clip.get_privacy_blur_boxes()
    snap["beat_sync_enabled"] = bool(getattr(clip, "beat_sync_enabled", False))
    snap["spectrum_enabled"] = bool(getattr(clip, "spectrum_enabled", False))
    snap["spectrum_position"] = str(getattr(clip, "spectrum_position", "bottom") or "bottom")
    snap["spectrum_color"] = str(getattr(clip, "spectrum_color", "#C6F32F") or "#C6F32F")
    snap["chapters_enabled"] = bool(getattr(clip, "chapters_enabled", False))
    snap["chapters"] = clip.get_chapters()
    snap["handle_watermark_enabled"] = bool(getattr(clip, "handle_watermark_enabled", False))
    snap["handle_text"] = str(getattr(clip, "handle_text", "") or "")
    snap["handle_platform_icon"] = str(getattr(clip, "handle_platform_icon", "none") or "none")
    snap["handle_corner"] = str(getattr(clip, "handle_corner", "bottom_right") or "bottom_right")
    snap["handle_opacity"] = float(getattr(clip, "handle_opacity", 0.65) or 0.65)
    snap["handle_display_mode"] = str(getattr(clip, "handle_display_mode", "always") or "always")
    snap["source_webcam_region"] = clip.get_source_webcam_region()
    snap["visual_map"] = clip.get_visual_map()
    snap["framing_override"] = clip.get_framing_override()
    snap["best_frames"] = clip.get_best_frames()
    snap["options"] = clip.get_options()
    return snap


_stream_locks_guard = threading.Lock()
_stream_render_locks: Dict[int, threading.Lock] = {}


def _get_stream_clip_lock(clip_id: int) -> threading.Lock:
    with _stream_locks_guard:
        lock = _stream_render_locks.get(clip_id)
        if lock is None:
            lock = threading.Lock()
            _stream_render_locks[clip_id] = lock
        return lock


def render_worker_ultra(clip_id: int, quality_mode: str = "final_hq"):
    """Lost Heaven background rendering worker with progress reporting and cancellation support."""
    from app.database import engine
    with Session(engine) as session:
        clip = session.get(ViralClip, clip_id)
        if not clip:
            return
        project = session.get(Project, clip.project_id)
        if not project or not os.path.exists(project.source_path):
            clip.status = "failed"
            clip.render_status = "failed"
            clip.render_message = "Source video not found on disk."
            session.add(clip)
            session.commit()
            return

        clip_snap = _snapshot_clip(clip)
        source_path = project.source_path

        def on_progress(percent: int, message: str):
            try:
                c_row = session.get(ViralClip, clip_id)
                if c_row:
                    c_row.render_progress = percent
                    c_row.render_message = message
                    session.add(c_row)
                    session.commit()
            except Exception:
                pass
            logger.info(f"Clip {clip_id} Render [{percent}%]: {message}")

        try:
            clip.status = "rendering"
            clip.render_status = "rendering"
            clip.render_progress = 5
            clip.render_message = "Queued in render factory..."
            session.add(clip)
            session.commit()

            output_file = _render_clip_from_snapshot(
                clip_snap,
                source_path,
                quality_mode=quality_mode,
                progress_callback=on_progress,
            )

            c_done = session.get(ViralClip, clip_id)
            if c_done:
                c_done.status = "ready"
                c_done.render_status = "completed"
                c_done.render_progress = 100
                c_done.render_message = "Render complete"
                c_done.rendered_path = output_file
                c_done.rendered_video_path = output_file
                session.add(c_done)
                session.commit()
                if (quality_mode or "final_hq").strip().lower() != "fast_preview":
                    try:
                        from app.services.analytics_ledger import record_export_event
                        record_export_event(session, c_done, event_type="mp4_render")
                    except Exception:
                        pass

        except RenderSupersededError:
            logger.info(f"Background render for clip {clip_id} superseded cleanly by newer request.")
        except Exception as e:
            logger.error(f"Render failed for clip {clip_id}: {e}")
            c_err = session.get(ViralClip, clip_id)
            if c_err:
                c_err.status = "failed"
                c_err.render_status = "failed"
                c_err.render_message = str(e)
                session.add(c_err)
                session.commit()


@app.post("/api/clips/{clip_id}/render")
@app.post("/api/clips/{project_id}/{clip_id}/render")
@app.post("/api/render/{project_id}/{clip_id}")
def trigger_render(
    clip_id: int,
    background_tasks: BackgroundTasks,
    project_id: Optional[int] = None,
    req: Optional[RenderClipRequest] = Body(default=None),
    session: Session = Depends(get_session),
):
    clip = session.get(ViralClip, clip_id)
    if not clip or (project_id is not None and clip.project_id != project_id):
        raise HTTPException(status_code=404, detail="Clip not found")

    if req is not None:
        dirty_req = False
        if req.cold_open_climax is not None:
            clip.use_cold_open = bool(req.cold_open_climax); dirty_req = True
        elif req.cold_open is not None:
            clip.use_cold_open = bool(req.cold_open); dirty_req = True
        elif req.use_cold_open is not None:
            clip.use_cold_open = bool(req.use_cold_open); dirty_req = True
        if req.trim_dead_air is not None:
            clip.auto_trim_dead_air = bool(req.trim_dead_air); dirty_req = True
        elif req.auto_trim_dead_air is not None:
            clip.auto_trim_dead_air = bool(req.auto_trim_dead_air); dirty_req = True
        if req.punch_zoom_fx is not None:
            clip.dynamic_punch_zoom = bool(req.punch_zoom_fx); dirty_req = True
        elif req.punch_zoom is not None:
            clip.dynamic_punch_zoom = bool(req.punch_zoom); dirty_req = True
        elif req.dynamic_punch_zoom is not None:
            clip.dynamic_punch_zoom = bool(req.dynamic_punch_zoom); dirty_req = True
        if req.punch_zoom_intensity is not None:
            clip.punch_zoom_intensity = max(1.04, min(1.35, float(req.punch_zoom_intensity))); dirty_req = True
        if req.silence_threshold is not None:
            clip.silence_threshold = max(0.25, min(2.0, float(req.silence_threshold))); dirty_req = True
        if req.caption_animation is not None:
            clip.caption_animation = req.caption_animation; dirty_req = True
        if req.highlight_shape is not None:
            clip.highlight_shape = req.highlight_shape; dirty_req = True
        if req.pill_color is not None:
            clip.set_options({"pill_color": req.pill_color}); dirty_req = True
        if req.pacing_density is not None:
            clip.set_options({"pacing_density": req.pacing_density}); dirty_req = True
        if req.text_case is not None:
            clip.set_options({"text_case": req.text_case})
            clip.all_caps = req.text_case.strip().lower() in ("all caps", "all_caps", "uppercase")
            dirty_req = True
        for opt_r in ("color_preset", "outro_cta_enabled"):
            rv = getattr(req, opt_r, None)
            if rv is not None:
                setattr(clip, opt_r, rv); dirty_req = True
        for opt_o in (
            "caption_x",
            "caption_y",
            "caption_rotation",
            "caption_max_width",
            "preset_intensity",
            "highlights",
            "shadows",
            "export_fps",
            "lut_path",
            "voice_eq_preset",
            "de_esser",
            "loudness_target",
            "speed_ramp",
            "chroma_split",
            "letterbox",
        ):
            ov = getattr(req, opt_o, None)
            if ov is not None:
                clip.set_options({opt_o: ov}); dirty_req = True
        if req.options is not None and isinstance(req.options, dict):
            clip.set_options(req.options); dirty_req = True
        if dirty_req:
            session.add(clip)
            session.commit()
            session.refresh(clip)

    quality_mode = (req.quality_mode if req and req.quality_mode else "final_hq").strip().lower()
    if quality_mode == "fast_preview":
        project = session.get(Project, clip.project_id)
        if not project or not project.source_path or not os.path.exists(project.source_path):
            raise HTTPException(status_code=404, detail="Source video not found on disk.")

        clip_snap = _snapshot_clip(clip)
        source_path = project.source_path
        try:
            output_file = _render_clip_from_snapshot(
                clip_snap,
                source_path,
                quality_mode="fast_preview",
                progress_callback=None,
            )
            # Atomic SQLite update only after FFmpeg exits 0 cleanly
            from app.database import engine
            with Session(engine) as wsession:
                c_done = wsession.get(ViralClip, clip_id)
                if c_done:
                    c_done.rendered_path = output_file
                    c_done.rendered_video_path = output_file
                    c_done.status = "ready"
                    c_done.render_status = "completed"
                    c_done.render_progress = 100
                    c_done.render_message = "Auto-rendered preview"
                    wsession.add(c_done)
                    wsession.commit()

            ts = int(time.time() * 1000)
            stream_url = f"/api/clips/{clip.project_id}/{clip.id}/video?v={ts}"
            return {
                "status": "completed",
                "clip_status": "ready",
                "clip_id": clip.id,
                "project_id": clip.project_id,
                "quality_mode": "fast_preview",
                "rendered_path": output_file,
                "video_url": stream_url,
                "preview_url": stream_url,
            }
        except RenderSupersededError:
            return {
                "status": "superseded",
                "clip_id": clip.id,
                "project_id": clip.project_id,
                "quality_mode": "fast_preview",
            }
        except Exception as exc:
            logger.error(f"Fast preview render failed for clip {clip_id}: {exc}")
            raise HTTPException(status_code=500, detail=str(exc))

    clip.status = "rendering"
    clip.render_status = "rendering"
    clip.render_progress = 5
    clip.render_message = "Queued in render factory..."
    session.add(clip)
    session.commit()

    background_tasks.add_task(render_worker_ultra, clip_id, "final_hq")
    return {"status": "queued", "clip_id": clip_id, "project_id": clip.project_id, "quality_mode": "final_hq"}


@app.get("/api/clips/{project_id}/{clip_id}/video")
@app.get("/api/clips/{clip_id}/video")
def stream_latest_clip_video(
    clip_id: int,
    project_id: Optional[int] = None,
    session: Session = Depends(get_session),
):
    """
    Serve the rendered MP4 for a clip with HTTP byte-range seeking support.
    If not yet rendered on disk, automatically render fast_preview on-demand and serve it (never 404).
    """
    clip = session.get(ViralClip, clip_id)
    if not clip or (project_id is not None and clip.project_id != project_id):
        raise HTTPException(status_code=404, detail="Clip not found")

    target_path = clip.rendered_video_path or getattr(clip, "rendered_path", None)
    if not (target_path and os.path.exists(target_path)):
        clip_lock = _get_stream_clip_lock(clip_id)
        with clip_lock:
            session.refresh(clip)
            target_path = clip.rendered_video_path or getattr(clip, "rendered_path", None)
            if not (target_path and os.path.exists(target_path)):
                project = session.get(Project, clip.project_id)
                if not project or not project.source_path or not os.path.exists(project.source_path):
                    raise HTTPException(status_code=404, detail="Source video not found for on-demand clip render.")
                logger.info(f"On-demand fast_preview render triggered via /video for clip {clip.id} (project {clip.project_id})")
                clip_snap = _snapshot_clip(clip)
                try:
                    output_file = _render_clip_from_snapshot(
                        clip_snap,
                        project.source_path,
                        quality_mode="fast_preview",
                        progress_callback=None,
                    )
                except RenderSupersededError:
                    session.refresh(clip)
                    output_file = clip.rendered_video_path or getattr(clip, "rendered_path", None)
                    if not output_file or not os.path.exists(output_file):
                        raise HTTPException(status_code=503, detail="Clip render superseded; retry request.")
                clip.rendered_path = output_file
                clip.rendered_video_path = output_file
                clip.status = "ready"
                clip.render_status = "completed"
                clip.render_progress = 100
                clip.render_message = "On-demand preview ready"
                session.add(clip)
                session.commit()
                target_path = output_file

    return FileResponse(
        path=target_path,
        media_type="video/mp4",
        headers={
            "Cache-Control": "no-store, no-cache, must-revalidate",
            "Pragma": "no-cache",
        },
    )

# -------------------------------------------------------------
# Download & Export Endpoints
# -------------------------------------------------------------

def serve_download_file(clip: ViralClip):
    file_path = clip.rendered_video_path or getattr(clip, "rendered_path", None)
    if not file_path or not os.path.exists(file_path):
        raise HTTPException(status_code=400, detail="Clip has not been rendered yet. Click 'Render' first.")

    safe_title = re.sub(r"[^\w\s-]", "", clip.selected_title or f"LostHeaven_Clip_{clip.id:02d}").strip()
    aspect_tag = clip.aspect_ratio.replace(":", "x")
    download_name = f"{safe_title[:40]}_{aspect_tag}.mp4"

    return FileResponse(
        path=file_path,
        media_type="video/mp4",
        filename=download_name
    )

@app.get("/api/download/{project_id}/{clip_id}")
def download_by_project_clip(project_id: int, clip_id: int, session: Session = Depends(get_session)):
    clip = session.get(ViralClip, clip_id)
    if not clip or clip.project_id != project_id:
        raise HTTPException(status_code=404, detail="Clip not found")
    try:
        from app.services.analytics_ledger import record_export_event
        record_export_event(session, clip, event_type="mp4_download")
    except Exception:
        pass
    return serve_download_file(clip)

@app.get("/api/clips/{clip_id}/download")
def download_by_clip(clip_id: int, session: Session = Depends(get_session)):
    clip = session.get(ViralClip, clip_id)
    if not clip:
        raise HTTPException(status_code=404, detail="Clip not found")
    try:
        from app.services.analytics_ledger import record_export_event
        record_export_event(session, clip, event_type="mp4_download")
    except Exception:
        pass
    return serve_download_file(clip)

# -------------------------------------------------------------
# YouTube Publishing Endpoints
# -------------------------------------------------------------

def handle_youtube_publish(clip: ViralClip, title: Optional[str], description: Optional[str], privacy: Optional[str], session: Session):
    if not clip.rendered_video_path or not os.path.exists(clip.rendered_video_path):
        raise HTTPException(status_code=400, detail="Render the clip before publishing to YouTube.")

    final_title = title or clip.selected_title or "Viral Video"
    final_desc = description or clip.youtube_description or "Generated with Lost Heaven Studio."
    final_privacy = privacy or clip.privacy_status or "private"

    try:
        result = publish_video_to_youtube(
            video_path=clip.rendered_video_path,
            title=final_title,
            description=final_desc,
            privacy_status=final_privacy,
            aspect_ratio=clip.aspect_ratio
        )
    except FileNotFoundError as fnf:
        raise HTTPException(
            status_code=400,
            detail=str(fnf)
        )
    except Exception as exc:
        raise HTTPException(
            status_code=400,
            detail=f"YouTube publishing error: {str(exc)}"
        )

    clip.youtube_video_id = result.get("video_id")
    clip.youtube_url = result.get("url")
    session.add(clip)
    session.commit()

    return result

@app.post("/api/publish/youtube")
def publish_to_youtube_generic(
    req: PublishYouTubeRequest,
    clip_id: Optional[int] = None,
    session: Session = Depends(get_session)
):
    target_clip_id = clip_id or req.clip_id
    if not target_clip_id:
        clip = session.exec(select(ViralClip)).first()
        if not clip:
            raise HTTPException(status_code=404, detail="No clips available to publish")
    else:
        clip = session.get(ViralClip, target_clip_id)
        if not clip:
            raise HTTPException(status_code=404, detail="Clip not found")
    privacy = req.privacy or req.privacy_status or "private"
    return handle_youtube_publish(clip, req.title, req.description, privacy, session)

@app.post("/api/clips/{clip_id}/publish-youtube")
def publish_to_youtube_clip(
    clip_id: int,
    req: PublishYouTubeRequest,
    session: Session = Depends(get_session)
):
    clip = session.get(ViralClip, clip_id)
    if not clip:
        raise HTTPException(status_code=404, detail="Clip not found")
    return handle_youtube_publish(clip, req.title, req.description, req.privacy_status, session)

# -------------------------------------------------------------
# Settings API
# -------------------------------------------------------------

@app.get("/api/settings")
def get_studio_settings():
    persisted = get_persisted_settings()
    active_key = str(persisted.get("gemini_api_key") or settings.GEMINI_API_KEY or os.environ.get("GEMINI_API_KEY", "")).strip()
    has_gemini = bool(active_key and active_key != "your_gemini_api_key_here")
    has_youtube = is_youtube_authenticated()
    gemini_enabled = persisted.get("gemini_enabled", True)
    raw_ai_mode = persisted.get("ai_mode")
    if raw_ai_mode in ("gemini", "offline"):
        eff_ai_mode = raw_ai_mode
    elif persisted.get("ai_provider") == "offline" or gemini_enabled is False:
        eff_ai_mode = "offline"
    elif has_gemini:
        eff_ai_mode = "gemini"
    else:
        eff_ai_mode = "needs_api_key_or_offline_choice"
    try:
        from app.services.transcriber import get_whisper_runtime_info
        whisper_badge = get_whisper_runtime_info().get("badge", "CPU int8")
    except Exception:
        whisper_badge = "CPU int8"
    return {
        "gemini_configured": has_gemini,
        "gemini_enabled": gemini_enabled,
        "gemini_boost_enabled": gemini_enabled,
        "ai_mode": raw_ai_mode,
        "effective_ai_mode": eff_ai_mode,
        "needs_api_key_or_offline_choice": (eff_ai_mode == "needs_api_key_or_offline_choice"),
        "whisper_badge": whisper_badge,
        "gemini_model": persisted.get("gemini_model", settings.GEMINI_MODEL),
        "gemini_fallback_model": persisted.get("gemini_fallback_model", settings.GEMINI_FALLBACK_MODEL),
        "gemini_analysis_mode": persisted.get("gemini_analysis_mode", settings.GEMINI_ANALYSIS_MODE),
        "auto_fallback_offline": persisted.get("auto_fallback_offline", settings.AUTO_FALLBACK_OFFLINE),
        "ai_provider": persisted.get("ai_provider", settings.AI_PROVIDER),
        "ollama_endpoint": persisted.get("ollama_endpoint", settings.OLLAMA_ENDPOINT),
        "ollama_model": persisted.get("ollama_model", settings.OLLAMA_MODEL),
        "whisper_model": settings.WHISPER_MODEL,
        "whisper_device": settings.WHISPER_DEVICE,
        "youtube_authenticated": has_youtube,
        "hardware_acceleration": settings.HARDWARE_ACCELERATION,
        "ffmpeg_path": get_ffmpeg_cmd(),
        "active_theme": persisted.get("active_theme", RUNTIME_SETTINGS.get("active_theme", "Hicksfield")),
        "model_cascade": DEFAULT_MODEL_CASCADE
    }

@app.post("/api/settings")
def update_studio_settings(req: SettingsUpdateRequest):
    persisted = get_persisted_settings()
    if req.gemini_api_key is not None:
        settings.GEMINI_API_KEY = req.gemini_api_key
        os.environ["GEMINI_API_KEY"] = req.gemini_api_key
        persisted["gemini_api_key"] = req.gemini_api_key
    if req.gemini_model is not None:
        settings.GEMINI_MODEL = req.gemini_model
        persisted["gemini_model"] = req.gemini_model
    if req.gemini_fallback_model is not None:
        settings.GEMINI_FALLBACK_MODEL = req.gemini_fallback_model
        persisted["gemini_fallback_model"] = req.gemini_fallback_model
    if req.gemini_analysis_mode is not None:
        settings.GEMINI_ANALYSIS_MODE = req.gemini_analysis_mode
        persisted["gemini_analysis_mode"] = req.gemini_analysis_mode
    if req.auto_fallback_offline is not None:
        settings.AUTO_FALLBACK_OFFLINE = req.auto_fallback_offline
        persisted["auto_fallback_offline"] = req.auto_fallback_offline
    if req.ai_provider is not None:
        settings.AI_PROVIDER = req.ai_provider
        persisted["ai_provider"] = req.ai_provider
    if req.ai_mode is not None:
        am_val = str(req.ai_mode).strip().lower()
        if am_val in ("gemini", "offline"):
            persisted["ai_mode"] = am_val
            if am_val == "offline":
                settings.AI_PROVIDER = "offline"
                persisted["ai_provider"] = "offline"
                persisted["gemini_enabled"] = False
                persisted["gemini_boost_enabled"] = False
            else:
                settings.AI_PROVIDER = "gemini"
                persisted["ai_provider"] = "gemini"
                persisted["gemini_enabled"] = True
                persisted["gemini_boost_enabled"] = True
    if req.ollama_endpoint is not None:
        settings.OLLAMA_ENDPOINT = req.ollama_endpoint
        persisted["ollama_endpoint"] = req.ollama_endpoint
    if req.ollama_model is not None:
        settings.OLLAMA_MODEL = req.ollama_model
        persisted["ollama_model"] = req.ollama_model
    if req.whisper_model is not None:
        settings.WHISPER_MODEL = req.whisper_model
        persisted["whisper_model"] = req.whisper_model
    if req.gemini_enabled is not None:
        persisted["gemini_enabled"] = req.gemini_enabled
        RUNTIME_SETTINGS["gemini_boost_enabled"] = req.gemini_enabled
    if req.gemini_boost_enabled is not None:
        persisted["gemini_enabled"] = req.gemini_boost_enabled
        RUNTIME_SETTINGS["gemini_boost_enabled"] = req.gemini_boost_enabled
    if req.active_theme is not None:
        persisted["active_theme"] = req.active_theme
        RUNTIME_SETTINGS["active_theme"] = req.active_theme
    if req.hardware_acceleration is not None:
        settings.HARDWARE_ACCELERATION = req.hardware_acceleration
        persisted["hardware_acceleration"] = req.hardware_acceleration

    save_persisted_settings(persisted)
    return {"status": "saved", "settings": get_studio_settings()}
