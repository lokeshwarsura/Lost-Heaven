import os
import shutil
import subprocess
from pathlib import Path

import pytest
from PIL import Image
from sqlmodel import Session, SQLModel, create_engine, select

import app.database as app_db
import app.main as main_mod
from app.config import settings
from app.main import (
    clip_preflight_endpoint,
    download_by_project_clip,
    get_analytics_formula_endpoint,
    import_analytics_csv_endpoint,
    update_clip,
)
from app.models import (
    ExportEvent,
    ImportAnalyticsCsvRequest,
    Project,
    UpdateClipRequest,
    ViralClip,
)
from app.services.analytics_ledger import record_export_event
from app.services.caption_renderer import build_caption_ffconcat, render_caption_frame
from app.services.gemini_clipper import build_gemini_prompt
from app.services.offline_clipper import extract_viral_clips_offline
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

    db_path = tmp_path / "studio_v4.db"
    test_engine = create_engine(
        f"sqlite:///{db_path}",
        connect_args={"check_same_thread": False},
    )
    SQLModel.metadata.create_all(test_engine)
    monkeypatch.setattr(app_db, "engine", test_engine)
    return test_engine, storage_dir


def _make_av_mp4(out_path: Path, duration_sec: float = 3.0) -> Path:
    out_path.parent.mkdir(parents=True, exist_ok=True)
    ffmpeg = get_ffmpeg_cmd()
    cmd = [
        ffmpeg, "-y",
        "-f", "lavfi", "-i", f"testsrc2=size=480x270:rate=20:duration={duration_sec}",
        "-f", "lavfi", "-i", f"sine=frequency=440:sample_rate=44100:duration={duration_sec}",
        "-c:v", "libx264", "-preset", "ultrafast", "-pix_fmt", "yuv420p",
        "-c:a", "aac", "-shortest",
        str(out_path),
    ]
    res = subprocess.run(cmd, capture_output=True, text=True, timeout=60)
    assert res.returncode == 0, res.stderr
    return out_path


def test_phase3_safety_scanner_preflight_and_1click_fixes(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
):
    test_engine, _ = _setup_isolated_db(tmp_path, monkeypatch)
    video_path = _make_av_mp4(tmp_path / "media" / "preflight_src.mp4", duration_sec=2.5)

    with Session(test_engine) as session:
        proj = Project(
            title="Preflight Safety Project",
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
            title="Unsafe Draft",
            hook_headline="THIS SCAM GOT EXPOSED",
            start_time=0.0,
            end_time=2.5,
            duration=2.5,
            aspect_ratio="9:16",
            burn_captions=True,
            position_x=50.0,
            position_y=90.0,  # UI chrome danger zone (>83%)
            censor_profanity=False,
            normalize_audio=False,
            audio_volume=1.6,  # risk clipping
        )
        clip.set_transcript([
            {"word": "What", "start": 0.1, "end": 0.3},
            {"word": "a", "start": 0.3, "end": 0.4},
            {"word": "fuck", "start": 0.5, "end": 0.9},
            {"word": "scam", "start": 1.0, "end": 1.4},
        ])
        session.add(clip)
        session.commit()
        session.refresh(clip)

        # 1. Initial preflight detects ad-safety, safe-zone, and audio risk
        report_initial = clip_preflight_endpoint(proj.id, clip.id, session=session)
        assert report_initial["readiness_score"] < 80
        assert report_initial["status"] in {"warn", "risk"}
        failing_with_fix = [c for c in report_initial["checks"] if c.get("fix_patch")]
        assert len(failing_with_fix) >= 2

        # 2. Apply every 1-Click Fix patch cleanly via update_clip
        for chk in failing_with_fix:
            update_clip(clip.id, UpdateClipRequest(**chk["fix_patch"]), session=session)

        # 3. Re-scan must reach 100/100 Readiness Pass
        report_fixed = clip_preflight_endpoint(proj.id, clip.id, session=session)
        assert report_fixed["readiness_score"] == 100
        assert report_fixed["status"] == "pass"
        assert all(c["status"] == "pass" for c in report_fixed["checks"])


