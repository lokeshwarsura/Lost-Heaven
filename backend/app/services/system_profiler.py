"""
First-run hardware & environment profiler, tiered setting recommender,
and 1-click prerequisite downloader (FFmpeg essentials + faster-whisper model cache).

Safe dependency fallbacks: uses stdlib (`platform`, `os`, `shutil`, `subprocess`, `ctypes`)
with optional `psutil` / `ctranslate2` / `nvidia-smi` / `ffmpeg -encoders` probes so a
fresh GitHub clone never crashes before prerequisites are installed.
"""

from __future__ import annotations

import ctypes
import importlib.util
import io
import logging
import os
import platform
import shutil
import subprocess
import sys
import threading
import urllib.request
import zipfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from app.config import BASE_DIR, PROJECT_ROOT, settings

logger = logging.getLogger(__name__)

STATIC_FFMPEG_WINDOWS_URL = "https://www.gyan.dev/ffmpeg/builds/ffmpeg-release-essentials.zip"

WHISPER_DOWNLOAD_SIZES_MB: Dict[str, int] = {
    "tiny": 75,
    "base": 145,
    "small": 465,
    "medium": 1500,
    "large-v3-turbo": 1500,
}

_INSTALL_LOCK = threading.RLock()
_INSTALL_STATE: Dict[str, Any] = {
    "status": "idle",  # idle | running | completed | error
    "progress_pct": 0,
    "current_step": "",
    "message": "Waiting to start",
    "error": None,
    "logs": [],
    "ffmpeg_installed": False,
    "whisper_model_installed": None,
    "started_at": None,
    "updated_at": None,
}


def _utc_iso() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _backend_bin_dir() -> Path:
    return BASE_DIR / "bin"


def _find_binary_path(binary_stem: str) -> Optional[str]:
    """Locate ffmpeg/ffprobe inside backend/bin, workspace bin/, or system PATH."""
    exe_name = f"{binary_stem}.exe" if sys.platform == "win32" else binary_stem
    candidates = [
        _backend_bin_dir() / exe_name,
        PROJECT_ROOT / "bin" / exe_name,
    ]
    for cand in candidates:
        if cand.exists() and cand.is_file():
            return str(cand.resolve())
    found = shutil.which(binary_stem)
    if found:
        return found
    return None


def _detect_cpu_info() -> Dict[str, Any]:
    logical = int(os.cpu_count() or 1)
    physical = max(1, logical // 2) if logical > 1 else 1
    try:
        import psutil  # type: ignore
        p_cnt = psutil.cpu_count(logical=False)
        if p_cnt:
            physical = int(p_cnt)
    except Exception:
        pass

    model_name = platform.processor() or "Multi-Core CPU"
    sys_name = platform.system()
    if sys_name == "Windows":
        try:
            import winreg
            key = winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, r"HARDWARE\DESCRIPTION\System\CentralProcessor\0")
            val, _ = winreg.QueryValueEx(key, "ProcessorNameString")
            if val and str(val).strip():
                model_name = str(val).strip()
            winreg.CloseKey(key)
        except Exception:
            pass
    elif sys_name == "Darwin":
        try:
            res = subprocess.run(["sysctl", "-n", "machdep.cpu.brand_string"], capture_output=True, text=True, timeout=2)
            if res.returncode == 0 and res.stdout.strip():
                model_name = res.stdout.strip()
        except Exception:
            pass
    elif sys_name == "Linux":
        try:
            cpuinfo = Path("/proc/cpuinfo")
            if cpuinfo.exists():
                for line in cpuinfo.read_text(encoding="utf-8", errors="ignore").splitlines():
                    if "model name" in line.lower() and ":" in line:
                        cand = line.split(":", 1)[1].strip()
                        if cand:
                            model_name = cand
                            break
        except Exception:
            pass

    return {
        "model": model_name,
        "physical_cores": physical,
        "logical_threads": logical,
    }


