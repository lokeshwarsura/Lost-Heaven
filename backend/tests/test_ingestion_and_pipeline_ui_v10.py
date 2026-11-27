"""
Verification suite v10: Home Ingestion Deck & Pipeline Progress Mission Control.

Tests:
1. GET /api/jobs active/queued/recent aggregation, live_metrics, per-stage duration_sec,
   and accurate OFFLINE AI vs GEMINI engine metadata.
2. GET /api/ingest/youtube-info & /api/projects/youtube-info metadata extraction via yt-dlp.
3. Explicit ai_mode ('gemini' | 'offline') and default_framing SQLite persistence across
   upload_video and import_youtube endpoints.
"""

import io
import os
import sys
import types
from pathlib import Path

import pytest
from fastapi import BackgroundTasks, HTTPException
from sqlalchemy.pool import StaticPool
from sqlmodel import Session, SQLModel, create_engine

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

import app.database as app_db
import app.main as main_mod
import app.services.job_manager as jm_mod
from app.config import settings
from app.models import Project, YouTubeImportRequest
from app.services.batch_queue import enqueue_batch_project


def _make_isolated_env(tmp_path: Path, monkeypatch):
    test_engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    SQLModel.metadata.create_all(test_engine)

    storage_dir = tmp_path / "storage"
    storage_dir.mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr(settings, "STORAGE_DIR", str(storage_dir))
    monkeypatch.setattr(app_db, "engine", test_engine)
    monkeypatch.setattr(jm_mod, "engine", test_engine)

    # Isolate batch queue & job manager in-memory map cleanly
    jm = jm_mod.job_manager
    saved_jobs = dict(jm._jobs)
    jm._jobs.clear()

    return test_engine, storage_dir, jm, saved_jobs


def test_list_jobs_endpoint_active_queued_recent_and_live_metrics(tmp_path, monkeypatch):
    test_engine, storage_dir, jm, saved_jobs = _make_isolated_env(tmp_path, monkeypatch)
    try:
        video_file = tmp_path / "sample_podcast.mp4"
        video_file.write_bytes(b"\x00\x00\x00\x20ftypisom" + b"0" * 4096)

        with Session(test_engine) as session:
            p_active = Project(
                title="Active Offline Podcast",
                source_type="local",
                source_filename="sample_podcast.mp4",
                source_path=str(video_file),
                status="processing",
                default_framing="smart_auto",
                ai_mode="offline",
                duration=124.5,
            )
            p_done = Project(
                title="Finished Gemini Highlights",
                source_type="youtube",
                source_filename="yt_clip.mp4",
                source_path=str(video_file),
                status="analyzed",
                default_framing="gameplay_webcam_pip",
                ai_mode="gemini",
                duration=90.0,
            )
            p_queued = Project(
                title="Queued Batch Video",
                source_type="local",
                source_filename="batch_next.mp4",
                source_path=str(video_file),
                status="created",
                default_framing="fit_blur",
                ai_mode="offline",
            )
            session.add(p_active)
            session.add(p_done)
            session.add(p_queued)
            session.commit()
            session.refresh(p_active)
            session.refresh(p_done)
            session.refresh(p_queued)

            active_id = p_active.id
            done_id = p_done.id
            queued_id = p_queued.id

        # Seed active job with progressive metrics across stages
        jm.create_job(
            project_id=active_id,
            title="Active Offline Podcast",
            source_path=str(video_file),
            source_filename="sample_podcast.mp4",
            ai_mode="offline",
            default_framing="smart_auto",
        )
        jm.update_progress(
            active_id,
            15,
            "ingest",
            "Media probed",
            message="Probed 1920x1080",
            duration_sec=124.5,
        )
        jm.update_progress(
            active_id,
            65,
            "transcribe",
            "Voice complete",
            message="Transcribed 412 spoken words",
            live_metrics={"words_transcribed": 412},
        )
        jm.update_progress(
            active_id,
            72,
            "gemini",
            "Offline Multimodal AI",
            message="Scoring local narrative arcs...",
            log_stage="OFFLINE AI",
            live_metrics={
                "keyframes_scanned": 96,
                "keyframes_total": 150,
                "highlights_detected": 4,
            },
        )

        # Seed finished job
        jm.create_job(
            project_id=done_id,
            title="Finished Gemini Highlights",
            source_path=str(video_file),
            source_filename="yt_clip.mp4",
            ai_mode="gemini",
            default_framing="gameplay_webcam_pip",
        )
        jm.finish_job(done_id, clips_count=5)

        # Enqueue batch job without spinning background worker thread
        enqueue_batch_project(queued_id, "Queued Batch Video", source="upload", auto_start=False)

        payload = main_mod.list_jobs_endpoint(limit=20)
        assert isinstance(payload, dict)
        assert len(payload["active_jobs"]) == 1
        assert len(payload["queued_jobs"]) == 1
        assert len(payload["recent_jobs"]) >= 1

        active_job = payload["active_jobs"][0]
        assert active_job["project_id"] == active_id
        assert active_job["ai_mode"] == "offline"
        assert active_job["duration_sec"] == 124.5
        assert active_job["progress_pct"] == 72
        assert active_job["stage_index"] == 3
        assert active_job["live_metrics"]["words_transcribed"] == 412
        assert active_job["live_metrics"]["keyframes_scanned"] == 96
        assert active_job["live_metrics"]["keyframes_total"] == 150
        assert active_job["live_metrics"]["highlights_detected"] == 4

        # Previous stages should be marked done with duration_sec populated
        assert active_job["stages"][0]["status"] == "done"
        assert active_job["stages"][0]["duration_sec"] is not None
        assert active_job["stages"][2]["status"] == "done"
        assert active_job["stages"][3]["status"] == "running"

        # Log tags reflect OFFLINE AI instead of misleading GEMINI
        stage_tags = [entry["stage"] for entry in active_job["logs"]]
        assert "OFFLINE AI" in stage_tags

        queued_job = payload["queued_jobs"][0]
        assert queued_job["project_id"] == queued_id
        assert queued_job["status"] == "queued"

        recent_ids = [r["project_id"] for r in payload["recent_jobs"]]
        assert done_id in recent_ids
    finally:
        jm._jobs.clear()
        jm._jobs.update(saved_jobs)