def test_phase3_creator_handle_watermark_always_and_pulse_12s(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
):
    test_engine, storage_dir = _setup_isolated_db(tmp_path, monkeypatch)

    plain = render_caption_frame(t=1.2, width=360, height=640, words=[], burn_captions=False)
    wm_always_yt = render_caption_frame(
        t=6.5,
        width=360,
        height=640,
        words=[],
        burn_captions=False,
        handle_watermark_enabled=True,
        handle_text="@LostHeaven",
        handle_platform_icon="youtube",
        handle_corner="bottom_right",
        handle_opacity=0.80,
        handle_display_mode="always",
    )
    wm_always_tt = render_caption_frame(
        t=6.5,
        width=360,
        height=640,
        words=[],
        burn_captions=False,
        handle_watermark_enabled=True,
        handle_text="@LostHeaven",
        handle_platform_icon="tiktok",
        handle_corner="top_left",
        handle_opacity=0.80,
        handle_display_mode="always",
    )
    assert wm_always_yt.tobytes() != plain.tobytes()
    assert wm_always_tt.tobytes() != wm_always_yt.tobytes()

    # Periodic Pulse 12s: visible at t=1.2s & t=13.2s, hidden at t=6.5s
    wm_pulse_on1 = render_caption_frame(
        t=1.2,
        width=360,
        height=640,
        words=[],
        burn_captions=False,
        handle_watermark_enabled=True,
        handle_text="@CreatorHandle",
        handle_platform_icon="instagram",
        handle_corner="bottom_left",
        handle_opacity=0.75,
        handle_display_mode="pulse_12s",
    )
    wm_pulse_off = render_caption_frame(
        t=6.5,
        width=360,
        height=640,
        words=[],
        burn_captions=False,
        handle_watermark_enabled=True,
        handle_text="@CreatorHandle",
        handle_platform_icon="instagram",
        handle_corner="bottom_left",
        handle_opacity=0.75,
        handle_display_mode="pulse_12s",
    )
    wm_pulse_on2 = render_caption_frame(
        t=13.2,
        width=360,
        height=640,
        words=[],
        burn_captions=False,
        handle_watermark_enabled=True,
        handle_text="@CreatorHandle",
        handle_platform_icon="instagram",
        handle_corner="bottom_left",
        handle_opacity=0.75,
        handle_display_mode="pulse_12s",
    )
    assert wm_pulse_on1.tobytes() != plain.tobytes()
    assert wm_pulse_off.tobytes() == plain.tobytes()
    assert wm_pulse_on2.tobytes() != plain.tobytes()

    cap_dir, ff_path, count = build_caption_ffconcat(
        words=[],
        clip_duration=15.0,
        width=360,
        height=640,
        burn_captions=False,
        handle_watermark_enabled=True,
        handle_text="@CreatorHandle",
        handle_platform_icon="x",
        handle_corner="top_right",
        handle_opacity=0.70,
        handle_display_mode="pulse_12s",
    )
    try:
        assert ff_path.exists()
        assert count >= 2
    finally:
        shutil.rmtree(cap_dir, ignore_errors=True)

    # Full MP4 render with watermark enabled
    video_path = _make_av_mp4(tmp_path / "media" / "wm_src.mp4", duration_sec=2.5)
    with Session(test_engine) as session:
        proj = Project(
            title="Watermark Project",
            source_type="upload",
            source_path=str(video_path),
            duration=2.5,
            status="finished",
        )
        session.add(proj)
        session.commit()
        session.refresh(proj)

        out_mp4 = str(storage_dir / "handle_wm_out.mp4")
        rendered = render_clip_ultra(
            master_video_path=str(video_path),
            clip_id=301,
            start_time=0.0,
            duration=2.5,
            words=[{"word": "Brand", "start": 0.2, "end": 1.6}],
            project_id=proj.id,
            aspect_ratio="9:16",
            frame_layout="center",
            burn_captions=True,
            handle_watermark_enabled=True,
            handle_text="@StudioMark",
            handle_platform_icon="youtube",
            handle_corner="top_right",
            handle_opacity=0.75,
            handle_display_mode="pulse_12s",
            hardware_mode="cpu",
            quality_mode="fast_preview",
            output_path_override=out_mp4,
        )
        assert os.path.exists(rendered)
        assert probe_video(rendered)["duration"] >= 2.0


