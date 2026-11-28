"""
Comprehensive verification tests (v21) for Lost Heaven's Unified Render Graph Engine (`render_graph.py`),
Hardened FFmpeg Runner / Timeline Splicer / Audio Fallback, Sub-5s Stage-5 Batch Keyframe Extraction,
10 Framing Templates + 18 Color Grades + Hook Off Parity, and Early 4K Downscale.
"""

from __future__ import annotations

import subprocess
import time
from pathlib import Path

import cv2
import numpy as np
import pytest
from PIL import Image

from app.config import settings
from app.services import best_frame_engine, renderer
from app.services.best_frame_engine import extract_best_frames_batch
from app.services.filters import PRESET_CATEGORIES
from app.services.preview_frame import render_exact_preview_frame
from app.services.proxy import get_ffmpeg_cmd, probe_video
from app.services.reframer import ALL_FRAMING_TEMPLATES, compute_top_safe_zone_y
from app.services.render_graph import compile_clip_render_plan
from app.services.timeline_editor import sanitize_timeline_intervals


def _make_video(
    out_path: Path,
    *,
    width: int = 1280,
    height: int = 720,
    duration: float = 3.0,
    fps: int = 12,
    audio_mode: str = "tone",  # "tone" | "anull" | "none"
    dark_scene: bool = False,
) -> Path:
    out_path.parent.mkdir(parents=True, exist_ok=True)
    frame_dir = out_path.parent / f"_frames_{out_path.stem}"
    frame_dir.mkdir(parents=True, exist_ok=True)
    total_frames = max(4, int(round(duration * fps)))

    for i in range(total_frames):
        bg_val = (14, 18, 28) if dark_scene else (36, 48, 66)
        fr = np.full((height, width, 3), bg_val, dtype=np.uint8)
        # Distinct corner & center regions so all 10 framing templates produce unique compositions
        hw, hh = width // 2, height // 2
        if not dark_scene:
            fr[:hh, :hw] = (210, 80, 40)        # top-left blue
            fr[:hh, hw:] = (45, 70, 220)        # top-right red
            fr[hh:, :hw] = (40, 200, 75)        # bottom-left green
            fr[hh:, hw:] = (200, 60, 200)       # bottom-right magenta
        else:
            cv2.rectangle(fr, (int(width * 0.04), int(height * 0.05)), (int(width * 0.30), int(height * 0.32)), (195, 130, 50), -1)
        # Bright center gameplay / subject card + HUD strip
        cv2.rectangle(fr, (int(width * 0.32), int(height * 0.24)), (int(width * 0.68), int(height * 0.72)), (60, 210, 240), -1)
        cv2.circle(fr, (int(width * 0.50), int(height * 0.48)), max(12, min(width, height) // 10), (245, 245, 245), -1)
        cv2.rectangle(fr, (int(width * 0.22), int(height * 0.80)), (int(width * 0.78), int(height * 0.96)), (240, 220, 40), -1)
        cv2.imwrite(str(frame_dir / f"f_{i:04d}.png"), fr)

    cmd = [
        get_ffmpeg_cmd(),
        "-y",
        "-framerate", str(fps),
        "-i", str(frame_dir / "f_%04d.png"),
    ]
    if audio_mode == "tone":
        cmd.extend(["-f", "lavfi", "-t", f"{duration:.2f}", "-i", f"sine=frequency=440:sample_rate=44100:d={duration:.2f}"])
    elif audio_mode == "anull":
        cmd.extend(["-f", "lavfi", "-t", f"{duration:.2f}", "-i", "anullsrc=channel_layout=stereo:sample_rate=48000"])

    cmd.extend(["-c:v", "libx264", "-preset", "ultrafast", "-pix_fmt", "yuv420p"])
    if audio_mode in ("tone", "anull"):
        cmd.extend(["-c:a", "aac", "-shortest"])
    cmd.append(str(out_path))

    res = subprocess.run(cmd, capture_output=True, text=True, timeout=60)
    assert res.returncode == 0, res.stderr
    return out_path


def _read_preview_img(res: dict) -> np.ndarray:
    rel = str(res["frame_url"]).replace("/media/", "")
    img_path = Path(settings.STORAGE_DIR) / rel
    assert img_path.exists()
    with Image.open(img_path) as im:
        return np.array(im.convert("RGB"), dtype=np.float32)


def _read_mp4_midframe(mp4_path: str) -> np.ndarray:
    cap = cv2.VideoCapture(mp4_path)
    assert cap.isOpened()
    total = int(cap.get(cv2.CAP_PROP_FRAME_COUNT) or 1)
    cap.set(cv2.CAP_PROP_POS_FRAMES, max(0, total // 2))
    ok, bgr = cap.read()
    cap.release()
    assert ok and bgr is not None
    return cv2.cvtColor(bgr, cv2.COLOR_BGR2RGB).astype(np.float32)


def test_timeline_fx_and_missing_or_silent_audio_zero_packet_safety(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    storage_dir = tmp_path / "storage_v21_audio"
    storage_dir.mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr(settings, "STORAGE_DIR", str(storage_dir))

    # 1. Interval sanitizer drops <0.12s, clamps EOF bounds, merges overlaps, and never returns empty
    cleaned = sanitize_timeline_intervals(
        [(0.0, 0.05), (0.30, 0.90), (0.89, 1.40), (1.20, 99.0), (1.10, 0.50)],
        min_bound=0.0,
        max_bound=1.45,
        min_slice_sec=0.12,
        fallback_interval=(0.0, 1.45),
    )
    assert cleaned == [(0.3, 1.45)]
    fb_only = sanitize_timeline_intervals(
        [(0.0, 0.04), (5.0, 6.0)],
        min_bound=0.0,
        max_bound=1.40,
        min_slice_sec=0.12,
        fallback_interval=(0.0, 1.40),
    )
    assert len(fb_only) == 1 and (fb_only[0][1] - fb_only[0][0]) >= 0.5

    # 2. Missing-audio video + aggressive FX + extreme keep_segments renders cleanly with synthesized audio
    no_aud = _make_video(tmp_path / "no_audio.mp4", duration=1.8, audio_mode="none")
    meta_no_aud = probe_video(str(no_aud))
    assert meta_no_aud["has_audio"] is False

    out_no_aud = renderer.render_clip_ultra(
        master_video_path=str(no_aud),
        clip_id=801,
        project_id=81,
        start_time=0.0,
        duration=2.5,  # exceeds source duration -> must clamp cleanly without zero packets!
        words=[{"word": "Zero", "start": 0.2, "end": 0.8}, {"word": "Audio", "start": 0.9, "end": 1.6}],
        keep_segments=[[0.0, 0.05], [0.2, 1.6], [1.55, 99.0]],
        aspect_ratio="9:16",
        frame_layout="fit_blur",
        clip_size="480p",
        use_cold_open=True,
        auto_trim_dead_air=True,
        punch_zoom=True,
        tighten_pacing=True,
        normalize_audio=True,
        burn_captions=True,
        hardware_mode="cpu",
        quality_mode="fast_preview",
    )
    assert Path(out_no_aud).exists()
    m1 = probe_video(out_no_aud)
    assert m1["width"] == 480 and m1["height"] == 854
    assert m1["has_audio"] is True
    assert m1["duration"] >= 0.5

    # 3. Digital-zero silent AAC source (anullsrc) + loudnorm=True never crashes FFmpeg 9.1 AAC!
    silent_aac = _make_video(tmp_path / "anull_silent.mp4", duration=1.6, audio_mode="anull", dark_scene=True)
    out_silent = renderer.render_clip_ultra(
        master_video_path=str(silent_aac),
        clip_id=802,
        project_id=81,
        start_time=0.0,
        duration=1.6,
        words=[],
        aspect_ratio="9:16",
        frame_layout="fit_blur",
        clip_size="480p",
        normalize_audio=True,
        burn_captions=False,
        hardware_mode="cpu",
        quality_mode="fast_preview",
    )
    assert Path(out_silent).exists()
    m2 = probe_video(out_silent)
    assert m2["width"] == 480 and m2["height"] == 854 and m2["duration"] >= 0.5


def test_compile_plan_4k_downscale_templates_colors_safezone_and_hook_off_parity(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
):
    storage_dir = tmp_path / "storage_v21_parity"
    storage_dir.mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr(settings, "STORAGE_DIR", str(storage_dir))

    # 1. Early 4K downscale & render_plan_hash verification
    src_4k = _make_video(tmp_path / "src_4k.mp4", width=3840, height=2160, duration=1.5, fps=10, audio_mode="tone")
    plan_1080 = compile_clip_render_plan(
        master_video_path=str(src_4k),
        clip_id=1,
        project_id=1,
        start_time=0.0,
        duration=1.4,
        framing_template="gameplay_webcam_pip",
        clip_size="1080p",
        safe_zone="tiktok",
        show_hook=False,
        hook_text="STALE TEXT",
    )
    assert plan_1080.early_downscale_filter == "scale='min(1920,iw)':-2"
    assert plan_1080.eff_source_w == 1920 and plan_1080.eff_source_h == 1080
    assert plan_1080.show_hook is False and plan_1080.hook_text == ""
    assert compute_top_safe_zone_y(plan_1080.out_h, "tiktok") >= int(round(plan_1080.out_h * 0.13))

    plan_4k = compile_clip_render_plan(
        master_video_path=str(src_4k),
        clip_id=1,
        project_id=1,
        start_time=0.0,
        duration=1.4,
        framing_template="fit_blur",
        clip_size="4k",
        show_hook=True,
        hook_text="ACTIVE",
    )
    assert plan_4k.early_downscale_filter == ""
    assert plan_4k.eff_source_w == 3840 and plan_4k.eff_source_h == 2160
    assert plan_4k.render_plan_hash != plan_1080.render_plan_hash

    # 2. All 10 templates compile cleanly on zero-face gameplay + render distinct outputs via preview & renderer
    hd_src = _make_video(tmp_path / "hd_zero_face.mp4", width=1280, height=720, duration=2.0, fps=12)
    preview_imgs = {}
    for tpl in ALL_FRAMING_TEMPLATES:
        res = render_exact_preview_frame(
            video_path=str(hd_src),
            timestamp=1.0,
            clip_start_time=0.0,
            clip_duration=2.0,
            frame_layout=tpl,
            framing_template=tpl,
            aspect_ratio="9:16",
            clip_size="480p",
            burn_captions=False,
            safe_zone="tiktok",
        )
        preview_imgs[tpl] = _read_preview_img(res)

    assert len(preview_imgs) == 10
    assert float(np.mean(np.abs(preview_imgs["fit_blur"] - preview_imgs["gameplay_webcam_pip"]))) > 12.0
    assert float(np.mean(np.abs(preview_imgs["webcam_gameplay_split"] - preview_imgs["triple_gaming_hud"]))) > 12.0
    assert float(np.mean(np.abs(preview_imgs["side_by_side_50_50"] - preview_imgs["portrait_pip_badge"]))) > 12.0

    # 3. All 18 color presets compile cleanly + render visible difference on graded preset vs "none"
    assert len(PRESET_CATEGORIES) == 18
    for cid in PRESET_CATEGORIES:
        p = compile_clip_render_plan(
            master_video_path=str(hd_src),
            clip_id=5,
            project_id=2,
            start_time=0.0,
            duration=2.0,
            color_grade=cid,
            clip_size="480p",
        )
        if cid == "none":
            assert p.color_filter == ""
        else:
            assert p.color_filter != ""

    p_none = render_exact_preview_frame(
        video_path=str(hd_src), timestamp=1.0, clip_start_time=0.0, clip_duration=2.0,
        frame_layout="fit_blur", clip_size="480p", burn_captions=False, color_grading={"preset": "none"},
    )
    p_teal = render_exact_preview_frame(
        video_path=str(hd_src), timestamp=1.0, clip_start_time=0.0, clip_duration=2.0,
        frame_layout="fit_blur", clip_size="480p", burn_captions=False, color_grading={"preset": "teal_orange"},
    )
    assert float(np.mean(np.abs(_read_preview_img(p_none) - _read_preview_img(p_teal)))) > 6.0

    # 4. Strict show_hook=False parity between preview_frame & render_clip_ultra even when stale hook_text supplied
    prev_clean = render_exact_preview_frame(
        video_path=str(hd_src), timestamp=0.6, clip_start_time=0.0, clip_duration=1.8,
        burn_captions=True, show_hook=False, hook_text="", clip_size="480p",
    )
    prev_stale = render_exact_preview_frame(
        video_path=str(hd_src), timestamp=0.6, clip_start_time=0.0, clip_duration=1.8,
        burn_captions=True, show_hook=False, hook_text="STALE TEXT", clip_size="480p",
    )
    np.testing.assert_allclose(_read_preview_img(prev_clean)[:260], _read_preview_img(prev_stale)[:260], atol=1.0)

    mp4_clean = renderer.render_clip_ultra(
        master_video_path=str(hd_src), clip_id=810, project_id=82, start_time=0.0, duration=1.5,
        words=[], frame_layout="fit_blur", clip_size="480p", burn_captions=True,
        show_hook=False, hook_text="", hardware_mode="cpu", quality_mode="fast_preview",
    )
    mp4_stale = renderer.render_clip_ultra(
        master_video_path=str(hd_src), clip_id=811, project_id=82, start_time=0.0, duration=1.5,
        words=[], frame_layout="fit_blur", clip_size="480p", burn_captions=True,
        show_hook=False, hook_text="STALE TEXT", hardware_mode="cpu", quality_mode="fast_preview",
    )
    f_clean = _read_mp4_midframe(mp4_clean)
    f_stale = _read_mp4_midframe(mp4_stale)
    assert float(np.mean(np.abs(f_clean[:260] - f_stale[:260]))) < 2.0


def test_stage5_8_clip_batch_keyframe_extraction_under_5s(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    storage_dir = tmp_path / "storage_v21_stage5"
    storage_dir.mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr(settings, "STORAGE_DIR", str(storage_dir))

    # Build a 16s HD video and precompute synthetic video_analysis/audio_analysis metrics
    src = _make_video(tmp_path / "stage5_16s.mp4", width=1280, height=720, duration=16.0, fps=8, audio_mode="tone")
    monkeypatch.setattr(
        best_frame_engine,
        "analyze_video_track",
        lambda *a, **kw: pytest.fail("Stage-5 batch extractor must reuse precomputed video_analysis without re-scanning"),
    )

    vid_analysis = {
        "frames": [
            {
                "t": round(t_i * 0.25, 2),
                "sharpness_score": 82.0 + (t_i % 5),
                "exposure_score": 68.0,
                "action_score": 55.0 + (t_i % 7) * 4,
                "composition_score": 74.0,
                "emotion_intensity": 65.0 + (t_i % 3) * 6,
                "eyes_open_confidence": 0.95,
                "gaze_score": 76.0,
                "face_count": 0,
                "is_black": False,
            }
            for t_i in range(64)
        ]
    }
    aud_analysis = {
        "timeline": [
            {"t": round(t_i * 0.25, 2), "excitement_score": 60.0 + (t_i % 6) * 5}
            for t_i in range(64)
        ]
    }
    clip_windows = [
        {"clip_id": idx + 1, "start_time": idx * 2.0, "end_time": (idx + 1) * 2.0}
        for idx in range(8)
    ]

    t0 = time.perf_counter()
    res_map = extract_best_frames_batch(
        video_path=str(src),
        clip_windows=clip_windows,
        video_analysis=vid_analysis,
        audio_analysis=aud_analysis,
        output_dir=str(tmp_path / "bf_batch"),
        project_id=99,
        max_workers=4,
    )
    elapsed = time.perf_counter() - t0
    print(f"[v21 BENCHMARK] 8-clip Stage-5 keyframe extraction elapsed={elapsed:.3f}s")
    assert elapsed < 5.0, f"8-clip keyframe extraction took {elapsed:.2f}s (budget < 5.0s)"
    assert len(res_map) == 8
    for idx in range(8):
        frames = res_map[idx + 1]["frames"]
        assert len(frames) >= 1
        for f in frames:
            assert f["image_path"] and Path(f["image_path"]).exists()
