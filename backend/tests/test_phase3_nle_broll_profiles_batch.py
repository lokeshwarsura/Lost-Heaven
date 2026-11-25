import io
import json
import os
import shutil
import subprocess
import tempfile
import xml.etree.ElementTree as ET
import zipfile
from pathlib import Path

from PIL import Image
from sqlmodel import SQLModel, Session, create_engine

from app.config import settings
from app.main import (
    create_manual_clip_endpoint,
    export_clip_nle_endpoint,
    search_project_transcript_endpoint,
)
from app.models import Project, ViralClip
from app.services.batch_queue import list_batch_queue, scan_watch_folder
from app.services.broll_engine import suggest_broll_cutaways
from app.services.caption_renderer import render_caption_frame
from app.services.profile_manager import create_profile, delete_profile, list_profiles
from app.services.proxy import get_ffmpeg_cmd, probe_video
from app.services.renderer import render_clip_ultra


def _make_source_mp4(path: Path, dur: float = 5.0) -> str:
    path.parent.mkdir(parents=True, exist_ok=True)
    ffmpeg = get_ffmpeg_cmd()
    res = subprocess.run(
        [
            ffmpeg, "-y",
            "-f", "lavfi", "-i", f"testsrc2=size=640x360:rate=24:duration={dur}",
            "-f", "lavfi", "-i", f"aevalsrc='0.3*sin(2*PI*360*t)':s=44100:d={dur}",
            "-c:v", "libx264", "-preset", "ultrafast", "-pix_fmt", "yuv420p",
            "-c:a", "aac", "-ar", "44100", "-ac", "1",
            str(path),
        ],
        capture_output=True, text=True, timeout=45,
    )
    assert res.returncode == 0, res.stderr
    return str(path)


def test_retention_bar_outro_cta_and_broll_render_in_unicode_dir():
    tmp_root = Path(tempfile.mkdtemp(prefix="lost_heaven_p3_ドキュメント test_"))
    orig_storage = settings.STORAGE_DIR
    try:
        settings.STORAGE_DIR = str(tmp_root / "storage")
        broll_dir = Path(settings.STORAGE_DIR) / "broll"
        broll_dir.mkdir(parents=True, exist_ok=True)
        # Create local B-Roll image matching keyword "rocket"
        rocket_img = broll_dir / "rocket_launch.png"
        Image.new("RGB", (640, 360), (20, 110, 220)).save(rocket_img, format="PNG")

        words = [
            {"word": "Watch", "start": 0.3, "end": 0.7},
            {"word": "the", "start": 0.8, "end": 1.0},
            {"word": "rocket", "start": 1.2, "end": 1.9},
            {"word": "soar", "start": 2.0, "end": 2.6},
            {"word": "today", "start": 2.8, "end": 3.6},
        ]

        # Pillow preview frame at t=4.2s draws retention bar + 2s outro CTA card
        frame = render_caption_frame(
            t=4.2,
            width=540,
            height=960,
            words=words,
            burn_captions=True,
            retention_bar_enabled=True,
            retention_bar_color="#C6F32F",
            outro_cta_enabled=True,
            outro_cta_text="Follow Now",
            clip_start_time=0.0,
            clip_duration=5.0,
        )
        # Verify bottom edge has non-zero alpha from retention bar
        bottom_strip = frame.crop((40, 930, 500, 955))
        assert bottom_strip.getbbox() is not None

        suggs = suggest_broll_cutaways(words, clip_start_time=0.0, clip_duration=5.0)
        assert len(suggs) >= 1
        assert suggs[0]["keyword"] == "rocket"
        assert suggs[0]["source"] == "local"
        assert suggs[0]["enabled"] is True

        src_mp4 = _make_source_mp4(tmp_root / "media space" / "input video.mp4", dur=5.0)
        out_mp4 = render_clip_ultra(
            master_video_path=src_mp4,
            clip_id=9903,
            project_id=9903,
            start_time=0.0,
            duration=5.0,
            words=words,
            aspect_ratio="9:16",
            frame_layout="auto_track",
            burn_captions=True,
            retention_bar_enabled=True,
            retention_bar_color="#C6F32F",
            outro_cta_enabled=True,
            outro_cta_text="Follow Now",
            broll_segments=suggs,
            quality_mode="fast_preview",
        )
        assert os.path.exists(out_mp4)
        assert probe_video(out_mp4)["duration"] >= 4.2
    finally:
        if "out_mp4" in locals() and os.path.exists(out_mp4):
            try:
                os.remove(out_mp4)
            except Exception:
                pass
        settings.STORAGE_DIR = orig_storage
        shutil.rmtree(tmp_root, ignore_errors=True)