def test_phase3_analytics_ledger_csv_formula_and_scoring_boost(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
):
    test_engine, storage_dir = _setup_isolated_db(tmp_path, monkeypatch)
    video_path = _make_av_mp4(tmp_path / "media" / "ledger_src.mp4", duration_sec=2.0)

    with Session(test_engine) as session:
        proj = Project(
            title="Analytics Project",
            source_type="upload",
            source_path=str(video_path),
            duration=2.0,
            status="finished",
        )
        session.add(proj)
        session.commit()
        session.refresh(proj)

        clip = ViralClip(
            project_id=proj.id,
            title="High Performer",
            selected_title="How To Build AI Shorts",
            aspect_ratio="9:16",
            caption_style="Bold highlight",
            start_time=0.0,
            end_time=2.0,
            duration=32.0,
            show_hook_banner=True,
            beat_sync_enabled=True,
            rendered_path=str(video_path),
            rendered_video_path=str(video_path),
        )
        session.add(clip)
        session.commit()
        session.refresh(clip)

        # Download records an export event in SQLite ledger
        download_by_project_clip(proj.id, clip.id, session=session)
        record_export_event(session, clip, event_type="mp4_render")
        assert len(session.exec(select(ExportEvent)).all()) == 2

        # Import YouTube Studio Analytics CSV showing huge views on 32s clip
        csv_payload = "\n".join([
            "Video title,Views,Average percentage viewed (%),Impressions click-through rate (%),Duration",
            "How To Build AI Shorts,62400,94.2,9.8,32",
            "Quick 18s Experiment,1400,48.0,3.1,18",
        ])
        imp = import_analytics_csv_endpoint(
            ImportAnalyticsCsvRequest(csv_text=csv_payload),
            session=session,
        )
        assert imp["imported_rows"] == 2
        assert imp["matched_clips"] >= 1

        formula = get_analytics_formula_endpoint(session=session)
        assert formula["has_data"] is True
        assert formula["preferred_duration_band"] == "25-40s"
        assert formula["preferred_caption_style"] == "Bold highlight"
        assert formula["avg_views"] > 20000
        assert formula["avg_retention_pct"] > 65.0
        assert "HISTORICAL WINNER FORMULA" in formula["prompt_guidance"]

        # Verify Gemini prompt reflects historical winning formula guidance
        prompt_str = build_gemini_prompt(duration_preset="30-58s", clip_count=3, formula_weights=formula)
        assert "HISTORICAL WINNER FORMULA" in prompt_str

        # Verify Offline Clipper boosts candidates aligning with preferred duration formula
        synth_words = []
        t_cur = 0.0
        for idx in range(28):
            w_end = round(t_cur + 1.15, 2)
            txt = f"word{idx}." if (idx % 6 == 5) else f"word{idx}"
            synth_words.append({"word": txt, "start": round(t_cur, 2), "end": w_end})
            t_cur = w_end + 0.1

        base_clips = extract_viral_clips_offline(
            audio_wav_path=str(storage_dir / "missing.wav"),
            transcript_words=synth_words,
            duration_sec=t_cur + 1.0,
            duration_preset="Auto",
            clip_count=2,
            formula_weights=None,
        )
        boosted_clips = extract_viral_clips_offline(
            audio_wav_path=str(storage_dir / "missing.wav"),
            transcript_words=synth_words,
            duration_sec=t_cur + 1.0,
            duration_preset="Auto",
            clip_count=2,
            formula_weights=formula,
        )
        assert len(base_clips) >= 1 and len(boosted_clips) >= 1
        assert max(c["virality_score"] for c in boosted_clips) >= max(c["virality_score"] for c in base_clips)
