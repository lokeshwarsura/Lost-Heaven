"""Phase 4 verification tests: Project tags, cloning protection, horizon tilt, voice enhancer, stage retry & throughput speed, YouTube MM:SS range + podcast mode."""

import sys
import types
from pathlib import Path
import pytest
from fastapi import BackgroundTasks, HTTPException
from sqlmodel import Session, SQLModel, create_engine, select


def _run_coro_sync(coro):
    try:
        while True:
            coro.send(None)
    except StopIteration as exc:
        return exc.value
    finally:
        coro.close()


def test_project_tags_and_safe_duplicate_clone(tmp_path, monkeypatch):
    from app.config import settings
    from app.models import Project, ViralClip
    from app.main import (
        update_project,
        duplicate_project_endpoint,
        _delete_single_project_and_files,
        _serialize_clip,
    )

    monkeypatch.setattr(settings, "STORAGE_DIR", str(tmp_path / "storage"))
    (tmp_path / "storage" / "masters").mkdir(parents=True, exist_ok=True)

    src_file = tmp_path / "storage" / "masters" / "pod_src.mp4"
    src_file.write_bytes(b"fake_video_bytes_123456")
    proxy_file = tmp_path / "storage" / "masters" / "pod_proxy.mp4"
    proxy_file.write_bytes(b"fake_proxy_bytes_123456")

    db_path = tmp_path / "phase4_vault.db"
    eng = create_engine(f"sqlite:///{db_path}", connect_args={"check_same_thread": False})
    SQLModel.metadata.create_all(eng)

    with Session(eng) as session:
        proj = Project(
            title="Founder Podcast Ep 12",
            source_type="local",
            source_filename="pod_src.mp4",
            source_path=str(src_file),
            proxy_path=str(proxy_file),
            status="ready",
        )
        session.add(proj)
        session.commit()
        session.refresh(proj)

        clip = ViralClip(
            project_id=proj.id,
            start_time=1.0,
            end_time=16.0,
            duration=15.0,
            selected_title="Hook Clip",
            aspect_ratio="9:16",
        )
        clip.set_options({"horizon_tilt": 3.5, "voice_enhancer": True})
        session.add(clip)
        session.commit()
        session.refresh(clip)

        # Serialization exposes Phase 4 options cleanly
        c_ser = _serialize_clip(clip, proj)
        assert c_ser["horizon_tilt"] == 3.5
        assert c_ser["voice_enhancer"] is True

        # Custom Project Tags + Podcast Mode persistence
        upd = update_project(
            proj.id,
            {"tags": ["Podcast", "Launch", "Podcast"], "podcast_mode": True},
            session=session,
        )
        assert upd["project"]["tags"] == ["Podcast", "Launch"]
        assert upd["project"]["podcast_mode"] is True

        # Non-destructive project cloning
        dup = duplicate_project_endpoint(proj.id, session=session)
        assert dup["status"] == "duplicated"
        assert dup["clips_cloned"] == 1
        cloned_id = dup["project"]["id"]
        assert dup["project"]["title"] == "Founder Podcast Ep 12 (Copy)"
        assert dup["project"]["tags"] == ["Podcast", "Launch"]
        assert dup["project"]["podcast_mode"] is True

        cloned_clips = session.exec(select(ViralClip).where(ViralClip.project_id == cloned_id)).all()
        assert len(cloned_clips) == 1
        assert cloned_clips[0].render_status == "idle"
        assert cloned_clips[0].get_options().get("horizon_tilt") == 3.5

        # Deleting cloned copy MUST NOT delete shared source or proxy files still owned by original
        _delete_single_project_and_files(cloned_id, session)
        session.commit()
        assert src_file.exists(), "Original source file was deleted when cloned duplicate was removed!"
        assert proxy_file.exists(), "Original proxy file was deleted when cloned duplicate was removed!"


def test_horizon_tilt_and_voice_enhancer_filters():
    from app.services.filters import build_advanced_color_filter
    from app.services.audio_mastering import build_voice_filter_chain

    # Zero tilt emits no rotate filter
    plain = build_advanced_color_filter(preset="none", horizon_tilt=0.0, target_w=1080, target_h=1920)
    assert "rotate=" not in plain

    # Non-zero tilt rotates + upscales + crops to exact target resolution to hide black corners
    tilted = build_advanced_color_filter(preset="none", horizon_tilt=4.5, target_w=1080, target_h=1920)
    assert "rotate=" in tilted
    assert "crop=1080:1920:" in tilted

    # Studio Voice Enhancer emits 80Hz highpass, presence EQ, compressor, and -14 LUFS loudnorm
    chain = build_voice_filter_chain(voice_enhancer=True, normalize_audio=False, loudness_target="-14 LUFS")
    assert "highpass=f=80" in chain
    assert "equalizer=f=3200:t=q:w=1.2:g=3.0" in chain
    assert "acompressor=" in chain
    assert "loudnorm=I=-14:TP=-1.5:LRA=11" in chain


