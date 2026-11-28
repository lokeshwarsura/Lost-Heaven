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
from app.models import Project, UpdateWebcamRegionRequest, ViralClip
from app.services.face_tracker import _smooth_step, compute_clamped_crop_coords
from app.services.preview_frame import render_exact_preview_frame
from app.services.proxy import get_ffmpeg_cmd
from app.services.reframer import build_multi_aspect_filter
from app.services.webcam_detector import detect_source_webcam


def _setup_isolated_db(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    storage_dir = tmp_path / "storage"
    for sub in ("renders", "masters", "previews", "assets", "thumbnails", "models"):
        (storage_dir / sub).mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr(settings, "STORAGE_DIR", str(storage_dir))
    monkeypatch.setattr(settings, "GEMINI_API_KEY", "")
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    monkeypatch.setattr(main_mod, "get_persisted_settings", lambda: {})

    db_path = tmp_path / "camera_rework.db"
    test_engine = create_engine(
        f"sqlite:///{db_path}",
        connect_args={"check_same_thread": False},
    )
    SQLModel.metadata.create_all(test_engine)
    monkeypatch.setattr(app_db, "engine", test_engine)
    return test_engine, storage_dir


def _draw_haar_portrait(roi: np.ndarray) -> None:
    """Draw a high-contrast studio portrait onto a 320x240 BGR buffer detectable by offline Haar/MediaPipe."""
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


def _create_stream_video(
    out_path: Path,
    corner: str = "top_left",
    bordered: bool = True,
    with_face: bool = True,
    duration: float = 2.4,
) -> Path:
    """Create a synthetic 1920x1080 gameplay stream clip with configurable webcam overlay."""
    out_path.parent.mkdir(parents=True, exist_ok=True)
    ffmpeg = get_ffmpeg_cmd()
    canvas = np.full((1080, 1920, 3), (120, 85, 22), dtype=np.uint8)

    if with_face:
        cw, ch = (480, 270) if bordered else (440, 330)
        if corner == "top_left":
            x, y = 20, 20
        elif corner == "top_right":
            x, y = 1920 - cw - 20, 20
        elif corner == "bottom_right":
            x, y = 1920 - cw - 20, 1080 - ch - 20
        else:
            x, y = 20, 1080 - ch - 20

        bg_bgr = (144, 180, 232) if bordered else (65, 175, 60)
        roi = np.full((240, 320, 3), bg_bgr, dtype=np.uint8)
        _draw_haar_portrait(roi)
        roi_resized = cv2.resize(roi, (cw, ch), interpolation=cv2.INTER_AREA)
        canvas[y:y + ch, x:x + cw] = roi_resized
        if bordered:
            cv2.rectangle(canvas, (x, y), (x + cw, y + ch), (248, 248, 252), 3)
    else:
        # Pure gameplay HUD shapes with zero faces
        cv2.rectangle(canvas, (100, 100), (620, 420), (35, 140, 210), -1)
        cv2.circle(canvas, (960, 540), 140, (60, 210, 170), -1)
        cv2.rectangle(canvas, (1300, 700), (1820, 960), (190, 95, 50), -1)

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


def test_detect_source_webcam_16_frames_and_ergonomic_unbordered(tmp_path: Path):
    bordered_mp4 = _create_stream_video(
        tmp_path / "media" / "streamer_tl.mp4",
        corner="top_left",
        bordered=True,
        with_face=True,
    )
    reg = detect_source_webcam(str(bordered_mp4), start_time=0.0, end_time=2.4, num_samples=16)
    assert reg["detected"] is True
    assert reg["webcam_detected"] is True
    assert reg["source_zone"] == "top_left"
    assert reg["frames_sampled"] >= 8
    assert 0.0 <= reg["x"] < 0.25 and 0.0 <= reg["y"] < 0.25
    assert 0.18 <= reg["w"] <= 0.50 and 0.16 <= reg["h"] <= 0.55

    # Unbordered / green-screen style camera falls back cleanly to ergonomic head-and-shoulders crop
    unbordered_mp4 = _create_stream_video(
        tmp_path / "media" / "streamer_br_greenscreen.mp4",
        corner="bottom_right",
        bordered=False,
        with_face=True,
    )
    reg_erg = detect_source_webcam(str(unbordered_mp4), start_time=0.0, end_time=2.4, num_samples=16)
    assert reg_erg["detected"] is True
    assert reg_erg["webcam_detected"] is True
    assert reg_erg["source_zone"] == "bottom_right"
    assert reg_erg["x"] >= 0.45 and reg_erg["y"] >= 0.40
    assert reg_erg["w"] >= 0.20 and reg_erg["h"] >= 0.22


def test_no_webcam_detected_guard_falls_back_gracefully(tmp_path: Path):
    pure_game_mp4 = _create_stream_video(
        tmp_path / "media" / "pure_gameplay.mp4",
        with_face=False,
    )
    reg = detect_source_webcam(str(pure_game_mp4), start_time=0.0, end_time=2.4, num_samples=16)
    assert reg["detected"] is False
    assert reg["webcam_detected"] is False
    assert reg["source_zone"] == "none"

    vf = build_multi_aspect_filter(
        aspect_ratio="9:16",
        frame_layout="gameplay_webcam_pip",
        source_w=1920,
        source_h=1080,
        webcam_corner="top_right",
        face_track_data={"webcam_detection": reg},
    )
    assert "vignette=" not in vf
    # Pure gameplay with gameplay_webcam_pip now deterministically crops top_left secondary_box into rounded PiP
    assert "crop=" in vf and "geq=" in vf and "overlay=" in vf


def test_pip_rounded_card_zero_vignette_and_safe_positions(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
):
    _, storage_dir = _setup_isolated_db(tmp_path, monkeypatch)
    src_mp4 = _create_stream_video(
        tmp_path / "media" / "streamer_bl.mp4",
        corner="bottom_left",
        bordered=True,
        with_face=True,
    )

    vf = build_multi_aspect_filter(
        aspect_ratio="9:16",
        frame_layout="gameplay_webcam_pip",
        source_w=1920,
        source_h=1080,
        webcam_corner="bottom_right",
        pip_border_color="#FFFFFF",
        face_track_data={
            "webcam_detection": {
                "detected": True,
                "webcam_detected": True,
                "source_zone": "bottom_left",
                "x": 0.01,
                "y": 0.72,
                "w": 0.25,
                "h": 0.25,
            }
        },
    )
    assert "r=18" in vf or "radius" in vf or "alpha" in vf
    assert "gblur=sigma=5" in vf
    assert "0xFFFFFF38" in vf
    assert "vignette=" not in vf

    # Render bottom_right PiP and check it clears caption zone & keeps full brightness without dark vignette
    prev = render_exact_preview_frame(
        video_path=str(src_mp4),
        timestamp=0.6,
        aspect_ratio="9:16",
        frame_layout="gameplay_webcam_pip",
        webcam_corner="bottom_right",
        pip_border_color="#FFFFFF",
        color_grading={"vignette": 1.0},  # Must still NOT darken gameplay_webcam_pip!
        burn_captions=False,
    )
    prev_path = storage_dir / "previews" / Path(prev["frame_url"]).name
    assert prev_path.exists()
    with Image.open(prev_path) as pim:
        rgb = np.array(pim.convert("RGB"))
        H, W = rgb.shape[:2]
        # Safe bottom_right sits at x=49% W, y=54% H -> bottom remains well above lower 26% caption band
        x0 = int(round(W * 0.49))
        y0 = int(round(H * 0.54))
        pip_cx = x0 + int(W * 0.18)
        pip_cy = y0 + int(W * 0.10)
        center_patch = rgb[pip_cy - 15:pip_cy + 15, pip_cx - 15:pip_cx + 15]
        assert float(np.mean(center_patch[:, :, 0])) > 130.0
        # Outer clipped rounded corner shows teal gameplay, not bright skin or black vignette
        corner_px = rgb[y0 + 1, x0 + 1]
        assert int(corner_px[0]) < 75 and int(corner_px[2]) > 75


def test_split_top_40_and_auto_track_headroom_smoothing():
    vf_split = build_multi_aspect_filter(
        aspect_ratio="9:16",
        frame_layout="webcam_gameplay_split",
        source_w=1920,
        source_h=1080,
        webcam_corner="top_left",
        webcam_split_ratio=0.40,
        face_track_data={
            "webcam_detection": {
                "detected": True,
                "webcam_detected": True,
                "source_zone": "top_left",
                "x": 0.01,
                "y": 0.02,
                "w": 0.25,
                "h": 0.25,
            }
        },
    )
    assert "vstack=inputs=2" in vf_split
    assert "h=3:color=0xFFFFFF@0.28" in vf_split

    # EMA alpha=0.14 & deadzone=0.03 filter out small jitter
    assert _smooth_step((0.50, 0.50), (0.52, 0.50), is_cut=False, alpha=0.14, deadzone=0.03) == (0.50, 0.50)
    moved_x, moved_y = _smooth_step((0.50, 0.50), (0.70, 0.50), is_cut=False, alpha=0.14, deadzone=0.03)
    assert abs(moved_x - 0.528) < 1e-6 and abs(moved_y - 0.50) < 1e-6

    # Rule-of-thirds headroom keeps head top well inside frame (eye line placed around ~36% from crop top)
    cx, cy = compute_clamped_crop_coords(0.50, 0.15, 608, 1080, 1920, 1080)
    assert cy == 0 and cx >= 0
    _, cy_mid = compute_clamped_crop_coords(0.50, 0.50, 608, 540, 1920, 1080)
    assert abs(cy_mid - int(round(540 - 0.36 * 540))) <= 4


def test_webcam_region_api_endpoints_cache_and_lock(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
):
    test_engine, _ = _setup_isolated_db(tmp_path, monkeypatch)
    src_mp4 = _create_stream_video(
        tmp_path / "media" / "api_stream_bl.mp4",
        corner="bottom_left",
        bordered=True,
        with_face=True,
    )

    with Session(test_engine) as session:
        proj = Project(
            title="Webcam API Project",
            source_type="upload",
            source_path=str(src_mp4),
            duration=2.4,
            status="finished",
        )
        session.add(proj)
        session.commit()
        session.refresh(proj)

        clip = ViralClip(
            project_id=proj.id,
            start_time=0.0,
            end_time=2.4,
            duration=2.4,
            virality_score=91,
            suggested_title="Clutch Moment",
            frame_layout="gameplay_webcam_pip",
        )
        session.add(clip)
        session.commit()
        session.refresh(clip)
        pid, cid = proj.id, clip.id

    route_paths = {getattr(r, "path", "") for r in main_mod.app.routes}
    assert "/api/clips/{project_id}/{clip_id}/webcam-region" in route_paths

    with Session(test_engine) as session:
        # 1. GET auto-detects across 16 frames and caches in SQLite
        d1 = main_mod.get_clip_webcam_region_endpoint(pid, cid, refresh=False, session=session)
        assert d1["webcam_detected"] is True
        assert d1["source_zone"] == "bottom_left"

        # Verify SQLite persistence
        cached_clip = session.get(ViralClip, cid)
        assert cached_clip is not None and cached_clip.get_source_webcam_region() is not None

        # 2. PATCH locks source zone to top_right
        d2 = main_mod.update_clip_webcam_region_endpoint(
            pid, cid, UpdateWebcamRegionRequest(source_zone="top_right"), session=session
        )
        assert d2["locked_zone"] == "top_right"
        assert d2["source_zone"] == "top_right"
        assert d2["x"] > 0.50

        # 3. Cached GET returns locked top_right without re-scanning
        d3 = main_mod.get_clip_webcam_region_endpoint(pid, cid, refresh=False, session=session)
        assert d3["source_zone"] == "top_right"

        # 4. PATCH auto + refresh re-runs 16-frame detector -> back to bottom_left
        d4 = main_mod.update_clip_webcam_region_endpoint(
            pid,
            cid,
            UpdateWebcamRegionRequest(source_zone="auto", refresh=True),
            session=session,
        )
        assert d4["locked_zone"] == "auto"
        assert d4["source_zone"] == "bottom_left"
