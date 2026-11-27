import os
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
from app.services.audio_analyzer import analyze_audio_track
from app.services.best_frame_engine import extract_best_frames
from app.services.multimodal_fusion import fuse_multimodal_tracks
from app.services.offline_clipper import extract_viral_clips_offline
from app.services.reframer import prepare_clip_framing_data
from app.services.thumbnail_studio import generate_depth_thumbnail
from app.services.video_analyzer import analyze_video_track
from tests.test_multimodal_analyzers import _encode_sequence_mp4, _write_synthetic_wav_with_burst
from tests.test_unified_reframer import _draw_portrait


def _draw_blinking_portrait(canvas: np.ndarray, cx: int, cy: int, scale: float = 1.3) -> None:
    """Draw a detectable portrait whose eyes are closed (flat skin-toned eyelids, zero pupil contrast)."""
    _draw_portrait(canvas, cx, cy, scale=scale, shirt_bgr=(60, 80, 140))
    # Replace upper eye band in face ROI with smooth uniform closed-eyelid skin tone + thin closed lash line
    ew = int(round(320 * scale))
    eh = int(round(240 * scale))
    x0 = cx - ew // 2
    y0 = cy - eh // 2
    eye_y1 = max(0, y0 + int(round(88 * scale)))
    eye_y2 = min(canvas.shape[0], y0 + int(round(114 * scale)))
    lx1 = max(0, x0 + int(round(122 * scale)))
    lx2 = min(canvas.shape[1], x0 + int(round(198 * scale)))
    if eye_y2 > eye_y1 and lx2 > lx1:
        canvas[eye_y1:eye_y2, lx1:lx2] = (160, 195, 232)
        mid_y = (eye_y1 + eye_y2) // 2
        cv2.line(canvas, (lx1 + 8, mid_y), (lx1 + int(24 * scale), mid_y), (145, 175, 210), 1)
        cv2.line(canvas, (lx2 - int(24 * scale), mid_y), (lx2 - 8, mid_y), (145, 175, 210), 1)


