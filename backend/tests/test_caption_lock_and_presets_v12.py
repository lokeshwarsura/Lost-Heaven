import shutil
import json
from pathlib import Path
from typing import Dict, List

import cv2
import numpy as np
import pytest
from sqlmodel import Session, select

import app.main as main_mod
import app.database as db_mod
from app.config import settings
from app.models import ViralClip, Project, PreviewFrameRequest, UpdateClipRequest, RenderClipRequest
from app.services.caption_renderer import build_caption_ffconcat, render_caption_frame
from app.services.filters import (
    COLOR_PRESETS,
    STUDIO_COLOR_PRESETS,
    build_advanced_color_filter,
    generate_clip_preset_previews,
)
from app.services.preview_frame import render_exact_preview_frame
from app.services.proxy import get_ffmpeg_cmd
from app.services.reframer import ALL_FRAMING_TEMPLATES, canonicalize_framing_template


def _create_test_video(out_path: Path, duration_sec: float = 2.0, width: int = 640, height: int = 360) -> Path:
    import subprocess

    out_path.parent.mkdir(parents=True, exist_ok=True)
    cmd = [
        get_ffmpeg_cmd(),
        "-y",
        "-f", "lavfi",
        "-i", f"testsrc2=size={width}x{height}:rate=15:duration={duration_sec}",
        "-f", "lavfi",
        "-i", f"sine=frequency=440:sample_rate=44100:duration={duration_sec}",
        "-af", "volume=0.05",
        "-c:v", "libx264",
        "-preset", "ultrafast",
        "-pix_fmt", "yuv420p",
        "-c:a", "aac",
        "-shortest",
        str(out_path),
    ]
    res = subprocess.run(cmd, capture_output=True, text=True, timeout=60)
    assert res.returncode == 0, f"Synthetic video creation failed: {res.stderr}"
    return out_path


def _alpha_ink_bbox(rgba_img) -> tuple[int, int, int, int, float, float]:
    alpha = np.array(rgba_img)[:, :, 3]
    ys, xs = np.where(alpha > 10)
    assert len(ys) > 0, "Expected non-transparent caption pixels"
    y0, y1 = int(ys.min()), int(ys.max())
    x0, x1 = int(xs.min()), int(xs.max())
    return x0, y0, x1, y1, (x0 + x1) / 2.0, (y0 + y1) / 2.0


def test_custom_caption_position_locks_exactly_and_overrides_face_safe():
    w, h = 540, 960
    words = [
        {"word": "HELLO", "start": 0.0, "end": 0.6},
        {"word": "WORLD", "start": 0.6, "end": 1.2},
    ]
    # Place a synthetic face right over y=0.20 where the user explicitly wants captions
    face_kf = [{"t": 0.3, "cx": 0.50, "cy": 0.20, "w": 0.28, "h": 0.26}]

    # 1. With caption_custom_position=True, even face_safe_captions=True MUST NOT push away from y=0.20
    locked_img = render_caption_frame(
        t=0.3,
        words=words,
        width=w,
        height=h,
        style_preset="Karaoke pop",
        pos_x=50.0,
        pos_y=20.0,
        face_safe_captions=True,
        face_keyframes=face_kf,
        caption_custom_position=True,
        clip_duration=2.0,
    )
    _, _, _, _, cx_locked, cy_locked = _alpha_ink_bbox(locked_img)
    assert abs(cx_locked - 0.50 * w) <= 12.0
    assert abs(cy_locked - 0.20 * h) <= 18.0, f"Custom caption moved unexpectedly: cy={cy_locked} vs {0.20 * h}"

    # 2. Without custom position lock, face_safe_captions=True shifts captions clear of the overlapping face
    auto_safe_img = render_caption_frame(
        t=0.3,
        words=words,
        width=w,
        height=h,
        style_preset="Karaoke pop",
        pos_x=50.0,
        pos_y=20.0,
        face_safe_captions=True,
        face_keyframes=face_kf,
        caption_custom_position=False,
        clip_duration=2.0,
    )
    _, _, _, _, _, cy_safe = _alpha_ink_bbox(auto_safe_img)
    assert abs(cy_safe - 0.20 * h) > 80.0, f"Expected face-safe shift when unlocked, got cy={cy_safe}"


