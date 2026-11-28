import subprocess
from pathlib import Path

import cv2
import numpy as np
import pytest
from PIL import Image
from fastapi import BackgroundTasks
from sqlmodel import SQLModel, create_engine, Session

import app.database as db_mod
import app.main as main_mod
from app.config import settings
from app.models import (
    PreviewFrameRequest,
    Project,
    RenderClipRequest,
    UpdateClipRequest,
    ViralClip,
)
from app.services.proxy import get_ffmpeg_cmd
from app.services.render_queue import compute_render_signature
from app.services.renderer import render_clip_ultra
from app.services.timeline_editor import build_timeline_edit_plan


def _setup_isolated_db(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    storage_dir = tmp_path / "storage"
    for sub in ("masters", "proxies", "renders", "previews", "captions", "projects"):
        (storage_dir / sub).mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr(settings, "STORAGE_DIR", str(storage_dir))

    db_path = tmp_path / "v19_template_chain.db"
    test_engine = create_engine(
        f"sqlite:///{db_path}",
        echo=False,
        connect_args={"check_same_thread": False},
    )
    SQLModel.metadata.create_all(test_engine)
    monkeypatch.setattr(db_mod, "engine", test_engine)
    return test_engine, storage_dir


def _make_12s_timeline_template_source(out_path: Path) -> Path:
    """
    Create a 15s 1280x720 source video:
    - Base green gameplay (#1C7C38)
    - Top-left corner magenta webcam card (#E020C8) at x=20,y=20,w=360,h=260
    - Silent dead-air pause in [2.1s..4.4s] and loud climax tone + bright cyan center in [10.5s..14.2s]
    """
    ffmpeg = get_ffmpeg_cmd()
    out_path.parent.mkdir(parents=True, exist_ok=True)
    vf = (
        "drawbox=x=20:y=20:w=360:h=260:color=#E020C8@1.0:t=fill,"
        "drawbox=x=460:y=220:w=520:h=340:color=#00D8FF@0.95:t=fill:enable='between(t,10.5,14.2)'"
    )
    a_expr = (
        "if(between(t,2.1,4.4),0,"
        "if(between(t,10.5,14.2),0.75*sin(2*PI*440*t),0.12*sin(2*PI*220*t)))"
    )
    res = subprocess.run(
        [
            ffmpeg,
            "-y",
            "-f",
            "lavfi",
            "-i",
            "color=c=#1C7C38:size=1280x720:rate=24:duration=15.00",
            "-f",
            "lavfi",
            "-i",
            f"aevalsrc='{a_expr}':s=44100:d=15.00",
            "-vf",
            vf,
            "-c:v",
            "libx264",
            "-preset",
            "ultrafast",
            "-pix_fmt",
            "yuv420p",
            "-c:a",
            "aac",
            "-ar",
            "44100",
            "-ac",
            "1",
            str(out_path),
        ],
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert res.returncode == 0, res.stderr
    return out_path


def _read_mp4_frame_at_ms(mp4_path: str, ms: float) -> np.ndarray:
    cap = cv2.VideoCapture(mp4_path)
    cap.set(cv2.CAP_PROP_POS_MSEC, ms)
    ok, frame = cap.read()
    cap.release()
    assert ok and frame is not None, f"Failed reading frame at {ms}ms from {mp4_path}"
    return cv2.cvtColor(frame, cv2.COLOR_BGR2RGB).astype(np.float32)


def _read_preview_rgb(frame_url: str) -> np.ndarray:
    path = Path(settings.STORAGE_DIR) / "previews" / Path(frame_url).name
    return np.array(Image.open(path).convert("RGB"), dtype=np.float32)


def test_framing_template_authoritative_patch_route_and_crop_override_clear(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    test_engine, _ = _setup_isolated_db(tmp_path, monkeypatch)
    src = _make_12s_timeline_template_source(tmp_path / "media" / "patch_src.mp4")

    with Session(test_engine) as session:
        proj = Project(title="Auth Tpl", source_type="local", source_path=str(src), duration=12.0)
        session.add(proj)
        session.commit()
        session.refresh(proj)

        clip = ViralClip(
            project_id=proj.id,
            title="Clip 1",
            start_time=0.0,
            end_time=12.0,
            duration=12.0,
            frame_layout="auto_track",
        )
        # Simulate stale manual crop override left over from Edit Crop Box
        clip.set_framing_override(
            {
                "frame_crop": {"x": 0.02, "y": 0.02, "w": 0.30, "h": 0.90},
                "lock_camera": True,
            }
        )
        session.add(clip)
        session.commit()
        session.refresh(clip)

        # Call PATCH /api/clips/{project_id}/{clip_id} selecting portrait_pip_badge
        main_mod.update_clip(
            clip_id=clip.id,
            req=UpdateClipRequest(
                framing_template="portrait_pip_badge",
                frame_layout="auto_track",  # even stale frame_layout must NOT beat framing_template
                framing_override=None,
                frame_crop_override=None,
                clear_framing_override=True,
            ),
            project_id=proj.id,
            session=session,
        )
        session.refresh(clip)
        assert clip.framing_template == "portrait_pip_badge"
        assert clip.frame_layout == "portrait_pip_badge"
        assert clip.get_options().get("framing_template") == "portrait_pip_badge"
        assert clip.get_framing_override() is None

        loaded = main_mod.get_project(proj.id, session=session)
        ser = loaded["clips"][0]
        assert ser["framing_template"] == "portrait_pip_badge"
        assert ser["frame_layout"] == "portrait_pip_badge"
        assert ser["framing_override"] is None

        # Verify POST /api/clips/{project_id}/{clip_id}/render honours framing_template and clear_framing_override
        rnd_res = main_mod.trigger_render(
            background_tasks=BackgroundTasks(),
            project_id=proj.id,
            clip_id=clip.id,
            req=RenderClipRequest(
                quality_mode="fast_preview",
                framing_template="triple_gaming_hud",
                frame_layout="auto_track",
                clear_framing_override=True,
            ),
            session=session,
        )
        assert rnd_res["status"] == "completed"
        session.refresh(clip)
        assert clip.framing_template == "triple_gaming_hud"
        assert clip.frame_layout == "triple_gaming_hud"
        assert clip.get_options().get("framing_template") == "triple_gaming_hud"


def test_template_switching_with_cold_open_and_trim_dead_air_preview_and_mp4(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    test_engine, _ = _setup_isolated_db(tmp_path, monkeypatch)
    src = _make_12s_timeline_template_source(tmp_path / "media" / "chain_src.mp4")

    words = [
        {"word": "Opening", "start": 0.4, "end": 1.1},
        {"word": "line", "start": 1.2, "end": 2.0},
        # [2.1..4.4] is a 2.3s dead air pause!
        {"word": "Body", "start": 4.6, "end": 5.8},
        {"word": "gameplay", "start": 6.0, "end": 7.4},
        {"word": "moving", "start": 7.6, "end": 9.0},
        {"word": "towards", "start": 9.2, "end": 10.4},
        {"word": "CLIMAX", "start": 10.6, "end": 12.2},
        {"word": "moment!", "start": 12.4, "end": 14.0},
    ]

    tl_plan = build_timeline_edit_plan(
        video_path=str(src),
        clip_start_time=0.0,
        clip_duration=15.0,
        words=words,
        cold_open=True,
        trim_dead_air=True,
        silence_threshold=0.55,
    )
    assert tl_plan.cold_open_active is True
    assert tl_plan.has_cuts is True

    # Signatures must differ cleanly per framing_template
    sigs = {
        tpl: compute_render_signature(
            clip_id=10,
            project_id=1,
            start_time=0.0,
            duration=15.0,
            params={
                "framing_template": tpl,
                "frame_layout": tpl,
                "cold_open": True,
                "trim_dead_air": True,
            },
        )
        for tpl in ("fit_blur", "portrait_pip_badge", "webcam_gameplay_split")
    }
    assert len(set(sigs.values())) == 3

    with Session(test_engine) as session:
        proj = Project(title="Chain Test", source_type="local", source_path=str(src), duration=15.0)
        session.add(proj)
        session.commit()
        session.refresh(proj)

        clip = ViralClip(
            project_id=proj.id,
            title="Chain Clip",
            start_time=0.0,
            end_time=15.0,
            duration=15.0,
            use_cold_open=True,
            auto_trim_dead_air=True,
            webcam_corner="top_right",
            burn_captions=False,
            clip_size="480p",
        )
        clip.set_source_webcam_region({"detected": True, "locked_zone": "top_left"})
        clip.set_transcript(words)
        session.add(clip)
        session.commit()
        session.refresh(clip)

        prev_imgs = {}
        mp4_teasers = {}
        mp4_bodies = {}

        for idx, tpl in enumerate(("fit_blur", "portrait_pip_badge", "webcam_gameplay_split")):
            main_mod.update_clip(
                clip_id=clip.id,
                req=UpdateClipRequest(
                    framing_template=tpl,
                    frame_layout=tpl,
                    clear_framing_override=True,
                ),
                project_id=proj.id,
                session=session,
            )

            prev = main_mod.preview_frame_endpoint(
                PreviewFrameRequest(
                    clip_id=clip.id,
                    timestamp=0.8,
                    clip_rel_ts=0.8,
                    framing_template=tpl,
                    frame_layout=tpl,
                    webcam_corner="top_right",
                    source_corner="top_left",
                    cold_open=True,
                    trim_dead_air=True,
                    burn_captions=False,
                    safe_zone="tiktok",
                    clip_size="480p",
                    clear_framing_override=True,
                ),
                session=session,
            )
            prev_imgs[tpl] = _read_preview_rgb(prev["frame_url"])

            out_mp4 = render_clip_ultra(
                master_video_path=str(src),
                clip_id=920 + idx,
                project_id=proj.id,
                start_time=0.0,
                duration=15.0,
                words=words,
                frame_layout=tpl,
                framing_template=tpl,
                webcam_corner="top_right",
                source_webcam_region={"detected": True, "locked_zone": "top_left"},
                cold_open=True,
                trim_dead_air=True,
                burn_captions=False,
                safe_zone="tiktok",
                clip_size="480p",
                quality_mode="fast_preview",
                hardware_mode="cpu",
                clear_framing_override=True,
                output_path_override=str(tmp_path / "storage" / "renders" / f"chain_{tpl}.mp4"),
            )
            mp4_teasers[tpl] = _read_mp4_frame_at_ms(out_mp4, 800.0)
            mp4_bodies[tpl] = _read_mp4_frame_at_ms(out_mp4, 4200.0)

        # 1. Previews across the 3 templates must be sharply distinct
        assert float(np.mean(np.abs(prev_imgs["fit_blur"] - prev_imgs["portrait_pip_badge"]))) > 12.0
        assert float(np.mean(np.abs(prev_imgs["portrait_pip_badge"] - prev_imgs["webcam_gameplay_split"]))) > 12.0
        assert float(np.mean(np.abs(prev_imgs["fit_blur"] - prev_imgs["webcam_gameplay_split"]))) > 12.0

        # 2. Exported MP4 cold-open teaser (800ms) AND body (4200ms) must render each template cleanly
        assert float(np.mean(np.abs(mp4_teasers["fit_blur"] - mp4_teasers["portrait_pip_badge"]))) > 12.0
        assert float(np.mean(np.abs(mp4_teasers["portrait_pip_badge"] - mp4_teasers["webcam_gameplay_split"]))) > 12.0
        assert float(np.mean(np.abs(mp4_bodies["fit_blur"] - mp4_bodies["portrait_pip_badge"]))) > 12.0
        assert float(np.mean(np.abs(mp4_bodies["portrait_pip_badge"] - mp4_bodies["webcam_gameplay_split"]))) > 12.0

        # 3. Verify portrait_pip_badge shows magenta PiP in top_right (below 13% TikTok safe zone) in BOTH teaser & body
        for label, frame in (("teaser", mp4_teasers["portrait_pip_badge"]), ("body", mp4_bodies["portrait_pip_badge"])):
            h, w = frame.shape[:2]
            pip_patch = frame[int(h * 0.14) : int(h * 0.25), int(w * 0.72) : w - 10, :]
            assert float(pip_patch[:, :, 0].mean()) > float(pip_patch[:, :, 1].mean()) + 30.0, (
                f"portrait_pip_badge {label} missing top_right magenta PiP card"
            )

        # 4. Verify webcam_gameplay_split renders magenta cam in top 38% and green gameplay in bottom 62% in BOTH teaser & body
        for label, frame in (("teaser", mp4_teasers["webcam_gameplay_split"]), ("body", mp4_bodies["webcam_gameplay_split"])):
            h, w = frame.shape[:2]
            top_pane = frame[: int(h * 0.34), :, :]
            bot_pane = frame[int(h * 0.44) : int(h * 0.90), :, :]
            assert float(top_pane[:, :, 0].mean()) > float(top_pane[:, :, 1].mean()) + 20.0, (
                f"webcam_gameplay_split {label} missing top webcam pane"
            )
            assert float(bot_pane[:, :, 1].mean()) > float(bot_pane[:, :, 0].mean()), (
                f"webcam_gameplay_split {label} missing gameplay bottom pane"
            )
