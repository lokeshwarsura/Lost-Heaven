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
from app.main import generate_thumbnail_endpoint, update_clip
from app.models import GenerateThumbnailRequest, Project, UpdateClipRequest, ViralClip
from app.services.bg_segmentation import segment_foreground_mask
from app.services.preview_frame import render_exact_preview_frame
from app.services.privacy_blur import track_roi_frames
from app.services.proxy import get_ffmpeg_cmd, probe_video
from app.services.renderer import render_clip_ultra


def _setup_isolated_db(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    storage_dir = tmp_path / "storage"
    for sub in ("renders", "masters", "previews", "assets", "thumbnails", "models"):
        (storage_dir / sub).mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr(settings, "STORAGE_DIR", str(storage_dir))
    monkeypatch.setattr(settings, "GEMINI_API_KEY", "")
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    monkeypatch.setattr(main_mod, "get_persisted_settings", lambda: {})

    db_path = tmp_path / "vision_v4.db"
    test_engine = create_engine(
        f"sqlite:///{db_path}",
        connect_args={"check_same_thread": False},
    )
    SQLModel.metadata.create_all(test_engine)
    monkeypatch.setattr(app_db, "engine", test_engine)
    return test_engine, storage_dir


def _create_portrait_subject_mp4(out_path: Path, duration_sec: float = 3.0) -> Path:
    """Create a synthetic AV clip with a high-contrast central foreground block over bright checkered background."""
    out_path.parent.mkdir(parents=True, exist_ok=True)
    ffmpeg = get_ffmpeg_cmd()
    # Center box + colorful moving patterns so backdrop segmentation & pixelate/gaussian filters show clear pixel delta
    vf = (
        "drawbox=x=210:y=60:w=220:h=260:color=0x2B4C7E@1.0:t=fill,"
        "drawbox=x=265:y=85:w=110:h=110:color=0xF1C27D@1.0:t=fill"
    )
    cmd = [
        ffmpeg, "-y",
        "-f", "lavfi", "-i", f"testsrc2=size=640x360:rate=20:duration={duration_sec}",
        "-f", "lavfi", "-i", f"sine=frequency=440:sample_rate=44100:duration={duration_sec}",
        "-vf", vf,
        "-c:v", "libx264", "-preset", "ultrafast", "-pix_fmt", "yuv420p",
        "-c:a", "aac", "-shortest",
        str(out_path),
    ]
    res = subprocess.run(cmd, capture_output=True, text=True, timeout=60)
    assert res.returncode == 0, res.stderr
    return out_path


def test_phase1_virtual_studio_backdrop_segmentation_preview_and_render(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
):
    test_engine, storage_dir = _setup_isolated_db(tmp_path, monkeypatch)
    video_path = _create_portrait_subject_mp4(tmp_path / "media" / "subject.mp4", duration_sec=3.0)

    # 1. Segmentation mask sanity on synthetic portrait frame
    cap = cv2.VideoCapture(str(video_path))
    ok, frame = cap.read()
    cap.release()
    assert ok and frame is not None
    matte = segment_foreground_mask(frame)
    assert matte.shape == frame.shape[:2]
    assert int(matte.max()) > 200

    # 2. Preview frame parity: none vs solid_charcoal vs studio_blur
    prev_orig = render_exact_preview_frame(
        video_path=str(video_path),
        timestamp=0.5,
        aspect_ratio="9:16",
        frame_layout="center",
        burn_captions=False,
        backdrop_mode="none",
    )
    prev_charcoal = render_exact_preview_frame(
        video_path=str(video_path),
        timestamp=0.5,
        aspect_ratio="9:16",
        frame_layout="center",
        burn_captions=False,
        backdrop_mode="solid_charcoal",
        backdrop_color="#18181B",
    )
    p_orig_file = storage_dir / "previews" / Path(prev_orig["frame_url"]).name
    p_char_file = storage_dir / "previews" / Path(prev_charcoal["frame_url"]).name
    assert p_orig_file.exists() and p_char_file.exists()
    with Image.open(p_orig_file) as i1, Image.open(p_char_file) as i2:
        assert i1.size == i2.size
        assert i1.tobytes() != i2.tobytes()
        # Outer top-left corner should mirror charcoal studio tint (~24,24,27)
        c_px = i2.getpixel((10, 10))
        assert max(c_px[0], c_px[1], c_px[2]) < 55

    # 3. Full FFmpeg alphamerge + overlay export render
    with Session(test_engine) as session:
        proj = Project(
            title="Backdrop Render Test",
            source_type="upload",
            source_path=str(video_path),
            duration=3.0,
            status="finished",
        )
        session.add(proj)
        session.commit()
        session.refresh(proj)

        out_mp4 = str(storage_dir / "backdrop_out.mp4")
        rendered = render_clip_ultra(
            master_video_path=str(video_path),
            clip_id=101,
            start_time=0.0,
            duration=2.5,
            words=[{"word": "Studio", "start": 0.2, "end": 1.4}],
            project_id=proj.id,
            aspect_ratio="9:16",
            frame_layout="center",
            burn_captions=True,
            backdrop_mode="accent_gradient",
            backdrop_color="#6366F1",
            hardware_mode="cpu",
            quality_mode="fast_preview",
            output_path_override=out_mp4,
        )
        assert os.path.exists(rendered)
        meta = probe_video(rendered)
        assert meta["duration"] >= 2.1
        assert meta["width"] == 1080 and meta["height"] == 1920


def test_phase1_depth_layer_thumbnail_studio_9x16_and_16x9(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
):
    test_engine, storage_dir = _setup_isolated_db(tmp_path, monkeypatch)
    video_path = _create_portrait_subject_mp4(tmp_path / "media" / "thumb_src.mp4", duration_sec=3.0)

    with Session(test_engine) as session:
        proj = Project(
            title="Thumb Project",
            source_type="upload",
            source_path=str(video_path),
            duration=3.0,
            status="finished",
        )
        session.add(proj)
        session.commit()
        session.refresh(proj)

        clip = ViralClip(
            project_id=proj.id,
            title="Cover Clip",
            hook_headline="UNBELIEVABLE REVEAL",
            start_time=0.0,
            end_time=3.0,
            duration=3.0,
            frame_layout="center",
        )
        session.add(clip)
        session.commit()
        session.refresh(clip)

        r_9x16 = generate_thumbnail_endpoint(
            proj.id,
            clip.id,
            GenerateThumbnailRequest(
                timestamp=0.6,
                headline="UNBELIEVABLE REVEAL",
                format="9:16",
                depth_layer=True,
                accent_color="#C6F32F",
                rim_glow=True,
                badge_text="VIRAL",
            ),
            session=session,
        )
        assert r_9x16["status"] == "generated"
        assert r_9x16["width"] == 1080 and r_9x16["height"] == 1920
        assert os.path.exists(r_9x16["png_path"]) and os.path.exists(r_9x16["jpg_path"])
        with Image.open(r_9x16["png_path"]) as img9:
            assert img9.size == (1080, 1920)

        r_flat = generate_thumbnail_endpoint(
            proj.id,
            clip.id,
            GenerateThumbnailRequest(
                timestamp=0.6,
                headline="UNBELIEVABLE REVEAL",
                format="9:16",
                depth_layer=False,
                rim_glow=False,
            ),
            session=session,
        )
        with Image.open(r_9x16["png_path"]) as im_depth, Image.open(r_flat["png_path"]) as im_flat:
            assert im_depth.tobytes() != im_flat.tobytes()

        r_16x9 = generate_thumbnail_endpoint(
            proj.id,
            clip.id,
            GenerateThumbnailRequest(
                headline="WIDE COVER",
                format="16:9",
                depth_layer=True,
            ),
            session=session,
        )
        assert r_16x9["width"] == 1280 and r_16x9["height"] == 720
        with Image.open(r_16x9["jpg_path"]) as img16:
            assert img16.size == (1280, 720)


def test_phase1_tracked_privacy_blur_gaussian_and_pixelate_render(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
):
    test_engine, storage_dir = _setup_isolated_db(tmp_path, monkeypatch)
    video_path = _create_portrait_subject_mp4(tmp_path / "media" / "blur_src.mp4", duration_sec=2.5)

    # 1. Verify OpenCV tracker follows a moving synthetic marker horizontally
    frames_mv = []
    for fi in range(6):
        fr = np.full((180, 320, 3), 30, dtype=np.uint8)
        cx = 50 + fi * 24
        cv2.rectangle(fr, (cx, 50), (cx + 48, 86), (255, 220, 40), -1)
        cv2.putText(fr, "HUD", (cx + 4, 75), cv2.FONT_HERSHEY_SIMPLEX, 0.5, (10, 10, 10), 2)
        frames_mv.append(fr)

    kfs = track_roi_frames(
        frames_mv,
        fps=6.0,
        box={"x": 50 / 320.0, "y": 50 / 180.0, "w": 48 / 320.0, "h": 36 / 180.0, "track_motion": True},
    )
    assert len(kfs) == 6
    assert kfs[-1]["x"] > kfs[0]["x"] + 0.12

    # 2. Verify preview & persistence + full video render with both Gaussian & Pixelate boxes
    boxes = [
        {"id": "b1", "x": 0.12, "y": 0.15, "w": 0.40, "h": 0.22, "mode": "pixelate", "track_motion": True, "intensity": 18},
        {"id": "b2", "x": 0.45, "y": 0.62, "w": 0.35, "h": 0.14, "mode": "gaussian", "track_motion": False, "intensity": 20},
    ]
    prev_plain = render_exact_preview_frame(
        video_path=str(video_path),
        timestamp=0.5,
        aspect_ratio="9:16",
        frame_layout="center",
        burn_captions=False,
    )
    prev_blurred = render_exact_preview_frame(
        video_path=str(video_path),
        timestamp=0.5,
        aspect_ratio="9:16",
        frame_layout="center",
        burn_captions=False,
        privacy_blur_boxes=boxes,
    )
    f_plain = storage_dir / "previews" / Path(prev_plain["frame_url"]).name
    f_blur = storage_dir / "previews" / Path(prev_blurred["frame_url"]).name
    with Image.open(f_plain) as ip, Image.open(f_blur) as ib:
        assert ip.tobytes() != ib.tobytes()

    with Session(test_engine) as session:
        proj = Project(
            title="Blur Project",
            source_type="upload",
            source_path=str(video_path),
            duration=2.5,
            status="finished",
        )
        session.add(proj)
        session.commit()
        session.refresh(proj)

        clip = ViralClip(
            project_id=proj.id,
            title="Privacy Clip",
            start_time=0.0,
            end_time=2.5,
            duration=2.5,
            aspect_ratio="9:16",
            frame_layout="center",
            hardware_mode="cpu",
        )
        session.add(clip)
        session.commit()
        session.refresh(clip)

        update_clip(clip.id, UpdateClipRequest(privacy_blur_boxes=boxes), session=session)
        session.refresh(clip)
        assert len(clip.get_privacy_blur_boxes()) == 2

        out_mp4 = str(storage_dir / "privacy_blur_out.mp4")
        rendered = render_clip_ultra(
            master_video_path=str(video_path),
            clip_id=clip.id,
            start_time=0.0,
            duration=2.5,
            words=[],
            project_id=proj.id,
            aspect_ratio="9:16",
            frame_layout="center",
            burn_captions=False,
            privacy_blur_boxes=boxes,
            hardware_mode="cpu",
            quality_mode="fast_preview",
            output_path_override=out_mp4,
        )
        assert os.path.exists(rendered)
        meta = probe_video(rendered)
        assert meta["duration"] >= 2.1


def test_phase2_beat_sync_and_spectrum_visualizer(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
):
    from app.services.beat_sync import (
        analyze_pcm_beats_and_spectrum,
        snap_punch_and_broll_to_beats,
        snap_timestamps_to_beats,
    )
    from app.services.caption_renderer import render_caption_frame

    test_engine, storage_dir = _setup_isolated_db(tmp_path, monkeypatch)

    # 1. Pure-NumPy BPM + onset detection on synthetic 120 BPM (0.5s beat interval) kick pulses
    sr = 22050
    dur = 3.5
    t_arr = np.linspace(0.0, dur, int(sr * dur), endpoint=False, dtype=np.float32)
    samples = np.random.default_rng(7).normal(0.0, 0.005, size=t_arr.shape).astype(np.float32)
    for beat_t in (0.5, 1.0, 1.5, 2.0, 2.5, 3.0):
        idx = int(beat_t * sr)
        pulse_len = int(0.09 * sr)
        env = np.exp(-np.linspace(0.0, 6.0, pulse_len, dtype=np.float32))
        pulse_t = np.linspace(0.0, 0.09, pulse_len, endpoint=False, dtype=np.float32)
        samples[idx:idx + pulse_len] += 0.75 * env * np.sin(2.0 * np.pi * 110.0 * pulse_t)

    analysis = analyze_pcm_beats_and_spectrum(samples, sample_rate=sr, spectrum_fps=10)
    assert len(analysis["beats"]) >= 3
    assert 85.0 <= analysis["bpm"] <= 155.0
    assert len(analysis["spectrum_frames"]) >= 25
    assert len(analysis["spectrum_frames"][0]) == 24
    assert all(0.0 <= b <= 1.0 for b in analysis["spectrum_frames"][5])

    # 2. Snap zoom peaks & B-roll cutaways to nearest beat within +-0.25s
    snapped = snap_timestamps_to_beats([0.42, 1.11, 2.88], [0.5, 1.0, 2.0], max_delta=0.25)
    assert snapped == [0.5, 1.0, 2.88]

    new_punch, new_broll = snap_punch_and_broll_to_beats(
        {"punch_timestamps": [0.92], "punch_intervals": [{"start": 0.92, "end": 1.42}]},
        [{"start": 1.91, "end": 3.2}],
        beats=[0.5, 1.0, 2.0],
    )
    assert new_punch["punch_intervals"][0]["start"] == 1.0
    assert new_broll[0]["start"] == 2.0

    # 3. Spectrum visualizer Pillow overlay pixel parity (bottom, top, center)
    plain = render_caption_frame(t=0.5, width=360, height=640, words=[], burn_captions=False)
    for pos in ("bottom", "top", "center"):
        spec_img = render_caption_frame(
            t=0.5,
            width=360,
            height=640,
            words=[],
            burn_captions=False,
            spectrum_enabled=True,
            spectrum_position=pos,
            spectrum_color="#C6F32F",
            spectrum_data=analysis,
        )
        assert spec_img.tobytes() != plain.tobytes()

    # 4. End-to-end FFmpeg render with beat_sync + spectrum visualizer
    video_path = _create_portrait_subject_mp4(tmp_path / "media" / "beat_src.mp4", duration_sec=2.5)
    with Session(test_engine) as session:
        proj = Project(
            title="Beat Project",
            source_type="upload",
            source_path=str(video_path),
            duration=2.5,
            status="finished",
        )
        session.add(proj)
        session.commit()
        session.refresh(proj)

        out_mp4 = str(storage_dir / "beat_spectrum_out.mp4")
        rendered = render_clip_ultra(
            master_video_path=str(video_path),
            clip_id=201,
            start_time=0.0,
            duration=2.4,
            words=[{"word": "Drop", "start": 0.2, "end": 1.8}],
            project_id=proj.id,
            aspect_ratio="9:16",
            frame_layout="center",
            burn_captions=True,
            beat_sync_enabled=True,
            spectrum_enabled=True,
            spectrum_position="bottom",
            spectrum_color="#C6F32F",
            hardware_mode="cpu",
            quality_mode="fast_preview",
            output_path_override=out_mp4,
        )
        assert os.path.exists(rendered)
        assert probe_video(rendered)["duration"] >= 2.0


def test_phase2_animated_mini_chapter_pills_and_endpoint(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
):
    import shutil
    from app.main import generate_chapters_endpoint
    from app.models import GenerateChaptersRequest
    from app.services.caption_renderer import build_caption_ffconcat, render_caption_frame

    test_engine, _ = _setup_isolated_db(tmp_path, monkeypatch)
    words = [
        {"word": "First", "start": 0.1, "end": 0.4},
        {"word": "hook", "start": 0.4, "end": 0.8},
        {"word": "moment.", "start": 0.8, "end": 1.2},
        {"word": "Next", "start": 2.4, "end": 2.8},
        {"word": "big", "start": 2.8, "end": 3.2},
        {"word": "twist", "start": 3.2, "end": 3.7},
        {"word": "arrives.", "start": 3.7, "end": 4.2},
        {"word": "Final", "start": 5.5, "end": 5.9},
        {"word": "payoff", "start": 5.9, "end": 6.4},
        {"word": "hits.", "start": 6.4, "end": 6.9},
    ]

    with Session(test_engine) as session:
        proj = Project(
            title="Chapters Project",
            source_type="upload",
            source_path=str(tmp_path / "dummy.mp4"),
            duration=8.0,
            status="finished",
        )
        session.add(proj)
        session.commit()
        session.refresh(proj)

        clip = ViralClip(
            project_id=proj.id,
            title="Chapter Clip",
            start_time=0.0,
            end_time=7.5,
            duration=7.5,
        )
        clip.set_transcript(words)
        session.add(clip)
        session.commit()
        session.refresh(clip)

        res = generate_chapters_endpoint(
            proj.id,
            clip.id,
            GenerateChaptersRequest(max_chapters=4, enable=True),
            session=session,
        )
        assert res["status"] == "generated"
        assert 2 <= len(res["chapters"]) <= 4
        assert res["chapters_enabled"] is True

        session.refresh(clip)
        assert clip.chapters_enabled is True
        assert len(clip.get_chapters()) == len(res["chapters"])

    plain = render_caption_frame(t=0.4, width=360, height=640, words=[], burn_captions=False)
    pill_in = render_caption_frame(
        t=0.06,
        width=360,
        height=640,
        words=[],
        burn_captions=False,
        chapters_enabled=True,
        chapters=res["chapters"],
    )
    pill_full = render_caption_frame(
        t=0.40,
        width=360,
        height=640,
        words=[],
        burn_captions=False,
        chapters_enabled=True,
        chapters=res["chapters"],
    )
    assert pill_in.tobytes() != plain.tobytes()
    assert pill_full.tobytes() != pill_in.tobytes()

    cap_dir, ff_path, count = build_caption_ffconcat(
        words=[],
        clip_duration=7.5,
        width=360,
        height=640,
        burn_captions=False,
        chapters_enabled=True,
        chapters=res["chapters"],
    )
    try:
        assert ff_path.exists()
        assert count >= len(res["chapters"])
    finally:
        shutil.rmtree(cap_dir, ignore_errors=True)


def test_phase2_natural_room_tone_jump_cut_splicer(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
):
    from app.services.audio_mastering import extract_room_tone_sample
    from app.services.timeline_editor import build_timeline_trim_concat_filter

    test_engine, storage_dir = _setup_isolated_db(tmp_path, monkeypatch)
    video_path = _create_portrait_subject_mp4(tmp_path / "media" / "jumpcut_src.mp4", duration_sec=3.5)

    rt_dir = tmp_path / "rt_exec"
    rt_wav = extract_room_tone_sample(
        str(video_path),
        exec_dir=str(rt_dir),
        start_sec=0.0,
        duration_sec=3.0,
        sample_len=0.50,
    )
    assert rt_wav and os.path.exists(rt_wav)
    assert os.path.getsize(rt_wav) > 1000

    tl_fg, _, _ = build_timeline_trim_concat_filter(
        kept_segments=[(0.0, 1.1), (1.7, 3.1)],
        crossfade_sec=0.04,
        has_audio=True,
        multi_input=True,
    )
    assert "acrossfade=d=0.040:c1=qsin:c2=qsin" in tl_fg

    with Session(test_engine) as session:
        proj = Project(
            title="RoomTone Project",
            source_type="upload",
            source_path=str(video_path),
            duration=3.5,
            status="finished",
        )
        session.add(proj)
        session.commit()
        session.refresh(proj)

        out_mp4 = str(storage_dir / "roomtone_cut.mp4")
        rendered = render_clip_ultra(
            master_video_path=str(video_path),
            clip_id=202,
            start_time=0.0,
            duration=3.5,
            words=[
                {"word": "First", "start": 0.1, "end": 0.5},
                {"word": "part", "start": 0.5, "end": 0.9},
                {"word": "Second", "start": 1.8, "end": 2.4},
                {"word": "part", "start": 2.4, "end": 2.9},
            ],
            keep_segments=[[0.0, 1.1], [1.7, 3.1]],
            tighten_pacing=True,
            project_id=proj.id,
            aspect_ratio="9:16",
            frame_layout="center",
            burn_captions=True,
            hardware_mode="cpu",
            quality_mode="fast_preview",
            output_path_override=out_mp4,
        )
        assert os.path.exists(rendered)
        meta = probe_video(rendered)
        assert meta["has_audio"] is True
        assert meta["duration"] >= 2.0