def test_caption_zero_drift_across_words_and_animations(tmp_path: Path):
    w, h = 540, 960
    words = [
        {"word": "go", "start": 0.0, "end": 0.4},
        {"word": "STOP", "start": 0.4, "end": 0.8},
        {"word": "going", "start": 0.8, "end": 1.2},
    ]

    # Across active words inside a multi-word line under Instant Cut / Steady, block bbox must not drift
    for anim in ("instant_cut", "steady", "color_pop"):
        boxes = []
        for sample_t in (0.2, 0.6, 1.0):
            img = render_caption_frame(
                t=sample_t,
                words=words,
                width=w,
                height=h,
                style_preset="Karaoke pop",
                pos_x=42.0,
                pos_y=32.0,
                caption_custom_position=True,
                caption_animation=anim,
                text_case="As Spoken",
                clip_duration=2.0,
            )
            boxes.append(_alpha_ink_bbox(img))

        base_x0, base_y0, base_x1, base_y1, base_cx, base_cy = boxes[0]
        for idx, (x0, y0, x1, y1, cx, cy) in enumerate(boxes[1:], start=1):
            assert abs(y0 - base_y0) <= 1.0, f"{anim} word {idx} drifted vertically (y0 {y0} vs {base_y0})"
            assert abs(y1 - base_y1) <= 1.0, f"{anim} word {idx} drifted vertically (y1 {y1} vs {base_y1})"
            assert abs(x0 - base_x0) <= 1.0, f"{anim} word {idx} drifted horizontally (x0 {x0} vs {base_x0})"
            assert abs(cx - base_cx) <= 1.0, f"{anim} word {idx} center_x drifted ({cx} vs {base_cx})"
            assert abs(cy - base_cy) <= 1.0, f"{anim} word {idx} center_y drifted ({cy} vs {base_cy})"

    # kinetic_pop scales active word locally without pushing line anchor across words
    kp_centers = []
    for sample_t in (0.2, 0.6, 1.0):
        img = render_caption_frame(
            t=sample_t,
            words=words,
            width=w,
            height=h,
            style_preset="Karaoke pop",
            pos_x=50.0,
            pos_y=78.0,
            caption_custom_position=True,
            caption_animation="kinetic_pop",
            text_case="As Spoken",
            clip_duration=2.0,
        )
        kp_centers.append(_alpha_ink_bbox(img))
    for _, _, _, _, cx, cy in kp_centers:
        assert abs(cx - 0.50 * w) <= 12.0
        assert abs(cy - 0.78 * h) <= 22.0

    # ffconcat pipeline also honors caption_custom_position=True & face_safe_captions=True without shifting
    cap_temp_dir, ffconcat_path, unique_cnt = build_caption_ffconcat(
        words=words,
        clip_duration=1.5,
        width=w,
        height=h,
        style_preset="Karaoke pop",
        pos_x=35.0,
        pos_y=22.0,
        face_safe_captions=True,
        face_keyframes=[{"t": 0.5, "cx": 0.35, "cy": 0.22, "w": 0.3, "h": 0.3}],
        caption_custom_position=True,
    )
    try:
        assert unique_cnt > 0
        assert Path(ffconcat_path).exists()
        png_files = sorted(Path(cap_temp_dir).glob("frame_*.png"))
        assert len(png_files) > 0
        for png in png_files[:3]:
            rgba = cv2.imdecode(np.fromfile(str(png), dtype=np.uint8), cv2.IMREAD_UNCHANGED)
            assert rgba is not None
            ys, xs = np.where(rgba[:, :, 3] > 10)
            assert len(ys) > 0
            assert abs(((ys.min() + ys.max()) / 2.0) - 0.22 * h) <= 20.0
    finally:
        shutil.rmtree(cap_temp_dir, ignore_errors=True)


