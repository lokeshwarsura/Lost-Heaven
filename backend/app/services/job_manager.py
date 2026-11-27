import os
import sys
import json
import time
import shutil
import asyncio
import logging
import threading
import traceback
from pathlib import Path
from datetime import datetime, timezone
from typing import Dict, Any, List, Optional

from sqlmodel import Session, select
import app.database as app_db
from app.config import settings
from app.database import engine
from app.models import Project, ViralClip
from app.services.proxy import probe_video, create_gemini_proxy, get_ffmpeg_cmd, detect_hardware_encoder
from app.services.renderer import render_clip_ultra

_DEFAULT_ENGINE = engine
from app.services.transcriber import (
    transcribe_clip,
    extract_audio_segment,
    transcribe_audio_full,
    get_whisper_runtime_info,
)
from app.services.gemini_clipper import (
    extract_viral_clips,
    GeminiAuthError,
    GeminiUnavailableError,
    verify_gemini_api_key
)
from app.services.offline_clipper import extract_viral_clips_offline
from app.services.ollama_clipper import extract_viral_clips_ollama
from app.services.hardware import detect_hardware_specs
from app.services.face_tracker import snap_clip_boundaries_to_scenes

logger = logging.getLogger("clipship_jobs")


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def utc_now_iso() -> str:
    return utc_now().isoformat()


def _parse_utc_iso(val: Optional[str]) -> Optional[datetime]:
    if not val or not isinstance(val, str):
        return None
    try:
        s = val.strip()
        if s.endswith("Z"):
            s = s[:-1] + "+00:00"
        dt = datetime.fromisoformat(s)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt
    except Exception:
        return None


def _compute_elapsed_seconds(job: Dict[str, Any]) -> int:
    acc = float(job.get("accumulated_seconds") or 0.0)
    status = job.get("status")
    active_iso = job.get("active_started_at")
    if status == "processing":
        anchor = _parse_utc_iso(active_iso) or _parse_utc_iso(job.get("started_at"))
        if anchor:
            delta = max(0.0, (utc_now() - anchor).total_seconds())
            return max(0, int(round(acc + delta)))
    elif active_iso:
        anchor = _parse_utc_iso(active_iso)
        end_dt = _parse_utc_iso(job.get("updated_at")) or utc_now()
        if anchor:
            return max(0, int(round(acc + max(0.0, (end_dt - anchor).total_seconds()))))
    if acc > 0:
        return max(0, int(round(acc)))
    # Legacy fallback for historical project.json files without accumulated_seconds
    start_dt = _parse_utc_iso(job.get("started_at"))
    if not start_dt:
        return 0
    end_dt = utc_now() if status == "processing" else (_parse_utc_iso(job.get("updated_at")) or utc_now())
    return max(0, int(round((end_dt - start_dt).total_seconds())))


def _freeze_timer(job: Dict[str, Any]):
    acc = float(job.get("accumulated_seconds") or 0.0)
    active_dt = _parse_utc_iso(job.get("active_started_at"))
    if active_dt:
        acc += max(0.0, (utc_now() - active_dt).total_seconds())
    job["accumulated_seconds"] = round(acc, 3)
    job["active_started_at"] = None
    job["elapsed_seconds"] = max(0, int(round(acc)))

def _atomic_write_json(target_path: Path, data: Dict[str, Any]) -> None:
    """Write JSON atomically via a temp file + os.replace to prevent torn/corrupt settings files."""
    target_path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = target_path.with_name(f".{target_path.name}.{os.getpid()}.{threading.get_ident()}.tmp")
    with open(tmp_path, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2)
        f.flush()
        os.fsync(f.fileno())
    os.replace(tmp_path, target_path)


def _legacy_clipship_settings_path() -> Optional[Path]:
    """Return legacy %APPDATA%\\ClipShip\\settings.json path when APPDATA is defined."""
    appdata = os.environ.get("APPDATA", "").strip()
    if not appdata:
        return None
    return Path(appdata) / "ClipShip" / "settings.json"


def _is_valid_api_key(val: Any) -> bool:
    s = str(val or "").strip()
    return bool(s and s != "your_gemini_api_key_here")


def get_persisted_settings() -> Dict[str, Any]:
    """
    Read settings.json from STORAGE_DIR (LOST_HEAVEN_DATA_DIR/storage), migrating
    any non-empty gemini_api_key from legacy %APPDATA%\\ClipShip\\settings.json if missing/empty.
    """
    from app.config import DEFAULT_STORAGE_DIR

    primary_path = Path(settings.STORAGE_DIR) / "settings.json"
    is_default_storage = Path(settings.STORAGE_DIR).resolve() == Path(DEFAULT_STORAGE_DIR).resolve()

    search_paths = [primary_path]
    if is_default_storage:
        search_paths.extend([Path("storage/settings.json"), Path("backend/storage/settings.json")])

    data: Dict[str, Any] = {}
    for p in search_paths:
        if p.exists():
            try:
                with open(p, "r", encoding="utf-8") as f:
                    loaded = json.load(f)
                if isinstance(loaded, dict):
                    data = loaded
                    break
            except Exception:
                pass

    # Migrate legacy API key from %APPDATA%\ClipShip\settings.json if primary has empty/missing key
    if not _is_valid_api_key(data.get("gemini_api_key")):
        legacy_path = _legacy_clipship_settings_path()
        if legacy_path and legacy_path.exists():
            try:
                with open(legacy_path, "r", encoding="utf-8") as lf:
                    legacy_data = json.load(lf)
                if isinstance(legacy_data, dict) and _is_valid_api_key(legacy_data.get("gemini_api_key")):
                    data["gemini_api_key"] = str(legacy_data["gemini_api_key"]).strip()
                    for opt_key in ("gemini_model", "gemini_analysis_mode", "auto_fallback_offline", "ai_provider"):
                        if opt_key not in data and opt_key in legacy_data:
                            data[opt_key] = legacy_data[opt_key]
                    try:
                        _atomic_write_json(primary_path, data)
                    except Exception as mig_err:
                        logger.warning(f"Legacy key migration save warning: {mig_err}")
            except Exception as leg_err:
                logger.warning(f"Failed reading legacy ClipShip settings for migration: {leg_err}")

    return data


def save_persisted_settings(data: Dict[str, Any]):
    """Atomically save settings to STORAGE_DIR/settings.json (and default mirror when not isolated)."""
    from app.config import DEFAULT_STORAGE_DIR

    current = get_persisted_settings()
    current.update(data)
    primary_path = Path(settings.STORAGE_DIR) / "settings.json"
    paths = [primary_path]
    if Path(settings.STORAGE_DIR).resolve() == Path(DEFAULT_STORAGE_DIR).resolve():
        for extra in (Path("storage/settings.json"), Path("backend/storage/settings.json")):
            if extra.resolve() != primary_path.resolve():
                paths.append(extra)

    for p in paths:
        try:
            _atomic_write_json(p, current)
        except Exception as e:
            logger.warning(f"Failed to save settings to {p}: {e}")

def resolve_ai_mode(
    persisted: Dict[str, Any],
    explicit_ai_mode: Optional[str] = None,
    force_offline: bool = False,
) -> str:
    """
    Resolve explicit pipeline AI mode cleanly:
      - 'offline': explicitly chosen offline mode, disabled Gemini boost, or force_offline
      - 'gemini': valid Gemini API key available and enabled
      - 'needs_api_key_or_offline_choice': key missing and offline not explicitly selected
    """
    if force_offline:
        return "offline"
    clean_explicit = (explicit_ai_mode or "").strip().lower()
    if clean_explicit == "offline":
        return "offline"

    raw_mode = clean_explicit or str(persisted.get("ai_mode") or "").strip().lower()
    if raw_mode == "offline":
        return "offline"

    ai_provider = str(persisted.get("ai_provider") or settings.AI_PROVIDER or "gemini").strip().lower()
    if clean_explicit != "gemini":
        if ai_provider == "offline":
            return "offline"
        gem_enabled = persisted.get("gemini_enabled", persisted.get("gemini_boost_enabled", True))
        if gem_enabled is False:
            return "offline"

    key = persisted.get("gemini_api_key") or settings.GEMINI_API_KEY or os.environ.get("GEMINI_API_KEY", "")
    if ai_provider == "ollama" and clean_explicit != "gemini":
        return "gemini"  # Uses Stage 4 local LLM branch cleanly
    if _is_valid_api_key(key):
        return "gemini"
    return "needs_api_key_or_offline_choice"


def _build_dynamic_stages(
    needs_proxy: bool = False,
    ai_mode: str = "gemini",
    ai_provider: str = "gemini",
) -> List[Dict[str, Any]]:
    is_offline = ai_mode == "offline" or ai_provider == "offline"
    return [
        {
            "id": "ingest",
            "name": "Media Prep",
            "desc": "Probing video stream & FFprobe metadata",
            "min": 0,
            "max": 15,
        },
        {
            "id": "audio",
            "name": "480p Proxy" if needs_proxy else "Fast Audio Extract",
            "desc": "16kHz WAV & 480p proxy" if needs_proxy else "16kHz mono WAV (proxy skipped)",
            "min": 15,
            "max": 30,
        },
        {
            "id": "transcribe",
            "name": "Whisper VAD",
            "desc": "Batched faster-whisper speech sync",
            "min": 30,
            "max": 65,
        },
        {
            "id": "gemini",
            "name": "Offline Multimodal AI" if is_offline else ("Ollama Local AI" if ai_provider == "ollama" else "Gemini Multimodal AI"),
            "desc": "Local facial, vocal & arc scoring" if is_offline else ("Local LLM highlight ranking" if ai_provider == "ollama" else "Cloud Gemini multimodal highlights"),
            "min": 65,
            "max": 90,
        },
        {
            "id": "ready",
            "name": "Sync & Ready",
            "desc": "Generating timeline & saving clips",
            "min": 90,
            "max": 100,
        },
    ]


def _sync_stage_labels(
    existing_stages: List[Dict[str, Any]],
    *,
    needs_proxy: bool,
    ai_mode: str,
    ai_provider: str,
) -> List[Dict[str, Any]]:
    tmpl = {s["id"]: s for s in _build_dynamic_stages(needs_proxy=needs_proxy, ai_mode=ai_mode, ai_provider=ai_provider)}
    updated: List[Dict[str, Any]] = []
    for st in existing_stages:
        sid = st.get("id")
        if sid in tmpl:
            merged = dict(st)
            merged["name"] = tmpl[sid]["name"]
            merged["desc"] = tmpl[sid]["desc"]
            updated.append(merged)
        else:
            updated.append(st)
    return updated


