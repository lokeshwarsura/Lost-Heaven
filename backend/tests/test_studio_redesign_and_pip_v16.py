import math
import re
import subprocess
import wave
from pathlib import Path
from typing import List, Tuple

import cv2
import numpy as np
import pytest
from PIL import Image

from app.config import settings
from app.services.audio_analyzer import analyze_audio_track
from app.services.offline_clipper import classify_narrative_arc
from app.services.preview_frame import render_exact_preview_frame
from app.services.proxy import get_ffmpeg_cmd, probe_video
from app.services.reframer import _portrait_pip_safe_xy, build_multi_aspect_filter
from app.services.renderer import render_clip_ultra


def _write_wav(out_path: Path, samples: np.ndarray, sr: int = 16000) -> Path:
    out_path.parent.mkdir(parents=True, exist_ok=True)
    pcm = np.clip(samples * 32767.0, -32767.0, 32767.0).astype("<i2")
    with wave.open(str(out_path), "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(sr)
        wf.writeframes(pcm.tobytes())
    return out_path


def _create_1080p_gameplay_webcam_mp4(out_mp4: Path, duration_sec: float = 2.5) -> Path:
    w, h = 1280, 720
    fps = 8
    n_frames = int(round(duration_sec * fps))
    raw = bytearray()
    for _ in range(n_frames):
        fr = np.zeros((h, w, 3), dtype=np.uint8)
        # Bright teal/amber gameplay background (zero black bars in gameplay body)
        fr[:, :] = (45, 165, 110)
        cv2.rectangle(fr, (int(w * 0.28), int(h * 0.16)), (int(w * 0.72), int(h * 0.84)), (35, 195, 225), -1)
        # Subtle 2% letterbox fringe at very top/bottom to verify vertical fill crop removes bars cleanly
        fr[:12, :] = 0
        fr[-12:, :] = 0
        # Bright magenta webcam box in top_left corner
        cv2.rectangle(fr, (16, 16), (int(w * 0.28), int(h * 0.38)), (225, 45, 210), -1)
        cv2.circle(fr, (int(w * 0.14), int(h * 0.19)), 38, (240, 215, 195), -1)
        raw.extend(fr.tobytes())

    out_mp4.parent.mkdir(parents=True, exist_ok=True)
    proc = subprocess.run(
        [
            get_ffmpeg_cmd(), "-y",
            "-f", "rawvideo", "-vcodec", "rawvideo", "-pix_fmt", "bgr24",
            "-s", f"{w}x{h}", "-r", str(fps), "-i", "-",
            "-f", "lavfi", "-i", f"sine=frequency=190:sample_rate=16000:duration={duration_sec}",
            "-c:v", "libx264", "-preset", "ultrafast", "-pix_fmt", "yuv420p",
            "-c:a", "aac", "-shortest", str(out_mp4),
        ],
        input=bytes(raw),
        capture_output=True,
        timeout=60,
    )
    assert proc.returncode == 0, proc.stderr.decode(errors="ignore")
    return out_mp4


def test_portrait_pip_badge_compact_3_4_card_full_bleed_and_caption_safe_corner(tmp_path: Path):
    src_mp4 = _create_1080p_gameplay_webcam_mp4(tmp_path / "gameplay_pip16.mp4", duration_sec=2.5)
    pure_analysis = {
        "face_detected": False,
        "webcam_detection": {"detected": False},
        "source_webcam_region": {"detected": False, "locked_zone": "top_left"},
        "keyframes": [{"t": 0.0, "cx": 0.50, "cy": 0.50, "action_cx": 0.50, "action_cy": 0.50}],
    }

    # 1. Filtergraph checks at 1080x1920: 26% width (280px), 3:4 portrait height (374px), 20px rounded (r^2=400), 2px border, full-bleed base
    vf = build_multi_aspect_filter(
        aspect_ratio="9:16",
        frame_layout="portrait_pip_badge",
        source_w=1920,
        source_h=1080,
        webcam_corner="top_right",
        source_corner="top_left",
        face_track_data=pure_analysis,
        is_video_render=False,
        caption_y=0.78,
    )
    assert "force_original_aspect_ratio=increase,crop=1080:1920" in vf
    assert ",400),0,alpha" in vf  # 20px rounded corner mask (20^2 = 400)
    m_pad = re.search(r"pad=(\d+):(\d+):2:2:", vf)
    assert m_pad, f"Expected 2px border pad in portrait_pip_badge filter: {vf}"
    card_w, card_h = int(m_pad.group(1)), int(m_pad.group(2))
    pip_w, pip_h = card_w - 4, card_h - 4
    assert pip_w / 1080.0 <= 0.30 and abs((pip_w / 1080.0) - 0.26) < 0.015
    assert abs((pip_w / float(pip_h)) - 0.75) < 0.02

    m_ov = re.search(r"\[bg_s\]\[cam_fg\]overlay=(\d+):(\d+)\[v\]", vf)
    assert m_ov, vf
    ov_x, ov_y = int(m_ov.group(1)), int(m_ov.group(2))
    # Anchored in top_right with 28px safe margin (NOT centered 65% width)
    assert abs((1080 - (ov_x + card_w)) - 28) <= 2
    assert abs(ov_y - 28) <= 2

    # 2. Automatic caption_y avoidance when captions inhabit the requested corner's vertical band
    _, py_bot_clear = _portrait_pip_safe_xy("bottom_right", 1080, 1920, card_w, card_h, caption_y=0.78)
    cap_top_bot = int(round((0.78 - 0.11) * 1920))
    assert (py_bot_clear + card_h) <= cap_top_bot, f"Bottom card {py_bot_clear}..{py_bot_clear + card_h} overlaps caption_y=0.78"

    _, py_top_nudge = _portrait_pip_safe_xy("top_right", 1080, 1920, card_w, card_h, caption_y=0.14)
    cap_bot_top = int(round((0.14 + 0.11) * 1920))
    assert py_top_nudge >= cap_bot_top, f"Top card y={py_top_nudge} overlaps caption_y=0.14 (cap_bot={cap_bot_top})"

    # 3. Preview PNG check: full-bleed top/bottom edges have zero black letterbox bars & top_right shows bright magenta webcam card
    prev = render_exact_preview_frame(
        video_path=str(src_mp4),
        timestamp=0.8,
        clip_start_time=0.0,
        clip_duration=2.5,
        frame_layout="portrait_pip_badge",
        webcam_corner="top_right",
        source_corner="top_left",
        source_webcam_region={"detected": False, "locked_zone": "top_left"},
        burn_captions=False,
        clip_size="480p",
    )
    png_path = Path(settings.STORAGE_DIR) / "previews" / Path(prev["frame_url"]).name
    assert png_path.exists()
    rgb = np.array(Image.open(png_path).convert("RGB"), dtype=np.float32)
    h_p, w_p = rgb.shape[:2]
    assert h_p % 2 == 0 and w_p % 2 == 0
    # Top edge (clear of PiP) and bottom edge must be full-bleed bright gameplay, NOT black letterbox bars
    assert float(rgb[4, : w_p // 2, :].mean()) > 45.0
    assert float(rgb[h_p - 5, :, :].mean()) > 45.0
    # Top-right corner holds magenta camera patch while center-left remains yellow-green gameplay
    tr_patch = rgb[16:90, w_p - 110 : w_p - 20, :]
    mid_patch = rgb[h_p // 2 - 40 : h_p // 2 + 40, w_p // 2 - 40 : w_p // 2 + 40, :]
    assert float(tr_patch[:, :, 0].mean()) > 140.0  # Red channel strong in BGR=(225,45,210)
    assert float(mid_patch[:, :, 1].mean()) > 130.0  # Green channel strong in gameplay center

    # 4. Full MP4 export parity with burn_captions=True & bottom_right corner
    out_mp4 = render_clip_ultra(
        master_video_path=str(src_mp4),
        clip_id=955,
        start_time=0.0,
        duration=2.2,
        words=[{"word": "GG!", "start": 0.4, "end": 1.2}],
        frame_layout="portrait_pip_badge",
        webcam_corner="bottom_right",
        source_webcam_region={"detected": False, "locked_zone": "top_left"},
        burn_captions=True,
        position_y=78.0,
        quality_mode="fast_preview",
        clip_size="480p",
        hardware_mode="cpu",
    )
    meta = probe_video(out_mp4)
    assert meta["width"] % 2 == 0 and meta["height"] % 2 == 0


def test_solo_gameplay_with_explosions_does_not_false_trigger_crosstalk_debate_clash(tmp_path: Path):
    sr = 16000
    dur = 12.0
    n = int(sr * dur)
    t_arr = np.arange(n, dtype=np.float32) / float(sr)

    # Single solo gaming commentator (135 Hz dominant voice + excited inflection around 165 Hz)
    solo_voice = (
        0.50 * np.sin(2.0 * math.pi * 145.0 * t_arr)
        + 0.42 * np.sin(2.0 * math.pi * 435.0 * t_arr)
        + 0.30 * np.sin(2.0 * math.pi * 725.0 * t_arr)
    ).astype(np.float32) * 0.28

    # Loud gaming explosions / sub-bass rumbles (60 Hz - 110 Hz) + gunfire white noise transients
    rng = np.random.default_rng(7)
    sfx = np.zeros(n, dtype=np.float32)
    for boom_t in (2.2, 5.8, 9.4):
        mask = (t_arr >= boom_t) & (t_arr <= boom_t + 0.85)
        boom = (
            0.55 * np.sin(2.0 * math.pi * 65.0 * t_arr[mask])
            + 0.35 * np.sin(2.0 * math.pi * 105.0 * t_arr[mask])
            + rng.normal(0.0, 0.22, size=int(np.sum(mask))).astype(np.float32)
        )
        sfx[mask] = boom

    sig = solo_voice + sfx
    words = [
        {"word": "Watch", "start": 0.4, "end": 0.9},
        {"word": "this", "start": 1.0, "end": 1.5},
        {"word": "push", "start": 1.8, "end": 2.4},
        {"word": "right", "start": 2.6, "end": 3.2},
        {"word": "now", "start": 3.4, "end": 4.0},
        {"word": "oh", "start": 5.5, "end": 6.0},
        {"word": "no", "start": 6.1, "end": 6.6},
        {"word": "what", "start": 7.2, "end": 7.8},
        {"word": "a", "start": 8.0, "end": 8.3},
        {"word": "clutch", "start": 8.6, "end": 9.4},
        {"word": "win!", "start": 9.6, "end": 10.6},
    ]

    wav_path = _write_wav(tmp_path / "solo_gaming_sfx.wav", sig, sr=sr)
    aud = analyze_audio_track(str(wav_path), word_segments=words)
    diar = aud["speaker_diarization"]
    assert diar["crosstalk_count"] == 0, f"False crosstalk on solo gameplay: {diar['crosstalk_overlaps']}"
    assert all(not pt["is_crosstalk"] for pt in aud["timeline"])

    # Verify offline narrative arc classifies as gameplay/action climax, NOT Cross-Talk Debate Clash
    arc = classify_narrative_arc(
        {
            "crosstalk_bins": 4,  # Even if noisy SFX passed a bin threshold
            "speaker_count": diar["speaker_count"],
            "dominance_ratios": {"Speaker A": 0.92, "Speaker B": 0.08},
            "max_turns_15s": 2,
            "shot_scale": "wide_gameplay",
            "graphic_peak": 55.0,
            "dominant_vocal_emotion": "excited_hype",
            "audio_score": 82.0,
            "visual_score": 76.0,
            "dominant_facial_emotion": "neutral",
        },
        "Watch this clutch win!",
        speaker_switch_rate=0.5,
    )
    assert arc["narrative_arc"] != "crosstalk_debate_clash"
    assert arc["arc_label"] == "Gameplay / Action Climax"
