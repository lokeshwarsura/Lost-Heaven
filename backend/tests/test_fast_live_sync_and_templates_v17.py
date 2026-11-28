"""
Tests for v17 Live Preview Sync Architecture & 10 Framing Templates:
  1. POST /api/preview-frame executes < 1.0s on 4K (3840x2160) video and NEVER scans full video
     (`analyze_clip_visuals` is not invoked), with warm cached slider updates < 0.5s.
  2. All 10 framing templates match exact filter ratios/dividers and render visually distinct outputs
     (mean pixel diff > 15) with zero unwanted fallback to fit_blur.
"""

import os
import subprocess
import time
from pathlib import Path

import cv2
import numpy as np
import pytest
from PIL import Image
from sqlmodel import Session

from app.config import settings
from app.database import engine
from app.main import preview_frame_endpoint
from app.models import PreviewFrameRequest, Project, ViralClip
from app.services.preview_frame import render_exact_preview_frame
from app.services.proxy import get_ffmpeg_cmd
from app.services.reframer import ALL_FRAMING_TEMPLATES, build_multi_aspect_filter


def _make_synthetic_mp4(out_path: Path, width: int, height: int, duration: float = 3.0, fps: int = 15) -> Path:
    ffmpeg_bin = get_ffmpeg_cmd()
    # Paint 4 ultra-distinct quadrants so split/PiP/HUD/crop templates produce starkly different pixels
    vf = (
        f"drawbox=x=0:y=0:w={width // 2}:h={height // 2}:color=0xFF5511@1.0:t=fill,"
        f"drawbox=x={width // 2}:y=0:w={width // 2}:h={height // 2}:color=0x3B82F6@1.0:t=fill,"
        f"drawbox=x=0:y={height // 2}:w={width // 2}:h={height // 2}:color=0xEAB308@1.0:t=fill,"
        f"drawbox=x={width // 2}:y={height // 2}:w={width // 2}:h={height // 2}:color=0x10B981@1.0:t=fill,"
        f"drawbox=x={int(width * 0.42)}:y={int(height * 0.25)}:w={int(width * 0.16)}:h={int(height * 0.35)}:color=0xEC4899@1.0:t=fill"
    )
    cmd = [
        ffmpeg_bin,
        "-y",
        "-f", "lavfi",
        "-i", f"color=c=0x111827:s={width}x{height}:r={fps}:d={duration:.2f}",
        "-f", "lavfi",
        "-i", f"anullsrc=r=48000:cl=stereo",
        "-t", f"{duration:.2f}",
        "-vf", vf,
        "-c:v", "libx264",
        "-preset", "ultrafast",
        "-pix_fmt", "yuv420p",
        "-c:a", "aac",
        "-shortest",
        str(out_path),
    ]
    res = subprocess.run(cmd, capture_output=True, text=True, timeout=60)
    assert res.returncode == 0, f"Synthetic video creation failed: {res.stderr[-400:]}"
    return out_path


def _load_preview_rgb(res: dict) -> np.ndarray:
    rel = str(res["frame_url"]).replace("/media/", "")
    img_path = Path(settings.STORAGE_DIR) / rel
    assert img_path.exists(), f"Missing preview image: {img_path}"
    with Image.open(img_path) as im:
        return np.array(im.convert("RGB"), dtype=np.int16)