def _normalize_job_payload(job: Dict[str, Any]) -> Dict[str, Any]:
    """Ensure every job payload carries consistent telemetry, metrics, and UI-friendly mirrors."""
    if not isinstance(job, dict):
        return job
    elapsed = _compute_elapsed_seconds(job)
    job["elapsed_seconds"] = elapsed
    job["elapsed_sec"] = elapsed
    pct = int(max(0, min(100, int(job.get("percent") or 0))))
    job["percent"] = pct
    job["progress_pct"] = pct
    hw_badge = str(job.get("hardware_badge") or job.get("hardware_label") or "CPU Multi-Core (libx264)")
    job["hardware_badge"] = hw_badge
    job["hardware_label"] = hw_badge
    fname = str(job.get("source_filename") or job.get("filename") or os.path.basename(str(job.get("source_path") or "")) or "")
    job["source_filename"] = fname
    job["filename"] = fname
    fbytes = int(job.get("file_size_bytes") or 0)
    job["file_size_mb"] = round(fbytes / (1024 ** 2), 2) if fbytes > 0 else float(job.get("file_size_mb") or 0.0)
    job["duration_sec"] = round(float(job.get("duration_sec") or 0.0), 2)
    job["default_framing"] = str(job.get("default_framing") or "smart_auto")

    lm = job.get("live_metrics")
    if not isinstance(lm, dict):
        lm = {}
    job["live_metrics"] = {
        "words_transcribed": int(lm.get("words_transcribed") or 0),
        "keyframes_scanned": int(lm.get("keyframes_scanned") or 0),
        "keyframes_total": int(lm.get("keyframes_total") or 0),
        "highlights_detected": int(lm.get("highlights_detected") or job.get("clips_count") or 0),
    }

    stages = job.get("stages") or []
    cur_stage = str(job.get("current_stage") or "ingest")
    stage_ids = [str(s.get("id")) for s in stages if isinstance(s, dict)]
    if cur_stage in stage_ids:
        job["stage_index"] = stage_ids.index(cur_stage)
    else:
        default_ids = [s["id"] for s in STAGES_CONFIG]
        job["stage_index"] = default_ids.index(cur_stage) if cur_stage in default_ids else 0
    return job


STAGES_CONFIG = _build_dynamic_stages(needs_proxy=False, ai_mode="gemini", ai_provider="gemini")