def test_caption_custom_position_endpoint_persistence_and_preview(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(settings, "STORAGE_DIR", str(tmp_path / "storage"))
    (Path(settings.STORAGE_DIR) / "previews").mkdir(parents=True, exist_ok=True)
    video_path = _create_test_video(tmp_path / "src.mp4")

    with Session(db_mod.engine) as session:
        proj = Project(
            title="Caption Lock Project",
            source_path=str(video_path),
            status="completed",
            duration=2.0,
            transcript_json=json.dumps([
                {"word": "EXACT", "start": 0.1, "end": 0.7},
                {"word": "LOCK", "start": 0.7, "end": 1.3},
            ]),
        )
        session.add(proj)
        session.commit()
        session.refresh(proj)

        clip = ViralClip(
            project_id=proj.id,
            start_time=0.0,
            end_time=2.0,
            duration=2.0,
            aspect_ratio="9:16",
            face_safe_captions=True,
            position_x=50.0,
            position_y=78.0,
        )
        session.add(clip)
        session.commit()
        session.refresh(clip)

        upd_res = main_mod.update_clip(
            clip.id,
            UpdateClipRequest(caption_x=0.35, caption_y=0.22, caption_custom_position=True),
            session=session,
        )
        assert upd_res["status"] == "updated"
        fetched = main_mod.get_clip(clip.id, session=session)
        assert fetched["caption_custom_position"] is True
        assert fetched["face_safe_captions"] is False
        assert pytest.approx(fetched["caption_x"], abs=1e-3) == 0.35
        assert pytest.approx(fetched["caption_y"], abs=1e-3) == 0.22

        # Verify RenderClipRequest schema accepts caption_custom_position & face_safe_captions cleanly
        r_req = RenderClipRequest(
            caption_x=0.35,
            caption_y=0.22,
            caption_custom_position=True,
            face_safe_captions=False,
        )
        assert r_req.caption_custom_position is True
        assert r_req.face_safe_captions is False

        # Verify preview_frame endpoint also accepts caption_custom_position without error
        prev = main_mod.preview_frame_endpoint(
            PreviewFrameRequest(
                project_id=proj.id,
                clip_id=clip.id,
                timestamp=0.4,
                caption_x=0.35,
                caption_y=0.22,
                caption_custom_position=True,
                face_safe_captions=True,
                burn_captions=True,
            ),
            session=session,
        )
        assert prev["frame_url"].endswith(".png")


def test_all_10_framing_templates_render_cleanly(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(settings, "STORAGE_DIR", str(tmp_path / "storage"))
    (Path(settings.STORAGE_DIR) / "previews").mkdir(parents=True, exist_ok=True)
    video_path = _create_test_video(tmp_path / "framing_src.mp4", duration_sec=2.0, width=640, height=360)

    expected_10 = {
        "auto_track",
        "fit_blur",
        "webcam_gameplay_split",
        "gameplay_webcam_pip",
        "split_podcast",
        "cinema_letterbox_blur",
        "triple_gaming_hud",
        "portrait_pip_badge",
        "dynamic_punch_crop",
        "side_by_side_50_50",
    }
    assert set(ALL_FRAMING_TEMPLATES) == expected_10
    for tpl in expected_10:
        assert canonicalize_framing_template(tpl) == tpl

    rendered: Dict[str, np.ndarray] = {}
    for tpl in ALL_FRAMING_TEMPLATES:
        res = render_exact_preview_frame(
            video_path=str(video_path),
            timestamp=0.5,
            aspect_ratio="9:16",
            frame_layout=tpl,
            webcam_corner="bottom_right",
            burn_captions=False,
        )
        img_path = Path(settings.STORAGE_DIR) / "previews" / Path(res["frame_url"]).name
        assert img_path.exists(), f"Missing preview for template {tpl}"
        img = cv2.imdecode(np.fromfile(str(img_path), dtype=np.uint8), cv2.IMREAD_COLOR)
        assert img is not None
        h, w = img.shape[:2]
        assert w % 2 == 0 and h % 2 == 0
        assert float(img.std()) > 5.0
        rendered[tpl] = img.astype(np.float32)

    # Confirm the 5 new templates differ noticeably from standard fit_blur and auto_track
    for new_tpl in (
        "cinema_letterbox_blur",
        "triple_gaming_hud",
        "portrait_pip_badge",
        "dynamic_punch_crop",
        "side_by_side_50_50",
    ):
        diff_fit = float(np.mean(np.abs(rendered[new_tpl] - rendered["fit_blur"])))
        diff_auto = float(np.mean(np.abs(rendered[new_tpl] - rendered["auto_track"])))
        assert diff_fit > 1.5 or diff_auto > 1.5, f"New template {new_tpl} matched both baselines too closely"


def test_all_18_visual_presets_filters_and_thumbnails(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(settings, "STORAGE_DIR", str(tmp_path / "storage"))
    video_path = _create_test_video(tmp_path / "preset_src.mp4", duration_sec=2.0, width=320, height=180)

    assert len(STUDIO_COLOR_PRESETS) == 18
    for p in STUDIO_COLOR_PRESETS:
        pid = p["id"]
        assert pid in COLOR_PRESETS
        if pid != "none":
            assert COLOR_PRESETS[pid]["filter"], f"Missing filter for {pid}"
            assert build_advanced_color_filter(preset=pid), f"Empty build_advanced_color_filter for {pid}"

    res = generate_clip_preset_previews(
        video_path=str(video_path),
        project_id=77,
        clip_id=88,
        ref_timestamp=0.5,
        force=True,
    )
    assert len(res["presets"]) == 18
    valid_categories = {"film", "gaming", "creator"}
    for item in res["presets"]:
        assert item["category"] in valid_categories
        jpg = Path(settings.STORAGE_DIR) / "previews" / "presets" / "77_88" / f"{item['id']}.jpg"
        assert jpg.exists(), f"Missing preset thumbnail for {item['id']}"
        img = cv2.imdecode(np.fromfile(str(jpg), dtype=np.uint8), cv2.IMREAD_COLOR)
        assert img is not None and img.shape[1] == 160 and img.shape[0] == 90