def test_youtube_info_endpoint_metadata_and_validation(monkeypatch):
    captured_opts = {}

    class _FakeYDL:
        def __init__(self, opts):
            captured_opts.update(opts)

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return False

        def extract_info(self, url, download=False):
            assert download is False
            return {
                "title": "Deep Dive Documentary",
                "thumbnail": "https://i.ytimg.com/vi/abc123/maxresdefault.jpg",
                "duration": 612,
                "width": 1920,
                "height": 1080,
                "uploader": "Lost Heaven Studio",
                "webpage_url": url,
                "filesize_approx": 104857600,
            }

    fake_yt_dlp = types.SimpleNamespace(YoutubeDL=_FakeYDL)
    monkeypatch.setitem(sys.modules, "yt_dlp", fake_yt_dlp)

    meta = main_mod.get_youtube_info_endpoint("https://www.youtube.com/watch?v=abc123")
    assert captured_opts.get("skip_download") is True
    assert meta["title"] == "Deep Dive Documentary"
    assert meta["duration"] == 612.0
    assert meta["width"] == 1920
    assert meta["height"] == 1080
    assert meta["resolution"] == "1920x1080"
    assert meta["uploader"] == "Lost Heaven Studio"
    assert meta["filesize_approx"] == 104857600

    with pytest.raises(HTTPException) as exc_info:
        main_mod.get_youtube_info_endpoint("")
    assert exc_info.value.status_code == 400


def test_explicit_ai_mode_and_default_framing_persist_across_ingest(tmp_path, monkeypatch):
    test_engine, storage_dir, jm, saved_jobs = _make_isolated_env(tmp_path, monkeypatch)
    try:
        # Mock yt_dlp for youtube import test without network access
        downloaded_file = tmp_path / "yt_source.mp4"
        downloaded_file.write_bytes(b"\x00\x00\x00\x20ftypisom" + b"0" * 1024)

        class _FakeDownloadYDL:
            def __init__(self, opts):
                pass

            def __enter__(self):
                return self

            def __exit__(self, exc_type, exc, tb):
                return False

            def extract_info(self, url, download=False):
                return {"id": "yt_source", "title": "Gameplay Highlights Vol 1", "ext": "mp4"}

            def prepare_filename(self, info):
                return str(downloaded_file)

        monkeypatch.setitem(sys.modules, "yt_dlp", types.SimpleNamespace(YoutubeDL=_FakeDownloadYDL))

        class _FakeUpload:
            def __init__(self, filename: str, payload: bytes):
                self.filename = filename
                self._buf = io.BytesIO(payload)

            async def read(self, size: int = -1) -> bytes:
                return self._buf.read(size)

        def _drive_coro(coro):
            try:
                while True:
                    coro.send(None)
            except StopIteration as stop:
                return stop.value

        async def _run():
            with Session(test_engine) as session:
                fake_upload = _FakeUpload(
                    filename="creator_talk.mp4",
                    payload=b"\x00\x00\x00\x20ftypisom" + b"1" * 1024,
                )
                bg1 = BackgroundTasks()
                res_up = await main_mod.upload_video(
                    file=fake_upload,
                    aspect_ratio="9:16",
                    duration_preset="30s",
                    clip_count=8,
                    topic_focus="Funny / High Energy",
                    caption_language="English",
                    hardware_mode="cpu",
                    default_framing="gameplay_webcam_pip",
                    ai_mode="offline",
                    batch_queue=False,
                    background_tasks=bg1,
                    session=session,
                )
                assert res_up["ai_mode"] == "offline"
                assert res_up["default_framing"] == "gameplay_webcam_pip"

                p1 = session.get(Project, res_up["project_id"])
                assert p1 is not None
                assert p1.ai_mode == "offline"
                assert p1.default_framing == "gameplay_webcam_pip"
                assert p1.clip_count == 8

                bg2 = BackgroundTasks()
                yt_req = YouTubeImportRequest(
                    url="https://youtu.be/yt_source",
                    aspect_ratio="1:1",
                    duration_preset="45s",
                    clip_count=3,
                    caption_language="As spoken",
                    hardware_mode="gpu",
                    default_framing="fit_blur",
                    ai_mode="gemini",
                    batch_queue=False,
                )
                res_yt = await main_mod.import_youtube(
                    req=yt_req,
                    background_tasks=bg2,
                    session=session,
                )
                assert res_yt["ai_mode"] == "gemini"
                assert res_yt["default_framing"] == "fit_blur"

                p2 = session.get(Project, res_yt["project_id"])
                assert p2 is not None
                assert p2.ai_mode == "gemini"
                assert p2.default_framing == "fit_blur"
                assert p2.default_aspect_ratio == "1:1"

        _drive_coro(_run())
    finally:
        jm._jobs.clear()
        jm._jobs.update(saved_jobs)
