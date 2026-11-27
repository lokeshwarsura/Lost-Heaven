import math
import os
import wave
from pathlib import Path

import cv2
import numpy as np
import pytest
from PIL import Image
from sqlmodel import Session, SQLModel, create_engine

import app.database as app_db
import app.main as main_mod
from app.config import settings
from app.models import (
    FramingOverrideRequest,
    GenerateThumbnailRequest,
    Project,
    TrimClipRequest,
    ViralClip,
)
from tests.test_multimodal_analyzers import _encode_sequence_mp4
from tests.test_unified_reframer import _draw_portrait


def _setup_isolated_env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    storage_dir = tmp_path / "storage"
    for sub in ("previews", "renders", "masters", "assets", "thumbnails", "models", "projects"):
        (storage_dir / sub).mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr(settings, "STORAGE_DIR", str(storage_dir))
    monkeypatch.setattr(settings, "GEMINI_API_KEY", "")
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    monkeypatch.setattr(main_mod, "get_persisted_settings", lambda: {})
    db_path = tmp_path / "intel_v6.db"
    test_engine = create_engine(f"sqlite:///{db_path}", connect_args={"check_same_thread": False})
    SQLModel.metadata.create_all(test_engine)
    monkeypatch.setattr(app_db, "engine", test_engine)
    return test_engine, storage_dir