def _detect_ram_gb() -> Tuple[float, float]:
    """Return (total_gb, available_gb) cleanly across OSes without requiring psutil."""
    try:
        import psutil  # type: ignore
        vm = psutil.virtual_memory()
        return round(vm.total / (1024 ** 3), 1), round(vm.available / (1024 ** 3), 1)
    except Exception:
        pass

    sys_name = platform.system()
    if sys_name == "Windows":
        try:
            class _MEMORYSTATUSEX(ctypes.Structure):
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

            stat = _MEMORYSTATUSEX()
            stat.dwLength = ctypes.sizeof(_MEMORYSTATUSEX)
            ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(stat))
            total_gb = round(stat.ullTotalPhys / (1024 ** 3), 1)
            avail_gb = round(stat.ullAvailPhys / (1024 ** 3), 1)
            return total_gb, avail_gb
        except Exception:
            pass

    if sys_name == "Linux":
        try:
            meminfo = Path("/proc/meminfo")
            if meminfo.exists():
                vals: Dict[str, int] = {}
                for line in meminfo.read_text(encoding="utf-8", errors="ignore").splitlines():
                    parts = line.split(":")
                    if len(parts) == 2:
                        num_tok = parts[1].strip().split()[0]
                        if num_tok.isdigit():
                            vals[parts[0].strip()] = int(num_tok)
                total_kb = vals.get("MemTotal", 0)
                avail_kb = vals.get("MemAvailable", vals.get("MemFree", 0))
                if total_kb > 0:
                    return round(total_kb / (1024 ** 2), 1), round(avail_kb / (1024 ** 2), 1)
        except Exception:
            pass

    if sys_name == "Darwin":
        try:
            res = subprocess.run(["sysctl", "-n", "hw.memsize"], capture_output=True, text=True, timeout=2)
            if res.returncode == 0 and res.stdout.strip().isdigit():
                total_bytes = int(res.stdout.strip())
                total_gb = round(total_bytes / (1024 ** 3), 1)
                return total_gb, round(total_gb * 0.6, 1)
        except Exception:
            pass

    return 0.0, 0.0


def _detect_gpu_info(system_name: str, arch: str, ram_total_gb: float) -> Dict[str, Any]:
    """Detect NVIDIA CUDA GPUs via ctranslate2/nvidia-smi, Apple Silicon, or integrated/CPU setups."""
    cuda_device_count = 0
    try:
        import ctranslate2  # type: ignore
        cuda_device_count = int(ctranslate2.get_cuda_device_count() or 0)
    except Exception:
        cuda_device_count = 0

    gpu_name = "None"
    vram_mb = 0
    has_nvidia = False

    try:
        res = subprocess.run(
            ["nvidia-smi", "--query-gpu=name,memory.total", "--format=csv,noheader,nounits"],
            capture_output=True,
            text=True,
            timeout=3,
        )
        if res.returncode == 0 and res.stdout.strip():
            first_line = res.stdout.strip().splitlines()[0]
            parts = [p.strip() for p in first_line.split(",")]
            if parts and parts[0]:
                gpu_name = parts[0]
                has_nvidia = True
                if len(parts) > 1:
                    try:
                        vram_mb = int(float(parts[1]))
                    except Exception:
                        vram_mb = 0
    except Exception:
        pass

    has_cuda = bool(has_nvidia or cuda_device_count > 0)
    is_apple_silicon = bool(system_name == "Darwin" and arch in ("arm64", "aarch64"))

    if gpu_name == "None":
        if is_apple_silicon:
            gpu_name = f"Apple Silicon Unified GPU ({arch})"
        elif cuda_device_count > 0:
            gpu_name = f"NVIDIA CUDA GPU ({cuda_device_count} device)"

    vram_gb = round(vram_mb / 1024.0, 1) if vram_mb > 0 else 0.0
    return {
        "name": gpu_name,
        "has_cuda": has_cuda,
        "has_nvidia": has_nvidia,
        "cuda_device_count": cuda_device_count,
        "vram_mb": vram_mb,
        "vram_gb": vram_gb,
        "is_apple_silicon": is_apple_silicon,
    }


