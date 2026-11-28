import re
import subprocess
from pathlib import Path

import cv2
import numpy as np
import pytest
from PIL import Image
from sqlmodel import SQLModel, create_engine, Session

import app.database as db_mod
import app.main as main_mod
from app.config import settings
from app.models import PreviewFrameRequest, Project, RenderClipRequest, ViralClip
from app.services.caption_renderer import render_caption_frame
from app.services.preview_frame import render_exact_preview_frame
from app.services.proxy import get_ffmpeg_cmd
from app.services.reframer import (
    _pip_safe_xy,
    _portrait_pip_safe_xy,
    build_multi_aspect_filter,
)
from app.services.render_queue import compute_render_signature
from app.services.renderer import render_clip_ultra


def _setup_isolated_db(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    storage_dir = tmp_path / "storage"
    for sub in ("masters", "proxies", "renders", "previews", "captions", "projects"):
        (storage_dir / sub).mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr(settings, "STORAGE_DIR", str(storage_dir))

    db_path = tmp_path / "safe_zone_test.db"
    test_engine = create_engine(
        f"sqlite:///{db_path}",
        echo=False,
        connect_args={"check_same_thread": False},
    )
    SQLModel.metadata.create_all(test_engine)
    monkeypatch.setattr(db_mod, "engine", test_engine)
    return test_engine, storage_dir


def _make_gameplay_with_corner_cam(out_path: Path, duration_sec: float = 10.0) -> Path:
    """Create 1280x720 source with green gameplay background and magenta top_left webcam box."""
    ffmpeg = get_ffmpeg_cmd()
    out_path.parent.mkdir(parents=True, exist_ok=True)
    vf = (
        "drawbox=x=20:y=20:w=360:h=260:color=#E020C8@1.0:t=fill,"
        "drawbox=x=420:y=200:w=600:h=360:color=#FF9900@0.95:t=fill:enable='between(t,6.5,9.5)'"
    )
    a_expr = "if(between(t,6.5,9.5),0.75*sin(2*PI*440*t),0.05*sin(2*PI*220*t))"
    res = subprocess.run(
        [
            ffmpeg,
            "-y",
            "-f",
            "lavfi",
            "-i",
            f"color=c=#1C7C38:size=1280x720:rate=24:duration={duration_sec:.2f}",
            "-f",
            "lavfi",
            "-i",
            f"aevalsrc='{a_expr}':s=44100:d={duration_sec:.2f}",
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
        timeout=45,
    )
    assert res.returncode == 0, res.stderr
    return out_path


@pytest.mark.parametrize("safe_zone", ["tiktok", "reels", "shorts", "TikTok", "Instagram", "YT Shorts"])
@pytest.mark.parametrize("corner", ["top_left", "top_right"])
def test_top_pip_overlays_clear_platform_safe_zone_margin(safe_zone: str, corner: str):
    w, h = 1080, 1920
    min_y = int(np.ceil(h * 0.13))

    _, py_pip = _pip_safe_xy(corner, w, h, 324, 240, caption_y=0.78, safe_zone=safe_zone)
    assert py_pip >= min_y, f"PiP y={py_pip} < 0.13*h ({min_y}) for {safe_zone}/{corner}"

    _, py_port = _portrait_pip_safe_xy(corner, w, h, 296, 392, caption_y=0.78, safe_zone=safe_zone)
    assert py_port >= min_y, f"Portrait PiP y={py_port} < 0.13*h ({min_y}) for {safe_zone}/{corner}"

    vf = build_multi_aspect_filter(
        aspect_ratio="9:16",
        frame_layout="portrait_pip_badge",
        source_w=1920,
        source_h=1080,
        webcam_corner=corner,
        source_corner="top_left",
        is_video_render=False,
        caption_y=0.78,
        safe_zone=safe_zone,
    )
    m_ov = re.search(r"\[bg_s\]\[cam_fg\]overlay=(\d+):(\d+)\[v\]", vf)
    assert m_ov, vf
    assert int(m_ov.group(2)) >= min_y


def test_climax_hook_badge_and_portrait_pip_respect_safe_zones_in_preview_and_mp4(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    test_engine, _ = _setup_isolated_db(tmp_path, monkeypatch)
    src = _make_gameplay_with_corner_cam(tmp_path / "media" / "sz_src.mp4", duration_sec=10.0)

    # 1. CLIMAX HOOK badge sits below 0.13 * frame_h whenever safe_zone is tiktok/reels/shorts
    w, h = 1080, 1920
    safe_margin_y = int(np.ceil(h * 0.13))
    for sz in ("tiktok", "reels", "shorts"):
        rgba = np.array(
            render_caption_frame(
                t=0.6,
                width=w,
                height=h,
                words=[{"word": "Wow", "start": 0.2, "end": 1.2}],
                burn_captions=False,
                cold_open_active=True,
                cold_open_teaser_dur=2.8,
                safe_zone=sz,
            )
        )
        assert int(np.sum(rgba[:safe_margin_y, :, 3] > 20)) == 0, f"CLIMAX HOOK drew into top 13% safe zone for {sz}"
        assert int(np.sum(rgba[safe_margin_y : int(h * 0.22), :, 3] > 20)) > 800, f"CLIMAX HOOK missing below safe zone for {sz}"

    # 2. Preview latency is always >= 1ms and safe_zone invalidates render signature
    sig_none = compute_render_signature(clip_id=1, project_id=1, start_time=0.0, duration=10.0, params={"safe_zone": "none"})
    sig_tk = compute_render_signature(clip_id=1, project_id=1, start_time=0.0, duration=10.0, params={"safe_zone": "tiktok"})
    assert sig_none != sig_tk

    prev_none = render_exact_preview_frame(
        video_path=str(src),
        timestamp=0.8,
        clip_start_time=0.0,
        clip_duration=10.0,
        frame_layout="portrait_pip_badge",
        webcam_corner="top_right",
        source_corner="top_left",
        source_webcam_region={"detected": True, "locked_zone": "top_left"},
        burn_captions=False,
        clip_size="480p",
        safe_zone="none",
    )
    prev_tk = render_exact_preview_frame(
        video_path=str(src),
        timestamp=0.8,
        clip_start_time=0.0,
        clip_duration=10.0,
        frame_layout="portrait_pip_badge",
        webcam_corner="top_right",
        source_corner="top_left",
        source_webcam_region={"detected": True, "locked_zone": "top_left"},
        burn_captions=False,
        clip_size="480p",
        safe_zone="tiktok",
    )
    assert prev_none["render_time_ms"] >= 1
    assert prev_tk["render_time_ms"] >= 1

    rgb_none = np.array(
        Image.open(Path(settings.STORAGE_DIR) / "previews" / Path(prev_none["frame_url"]).name).convert("RGB"),
        dtype=np.float32,
    )
    rgb_tk = np.array(
        Image.open(Path(settings.STORAGE_DIR) / "previews" / Path(prev_tk["frame_url"]).name).convert("RGB"),
        dtype=np.float32,
    )
    ph, pw = rgb_tk.shape[:2]
    top_band_end = int(ph * 0.125)
    # In TikTok mode, the top 12.5% right band stays green gameplay while [13%..26%] holds the magenta PiP card
    magenta_above = rgb_tk[:top_band_end, int(pw * 0.72) : pw - 10, :]
    magenta_below = rgb_tk[int(ph * 0.14) : int(ph * 0.25), int(pw * 0.72) : pw - 10, :]
    assert float(magenta_above[:, :, 0].mean()) < float(magenta_above[:, :, 1].mean()) + 15.0
    assert float(magenta_below[:, :, 0].mean()) > float(magenta_below[:, :, 1].mean()) + 35.0
    assert float(np.mean(np.abs(rgb_none[:top_band_end] - rgb_tk[:top_band_end]))) > 3.0

    # 3. Cold-open MP4 export + PreviewFrameRequest endpoint with safe_zone="shorts"
    with Session(test_engine) as session:
        proj = Project(title="SafeZone MP4", source_type="local", source_path=str(src), duration=10.0)
        session.add(proj)
        session.commit()
        session.refresh(proj)

        clip = ViralClip(
            project_id=proj.id,
            title="SZ Clip",
            hook_headline="TEST HOOK",
            show_hook_banner=False,
            start_time=0.0,
            end_time=10.0,
            duration=10.0,
            frame_layout="portrait_pip_badge",
            webcam_corner="top_right",
            use_cold_open=True,
            burn_captions=False,
            clip_size="480p",
        )
        clip.set_source_webcam_region({"detected": True, "locked_zone": "top_left"})
        clip.set_transcript([{"word": "Climax!", "start": 7.0, "end": 8.0}])
        session.add(clip)
        session.commit()
        session.refresh(clip)

        ep_res = main_mod.preview_frame_endpoint(
            PreviewFrameRequest(
                clip_id=clip.id,
                timestamp=0.6,
                clip_rel_ts=0.6,
                frame_layout="portrait_pip_badge",
                webcam_corner="top_right",
                cold_open=True,
                burn_captions=False,
                show_hook=False,
                safe_zone="shorts",
                clip_size="480p",
            ),
            session=session,
        )
        assert ep_res["render_time_ms"] >= 1

        out_mp4 = render_clip_ultra(
            master_video_path=str(src),
            clip_id=888,
            project_id=proj.id,
            start_time=0.0,
            duration=10.0,
            words=[{"word": "Climax!", "start": 7.0, "end": 8.0}],
            frame_layout="portrait_pip_badge",
            webcam_corner="top_right",
            source_webcam_region={"detected": True, "locked_zone": "top_left"},
            burn_captions=False,
            show_hook=False,
            cold_open=True,
            clip_size="480p",
            safe_zone="tiktok",
            quality_mode="fast_preview",
            hardware_mode="cpu",
            output_path_override=str(tmp_path / "storage" / "renders" / "sz_tiktok.mp4"),
        )
        cap = cv2.VideoCapture(out_mp4)
        cap.set(cv2.CAP_PROP_POS_MSEC, 600.0)
        ok, bgr_mp4 = cap.read()
        cap.release()
        assert ok and bgr_mp4 is not None
        mh, mw = bgr_mp4.shape[:2]
        # Top center 12.5% band must be clear of CLIMAX HOOK pill (no high-red badge in top 12.5%)
        top_center = bgr_mp4[: int(mh * 0.125), int(mw * 0.30) : int(mw * 0.70), :]
        red_pills = int(((top_center[:, :, 2] > 180) & (top_center[:, :, 1] < 90)).sum())
        assert red_pills == 0, f"Found {red_pills} CLIMAX HOOK pixels inside TikTok top 12.5% safe zone"