class JobManager:
    """
    Central background job manager:
    - Tracks job progress in memory & persists to /backend/storage/projects/{id}/project.json
    - Broadcasts live progress & timestamped logs to SSE subscribers
    - Supports cancellation
    - Executes multi-step pipeline with real voice recognition & Gemini AI
    """
    def __init__(self):
        self._jobs: Dict[int, Dict[str, Any]] = {}
        self._cancel_flags: Dict[int, threading.Event] = {}
        self._active_procs: Dict[int, List[Any]] = {}
        self._lock = threading.RLock()
        self._broadcast_callback = None

    def _register_proc(self, project_id: int, proc: Any) -> None:
        with self._lock:
            lst = self._active_procs.setdefault(project_id, [])
            lst.append(proc)

    def _terminate_active_procs(self, project_id: int) -> None:
        with self._lock:
            procs = self._active_procs.pop(project_id, [])
        for p in procs:
            try:
                if p is not None and p.poll() is None:
                    p.kill()
            except Exception:
                pass

    def set_broadcast_callback(self, cb):
        self._broadcast_callback = cb

    def _get_project_dir(self, project_id: int) -> Path:
        p_dir = Path(settings.STORAGE_DIR) / "projects" / str(project_id)
        p_dir.mkdir(parents=True, exist_ok=True)
        return p_dir

    def _get_project_json_path(self, project_id: int) -> Path:
        return self._get_project_dir(project_id) / "project.json"

    def _save_project_json(self, project_id: int, data: Dict[str, Any]):
        try:
            path = self._get_project_json_path(project_id)
            with open(path, "w", encoding="utf-8") as f:
                json.dump(data, f, indent=2, default=str)
        except Exception as e:
            logger.warning(f"Could not persist project.json for {project_id}: {e}")

    def create_job(
        self,
        project_id: int,
        title: str,
        source_path: str,
        source_filename: Optional[str] = None,
        duration_preset: str = "Auto",
        aspect_ratio: str = "9:16",
        clip_count: int = 5,
        caption_language: str = "As spoken",
        topic_focus: Optional[str] = None,
        hardware_mode: str = "gpu",
        ai_mode: Optional[str] = None,
        default_framing: str = "smart_auto",
    ) -> Dict[str, Any]:
        with self._lock:
            cancel_event = threading.Event()
            self._cancel_flags[project_id] = cancel_event

            file_size = 0
            if source_path and os.path.exists(source_path):
                try:
                    file_size = os.path.getsize(source_path)
                except Exception:
                    pass

            def format_size(b: int) -> str:
                if b < 1024**2:
                    return f"{b / 1024:.1f} KB"
                elif b < 1024**3:
                    return f"{b / (1024**2):.1f} MB"
                return f"{b / (1024**3):.2f} GB"

            encoder = detect_hardware_encoder()
            specs = detect_hardware_specs()
            gpu_name = specs.get("gpu_name")
            if gpu_name and gpu_name != "None" and "nvenc" in encoder:
                hw_badge = f"{gpu_name} (NVENC)"
            elif "videotoolbox" in encoder:
                hw_badge = "Apple Silicon (VideoToolbox)"
            else:
                hw_badge = "CPU Multi-Core (libx264)"

            try:
                whisper_rt = get_whisper_runtime_info()
                whisper_badge = str(whisper_rt.get("badge") or "CPU int8")
            except Exception:
                whisper_badge = "CPU int8"

            persisted = get_persisted_settings()
            ai_provider = str(persisted.get("ai_provider") or settings.AI_PROVIDER or "gemini")
            resolved_mode = resolve_ai_mode(persisted, explicit_ai_mode=ai_mode)
            gem_analysis_mode = str(persisted.get("gemini_analysis_mode") or settings.GEMINI_ANALYSIS_MODE or "transcript")
            needs_proxy = (resolved_mode == "gemini" and ai_provider == "gemini" and gem_analysis_mode == "multimodal")

            now_iso = utc_now_iso()
            dyn_stages = _build_dynamic_stages(
                needs_proxy=needs_proxy,
                ai_mode=resolved_mode,
                ai_provider=ai_provider,
            )
            stages = [
                {
                    "id": s["id"],
                    "name": s["name"],
                    "desc": s["desc"],
                    "status": "running" if i == 0 else "pending",
                    "percent": 0,
                    "started_at": now_iso if i == 0 else None,
                    "completed_at": None,
                    "duration_sec": None,
                }
                for i, s in enumerate(dyn_stages)
            ]

            job_state = {
                "project_id": project_id,
                "title": title,
                "status": "processing",
                "current_stage": "ingest",
                "stage_label": "Media Prep & Video Ingestion",
                "percent": 5,
                "started_at": now_iso,
                "active_started_at": now_iso,
                "accumulated_seconds": 0.0,
                "elapsed_seconds": 0,
                "updated_at": now_iso,
                "eta_seconds": 45,
                "source_path": source_path,
                "source_filename": source_filename or os.path.basename(source_path),
                "file_size_bytes": file_size,
                "file_size_formatted": format_size(file_size),
                "file_size_mb": round(file_size / (1024 ** 2), 2),
                "duration_sec": 0.0,
                "hardware_badge": hw_badge,
                "hardware_label": hw_badge,
                "whisper_badge": whisper_badge,
                "ai_mode": resolved_mode,
                "ai_provider": ai_provider,
                "needs_api_key_or_offline_choice": resolved_mode == "needs_api_key_or_offline_choice",
                "needs_proxy": needs_proxy,
                "duration_preset": duration_preset,
                "aspect_ratio": aspect_ratio,
                "clip_count": clip_count,
                "caption_language": caption_language,
                "default_framing": default_framing or "smart_auto",
                "topic_focus": topic_focus,
                "hardware_mode": hardware_mode,
                "live_metrics": {
                    "words_transcribed": 0,
                    "keyframes_scanned": 0,
                    "keyframes_total": 0,
                    "highlights_detected": 0,
                },
                "stages": stages,
                "logs": [
                    {
                        "timestamp": now_iso,
                        "stage": "INGEST",
                        "level": "INFO",
                        "message": f"Job initialized for '{title}'. Hardware: {hw_badge} · Whisper: {whisper_badge}."
                    }
                ],
                "error_detail": None,
                "clips_count": 0
            }
            _normalize_job_payload(job_state)

            self._jobs[project_id] = job_state
            self._save_project_json(project_id, job_state)
            return job_state

    def update_progress(
        self,
        project_id: int,
        percent: int,
        stage: str,
        stage_label: str,
        message: Optional[str] = None,
        level: str = "INFO",
        eta_seconds: Optional[int] = None,
        log_stage: Optional[str] = None,
        live_metrics: Optional[Dict[str, Any]] = None,
        duration_sec: Optional[float] = None,
    ):
        with self._lock:
            job = self._jobs.get(project_id)
            if not job:
                # Try loading from disk
                job = self.get_progress(project_id)
                if not job:
                    return

            now_dt = utc_now()
            now_iso = now_dt.isoformat().replace("+00:00", "Z")
            job["percent"] = max(0, min(100, percent))
            job["current_stage"] = stage
            job["stage_label"] = stage_label
            job["updated_at"] = now_iso
            if eta_seconds is not None:
                job["eta_seconds"] = max(0, eta_seconds)
            if duration_sec is not None:
                job["duration_sec"] = round(float(duration_sec), 2)
            if isinstance(live_metrics, dict):
                lm = job.setdefault("live_metrics", {})
                for k, v in live_metrics.items():
                    if v is not None:
                        lm[k] = v

            # Update stages status + per-stage durations
            stage_order = [s["id"] for s in STAGES_CONFIG]
            current_idx = stage_order.index(stage) if stage in stage_order else 0

            for i, st in enumerate(job.get("stages", [])):
                if i < current_idx:
                    if st.get("status") != "done":
                        st["completed_at"] = st.get("completed_at") or now_iso
                        s_dt = _parse_utc_iso(st.get("started_at"))
                        if st.get("duration_sec") is None:
                            st["duration_sec"] = round(max(0.1, (now_dt - s_dt).total_seconds()), 1) if s_dt else 0.0
                    st["status"] = "done"
                    st["percent"] = 100
                elif i == current_idx:
                    if not st.get("started_at"):
                        st["started_at"] = now_iso
                    st["status"] = "running"
                    st["percent"] = percent
                else:
                    st["status"] = "pending"
                    st["percent"] = 0

            if message:
                tag = (log_stage or stage).strip().upper()
                log_entry = {
                    "timestamp": now_iso,
                    "stage": tag,
                    "level": level,
                    "message": message
                }
                job.setdefault("logs", []).append(log_entry)

            _normalize_job_payload(job)
            self._jobs[project_id] = job
            self._save_project_json(project_id, job)

        if self._broadcast_callback:
            try:
                self._broadcast_callback("progress", job)
            except Exception:
                pass

        return job

    def fail_job(self, project_id: int, error_message: str, error_detail: Optional[str] = None):
        with self._lock:
            job = self._jobs.get(project_id) or self.get_progress(project_id)
            if not job:
                return

            now_dt = utc_now()
            now_iso = now_dt.isoformat().replace("+00:00", "Z")
            job["status"] = "error"
            job["stage_label"] = "Processing failed"
            job["error_detail"] = error_detail or error_message
            job["updated_at"] = now_iso
            _freeze_timer(job)

            # Mark current stage as failed
            for st in job.get("stages", []):
                if st.get("status") == "running":
                    st["status"] = "failed"
                    s_dt = _parse_utc_iso(st.get("started_at"))
                    if st.get("duration_sec") is None and s_dt:
                        st["duration_sec"] = round(max(0.1, (now_dt - s_dt).total_seconds()), 1)

            log_entry = {
                "timestamp": now_iso,
                "stage": "ERROR",
                "level": "ERROR",
                "message": f"Pipeline failure: {error_message}"
            }
            job.setdefault("logs", []).append(log_entry)

            _normalize_job_payload(job)
            self._jobs[project_id] = job
            self._save_project_json(project_id, job)

            # Also update SQLite DB record
            try:
                active_db_engine = app_db.engine if app_db.engine is not _DEFAULT_ENGINE else engine
                with Session(active_db_engine) as session:
                    p = session.get(Project, project_id)
                    if p:
                        p.status = "error"
                        p.error_message = error_message
                        session.add(p)
                        session.commit()
            except Exception as e:
                logger.error(f"Failed to update db project status to error: {e}")

        if self._broadcast_callback:
            try:
                self._broadcast_callback("progress", job)
            except Exception:
                pass

        return job

    def finish_job(self, project_id: int, clips_count: int = 0):
        with self._lock:
            job = self._jobs.get(project_id) or self.get_progress(project_id)
            if not job:
                return

            now_dt = utc_now()
            now_iso = now_dt.isoformat().replace("+00:00", "Z")
            job["status"] = "finished"
            job["current_stage"] = "ready"
            job["stage_label"] = "Clips Ready & Synchronized"
            job["percent"] = 100
            job["eta_seconds"] = 0
            job["clips_count"] = clips_count
            job["updated_at"] = now_iso
            _freeze_timer(job)
            lm = job.setdefault("live_metrics", {})
            lm["highlights_detected"] = max(int(clips_count or 0), int(lm.get("highlights_detected") or 0))

            for st in job.get("stages", []):
                if st.get("status") != "done":
                    st["completed_at"] = st.get("completed_at") or now_iso
                    s_dt = _parse_utc_iso(st.get("started_at"))
                    if st.get("duration_sec") is None:
                        st["duration_sec"] = round(max(0.1, (now_dt - s_dt).total_seconds()), 1) if s_dt else 0.0
                st["status"] = "done"
                st["percent"] = 100

            log_entry = {
                "timestamp": now_iso,
                "stage": "READY",
                "level": "SUCCESS",
                "message": f"Pipeline completed successfully! {clips_count} viral clips ready for editing."
            }
            job.setdefault("logs", []).append(log_entry)

            _normalize_job_payload(job)
            self._jobs[project_id] = job
            self._save_project_json(project_id, job)

            # Update DB
            try:
                active_db_engine = app_db.engine if app_db.engine is not _DEFAULT_ENGINE else engine
                with Session(active_db_engine) as session:
                    p = session.get(Project, project_id)
                    if p:
                        p.status = "analyzed"
                        session.add(p)
                        session.commit()
            except Exception as e:
                logger.error(f"Failed to update db project status to analyzed: {e}")

        if self._broadcast_callback:
            try:
                self._broadcast_callback("progress", job)
            except Exception:
                pass

        return job

    def cancel_job(self, project_id: int):
        with self._lock:
            evt = self._cancel_flags.get(project_id)
            if evt:
                evt.set()
            self._terminate_active_procs(project_id)

            job = self._jobs.get(project_id) or self.get_progress(project_id)
            if job:
                job["status"] = "cancelled"
                job["stage_label"] = "Processing cancelled by user"
                job["updated_at"] = utc_now_iso()
                _freeze_timer(job)
                job.setdefault("logs", []).append({
                    "timestamp": utc_now_iso(),
                    "stage": "CANCEL",
                    "level": "WARN",
                    "message": "Job cancelled by user request."
                })
                self._jobs[project_id] = job
                self._save_project_json(project_id, job)

            # ALWAYS update project status in SQLite to "cancelled"
            try:
                with Session(engine) as session:
                    p = session.get(Project, project_id)
                    if p:
                        p.status = "cancelled"
                        p.error_message = "Processing cancelled by user"
                        session.add(p)
                        session.commit()
            except Exception as dbe:
                logger.error(f"Failed to update project status in DB on cancel: {dbe}")

        if self._broadcast_callback:
            try:
                self._broadcast_callback("progress", job)
            except Exception:
                pass

    def cleanup_orphan_jobs(self):
        """
        On server startup, scan SQLite AND storage project.json files for any projects stuck in
        'processing' or 'running' from a prior session, mark them interrupted so timers never keep ticking,
        and mark any active running stages as failed for clean Retry recovery.
        """
        try:
            recovered_ids = set()
            with Session(engine) as session:
                stuck_projects = session.exec(
                    select(Project).where(Project.status.in_(["processing", "running"]))
                ).all()
                for p in stuck_projects:
                    recovered_ids.add(p.id)
                    p.status = "error"
                    p.error_message = "Server restarted — interrupted by restart. Click Retry to resume from saved checkpoints."
                    session.add(p)

                    job = self.get_progress(p.id)
                    if job:
                        job["status"] = "error"
                        job["stage_label"] = "Interrupted by server restart — click Retry"
                        job["error_detail"] = "Interrupted by server restart — click Retry"
                        job["updated_at"] = utc_now_iso()
                        _freeze_timer(job)
                        for st in job.get("stages", []):
                            if st.get("status") == "running":
                                st["status"] = "failed"
                        job.setdefault("logs", []).append({
                            "timestamp": utc_now_iso(),
                            "stage": "SYSTEM",
                            "level": "WARN",
                            "message": "Interrupted by server restart — click Retry to resume from saved checkpoints."
                        })
                        self._jobs[p.id] = job
                        self._save_project_json(p.id, job)
                session.commit()

            # Also sweep any disk project.json orphaned in 'running'/'processing' state
            projects_root = Path(settings.STORAGE_DIR) / "projects"
            if projects_root.exists():
                for pjson in projects_root.glob("*/project.json"):
                    try:
                        pid = int(pjson.parent.name)
                        if pid in recovered_ids:
                            continue
                        data = json.loads(pjson.read_text(encoding="utf-8"))
                        if isinstance(data, dict) and data.get("status") in ("running", "processing"):
                            data["status"] = "error"
                            data["stage_label"] = "Interrupted by server restart — click Retry"
                            data["error_detail"] = "Interrupted by server restart — click Retry"
                            data["updated_at"] = utc_now_iso()
                            _freeze_timer(data)
                            for st in data.get("stages", []):
                                if st.get("status") == "running":
                                    st["status"] = "failed"
                            self._jobs[pid] = data
                            self._save_project_json(pid, data)
                            recovered_ids.add(pid)
                    except Exception:
                        pass

            if recovered_ids:
                logger.info(f"Cleaned up {len(recovered_ids)} orphan jobs on startup.")
        except Exception as e:
            logger.error(f"Failed to clean up orphan jobs: {e}")

    def pause_job_for_api_key(self, project_id: int, reason: str = "missing", message: str = ""):
        """
        Transition job to awaiting_api_key pause state without losing checkpoints.
        Allows user to provide a valid API key or switch to 100% offline mode.
        """
        with self._lock:
            job = self._jobs.get(project_id) or self.get_progress(project_id)
            if not job:
                return

            clean_msg = message or (
                "Google Gemini API Key is required for AI Boost analysis. Paste your API key to continue, or switch to 100% Offline Mode."
                if reason == "missing"
                else "The provided Gemini API Key was rejected by Google AI Studio. Paste a valid key to continue, or switch to 100% Offline Mode."
            )

            job["status"] = "awaiting_api_key"
            job["current_stage"] = "gemini"
            job["stage_label"] = "Gemini API Key Required" if reason == "missing" else "Gemini API Key Not Recognized"
            job["api_key_reason"] = reason
            job["api_key_message"] = clean_msg
            job["needs_api_key_or_offline_choice"] = True
            if reason == "missing":
                job["ai_mode"] = "needs_api_key_or_offline_choice"
            job["updated_at"] = utc_now_iso()
            _freeze_timer(job)

            # Mark gemini stage as action_required
            for st in job.get("stages", []):
                if st["id"] == "gemini":
                    st["status"] = "action_required"
                    st["percent"] = 68

            log_entry = {
                "timestamp": utc_now_iso(),
                "stage": "PAUSE",
                "level": "WARN",
                "message": f"Paused at Stage 4 — {clean_msg}"
            }
            job.setdefault("logs", []).append(log_entry)

            self._jobs[project_id] = job
            self._save_project_json(project_id, job)

            try:
                with Session(engine) as session:
                    p = session.get(Project, project_id)
                    if p:
                        p.status = "awaiting_api_key"
                        session.add(p)
                        session.commit()
            except Exception as e:
                logger.error(f"Failed to update db project status to awaiting_api_key: {e}")

        if self._broadcast_callback:
            try:
                self._broadcast_callback("progress", job)
            except Exception:
                pass

        return job

    def retry_pipeline(self, project_id: int, background_tasks: Optional[Any] = None, force_offline: bool = False) -> Dict[str, Any]:
        """
        Retry a failed, cancelled, or paused pipeline job.
        Checks for existing intermediate artifacts and resumes directly at the failed stage:
        - If force_offline is True: skips Gemini entirely and uses extract_viral_clips_offline.
        - If transcript.json exists: resume at stage 'gemini' (68%)
        - Elif proxy_480p.mp4 exists: resume at stage 'transcribe' (35%)
        - Elif audio_16k.wav exists: resume at stage 'audio' (20%)
        - Else: resume at stage 'ingest' (5%)
        """
        with self._lock:
            cancel_event = threading.Event()
            self._cancel_flags[project_id] = cancel_event

            job = self._jobs.get(project_id) or self.get_progress(project_id)
            p_dir = self._get_project_dir(project_id)
            audio_path = p_dir / "audio_16k.wav"
            proxy_path = p_dir / "proxy_480p.mp4"
            transcript_path = p_dir / "transcript.json"

            if force_offline:
                job["force_offline"] = True
                job["ai_mode"] = "offline"
                job["needs_api_key_or_offline_choice"] = False
                job["api_key_reason"] = None
                job["api_key_message"] = None
            else:
                job["needs_api_key_or_offline_choice"] = False

            start_stage = "ingest"
            start_percent = 5
            stage_label = "Restarting pipeline from Media Prep"
            cached_words_count = 0

            if transcript_path.exists() and transcript_path.stat().st_size > 50:
                try:
                    with open(transcript_path, "r", encoding="utf-8") as tf:
                        _cw = json.load(tf)
                        if isinstance(_cw, list):
                            cached_words_count = len(_cw)
                except Exception:
                    pass
                start_stage = "gemini"
                start_percent = 68
                stage_label = f"Resuming in 100% Offline Mode ({cached_words_count:,} words)" if force_offline else f"Resuming from cached transcript ({cached_words_count:,} words)"
            elif proxy_path.exists() and proxy_path.stat().st_size > 1000:
                start_stage = "transcribe"
                start_percent = 35
                stage_label = "Resuming from cached proxy (Voice Recognition)"
            elif audio_path.exists() and audio_path.stat().st_size > 1000:
                start_stage = "audio"
                start_percent = 20
                stage_label = "Resuming from cached audio extraction"

            job["status"] = "processing"
            job["current_stage"] = start_stage
            job["stage_label"] = stage_label
            job["percent"] = start_percent
            job["error_detail"] = None
            if not force_offline and job.get("status") != "awaiting_api_key":
                job["api_key_reason"] = None
                job["api_key_message"] = None

            # Reset timer anchors cleanly on retry
            now_iso = utc_now_iso()
            job["started_at"] = now_iso
            job["active_started_at"] = now_iso
            job["accumulated_seconds"] = 0.0
            job["elapsed_seconds"] = 0
            job["updated_at"] = now_iso

            stage_order = [s["id"] for s in STAGES_CONFIG]
            current_idx = stage_order.index(start_stage) if start_stage in stage_order else 0
            for i, st in enumerate(job.get("stages", [])):
                if i < current_idx:
                    st["status"] = "done"
                    st["percent"] = 100
                elif i == current_idx:
                    st["status"] = "running"
                    st["percent"] = start_percent
                else:
                    st["status"] = "pending"
                    st["percent"] = 0

            offline_note = " in 100% Offline Mode" if force_offline else ""
            words_msg = f" Loaded {cached_words_count:,} cached words from transcript.json." if cached_words_count > 0 else ""
            log_entry = {
                "timestamp": now_iso,
                "stage": "RETRY",
                "level": "INFO",
                "message": f"Retry initiated{offline_note}. Checkpoints detected: resuming from stage '{start_stage.upper()}' at {start_percent}%.{words_msg}"
            }
            job.setdefault("logs", []).append(log_entry)

            self._jobs[project_id] = job
            self._save_project_json(project_id, job)

            # Update DB
            try:
                with Session(engine) as session:
                    p = session.get(Project, project_id)
                    if p:
                        p.status = "processing"
                        p.error_message = None
                        session.add(p)
                        session.commit()
            except Exception as e:
                logger.error(f"Failed to update db project status on retry: {e}")

        if self._broadcast_callback:
            try:
                self._broadcast_callback("progress", job)
            except Exception:
                pass

        if background_tasks:
            background_tasks.add_task(self.run_pipeline, project_id, force_offline)
        else:
            t = threading.Thread(target=self.run_pipeline, args=(project_id, force_offline), daemon=True)
            t.start()

        return job

    def is_cancelled(self, project_id: int) -> bool:
        evt = self._cancel_flags.get(project_id)
        return evt.is_set() if evt else False

    def get_progress(self, project_id: int) -> Dict[str, Any]:
        with self._lock:
            if project_id in self._jobs:
                job = self._jobs[project_id]
                _normalize_job_payload(job)
                return job

        # Check disk
        path = self._get_project_json_path(project_id)
        if path.exists():
            try:
                with open(path, "r", encoding="utf-8") as f:
                    data = json.load(f)
                    _normalize_job_payload(data)
                    with self._lock:
                        self._jobs[project_id] = data
                    return data
            except Exception as e:
                logger.warning(f"Error reading {path}: {e}")

        # Fallback to DB
        try:
            active_db_engine = app_db.engine if app_db.engine is not _DEFAULT_ENGINE else engine
            with Session(active_db_engine) as session:
                project = session.get(Project, project_id)
                if not project:
                    return {
                        "project_id": project_id,
                        "status": "not_found",
                        "percent": 0,
                        "progress_pct": 0,
                        "stages": [],
                        "logs": [],
                        "live_metrics": {
                            "words_transcribed": 0,
                            "keyframes_scanned": 0,
                            "keyframes_total": 0,
                            "highlights_detected": 0,
                        },
                    }
                clips = session.exec(select(ViralClip).where(ViralClip.project_id == project_id)).all()
                status = "finished" if project.status in ["analyzed", "ready"] else project.status
                file_size = 0
                if project.source_path and os.path.exists(project.source_path):
                    try:
                        file_size = os.path.getsize(project.source_path)
                    except Exception:
                        pass
                fallback_job = {
                    "project_id": project.id,
                    "title": project.title,
                    "status": status,
                    "current_stage": "ready" if status == "finished" else "ingest",
                    "stage_label": "Ready" if status == "finished" else "Created",
                    "percent": 100 if status == "finished" else 10,
                    "started_at": project.created_at,
                    "updated_at": project.created_at,
                    "elapsed_seconds": 0,
                    "eta_seconds": 0,
                    "source_path": project.source_path,
                    "source_filename": project.source_filename,
                    "file_size_bytes": file_size,
                    "duration_sec": float(project.duration or 0.0),
                    "ai_mode": getattr(project, "ai_mode", None) or "offline",
                    "default_framing": getattr(project, "default_framing", "smart_auto") or "smart_auto",
                    "clips_count": len(clips),
                    "live_metrics": {
                        "words_transcribed": sum(len(c.get_transcript()) for c in clips),
                        "keyframes_scanned": 0,
                        "keyframes_total": 0,
                        "highlights_detected": len(clips),
                    },
                    "stages": [
                        {
                            "id": s["id"],
                            "name": s["name"],
                            "desc": s["desc"],
                            "status": "done" if status == "finished" else "pending",
                            "percent": 100 if status == "finished" else 0,
                            "duration_sec": None,
                        }
                        for s in STAGES_CONFIG
                    ],
                    "logs": [
                        {"timestamp": project.created_at, "stage": "INIT", "level": "INFO", "message": f"Project '{project.title}' initialized."}
                    ]
                }
                return _normalize_job_payload(fallback_job)
        except Exception as e:
            return {
                "project_id": project_id,
                "status": "error",
                "percent": 0,
                "progress_pct": 0,
                "error_detail": str(e),
                "stages": [],
                "logs": [],
                "live_metrics": {
                    "words_transcribed": 0,
                    "keyframes_scanned": 0,
                    "keyframes_total": 0,
                    "highlights_detected": 0,
                },
            }

    def list_jobs(self, limit: int = 20) -> Dict[str, Any]:
        """
        Return normalized active running jobs, queued batch jobs, and recent completed/failed jobs
        by merging in-memory job state, persisted project.json files, SQLite projects, and batch queue.
        """
        from app.services.batch_queue import list_batch_queue

        by_pid: Dict[int, Dict[str, Any]] = {}
        active_db_engine = app_db.engine if app_db.engine is not _DEFAULT_ENGINE else engine

        try:
            with Session(active_db_engine) as session:
                db_projects = session.exec(
                    select(Project).order_by(Project.id.desc()).limit(max(limit, 30))
                ).all()
        except Exception as db_err:
            logger.warning(f"list_jobs could not read DB projects: {db_err}")
            db_projects = []

        # Seed from DB rows (newest first), enriched by in-memory/project.json job telemetry
        db_by_pid = {int(p.id): p for p in db_projects if p.id is not None}
        candidate_pids: List[int] = [int(p.id) for p in db_projects if p.id is not None]
        with self._lock:
            for mem_pid in self._jobs.keys():
                if mem_pid not in db_by_pid and mem_pid not in candidate_pids:
                    candidate_pids.append(mem_pid)

        for pid in candidate_pids:
            job_data = self.get_progress(pid)
            if not job_data or job_data.get("status") == "not_found":
                continue
            item = dict(job_data)
            p_row = db_by_pid.get(pid)
            if p_row:
                if not item.get("duration_sec") and p_row.duration:
                    item["duration_sec"] = round(float(p_row.duration), 2)
                if not item.get("title"):
                    item["title"] = p_row.title
                if not item.get("ai_mode") and getattr(p_row, "ai_mode", None):
                    item["ai_mode"] = p_row.ai_mode
                if not item.get("default_framing") and getattr(p_row, "default_framing", None):
                    item["default_framing"] = p_row.default_framing
            _normalize_job_payload(item)
            by_pid[pid] = item

        # Merge queued batch items
        queued_jobs: List[Dict[str, Any]] = []
        queued_pids = set()
        for q in list_batch_queue():
            try:
                q_pid = int(q.get("project_id", 0))
            except Exception:
                continue
            q_status = str(q.get("status") or "queued").lower()
            if q_status == "queued":
                queued_pids.add(q_pid)
                existing = dict(by_pid.get(q_pid) or {
                    "project_id": q_pid,
                    "title": q.get("title") or f"Project #{q_pid}",
                    "status": "queued",
                    "percent": 0,
                    "current_stage": "ingest",
                    "stage_label": "Queued in Batch",
                    "started_at": q.get("enqueued_at"),
                    "stages": [],
                    "logs": [],
                })
                existing["status"] = "queued"
                existing["stage_label"] = "Queued in Multi-Video Batch"
                _normalize_job_payload(existing)
                by_pid[q_pid] = existing
                queued_jobs.append(existing)

        active_statuses = {"processing", "analyzing", "awaiting_api_key", "proxy_ready", "created"}
        active_jobs: List[Dict[str, Any]] = []
        recent_jobs: List[Dict[str, Any]] = []

        for pid in sorted(by_pid.keys(), reverse=True):
            item = by_pid[pid]
            st = str(item.get("status") or "").lower()
            if pid in queued_pids and st == "queued":
                continue
            if st in active_statuses:
                active_jobs.append(item)
            else:
                recent_jobs.append(item)

        recent_jobs = recent_jobs[:limit]
        combined = active_jobs + queued_jobs + recent_jobs
        return {
            "active_jobs": active_jobs,
            "queued_jobs": queued_jobs,
            "recent_jobs": recent_jobs,
            "jobs": combined,
        }

    def run_pipeline(self, project_id: int, force_offline: bool = False):
        """Top-level bulletproof thread entry point. Catches BaseException to guarantee no silent thread death."""
        try:
            self._execute_pipeline(project_id, force_offline)
        except BaseException as exc:
            tb = traceback.format_exc()
            err_msg = str(exc) or exc.__class__.__name__
            logger.critical(f"FATAL unhandled exception in pipeline thread for project {project_id}: {err_msg}\n{tb}")
            try:
                self.fail_job(
                    project_id,
                    error_message=f"Pipeline crashed unexpectedly: {err_msg}",
                    error_detail=tb
                )
                with Session(engine) as session:
                    p = session.get(Project, project_id)
                    if p:
                        p.status = "error"
                        p.error_message = f"Fatal error: {err_msg[:120]}"
                        session.add(p)
                        session.commit()
            except Exception as inner_err:
                logger.error(f"Failed to record crash in DB for project {project_id}: {inner_err}")

    def _execute_pipeline(self, project_id: int, force_offline: bool = False):
        """
        Full 5-stage background processing pipeline:
        1. 0-15%: Media Prep (FFprobe probe, dimensions, duration)
        2. 15-30%: Audio extraction (16kHz mono WAV) & 480p proxy (skipped if offline)
        3. 30-65%: Recognizing voice & word timestamps with faster-whisper (VAD)
        4. 65-90%: Detecting viral clips (100% Offline or Gemini Multimodal AI)
        5. 90-100%: Timeline sync, database records, finalize
        """
        logger.info(f"Starting pipeline thread for project {project_id} (force_offline={force_offline})")

        with Session(engine) as session:
            project = session.get(Project, project_id)
            if not project:
                self.fail_job(project_id, f"Project {project_id} not found in database.")
                return

        # Check live settings for Gemini Boost vs Offline Mode
        persisted = get_persisted_settings()
        job = self.get_progress(project_id) or {}
        effective_mode = resolve_ai_mode(
            persisted,
            explicit_ai_mode=job.get("ai_mode") if job.get("ai_mode") in ("offline", "gemini") else None,
            force_offline=bool(force_offline or job.get("force_offline", False)),
        )
        gemini_boost_enabled = effective_mode != "offline"
        if force_offline or job.get("force_offline", False):
            gemini_boost_enabled = False

        gemini_api_key = persisted.get("gemini_api_key") or settings.GEMINI_API_KEY or os.environ.get("GEMINI_API_KEY", "")
        gemini_model = persisted.get("gemini_model") or settings.GEMINI_MODEL or "gemini-3.5-flash-lite"
        gemini_analysis_mode = persisted.get("gemini_analysis_mode", settings.GEMINI_ANALYSIS_MODE) # "transcript" or "multimodal"
        auto_fallback_offline = persisted.get("auto_fallback_offline", settings.AUTO_FALLBACK_OFFLINE)
        ai_provider = persisted.get("ai_provider", settings.AI_PROVIDER) # "gemini", "ollama", "offline"
        ollama_endpoint = persisted.get("ollama_endpoint", settings.OLLAMA_ENDPOINT)
        ollama_model = persisted.get("ollama_model", settings.OLLAMA_MODEL)

        with self._lock:
            live_j = self._jobs.get(project_id)
            if live_j:
                needs_px_init = bool(gemini_boost_enabled and ai_provider == "gemini" and gemini_analysis_mode == "multimodal" and _is_valid_api_key(gemini_api_key))
                live_j["ai_mode"] = effective_mode
                live_j["ai_provider"] = ai_provider
                live_j["needs_api_key_or_offline_choice"] = effective_mode == "needs_api_key_or_offline_choice"
                live_j["needs_proxy"] = needs_px_init
                live_j["stages"] = _sync_stage_labels(
                    live_j.get("stages", []),
                    needs_proxy=needs_px_init,
                    ai_mode=effective_mode,
                    ai_provider=ai_provider,
                )

        try:
            # -------------------------------------------------------------
            # Stage 1: Media Prep & Probing (0-15%)
            # -------------------------------------------------------------
            if self.is_cancelled(project_id): return
            self.update_progress(
                project_id, 8, "ingest", "Probing video stream & metadata...",
                message=f"Running FFprobe on source: {os.path.basename(project.source_path)}",
                eta_seconds=45
            )

            meta = probe_video(project.source_path)
            duration = float(meta.get("duration", 0.0))
            width = int(meta.get("width", 1920))
            height = int(meta.get("height", 1080))
            fps = float(meta.get("fps", 30.0))

            with Session(engine) as session:
                p = session.get(Project, project_id)
                if p:
                    p.duration = duration
                    p.resolution_w = width
                    p.resolution_h = height
                    p.fps = fps
                    session.add(p)
                    session.commit()

            self.update_progress(
                project_id, 15, "ingest", "Media probed successfully",
                message=f"Stream specs: {width}x{height} @ {fps:.1f}fps, total duration: {duration:.1f}s",
                eta_seconds=40,
                duration_sec=duration,
            )

            # -------------------------------------------------------------
            # Stage 2: Audio Extraction & Proxy Generation (15-30%)
            # -------------------------------------------------------------
            if self.is_cancelled(project_id): return
            self.update_progress(
                project_id, 18, "audio", "Extracting 16kHz mono audio...",
                message="Checking checkpoints and extracting audio for whisper engine",
                eta_seconds=35
            )

            p_dir = self._get_project_dir(project_id)
            audio_path = p_dir / "audio_16k.wav"
            proxy_path = p_dir / "proxy_480p.mp4"
            transcript_path = p_dir / "transcript.json"

            # 2a. 16kHz mono audio extraction
            if audio_path.exists() and audio_path.stat().st_size > 1000:
                self.update_progress(
                    project_id, 22, "audio", "Audio checkpoint found",
                    message="Reusing cached 16kHz audio from previous run",
                    eta_seconds=33
                )
            else:
                extract_audio_segment(project.source_path, 0.0, duration, str(audio_path))

            # 2b. Proxy generation (Only needed when multimodal Gemini video proxy mode is active & key configured)
            needs_proxy = bool(
                gemini_boost_enabled
                and effective_mode == "gemini"
                and ai_provider == "gemini"
                and gemini_analysis_mode == "multimodal"
                and _is_valid_api_key(gemini_api_key)
            )

            if not needs_proxy:
                self.update_progress(
                    project_id, 30, "audio", "Audio ready (Proxy Skipped)",
                    message="Transcript-First / Local Mode active — skipping 480p proxy encoding",
                    eta_seconds=28
                )
            else:
                if proxy_path.exists() and proxy_path.stat().st_size > 1000:
                    self.update_progress(
                        project_id, 28, "audio", "Proxy checkpoint found",
                        message="Reusing cached 480p proxy from previous run",
                        eta_seconds=30
                    )
                else:
                    create_gemini_proxy(project.source_path, str(proxy_path))

                with Session(engine) as session:
                    p = session.get(Project, project_id)
                    if p:
                        p.proxy_path = str(proxy_path)
                        p.status = "proxy_ready"
                        session.add(p)
                        session.commit()

                self.update_progress(
                    project_id, 30, "audio", "16kHz audio & 480p proxy ready",
                    message=f"Created lightweight proxy ({os.path.getsize(proxy_path) / (1024**2):.1f} MB) for Gemini multimodal upload",
                    eta_seconds=30
                )

            # -------------------------------------------------------------
            # Stage 3: Recognizing Voice with faster-whisper VAD (30-65%)
            # -------------------------------------------------------------
            if self.is_cancelled(project_id): return
            self.update_progress(
                project_id, 35, "transcribe", "Loading faster-whisper & recognizing speech...",
                message=f"Checking speech recognition checkpoint (VAD, language='{project.caption_language}')...",
                eta_seconds=25
            )

            transcript_words: List[Dict[str, Any]] = []
            if transcript_path.exists():
                try:
                    with open(transcript_path, "r", encoding="utf-8") as tf:
                        cached_words = json.load(tf)
                        if isinstance(cached_words, list) and len(cached_words) > 0:
                            transcript_words = cached_words
                            self.update_progress(
                                project_id, 65, "transcribe", "Voice recognition loaded from checkpoint",
                                message=f"Loaded {len(transcript_words)} cached words with timestamps from checkpoint",
                                eta_seconds=20,
                                live_metrics={"words_transcribed": len(transcript_words)},
                            )
                except Exception as ex:
                    logger.warning(f"Could not load cached transcript: {ex}")

            if not transcript_words:
                def on_speech_progress(pct: int, msg: str, word_count: Optional[int] = None):
                    progress_val = 35 + int(pct * 0.28)
                    self.update_progress(
                        project_id, progress_val, "transcribe", "Transcribing spoken words & timestamps...",
                        message=msg,
                        eta_seconds=max(5, int(25 * (1 - pct / 100))),
                        live_metrics={"words_transcribed": int(word_count)} if word_count is not None else None,
                    )

                self.update_progress(
                    project_id, 40, "transcribe", "faster-whisper recognizing speech...",
                    message="Running full faster-whisper inference with millisecond timestamps...",
                    eta_seconds=22
                )
                transcript_words = transcribe_audio_full(
                    str(audio_path),
                    language=project.caption_language,
                    on_progress=on_speech_progress
                )
                try:
                    with open(transcript_path, "w", encoding="utf-8") as tf:
                        json.dump(transcript_words, tf, indent=2)
                except Exception as wex:
                    logger.warning(f"Could not persist transcript checkpoint: {wex}")

                self.update_progress(
                    project_id, 65, "transcribe", "Voice recognition complete",
                    message=f"Successfully transcribed {len(transcript_words)} words across {duration:.1f}s",
                    eta_seconds=18,
                    live_metrics={"words_transcribed": len(transcript_words)},
                )

            # -------------------------------------------------------------
            # Stage 4: Viral Clip Detection (65-90%) - Gemini, Ollama or Offline
            # -------------------------------------------------------------
            if self.is_cancelled(project_id): return

            # Reload fresh settings right before Stage 4 execution
            fresh_settings = get_persisted_settings()
            gemini_boost_enabled = fresh_settings.get("gemini_enabled", fresh_settings.get("gemini_boost_enabled", True))
            job = self.get_progress(project_id) or {}
            if force_offline or job.get("force_offline", False):
                gemini_boost_enabled = False

            gemini_api_key = fresh_settings.get("gemini_api_key") or settings.GEMINI_API_KEY or os.environ.get("GEMINI_API_KEY", "")
            gemini_model = fresh_settings.get("gemini_model") or settings.GEMINI_MODEL or "gemini-3.5-flash-lite"
            gemini_analysis_mode = fresh_settings.get("gemini_analysis_mode", settings.GEMINI_ANALYSIS_MODE)
            auto_fallback_offline = fresh_settings.get("auto_fallback_offline", settings.AUTO_FALLBACK_OFFLINE)
            ai_provider = fresh_settings.get("ai_provider", settings.AI_PROVIDER)
            ollama_endpoint = fresh_settings.get("ollama_endpoint", settings.OLLAMA_ENDPOINT)
            ollama_model = fresh_settings.get("ollama_model", settings.OLLAMA_MODEL)

            # Explicitly reload transcript_words and audio_path from checkpoint if resuming
            if not transcript_words and transcript_path.exists():
                try:
                    with open(transcript_path, "r", encoding="utf-8") as tf:
                        cached_tr = json.load(tf)
                        if isinstance(cached_tr, list):
                            transcript_words = cached_tr
                except Exception as ex:
                    logger.warning(f"Could not load transcript checkpoint: {ex}")

            if not duration or duration <= 0:
                duration = float(project.duration or 120.0)

            # Multimodal Audio + Video Intelligence Pass (LUFS, Prosody, Sharpness, Cuts, Expression)
            aud_analysis: Optional[Dict[str, Any]] = None
            vid_analysis: Optional[Dict[str, Any]] = None
            mm_analysis: Optional[Dict[str, Any]] = None
            mm_ckpt_path = p_dir / "multimodal_analysis.json"
            if mm_ckpt_path.exists() and mm_ckpt_path.stat().st_size > 100:
                try:
                    cached_mm = json.loads(mm_ckpt_path.read_text(encoding="utf-8"))
                    if isinstance(cached_mm, dict) and (cached_mm.get("timeline") or cached_mm.get("summary")):
                        mm_analysis = cached_mm
                        mm_summary = cached_mm.get("summary") or {}
                        cached_fc = int(mm_summary.get("frame_count") or 0)
                        self.update_progress(
                            project_id, 69, "gemini", "Multimodal checkpoint loaded",
                            message="Reused cached multimodal_analysis.json (< 0.1s)",
                            eta_seconds=10,
                            log_stage="ANALYSIS",
                            live_metrics={"keyframes_scanned": cached_fc, "keyframes_total": cached_fc} if cached_fc > 0 else None,
                        )
                        active_mm_engine = app_db.engine if app_db.engine is not _DEFAULT_ENGINE else engine
                        with Session(active_mm_engine) as msession:
                            mp_row = msession.get(Project, project_id)
                            if mp_row:
                                mp_row.set_multimodal_analysis(mm_analysis)
                                msession.add(mp_row)
                                msession.commit()
                except Exception as c_mm_err:
                    logger.warning(f"Ignoring corrupt multimodal_analysis checkpoint for project {project_id}: {c_mm_err}")
                    mm_analysis = None

            if mm_analysis is None and project.source_path and os.path.exists(project.source_path):
                try:
                    from concurrent.futures import ThreadPoolExecutor, TimeoutError as FuturesTimeoutError
                    from app.services.audio_analyzer import analyze_audio_track
                    from app.services.video_analyzer import analyze_video_track
                    from app.services.multimodal_fusion import fuse_multimodal_tracks

                    self.update_progress(
                        project_id, 66, "gemini", "Analyzing acoustic prosody & LUFS...",
                        message="Analyzing acoustic prosody & LUFS (EBU R128 + vectorized vocal DSP)...",
                        eta_seconds=16,
                        log_stage="ANALYSIS",
                    )
                    audio_src_mm = str(audio_path) if audio_path.exists() else project.source_path
                    aud_analysis = analyze_audio_track(audio_src_mm, word_segments=transcript_words)

                    if self.is_cancelled(project_id): return

                    self.update_progress(
                        project_id, 67, "gemini", "Streaming keyframes via sequential FFmpeg...",
                        message="Streaming sequential downscaled keyframes (max 360 frames, 45s watchdog)...",
                        eta_seconds=14,
                        log_stage="ANALYSIS",
                    )

                    def on_vision_progress(pct: int, scanned: int, total_fr: int):
                        mapped = 67 + int(round((pct / 100.0) * 2.0))
                        self.update_progress(
                            project_id,
                            min(69, mapped),
                            "gemini",
                            f"Scanning keyframes ({pct}%)...",
                            message=f"Scanned {scanned:,} / {total_fr:,} keyframes ({pct}%)...",
                            eta_seconds=max(4, int(14 * (1 - pct / 100.0))),
                            log_stage="ANALYSIS",
                            live_metrics={"keyframes_scanned": int(scanned), "keyframes_total": int(total_fr)},
                        )

                    cancel_ev = self._cancel_flags.get(project_id)
                    with ThreadPoolExecutor(max_workers=1) as vpool:
                        fut = vpool.submit(
                            analyze_video_track,
                            project.source_path,
                            sample_fps=1.2,
                            on_progress=on_vision_progress,
                            max_frames=360,
                            watchdog_sec=45.0,
                            cancel_event=cancel_ev,
                            on_proc=lambda pr: self._register_proc(project_id, pr),
                        )
                        try:
                            vid_analysis = fut.result(timeout=47.0)
                        except FuturesTimeoutError:
                            logger.warning(f"Project {project_id}: vision scan exceeded 45s watchdog; terminating stream and continuing with heuristic telemetry.")
                            self._terminate_active_procs(project_id)
                            vid_analysis = {
                                "summary": {
                                    "duration": round(float(duration or 120.0), 3),
                                    "frame_count": 0,
                                    "avg_sharpness": 50.0,
                                    "avg_exposure": 50.0,
                                    "avg_action": 25.0,
                                    "face_presence_ratio": 0.0,
                                    "dominant_emotion": "neutral",
                                    "timed_out": True,
                                },
                                "scene_cuts": [],
                                "black_intervals": [],
                                "graphic_events": [],
                                "micro_expression_spikes": [],
                                "frames": [],
                            }
                            self.update_progress(
                                project_id, 69, "gemini", "Vision watchdog fallback — continuing...",
                                message="Vision scan exceeded 45s limit — continuing immediately with acoustic + transcript intelligence",
                                eta_seconds=10,
                                log_stage="ANALYSIS",
                            )

                    mm_analysis = fuse_multimodal_tracks(
                        aud_analysis,
                        vid_analysis,
                        word_segments=transcript_words,
                        duration=duration,
                    )
                    try:
                        _atomic_write_json(mm_ckpt_path, mm_analysis)
                    except Exception:
                        pass
                    active_mm_engine = app_db.engine if app_db.engine is not _DEFAULT_ENGINE else engine
                    with Session(active_mm_engine) as msession:
                        mp_row = msession.get(Project, project_id)
                        if mp_row:
                            mp_row.set_multimodal_analysis(mm_analysis)
                            msession.add(mp_row)
                            msession.commit()
                except Exception as mm_err:
                    logger.warning(f"Multimodal analysis skipped for project {project_id}: {mm_err}")

            words_loaded_note = f"Loaded {len(transcript_words):,} cached words from transcript.json. " if transcript_words else ""

            formula_weights = None
            try:
                from app.services.analytics_ledger import compute_top_performing_formula
                formula_weights = compute_top_performing_formula(session)
            except Exception:
                formula_weights = None

            fresh_mode = resolve_ai_mode(
                fresh_settings,
                explicit_ai_mode=job.get("ai_mode") if job.get("ai_mode") in ("offline", "gemini") else None,
                force_offline=bool(force_offline or job.get("force_offline", False)),
            )

            if not gemini_boost_enabled or fresh_mode == "offline" or ai_provider == "offline":
                with self._lock:
                    live_j4 = self._jobs.get(project_id)
                    if live_j4:
                        live_j4["ai_mode"] = "offline"
                        live_j4["needs_api_key_or_offline_choice"] = False
                        live_j4["stages"] = _sync_stage_labels(
                            live_j4.get("stages", []),
                            needs_proxy=bool(live_j4.get("needs_proxy", False)),
                            ai_mode="offline",
                            ai_provider="offline",
                        )
                self.update_progress(
                    project_id, 70, "gemini", "Offline Multimodal AI",
                    message=f"{words_loaded_note}Running 100% offline facial, vocal emotion & narrative arc detection...",
                    eta_seconds=10,
                    log_stage="OFFLINE AI",
                )
                raw_clips = extract_viral_clips_offline(
                    audio_wav_path=str(audio_path),
                    transcript_words=transcript_words,
                    duration_sec=duration or 120.0,
                    duration_preset=project.duration_preset or "Auto",
                    clip_count=project.clip_count or 5,
                    topic_focus=project.topic_focus,
                    formula_weights=formula_weights,
                    multimodal_context=mm_analysis,
                )
                self.update_progress(
                    project_id, 88, "gemini", "Offline blueprints extracted",
                    message=f"Extracted {len(raw_clips)} viral moments locally with Offline Multimodal AI",
                    eta_seconds=8,
                    log_stage="OFFLINE AI",
                    live_metrics={"highlights_detected": len(raw_clips)},
                )
            elif ai_provider == "ollama":
                self.update_progress(
                    project_id, 70, "gemini", f"Local Ollama AI ({ollama_model})",
                    message=f"{words_loaded_note}Connecting to local Ollama on {ollama_endpoint}...",
                    eta_seconds=15,
                    log_stage="OLLAMA",
                )
                try:
                    raw_clips = extract_viral_clips_ollama(
                        transcript_words=transcript_words,
                        total_duration=duration or 120.0,
                        duration_preset=project.duration_preset or "Auto",
                        clip_count=project.clip_count or 5,
                        topic_focus=project.topic_focus,
                        endpoint=ollama_endpoint,
                        model=ollama_model,
                        on_log=lambda stage, msg: self.update_progress(project_id, 75, "gemini", "Ollama LLM", message=msg, eta_seconds=10, log_stage="OLLAMA")
                    )
                except Exception as o_err:
                    logger.warning(f"Ollama extraction failed: {o_err}. Seamlessly falling back to 100% offline engine.")
                    self.update_progress(
                        project_id, 75, "gemini", "Offline Multimodal AI",
                        message=f"Ollama unreachable ({str(o_err)[:60]}) — seamlessly switching to Offline Multimodal AI",
                        level="WARN",
                        log_stage="OFFLINE AI",
                    )
                    raw_clips = extract_viral_clips_offline(
                        audio_wav_path=str(audio_path),
                        transcript_words=transcript_words,
                        duration_sec=duration or 120.0,
                        duration_preset=project.duration_preset or "Auto",
                        clip_count=project.clip_count or 5,
                        topic_focus=project.topic_focus,
                        formula_weights=formula_weights,
                        multimodal_context=mm_analysis,
                    )
                    for c in raw_clips:
                        c["analysis_source"] = "offline_fallback"
            else:
                # Cloud Gemini with 4-Model Cascade & Auto-Offline Fallback
                clean_key = (gemini_api_key or "").strip()
                if not _is_valid_api_key(clean_key):
                    self.pause_job_for_api_key(
                        project_id,
                        reason="missing",
                        message="A Google Gemini API key is required for Gemini AI mode. Paste your API key to continue, or switch to 100% Offline Multimodal AI Mode."
                    )
                    return

                self.update_progress(
                    project_id, 68, "gemini", "Analyzing virality & hooks with Gemini...",
                    message=f"{words_loaded_note}Sending transcript + affective telemetry to {gemini_model} (timeout: 60s)...",
                    eta_seconds=15,
                    log_stage="GEMINI",
                )

                def on_gemini_log(stage: str, msg: str):
                    self.update_progress(
                        project_id, 75, "gemini", "Gemini AI Analysis",
                        message=msg,
                        eta_seconds=12,
                        log_stage="GEMINI",
                    )

                try:
                    raw_clips = extract_viral_clips(
                        proxy_path=str(proxy_path) if proxy_path.exists() else None,
                        total_duration=duration or 120.0,
                        duration_preset=project.duration_preset or "Auto",
                        clip_count=project.clip_count or 5,
                        topic_focus=project.topic_focus,
                        transcript_words=transcript_words,
                        gemini_analysis_mode=gemini_analysis_mode,
                        preferred_model=gemini_model,
                        on_log=on_gemini_log,
                        formula_weights=formula_weights,
                        multimodal_context=mm_analysis,
                    )
                except GeminiAuthError as ae:
                    logger.warning(f"Project {project_id} paused due to Gemini auth error: {ae.message}")
                    self.pause_job_for_api_key(project_id, reason=ae.reason, message=ae.message)
                    return
                except GeminiUnavailableError as ue:
                    msg = getattr(ue, "message", str(ue))
                    logger.warning(f"All Gemini models in cascade failed: {msg}")
                    is_overload = any(k in msg.upper() for k in ["503", "429", "UNAVAILABLE", "RESOURCE_EXHAUSTED", "OVERLOADED", "HIGH DEMAND"])
                    summary_reason = "503/429 high demand" if is_overload else f"errors ({msg[:110]})"
                    if auto_fallback_offline:
                        self.update_progress(
                            project_id, 75, "gemini", "Offline Multimodal AI Fallback",
                            message=f"Gemini cascade exhausted ({summary_reason}) — seamlessly completing Stage 4 with Offline Multimodal AI",
                            level="WARN",
                            log_stage="OFFLINE AI",
                        )
                        raw_clips = extract_viral_clips_offline(
                            audio_wav_path=str(audio_path),
                            transcript_words=transcript_words,
                            duration_sec=duration or 120.0,
                            duration_preset=project.duration_preset or "Auto",
                            clip_count=project.clip_count or 5,
                            topic_focus=project.topic_focus,
                            formula_weights=formula_weights,
                            multimodal_context=mm_analysis,
                        )
                        for c in raw_clips:
                            c["analysis_source"] = "offline_fallback"
                    else:
                        self.fail_job(
                            project_id,
                            error_message=f"All Gemini models unavailable ({summary_reason}).",
                            error_detail=f"{ue.message}\nCheckpoints for Stages 1-3 are saved. Click 'Retry Job' or 'Complete in Offline Mode' below."
                        )
                        return

                self.update_progress(
                    project_id, 88, "gemini", "Viral blueprints extracted",
                    message=f"Extracted {len(raw_clips)} viral moments with hook headlines & retention scoring",
                    eta_seconds=8,
                    log_stage="GEMINI",
                    live_metrics={"highlights_detected": len(raw_clips)},
                )

            # -------------------------------------------------------------
            # Stage 5: Caption Sync & Database Assembly (90-100%)
            # -------------------------------------------------------------
            if self.is_cancelled(project_id): return
            self.update_progress(
                project_id, 90, "ready", "Snapping cuts to scene boundaries & syncing captions...",
                message="Aligning clip boundaries to visual shot cuts & word-level transcripts",
                eta_seconds=5
            )

            try:
                raw_clips = snap_clip_boundaries_to_scenes(
                    project.source_path,
                    raw_clips,
                    total_duration=float(duration or 0.0),
                    snap_window=0.5,
                )
            except Exception as snap_err:
                logger.warning(f"Scene boundary snapping skipped: {snap_err}")

            saved_clips = []
            with Session(engine) as session:
                for idx, item in enumerate(raw_clips):
                    if self.is_cancelled(project_id): return
                    start_t = float(item["start_time"])
                    dur_t = float(item["duration"])
                    end_t = float(item["end_time"])

                    # Slice words from master transcript_words
                    clip_words = []
                    if transcript_words:
                        for w in transcript_words:
                            w_start = float(w.get("start", 0.0))
                            w_end = float(w.get("end", 0.0))
                            if w_start >= (start_t - 0.25) and w_end <= (end_t + 0.25):
                                clip_words.append({
                                    "word": w.get("word", ""),
                                    "start": round(max(0.0, w_start - start_t), 3),
                                    "end": round(max(0.0, w_end - start_t), 3),
                                    "is_filler": w.get("is_filler", False),
                                    "deleted": False
                                })

                    # Fallback to audio segment transcription if slice is empty
                    if not clip_words:
                        try:
                            clip_words = transcribe_clip(
                                project.source_path,
                                start_t,
                                dur_t,
                                language=project.caption_language
                            )
                        except Exception as ce:
                            logger.warning(f"Clip-segment fallback transcription failed ({ce})")

                    clip = ViralClip(
                        project_id=project_id,
                        start_time=start_t,
                        end_time=end_t,
                        duration=dur_t,
                        virality_score=int(item.get("virality_score", 85)),
                        hook_score=int(item.get("hook_score", 85)),
                        retention_score=int(item.get("retention_score", 85)),
                        emotional_score=int(item.get("emotional_score", 80)),
                        standalone_score=int(item.get("standalone_score", 85)),
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
                        status="draft",
                        render_status="idle",
                        render_progress=0,
                        analysis_source=item.get("analysis_source", "gemini")
                    )
                    clip.set_titles(item.get("youtube_titles", []))
                    clip.set_transcript(clip_words)
                    affect_opts = {}
                    for k in (
                        "narrative_arc",
                        "arc_label",
                        "dominant_emotion",
                        "vocal_emotion",
                        "emotion_arousal",
                        "emotion_valence",
                        "acoustic_events",
                    ):
                        if item.get(k) is not None:
                            affect_opts[k] = item[k]
                    if affect_opts:
                        affect_opts["offline_multimodal_active"] = str(item.get("analysis_source", "")).startswith("offline")
                        clip.set_options(affect_opts)

                    # Stage 5 Smart Auto-Frame AI Router (corner cam / dual podcast / talking head / pure gameplay)
                    if project.source_path and os.path.exists(project.source_path):
                        try:
                            from app.services.vision_analyzer import analyze_clip_visuals
                            from app.services.auto_framer import recommend_optimal_framing

                            vmap = analyze_clip_visuals(
                                video_path=project.source_path,
                                start_time=float(clip.start_time or 0.0),
                                end_time=float(clip.end_time or 0.0),
                                use_cache=True,
                            )
                            rec = recommend_optimal_framing(vmap)
                            proj_framing = str(getattr(project, "default_framing", "smart_auto") or "smart_auto")
                            explicit_layouts = {
                                "auto_track",
                                "gameplay_webcam_pip",
                                "webcam_gameplay_pip",
                                "webcam_gameplay_split",
                                "split_podcast",
                                "fit_blur",
                            }
                            if proj_framing != "smart_auto" and proj_framing in explicit_layouts:
                                clip.frame_layout = proj_framing
                            else:
                                clip.frame_layout = rec["frame_layout"]
                            clip.webcam_corner = rec["webcam_corner"]
                            clip.set_visual_map(vmap)
                            if rec.get("source_webcam_region"):
                                clip.set_source_webcam_region(rec["source_webcam_region"])
                        except Exception as af_err:
                            logger.debug(f"Stage 5 smart auto-frame skipped for clip: {af_err}")

                    session.add(clip)
                    saved_clips.append(clip)

                session.commit()
                for c_obj in saved_clips:
                    session.refresh(c_obj)

                # Extract Top 6 Best Frames per clip & auto-snap unhealthy clip openers forward (<= +0.6s)
                if project.source_path and os.path.exists(project.source_path):
                    try:
                        from app.services.best_frame_engine import extract_best_frames

                        bf_dir = Path(settings.STORAGE_DIR) / "assets" / "best_frames" / str(project_id)
                        bf_dir.mkdir(parents=True, exist_ok=True)
                        for c_obj in saved_clips:
                            bf_res = extract_best_frames(
                                video_path=project.source_path,
                                video_analysis=vid_analysis,
                                audio_analysis=aud_analysis,
                                start_time=float(c_obj.start_time or 0.0),
                                end_time=float(c_obj.end_time or 0.0),
                                output_dir=str(bf_dir),
                                clip_id=int(c_obj.id or 0),
                                project_id=project_id,
                            )
                            sugg_start = bf_res.get("suggested_start_time")
                            if sugg_start is not None and (float(c_obj.end_time) - float(sugg_start)) >= 1.2:
                                new_s = round(float(sugg_start), 2)
                                if new_s > float(c_obj.start_time) and (new_s - float(c_obj.start_time)) <= 0.65:
                                    c_obj.start_time = new_s
                                    c_obj.duration = round(float(c_obj.end_time) - new_s, 2)
                                    c_obj.set_keep_segments([[new_s, float(c_obj.end_time)]])
                                    if transcript_words:
                                        re_words = []
                                        for w in transcript_words:
                                            ws = float(w.get("start", 0.0))
                                            we = float(w.get("end", 0.0))
                                            if ws >= (new_s - 0.15) and we <= (float(c_obj.end_time) + 0.25):
                                                re_words.append({
                                                    "word": w.get("word", ""),
                                                    "start": round(max(0.0, ws - new_s), 3),
                                                    "end": round(max(0.0, we - new_s), 3),
                                                    "is_filler": w.get("is_filler", False),
                                                    "deleted": False,
                                                })
                                        if re_words:
                                            c_obj.set_transcript(re_words)
                            c_obj.set_best_frames(bf_res.get("frames") or [])
                            session.add(c_obj)
                        session.commit()
                        for c_obj in saved_clips:
                            session.refresh(c_obj)
                        self.update_progress(
                            project_id, 90, "ready", "Selected 6 best keyframes per clip",
                            message=f"Selected 6 best keyframes per clip across {len(saved_clips)} clip(s)",
                            eta_seconds=4
                        )
                    except Exception as bf_err:
                        logger.debug(f"Stage 5 best-frame extraction skipped: {bf_err}")

                saved_clip_ids = [c_obj.id for c_obj in saved_clips if c_obj.id is not None]

            # Pre-render fast_preview MP4s for ALL extracted clips (1..N)
            total_to_render = len(saved_clip_ids)
            if total_to_render > 0 and project.source_path and os.path.exists(project.source_path):
                active_db_engine = app_db.engine if app_db.engine is not _DEFAULT_ENGINE else engine
                for clip_idx, cid in enumerate(saved_clip_ids, start=1):
                    if self.is_cancelled(project_id):
                        return

                    with Session(active_db_engine) as rsession:
                        c_row = rsession.get(ViralClip, cid)
                        if not c_row:
                            continue
                        clip_label = (c_row.hook_headline or c_row.selected_title or f"Clip {clip_idx}").strip()
                        clip_words_list = c_row.get_transcript()
                        c_row.status = "rendering"
                        c_row.render_status = "rendering"
                        c_row.render_progress = 15
                        c_row.render_message = f"Rendering preview {clip_idx}/{total_to_render}..."
                        rsession.add(c_row)
                        rsession.commit()

                        clip_args = {
                            "clip_id": cid,
                            "project_id": project_id,
                            "start_time": float(c_row.start_time),
                            "duration": float(c_row.duration),
                            "words": clip_words_list,
                            "aspect_ratio": c_row.aspect_ratio,
                            "custom_width": c_row.custom_width,
                            "custom_height": c_row.custom_height,
                            "frame_layout": c_row.frame_layout,
                            "webcam_corner": c_row.webcam_corner,
                            "webcam_split_ratio": c_row.webcam_split_ratio,
                            "source_webcam_region": c_row.get_source_webcam_region(),
                            "visual_map": c_row.get_visual_map(),
                            "framing_override": c_row.get_framing_override(),
                            "best_frames": c_row.get_best_frames(),
                            "pip_border_color": c_row.pip_border_color,
                            "zoom": c_row.zoom,
                            "pan_x_offset": c_row.pan_x_offset,
                            "burn_captions": c_row.burn_captions,
                            "timing_offset": c_row.timing_offset,
                            "caption_style": c_row.caption_style,
                            "caption_font": c_row.caption_font,
                            "caption_animation": c_row.caption_animation,
                            "font_size_percent": c_row.font_size_percent,
                            "base_color": c_row.base_color,
                            "active_color": c_row.active_color,
                            "outline_color": c_row.outline_color,
                            "font_size": c_row.font_size,
                            "bold": c_row.bold,
                            "italic": c_row.italic,
                            "all_caps": c_row.all_caps,
                            "text_align": c_row.text_align,
                            "position_grid": c_row.position_grid,
                            "position_x": c_row.position_x,
                            "position_y": c_row.position_y,
                            "hold_back_alpha": c_row.hold_back_alpha,
                            "highlight_shape": c_row.highlight_shape,
                            "emoji_enabled": c_row.emoji_enabled,
                            "highlight_power_words": bool(getattr(c_row, "highlight_power_words", False)),
                            "how_phrase_arrives": c_row.how_phrase_arrives,
                            "outline_width": c_row.outline_width,
                            "shadow_depth": c_row.shadow_depth,
                            "glow_intensity": c_row.glow_intensity,
                            "letter_spacing": c_row.letter_spacing,
                            "clean_filler_words": c_row.clean_filler_words,
                            "tighten_pacing": c_row.tighten_pacing,
                            "silence_threshold": c_row.silence_threshold,
                            "censor_profanity": c_row.censor_profanity,
                            "clip_size": c_row.clip_size,
                            "color_preset": c_row.color_preset,
                            "hardware_mode": c_row.hardware_mode or "gpu",
                            "hook_headline": c_row.hook_headline,
                            "show_hook_banner": c_row.show_hook_banner,
                            "cold_open_quote": c_row.cold_open_quote,
                            "use_cold_open": c_row.use_cold_open,
                            "keep_segments": c_row.get_keep_segments(),
                            "merge_crossfade_sec": float(getattr(c_row, "merge_crossfade_sec", 0.03) or 0.03),
                            "speakers": c_row.get_speakers(),
                            "speaker_lower_thirds": bool(getattr(c_row, "speaker_lower_thirds", False)),
                            "face_safe_captions": bool(getattr(c_row, "face_safe_captions", True)),
                            "callout_cards": c_row.get_callout_cards(),
                            "auto_callouts": bool(getattr(c_row, "auto_callouts", False)),
                            "bilingual_enabled": bool(getattr(c_row, "bilingual_enabled", False)),
                            "secondary_language": str(getattr(c_row, "secondary_language", "") or ""),
                            "dubbing_enabled": bool(getattr(c_row, "dubbing_enabled", False)),
                            "dubbed_audio_path": getattr(c_row, "dubbed_audio_path", None),
                            "dubbing_bed_volume": float(getattr(c_row, "dubbing_bed_volume", 0.15) or 0.15),
                            "backdrop_mode": getattr(c_row, "backdrop_mode", "none") or "none",
                            "backdrop_blur_intensity": float(getattr(c_row, "backdrop_blur_intensity", 18.0) or 18.0),
                            "backdrop_color": getattr(c_row, "backdrop_color", "#18181B") or "#18181B",
                            "camera_motion": str(getattr(c_row, "camera_motion", "steady") or "steady"),
                            "whip_pan_cuts": bool(getattr(c_row, "whip_pan_cuts", False)),
                            "broll_layout": str(getattr(c_row, "broll_layout", "fullscreen") or "fullscreen"),
                            "depth_pop": float(getattr(c_row, "depth_pop", 0.0) or 0.0),
                            "privacy_blur_boxes": c_row.get_privacy_blur_boxes(),
                            "beat_sync_enabled": bool(getattr(c_row, "beat_sync_enabled", False)),
                            "spectrum_enabled": bool(getattr(c_row, "spectrum_enabled", False)),
                            "spectrum_position": str(getattr(c_row, "spectrum_position", "bottom") or "bottom"),
                            "spectrum_color": str(getattr(c_row, "spectrum_color", "#C6F32F") or "#C6F32F"),
                            "chapters_enabled": bool(getattr(c_row, "chapters_enabled", False)),
                            "chapters": c_row.get_chapters(),
                            "handle_watermark_enabled": bool(getattr(c_row, "handle_watermark_enabled", False)),
                            "handle_text": str(getattr(c_row, "handle_text", "") or ""),
                            "handle_platform_icon": str(getattr(c_row, "handle_platform_icon", "none") or "none"),
                            "handle_corner": str(getattr(c_row, "handle_corner", "bottom_right") or "bottom_right"),
                            "handle_opacity": float(getattr(c_row, "handle_opacity", 0.65) or 0.65),
                            "handle_display_mode": str(getattr(c_row, "handle_display_mode", "always") or "always"),
                        }

                    pct = 90 + int(9 * (clip_idx - 1) / max(1, total_to_render))
                    self.update_progress(
                        project_id,
                        pct,
                        "ready",
                        f"Rendering clip preview {clip_idx}/{total_to_render}...",
                        message=f"Rendering clip preview {clip_idx}/{total_to_render}: {clip_label}...",
                        eta_seconds=max(1, (total_to_render - clip_idx + 1) * 3),
                    )

                    try:
                        rendered_file = render_clip_ultra(
                            master_video_path=project.source_path,
                            quality_mode="fast_preview",
                            **clip_args,
                        )
                        with Session(active_db_engine) as wsession:
                            c_done = wsession.get(ViralClip, cid)
                            if c_done:
                                c_done.rendered_video_path = rendered_file
                                c_done.rendered_path = rendered_file
                                c_done.render_status = "completed"
                                c_done.status = "ready"
                                c_done.render_progress = 100
                                c_done.render_message = "Preview ready"
                                wsession.add(c_done)
                                wsession.commit()

                        done_pct = 90 + int(9 * clip_idx / max(1, total_to_render))
                        self.update_progress(
                            project_id,
                            done_pct,
                            "ready",
                            f"Preview {clip_idx}/{total_to_render} ready",
                            message=f"Ready {clip_idx}/{total_to_render}: {clip_label}",
                            level="SUCCESS",
                            eta_seconds=max(1, (total_to_render - clip_idx) * 3),
                        )
                    except Exception as clip_render_err:
                        logger.warning(f"Stage 5 preview render failed for clip {cid}: {clip_render_err}")
                        self.update_progress(
                            project_id,
                            pct,
                            "ready",
                            f"Preview warning {clip_idx}/{total_to_render}",
                            message=f"Preview render deferred for '{clip_label}': {str(clip_render_err)[:100]}",
                            level="WARN",
                        )
                        with Session(active_db_engine) as esession:
                            c_err = esession.get(ViralClip, cid)
                            if c_err:
                                c_err.status = "draft"
                                c_err.render_status = "idle"
                                c_err.render_progress = 0
                                c_err.render_message = "Will render on-demand when selected"
                                esession.add(c_err)
                                esession.commit()

            self.finish_job(project_id, len(saved_clips))
            logger.info(f"Pipeline finished successfully for project {project_id} with {len(saved_clips)} clips")

        except Exception as e:
            err_msg = str(e)
            tb = traceback.format_exc()
            logger.error(f"Pipeline error for project {project_id}: {err_msg}\n{tb}")
            self.fail_job(project_id, err_msg, tb)

# Global Singleton
job_manager = JobManager()