def test_nle_exports_profiles_transcript_manual_clip_and_watch_folder():
    tmp_root = Path(tempfile.mkdtemp(prefix="lost_heaven_p3_api_ドキュメント test_"))
    orig_storage = settings.STORAGE_DIR
    try:
        settings.STORAGE_DIR = str(tmp_root / "storage")

        # 1. Studio Profiles CRUD
        starters = list_profiles()
        assert len(starters) >= 3
        custom = create_profile("My Viral Setup", "Custom", {"aspect_ratio": "9:16", "tighten_pacing": True})
        assert any(p["id"] == custom["id"] for p in list_profiles())
        assert delete_profile(custom["id"]) is True

        # 2. SQLite Project + Transcript Search + Manual Clip + NLE Export
        src_mp4 = _make_source_mp4(tmp_root / "videos" / "source.mp4", dur=5.0)
        engine = create_engine("sqlite:///:memory:")
        SQLModel.metadata.create_all(engine)

        with Session(engine) as session:
            proj = Project(title="Phase 3 NLE Test", source_path=src_mp4, duration=5.0, status="completed")
            session.add(proj)
            session.commit()
            session.refresh(proj)

            proj_dir = Path(settings.STORAGE_DIR) / "projects" / str(proj.id)
            proj_dir.mkdir(parents=True, exist_ok=True)
            full_t = [
                {"word": "Opening", "start": 0.2, "end": 0.6},
                {"word": "pricing", "start": 0.7, "end": 1.2},
                {"word": "secret", "start": 1.3, "end": 1.9},
                {"word": "shit", "start": 2.1, "end": 2.6},
                {"word": "hidden", "start": 2.7, "end": 3.2, "deleted": True},
            ]
            (proj_dir / "transcript.json").write_text(json.dumps(full_t), encoding="utf-8")

            hits = search_project_transcript_endpoint(project_id=proj.id, q="pricing", session=session)
            assert len(hits["matches"]) == 1
            assert "pricing" in hits["matches"][0]["text"]

            created = create_manual_clip_endpoint(
                project_id=proj.id,
                req={"start_time": 0.5, "end_time": 3.5, "title": "Pricing Secret"},
                session=session,
            )
            assert created["status"] == "created"
            clip_id = created["clip"]["id"]
            clip_row = session.get(ViralClip, clip_id)
            assert clip_row is not None
            clip_row.censor_profanity = True
            session.add(clip_row)
            session.commit()

            # Export .srt
            srt_resp = export_clip_nle_endpoint(proj.id, clip_id, format="srt", session=session)
            srt_txt = srt_resp.body.decode("utf-8")
            assert "pricing" in srt_txt
            assert "s***" in srt_txt

            # Export .fcpxml & validate XML
            fcp_resp = export_clip_nle_endpoint(proj.id, clip_id, format="fcpxml", session=session)
            root_fcp = ET.fromstring(fcp_resp.body.decode("utf-8"))
            assert root_fcp.tag == "fcpxml"
            assert root_fcp.attrib.get("version") == "1.10"

            # Export Premiere XMEML & validate XML
            pr_resp = export_clip_nle_endpoint(proj.id, clip_id, format="premiere", session=session)
            root_pr = ET.fromstring(pr_resp.body.decode("utf-8"))
            assert root_pr.tag == "xmeml"

            # Export .zip Bundle
            zip_resp = export_clip_nle_endpoint(proj.id, clip_id, format="zip", session=session)
            with zipfile.ZipFile(io.BytesIO(zip_resp.body), "r") as zf:
                names = zf.namelist()
                assert any(n.endswith(".fcpxml") for n in names)
                assert any(n.endswith("_premiere.xml") for n in names)
                assert any(n.endswith(".srt") for n in names)
                assert "metadata.json" in names

        # 3. Watch Folder Scanner (auto_start=False)
        watch_dir = tmp_root / "watch"
        watch_dir.mkdir(parents=True, exist_ok=True)
        shutil.copy2(src_mp4, watch_dir / "batch_one.mp4")
        shutil.copy2(src_mp4, watch_dir / "batch_two.mp4")

        # Point database engine to temp SQLite for watch scanner test
        watch_db = create_engine(f"sqlite:///{tmp_root / 'watch.db'}")
        SQLModel.metadata.create_all(watch_db)
        import app.services.batch_queue as bq
        orig_bq_engine = bq.engine
        bq.engine = watch_db
        try:
            scan1 = scan_watch_folder(str(watch_dir), auto_start=False)
            assert scan1["scanned"] == 2
            assert len(list_batch_queue()) == 2
            # Re-scan must not duplicate existing paths
            scan2 = scan_watch_folder(str(watch_dir), auto_start=False)
            assert scan2["scanned"] == 0
        finally:
            bq.engine = orig_bq_engine
    finally:
        settings.STORAGE_DIR = orig_storage
        shutil.rmtree(tmp_root, ignore_errors=True)
