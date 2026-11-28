import os
import subprocess
from pathlib import Path

import cv2
import numpy as np
import pytest
from PIL import Image
from sqlmodel import Session, SQLModel, create_engine

import app.database as app_db
import app.main as main_mod
from app.config import settings
from app.models import Project, ViralClip
from app.services.caption_renderer import render_caption_frame
from app.services.face_tracker import detect_webcam_corner_in_frame
from app.services.preview_frame import render_exact_preview_frame
from app.services.proxy import get_ffmpeg_cmd, probe_video
from app.services.reframer import build_multi_aspect_filter
from app.services.renderer import render_clip_ultra
from app.services.trailer_compiler import compile_project_trailer


def _setup_db(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    storage_dir = tmp_path / "storage"
    for sub in ("renders", "masters", "previews", "assets", "thumbnails", "models"):
        (storage_dir / sub).mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr(settings, "STORAGE_DIR", str(storage_dir))
    monkeypatch.setattr(settings, "GEMINI_API_KEY", "")
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    monkeypatch.setattr(main_mod, "get_persisted_settings", lambda: {})

    db_path = tmp_path / "fixes_verify.db"
    test_engine = create_engine(
        f"sqlite:///{db_path}",
        connect_args={"check_same_thread": False},
    )
    SQLModel.metadata.create_all(test_engine)
    monkeypatch.setattr(app_db, "engine", test_engine)
    return test_engine, storage_dir


def test_caption_autofit_wraps_and_stays_within_safe_width():
    text = "MYTH CONFIRMED IN GTA 6"
    words = [
        {"word": tok, "start": round(i * 0.25, 2), "end": round((i + 1) * 0.25, 2)}
        for i, tok in enumerate(text.split())
    ]

    for width, height in ((1080, 1920), (540, 960)):
        for style, pct in (("Karaoke pop", 150), ("Punch Box", 160), ("Brand Block", 145)):
            img = render_caption_frame(
                t=0.30,
                width=width,
                height=height,
                words=words,
                burn_captions=True,
                style_preset=style,
                font_size_pct=pct,
                position_grid="bottom_center",
                pos_x=50.0,
                pos_y=74.0,
            )
            bbox = img.getbbox()
            assert bbox is not None, f"Caption rendered blank for {style} {width}x{height}"
            x0, y0, x1, y1 = bbox
            bbox_w = x1 - x0
            # Rendered caption width fits inside 0.85 * frame_width with safe horizontal margins
            assert bbox_w <= int(round(width * 0.85)), f"Overflow {bbox_w} > {0.85 * width} ({style} {width})"
            assert x0 >= int(round(width * 0.06)), f"Left spill x0={x0} ({style} {width})"
            assert x1 <= int(round(width * 0.94)), f"Right spill x1={x1} ({style} {width})"
            # Lower safe vertical band below hook banner and above platform footer
            assert y0 >= int(round(height * 0.50))
            assert y1 <= int(round(height * 0.90))


def test_single_hook_banner_drawn_without_duplication(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    _setup_db(tmp_path, monkeypatch)

    # Render caption frame with hook banner enabled and no active words in caption zone
    img = render_caption_frame(
        t=0.5,
        width=540,
        height=960,
        words=[{"word": "Later", "start": 4.2, "end": 4.8}],
        burn_captions=True,
        hook_banner_enabled=True,
        hook_headline="MYTH CONFIRMED IN GTA 6",
        clip_duration=6.0,
    )
    arr = np.array(img)
    alpha = arr[:, :, 3]
    top_band = alpha[: int(960 * 0.35), :]
    row_has_pixels = np.any(top_band > 20, axis=1)

    # Count contiguous vertical blocks in the top third -> strictly 1 hook banner box!
    blocks = 0
    in_block = False
    for flag in row_has_pixels:
        if flag and not in_block:
            blocks += 1
            in_block = True
        elif not flag and in_block:
            in_block = False
    assert blocks == 1, f"Expected exactly 1 top hook banner block, found {blocks}"

    # Verify trailer_compiler preserves a clean single hook headline without duplicating text
    c1 = ViralClip(
        project_id=1,
        title="Clip 1",
        hook_headline="MYTH CONFIRMED",
        start_time=0.0,
        end_time=4.0,
        duration=4.0,
        virality_score=92,
        hook_score=90,
    )
    c2 = ViralClip(
        project_id=1,
        title="Clip 2",
        hook_headline="SECOND CLIP",
        start_time=5.0,
        end_time=9.0,
        duration=4.0,
        virality_score=85,
        hook_score=82,
    )
    trailer = compile_project_trailer([c1, c2], project_id=1)
    assert trailer.show_hook_banner is True
    assert trailer.hook_headline == "SUPERCUT TRAILER: BEST MOMENTS"


def _create_1080p_gameplay_with_corner_webcam(out_path: Path, corner: str = "bottom_left", duration: float = 2.5) -> Path:
    """
    Create a 1920x1080 synthetic gameplay MP4 (teal/blue background, no skin) with a sharp
    Haar/MediaPipe-detectable studio portrait webcam card inside the target corner.
    """
    out_path.parent.mkdir(parents=True, exist_ok=True)
    ffmpeg = get_ffmpeg_cmd()
    cw, ch = 460, 340
    if corner == "top_left":
        x, y = 20, 20
    elif corner == "top_right":
        x, y = 1920 - cw - 20, 20
    elif corner == "bottom_right":
        x, y = 1920 - cw - 20, 1080 - ch - 20
    else:  # bottom_left
        x, y = 20, 1080 - ch - 20

    # Background is pure sky/teal gameplay (BGR ~(120, 85, 22), RGB ~(22, 85, 120) -> zero skin false positives).
    canvas = np.full((1080, 1920, 3), (120, 85, 22), dtype=np.uint8)
    roi = np.full((240, 320, 3), (144, 180, 232), dtype=np.uint8)  # warm backdrop in cam
    cv2.ellipse(roi, (160, 115), (52, 68), 0, 0, 360, (160, 195, 232), -1)
    cv2.ellipse(roi, (140, 100), (11, 6), 0, 0, 360, (245, 245, 245), -1)
    cv2.ellipse(roi, (180, 100), (11, 6), 0, 0, 360, (245, 245, 245), -1)
    cv2.circle(roi, (140, 100), 5, (35, 30, 25), -1)
    cv2.circle(roi, (180, 100), 5, (35, 30, 25), -1)
    cv2.line(roi, (126, 87), (152, 85), (45, 35, 30), 3)
    cv2.line(roi, (168, 85), (194, 87), (45, 35, 30), 3)
    cv2.line(roi, (160, 102), (156, 128), (130, 150, 185), 3)
    cv2.ellipse(roi, (160, 146), (18, 7), 0, 0, 180, (80, 75, 140), 3)
    cv2.rectangle(roi, (105, 178), (215, 240), (60, 70, 90), -1)

    roi_resized = cv2.resize(roi, (cw, ch), interpolation=cv2.INTER_AREA)
    canvas[y:y + ch, x:x + cw] = roi_resized
    cv2.rectangle(canvas, (x, y), (x + cw, y + ch), (245, 245, 250), 3)

    png_path = out_path.with_suffix(".png")
    cv2.imwrite(str(png_path), canvas)

    cmd = [
        ffmpeg, "-y",
        "-loop", "1", "-i", str(png_path),
        "-f", "lavfi", "-i", f"sine=frequency=440:sample_rate=44100:d={duration}",
        "-t", str(duration), "-r", "15",
        "-c:v", "libx264", "-preset", "ultrafast", "-pix_fmt", "yuv420p",
        "-c:a", "aac", "-shortest",
        str(out_path),
    ]
    res = subprocess.run(cmd, capture_output=True, text=True, timeout=60)
    try:
        png_path.unlink(missing_ok=True)
    except Exception:
        pass
    assert res.returncode == 0, res.stderr
    return out_path


def test_gameplay_webcam_pip_detects_corner_and_composites_rounded_pip(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
):
    test_engine, storage_dir = _setup_db(tmp_path, monkeypatch)
    src_mp4 = _create_1080p_gameplay_with_corner_webcam(
        tmp_path / "media" / "stream_bl.mp4", corner="bottom_left", duration=2.2
    )

    # 1. Verify 4-corner frame detector accurately identifies bottom_left webcam
    cap = cv2.VideoCapture(str(src_mp4))
    ok, frame = cap.read()
    cap.release()
    assert ok and frame is not None
    det = detect_webcam_corner_in_frame(frame)
    assert det["has_webcam"] is True
    assert det["corner"] == "bottom_left"
    assert det["norm_box"] is not None

    # 2. Pure gameplay frame with zero facecam still composites deterministic PiP corner region
    pure_game = np.full((360, 640, 3), (120, 85, 22), dtype=np.uint8)
    cv2.rectangle(pure_game, (220, 120), (420, 240), (160, 200, 60), -1)
    det_pure = detect_webcam_corner_in_frame(pure_game)
    assert det_pure["has_webcam"] is False
    vf_pure = build_multi_aspect_filter(
        aspect_ratio="9:16",
        frame_layout="gameplay_webcam_pip",
        source_w=1920,
        source_h=1080,
        webcam_corner="top_right",
        face_track_data={"webcam_detection": det_pure},
    )
    assert "overlay=" in vf_pure and "geq=" in vf_pure and "crop=" in vf_pure

    # 3. Preview frame with gameplay_webcam_pip places bottom_left source cam into requested top_right output
    prev = render_exact_preview_frame(
        video_path=str(src_mp4),
        timestamp=0.5,
        aspect_ratio="9:16",
        frame_layout="gameplay_webcam_pip",
        webcam_corner="top_right",
        pip_border_color="#F59E0B",
        burn_captions=False,
    )
    prev_path = storage_dir / "previews" / Path(prev["frame_url"]).name
    assert prev_path.exists()
    with Image.open(prev_path) as pim:
        rgb = np.array(pim.convert("RGB"))
        H, W = rgb.shape[:2]
        # Top-right PiP (safe coords 60% x, 14% y) shows skin/eyes from bottom_left source cam
        # at full brightness (no radial dark vignette), while outer 18px rounded corner shows teal gameplay.
        x0 = int(round(W * 0.60))
        y0 = int(round(H * 0.14))
        pip_cx = x0 + int(W * 0.18)
        pip_cy = y0 + int(W * 0.11)
        sample_patch = rgb[pip_cy - 15:pip_cy + 15, pip_cx - 15:pip_cx + 15]
        assert float(np.max(sample_patch[:, :, 0])) > 200.0
        assert float(np.mean(sample_patch[:, :, 0])) > 120.0
        rounded_corner_px = rgb[y0 + 1, x0 + 1]
        assert int(rounded_corner_px[0]) < 70 and int(rounded_corner_px[2]) > 80

    # 4. End-to-end rendered MP4 cleanly completes with gameplay_webcam_pip & rounded PiP corners
    with Session(test_engine) as session:
        proj = Project(
            title="Gameplay PiP Project",
            source_type="upload",
            source_path=str(src_mp4),
            duration=2.2,
            status="finished",
        )
        session.add(proj)
        session.commit()
        session.refresh(proj)

        out_mp4 = str(storage_dir / "pip_out.mp4")
        rendered = render_clip_ultra(
            master_video_path=str(src_mp4),
            clip_id=501,
            start_time=0.0,
            duration=2.0,
            words=[{"word": "CLUTCH", "start": 0.2, "end": 1.2}],
            project_id=proj.id,
            aspect_ratio="9:16",
            frame_layout="gameplay_webcam_pip",
            webcam_corner="top_right",
            pip_border_color="#F59E0B",
            burn_captions=True,
            hardware_mode="cpu",
            quality_mode="fast_preview",
            output_path_override=out_mp4,
        )
        assert os.path.exists(rendered)
        meta = probe_video(rendered)
        assert meta["width"] == 1080 and meta["height"] == 1920
        assert meta["duration"] >= 1.7
