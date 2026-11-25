import os
import pytest
from pathlib import Path
from app.services.proxy import get_ffmpeg_cmd
from app.services.transcriber import extract_audio_segment, transcribe_clip, filter_filler_words
from app.services.diagnostics import get_system_check, get_machine_specs
from app.services.job_manager import JobManager
from app.services.preview_frame import render_exact_preview_frame

def test_system_check_and_machine_specs():
    check = get_system_check()
    assert "checks" in check
    assert len(check["checks"]) == 8
    assert check.get("all_passed") is True or check.get("all_ok") is True

    specs = get_machine_specs()
    assert "cpu" in specs
    assert specs["cpu"]["cores_logical"] >= 1
    assert "ram" in specs
    assert "gpu" in specs
    assert "binaries" in specs
    assert "ffmpeg" in specs["binaries"]

def test_extract_audio_and_transcribe_voice():
    # Find any sample video in storage/masters
    masters = list(Path("storage/masters").glob("*.mp4"))
    if not masters:
        pytest.skip("No video in storage/masters to run audio extraction test")

    video_path = str(masters[0])
    output_wav = "storage/temp/test_speech.wav"
    os.makedirs("storage/temp", exist_ok=True)

    try:
        # 1. Real audio extraction via FFmpeg
        wav_path = extract_audio_segment(video_path, start_time=1.0, duration=4.0, output_wav_path=output_wav)
        assert os.path.exists(wav_path)
        assert os.path.getsize(wav_path) > 1000 # Valid WAV header + PCM

        # 2. Real faster-whisper voice transcription
        words = transcribe_clip(video_path, start_time=1.0, duration=4.0, language="English")
        assert isinstance(words, list)
        for w in words:
            assert "word" in w
            assert "start" in w
            assert "end" in w
            assert w["end"] >= w["start"]
            # Verify no mock / dummy phrases
            assert "MOST VIRAL ENGAGEMENT" not in w["word"]
    finally:
        if os.path.exists(output_wav):
            try:
                os.remove(output_wav)
            except Exception:
                pass

def test_preview_frame_generation_with_captions():
    masters = list(Path("storage/masters").glob("*.mp4"))
    if not masters:
        pytest.skip("No video in storage/masters for preview test")

    video_path = str(masters[0])
    sample_words = [
        {"word": "Real", "start": 0.1, "end": 0.5, "is_filler": False, "deleted": False},
        {"word": "voice", "start": 0.6, "end": 1.0, "is_filler": False, "deleted": False},
        {"word": "recognition", "start": 1.1, "end": 1.8, "is_filler": False, "deleted": False}
    ]

    res = render_exact_preview_frame(
        video_path=video_path,
        timestamp=2.0,
        aspect_ratio="9:16",
        burn_captions=True,
        caption_options={
            "caption_style": "Karaoke pop",
            "caption_font": "Montserrat",
            "caption_animation": "Karaoke",
            "base_color": "#FFFFFF",
            "active_color": "#C6F32F",
            "position_x": 50.0,
            "position_y": 78.0,
            "outline_width": 4.0,
            "shadow_depth": 3.0,
            "font_size_percent": 100
        },
        transcript_words=sample_words
    )

    assert "frame_url" in res
    assert res["frame_url"].endswith(".png")
    assert res["render_time_ms"] > 0
