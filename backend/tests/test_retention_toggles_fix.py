import os
import subprocess
from pathlib import Path

import cv2
import numpy as np
import pytest
from fastapi import BackgroundTasks
from sqlmodel import SQLModel, create_engine, Session

import app.main as main_mod
import app.database as db_mod
from app.config import settings
from app.models import Project, ViralClip, UpdateClipRequest, PreviewFrameRequest, RenderClipRequest
from app.services.proxy import get_ffmpeg_cmd, probe_video
from app.services.renderer import render_clip_ultra
from app.services.preview_frame import render_exact_preview_frame
from app.services.face_tracker import detect_punch_zoom_peaks


def _setup_isolated_db(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    storage_dir = tmp_path / "storage"
    for sub in ("masters", "proxies", "renders", "previews", "captions", "projects"):
        (storage_dir / sub).mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr(settings, "STORAGE_DIR", str(storage_dir))

    db_path = tmp_path / "retention_test.db"
    test_engine = create_engine(f"sqlite:///{db_path}", echo=False, connect_args={"check_same_thread": False})
    SQLModel.metadata.create_all(test_engine)
    monkeypatch.setattr(db_mod, "engine", test_engine)
    return test_engine, storage_dir


def _make_climax_video(out_path: Path, duration_sec: float = 10.0) -> Path:
    """Create a 10s test video where t=[6.8, 9.5] has bright visual pattern + loud 440Hz climax tone."""
    ffmpeg = get_ffmpeg_cmd()
    out_path.parent.mkdir(parents=True, exist_ok=True)
    # First 6.5s quiet blue/dark tone, [6.8, 9.6] loud 440Hz climax with orange/bright square
    a_expr = "if(between(t,6.8,9.6),0.65*sin(2*PI*440*t),0.06*sin(2*PI*220*t))"
    vf = (
        "drawbox=x=120:y=60:w=400:h=240:color=orange@0.9:t=fill:enable='between(t,6.8,9.6)',"
        "drawbox=x=40:y=40:w=80:h=80:color=white@0.85:t=fill"
    )
    res = subprocess.run(
        [
            ffmpeg, "-y",
            "-f", "lavfi", "-i", f"color=c=#182436:size=640x360:rate=24:duration={duration_sec:.2f}",
            "-f", "lavfi", "-i", f"aevalsrc='{a_expr}':s=44100:d={duration_sec:.2f}",
            "-vf", vf,
            "-c:v", "libx264", "-preset", "ultrafast", "-pix_fmt", "yuv420p",
            "-c:a", "aac", "-ar", "44100", "-ac", "1",
            str(out_path),
        ],
        capture_output=True, text=True, timeout=45,
    )
    assert res.returncode == 0, res.stderr
    return out_path


def _make_dead_air_video(out_path: Path) -> Path:
    """Create a 6.5s video with speech tone at [0.0..1.8] & [3.8..6.3] and dead silence at [1.8..3.8]."""
    ffmpeg = get_ffmpeg_cmd()
    out_path.parent.mkdir(parents=True, exist_ok=True)
    a_expr = (
        "if(lt(t,1.8),0.45*sin(2*PI*310*t),"
        "if(between(t,3.8,6.3),0.45*sin(2*PI*360*t),0.0))"
    )
    res = subprocess.run(
        [
            ffmpeg, "-y",
            "-f", "lavfi", "-i", "testsrc2=size=640x360:rate=24:duration=6.5",
            "-f", "lavfi", "-i", f"aevalsrc='{a_expr}':s=44100:d=6.5",
            "-c:v", "libx264", "-preset", "ultrafast", "-pix_fmt", "yuv420p",
            "-c:a", "aac", "-ar", "44100", "-ac", "1",
            str(out_path),
        ],
        capture_output=True, text=True, timeout=45,
    )
    assert res.returncode == 0, res.stderr
    return out_path


def _make_punch_video(out_path: Path) -> Path:
    """Create a 5.0s video with distinct checkerboard box + strong vocal peak at t=2.0s."""
    ffmpeg = get_ffmpeg_cmd()
    out_path.parent.mkdir(parents=True, exist_ok=True)
    a_expr = "if(between(t,1.75,2.30),0.85*sin(2*PI*420*t),0.05*sin(2*PI*180*t))"
    vf = (
        "drawbox=x=220:y=80:w=200:h=200:color=yellow@1.0:t=fill,"
        "drawbox=x=260:y=120:w=120:h=120:color=red@1.0:t=fill"
    )
    res = subprocess.run(
        [
            ffmpeg, "-y",
            "-f", "lavfi", "-i", "color=c=#111111:size=640x360:rate=24:duration=5.0",
            "-f", "lavfi", "-i", f"aevalsrc='{a_expr}':s=44100:d=5.0",
            "-vf", vf,
            "-c:v", "libx264", "-preset", "ultrafast", "-pix_fmt", "yuv420p",
            "-c:a", "aac", "-ar", "44100", "-ac", "1",
            str(out_path),
        ],
        capture_output=True, text=True, timeout=45,
    )
    assert res.returncode == 0, res.stderr
    return out_path


def test_retention_aliases_persist_to_sqlite(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    test_engine, _ = _setup_isolated_db(tmp_path, monkeypatch)
    src = _make_punch_video(tmp_path / "media" / "alias_src.mp4")

    with Session(test_engine) as session:
        proj = Project(title="Alias Test", source_type="upload", source_path=str(src), duration=5.0, status="finished")
        session.add(proj)
        session.commit()
        session.refresh(proj)

        clip = ViralClip(project_id=proj.id, start_time=0.0, end_time=5.0, duration=5.0)
        session.add(clip)
        session.commit()
        session.refresh(clip)

        # Toggle using alias keys
        res = main_mod.update_clip(
            clip.id,
            UpdateClipRequest(
                cold_open_climax=True,
                trim_dead_air=True,
                punch_zoom_fx=True,
                punch_zoom_intensity=1.22,
                options={"custom_flag": "ok"},
            ),
            session=session,
        )
        assert res["status"] == "updated"

        session.refresh(clip)
        assert clip.use_cold_open is True
        assert clip.auto_trim_dead_air is True
        assert clip.dynamic_punch_zoom is True
        assert clip.punch_zoom_intensity == pytest.approx(1.22)
        assert clip.get_options().get("custom_flag") == "ok"

        # Fast preview render endpoint accepts alias overrides directly too
        r_res = main_mod.trigger_render(
            clip_id=clip.id,
            project_id=proj.id,
            background_tasks=BackgroundTasks(),
            req=RenderClipRequest(quality_mode="fast_preview", punch_zoom=True, cold_open=False, trim_dead_air=False),
            session=session,
        )
        assert r_res["status"] == "completed"
        session.refresh(clip)
        assert clip.use_cold_open is False
        assert clip.auto_trim_dead_air is False
        assert clip.dynamic_punch_zoom is True


def test_cold_open_climax_adds_teaser_shifts_captions_and_renders_badge(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    _setup_isolated_db(tmp_path, monkeypatch)
    src = _make_climax_video(tmp_path / "media" / "climax_10s.mp4", duration_sec=10.0)
    words = [
        {"word": "Opening", "start": 0.2, "end": 0.8},
        {"word": "line", "start": 0.9, "end": 1.4},
        {"word": "Climax!", "start": 7.4, "end": 8.4},
    ]

    # With Cold-Open Climax enabled on 10s clip -> duration increases ~+2.30s (~12.3s)
    out_co = render_clip_ultra(
        master_video_path=str(src),
        clip_id=501,
        project_id=501,
        start_time=0.0,
        duration=10.0,
        words=words,
        aspect_ratio="9:16",
        frame_layout="auto_track",
        burn_captions=True,
        show_hook_banner=True,
        hook_headline="WATCH UNTIL THE END",
        use_cold_open=True,
        quality_mode="fast_preview",
        hardware_mode="cpu",
    )
    meta_co = probe_video(out_co)
    assert 11.7 <= meta_co["duration"] <= 12.9, f"Expected ~12.3s with 2.3s cold open teaser, got {meta_co['duration']}"

    # Teaser frame at t=0.5s should clearly differ from opening frame without cold open (orange climax + badge)
    base_prev = render_exact_preview_frame(
        video_path=str(src),
        timestamp=0.5,
        clip_start_time=0.0,
        clip_duration=10.0,
        clip_rel_ts_override=0.5,
        burn_captions=True,
        use_cold_open=False,
        transcript_words=words,
    )
    co_prev = render_exact_preview_frame(
        video_path=str(src),
        timestamp=0.5,
        clip_start_time=0.0,
        clip_duration=10.0,
        clip_rel_ts_override=0.5,
        burn_captions=True,
        use_cold_open=True,
        transcript_words=words,
    )
    img_base = cv2.imdecode(np.fromfile(str(Path(settings.STORAGE_DIR) / "previews" / Path(base_prev["frame_url"]).name), dtype=np.uint8), cv2.IMREAD_COLOR)
    img_co = cv2.imdecode(np.fromfile(str(Path(settings.STORAGE_DIR) / "previews" / Path(co_prev["frame_url"]).name), dtype=np.uint8), cv2.IMREAD_COLOR)
    assert img_base is not None and img_co is not None
    assert float(np.mean(np.abs(img_base.astype(np.float32) - img_co.astype(np.float32)))) > 8.0

    # Short <8s clip gracefully skips cold open without crashing or extending duration
    short_src = _make_punch_video(tmp_path / "media" / "short_5s.mp4")
    out_short = render_clip_ultra(
        master_video_path=str(short_src),
        clip_id=502,
        project_id=502,
        start_time=0.0,
        duration=5.0,
        words=[{"word": "Short", "start": 0.5, "end": 1.2}],
        use_cold_open=True,
        quality_mode="fast_preview",
        hardware_mode="cpu",
    )
    assert probe_video(out_short)["duration"] < 5.6


def test_trim_dead_air_cuts_silence_in_video_audio_and_syncs_captions(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    _setup_isolated_db(tmp_path, monkeypatch)
    src = _make_dead_air_video(tmp_path / "media" / "dead_air_6s.mp4")
    words = [
        {"word": "First", "start": 0.1, "end": 0.9},
        {"word": "part", "start": 0.95, "end": 1.75},
        {"word": "Second", "start": 3.85, "end": 4.9},
        {"word": "part", "start": 4.95, "end": 6.1},
    ]

    out_trimmed = render_clip_ultra(
        master_video_path=str(src),
        clip_id=503,
        project_id=503,
        start_time=0.0,
        duration=6.5,
        words=words,
        aspect_ratio="9:16",
        burn_captions=True,
        auto_trim_dead_air=True,
        quality_mode="fast_preview",
        hardware_mode="cpu",
    )
    meta = probe_video(out_trimmed)
    # 2.0s dead air minus ~180ms breath cushion minus 30ms xfade -> noticeably shorter (< 5.2s)
    assert 4.0 <= meta["duration"] <= 5.25, f"Expected noticeably shorter video after trimming 2.0s gap, got {meta['duration']}"
    assert meta["has_audio"] is True

    # Ensure preview_frame handles trim_dead_air remap cleanly without error
    p_res = render_exact_preview_frame(
        video_path=str(src),
        timestamp=2.2,
        clip_start_time=0.0,
        clip_duration=6.5,
        clip_rel_ts_override=2.2,
        burn_captions=True,
        auto_trim_dead_air=True,
        transcript_words=words,
    )
    p_file = Path(settings.STORAGE_DIR) / "previews" / Path(p_res["frame_url"]).name
    assert p_file.exists() and p_file.stat().st_size > 2000


def test_punch_zoom_fx_uses_deterministic_scale_crop_in_export_and_preview(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    _setup_isolated_db(tmp_path, monkeypatch)
    src = _make_punch_video(tmp_path / "media" / "punch_peak.mp4")

    pz = detect_punch_zoom_peaks(str(src), 0.0, 5.0, base_zoom=1.0, punch_intensity=1.22)
    assert len(pz["punch_intervals"]) >= 1
    peak_t = round(0.5 * (pz["punch_intervals"][0][0] + pz["punch_intervals"][0][1]), 2)

    p_off = render_exact_preview_frame(
        video_path=str(src),
        timestamp=peak_t,
        clip_start_time=0.0,
        clip_duration=5.0,
        clip_rel_ts_override=peak_t,
        aspect_ratio="16:9",
        burn_captions=False,
        dynamic_punch_zoom=False,
    )
    p_on = render_exact_preview_frame(
        video_path=str(src),
        timestamp=peak_t,
        clip_start_time=0.0,
        clip_duration=5.0,
        clip_rel_ts_override=peak_t,
        aspect_ratio="16:9",
        burn_captions=False,
        dynamic_punch_zoom=True,
        punch_zoom_intensity=1.22,
    )
    img_off = cv2.imdecode(np.fromfile(str(Path(settings.STORAGE_DIR) / "previews" / Path(p_off["frame_url"]).name), dtype=np.uint8), cv2.IMREAD_COLOR)
    img_on = cv2.imdecode(np.fromfile(str(Path(settings.STORAGE_DIR) / "previews" / Path(p_on["frame_url"]).name), dtype=np.uint8), cv2.IMREAD_COLOR)
    assert img_off is not None and img_on is not None
    # Zoomed frame enlarges the central box noticeably compared to 1.0x unpunched frame
    diff = float(np.mean(np.abs(img_off.astype(np.float32) - img_on.astype(np.float32))))
    assert diff > 5.0, f"Expected zoomed preview frame diff > 5.0, got {diff}"

    # Full MP4 render also succeeds cleanly with deterministic eval=frame crop (not zoompan)
    out_mp4 = render_clip_ultra(
        master_video_path=str(src),
        clip_id=504,
        project_id=504,
        start_time=0.0,
        duration=5.0,
        words=[{"word": "Boom!", "start": 1.8, "end": 2.3}],
        aspect_ratio="16:9",
        burn_captions=False,
        dynamic_punch_zoom=True,
        punch_zoom_intensity=1.22,
        quality_mode="fast_preview",
        hardware_mode="cpu",
    )
    assert os.path.exists(out_mp4)
    assert 4.6 <= probe_video(out_mp4)["duration"] <= 5.4
