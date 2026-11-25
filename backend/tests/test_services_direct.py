import os
import pytest
from pathlib import Path
from sqlmodel import Session, select
from app.database import engine, init_db
from app.models import Project, ViralClip
from app.services.preview_frame import render_exact_preview_frame
from app.services.captions import generate_ass_script_pro
from app.services.gemini_clipper import extract_viral_clips, GeminiAuthError
from app.services.transcriber import filter_filler_words, get_whisper_model
from app.config import settings

def test_db_project_and_clip_persistence():
    init_db()
    with Session(engine) as session:
        projects = session.exec(select(Project)).all()
        assert len(projects) > 0
        p = projects[0]
        assert p.id is not None
        # Test modifying project properties
        p.caption_language = "Hinglish"
        p.clip_count = 6
        p.duration_preset = "45s"
        session.add(p)
        session.commit()
        session.refresh(p)
        assert p.caption_language == "Hinglish"
        assert p.clip_count == 6
        assert p.duration_preset == "45s"

        clips = session.exec(select(ViralClip).where(ViralClip.project_id == p.id)).all()
        if len(clips) == 0:
            test_clip = ViralClip(
                project_id=p.id,
                hook_headline="LAS VEGAS GRAND PRIX OVERTAKE",
                selected_title="Insane Formula 1 Moment #Shorts",
                start_time=10.0,
                end_time=25.0,
                duration=15.0,
                virality_score=94
            )
            session.add(test_clip)
            session.commit()
            session.refresh(test_clip)
            clips = [test_clip]

        assert len(clips) > 0
        c = clips[0]
        assert c.hook_headline is not None

def _get_or_create_test_video(tmp_path: Path) -> Path:
    init_db()
    with Session(engine) as session:
        projects = session.exec(select(Project)).all()
        for p in projects:
            if p.source_path and Path(p.source_path).exists():
                return Path(p.source_path)
    import subprocess
    from app.services.proxy import get_ffmpeg_cmd
    out = tmp_path / "synthetic_test.mp4"
    subprocess.run(
        [
            get_ffmpeg_cmd(), "-y",
            "-f", "lavfi", "-i", "testsrc=duration=3:size=640x360:rate=25",
            "-c:v", "libx264", "-pix_fmt", "yuv420p", str(out)
        ],
        capture_output=True, check=True
    )
    return out

def test_preview_frame_direct_rendering(tmp_path):
    master_path = _get_or_create_test_video(tmp_path)
    assert master_path.exists(), f"Master video not found at {master_path}"

    result = render_exact_preview_frame(
        video_path=str(master_path),
        timestamp=1.0,
        aspect_ratio="9:16",
        frame_layout="fit_blur",
        zoom=1.0,
        pan_x_offset=0.0,
        color_grading={"preset": "Warm Amber", "brightness": 0.05, "contrast": 1.1, "saturation": 1.2}
    )
    assert "frame_url" in result
    assert result["render_time_ms"] > 0
    frame_file = Path(settings.STORAGE_DIR) / "previews" / Path(result["frame_url"]).name
    assert frame_file.exists()
    assert frame_file.stat().st_size > 1000

def test_preview_frame_4_3_aspect_ratio(tmp_path):
    master_path = _get_or_create_test_video(tmp_path)

    result = render_exact_preview_frame(
        video_path=str(master_path),
        timestamp=1.0,
        aspect_ratio="4:3",
        frame_layout="auto_track",
        zoom=1.1,
        pan_x_offset=0.1,
        color_grading={"preset": "None", "brightness": 0.0, "contrast": 1.0, "saturation": 1.0}
    )
    assert "frame_url" in result
    frame_file = Path(settings.STORAGE_DIR) / "previews" / Path(result["frame_url"]).name
    assert frame_file.exists()
    assert frame_file.stat().st_size > 1000

def test_gemini_raises_real_error_without_mock():
    from unittest.mock import patch
    orig_key = settings.GEMINI_API_KEY
    settings.GEMINI_API_KEY = ""
    try:
        with patch.dict(os.environ, {"GEMINI_API_KEY": ""}, clear=False):
            with pytest.raises((RuntimeError, GeminiAuthError)) as exc_info:
                extract_viral_clips(proxy_path="non_existent.mp4", total_duration=30.0)
            assert "Google Gemini API Key is missing or unconfigured" in str(exc_info.value)
    finally:
        settings.GEMINI_API_KEY = orig_key

def test_ass_hook_banner_formatting(tmp_path):
    words = [
        {"word": "UNSTOPPABLE", "start": 0.0, "end": 0.5},
        {"word": "GROWTH", "start": 0.5, "end": 1.2}
    ]
    out_ass = tmp_path / "test_hook.ass"
    generate_ass_script_pro(
        words=words,
        output_ass_path=str(out_ass),
        hook_headline="DO THIS BEFORE IT'S TOO LATE TO SKYROCKET ENGAGEMENT ON YOUTUBE SHORTS",
        show_hook_banner=True
    )
    assert out_ass.exists()
    content = out_ass.read_text(encoding="utf-8")
    assert "WrapStyle: 1" in content
    assert "Style: HookBanner" in content
    assert "\\q1" in content

def test_filter_filler_words():
    words = [
        {"word": "Um", "start": 0.0, "end": 0.2, "is_filler": True},
        {"word": "this", "start": 0.2, "end": 0.5, "is_filler": False},
        {"word": "uh", "start": 0.5, "end": 0.7, "is_filler": True},
        {"word": "rocks", "start": 0.7, "end": 1.0, "is_filler": False}
    ]
    filtered = filter_filler_words(words)
    assert len(filtered) == 2
    assert [w["word"] for w in filtered] == ["this", "rocks"]