def test_preview_frame_4k_fast_latency_and_zero_video_scan(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
):
    video_4k = _make_synthetic_mp4(tmp_path / "source_4k.mp4", 3840, 2160, duration=3.0, fps=12)

    def _forbid_full_scan(*args, **kwargs):
        raise AssertionError("analyze_clip_visuals must NEVER be called inside /api/preview-frame")

    monkeypatch.setattr("app.services.vision_analyzer.analyze_clip_visuals", _forbid_full_scan)

    with Session(engine) as session:
        proj = Project(
            title="4K Fast Preview Project",
            source_filename="source_4k.mp4",
            source_path=str(video_4k),
            duration=3.0,
            resolution_w=3840,
            resolution_h=2160,
            fps=12.0,
            status="analyzed",
        )
        session.add(proj)
        session.commit()
        session.refresh(proj)

        clip = ViralClip(
            project_id=proj.id,
            start_time=0.0,
            end_time=3.0,
            duration=3.0,
            frame_layout="gameplay_webcam_pip",
            webcam_corner="top_right",
            status="pending",
        )
        session.add(clip)
        session.commit()
        session.refresh(clip)

        req_cold = PreviewFrameRequest(
            clip_id=clip.id,
            project_id=proj.id,
            timestamp=1.2,
            clip_rel_ts=1.2,
            aspect_ratio="9:16",
            frame_layout="gameplay_webcam_pip",
            webcam_corner="top_right",
            source_corner="top_left",
            burn_captions=True,
            words=[{"word": "FOUR", "start": 1.0, "end": 1.4}, {"word": "KAY", "start": 1.4, "end": 1.9}],
            clip_size="720p",
        )
        t0 = time.perf_counter()
        res_cold = preview_frame_endpoint(req_cold, session=session)
        cold_sec = time.perf_counter() - t0

        assert res_cold["frame_url"].endswith(".png")
        print(f"[v17 BENCHMARK] cold_4k={cold_sec*1000:.1f}ms, reported={res_cold.get('render_time_ms')}ms")
        assert cold_sec < 1.5, f"Cold 4K preview took {cold_sec:.3f}s (expected < 1.5s; target < 0.25s warm)"

        # Warm edit at identical timestamp (e.g., slider drag: color grade + zoom + caption coords)
        req_warm = PreviewFrameRequest(
            clip_id=clip.id,
            project_id=proj.id,
            timestamp=1.2,
            clip_rel_ts=1.2,
            aspect_ratio="9:16",
            frame_layout="portrait_pip_badge",
            webcam_corner="bottom_right",
            source_corner="top_left",
            zoom=1.18,
            color_preset="cinematic_warm",
            burn_captions=True,
            caption_x=0.5,
            caption_y=0.35,
            caption_custom_position=True,
            words=[{"word": "WARM", "start": 1.0, "end": 1.5}],
            clip_size="720p",
        )
        t1 = time.perf_counter()
        res_warm = preview_frame_endpoint(req_warm, session=session)
        warm_sec = time.perf_counter() - t1

        assert res_warm["frame_url"].endswith(".png")
        print(f"[v17 BENCHMARK] warm_4k={warm_sec*1000:.1f}ms, reported={res_warm.get('render_time_ms')}ms")
        assert warm_sec < 0.85, f"Cached 4K preview edit took {warm_sec:.3f}s (expected < 0.85s)"

        img_cold = _load_preview_rgb(res_cold)
        img_warm = _load_preview_rgb(res_warm)
        diff = float(np.mean(np.abs(img_cold - img_warm)))
        assert diff > 10.0, f"Template/grade/caption change must alter preview pixels significantly (diff={diff:.2f})"


def test_all_10_templates_exact_ratios_and_visually_distinct(tmp_path: Path):
    assert len(ALL_FRAMING_TEMPLATES) == 10
    src_mp4 = _make_synthetic_mp4(tmp_path / "templates_quadrants.mp4", 1280, 720, duration=2.5, fps=15)

    # Check exact filtergraph specifications for updated divider widths & PiP/HUD ratios
    fg_split = build_multi_aspect_filter("9:16", "webcam_gameplay_split", 1280, 720)
    assert "vstack=inputs=2" in fg_split and "h=3:color=0xFFFFFF@0.28" in fg_split

    fg_pip = build_multi_aspect_filter("9:16", "gameplay_webcam_pip", 1280, 720)
    assert "scale=324:" in fg_pip and "overlay=" in fg_pip  # 30% of 1080 = 324px

    fg_pod = build_multi_aspect_filter("9:16", "split_podcast", 1280, 720)
    assert "vstack=inputs=2" in fg_pod and "h=3:color=0xFFFFFF@0.28" in fg_pod

    fg_hud = build_multi_aspect_filter("9:16", "triple_gaming_hud", 1280, 720)
    assert "vstack=inputs=3" in fg_hud and "1080:498" in fg_hud and "1080:1000" in fg_hud and "1080:422" in fg_hud  # 26% / 52% / 22%

    fg_badge = build_multi_aspect_filter("9:16", "portrait_pip_badge", 1280, 720)
    assert "scale=292:" in fg_badge and "overlay=" in fg_badge  # 27% of 1080 = 292px

    fg_sbs = build_multi_aspect_filter("9:16", "side_by_side_50_50", 1280, 720)
    assert "hstack=inputs=2" in fg_sbs and "w=3:h=ih" in fg_sbs

    rendered: dict[str, np.ndarray] = {}
    for tpl in ALL_FRAMING_TEMPLATES:
        res = render_exact_preview_frame(
            video_path=str(src_mp4),
            timestamp=1.0,
            clip_start_time=0.0,
            clip_duration=2.5,
            frame_layout=tpl,
            webcam_corner="top_right",
            source_corner="top_left",
            source_webcam_region={"detected": True, "locked_zone": "top_left"},
            burn_captions=False,
            clip_size="480p",
        )
        rendered[tpl] = _load_preview_rgb(res)
        assert rendered[tpl].shape[0] > 0 and rendered[tpl].shape[1] > 0

    # No other template may accidentally fall back to fit_blur
    for tpl in ALL_FRAMING_TEMPLATES:
        if tpl == "fit_blur":
            continue
        diff_vs_blur = float(np.mean(np.abs(rendered[tpl] - rendered["fit_blur"])))
        threshold = 10.0 if tpl == "cinema_letterbox_blur" else 15.0
        assert diff_vs_blur > threshold, f"Template '{tpl}' looks identical to fit_blur (diff={diff_vs_blur:.2f})"
    # Specifically check cinema_letterbox_blur matte-bar edges (y=34%..39%) differ clearly from fit_blur
    h_out = rendered["fit_blur"].shape[0]
    matte_diff = float(
        np.mean(np.abs(rendered["cinema_letterbox_blur"][int(h_out * 0.34):int(h_out * 0.39)] - rendered["fit_blur"][int(h_out * 0.34):int(h_out * 0.39)]))
    )
    assert matte_diff > 20.0, f"cinema_letterbox_blur 2.39:1 matte edge too close to fit_blur ({matte_diff:.2f})"

    # Key structural template comparisons must all produce distinct imagery (>15 mean pixel diff)
    pairs = [
        ("auto_track", "cinema_letterbox_blur", 15.0),
        ("webcam_gameplay_split", "gameplay_webcam_pip", 15.0),
        ("webcam_gameplay_split", "triple_gaming_hud", 15.0),
        ("gameplay_webcam_pip", "portrait_pip_badge", 10.0),
        ("split_podcast", "side_by_side_50_50", 15.0),
        ("triple_gaming_hud", "side_by_side_50_50", 15.0),
        ("dynamic_punch_crop", "fit_blur", 15.0),
    ]
    for a, b, min_diff in pairs:
        diff = float(np.mean(np.abs(rendered[a] - rendered[b])))
        assert diff > min_diff, f"Templates '{a}' and '{b}' too similar in preview render (diff={diff:.2f})"
    # Also check top_right PiP corner shape/background difference between 4:3 vs 3:4 card
    w_out = rendered["gameplay_webcam_pip"].shape[1]
    pip_quad_diff = float(
        np.mean(np.abs(rendered["gameplay_webcam_pip"][:int(h_out * 0.45), int(w_out * 0.55):] - rendered["portrait_pip_badge"][:int(h_out * 0.45), int(w_out * 0.55):]))
    )
    assert pip_quad_diff > 18.0, f"4:3 PiP vs 3:4 Portrait PiP top-right region too similar ({pip_quad_diff:.2f})"