def test_multimodal_best_frames_reanalyze_thumbnail_snap_and_calibrate_e2e(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
):
    test_engine, storage_dir = _setup_isolated_env(tmp_path, monkeypatch)

    # Construct multi-scene source video with:
    # 0.0..0.35s: dark frame (low exposure)
    # 0.35..2.0s: crisp studio portrait
    # 2.0..3.4s: blurry frame (rejected)
    # 3.4..5.2s: sharp smiling portrait at slightly offset position (peak_emotion + clean_anchor)
    # 5.2..6.6s: high motion target (peak_action)
    dark = np.full((720, 1280, 3), 6, dtype=np.uint8)

    studio_a = np.full((720, 1280, 3), (75, 88, 115), dtype=np.uint8)
    for gx in range(80, 1200, 90):
        cv2.line(studio_a, (gx, 30), (gx, 690), (140, 155, 185), 2)
    _draw_portrait(studio_a, 640, 340, scale=1.35, shirt_bgr=(55, 95, 200))

    blur = cv2.GaussianBlur(studio_a, (31, 31), sigmaX=12.0)

    studio_b = np.full((720, 1280, 3), (82, 96, 124), dtype=np.uint8)
    for gx in range(80, 1200, 90):
        cv2.line(studio_b, (gx, 30), (gx, 690), (150, 165, 195), 2)
    _draw_portrait(studio_b, 820, 330, scale=1.40, shirt_bgr=(65, 130, 220))

    act_a = np.full((720, 1280, 3), (185, 160, 45), dtype=np.uint8)
    for gy in range(80, 640, 80):
        cv2.line(act_a, (60, gy), (1220, gy), (240, 240, 240), 2)
    cv2.rectangle(act_a, (140, 140), (480, 520), (35, 50, 225), -1)

    act_b = np.full((720, 1280, 3), (185, 160, 45), dtype=np.uint8)
    for gy in range(80, 640, 80):
        cv2.line(act_b, (60, gy), (1220, gy), (240, 240, 240), 2)
    cv2.rectangle(act_b, (760, 140), (1100, 520), (35, 50, 225), -1)

    src_vid = _encode_sequence_mp4(
        tmp_path / "intel_v6_src.mp4",
        [(dark, 0.35), (studio_a, 1.65), (blur, 1.4), (studio_b, 1.8), (act_a, 0.7), (act_b, 0.7)],
        fps=8,
    )

    with Session(test_engine) as session:
        proj = Project(
            title="V6 Multimodal E2E",
            source_path=str(src_vid),
            duration=6.6,
            status="finished",
        )
        session.add(proj)
        session.commit()
        session.refresh(proj)

        # Save matching 16k audio checkpoint with dynamic burst at 3.5..5.2s
        p_dir = storage_dir / "projects" / str(proj.id)
        p_dir.mkdir(parents=True, exist_ok=True)
        sr = 16000
        dur = 6.6
        t = np.linspace(0.0, dur, int(sr * dur), endpoint=False, dtype=np.float32)
        sig = np.full(len(t), 0.0005, dtype=np.float32)
        burst = (t >= 3.5) & (t <= 5.2)
        sig[burst] = (0.45 * np.sin(2.0 * math.pi * 240.0 * t) + 0.25 * np.sin(2.0 * math.pi * 720.0 * t))[burst]
        pcm = np.clip(sig * 32767.0, -32767.0, 32767.0).astype(np.int16)
        with wave.open(str(p_dir / "audio_16k.wav"), "wb") as wf:
            wf.setnchannels(1)
            wf.setsampwidth(2)
            wf.setframerate(sr)
            wf.writeframes(pcm.tobytes())

        clip = ViralClip(
            project_id=proj.id,
            start_time=0.0,
            end_time=6.6,
            duration=6.6,
            virality_score=90,
            hook_headline="MULTIMODAL WORKSTATION",
        )
        clip.set_transcript([
            {"word": "Watch", "start": 3.55, "end": 3.85},
            {"word": "this", "start": 3.90, "end": 4.20},
            {"word": "breakthrough!", "start": 4.25, "end": 4.95},
        ])
        session.add(clip)
        session.commit()
        session.refresh(clip)

        # 1. GET /api/clips/{project_id}/{clip_id}/best-frames (lazy compute + persist)
        bf_res = main_mod.get_clip_best_frames_endpoint(proj.id, clip.id, session=session)
        assert bf_res["clip_id"] == clip.id
        assert bf_res["project_id"] == proj.id
        assert len(bf_res["frames"]) >= 2
        assert bf_res["multimodal_summary"]["integrated_lufs"] > -40.0
        assert len(bf_res["multimodal_timeline"]) >= 12
        assert len(bf_res["scene_cuts"]) >= 1

        # Verify SQLite persistence across Project & ViralClip + serializer exposure
        proj_db = session.get(Project, proj.id)
        clip_db = session.get(ViralClip, clip.id)
        assert proj_db is not None and proj_db.get_multimodal_analysis() is not None
        assert clip_db is not None and len(clip_db.get_best_frames()) == len(bf_res["frames"])
        serialized = main_mod._serialize_clip(clip_db, proj_db)
        assert len(serialized["best_frames"]) == len(bf_res["frames"])
        assert serialized["multimodal_summary"]["scene_cut_count"] >= 1

        # 2. GET /api/clips/{project_id}/{clip_id}/best-frames/{frame_index}/image
        for idx in range(len(bf_res["frames"])):
            img_resp = main_mod.get_clip_best_frame_image_endpoint(proj.id, clip.id, idx, session=session)
            assert os.path.exists(img_resp.path)
            with Image.open(img_resp.path) as im:
                assert im.size == (1280, 720)

        # 3. POST /api/clips/{project_id}/{clip_id}/reanalyze refreshes disk stills cleanly
        re_res = main_mod.reanalyze_clip_multimodal_endpoint(proj.id, clip.id, session=session)
        assert re_res["status"] == "reanalyzed"
        assert len(re_res["frames"]) >= 2
        assert re_res["multimodal_timeline"]

        # 4. Simulate Workstation 1-click actions:
        # 4a) Set as Thumbnail Cover using chosen Best Frame
        chosen = bf_res["frames"][0]
        thumb = main_mod.generate_thumbnail_endpoint(
            proj.id,
            clip.id,
            GenerateThumbnailRequest(
                timestamp=chosen["t"],
                headline="BEST FRAME COVER",
                format="9:16",
                depth_layer=True,
            ),
            session=session,
        )
        assert thumb["status"] == "generated"
        assert abs(thumb["timestamp"] - round(chosen["t"], 2)) <= 0.05

        # 4b) Snap Clip Start Here -> trims clip cleanly to chosen opener frame
        clean_op = next(
            (bf for bf in bf_res["frames"] if bf["t"] >= 0.35 and bf["t"] <= 2.5),
            bf_res["frames"][-1],
        )
        trim_res = main_mod.trim_clip_endpoint(
            proj.id,
            clip.id,
            TrimClipRequest(start_time=round(clean_op["t"], 2), end_time=6.6),
            session=session,
        )
        assert trim_res["clip"]["start_time"] == pytest.approx(round(clean_op["t"], 2), abs=0.05)

        # 4c) Calibrate Camera Here -> locks framing_override anchor onto selected frame
        cal_res = main_mod.update_clip_framing_override_endpoint(
            proj.id,
            clip.id,
            FramingOverrideRequest(focus_anchor={"x": 0.64, "y": 0.36}, lock_camera=True),
            session=session,
        )
        assert cal_res["framing_override"]["lock_camera"] is True
        assert cal_res["framing_override"]["focus_anchor"]["x"] == pytest.approx(0.64, abs=1e-4)