def _probe_ffmpeg_encoders(ffmpeg_path: Optional[str]) -> List[str]:
    if not ffmpeg_path:
        return ["libx264"]
    detected: List[str] = []
    try:
        res = subprocess.run([ffmpeg_path, "-hide_banner", "-encoders"], capture_output=True, text=True, timeout=5)
        out = res.stdout or ""
        for cand in ("h264_nvenc", "h264_videotoolbox", "h264_amf", "h264_qsv", "libx264"):
            if cand in out:
                detected.append(cand)
    except Exception:
        pass
    if "libx264" not in detected:
        detected.append("libx264")
    return detected


def _scan_cached_whisper_models() -> List[str]:
    """Audit HuggingFace hub cache + storage/models for pre-downloaded faster-whisper weights."""
    cached: set[str] = set()
    search_roots = [
        Path.home() / ".cache" / "huggingface" / "hub",
        Path(settings.STORAGE_DIR) / "models",
    ]
    known_ids = list(WHISPER_DOWNLOAD_SIZES_MB.keys()) + ["large-v3", "large-v2"]

    for root in search_roots:
        if not root.exists():
            continue
        try:
            for model_id in known_ids:
                # HuggingFace cache folder: models--Systran--faster-whisper-<model_id>
                hf_dir = root / f"models--Systran--faster-whisper-{model_id}"
                if hf_dir.exists() and hf_dir.is_dir():
                    if any(hf_dir.rglob("model.bin")) or (hf_dir / "snapshots").exists():
                        cached.add(model_id)
                        continue
                # Direct folder under storage/models/<model_id>
                direct_dir = root / model_id
                if direct_dir.exists() and (direct_dir / "model.bin").exists():
                    cached.add(model_id)
        except Exception:
            pass

    return sorted(cached)


def profile_system_specs() -> Dict[str, Any]:
    """
    Detect real machine specifications, video encoders, disk space,
    installed CLI/Python dependencies, and cached Whisper models.
    Never crashes even if optional packages are missing on a fresh install.
    """
    sys_name = platform.system() or "Unknown"
    raw_machine = (platform.machine() or "x86_64").lower()
    if raw_machine in ("amd64", "x64", "x86_64"):
        arch = "x86_64"
    elif raw_machine in ("arm64", "aarch64"):
        arch = "arm64"
    else:
        arch = raw_machine

    cpu_info = _detect_cpu_info()
    ram_total_gb, ram_available_gb = _detect_ram_gb()
    gpu_info = _detect_gpu_info(sys_name, arch, ram_total_gb)

    ffmpeg_path = _find_binary_path("ffmpeg")
    ffprobe_path = _find_binary_path("ffprobe")
    available_encoders = _probe_ffmpeg_encoders(ffmpeg_path)
    preferred_encoder = "libx264"
    for cand in ("h264_nvenc", "h264_videotoolbox", "h264_amf", "h264_qsv", "libx264"):
        if cand in available_encoders:
            if cand == "h264_nvenc" and not gpu_info["has_cuda"]:
                continue
            if cand == "h264_videotoolbox" and sys_name != "Darwin":
                continue
            preferred_encoder = cand
            break

    storage_root = Path(settings.STORAGE_DIR)
    try:
        storage_root.mkdir(parents=True, exist_ok=True)
        du = shutil.disk_usage(str(storage_root.resolve()))
        disk_total_gb = round(du.total / (1024 ** 3), 1)
        disk_free_gb = round(du.free / (1024 ** 3), 1)
    except Exception:
        disk_total_gb, disk_free_gb = 0.0, 0.0

    tools = {
        "ffmpeg": bool(ffmpeg_path),
        "ffmpeg_path": ffmpeg_path,
        "ffprobe": bool(ffprobe_path),
        "ffprobe_path": ffprobe_path,
        "yt_dlp": bool(shutil.which("yt-dlp") or importlib.util.find_spec("yt_dlp") is not None),
    }
    packages = {
        "faster_whisper": importlib.util.find_spec("faster_whisper") is not None,
        "mediapipe": importlib.util.find_spec("mediapipe") is not None,
        "cv2": importlib.util.find_spec("cv2") is not None,
        "scipy": importlib.util.find_spec("scipy") is not None,
    }
    cached_whisper_models = _scan_cached_whisper_models()

    return {
        "os": {
            "name": sys_name,
            "release": platform.release(),
            "arch": arch,
        },
        "cpu": cpu_info,
        "ram": {
            "total_gb": ram_total_gb,
            "available_gb": ram_available_gb,
        },
        "gpu": gpu_info,
        "encoders": {
            "available": available_encoders,
            "preferred": preferred_encoder,
        },
        "storage": {
            "path": str(storage_root.resolve()),
            "total_gb": disk_total_gb,
            "free_gb": disk_free_gb,
        },
        "tools": tools,
        "packages": packages,
        "cached_whisper_models": cached_whisper_models,
        # Convenience flattened fields
        "os_name": sys_name,
        "arch": arch,
        "cpu_model": cpu_info["model"],
        "cpu_cores_physical": cpu_info["physical_cores"],
        "cpu_cores_logical": cpu_info["logical_threads"],
        "ram_total_gb": ram_total_gb,
        "ram_available_gb": ram_available_gb,
        "gpu_name": gpu_info["name"],
        "gpu_vram_gb": gpu_info["vram_gb"],
        "has_cuda": gpu_info["has_cuda"],
        "is_apple_silicon": gpu_info["is_apple_silicon"],
        "available_encoders": available_encoders,
        "preferred_encoder": preferred_encoder,
        "disk_free_gb": disk_free_gb,
    }


