import os
import subprocess
import wave
from pathlib import Path

import cv2
import numpy as np
import pytest
from PIL import Image
from sqlmodel import SQLModel, create_engine, Session

import app.main as main_mod
import app.database as db_mod
from app.config import settings
from app.models import Project, ViralClip, UpdateClipRequest, PreviewFrameRequest
from app.services.filters import COLOR_PRESETS, build_advanced_color_filter
from app.services.audio_mastering import build_voice_filter_chain
from app.services.renderer import render_clip_ultra
from app.services.preview_frame import render_exact_preview_frame
from app.services.proxy import get_ffmpeg_cmd, probe_video


def _setup_isolated_db(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    storage_dir = tmp_path / "storage"
    for sub in ("masters", "proxies", "renders", "previews", "captions", "projects", "fonts", "luts", "assets"):
        (storage_dir / sub).mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr(settings, "STORAGE_DIR", str(storage_dir))

    db_path = tmp_path / "v7_suite.db"
    test_engine = create_engine(f"sqlite:///{db_path}", echo=False, connect_args={"check_same_thread": False})
    SQLModel.metadata.create_all(test_engine)
    monkeypatch.setattr(db_mod, "engine", test_engine)
    return test_engine, storage_dir


def _make_cinema_test_mp4(out_path: Path, duration_sec: float = 4.0) -> Path:
    """Generate a high-contrast 4s source clip with speech-like harmonic + sibilant 6.5kHz energy."""
    ffmpeg = get_ffmpeg_cmd()
    out_path.parent.mkdir(parents=True, exist_ok=True)
    # Mix 300Hz vocal fundamental + strong 6.5kHz sibilance so de-esser & EQ clearly act on spectrum
    a_expr = "0.28*sin(2*PI*300*t)+0.22*sin(2*PI*6500*t)"
    vf = (
        "drawbox=x=100:y=60:w=440:h=240:color=#D97706@1.0:t=fill,"
        "drawbox=x=220:y=110:w=200:h=140:color=white@1.0:t=fill"
    )
    res = subprocess.run(
        [
            ffmpeg, "-y",
            "-f", "lavfi", "-i", f"color=c=#1F2937:size=640x360:rate=24:duration={duration_sec:.2f}",
            "-f", "lavfi", "-i", f"aevalsrc='{a_expr}':s=48000:d={duration_sec:.2f}",
            "-vf", vf,
            "-c:v", "libx264", "-preset", "ultrafast", "-pix_fmt", "yuv420p",
            "-c:a", "aac", "-ar", "48000", "-ac", "1",
            str(out_path),
        ],
        capture_output=True, text=True, timeout=45,
    )
    assert res.returncode == 0, res.stderr
    return out_path


def _write_test_3dlut_cube(path: Path) -> Path:
    """Create a valid 2x2x2 Adobe .cube 3D LUT that swaps R and B channels dramatically."""
    lines = [
        'TITLE "LostHeaven Test TealSwap LUT"',
        "LUT_3D_SIZE 2",
        "0.0 0.0 0.0",
        "0.0 0.0 1.0",
        "0.0 1.0 0.0",
        "0.0 1.0 1.0",
        "1.0 0.0 0.0",
        "1.0 0.0 1.0",
        "1.0 1.0 0.0",
        "1.0 1.0 1.0",
    ]
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return path


def test_cinema_presets_halation_sliders_chroma_and_letterbox_preview_and_export(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    _setup_isolated_db(tmp_path, monkeypatch)
    src = _make_cinema_test_mp4(tmp_path / "media" / "cinema_src.mp4", duration_sec=3.5)

    # All 10 pro grade presets exist cleanly in COLOR_PRESETS
    pro_keys = [
        "kodak_portra_400", "teal_orange", "cyberpunk_neon", "moody_dark",
        "crisp_daylight", "bw_16mm_noir", "golden_hour", "vhs_retro",
        "clean_rec709", "high_contrast_gaming",
    ]
    for pk in pro_keys:
        assert pk in COLOR_PRESETS and COLOR_PRESETS[pk]["filter"]

    # Halation + Chroma + Temp + Vibrance + Matte Fade + 2.39:1 Letterbox preview vs neutral
    prev_neutral = render_exact_preview_frame(
        video_path=str(src), timestamp=1.0, aspect_ratio="16:9", burn_captions=False,
        color_grading={"preset": "none"},
    )
    prev_graded = render_exact_preview_frame(
        video_path=str(src), timestamp=1.0, aspect_ratio="16:9", burn_captions=False,
        color_grading={
            "preset": "teal_orange",
            "color_temp": 18,
            "tint_shift": -10,
            "vibrance": 45,
            "matte_fade": 35,
            "halation_glow": 65,
            "chroma_split": 0.65,
            "letterbox": "2.39:1",
        },
    )
    img_neutral = cv2.imdecode(np.fromfile(str(Path(settings.STORAGE_DIR) / "previews" / Path(prev_neutral["frame_url"]).name), dtype=np.uint8), cv2.IMREAD_COLOR)
    img_graded = cv2.imdecode(np.fromfile(str(Path(settings.STORAGE_DIR) / "previews" / Path(prev_graded["frame_url"]).name), dtype=np.uint8), cv2.IMREAD_COLOR)
    assert img_neutral is not None and img_graded is not None
    assert float(np.mean(np.abs(img_neutral.astype(float) - img_graded.astype(float)))) > 12.0
    # Top and bottom 2.39:1 letterbox rows are pure matte black
    assert float(np.mean(img_graded[:15, :, :])) < 5.0
    assert float(np.mean(img_graded[-15:, :, :])) < 5.0

    # Full MP4 render with cinema suite succeeds cleanly
    out_mp4 = render_clip_ultra(
        master_video_path=str(src), clip_id=701, project_id=701,
        start_time=0.0, duration=3.5, words=[], aspect_ratio="16:9",
        burn_captions=False, color_preset="kodak_portra_400",
        options={
            "color_temp": 14, "tint_shift": 5, "vibrance": 25,
            "matte_fade": 20, "halation_glow": 50, "chroma_split": 0.5, "letterbox": "2.39:1",
        },
        quality_mode="fast_preview", hardware_mode="cpu",
    )
    assert os.path.exists(out_mp4)
    assert probe_video(out_mp4)["duration"] >= 3.0


def test_custom_cube_lut_speed_ramp_broadcast_audio_and_branded_outro(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    test_engine, storage_dir = _setup_isolated_db(tmp_path, monkeypatch)
    src = _make_cinema_test_mp4(tmp_path / "media" / "graded_spaced path.mp4", duration_sec=4.0)
    lut_file = _write_test_3dlut_cube(storage_dir / "luts" / "teal_swap_custom.cube")

    # Create Brand Logo PNG in storage/assets
    logo_path = storage_dir / "assets" / "brand_logo_test.png"
    Image.new("RGBA", (96, 96), (198, 243, 47, 255)).save(logo_path, format="PNG")

    luts_listed = main_mod.list_luts()
    assert any(item["filename"] == "teal_swap_custom.cube" for item in luts_listed)

    # Verify Broadcast Audio EQ Presets, De-Esser & Loudness Targets filter syntax
    ch_warm = build_voice_filter_chain(voice_eq_preset="Podcast Warmth", de_esser=True, loudness_target="-16 LUFS")
    ch_loud = build_voice_filter_chain(voice_eq_preset="Crisp Broadcast", de_esser=True, loudness_target="-11 LUFS", speed_ramp=1.12)
    assert "equalizer=f=135" in ch_warm and "equalizer=f=6500" in ch_warm and "loudnorm=I=-16" in ch_warm
    assert "equalizer=f=3200" in ch_loud and "loudnorm=I=-11" in ch_loud and "atempo=1.1200" in ch_loud

    words = [{"word": "Testing", "start": 0.2, "end": 0.8}, {"word": "Broadcast", "start": 0.9, "end": 1.6}]
    with Session(test_engine) as session:
        proj = Project(title="Phase3 Full", source_type="upload", source_path=str(src), duration=4.0, status="finished")
        session.add(proj); session.commit(); session.refresh(proj)
        clip = ViralClip(project_id=proj.id, start_time=0.0, end_time=4.0, duration=4.0)
        session.add(clip); session.commit(); session.refresh(clip)

        main_mod.update_clip(
            clip.id,
            UpdateClipRequest(
                lut_path=str(lut_file),
                voice_eq_preset="Crisp Broadcast",
                de_esser=True,
                loudness_target="-11 LUFS",
                speed_ramp=1.12,
                outro_cta_enabled=True,
                outro_cta_headline="WATCH PART 2",
                outro_cta_handle="@LostHeaven",
                outro_cta_text="Follow Now",
                outro_logo_path=str(logo_path),
                transcript_words=words,
            ),
            session=session,
        )
        session.refresh(clip)
        snap = main_mod._snapshot_clip(clip)
        assert snap["options"]["lut_path"] == str(lut_file)
        assert snap["options"]["voice_eq_preset"] == "Crisp Broadcast"
        assert snap["options"]["loudness_target"] == "-11 LUFS"
        assert snap["options"]["speed_ramp"] == pytest.approx(1.12)
        assert snap["options"]["outro_cta_headline"] == "WATCH PART 2"

        # 1.12x Speed Ramp shortens 4.0s clip noticeably (~3.57s) while keeping A/V synced & LUT + Branded Outro intact
        out_fast = main_mod._render_clip_from_snapshot(snap, str(src), quality_mode="fast_preview")
        assert os.path.exists(out_fast)
        meta_fast = probe_video(out_fast)
        assert meta_fast["has_audio"] is True
        assert 3.2 <= meta_fast["duration"] <= 3.78, f"Expected 1.12x speed ramp duration ~3.57s, got {meta_fast['duration']}"

        # Compare Branded 2s End-Card Outro frame against t=0.5s frame in rendered MP4 (outro visibly composited)
        cap = cv2.VideoCapture(out_fast)
        cap.set(cv2.CAP_PROP_POS_MSEC, 500)
        _, f_early = cap.read()
        cap.set(cv2.CAP_PROP_POS_MSEC, 2800)
        _, f_end = cap.read()
        cap.release()
        assert f_early is not None and f_end is not None
        assert float(np.mean(np.abs(f_early.astype(float) - f_end.astype(float)))) > 6.0

    # Verify De-Esser actually attenuates 6.5kHz energy vs Raw
    out_deess = render_clip_ultra(
        master_video_path=str(src), clip_id=702, project_id=702,
        start_time=0.0, duration=3.0, words=[], burn_captions=False,
        options={"voice_eq_preset": "Raw", "de_esser": True, "loudness_target": "-16 LUFS"},
        normalize_audio=True, quality_mode="fast_preview", hardware_mode="cpu",
    )
    out_raw = render_clip_ultra(
        master_video_path=str(src), clip_id=703, project_id=703,
        start_time=0.0, duration=3.0, words=[], burn_captions=False,
        options={"voice_eq_preset": "Raw", "de_esser": False, "loudness_target": "-16 LUFS"},
        normalize_audio=True, quality_mode="fast_preview", hardware_mode="cpu",
    )
    ffmpeg = get_ffmpeg_cmd()
    w1, w2 = tmp_path / "deess.wav", tmp_path / "raw.wav"
    subprocess.run([ffmpeg, "-y", "-i", out_deess, "-vn", "-c:a", "pcm_s16le", str(w1)], check=True, capture_output=True)
    subprocess.run([ffmpeg, "-y", "-i", out_raw, "-vn", "-c:a", "pcm_s16le", str(w2)], check=True, capture_output=True)
    with wave.open(str(w1), "rb") as f1:
        ch1, sr1 = f1.getnchannels(), f1.getframerate()
        a1 = np.frombuffer(f1.readframes(f1.getnframes()), dtype=np.int16).astype(np.float32) / 32768.0
        if ch1 > 1:
            a1 = a1.reshape(-1, ch1).mean(axis=1)
    with wave.open(str(w2), "rb") as f2:
        ch2, sr2 = f2.getnchannels(), f2.getframerate()
        a2 = np.frombuffer(f2.readframes(f2.getnframes()), dtype=np.int16).astype(np.float32) / 32768.0
        if ch2 > 1:
            a2 = a2.reshape(-1, ch2).mean(axis=1)
    # Compare relative 6.5kHz sibilance vs 300Hz vocal fundamental (invariant to overall loudnorm gain)
    n_fft = min(len(a1), len(a2), sr1)
    fft1 = np.abs(np.fft.rfft(a1[:n_fft]))
    fft2 = np.abs(np.fft.rfft(a2[:n_fft]))
    freqs = np.fft.rfftfreq(n_fft, 1.0 / float(sr1))
    mask_6k5 = (freqs >= 6200) & (freqs <= 6800)
    mask_300 = (freqs >= 250) & (freqs <= 350)
    ratio_deess = float(np.max(fft1[mask_6k5])) / max(1e-6, float(np.max(fft1[mask_300])))
    ratio_raw = float(np.max(fft2[mask_6k5])) / max(1e-6, float(np.max(fft2[mask_300])))
    assert ratio_deess < ratio_raw * 0.70, f"Expected de-esser 6.5kHz/300Hz ratio to drop sharply ({ratio_deess} vs {ratio_raw})"
