import os
import shutil
import subprocess
import json
import logging
from pathlib import Path
from typing import Dict, Any, Optional

logger = logging.getLogger(__name__)

def get_ffmpeg_cmd() -> str:
    """Return available ffmpeg executable, checking backend/bin, workspace/bin, and system PATH."""
    from app.config import BACKEND_BIN, PROJECT_ROOT
    for base in (BACKEND_BIN, PROJECT_ROOT / "bin"):
        for cand in (base / "ffmpeg.exe", base / "ffmpeg"):
            if cand.exists():
                return str(cand)
    system_ffmpeg = shutil.which("ffmpeg")
    if system_ffmpeg:
        return system_ffmpeg
    return "ffmpeg"

def get_ffprobe_cmd() -> str:
    """Return available ffprobe executable, checking backend/bin, workspace/bin, and system PATH."""
    from app.config import BACKEND_BIN, PROJECT_ROOT
    for base in (BACKEND_BIN, PROJECT_ROOT / "bin"):
        for cand in (base / "ffprobe.exe", base / "ffprobe"):
            if cand.exists():
                return str(cand)
    system_ffprobe = shutil.which("ffprobe")
    if system_ffprobe:
        return system_ffprobe
    return "ffprobe"

def probe_video(video_path: str) -> Dict[str, Any]:
    """Probe video resolution, duration, fps, and audio streams using ffprobe."""
    ffprobe = get_ffprobe_cmd()
    cmd = [
        ffprobe,
        "-v", "quiet",
        "-print_format", "json",
        "-show_format",
        "-show_streams",
        video_path
    ]
    try:
        result = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, check=True)
        data = json.loads(result.stdout)
    except Exception as e:
        logger.error(f"Error probing video {video_path}: {e}")
        return {
            "duration": 0.0,
            "width": 1920,
            "height": 1080,
            "fps": 30.0,
            "has_audio": True
        }

    duration = float(data.get("format", {}).get("duration", 0.0))
    width, height, fps = 1920, 1080, 30.0
    has_audio = False

    for stream in data.get("streams", []):
        if stream.get("codec_type") == "video":
            width = int(stream.get("width", 1920))
            height = int(stream.get("height", 1080))
            fps_str = stream.get("avg_frame_rate", "30/1")
            try:
                if "/" in fps_str:
                    num, den = fps_str.split("/")
                    fps = float(num) / float(den) if float(den) != 0 else 30.0
                else:
                    fps = float(fps_str)
            except Exception:
                fps = 30.0
            if duration == 0.0:
                duration = float(stream.get("duration", 0.0))
        elif stream.get("codec_type") == "audio":
            has_audio = True

    return {
        "duration": duration,
        "width": width,
        "height": height,
        "fps": fps,
        "has_audio": has_audio
    }

def detect_hardware_encoder() -> str:
    """Detect available hardware encoders or fallback to libx264."""
    ffmpeg = get_ffmpeg_cmd()
    try:
        result = subprocess.run([ffmpeg, "-encoders"], stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        output = result.stdout
        if "h264_nvenc" in output:
            return "h264_nvenc"
        if "h264_videotoolbox" in output:
            return "h264_videotoolbox"
    except Exception:
        pass
    return "libx264"

def create_gemini_proxy(source_path: str, output_proxy_path: str) -> str:
    """
    Generate a lightweight 480p @ 1 FPS proxy MP4 for Gemini multimodal analysis.
    Uses hardware-accelerated NVENC when available for ultra-fast transcoding.
    """
    ffmpeg = get_ffmpeg_cmd()
    Path(output_proxy_path).parent.mkdir(parents=True, exist_ok=True)
    encoder = detect_hardware_encoder()

    if encoder == "h264_nvenc":
        cmd = [
            ffmpeg,
            "-y",
            "-i", source_path,
            "-vf", "scale=-2:480,fps=1",
            "-c:v", "h264_nvenc",
            "-preset", "p1",
            "-cq", "32",
            "-c:a", "aac",
            "-b:a", "64k",
            "-ac", "1",
            "-ar", "22050",
            output_proxy_path
        ]
        logger.info(f"Generating NVENC-accelerated proxy: {' '.join(cmd)}")
        res = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        if res.returncode == 0:
            return output_proxy_path
        logger.warning(f"NVENC proxy generation failed (code {res.returncode}). Falling back to libx264...")

    cmd = [
        ffmpeg,
        "-y",
        "-i", source_path,
        "-vf", "scale=-2:480,fps=1",
        "-c:v", "libx264",
        "-preset", "ultrafast",
        "-crf", "32",
        "-c:a", "aac",
        "-b:a", "64k",
        "-ac", "1",
        "-ar", "22050",
        output_proxy_path
    ]

    logger.info(f"Generating CPU ultrafast proxy: {' '.join(cmd)}")
    subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=True)
    return output_proxy_path