def build_first_run_recommendations(
    specs: Dict[str, Any],
    current_settings: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """
    Assign one of 4 hardware tiers (`ultra_gpu`, `mid_gpu`, `standard_cpu`, `lightweight_cpu`)
    and produce concrete Whisper, encoder, resolution, and prerequisite download recommendations.
    """
    cur = current_settings or {}
    gpu = specs.get("gpu") or {}
    cpu = specs.get("cpu") or {}
    ram = specs.get("ram") or {}
    enc = specs.get("encoders") or {}
    tools = specs.get("tools") or {}
    packages = specs.get("packages") or {}

    has_cuda = bool(specs.get("has_cuda", gpu.get("has_cuda", False)))
    vram_gb = float(specs.get("gpu_vram_gb", gpu.get("vram_gb", 0.0)) or 0.0)
    if vram_gb <= 0 and gpu.get("vram_mb"):
        vram_gb = round(float(gpu["vram_mb"]) / 1024.0, 1)
    is_apple_silicon = bool(specs.get("is_apple_silicon", gpu.get("is_apple_silicon", False)))
    gpu_name = str(specs.get("gpu_name", gpu.get("name", "None")) or "None")

    ram_total_gb = float(specs.get("ram_total_gb", ram.get("total_gb", 0.0)) or 0.0)
    cpu_cores = int(specs.get("cpu_cores_physical", cpu.get("physical_cores", 0)) or specs.get("cpu_cores_logical", cpu.get("logical_threads", 1)) or 1)
    cpu_logical = int(specs.get("cpu_cores_logical", cpu.get("logical_threads", 1)) or 1)
    available_encoders: List[str] = list(specs.get("available_encoders", enc.get("available", ["libx264"])) or ["libx264"])
    ffmpeg_ready = bool(tools.get("ffmpeg", True) and tools.get("ffprobe", True))
    cached_models: List[str] = list(specs.get("cached_whisper_models") or [])

    # Tier Classification
    tier = "lightweight_cpu"
    tier_label = "Lightweight CPU Setup"
    if has_cuda and vram_gb >= 7.5:
        tier = "ultra_gpu"
        tier_label = "Ultra GPU Workstation"
        nvenc_ready = ("h264_nvenc" in available_encoders) or not ffmpeg_ready
        rec_encoder = "h264_nvenc" if nvenc_ready else str(specs.get("preferred_encoder") or "libx264")
        rec_settings = {
            "whisper_model": "large-v3-turbo",
            "whisper_device": "cuda",
            "whisper_compute_type": "float16",
            "whisper_batch_size": 16,
            "video_encoder": rec_encoder,
            "encoder_preset": "p4",
            "default_resolution": "1080p",
            "vision_scan_fps": 2.0,
            "hardware_acceleration": "gpu",
        }
        summary_reason = (
            f"Detected {gpu_name} with {vram_gb:.1f} GB VRAM — enables Studio-grade Whisper large-v3-turbo "
            f"(float16, batch_size=16) and {rec_encoder} GPU rendering at 1080p."
        )
    elif (has_cuda and 3.5 <= vram_gb < 7.5) or (is_apple_silicon and ram_total_gb >= 15.0) or (
        ("h264_amf" in available_encoders or "h264_videotoolbox" in available_encoders) and ram_total_gb >= 12.0
    ):
        tier = "mid_gpu"
        tier_label = "Mid-Tier GPU Acceleration"
        if has_cuda and (("h264_nvenc" in available_encoders) or not ffmpeg_ready):
            rec_encoder = "h264_nvenc"
            comp_type = "int8_float16"
            dev = "cuda"
        elif "h264_videotoolbox" in available_encoders or is_apple_silicon:
            rec_encoder = "h264_videotoolbox"
            comp_type = "int8"
            dev = "cpu"
        elif "h264_amf" in available_encoders:
            rec_encoder = "h264_amf"
            comp_type = "int8"
            dev = "cpu"
        else:
            rec_encoder = str(specs.get("preferred_encoder") or "libx264")
            comp_type = "int8"
            dev = "cpu"

        rec_settings = {
            "whisper_model": "small",
            "whisper_device": dev,
            "whisper_compute_type": comp_type,
            "whisper_batch_size": 8,
            "video_encoder": rec_encoder,
            "encoder_preset": "balanced",
            "default_resolution": "1080p",
            "vision_scan_fps": 1.5,
            "hardware_acceleration": "gpu" if dev == "cuda" or rec_encoder != "libx264" else "auto",
        }
        accel_desc = f"{gpu_name} ({vram_gb:.1f} GB VRAM)" if vram_gb > 0 else gpu_name
        summary_reason = (
            f"Detected {accel_desc} with {ram_total_gb:.0f} GB RAM — balanced for Whisper small "
            f"({comp_type}, batch_size=8) and hardware {rec_encoder} exports at 1080p."
        )
    elif (ram_total_gb >= 15.0 and max(cpu_cores, cpu_logical) >= 6) or ram_total_gb > 8.5:
        tier = "standard_cpu"
        tier_label = "Standard Multi-Core CPU"
        rec_encoder = "h264_qsv" if "h264_qsv" in available_encoders else "libx264"
        rec_settings = {
            "whisper_model": "base",
            "whisper_device": "cpu",
            "whisper_compute_type": "int8",
            "whisper_batch_size": 4,
            "video_encoder": rec_encoder,
            "encoder_preset": "veryfast",
            "default_resolution": "720p",
            "vision_scan_fps": 1.2,
            "hardware_acceleration": "cpu" if rec_encoder == "libx264" else "auto",
        }
        summary_reason = (
            f"Detected {cpu_logical}-thread CPU with {ram_total_gb:.1f} GB RAM — tuned for fast CPU int8 "
            f"Whisper base transcription and smooth 720p exports via {rec_encoder}."
        )
    else:
        tier = "lightweight_cpu"
        tier_label = "Lightweight CPU Laptop"
        rec_settings = {
            "whisper_model": "tiny",
            "whisper_device": "cpu",
            "whisper_compute_type": "int8",
            "whisper_batch_size": 2,
            "video_encoder": "libx264",
            "encoder_preset": "ultrafast",
            "default_resolution": "540p",
            "vision_scan_fps": 0.8,
            "hardware_acceleration": "cpu",
        }
        summary_reason = (
            f"Detected {ram_total_gb:.1f} GB RAM CPU-only setup — optimized for lightweight Whisper tiny "
            f"(~75 MB) and low-memory 540p ultrafast encoding."
        )

    rec_model = rec_settings["whisper_model"]
    rec_model_mb = WHISPER_DOWNLOAD_SIZES_MB.get(rec_model, 145)
    whisper_cached = rec_model in cached_models
    vision_ok = bool(packages.get("mediapipe", False) and packages.get("cv2", False))

    downloads_needed = [
        {
            "id": "ffmpeg",
            "name": "FFmpeg & FFprobe Media Engine",
            "description": "Core media decoder, audio extractor, and hardware/CPU MP4 renderer.",
            "size_mb": 85,
            "required": True,
            "installed": ffmpeg_ready,
        },
        {
            "id": "whisper_model",
            "name": f"Whisper Speech Model ({rec_model})",
            "description": f"Offline multilingual voice recognition ({rec_settings['whisper_device']} / {rec_settings['whisper_compute_type']}).",
            "size_mb": rec_model_mb,
            "required": True,
            "installed": whisper_cached,
            "model_id": rec_model,
        },
        {
            "id": "vision_models",
            "name": "Local Computer-Vision Engine (MediaPipe / OpenCV)",
            "description": "Powers webcam detection, smart speaker tracking, and emotion telemetry.",
            "size_mb": 18,
            "required": False,
            "installed": vision_ok,
        },
    ]

    # Preserve configured AI mode if user already saved one
    cur_ai_mode = str(cur.get("ai_mode") or "").strip().lower()
    if cur_ai_mode in ("gemini", "offline"):
        rec_settings["ai_mode"] = cur_ai_mode
    else:
        rec_settings["ai_mode"] = "offline"

    return {
        "tier": tier,
        "tier_label": tier_label,
        "summary_reason": summary_reason,
        "recommended_settings": rec_settings,
        "downloads_needed": downloads_needed,
    }


def _update_install_state(
    *,
    status: Optional[str] = None,
    progress_pct: Optional[int] = None,
    current_step: Optional[str] = None,
    message: Optional[str] = None,
    error: Optional[str] = None,
    ffmpeg_installed: Optional[bool] = None,
    whisper_model_installed: Optional[str] = None,
    log_msg: Optional[str] = None,
) -> None:
    with _INSTALL_LOCK:
        if status is not None:
            _INSTALL_STATE["status"] = status
        if progress_pct is not None:
            _INSTALL_STATE["progress_pct"] = max(0, min(100, int(progress_pct)))
        if current_step is not None:
            _INSTALL_STATE["current_step"] = current_step
        if message is not None:
            _INSTALL_STATE["message"] = message
        if error is not None:
            _INSTALL_STATE["error"] = error
        if ffmpeg_installed is not None:
            _INSTALL_STATE["ffmpeg_installed"] = ffmpeg_installed
        if whisper_model_installed is not None:
            _INSTALL_STATE["whisper_model_installed"] = whisper_model_installed
        _INSTALL_STATE["updated_at"] = _utc_iso()
        if log_msg:
            _INSTALL_STATE.setdefault("logs", []).append({
                "timestamp": _utc_iso(),
                "message": log_msg,
            })


def get_install_status() -> Dict[str, Any]:
    with _INSTALL_LOCK:
        snap = dict(_INSTALL_STATE)
        snap["logs"] = list(_INSTALL_STATE.get("logs") or [])
        return snap


def _download_static_ffmpeg_windows() -> None:
    """Download static gyan.dev FFmpeg essentials zip into backend/bin/ffmpeg.exe + ffprobe.exe."""
    bin_dir = _backend_bin_dir()
    bin_dir.mkdir(parents=True, exist_ok=True)
    ffmpeg_dest = bin_dir / "ffmpeg.exe"
    ffprobe_dest = bin_dir / "ffprobe.exe"

    if ffmpeg_dest.exists() and ffprobe_dest.exists():
        if str(bin_dir) not in os.environ.get("PATH", ""):
            os.environ["PATH"] = f"{bin_dir}{os.pathsep}{os.environ.get('PATH', '')}"
        _update_install_state(
            progress_pct=40,
            current_step="ffmpeg",
            message="FFmpeg binaries verified in backend/bin/",
            ffmpeg_installed=True,
            log_msg="FFmpeg & FFprobe already present in backend/bin/.",
        )
        return

    _update_install_state(
        progress_pct=10,
        current_step="ffmpeg",
        message="Downloading static FFmpeg essentials build (~85 MB)...",
        log_msg=f"Downloading FFmpeg from {STATIC_FFMPEG_WINDOWS_URL}...",
    )

    req = urllib.request.Request(
        STATIC_FFMPEG_WINDOWS_URL,
        headers={"User-Agent": "LostHeavenStudio/1.0"},
    )
    buf = io.BytesIO()
    with urllib.request.urlopen(req, timeout=180) as resp:
        total = int(resp.headers.get("Content-Length") or 0)
        downloaded = 0
        while True:
            chunk = resp.read(256 * 1024)
            if not chunk:
                break
            buf.write(chunk)
            downloaded += len(chunk)
            if total > 0:
                pct = 10 + int((downloaded / total) * 35)
                _update_install_state(
                    progress_pct=min(45, pct),
                    current_step="ffmpeg",
                    message=f"Downloading FFmpeg ({downloaded / (1024 ** 2):.1f} / {total / (1024 ** 2):.1f} MB)...",
                )

    _update_install_state(
        progress_pct=46,
        current_step="ffmpeg",
        message="Extracting ffmpeg.exe & ffprobe.exe into backend/bin/...",
        log_msg="Extracting FFmpeg executables into backend/bin/...",
    )

    buf.seek(0)
    with zipfile.ZipFile(buf) as zf:
        for member in zf.namelist():
            low = member.lower()
            if low.endswith("/bin/ffmpeg.exe") or low == "ffmpeg.exe":
                with zf.open(member) as src, open(ffmpeg_dest, "wb") as dst:
                    shutil.copyfileobj(src, dst)
            elif low.endswith("/bin/ffprobe.exe") or low == "ffprobe.exe":
                with zf.open(member) as src, open(ffprobe_dest, "wb") as dst:
                    shutil.copyfileobj(src, dst)

    if not (ffmpeg_dest.exists() and ffprobe_dest.exists()):
        raise RuntimeError("Downloaded archive did not contain ffmpeg.exe and ffprobe.exe.")

    if str(bin_dir) not in os.environ.get("PATH", ""):
        os.environ["PATH"] = f"{bin_dir}{os.pathsep}{os.environ.get('PATH', '')}"

    _update_install_state(
        progress_pct=50,
        current_step="ffmpeg",
        message="FFmpeg installed successfully.",
        ffmpeg_installed=True,
        log_msg=f"Installed ffmpeg.exe & ffprobe.exe to {bin_dir}.",
    )


def _install_worker(install_ffmpeg: bool, whisper_model: Optional[str]) -> None:
    try:
        if install_ffmpeg:
            if _find_binary_path("ffmpeg") and _find_binary_path("ffprobe"):
                _update_install_state(
                    progress_pct=45,
                    current_step="ffmpeg",
                    message="FFmpeg & FFprobe already installed.",
                    ffmpeg_installed=True,
                    log_msg="Verified FFmpeg & FFprobe on system.",
                )
            elif sys.platform == "win32":
                _download_static_ffmpeg_windows()
            else:
                raise RuntimeError("Automatic FFmpeg download is currently supported on Windows; please install ffmpeg via brew/apt.")

        if whisper_model:
            clean_model = str(whisper_model).strip()
            specs = profile_system_specs()
            rec = build_first_run_recommendations(specs)
            dev = rec["recommended_settings"]["whisper_device"]
            comp = rec["recommended_settings"]["whisper_compute_type"]

            _update_install_state(
                progress_pct=55 if install_ffmpeg else 15,
                current_step="whisper_model",
                message=f"Downloading Whisper speech model ({clean_model})...",
                log_msg=f"Downloading & caching faster-whisper model '{clean_model}'...",
            )

            models_dir = str(Path(settings.STORAGE_DIR) / "models")
            Path(models_dir).mkdir(parents=True, exist_ok=True)

            from faster_whisper import WhisperModel  # type: ignore
            if dev == "cuda":
                try:
                    from app.services.transcriber import register_cuda_dll_directories
                    register_cuda_dll_directories()
                except Exception:
                    pass

            try:
                _update_install_state(
                    progress_pct=80,
                    current_step="whisper_verify",
                    message=f"Downloading & verifying {dev.upper()} inference ({clean_model})...",
                )
                model_obj = WhisperModel(
                    clean_model,
                    device=dev,
                    compute_type=comp,
                    download_root=models_dir,
                )
                del model_obj
                _update_install_state(
                    progress_pct=95,
                    current_step="whisper_verify",
                    message=f"Verified {dev.upper()} ({comp}) inference for '{clean_model}'.",
                    whisper_model_installed=clean_model,
                    log_msg=f"Verified Whisper '{clean_model}' on {dev.upper()} ({comp}).",
                )
            except Exception as gpu_err:
                logger.warning(f"CUDA Whisper verification fell back to CPU int8: {gpu_err}")
                _update_install_state(
                    progress_pct=85,
                    current_step="whisper_verify",
                    message=f"Verifying CPU int8 inference for '{clean_model}'...",
                    log_msg=f"CUDA unavailable/fallback ({str(gpu_err)[:90]}); verifying CPU int8...",
                )
                model_obj = WhisperModel(
                    clean_model,
                    device="cpu",
                    compute_type="int8",
                    download_root=models_dir,
                )
                del model_obj
                _update_install_state(
                    progress_pct=95,
                    current_step="whisper_verify",
                    message=f"Verified CPU int8 inference for '{clean_model}'.",
                    whisper_model_installed=clean_model,
                    log_msg=f"Verified Whisper '{clean_model}' on CPU int8.",
                )

        _update_install_state(
            status="completed",
            progress_pct=100,
            current_step="ready",
            message="Ready — all selected prerequisites verified!",
            log_msg="All requested prerequisites are installed and ready.",
        )
    except Exception as exc:
        logger.error(f"Prerequisite install error: {exc}", exc_info=True)
        _update_install_state(
            status="error",
            current_step="error",
            message=f"Installation failed: {str(exc)}",
            error=str(exc),
            log_msg=f"ERROR: {str(exc)}",
        )


def start_prerequisite_install(
    install_ffmpeg: bool = False,
    whisper_model: Optional[str] = None,
) -> Dict[str, Any]:
    with _INSTALL_LOCK:
        if _INSTALL_STATE.get("status") == "running":
            return get_install_status()

        _INSTALL_STATE.update({
            "status": "running",
            "progress_pct": 5,
            "current_step": "starting",
            "message": "Preparing prerequisite installation...",
            "error": None,
            "logs": [{"timestamp": _utc_iso(), "message": "Started prerequisite setup task."}],
            "started_at": _utc_iso(),
            "updated_at": _utc_iso(),
        })

    t = threading.Thread(
        target=_install_worker,
        args=(bool(install_ffmpeg), whisper_model),
        daemon=True,
    )
    t.start()
    return get_install_status()
