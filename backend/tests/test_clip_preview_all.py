import os
import subprocess
from pathlib import Path
from unittest.mock import patch

import pytest
from sqlmodel import Session, SQLModel, create_engine, select

import app.database as app_db
import app.services.job_manager as jm_module
from app.config import settings
from app.main import stream_latest_clip_video
from app.models import Project, ViralClip
from app.services.job_manager import JobManager
from app.services.proxy import get_ffmpeg_cmd


def _create_test_video(out_path: Path, duration_sec: float = 12.0) -> Path:
    out_path.parent.mkdir(parents=True, exist_ok=True)
    cmd = [
        get_ffmpeg_cmd(),
        "-y",
        "-f", "lavfi",
        "-i", f"testsrc2=size=640x360:rate=15:duration={duration_sec}",
        "-f", "lavfi",
        "-i", f"sine=frequency=330:sample_rate=44100:duration={duration_sec}",
        "-c:v", "libx264",
        "-preset", "ultrafast",
        "-pix_fmt", "yuv420p",
        "-c:a", "aac",
        "-shortest",
        str(out_path),
    ]
    proc = subprocess.run(cmd, capture_output=True, text=True, timeout=60)
    assert proc.returncode == 0, proc.stderr
    return out_path


def test_stage5_prerenders_all_clips_ready_with_valid_mp4(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """
    1. Stage 5 must pre-render valid playable MP4 files (rendered_path exists on disk,
       status == 'ready', render_status == 'completed') for ALL clips in a multi-clip project.
    """
    storage_dir = tmp_path / "storage"
    storage_dir.mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr(settings, "STORAGE_DIR", str(storage_dir))

    test_engine = create_engine(
        f"sqlite:///{tmp_path / 'test_all_clips.db'}",
        connect_args={"check_same_thread": False},
    )
    SQLModel.metadata.create_all(test_engine)
    monkeypatch.setattr(app_db, "engine", test_engine)
    monkeypatch.setattr(jm_module, "engine", test_engine)

    src_mp4 = _create_test_video(tmp_path / "master.mp4", duration_sec=12.0)

    project_id = 5101
    with Session(test_engine) as session:
        proj = Project(
            id=project_id,
            title="Multi-Clip Stage 5 Pre-Render",
            source_path=str(src_mp4),
            duration=12.0,
            default_aspect_ratio="9:16",
            hardware_mode="cpu",
        )
        session.add(proj)
        session.commit()

    # Prepare cached checkpoints so run_pipeline goes straight to Stage 4 -> Stage 5
    jm = JobManager()
    proj_dir = tmp_path / "proj_cache"
    proj_dir.mkdir(parents=True, exist_ok=True)
    audio_wav = proj_dir / "audio_16k.wav"
    subprocess.run(
        [get_ffmpeg_cmd(), "-y", "-i", str(src_mp4), "-ac", "1", "-ar", "16000", str(audio_wav)],
        capture_output=True,
        check=True,
        timeout=30,
    )
    sample_words = [
        {"word": "First", "start": 0.2, "end": 0.6},
        {"word": "clip", "start": 0.7, "end": 1.1},
        {"word": "Second", "start": 4.2, "end": 4.6},
        {"word": "clip", "start": 4.7, "end": 5.1},
        {"word": "Third", "start": 8.2, "end": 8.6},
        {"word": "clip", "start": 8.7, "end": 9.1},
    ]
    (proj_dir / "transcript.json").write_text(
        __import__("json").dumps(sample_words), encoding="utf-8"
    )

    mock_three_clips = [
        {
            "start_time": 0.0,
            "end_time": 2.0,
            "duration": 2.0,
            "virality_score": 95,
            "hook_score": 92,
            "retention_score": 90,
            "emotional_score": 88,
            "standalone_score": 93,
            "viral_rationale": "Clip 1 rationale",
            "hook_headline": "CLIP ONE",
            "cold_open_quote": "",
            "youtube_titles": ["Clip 1 #Shorts"],
            "youtube_description": "Desc 1",
            "pinned_comment": "Q1?",
            "analysis_source": "gemini",
        },
        {
            "start_time": 4.0,
            "end_time": 6.0,
            "duration": 2.0,
            "virality_score": 91,
            "hook_score": 89,
            "retention_score": 88,
            "emotional_score": 86,
            "standalone_score": 90,
            "viral_rationale": "Clip 2 rationale",
            "hook_headline": "CLIP TWO",
            "cold_open_quote": "",
            "youtube_titles": ["Clip 2 #Shorts"],
            "youtube_description": "Desc 2",
            "pinned_comment": "Q2?",
            "analysis_source": "gemini",
        },
        {
            "start_time": 8.0,
            "end_time": 10.0,
            "duration": 2.0,
            "virality_score": 88,
            "hook_score": 85,
            "retention_score": 84,
            "emotional_score": 82,
            "standalone_score": 87,
            "viral_rationale": "Clip 3 rationale",
            "hook_headline": "CLIP THREE",
            "cold_open_quote": "",
            "youtube_titles": ["Clip 3 #Shorts"],
            "youtube_description": "Desc 3",
            "pinned_comment": "Q3?",
            "analysis_source": "gemini",
        },
    ]

    with patch.object(jm, "_get_project_dir", return_value=proj_dir), \
         patch("app.services.job_manager.get_persisted_settings", return_value={
             "gemini_enabled": True,
             "gemini_api_key": "test-api-key",
             "gemini_analysis_mode": "transcript",
             "auto_fallback_offline": True,
         }), \
         patch("app.services.job_manager.extract_viral_clips", return_value=mock_three_clips), \
         patch("app.services.job_manager.snap_clip_boundaries_to_scenes", side_effect=lambda _p, c, **_k: c):
        jm.create_job(project_id, "Multi-Clip Stage 5 Pre-Render", str(src_mp4))
        jm.run_pipeline(project_id)

    prog = jm.get_progress(project_id)
    assert prog["status"] == "finished"
    assert prog["percent"] == 100

    with Session(test_engine) as session:
        db_clips = session.exec(
            select(ViralClip).where(ViralClip.project_id == project_id).order_by(ViralClip.id)
        ).all()
        assert len(db_clips) == 3
        for idx, c in enumerate(db_clips, start=1):
            assert c.status == "ready", f"Clip #{idx} expected status=='ready', got {c.status}"
            assert c.render_status == "completed"
            assert c.rendered_path is not None
            assert os.path.exists(c.rendered_path), f"Clip #{idx} MP4 missing at {c.rendered_path}"
            assert os.path.getsize(c.rendered_path) > 1000
            assert c.rendered_video_path == c.rendered_path


def test_stream_clip_video_renders_on_demand_when_missing(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """
    2. Calling GET /api/clips/{project_id}/{clip_id}/video on an unrendered clip
       automatically renders fast_preview on-demand and returns HTTP 200 (video/mp4), never 404.
    """
    storage_dir = tmp_path / "storage_ondemand"
    storage_dir.mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr(settings, "STORAGE_DIR", str(storage_dir))

    test_engine = create_engine(
        f"sqlite:///{tmp_path / 'test_ondemand.db'}",
        connect_args={"check_same_thread": False},
    )
    SQLModel.metadata.create_all(test_engine)
    monkeypatch.setattr(app_db, "engine", test_engine)

    src_mp4 = _create_test_video(tmp_path / "source_ondemand.mp4", duration_sec=4.0)

    with Session(test_engine) as session:
        proj = Project(
            title="On-Demand Render Project",
            source_path=str(src_mp4),
            duration=4.0,
            default_aspect_ratio="9:16",
            hardware_mode="cpu",
        )
        session.add(proj)
        session.commit()
        session.refresh(proj)

        clip = ViralClip(
            project_id=proj.id,
            start_time=0.5,
            end_time=2.5,
            duration=2.0,
            frame_layout="fit_blur",
            burn_captions=False,
            hardware_mode="cpu",
            status="draft",
            render_status="idle",
            rendered_path=None,
            rendered_video_path=None,
        )
        session.add(clip)
        session.commit()
        session.refresh(clip)

        resp = stream_latest_clip_video(clip_id=clip.id, project_id=proj.id, session=session)
        assert resp.status_code == 200
        assert resp.media_type == "video/mp4"
        assert os.path.exists(resp.path)
        assert os.path.getsize(resp.path) > 1000
        # Confirm it served a freshly rendered clip, NOT raw uncropped source_ondemand.mp4
        assert Path(resp.path).resolve() != src_mp4.resolve()

        session.refresh(clip)
        assert clip.status == "ready"
        assert clip.render_status == "completed"
        assert clip.rendered_path == resp.path
        assert clip.rendered_video_path == resp.path


def test_watermark_and_resolution_parity_preview_and_render(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """Verify watermark overlay + export resolution + glow parity across preview_frame and render_clip_ultra."""
    from PIL import Image
    from app.services.preview_frame import render_exact_preview_frame
    from app.services.renderer import render_clip_ultra

    storage_dir = tmp_path / "storage_wm"
    storage_dir.mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr(settings, "STORAGE_DIR", str(storage_dir))

    src_mp4 = _create_test_video(tmp_path / "source_wm.mp4", duration_sec=3.0)

    # Create a distinctive bright red watermark PNG
    wm_path = storage_dir / "assets" / "logo_test.png"
    wm_path.parent.mkdir(parents=True, exist_ok=True)
    Image.new("RGBA", (120, 60), (255, 0, 0, 255)).save(wm_path)

    prev_res = render_exact_preview_frame(
        video_path=str(src_mp4),
        timestamp=1.0,
        aspect_ratio="9:16",
        clip_size="480p",
        watermark_path=str(wm_path),
        watermark_corner="top_right",
        watermark_opacity=1.0,
        watermark_size=0.25,
        color_grading={"glow": 0.4, "vignette": 0.25},
        burn_captions=False,
    )
    prev_img_path = storage_dir / "previews" / Path(prev_res["frame_url"]).name
    assert prev_img_path.exists()
    with Image.open(prev_img_path) as im:
        assert im.height == 854  # 480p 9:16 height
        # Top-right region should show red watermark pixels
        px = im.getpixel((im.width - 40, 35))
        assert px[0] > 170 and px[1] < 90, f"Expected red watermark pixel in preview, got {px}"

    out_mp4 = render_clip_ultra(
        master_video_path=str(src_mp4),
        clip_id=99,
        project_id=501,
        start_time=0.0,
        duration=2.0,
        words=[],
        aspect_ratio="9:16",
        clip_size="480p",
        burn_captions=False,
        watermark_path=str(wm_path),
        watermark_corner="top_right",
        watermark_opacity=1.0,
        watermark_size=0.25,
        glow=0.4,
        vignette=0.25,
        hardware_mode="cpu",
        quality_mode="fast_preview",
    )
    assert os.path.exists(out_mp4)
    assert os.path.getsize(out_mp4) > 1000

