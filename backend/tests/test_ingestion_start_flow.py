"""
Verification suite: Ingestion Start Flow, YouTube URL Normalization & DB Schema Migration.

Tests:
1. normalize_youtube_url cleanly converts youtu.be/<id>?si=... into
   https://www.youtube.com/watch?v=<id> without tracking parameters.
2. Direct YouTube URL ingestion (https://youtu.be/Y4yTwDK37D0?si=0wvivLjRWRM_RB1C mocked)
   and local file upload create a Project, enqueue a background job, accept all alias
   parameters without 422 errors, and return valid project_id + job_id.
3. init_db migrates older SQLite caption_style_presets tables missing highlight_power_words.
"""

import io
import os
import sqlite3
import sys
import types
from pathlib import Path

from fastapi import BackgroundTasks
from sqlalchemy.pool import StaticPool
from sqlmodel import Session, SQLModel, create_engine

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

import app.database as app_db
import app.main as main_mod
import app.services.job_manager as jm_mod
from app.config import settings
from app.models import Project, YouTubeImportRequest


def _drive_coro(coro):
    try:
        while True:
            coro.send(None)
    except StopIteration as stop:
        return stop.value


def test_youtube_url_normalization_strips_tracking_params():
    raw = "https://youtu.be/Y4yTwDK37D0?si=0wvivLjRWRM_RB1C"
    assert main_mod.normalize_youtube_url(raw) == "https://www.youtube.com/watch?v=Y4yTwDK37D0"

    bare = "youtu.be/Y4yTwDK37D0?si=abc123&feature=shared"
    assert main_mod.normalize_youtube_url(bare) == "https://www.youtube.com/watch?v=Y4yTwDK37D0"

    shorts = "https://www.youtube.com/shorts/dQw4w9WgXcQ?si=xyz"
    assert main_mod.normalize_youtube_url(shorts) == "https://www.youtube.com/watch?v=dQw4w9WgXcQ"


def test_direct_youtube_and_local_upload_start_flow(tmp_path, monkeypatch):
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

    jm = jm_mod.job_manager
    saved_jobs = dict(jm._jobs)
    jm._jobs.clear()

    seen_urls = []
    try:
        expected_yt_file = storage_dir / "masters" / "Y4yTwDK37D0.mp4"

        class _FakeYDL:
            def __init__(self, opts):
                self.opts = opts

            def __enter__(self):
                return self

            def __exit__(self, exc_type, exc, tb):
                return False

            def extract_info(self, url, download=False):
                seen_urls.append((url, download))
                return {
                    "id": "Y4yTwDK37D0",
                    "title": "Sample YouTube Creator Clip",
                    "ext": "mp4",
                }

            def prepare_filename(self, info):
                return str(expected_yt_file)

        monkeypatch.setitem(sys.modules, "yt_dlp", types.SimpleNamespace(YoutubeDL=_FakeYDL))

        class _FakeUpload:
            def __init__(self, filename: str, payload: bytes):
                self.filename = filename
                self._buf = io.BytesIO(payload)

            async def read(self, size: int = -1) -> bytes:
                return self._buf.read(size)

        async def _run():
            with Session(test_engine) as session:
                # 1. Direct YouTube URL ingestion WITHOUT calling Fetch Info first
                bg_yt = BackgroundTasks()
                yt_req = YouTubeImportRequest(
                    url="https://youtu.be/Y4yTwDK37D0?si=0wvivLjRWRM_RB1C",
                    aspect_ratio="9:16",
                    clip_duration="30s",
                    num_clips=5,
                    framing_template="smart_auto",
                    language="English",
                    guidance="Storytime Arc",
                    ai_mode="offline",
                )
                yt_res = await main_mod.import_youtube(
                    req=yt_req,
                    background_tasks=bg_yt,
                    session=session,
                )
                assert yt_res["project_id"] > 0
                assert yt_res["job_id"] == yt_res["project_id"]
                assert yt_res["status"] == "processing"
                assert yt_res["normalized_url"] == "https://www.youtube.com/watch?v=Y4yTwDK37D0"
                assert len(bg_yt.tasks) == 1

                # Ensure normalized clean URL without ?si= was passed to yt-dlp non-blocking lookup
                assert seen_urls == [("https://www.youtube.com/watch?v=Y4yTwDK37D0", False)]

                yt_prog = jm.get_progress(yt_res["project_id"])
                assert yt_prog["project_id"] == yt_res["project_id"]
                assert yt_prog["source_url"] == "https://www.youtube.com/watch?v=Y4yTwDK37D0"

                # 2. Local file upload with alias parameters
                bg_file = BackgroundTasks()
                fake_file = _FakeUpload(
                    filename="local_podcast.mp4",
                    payload=b"\x00\x00\x00\x20ftypisom" + b"a" * 2048,
                )
                up_res = await main_mod.upload_video(
                    file=fake_file,
                    aspect_ratio="16:9",
                    clip_duration="45s",
                    num_clips=3,
                    framing_template="auto_track",
                    language="As spoken",
                    guidance="Key Takeaways",
                    ai_mode="offline",
                    background_tasks=bg_file,
                    session=session,
                )
                assert up_res["project_id"] > 0
                assert up_res["job_id"] == up_res["project_id"]
                assert up_res["status"] == "processing"
                assert up_res["ai_mode"] == "offline"
                assert up_res["default_framing"] == "auto_track"
                assert len(bg_file.tasks) == 1

                proj = session.get(Project, up_res["project_id"])
                assert proj is not None
                assert proj.duration_preset == "45s"
                assert proj.clip_count == 3
                assert proj.topic_focus == "Key Takeaways"

        _drive_coro(_run())
    finally:
        jm._jobs.clear()
        jm._jobs.update(saved_jobs)


def test_init_db_migrates_legacy_caption_style_presets_columns(tmp_path, monkeypatch):
    legacy_db_path = tmp_path / "legacy_clipship.db"
    conn = sqlite3.connect(str(legacy_db_path))
    conn.execute(
        """
        CREATE TABLE caption_style_presets (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL,
            font_family TEXT DEFAULT 'Montserrat',
            font_size_percent INTEGER DEFAULT 100
        )
        """
    )
    conn.commit()
    conn.close()

    legacy_engine = create_engine(
        f"sqlite:///{legacy_db_path}",
        connect_args={"check_same_thread": False},
    )
    monkeypatch.setattr(app_db, "engine", legacy_engine)

    app_db.init_db()

    conn2 = sqlite3.connect(str(legacy_db_path))
    cols = [r[1] for r in conn2.execute("PRAGMA table_info(caption_style_presets)").fetchall()]
    conn2.close()
    assert "highlight_power_words" in cols
    assert "outline_color" in cols
