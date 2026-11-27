"""
Verification suite for Studio Project Vault (v11):
1. pytest session isolation via conftest.py keeps tests out of production backend/storage/clipship.db.
2. GET /api/projects/{id}/thumbnail extracts & caches a crisp 480x270 JPEG from 15% into source video,
   and returns 204 for missing / 0 B files.
3. POST /api/projects/cleanup handles 'test_and_empty', 'failed_and_cancelled', and 'render_cache'.
4. POST /api/projects/bulk-delete removes selected projects & reclaims disk usage accurately.
5. POST /api/projects/{id}/retry (and /start) starts CREATED / ERROR / CANCELLED projects.
"""

import os
import subprocess
import sys
from pathlib import Path
from unittest.mock import patch

import cv2
import numpy as np
from fastapi import BackgroundTasks
from sqlmodel import Session, SQLModel, create_engine, select

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

import app.database as app_db
import app.main as main_mod
import app.services.job_manager as jm_mod
from app.config import BASE_DIR, settings
from app.models import (
    BulkDeleteProjectsRequest,
    Project,
    ProjectCleanupRequest,
    ViralClip,
)
from app.services.proxy import get_ffmpeg_cmd


def _create_synthetic_mp4(out_path: Path, duration_sec: float = 3.0) -> Path:
    out_path.parent.mkdir(parents=True, exist_ok=True)
    cmd = [
        get_ffmpeg_cmd(),
        "-y",
        "-f",
        "lavfi",
        "-i",
        f"testsrc2=size=640x360:rate=15:duration={duration_sec}",
        "-c:v",
        "libx264",
        "-preset",
        "ultrafast",
        "-pix_fmt",
        "yuv420p",
        str(out_path),
    ]
    res = subprocess.run(cmd, capture_output=True, text=True, timeout=60)
    assert res.returncode == 0, f"Synthetic video creation failed: {res.stderr}"
    return out_path


def _isolated_vault_env(tmp_path: Path, monkeypatch):
    storage_dir = tmp_path / "vault_storage"
    for sub in ("masters", "proxies", "renders", "captions", "temp", "projects", "assets", "previews", "thumbnails"):
        (storage_dir / sub).mkdir(parents=True, exist_ok=True)
    db_path = tmp_path / "vault_test.db"
    test_engine = create_engine(f"sqlite:///{db_path}", connect_args={"check_same_thread": False})
    SQLModel.metadata.create_all(test_engine)

    monkeypatch.setattr(settings, "STORAGE_DIR", str(storage_dir))
    monkeypatch.setattr(app_db, "engine", test_engine)
    monkeypatch.setattr(jm_mod, "engine", test_engine)
    return storage_dir, test_engine


def test_pytest_conftest_isolates_from_production_clipship_db():
    prod_db = (BASE_DIR / "storage" / "clipship.db").resolve()
    prod_storage = (BASE_DIR / "storage").resolve()
    assert Path(app_db.SQLITE_DB_PATH).resolve() != prod_db
    assert Path(settings.STORAGE_DIR).resolve() != prod_storage


def test_project_thumbnail_extraction_caching_and_204_for_empty(tmp_path: Path, monkeypatch):
    storage_dir, test_engine = _isolated_vault_env(tmp_path, monkeypatch)
    real_vid = _create_synthetic_mp4(storage_dir / "masters" / "creator_clip.mp4", duration_sec=3.0)
    zero_vid = storage_dir / "masters" / "empty_test.mp4"
    zero_vid.write_bytes(b"")

    with Session(test_engine) as session:
        p_valid = Project(
            title="Real Studio Recording",
            source_type="local",
            source_filename="creator_clip.mp4",
            source_path=str(real_vid),
            duration=3.0,
            resolution_w=640,
            resolution_h=360,
            status="analyzed",
        )
        p_empty = Project(
            title="Missing Key Retry Test",
            source_type="local",
            source_filename="empty_test.mp4",
            source_path=str(zero_vid),
            status="error",
        )
        session.add(p_valid)
        session.add(p_empty)
        session.commit()
        session.refresh(p_valid)
        session.refresh(p_empty)

        thumb_resp = main_mod.get_project_thumbnail(p_valid.id, session=session)
        assert thumb_resp.status_code == 200
        assert thumb_resp.media_type == "image/jpeg"

        cached_jpg = storage_dir / "projects" / str(p_valid.id) / "source_thumb.jpg"
        assert cached_jpg.exists() and cached_jpg.stat().st_size > 500
        decoded = cv2.imdecode(np.fromfile(str(cached_jpg), dtype=np.uint8), cv2.IMREAD_COLOR)
        assert decoded is not None
        assert decoded.shape[:2] == (270, 480)

        empty_resp = main_mod.get_project_thumbnail(p_empty.id, session=session)
        assert empty_resp.status_code == 204

        vault_list = main_mod.list_projects(session=session)
        by_id = {row["id"]: row for row in vault_list["projects"]}
        assert by_id[p_valid.id]["thumbnail_url"] == f"/api/projects/{p_valid.id}/thumbnail"
        assert by_id[p_valid.id]["disk_bytes"] >= real_vid.stat().st_size
        assert by_id[p_empty.id]["source_missing"] is True
        assert by_id[p_empty.id]["thumbnail_url"] is None


