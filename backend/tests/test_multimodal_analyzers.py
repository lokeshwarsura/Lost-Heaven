import math
import subprocess
import wave
from pathlib import Path
from typing import List, Tuple

import cv2
import numpy as np

from app.services.audio_analyzer import analyze_audio_track
from app.services.proxy import get_ffmpeg_cmd
from app.services.video_analyzer import analyze_video_track
from tests.test_unified_reframer import _draw_portrait


def _write_synthetic_wav_with_burst(out_wav: Path) -> Path:
    """Create a 4.0s 16kHz mono WAV: 0..1.5s quiet, 1.5..2.8s loud 440Hz+880Hz speech-like burst, 2.8..4.0s quiet."""
    sr = 16000
    dur = 4.0
    t = np.linspace(0.0, dur, int(sr * dur), endpoint=False, dtype=np.float32)

    signal = np.random.default_rng(42).normal(0.0, 0.0002, size=len(t)).astype(np.float32)
    burst_mask = (t >= 1.5) & (t <= 2.8)
    burst_wave = (
        0.48 * np.sin(2.0 * math.pi * 220.0 * t)
        + 0.28 * np.sin(2.0 * math.pi * 660.0 * t)
        + 0.14 * np.sin(2.0 * math.pi * 1320.0 * t)
    ).astype(np.float32)
    signal[burst_mask] = burst_wave[burst_mask]

    pcm = np.clip(signal * 32767.0, -32767.0, 32767.0).astype(np.int16)
    out_wav.parent.mkdir(parents=True, exist_ok=True)
    with wave.open(str(out_wav), "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(sr)
        wf.writeframes(pcm.tobytes())
    return out_wav


def _encode_sequence_mp4(out_mp4: Path, clips: List[Tuple[np.ndarray, float]], fps: int = 8) -> Path:
    """Encode exact synthetic segments to H.264 MP4 via ffconcat."""
    tmp_dir = out_mp4.parent / f"vseq_{out_mp4.stem}"
    tmp_dir.mkdir(parents=True, exist_ok=True)
    lines = ["ffconcat version 1.0"]
    last = None
    total_dur = 0.0
    for idx, (frame, dur) in enumerate(clips):
        p = tmp_dir / f"f_{idx}.png"
        cv2.imwrite(str(p), frame)
        lines.append(f"file '{p.name}'")
        lines.append(f"duration {dur:.3f}")
        last = p
        total_dur += dur
    if last is not None:
        lines.append(f"file '{last.name}'")
    c_file = tmp_dir / "seq.ffconcat"
    c_file.write_text("\n".join(lines), encoding="utf-8")

    ffmpeg = get_ffmpeg_cmd()
    out_mp4.parent.mkdir(parents=True, exist_ok=True)
    cmd = [
        ffmpeg, "-y", "-hide_banner",
        "-f", "concat", "-safe", "0", "-i", c_file.name,
        "-f", "lavfi", "-i", f"sine=f=440:sample_rate=44100:d={total_dur:.3f}",
        "-vf", f"fps={fps}",
        "-c:v", "libx264", "-preset", "ultrafast", "-pix_fmt", "yuv420p",
        "-c:a", "aac", "-shortest",
        str(out_mp4.resolve()),
    ]
    res = subprocess.run(cmd, cwd=str(tmp_dir), capture_output=True, text=True, timeout=60)
    assert res.returncode == 0, res.stderr
    return out_mp4


def test_analyze_audio_track_burst_lufs_wpm_and_silence(tmp_path: Path):
    wav_path = _write_synthetic_wav_with_burst(tmp_path / "burst_test.wav")
    words = [
        {"word": "Wait", "start": 1.52, "end": 1.72},
        {"word": "for", "start": 1.75, "end": 1.92},
        {"word": "this", "start": 1.95, "end": 2.15},
        {"word": "secret!", "start": 2.18, "end": 2.68},
    ]
    res = analyze_audio_track(str(wav_path), word_segments=words)

    summary = res["summary"]
    timeline = res["timeline"]
    assert summary["integrated_lufs"] > -35.0
    assert summary["lra"] >= 0.0
    assert summary["true_peak_db"] > -12.0
    assert summary["avg_wpm"] > 80.0
    assert len(timeline) >= 15  # ~16 buckets of 0.25s across 4.0s

    quiet_bins = [pt for pt in timeline if pt["t"] <= 1.0 or pt["t"] >= 3.2]
    burst_bins = [pt for pt in timeline if 1.75 <= pt["t"] <= 2.50]
    assert quiet_bins and burst_bins

    avg_quiet_exc = sum(pt["excitement_score"] for pt in quiet_bins) / len(quiet_bins)
    avg_burst_exc = sum(pt["excitement_score"] for pt in burst_bins) / len(burst_bins)
    assert avg_burst_exc > avg_quiet_exc + 25.0
    assert max(pt["lufs_m"] for pt in burst_bins) > max(pt["lufs_m"] for pt in quiet_bins) + 12.0
    assert any(1.5 <= ts <= 2.75 for ts in summary["peak_excitement_timestamps"])
    assert len(summary["silence_intervals"]) >= 1
    assert any(1.5 <= ht <= 2.75 for ht in summary["hook_inflection_timestamps"])


def test_analyze_video_track_cuts_sharpness_exposure_action_and_face(tmp_path: Path):
    # Segment 1 (0.0..1.0s): Crushed dark black frame -> low exposure
    dark_frame = np.full((720, 1280, 3), 4, dtype=np.uint8)

    # Segment 2 (1.0..2.2s): Well-lit sharp studio portrait with visible eyes/face + textured room lines
    sharp_frame = np.full((720, 1280, 3), (75, 85, 110), dtype=np.uint8)
    for gx in range(80, 1200, 90):
        cv2.line(sharp_frame, (gx, 40), (gx, 680), (140, 155, 185), 2)
    _draw_portrait(sharp_frame, 640, 340, scale=1.35, shirt_bgr=(50, 90, 195))

    # Segment 3 (2.2..3.4s): Identical portrait severely defocused via Gaussian blur
    blurry_frame = cv2.GaussianBlur(sharp_frame, (31, 31), sigmaX=12.0)

    # Segment 4 (3.4..4.4s): Moving object sequence (action) + blown-out white flash at end
    move_a = np.full((720, 1280, 3), (200, 180, 35), dtype=np.uint8)
    cv2.rectangle(move_a, (160, 180), (500, 520), (35, 45, 220), -1)
    move_b = np.full((720, 1280, 3), (200, 180, 35), dtype=np.uint8)
    cv2.rectangle(move_b, (680, 180), (1020, 520), (35, 45, 220), -1)
    flash_frame = np.full((720, 1280, 3), 252, dtype=np.uint8)

    mp4_path = _encode_sequence_mp4(
        tmp_path / "vision_test.mp4",
        [(dark_frame, 1.0), (sharp_frame, 1.2), (blurry_frame, 1.2), (move_a, 0.5), (move_b, 0.5), (flash_frame, 0.6)],
        fps=8,
    )

    res = analyze_video_track(str(mp4_path), sample_fps=4.0)
    frames = res["frames"]
    assert res["summary"]["frame_count"] >= 16
    assert len(res["scene_cuts"]) >= 1
    assert len(res["black_intervals"]) >= 1

    dark_bins = [f for f in frames if 0.1 <= f["t"] <= 0.75]
    sharp_bins = [f for f in frames if 1.25 <= f["t"] <= 1.95]
    blur_bins = [f for f in frames if 2.45 <= f["t"] <= 3.15]
    action_bins = [f for f in frames if 3.55 <= f["t"] <= 4.25]
    flash_bins = [f for f in frames if f["t"] >= 4.55]

    assert dark_bins and sharp_bins and blur_bins and action_bins and flash_bins
    assert all(f["exposure_score"] < 30.0 for f in dark_bins)
    assert all(f["exposure_score"] < 30.0 for f in flash_bins)
    assert max(f["exposure_score"] for f in sharp_bins) >= 65.0
    assert max(f["sharpness_score"] for f in sharp_bins) >= 55.0
    assert max(f["sharpness_score"] for f in blur_bins) < 35.0
    assert max(f["action_score"] for f in action_bins) >= 25.0
    assert any(f["face_count"] >= 1 for f in sharp_bins)
    assert any(f["eyes_open_confidence"] >= 0.70 for f in sharp_bins if f["face_count"] > 0)
