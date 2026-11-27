import io
import json
import subprocess
import time
import wave
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import cv2
import numpy as np
import pytest
from sqlmodel import SQLModel, Session, create_engine

import app.database as db_mod
import app.main as main_mod
from app.config import settings
from app.models import PreviewFrameRequest, Project, UpdateClipRequest, ViralClip
from app.services import job_manager as jm_mod
from app.services.audio_analyzer import analyze_audio_track
from app.services.caption_renderer import render_caption_frame
from app.services.proxy import get_ffmpeg_cmd
from app.services.transcriber import (
    get_whisper_runtime_info,
    resolve_whisper_device_and_compute,
    transcribe_audio_full,
)
from app.services.video_analyzer import analyze_video_track


def _setup_isolated_db(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    db_file = tmp_path / "test_part3_v10.db"
    test_engine = create_engine(
        f"sqlite:///{db_file.as_posix()}",
        connect_args={"check_same_thread": False},
    )
    SQLModel.metadata.create_all(test_engine)

    storage_dir = tmp_path / "storage"
    for sub in ("masters", "renders", "previews", "assets", "thumbnails", "luts", "projects", "models"):
        (storage_dir / sub).mkdir(parents=True, exist_ok=True)

    monkeypatch.setattr(db_mod, "engine", test_engine)
    monkeypatch.setattr(jm_mod, "engine", test_engine)
    monkeypatch.setattr(settings, "STORAGE_DIR", str(storage_dir))
    return test_engine, storage_dir


def _make_sample_mp4(dst_path: Path, duration_sec: float = 6.0) -> Path:
    ffmpeg = get_ffmpeg_cmd()
    dst_path.parent.mkdir(parents=True, exist_ok=True)
    cmd = [
        ffmpeg, "-y",
        "-f", "lavfi", "-i", f"testsrc2=size=640x360:rate=15:duration={duration_sec:.2f}",
        "-f", "lavfi", "-i", f"sine=frequency=440:sample_rate=16000:duration={duration_sec:.2f}",
        "-c:v", "libx264", "-preset", "ultrafast", "-pix_fmt", "yuv420p",
        "-c:a", "aac", "-b:a", "64k",
        str(dst_path),
    ]
    proc = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
    assert proc.returncode == 0, f"FFmpeg test video creation failed: {proc.stderr[-300:]}"
    return dst_path


def _make_wav(dst_path: Path, duration_sec: float = 2.0) -> Path:
    sr = 16000
    t = np.linspace(0, duration_sec, int(sr * duration_sec), endpoint=False)
    pcm = (0.25 * np.sin(2.0 * np.pi * 220.0 * t) * 32767.0).astype(np.int16)
    with wave.open(str(dst_path), "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(sr)
        wf.writeframes(pcm.tobytes())
    return dst_path


def test_analyze_video_track_sequential_ffmpeg_cap_and_no_cv2_seek(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """
    Verify analyze_video_track uses sequential FFmpeg rawvideo streaming (never cv2.VideoCapture seeking),
    respects the max_frames cap, and executes quickly without hanging.
    """
    mp4 = _make_sample_mp4(tmp_path / "seq_test.mp4", duration_sec=6.0)

    def _forbid_cv2_cap(*args, **kwargs):
        raise AssertionError("analyze_video_track must NOT use cv2.VideoCapture random seeking!")

    monkeypatch.setattr(cv2, "VideoCapture", _forbid_cv2_cap)

    t0 = time.monotonic()
    res = analyze_video_track(str(mp4), sample_fps=4.0, max_frames=10, watchdog_sec=30.0)
    elapsed = time.monotonic() - t0

    assert elapsed < 15.0
    assert 4 <= len(res["frames"]) <= 10
    assert res["summary"]["frame_count"] <= 10
    assert res["summary"]["timed_out"] is False


def test_analyze_video_track_watchdog_timeout_returns_partial_safely(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """
    Simulate a hung FFmpeg rawvideo stream and verify the watchdog timeout terminates it cleanly
    and returns partial/valid telemetry within ~2.5s instead of freezing at 67%.
    """
    mp4 = _make_sample_mp4(tmp_path / "hung_test.mp4", duration_sec=2.0)

    class _SlowPipe:
        def __init__(self):
            self.closed = False

        def read(self, n: int) -> bytes:
            time.sleep(0.35)
            return b"" if self.closed else b""

        def close(self):
            self.closed = True

    class _HungProc:
        def __init__(self, *args, **kwargs):
            self.stdout = _SlowPipe()
            self._killed = False

        def poll(self):
            return 0 if self._killed else None

        def kill(self):
            self._killed = True
            self.stdout.close()

        def wait(self, timeout=None):
            return 0

    # Deliver one slow chunk then hang past 2s watchdog
    frame_size = 426 * 240 * 3

    class _HangingOncePipe:
        def __init__(self):
            self.count = 0
            self.closed = False

        def read(self, n: int) -> bytes:
            self.count += 1
            if self.count == 1:
                return bytes(n)
            for _ in range(30):
                if self.closed:
                    return b""
                time.sleep(0.15)
            return b""

        def close(self):
            self.closed = True

    class _HangingOnceProc(_HungProc):
        def __init__(self, *args, **kwargs):
            super().__init__(*args, **kwargs)
            self.stdout = _HangingOncePipe()

    monkeypatch.setattr("app.services.video_analyzer.subprocess.Popen", _HangingOnceProc)

    t0 = time.monotonic()
    res = analyze_video_track(str(mp4), sample_fps=2.0, max_frames=50, watchdog_sec=2.0)
    elapsed = time.monotonic() - t0

    assert elapsed < 4.5, f"Watchdog took too long: {elapsed:.2f}s"
    assert res["summary"]["timed_out"] is True
    assert len(res["frames"]) == 1


def test_transcriber_cuda_float16_and_cpu_int8_batched_plus_checkpoints_and_zombie_cleanup(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    """
    Verify CUDA/CPU device selection via ctranslate2, beam_size=1 + BatchedInferencePipeline args,
    startup zombie-job cleanup (freezing running timers + marking failed stages), and fast multimodal checkpointing.
    """
    test_engine, storage_dir = _setup_isolated_db(tmp_path, monkeypatch)

    # 1. Explicit CUDA vs CPU selection
    with patch("app.services.transcriber.register_cuda_dll_directories"), \
         patch("ctranslate2.get_cuda_device_count", return_value=1), \
         patch("app.services.hardware.detect_hardware_specs", return_value={"has_cuda": True, "vram_mb": 8192}):
        dev, comp, _ = resolve_whisper_device_and_compute()
        assert dev == "cuda"
        assert comp == "float16"

    with patch("app.services.transcriber.register_cuda_dll_directories"), \
         patch("ctranslate2.get_cuda_device_count", return_value=0), \
         patch("app.services.hardware.detect_hardware_specs", return_value={"has_cuda": False, "vram_mb": 0}):
        dev_cpu, comp_cpu, _ = resolve_whisper_device_and_compute()
        assert dev_cpu == "cpu"
        assert comp_cpu == "int8"

    wav = _make_wav(tmp_path / "whisper_speed.wav", duration_sec=2.0)
    captured = []

    class FakeBatched:
        def __init__(self, model):
            self.model = model

        def transcribe(self, path, **kwargs):
            captured.append(kwargs)
            segs = [SimpleNamespace(end=1.8, words=[SimpleNamespace(word=" Fast", start=0.1, end=0.9)])]
            return iter(segs), SimpleNamespace(duration=2.0)

    with patch("app.services.transcriber.get_whisper_model", return_value=object()), \
         patch("app.services.transcriber.get_whisper_runtime_info", return_value={
             "device": "cpu", "compute_type": "int8", "model_name": "base", "batch_size": 4, "badge": "CPU int8"
         }), \
         patch("faster_whisper.BatchedInferencePipeline", FakeBatched):
        words = transcribe_audio_full(str(wav), language="English")
    assert len(words) == 1
    assert captured and captured[0]["batch_size"] == 4 and captured[0]["beam_size"] == 1

    # 2. Zombie orphan job recovery freezes timer & marks running stages failed
    jm = jm_mod.JobManager()
    p_dir = storage_dir / "projects" / "7701"
    p_dir.mkdir(parents=True, exist_ok=True)
    stale_json = {
        "project_id": 7701,
        "status": "running",
        "started_at": "2020-01-01T00:00:00+00:00",
        "active_started_at": "2020-01-01T00:00:00+00:00",
        "accumulated_seconds": 10.0,
        "stages": [{"id": "transcribe", "status": "done"}, {"id": "gemini", "status": "running"}],
        "logs": [],
    }
    (p_dir / "project.json").write_text(json.dumps(stale_json), encoding="utf-8")
    with Session(test_engine) as session:
        session.add(Project(id=7701, title="Zombie", source_path="x.mp4", status="processing"))
        session.commit()

    jm.cleanup_orphan_jobs()
    reloaded = json.loads((p_dir / "project.json").read_text(encoding="utf-8"))
    assert reloaded["status"] == "error"
    assert "Retry" in reloaded["stage_label"]
    assert reloaded["active_started_at"] is None
    assert reloaded["stages"][1]["status"] == "failed"

    # 3. Fast audio prosody check
    aud_res = analyze_audio_track(str(wav), word_segments=words)
    assert len(aud_res["timeline"]) >= 4


def test_freeform_caption_coordinates_rotation_and_edge_clamping(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """
    Verify caption_x, caption_y, caption_rotation (-15..+15 deg), caption_max_width, and
    >= 24px edge clamping in caption_renderer and persistence through update_clip/preview_frame.
    """
    test_engine, storage_dir = _setup_isolated_db(tmp_path, monkeypatch)
    words = [
        {"word": "FREEFORM", "start": 0.1, "end": 0.6},
        {"word": "DRAGGING", "start": 0.6, "end": 1.2},
        {"word": "CAPTIONS", "start": 1.2, "end": 1.9},
    ]

    w, h = 720, 1280
    img_tl = render_caption_frame(
        t=0.7,
        width=w,
        height=h,
        words=words,
        burn_captions=True,
        caption_x=0.05,
        caption_y=0.08,
        caption_rotation=-15.0,
        caption_max_width=0.50,
    )
    bb_tl = img_tl.getbbox()
    assert bb_tl is not None
    # Strictly >= 24px margin on all 4 sides
    assert bb_tl[0] >= 24 and bb_tl[1] >= 24
    assert bb_tl[2] <= w - 24 and bb_tl[3] <= h - 24
    cx_tl = 0.5 * (bb_tl[0] + bb_tl[2])
    cy_tl = 0.5 * (bb_tl[1] + bb_tl[3])

    img_br = render_caption_frame(
        t=0.7,
        width=w,
        height=h,
        words=words,
        burn_captions=True,
        caption_x=0.95,
        caption_y=0.92,
        caption_rotation=15.0,
        caption_max_width=0.50,
    )
    bb_br = img_br.getbbox()
    assert bb_br is not None
    assert bb_br[0] >= 24 and bb_br[1] >= 24
    assert bb_br[2] <= w - 24 and bb_br[3] <= h - 24
    cx_br = 0.5 * (bb_br[0] + bb_br[2])
    cy_br = 0.5 * (bb_br[1] + bb_br[3])

    # Top-left coordinates render significantly upper-left compared to bottom-right coordinates
    assert cx_tl < w * 0.45 and cy_tl < h * 0.35
    assert cx_br > w * 0.55 and cy_br > h * 0.70

    # Persist freeform position in SQLite and render live preview frame
    src_mp4 = _make_sample_mp4(tmp_path / "cap_src.mp4", duration_sec=2.0)
    with Session(test_engine) as session:
        proj = Project(title="Freeform Cap", source_type="upload", source_path=str(src_mp4), duration=2.0, status="finished")
        session.add(proj)
        session.commit()
        session.refresh(proj)

        clip = ViralClip(project_id=proj.id, start_time=0.0, end_time=2.0, duration=2.0)
        clip.set_transcript(words)
        session.add(clip)
        session.commit()
        session.refresh(clip)

        main_mod.update_clip(
            clip.id,
            UpdateClipRequest(
                caption_x=0.22,
                caption_y=0.32,
                caption_rotation=10.5,
                caption_max_width=0.62,
            ),
            session=session,
        )
        session.refresh(clip)
        ser = main_mod._serialize_clip(clip)
        assert ser["caption_x"] == pytest.approx(0.22)
        assert ser["caption_y"] == pytest.approx(0.32)
        assert ser["position_x"] == pytest.approx(22.0)
        assert ser["position_y"] == pytest.approx(32.0)
        assert ser["caption_rotation"] == pytest.approx(10.5)
        assert ser["caption_max_width"] == pytest.approx(0.62)

        prev = main_mod.preview_frame_endpoint(
            PreviewFrameRequest(
                project_id=proj.id,
                clip_id=clip.id,
                timestamp=0.8,
                burn_captions=True,
                caption_x=0.22,
                caption_y=0.32,
                caption_rotation=10.5,
                caption_max_width=0.62,
            ),
            session=session,
        )
        assert (storage_dir / "previews" / Path(prev["frame_url"]).name).exists()
