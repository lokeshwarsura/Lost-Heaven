import os
import shutil
import logging
from pathlib import Path
from typing import Dict, Any, List
from app.config import settings

logger = logging.getLogger(__name__)

WHISPER_MODELS_CATALOG = [
    {
        "id": "tiny",
        "name": "Tiny",
        "size_mb": 75,
        "ram_required_gb": 1.0,
        "speed": "Ultra Fast",
        "precision": "Good",
        "description": "Lightweight model for low-resource environments."
    },
    {
        "id": "base",
        "name": "Base",
        "size_mb": 145,
        "ram_required_gb": 1.5,
        "speed": "Fast",
        "precision": "High",
        "description": "Standard fast multilingual model with great accuracy."
    },
    {
        "id": "small",
        "name": "Small",
        "size_mb": 480,
        "ram_required_gb": 2.5,
        "speed": "Medium",
        "precision": "Very High",
        "description": "Crisp word-level precision for professional dialogue."
    },
    {
        "id": "medium",
        "name": "Medium",
        "size_mb": 1500,
        "ram_required_gb": 5.0,
        "speed": "Balanced",
        "precision": "Excellent",
        "description": "Superior accuracy for noisy backgrounds & podcasts."
    },
    {
        "id": "large-v3-turbo",
        "name": "Large v3 Turbo",
        "size_mb": 1600,
        "ram_required_gb": 6.0,
        "speed": "Fast (GPU)",
        "precision": "Studio Master",
        "description": "State-of-the-art multilingual model with millisecond word timestamps."
    }
]

def get_models_dir() -> Path:
    models_dir = Path(settings.STORAGE_DIR) / "models"
    models_dir.mkdir(parents=True, exist_ok=True)
    return models_dir

def get_model_storage_info() -> Dict[str, Any]:
    """Calculate total storage used by downloaded models and remaining drive space."""
    models_dir = get_models_dir()
    total_model_bytes = sum(f.stat().st_size for f in models_dir.glob("**/*") if f.is_file())
    model_used_mb = round(total_model_bytes / (1024**2), 1)

    free_bytes = shutil.disk_usage(str(models_dir)).free
    free_gb = round(free_bytes / (1024**3), 1)

    return {
        "models_dir": str(models_dir.resolve()),
        "used_mb": model_used_mb,
        "used_gb": round(model_used_mb / 1024, 2),
        "free_gb": free_gb
    }

def list_whisper_models() -> Dict[str, Any]:
    """
    List all catalog models, indicating downloaded status, sizes,
    and a RAM-aware RECOMMENDED badge.
    """
    system_ram_gb = 16.0
    try:
        import psutil
        system_ram_gb = psutil.virtual_memory().total / (1024**3)
    except Exception:
        try:
            import ctypes
            class MEMORYSTATUSEX(ctypes.Structure):
                _fields_ = [
                    ("dwLength", ctypes.c_ulong),
                    ("dwMemoryLoad", ctypes.c_ulong),
                    ("ullTotalPhys", ctypes.c_ulonglong),
                ]
            stat = MEMORYSTATUSEX()
            stat.dwLength = ctypes.sizeof(MEMORYSTATUSEX)
            ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(stat))
            system_ram_gb = stat.ullTotalPhys / (1024**3)
        except Exception:
            pass

    models_dir = get_models_dir()
    storage_info = get_model_storage_info()

    model_list: List[Dict[str, Any]] = []

    for m in WHISPER_MODELS_CATALOG:
        m_id = m["id"]
        # Check if model folder or marker exists in storage/models
        # HuggingFace hub cache or direct folder
        is_installed = (
            (models_dir / m_id).exists() or
            (models_dir / f"models--Systran--faster-whisper-{m_id}").exists() or
            (m_id == settings.WHISPER_MODEL)
        )

        # Recommendation logic:
        # If RAM >= 16 GB, recommend large-v3-turbo.
        # If RAM >= 8 GB, recommend small or base.
        # Otherwise recommend base.
        is_recommended = False
        if system_ram_gb >= 16.0 and m_id == "large-v3-turbo":
            is_recommended = True
        elif 8.0 <= system_ram_gb < 16.0 and m_id == "small":
            is_recommended = True
        elif system_ram_gb < 8.0 and m_id == "base":
            is_recommended = True

        model_list.append({
            **m,
            "is_installed": is_installed,
            "is_active": m_id == settings.WHISPER_MODEL,
            "is_recommended": is_recommended
        })

    return {
        "storage": storage_info,
        "active_model": settings.WHISPER_MODEL,
        "models": model_list
    }

def download_whisper_model(model_name: str) -> Dict[str, Any]:
    """Download faster-whisper model into storage/models/."""
    try:
        from faster_whisper import download_model
        models_dir = str(get_models_dir())
        download_model(model_name, output_dir=models_dir)
        return {"success": True, "message": f"Model {model_name} downloaded successfully."}
    except Exception as e:
        logger.error(f"Download failed for {model_name}: {e}")
        return {"success": False, "message": str(e)}

def delete_whisper_model(model_name: str) -> Dict[str, Any]:
    """Delete downloaded model directory to free up disk space."""
    models_dir = get_models_dir()
    deleted = False

    for target in [models_dir / model_name, models_dir / f"models--Systran--faster-whisper-{model_name}"]:
        if target.exists():
            if target.is_dir():
                shutil.rmtree(target)
            else:
                target.unlink()
            deleted = True

    return {
        "success": True,
        "deleted": deleted,
        "message": f"Model {model_name} removed from disk." if deleted else "Model not found on disk."
    }
