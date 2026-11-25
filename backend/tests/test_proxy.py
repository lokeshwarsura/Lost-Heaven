import os
import subprocess
import tempfile
import pytest
from app.services.proxy import get_ffmpeg_cmd, get_ffprobe_cmd, probe_video, create_gemini_proxy

def create_synthetic_test_video(output_mp4: str, duration: int = 4):
    """Generate a clean synthetic MP4 with audio using FFmpeg testsrc."""
    ffmpeg = get_ffmpeg_cmd()
    cmd = [
        ffmpeg,
        "-y",
        "-f", "lavfi",
        "-i", f"testsrc=duration={duration}:size=1280x720:rate=30",
        "-f", "lavfi",
        "-i", f"sine=frequency=1000:duration={duration}",
        "-c:v", "libx264",
        "-preset", "ultrafast",
        "-c:a", "aac",
        "-b:a", "64k",
        output_mp4
    ]
    subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=True)

def test_ffmpeg_probe_and_proxy_creation():
    with tempfile.TemporaryDirectory() as tmpdir:
        test_video = os.path.join(tmpdir, "master_test.mp4")
        proxy_video = os.path.join(tmpdir, "proxy_test.mp4")

        # 1. Create synthetic video
        create_synthetic_test_video(test_video, duration=3)
        assert os.path.exists(test_video)

        # 2. Probe video metadata
        meta = probe_video(test_video)
        assert meta["width"] == 1280
        assert meta["height"] == 720
        assert meta["duration"] >= 2.9
        assert meta["has_audio"] is True

        # 3. Create Gemini 480p @ 1 FPS proxy
        proxy_result = create_gemini_proxy(test_video, proxy_video)
        assert os.path.exists(proxy_result)

        # 4. Probe proxy metadata to verify 480p and 1 FPS
        proxy_meta = probe_video(proxy_result)
        # Height should be 480
        assert proxy_meta["height"] == 480
        # Fps should be 1.0 (or close to 1)
        assert abs(proxy_meta["fps"] - 1.0) < 0.2
        # File size should be small
        assert os.path.getsize(proxy_result) > 1000