def test_cleanup_modes_and_bulk_delete_and_start_retry(tmp_path: Path, monkeypatch):
    storage_dir, test_engine = _isolated_vault_env(tmp_path, monkeypatch)

    real_analyzed_vid = _create_synthetic_mp4(storage_dir / "masters" / "analyzed_master.mp4", duration_sec=2.5)
    real_created_vid = _create_synthetic_mp4(storage_dir / "masters" / "pending_master.mp4", duration_sec=2.5)
    rendered_clip_file = storage_dir / "renders" / "clip_out.mp4"
    rendered_clip_file.write_bytes(b"MP4DATA" * 4096)

    dummy_zero = storage_dir / "masters" / "source.mp4"
    dummy_zero.write_bytes(b"")
    failed_vid = storage_dir / "masters" / "failed_master.mp4"
    failed_vid.write_bytes(b"FAIL" * 2048)

    with Session(test_engine) as session:
        p_analyzed = Project(
            title="Analyzed Keeper",
            source_filename="analyzed_master.mp4",
            source_path=str(real_analyzed_vid),
            duration=2.5,
            status="analyzed",
        )
        p_created = Project(
            title="Needs Analysis Keeper",
            source_filename="pending_master.mp4",
            source_path=str(real_created_vid),
            duration=2.5,
            status="created",
        )
        p_dummy_zero = Project(
            title="Synthetic Dummy",
            source_filename="source.mp4",
            source_path=str(dummy_zero),
            status="created",
        )
        p_orphan_missing = Project(
            title="Stuck Orphan Job",
            source_filename="ghost.mp4",
            source_path=str(storage_dir / "masters" / "does_not_exist.mp4"),
            status="error",
        )
        p_failed = Project(
            title="Failed Render Job",
            source_filename="failed_master.mp4",
            source_path=str(failed_vid),
            duration=4.0,
            status="cancelled",
        )
        for p in (p_analyzed, p_created, p_dummy_zero, p_orphan_missing, p_failed):
            session.add(p)
        session.commit()
        for p in (p_analyzed, p_created, p_dummy_zero, p_orphan_missing, p_failed):
            session.refresh(p)

        clip = ViralClip(
            project_id=p_analyzed.id,
            start_time=0.0,
            end_time=2.5,
            duration=2.5,
            rendered_video_path=str(rendered_clip_file),
            render_status="completed",
        )
        session.add(clip)
        session.commit()

        # Populate render cache artifacts that should be purged by render_cache cleanup
        temp_preview = storage_dir / "previews" / "frame_cache.jpg"
        temp_preview.write_bytes(b"JPG" * 2048)
        proj_cache = storage_dir / "projects" / str(p_analyzed.id) / "audio_16k.wav"
        proj_cache.parent.mkdir(parents=True, exist_ok=True)
        proj_cache.write_bytes(b"RIFF" * 1024)

        # 1. test_and_empty cleanup drops dummy/orphan rows but preserves valid analyzed & created recordings
        res_test = main_mod.cleanup_projects_endpoint(
            ProjectCleanupRequest(mode="test_and_empty"),
            session=session,
        )
        assert set(res_test["deleted_ids"]) == {p_dummy_zero.id, p_orphan_missing.id}
        remaining_ids = {p.id for p in session.exec(select(Project)).all()}
        assert remaining_ids == {p_analyzed.id, p_created.id, p_failed.id}

        # 2. render_cache cleanup purges temp preview & audio_16k.wav while keeping master & exported MP4 clip
        res_cache = main_mod.cleanup_projects_endpoint(
            ProjectCleanupRequest(mode="render_cache"),
            session=session,
        )
        assert res_cache["reclaimed_bytes"] > 0
        assert not temp_preview.exists()
        assert not proj_cache.exists()
        assert real_analyzed_vid.exists()
        assert rendered_clip_file.exists()

        # 3. failed_and_cancelled removes failed/cancelled projects
        res_fail = main_mod.cleanup_projects_endpoint(
            ProjectCleanupRequest(mode="failed_and_cancelled"),
            session=session,
        )
        assert res_fail["deleted_ids"] == [p_failed.id]
        assert not failed_vid.exists()

        # 4. Retry / Start on CREATED project transitions state to processing cleanly
        bg = BackgroundTasks()
        with patch.object(jm_mod.job_manager, "run_pipeline"):
            retry_res = main_mod.retry_project_job_endpoint(p_created.id, None, bg)
            assert retry_res["status"] == "processing"
            assert retry_res["project_id"] == p_created.id

        # 5. Bulk delete deletes selected projects and shrinks total_disk_bytes
        before = main_mod.list_projects(session=session)
        assert before["total_disk_bytes"] > 0
        bulk_res = main_mod.bulk_delete_projects_endpoint(
            BulkDeleteProjectsRequest(project_ids=[p_analyzed.id, p_created.id]),
            session=session,
        )
        assert set(bulk_res["deleted_ids"]) == {p_analyzed.id, p_created.id}
        assert bulk_res["deleted_count"] == 2
        assert bulk_res["reclaimed_bytes"] > 0
        assert not real_analyzed_vid.exists()
        assert not rendered_clip_file.exists()

        after = main_mod.list_projects(session=session)
        assert after["total_projects"] == 0
        assert after["total_disk_bytes"] == 0