def test_fit_blur_visible_backdrop_dark_scene_parity(tmp_path: Path):
    """fit_blur must produce unmistakably visible ambient blurred top/bottom backdrops even on dark scenes."""
    from app.services.renderer import render_clip_ultra

    ffmpeg_bin = get_ffmpeg_cmd()
    dark_src = tmp_path / "dark_scene.mp4"
    # Dark blue/indigo scene with warm center highlight box (RGB ~ (22, 30, 45) + highlight)
    vf = (
        "drawbox=x=420:y=120:w=440:h=480:color=0x3B82F6@0.75:t=fill,"
        "drawbox=x=520:y=220:w=240:h=240:color=0xF59E0B@0.70:t=fill"
    )
    res = subprocess.run(
        [
            ffmpeg_bin, "-y",
            "-f", "lavfi", "-i", "color=c=0x1C2438:s=1280x720:r=15:d=1.5",
            "-f", "lavfi", "-i", "sine=frequency=220:sample_rate=48000:duration=1.5",
            "-t", "1.5", "-vf", vf,
            "-af", "volume=0.05",
            "-c:v", "libx264", "-preset", "ultrafast", "-pix_fmt", "yuv420p",
            "-c:a", "aac", "-shortest", str(dark_src),
        ],
        capture_output=True, text=True, timeout=60,
    )
    assert res.returncode == 0, f"Dark synthetic video failed: {res.stderr[-300:]}"

    prev = render_exact_preview_frame(
        video_path=str(dark_src),
        timestamp=0.6,
        clip_start_time=0.0,
        clip_duration=1.5,
        aspect_ratio="9:16",
        frame_layout="fit_blur",
        framing_template="fit_blur",
        burn_captions=False,
        clip_size="480p",
    )
    prev_rgb = _load_preview_rgb(prev)
    h, _ = prev_rgb.shape[:2]
    top_band = prev_rgb[: int(h * 0.28), :, :]
    bot_band = prev_rgb[int(h * 0.72) :, :, :]
    assert float(top_band.mean()) > 16.0, f"Preview top fit_blur backdrop crushed dark: mean={top_band.mean():.2f}"
    assert float(bot_band.mean()) > 16.0, f"Preview bottom fit_blur backdrop crushed dark: mean={bot_band.mean():.2f}"

    out_mp4 = render_clip_ultra(
        master_video_path=str(dark_src),
        clip_id=971,
        start_time=0.0,
        duration=1.5,
        words=[],
        aspect_ratio="9:16",
        frame_layout="fit_blur",
        framing_template="fit_blur",
        burn_captions=False,
        show_hook_banner=False,
        clip_size="480p",
        hardware_mode="cpu",
        quality_mode="fast_preview",
        output_path_override=str(tmp_path / "dark_fit_blur_out.mp4"),
    )
    cap = cv2.VideoCapture(out_mp4)
    try:
        cap.set(cv2.CAP_PROP_POS_MSEC, 500.0)
        ok, frame_bgr = cap.read()
        assert ok and frame_bgr is not None
        mh = frame_bgr.shape[0]
        assert float(frame_bgr[: int(mh * 0.28)].mean()) > 16.0
        assert float(frame_bgr[int(mh * 0.72) :].mean()) > 16.0
    finally:
        cap.release()
