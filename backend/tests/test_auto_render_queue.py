import os
import subprocess
import threading
import time
from pathlib import Path

import pytest
from fastapi import BackgroundTasks
from sqlmodel import Session, SQLModel, create_engine

import app.database as app_db
from app.config import settings
from app.main import stream_latest_clip_video, trigger_render
from app.models import Project, RenderClipRequest, ViralClip
from app.services import renderer
from app.services.proxy import get_ffmpeg_cmd


def _make_source_mp4(out_path: Path, duration_sec: float = 4.0) -> Path:
    out_path.parent.mkdir(parents=True, exist_ok=True)
    cmd = [
        get_ffmpeg_cmd(),
        "-y",
        "-f", "lavfi",
        "-i", f"testsrc2=size=640x360:rate=15:duration={duration_sec}",
        "-f", "lavfi",
        "-i", f"sine=frequency=240:sample_rate=44100:duration={duration_sec}",
        "-c:v", "libx264",
        "-preset", "ultrafast",
        "-pix_fmt", "yuv420p",
        "-c:a", "aac",
        "-shortest",
        str(out_path),
    ]
    res = subprocess.run(cmd, capture_output=True, text=True, timeout=60)
    assert res.returncode == 0, res.stderr
    return out_path


@pytest.fixture()
def queue_env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    storage_dir = tmp_path / "storage_queue"
    storage_dir.mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr(settings, "STORAGE_DIR", str(storage_dir))

    test_engine = create_engine(
        f"sqlite:///{tmp_path / 'queue_test.db'}",
        connect_args={"check_same_thread": False},
    )
    SQLModel.metadata.create_all(test_engine)
    monkeypatch.setattr(app_db, "engine", test_engine)

    src_mp4 = _make_source_mp4(tmp_path / "source.mp4", duration_sec=4.0)

    with Session(test_engine) as session:
        proj = Project(title="Queue Test", source_path=str(src_mp4))
        session.add(proj)
        session.commit()
        session.refresh(proj)

        clip = ViralClip(
            project_id=proj.id,
            start_time=0.0,
            end_time=3.5,
            duration=3.5,
            frame_layout="fit_blur",
            burn_captions=False,
            hardware_mode="cpu",
        )
        session.add(clip)
        session.commit()
        session.refresh(clip)
        return storage_dir, test_engine, proj.id, clip.id


def test_versioned_output_and_windows_permission_error_safe(queue_env, monkeypatch: pytest.MonkeyPatch):
    storage_dir, test_engine, proj_id, clip_id = queue_env

    with Session(test_engine) as s:
        clip = s.get(ViralClip, clip_id)
        proj = s.get(Project, proj_id)
        src_path = proj.source_path

    p1 = renderer.render_clip_ultra(
        master_video_path=src_path,
        clip_id=clip_id,
        project_id=proj_id,
        start_time=0.0,
        duration=1.5,
        words=[],
        frame_layout="fit_blur",
        burn_captions=False,
        hardware_mode="cpu",
        quality_mode="fast_preview",
    )
    assert Path(p1).exists()
    assert f"clip_{clip_id}_r" in Path(p1).name

    # Simulate Windows browser file-lock PermissionError when unlinking older render
    orig_unlink = Path.unlink

    def guarded_unlink(self, *args, **kwargs):
        if Path(self).resolve() == Path(p1).resolve():
            raise PermissionError("Locked by browser <video> stream")
        return orig_unlink(self, *args, **kwargs)

    monkeypatch.setattr(Path, "unlink", guarded_unlink)
    time.sleep(0.02)

    p2 = renderer.render_clip_ultra(
        master_video_path=src_path,
        clip_id=clip_id,
        project_id=proj_id,
        start_time=0.0,
        duration=1.5,
        words=[],
        frame_layout="fit_blur",
        burn_captions=False,
        hardware_mode="cpu",
        quality_mode="fast_preview",
    )
    assert Path(p2).exists()
    assert Path(p1).resolve() != Path(p2).resolve()
    assert Path(p1).exists()  # gracefully kept when locked


def test_supersede_cancels_active_ffmpeg_and_returns_superseded(queue_env):
    storage_dir, test_engine, proj_id, clip_id = queue_env
    clip_key = f"{proj_id}:{clip_id}"

    results = {}

    def run_req(tag: str):
        with Session(test_engine) as s:
            results[tag] = trigger_render(
                clip_id=clip_id,
                background_tasks=BackgroundTasks(),
                project_id=proj_id,
                req=RenderClipRequest(quality_mode="fast_preview"),
                session=s,
            )

    t1 = threading.Thread(target=run_req, args=("first",), daemon=True)
    t1.start()

    # Wait until first render enters active FFmpeg subprocess or tokens map
    deadline = time.time() + 10.0
    while time.time() < deadline:
        with renderer._render_lock:
            if clip_key in renderer._active_renders:
                break
        time.sleep(0.05)

    t2 = threading.Thread(target=run_req, args=("second",), daemon=True)
    t2.start()

    t1.join(timeout=30.0)
    t2.join(timeout=30.0)

    assert "first" in results and "second" in results
    assert results["first"]["status"] == "superseded"
    assert results["second"]["status"] == "completed"
    assert results["second"]["quality_mode"] == "fast_preview"
    assert f"/api/clips/{proj_id}/{clip_id}/video" in results["second"]["video_url"]

    with renderer._render_lock:
        assert clip_key not in renderer._active_renders

    with Session(test_engine) as s:
        resp = stream_latest_clip_video(clip_id=clip_id, project_id=proj_id, session=s)
        assert "no-store" in resp.headers.get("Cache-Control", "")
        assert os.path.exists(resp.path)
