import os
import shutil
import platform
import subprocess
import logging
from typing import Dict, Any, Optional
from app.config import settings

logger = logging.getLogger(__name__)

def detect_hardware_specs() -> Dict[str, Any]:
    """
    Detect real CPU model & cores, RAM GB, real GPU device name + VRAM,
    active hardware encoder, and runtime environment telemetry.
    Never uses fake/hardcoded fallback hardware.
    """
    # 1. Real CPU Model & Cores
    cpu_cores = os.cpu_count() or 1
    cpu_model = "Unknown CPU"
    try:
        import winreg
        key = winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, r"HARDWARE\DESCRIPTION\System\CentralProcessor\0")
        val, _ = winreg.QueryValueEx(key, "ProcessorNameString")
        cpu_model = val.strip()
        winreg.CloseKey(key)
    except Exception:
        cpu_model = platform.processor() or "Multi-Core CPU"

    # 2. Real RAM via GlobalMemoryStatusEx or psutil
    ram_gb = 0
    try:
        import psutil
        ram_gb = round(psutil.virtual_memory().total / (1024**3), 1)
    except Exception:
        try:
            import ctypes
            class MEMORYSTATUSEX(ctypes.Structure):
                _fields_ = [
                    ("dwLength", ctypes.c_ulong),
                    ("dwMemoryLoad", ctypes.c_ulong),
                    ("ullTotalPhys", ctypes.c_ulonglong),
                    ("ullAvailPhys", ctypes.c_ulonglong),
                    ("ullTotalPageFile", ctypes.c_ulonglong),
                    ("ullAvailPageFile", ctypes.c_ulonglong),
                    ("ullTotalVirtual", ctypes.c_ulonglong),
                    ("ullAvailVirtual", ctypes.c_ulonglong),
                    ("sullAvailExtendedVirtual", ctypes.c_ulonglong),
                ]
            stat = MEMORYSTATUSEX()
            stat.dwLength = ctypes.sizeof(MEMORYSTATUSEX)
            ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(stat))
            ram_gb = round(stat.ullTotalPhys / (1024**3), 1)
        except Exception:
            pass

    # 3. Real GPU & VRAM via nvidia-smi (never hardcoded fake GPU)
    gpu_name = "None"
    vram_mb = 0
    has_cuda = False
    try:
        res = subprocess.run(
            ["nvidia-smi", "--query-gpu=name,memory.total", "--format=csv,noheader,nounits"],
            capture_output=True,
            text=True,
            timeout=3
        )
        if res.returncode == 0 and res.stdout.strip():
            first_line = res.stdout.strip().splitlines()[0]
            parts = [p.strip() for p in first_line.split(",")]
            if parts and parts[0]:
                gpu_name = parts[0]
                has_cuda = True
                if len(parts) > 1:
                    try:
                        vram_mb = int(float(parts[1]))
                    except Exception:
                        pass
    except Exception:
        gpu_name = "None"
        has_cuda = False

    # 4. Active Hardware Encoder
    active_encoder = "libx264"
    from app.services.proxy import get_ffmpeg_cmd
    from app.services.job_manager import get_persisted_settings
    persisted = get_persisted_settings()
    ffmpeg_bin = get_ffmpeg_cmd()
    try:
        res = subprocess.run([ffmpeg_bin, "-encoders"], capture_output=True, text=True, timeout=5)
        if "h264_nvenc" in res.stdout and has_cuda:
            active_encoder = "h264_nvenc"
        elif "h264_videotoolbox" in res.stdout and platform.system() == "Darwin":
            active_encoder = "h264_videotoolbox"
    except Exception as e:
        logger.warning(f"Failed to probe ffmpeg encoders: {e}")

    effective_key = settings.GEMINI_API_KEY or os.environ.get("GEMINI_API_KEY", "") or persisted.get("gemini_api_key", "")
    gemini_configured = bool(effective_key and effective_key != "your_gemini_api_key_here")

    return {
        "cpu_model": cpu_model,
        "cpu_cores": cpu_cores,
        "ram_gb": ram_gb,
        "gpu_name": gpu_name,
        "vram_mb": vram_mb,
        "has_cuda": has_cuda,
        "active_encoder": active_encoder,
        "whisper_model": persisted.get("whisper_model", settings.WHISPER_MODEL),
        "gemini_model": persisted.get("gemini_model", settings.GEMINI_MODEL),
        "ai_provider": persisted.get("ai_provider", settings.AI_PROVIDER),
        "gemini_status": "active" if gemini_configured else "unconfigured",
        "gemini_configured": gemini_configured,
        "gemini_enabled": persisted.get("gemini_enabled", True)
    }