def test_job_manager_throughput_and_stage_retry(tmp_path, monkeypatch):
    from app.config import settings
    from app.services.job_manager import JobManager, _normalize_job_payload

    monkeypatch.setattr(settings, "STORAGE_DIR", str(tmp_path / "storage"))
    jm = JobManager()

    payload = {
        "project_id": 9901,
        "status": "processing",
        "duration_sec": 120.0,
        "accumulated_seconds": 10.0,
    }
    _normalize_job_payload(payload)
    assert payload["throughput_speed"] == 12.0

    # Prepare cached artifacts for stage-level retry
    p_dir = jm._get_project_dir(9902)
    audio_file = p_dir / "audio_16k.wav"
    proxy_file = p_dir / "proxy_480p.mp4"
    transcript_file = p_dir / "transcript.json"
    audio_file.write_bytes(b"a" * 2048)
    proxy_file.write_bytes(b"p" * 2048)
    transcript_file.write_text('[{"word": "hi", "start": 0.0, "end": 0.5}]', encoding="utf-8")

    # Retrying from stage='transcribe' deletes transcript.json while preserving proxy & audio
    res_job = jm.retry_pipeline(9902, background_tasks=None, force_offline=True, stage="transcribe")
    assert not transcript_file.exists()
    assert proxy_file.exists()
    assert res_job["current_stage"] == "transcribe"

    # Retrying from stage='audio' wipes audio, proxy & transcript to restart cleanly from Media Prep
    res_job2 = jm.retry_pipeline(9902, background_tasks=None, force_offline=True, stage="audio")
    assert not audio_file.exists()
    assert not proxy_file.exists()
    assert res_job2["current_stage"] == "ingest"


def test_youtube_mmss_range_and_podcast_mode_ingestion(tmp_path, monkeypatch):
    from app.config import settings
    import app.database as app_db
    import app.main as main_mod
    from app.models import Project, YouTubeImportRequest

    monkeypatch.setattr(settings, "STORAGE_DIR", str(tmp_path / "storage"))
    db_path = tmp_path / "phase4_yt.db"
    eng = create_engine(f"sqlite:///{db_path}", connect_args={"check_same_thread": False})
    SQLModel.metadata.create_all(eng)
    monkeypatch.setattr(app_db, "engine", eng)

    class _FakeYDL:
        def __init__(self, opts):
            self.opts = opts

        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

        def extract_info(self, url, download=False):
            return {"id": "dQw4w9WgXcQ", "title": "Podcast Range Test"}

        def prepare_filename(self, info):
            return str(tmp_path / "storage" / "masters" / f"{info['id']}.mp4")

    fake_yt_dlp = types.ModuleType("yt_dlp")
    fake_yt_dlp.YoutubeDL = _FakeYDL
    monkeypatch.setitem(sys.modules, "yt_dlp", fake_yt_dlp)

    with Session(eng) as session:
        # Invalid range where end <= start must return HTTP 400
        bad_req = YouTubeImportRequest(
            url="https://youtu.be/dQw4w9WgXcQ",
            start_time="05:00",
            end_time="02:00",
        )
        with pytest.raises(HTTPException) as exc_info:
            _run_coro_sync(main_mod.import_youtube(bad_req, BackgroundTasks(), session))
        assert exc_info.value.status_code == 400

        # Valid MM:SS range + podcast_mode persists on Project and JobManager
        ok_req = YouTubeImportRequest(
            url="https://youtu.be/dQw4w9WgXcQ?si=sharetag",
            start_time="01:15",
            end_time="03:45",
            podcast_mode=True,
            ai_mode="offline",
        )
        res = _run_coro_sync(main_mod.import_youtube(ok_req, BackgroundTasks(), session))
        assert res["start_sec"] == 75.0
        assert res["end_sec"] == 225.0
        assert res["podcast_mode"] is True

        p = session.get(Project, res["project_id"])
        assert p is not None and p.podcast_mode is True
        prog = main_mod.job_manager.get_progress(res["project_id"])
        assert prog.get("podcast_mode") is True