def _setup_db(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    storage_dir = tmp_path / "storage"
    for sub in ("previews", "renders", "masters", "assets", "thumbnails", "models", "projects"):
        (storage_dir / sub).mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr(settings, "STORAGE_DIR", str(storage_dir))
    monkeypatch.setattr(settings, "GEMINI_API_KEY", "")
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    monkeypatch.setattr(main_mod, "get_persisted_settings", lambda: {})
    db_path = tmp_path / "phase2_bf.db"
    test_engine = create_engine(f"sqlite:///{db_path}", connect_args={"check_same_thread": False})
    SQLModel.metadata.create_all(test_engine)
    monkeypatch.setattr(app_db, "engine", test_engine)
    return test_engine, storage_dir


def test_best_frame_engine_disqualifies_bad_frames_and_assigns_roles(tmp_path: Path):
    # 0.0..0.35s: dark fade black opener -> should trigger suggested_start_time snap forward <= 0.60s
    black_fr = np.full((720, 1280, 3), 5, dtype=np.uint8)

    # 0.35..1.7s: crisp open-eyed clean portrait studio frame
    clean_fr = np.full((720, 1280, 3), (75, 88, 115), dtype=np.uint8)
    for gx in range(80, 1200, 90):
        cv2.line(clean_fr, (gx, 30), (gx, 690), (140, 155, 185), 2)
    _draw_portrait(clean_fr, 640, 340, scale=1.35, shirt_bgr=(55, 95, 200))

    # 1.7..3.0s: blurry frame (GaussianBlur 31x31) -> strictly disqualified
    blur_fr = cv2.GaussianBlur(clean_fr, (31, 31), sigmaX=12.0)

    # 3.0..4.3s: sharp closed-eyes blinking portrait -> strictly disqualified when face eyes_open < 0.65
    blink_fr = np.full((720, 1280, 3), (75, 88, 115), dtype=np.uint8)
    for gx in range(80, 1200, 90):
        cv2.line(blink_fr, (gx, 30), (gx, 690), (140, 155, 185), 2)
    _draw_blinking_portrait(blink_fr, 640, 340, scale=1.35)

    # 4.3..5.6s: crisp open-eyed portrait at rule-of-thirds peak -> clean anchor / peak_emotion
    emo_fr = np.full((720, 1280, 3), (82, 92, 122), dtype=np.uint8)
    for gx in range(80, 1200, 90):
        cv2.line(emo_fr, (gx, 30), (gx, 690), (150, 165, 195), 2)
    _draw_portrait(emo_fr, 640, 330, scale=1.40, shirt_bgr=(65, 130, 220))

    # 5.6..7.0s: fast horizontal motion across colorful crisp objects -> peak_action
    act_a = np.full((720, 1280, 3), (185, 160, 45), dtype=np.uint8)
    for gy in range(80, 640, 80):
        cv2.line(act_a, (60, gy), (1220, gy), (240, 240, 240), 2)
    cv2.rectangle(act_a, (160, 160), (480, 540), (35, 50, 225), -1)
    cv2.rectangle(act_a, (200, 220), (440, 480), (245, 245, 245), 4)
    act_b = np.full((720, 1280, 3), (185, 160, 45), dtype=np.uint8)
    for gy in range(80, 640, 80):
        cv2.line(act_b, (60, gy), (1220, gy), (240, 240, 240), 2)
    cv2.rectangle(act_b, (760, 160), (1080, 540), (35, 50, 225), -1)
    cv2.rectangle(act_b, (800, 220), (1040, 480), (245, 245, 245), 4)

    mp4 = _encode_sequence_mp4(
        tmp_path / "bf_multi.mp4",
        [(black_fr, 0.35), (clean_fr, 1.35), (blur_fr, 1.3), (blink_fr, 1.3), (emo_fr, 1.3), (act_a, 0.7), (act_b, 0.7)],
        fps=8,
    )

    vid_analysis = analyze_video_track(str(mp4), sample_fps=4.0)
    # Verify our blinking portrait genuinely dropped eyes_open_confidence below 0.65
    blink_frames = [f for f in vid_analysis["frames"] if 3.2 <= f["t"] <= 4.1 and f["face_count"] > 0]
    assert blink_frames, "Expected face detection on sharp blinking frame"
    assert min(f["eyes_open_confidence"] for f in blink_frames) < 0.65

    aud_mock = {
        "summary": {"integrated_lufs": -18.0, "lra": 4.0, "true_peak_db": -1.5, "avg_wpm": 150.0},
        "timeline": [
            {"t": round(i * 0.25, 2), "excitement_score": 88.0 if 4.25 <= i * 0.25 <= 5.5 else 35.0}
            for i in range(28)
        ],
    }

    bf_out_dir = tmp_path / "bf_stills"
    res = extract_best_frames(
        video_path=str(mp4),
        video_analysis=vid_analysis,
        audio_analysis=aud_mock,
        start_time=0.0,
        end_time=7.0,
        output_dir=str(bf_out_dir),
        clip_id=10,
        project_id=1,
    )

    assert res["rejected_counts"]["dark"] >= 1
    assert res["rejected_counts"]["blurry"] >= 1
    assert res["rejected_counts"]["blink"] >= 1
    assert res["suggested_start_time"] is not None
    assert 0.35 <= res["suggested_start_time"] <= 0.65

    picks = res["frames"]
    assert 2 <= len(picks) <= 6
    # Strictly zero bad frames allowed into Top 6
    for p in picks:
        assert p["sharpness_score"] >= 35.0
        assert p["exposure_score"] >= 30.0
        if p["face_count"] > 0:
            assert p["eyes_open_confidence"] >= 0.65
        assert p["image_path"] and os.path.exists(p["image_path"])
        with Image.open(p["image_path"]) as im:
            assert im.size == (1280, 720)

    # Temporal NMS min_gap >= 1.2s strictly satisfied
    ts_sorted = sorted(p["t"] for p in picks)
    for a, b in zip(ts_sorted, ts_sorted[1:]):
        assert (b - a) >= 1.19

    roles = {p["role"] for p in picks}
    assert "clean_opener" in roles
    assert "peak_emotion" in roles
    assert "peak_action" in roles


def test_multimodal_fusion_clipper_thumbnail_and_api_integration(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
):
    test_engine, storage_dir = _setup_db(tmp_path, monkeypatch)

    wav = _write_synthetic_wav_with_burst(tmp_path / "mm_audio.wav")
    words = [
        {"word": "How", "start": 1.55, "end": 1.72},
        {"word": "to", "start": 1.74, "end": 1.88},
        {"word": "win?", "start": 1.90, "end": 2.20},
        {"word": "Secret!", "start": 2.25, "end": 2.65},
    ]

    sharp = np.full((720, 1280, 3), (85, 95, 120), dtype=np.uint8)
    for gx in range(80, 1200, 90):
        cv2.line(sharp, (gx, 30), (gx, 690), (150, 165, 195), 2)
    _draw_portrait(sharp, 640, 340, scale=1.35, shirt_bgr=(55, 110, 210))
    blur = cv2.GaussianBlur(sharp, (31, 31), sigmaX=12.0)

    vid = _encode_sequence_mp4(tmp_path / "mm_vid.mp4", [(blur, 1.5), (sharp, 2.5)], fps=8)

    aud = analyze_audio_track(str(wav), word_segments=words)
    vis = analyze_video_track(str(vid), sample_fps=4.0)
    fused = fuse_multimodal_tracks(aud, vis, word_segments=words, duration=4.0)

    tl = fused["multimodal_timeline"]
    assert len(tl) >= 15
    # Verify exact 0.40/0.30/0.30 weighted fusion formula
    sample = tl[min(8, len(tl) - 1)]
    expected = round(
        0.40 * sample["semantic_score"]
        + 0.30 * sample["audio_prosody_score"]
        + 0.30 * sample["visual_emotion_action_score"],
        1,
    )
    assert abs(sample["combined_engagement_score"] - expected) <= 0.2

    early_pts = [p["combined_engagement_score"] for p in tl if p["t"] <= 1.0]
    burst_pts = [p["combined_engagement_score"] for p in tl if 1.6 <= p["t"] <= 2.6]
    assert sum(burst_pts) / len(burst_pts) > sum(early_pts) / len(early_pts) + 15.0

    # Offline clipper uses multimodal_context cleanly
    clips_mm = extract_viral_clips_offline(
        audio_wav_path=str(wav),
        transcript_words=words,
        duration_sec=4.0,
        duration_preset="Auto",
        clip_count=2,
        multimodal_context=fused,
    )
    assert clips_mm and "Multimodal" in clips_mm[0]["viral_rationale"]

    with Session(test_engine) as session:
        proj = Project(title="MM API Test", source_path=str(vid), duration=4.0, status="finished")
        session.add(proj)
        session.commit()
        session.refresh(proj)

        clip = ViralClip(
            project_id=proj.id,
            start_time=0.0,
            end_time=4.0,
            duration=4.0,
            virality_score=85,
        )
        session.add(clip)
        session.commit()
        session.refresh(clip)

        bf_endpoint_res = main_mod.get_clip_best_frames_endpoint(proj.id, clip.id, session=session)
        assert bf_endpoint_res["frames"]
        assert bf_endpoint_res["multimodal_timeline"]

        # Thumbnail auto-defaults to peak_emotion best frame when timestamp=None
        peak_emo = next(
            (bf for bf in bf_endpoint_res["frames"] if bf["role"] == "peak_emotion"),
            bf_endpoint_res["frames"][0],
        )
        thumb = generate_depth_thumbnail(
            video_path=str(vid),
            clip_id=clip.id,
            project_id=proj.id,
            timestamp=None,
            clip_start_time=0.0,
            clip_duration=4.0,
            headline="PEAK FRAME TEST",
            best_frames=bf_endpoint_res["frames"],
        )
        assert abs(thumb["timestamp"] - round(peak_emo["t"], 2)) <= 0.05

        # Reframer accepts best_frames without breaking framing parity
        ft, corner, vm = prepare_clip_framing_data(
            str(vid), [(0.0, 4.0)], frame_layout="auto_track", best_frames=bf_endpoint_res["frames"]
        )
        assert ft.get("keyframes")
        assert isinstance(vm, dict)

        img_resp = main_mod.get_clip_best_frame_image_endpoint(proj.id, clip.id, 0, session=session)
        assert os.path.exists(img_resp.path)
