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
    RenderClipRequest, TrimClipRequest, SettingsUpdateRequest, PreviewFrameRequest, PreviewFrameResponse,
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

for p in [storage_renders, storage_masters, storage_previews, storage_assets, storage_fonts, storage_models, storage_projects]:
    p.mkdir(parents=True, exist_ok=True)

app.mount("/media/renders", StaticFiles(directory=str(storage_renders)), name="renders")
app.mount("/media/masters", StaticFiles(directory=str(storage_masters)), name="masters")
app.mount("/media/previews", StaticFiles(directory=str(storage_previews)), name="previews")
app.mount("/media/assets", StaticFiles(directory=str(storage_assets)), name="assets")

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
    proj_id = req.project_id

    if req.clip_id:
        clip = session.get(ViralClip, req.clip_id)
        if clip:
            if not transcript_words:
                transcript_words = clip.get_transcript()
            clip_start_time = float(clip.start_time or 0.0)
            clip_duration = float(clip.duration or 0.0)
            proj_id = clip.project_id
            project = session.get(Project, clip.project_id)
            if project and project.source_path and os.path.exists(project.source_path):
                target_video_path = project.source_path

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
        "exposure": req.exposure,
        "brightness": req.brightness,
        "contrast": req.contrast,
        "lift_mids": req.lift_mids,
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
        "bottom_fade": req.bottom_fade
    }

    caption_options = {
        "caption_style": req.style_preset or req.caption_style,
        "caption_font": req.font_family or req.caption_font,
        "caption_animation": req.animation or req.caption_animation,
        "font_size_percent": req.font_size_pct if req.font_size_pct is not None else req.font_size_percent,
        "base_color": req.base_color,
        "active_color": req.highlight_color or req.active_color,
        "outline_color": req.outline_color,
        "hold_back_alpha": req.hold_back_opacity if req.hold_back_opacity is not None else req.hold_back_alpha,
        "highlight_shape": req.highlight_shape,
        "position_grid": req.position_grid,
        "position_x": req.pos_x if req.pos_x is not None else req.position_x,
        "position_y": req.pos_y if req.pos_y is not None else req.position_y,
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
async def upload_font(file: UploadFile = File(...)):
    fonts_dir = Path(settings.STORAGE_DIR) / "fonts"
    fonts_dir.mkdir(parents=True, exist_ok=True)

    dest = fonts_dir / file.filename
    with open(dest, "wb") as buffer:
        while chunk := await file.read(1024 * 512):
            buffer.write(chunk)

    font_name = Path(file.filename).stem
    FONT_MAP[font_name] = font_name
    return {"status": "uploaded", "font_name": font_name}

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
        hardware_mode=hardware_mode or "gpu"
    )

    if batch_queue:
        enqueue_batch_project(project.id, project.title, source="upload", auto_start=True)
        return {"project_id": project.id, "title": project.title, "status": "queued"}

    background_tasks.add_task(job_manager.run_pipeline, project.id)

    return {"project_id": project.id, "title": project.title, "status": "processing"}

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
        hardware_mode=req.hardware_mode or "gpu"
    )

    background_tasks.add_task(job_manager.run_pipeline, project.id)

    return {"project_id": project.id, "title": project.title, "status": "processing"}

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

    # Time boundaries
    if req.start_time is not None:
        clip.start_time = req.start_time
    if req.end_time is not None:
        clip.end_time = req.end_time
    clip.duration = round(clip.end_time - clip.start_time, 2)

    # Hooks & Retention
    if req.hook_headline is not None:
        clip.hook_headline = req.hook_headline
    if req.show_hook_banner is not None:
        clip.show_hook_banner = req.show_hook_banner
    if req.cold_open_quote is not None:
        clip.cold_open_quote = req.cold_open_quote
    if req.use_cold_open is not None:
        clip.use_cold_open = req.use_cold_open
    if req.auto_trim_dead_air is not None:
        clip.auto_trim_dead_air = req.auto_trim_dead_air
    if req.dynamic_punch_zoom is not None:
        clip.dynamic_punch_zoom = req.dynamic_punch_zoom

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
    if req.font_size is not None:
        clip.font_size = req.font_size
    if req.font_size_percent is not None:
        clip.font_size_percent = req.font_size_percent
    if req.bold is not None:
        clip.bold = req.bold
    if req.italic is not None:
        clip.italic = req.italic
    if req.all_caps is not None:
        clip.all_caps = req.all_caps
    if req.text_align is not None:
        clip.text_align = req.text_align
    if req.position_grid is not None:
        clip.position_grid = req.position_grid
    if req.position_x is not None:
        clip.position_x = req.position_x
    if req.position_y is not None:
        clip.position_y = req.position_y
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
    return extract_waveform_bins(
        video_or_wav_path=project.source_path or str(audio_wav_path),
        clip_start=clip.start_time,
        clip_end=clip.end_time,
        num_bins=bins,
        audio_wav_path=str(audio_wav_path) if audio_wav_path.exists() else None,
        transcript_words=transcript_words,
    )


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
    clip.duration = round(new_end - new_start, 3)
    clip.set_transcript(sliced_words)

    session.add(clip)
    session.commit()
    session.refresh(clip)

    return {"status": "trimmed", "clip": _serialize_clip(clip, project)}


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
    )


def _snapshot_clip(clip: ViralClip) -> Dict[str, Any]:
    snap = clip.model_dump()
    snap["words"] = clip.get_transcript()
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
    return serve_download_file(clip)

@app.get("/api/clips/{clip_id}/download")
def download_by_clip(clip_id: int, session: Session = Depends(get_session)):
    clip = session.get(ViralClip, clip_id)
    if not clip:
        raise HTTPException(status_code=404, detail="Clip not found")
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
    return {
        "gemini_configured": has_gemini,
        "gemini_enabled": gemini_enabled,
        "gemini_boost_enabled": gemini_enabled,
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
