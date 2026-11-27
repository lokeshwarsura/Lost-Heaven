from pathlib import Path

import cv2
import numpy as np
import pytest
from PIL import Image
from sqlmodel import Session, SQLModel, create_engine

import app.database as app_db
import app.main as main_mod
from app.config import settings
from app.models import FramingOverrideRequest, Project, ViralClip
from app.services.auto_framer import recommend_optimal_framing
from app.services.preview_frame import render_exact_preview_frame
from app.services.reframer import prepare_clip_framing_data
from tests.test_unified_reframer import _draw_portrait, _encode_png_sequence_video


def _setup_db(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    storage_dir = tmp_path / "storage"
    for sub in ("previews", "renders", "masters", "assets"):
        (storage_dir / sub).mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr(settings, "STORAGE_DIR", str(storage_dir))
    db_path = tmp_path / "phase2.db"
    test_engine = create_engine(f"sqlite:///{db_path}", connect_args={"check_same_thread": False})
    SQLModel.metadata.create_all(test_engine)
    monkeypatch.setattr(app_db, "engine", test_engine)
    return test_engine, storage_dir


def test_recommend_optimal_framing_all_4_scene_archetypes():
    # 1. Corner facecam overlay (bottom_left) + gameplay -> gameplay_webcam_pip opposite right corner
    rec_cam_bl = recommend_optimal_framing({
        "webcam": {"detected": True, "source_zone": "bottom_left", "x": 0.02, "y": 0.65, "w": 0.25, "h": 0.30, "action_cx": 0.5, "action_cy": 0.5},
        "keyframes": [{"t": 0.0, "action_cx": 0.5, "action_cy": 0.5}],
        "shots": [],
        "summary": {"has_corner_webcam": True, "face_presence_ratio": 1.0, "dual_speaker_ratio": 0.0},
    })
    assert rec_cam_bl["frame_layout"] == "gameplay_webcam_pip"
    assert rec_cam_bl["webcam_corner"] in ("top_right", "bottom_right")

    # Top-right corner webcam -> opposite left corner
    rec_cam_tr = recommend_optimal_framing({
        "webcam": {"detected": True, "source_zone": "top_right", "x": 0.72, "y": 0.02, "w": 0.25, "h": 0.30, "action_cx": 0.5, "action_cy": 0.5},
        "keyframes": [{"t": 0.0, "action_cx": 0.5, "action_cy": 0.5}],
        "shots": [],
        "summary": {"has_corner_webcam": True, "face_presence_ratio": 1.0, "dual_speaker_ratio": 0.0},
    })
    assert rec_cam_tr["frame_layout"] == "gameplay_webcam_pip"
    assert rec_cam_tr["webcam_corner"] in ("top_left", "bottom_left")

    # 2. Persistent dual speakers -> split_podcast
    rec_dual = recommend_optimal_framing({
        "webcam": {"detected": False, "source_zone": "none"},
        "shots": [{"shot_id": 0, "speaker_mode": "two_speaker"}],
        "keyframes": [{"t": 0.0, "face_count": 2}],
        "summary": {"has_corner_webcam": False, "face_presence_ratio": 1.0, "dual_speaker_ratio": 0.9},
    })
    assert rec_dual["frame_layout"] == "split_podcast"

    # 3. Single talking head -> auto_track
    rec_single = recommend_optimal_framing({
        "webcam": {"detected": False, "source_zone": "center"},
        "shots": [{"shot_id": 0, "speaker_mode": "single_speaker"}],
        "keyframes": [{"t": 0.0, "face_count": 1}],
        "summary": {"has_corner_webcam": False, "face_presence_ratio": 0.95, "dual_speaker_ratio": 0.0},
    })
    assert rec_single["frame_layout"] == "auto_track"

    # 4. Zero-face gameplay / screen recording -> fit_blur
    rec_game = recommend_optimal_framing({
        "webcam": {"detected": False, "source_zone": "none"},
        "shots": [{"shot_id": 0, "speaker_mode": "zero_face"}],
        "keyframes": [{"t": 0.0, "face_count": 0}],
        "summary": {"has_corner_webcam": False, "face_presence_ratio": 0.0, "dual_speaker_ratio": 0.0},
    })
    assert rec_game["frame_layout"] == "fit_blur"


def test_framing_map_override_persistence_and_auto_frame_all_endpoints(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
):
    test_engine, storage_dir = _setup_db(tmp_path, monkeypatch)

    # Multi-clip master source:
    # 0.0s .. 2.0s: 1920x1080 gameplay with bottom-left webcam card + portrait
    seg_cam = np.full((1080, 1920, 3), (120, 85, 22), dtype=np.uint8)
    roi = np.full((240, 320, 3), (144, 180, 232), dtype=np.uint8)
    _draw_portrait(roi, 160, 120, scale=1.0, shirt_bgr=(60, 70, 90))
    seg_cam[1080 - 360:1080 - 20, 20:480] = cv2.resize(roi, (460, 340), interpolation=cv2.INTER_AREA)
    cv2.rectangle(seg_cam, (20, 1080 - 360), (480, 1080 - 20), (245, 245, 250), 3)

    # 2.0s .. 4.0s: pure gameplay (0 faces)
    seg_pure = np.full((1080, 1920, 3), (40, 110, 60), dtype=np.uint8)
    cv2.rectangle(seg_pure, (500, 300), (1420, 780), (45, 200, 235), -1)

    src_mp4 = _encode_png_sequence_video(tmp_path / "phase2_src.mp4", [(seg_cam, 2.0), (seg_pure, 2.0)])

    with Session(test_engine) as session:
        proj = Project(title="Phase 2 Routing", source_path=str(src_mp4), duration=4.0, status="finished")
        session.add(proj)
        session.commit()
        session.refresh(proj)

        c1 = ViralClip(project_id=proj.id, start_time=0.0, end_time=1.9, duration=1.9, virality_score=92)
        c2 = ViralClip(project_id=proj.id, start_time=2.1, end_time=3.9, duration=1.8, virality_score=84)
        session.add(c1)
        session.add(c2)
        session.commit()
        session.refresh(c1)
        session.refresh(c2)

        # 1. GET /framing-map populates SQLite visual_map_json and detects bottom_left webcam
        fmap_res = main_mod.get_clip_framing_map_endpoint(proj.id, c1.id, refresh=False, session=session)
        assert fmap_res["visual_map"]["webcam"]["detected"] is True
        assert fmap_res["visual_map"]["webcam"]["source_zone"] == "bottom_left"
        assert fmap_res["recommended"]["frame_layout"] == "gameplay_webcam_pip"
        reloaded_c1 = session.get(ViralClip, c1.id)
        assert reloaded_c1 is not None and reloaded_c1.get_visual_map() is not None

        # 2. PATCH /framing-override persists custom webcam_box, focus_anchor & lock_camera
        ov_res = main_mod.update_clip_framing_override_endpoint(
            proj.id,
            c1.id,
            FramingOverrideRequest(
                webcam_box={"x": 0.01, "y": 0.66, "w": 0.24, "h": 0.31},
                focus_anchor={"x": 0.72, "y": 0.44},
                lock_camera=True,
            ),
            session=session,
        )
        assert ov_res["framing_override"]["lock_camera"] is True
        assert ov_res["framing_override"]["webcam_box"]["w"] == 0.24
        assert ov_res["source_webcam_region"]["locked_zone"] == "custom"

        # Verify locked focus_anchor overrides dynamic keyframe tracking cleanly in reframer
        ft_locked, _, _ = prepare_clip_framing_data(
            str(src_mp4),
            [(0.0, 1.9)],
            frame_layout="auto_track",
            framing_override=ov_res["framing_override"],
        )
        assert all(abs(k["cx"] - 0.72) < 1e-4 for k in ft_locked["keyframes"])

        # Preview render honors custom override without errors
        prev = render_exact_preview_frame(
            video_path=str(src_mp4),
            timestamp=0.6,
            frame_layout="gameplay_webcam_pip",
            webcam_corner="top_right",
            source_webcam_region=ov_res["source_webcam_region"],
            framing_override=ov_res["framing_override"],
            burn_captions=False,
        )
        assert (storage_dir / "previews" / Path(prev["frame_url"]).name).exists()

        # Reset override cleanly clears framing_override_json
        rst = main_mod.update_clip_framing_override_endpoint(
            proj.id, c1.id, FramingOverrideRequest(reset=True), session=session
        )
        assert rst["framing_override"] is None

        # 3. POST /api/projects/{pid}/auto-frame-all auto-configures all clips across scene archetypes
        af_res = main_mod.auto_frame_all_clips_endpoint(proj.id, session=session)
        assert af_res["status"] == "completed"
        assert af_res["updated_count"] == 2

        c1_after = session.get(ViralClip, c1.id)
        c2_after = session.get(ViralClip, c2.id)
        assert c1_after is not None and c2_after is not None
        assert c1_after.frame_layout == "gameplay_webcam_pip"
        assert c1_after.webcam_corner in ("top_right", "bottom_right")
        assert c2_after.frame_layout == "fit_blur"
