import json
import math
import struct
import subprocess
import wave
from pathlib import Path
from typing import Dict, List, Tuple

import cv2
import numpy as np
import pytest
from PIL import Image
from sqlmodel import SQLModel, Session, create_engine

import app.main as main_mod
from app.config import settings
from app.models import Project, UpdateClipRequest, ViralClip
from app.services.face_tracker import detect_punch_zoom_peaks
from app.services.preview_frame import render_exact_preview_frame
from app.services.proxy import get_ffmpeg_cmd, probe_video
from app.services.reframer import (
    ALL_FRAMING_TEMPLATES,
    build_multi_aspect_filter,
    resolve_template_regions,
)
from app.services.renderer import render_clip_ultra
from app.services.timeline_editor import (
    COLD_OPEN_WINDOW_SEC,
    DEAD_AIR_BREATH_BUFFER_SEC,
    DEAD_AIR_MIN_PAUSE_SEC,
    build_timeline_edit_plan,
)


def _write_wav(wav_path: Path, samples: np.ndarray, sr: int = 16000) -> None:
    wav_path.parent.mkdir(parents=True, exist_ok=True)
    pcm = np.clip(samples * 32767.0, -32767.0, 32767.0).astype("<i2")
    with wave.open(str(wav_path), "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(sr)
        wf.writeframes(pcm.tobytes())


def _build_synthetic_video(
    out_mp4: Path,
    duration_sec: float,
    width: int = 640,
    height: int = 360,
    fps: int = 12,
    audio_env_fn=None,
    frame_draw_fn=None,
) -> Path:
    sr = 16000
    n_samples = int(duration_sec * sr)
    t_arr = np.arange(n_samples, dtype=np.float32) / sr
    if audio_env_fn is None:
        env = np.full(n_samples, 0.20, dtype=np.float32)
    else:
        env = np.array([audio_env_fn(float(t)) for t in t_arr], dtype=np.float32)
    tone = env * np.sin(2.0 * math.pi * 220.0 * t_arr)
    wav_path = out_mp4.with_suffix(".wav")
    _write_wav(wav_path, tone, sr=sr)

    n_frames = int(round(duration_sec * fps))
    raw = bytearray()
    for fi in range(n_frames):
        t = fi / float(fps)
        fr = np.zeros((height, width, 3), dtype=np.uint8)
        if frame_draw_fn is not None:
            frame_draw_fn(fr, fi, t, width, height)
        else:
            fr[:, :] = (32, 48, 84)
            cv2.rectangle(fr, (width // 4, height // 4), (3 * width // 4, 3 * height // 4), (190, 210, 60), -1)
        raw.extend(fr.tobytes())

    out_mp4.parent.mkdir(parents=True, exist_ok=True)
    proc = subprocess.run(
        [
            get_ffmpeg_cmd(), "-y",
            "-f", "rawvideo", "-vcodec", "rawvideo", "-pix_fmt", "bgr24",
            "-s", f"{width}x{height}", "-r", str(fps), "-i", "-",
            "-i", str(wav_path),
            "-c:v", "libx264", "-preset", "ultrafast", "-pix_fmt", "yuv420p",
            "-c:a", "aac", "-shortest", str(out_mp4),
        ],
        input=bytes(raw),
        capture_output=True,
        timeout=90,
    )
    assert proc.returncode == 0, proc.stderr.decode(errors="ignore")
    return out_mp4


def _load_preview_png(res: Dict) -> np.ndarray:
    fname = Path(res["frame_url"]).name
    png_path = Path(settings.STORAGE_DIR) / "previews" / fname
    assert png_path.exists(), f"Missing preview PNG at {png_path}"
    return np.array(Image.open(png_path).convert("RGB"), dtype=np.int16)


def test_cold_open_climax_concats_exact_2_8s_shifts_captions_and_renders_climax_hook(tmp_path):
    # Quiet speech at start, strong acoustic climax burst between 7.5s and 10.5s
    def env_fn(t: float) -> float:
        return 0.92 if 7.5 <= t <= 10.5 else 0.14

    def draw_fn(fr, fi, t, w, h):
        # First 7s bluish background, climax window bright emerald center
        if 7.5 <= t <= 10.8:
            fr[:, :] = (40, 170, 50)
            cv2.circle(fr, (w // 2, h // 2), 68, (255, 220, 40), -1)
        else:
            fr[:, :] = (70, 45, 30)
            cv2.rectangle(fr, (w // 3, h // 4), (2 * w // 3, 3 * h // 4), (180, 150, 110), -1)

    src_mp4 = _build_synthetic_video(
        tmp_path / "cold_open_src.mp4",
        duration_sec=12.0,
        width=640,
        height=360,
        fps=12,
        audio_env_fn=env_fn,
        frame_draw_fn=draw_fn,
    )
    words = [
        {"word": "Opening", "start": 0.4, "end": 0.9},
        {"word": "setup", "start": 1.0, "end": 1.6},
        {"word": "calm", "start": 2.0, "end": 2.6},
        {"word": "INSANE", "start": 8.0, "end": 8.6},
        {"word": "CLIMAX", "start": 8.7, "end": 9.4},
        {"word": "PLAY!", "start": 9.5, "end": 10.2},
    ]

    plan = build_timeline_edit_plan(
        video_path=str(src_mp4),
        clip_start_time=0.0,
        clip_duration=12.0,
        words=words,
        cold_open=True,
        trim_dead_air=False,
        punch_zoom=False,
    )
    assert plan.cold_open_active is True
    assert abs(plan.teaser_duration - COLD_OPEN_WINDOW_SEC) < 0.05
    assert plan.climax_window is not None
    assert plan.climax_window[0] >= 12.0 * 0.35 - 0.15
    assert "2.8s climax @" in str(plan.timeline_fx_summary.get("cold_open_label") or "")

    teaser_words = [w for w in plan.remapped_words if w.get("is_climax_teaser")]
    main_words = [w for w in plan.remapped_words if not w.get("is_climax_teaser")]
    assert teaser_words, "Expected teaser captions mapped to [0..2.8s]"
    assert all(0.0 <= float(w["start"]) < plan.teaser_duration for w in teaser_words)
    first_main = next(w for w in main_words if w["word"] == "Opening")
    assert abs(float(first_main["start"]) - (0.4 + plan.teaser_duration)) < 0.08

    # Preview at t=1.0s (inside teaser window) must show amber/orange CLIMAX HOOK badge
    prev_teaser = render_exact_preview_frame(
        video_path=str(src_mp4),
        timestamp=1.0,
        clip_start_time=0.0,
        clip_duration=12.0,
        clip_rel_ts_override=1.0,
        transcript_words=words,
        cold_open=True,
        burn_captions=True,
        clip_size="480p",
    )
    assert prev_teaser.get("timeline_fx_summary", {}).get("cold_open_active") is True
    img_teaser = _load_preview_png(prev_teaser)

    prev_main = render_exact_preview_frame(
        video_path=str(src_mp4),
        timestamp=4.0,
        clip_start_time=0.0,
        clip_duration=12.0,
        clip_rel_ts_override=4.0,
        transcript_words=words,
        cold_open=True,
        burn_captions=True,
        clip_size="480p",
    )
    img_main = _load_preview_png(prev_main)

    # Top badge band (~top 25% of portrait canvas) contains warm amber/orange pixels only during teaser (< 2.8s)
    h_p = img_teaser.shape[0]
    top_t = img_teaser[: int(h_p * 0.25), :, :]
    top_m = img_main[: int(h_p * 0.25), :, :]
    orange_t = int(((top_t[:, :, 0] > 180) & (top_t[:, :, 1] > 80) & (top_t[:, :, 2] < 90)).sum())
    orange_m = int(((top_m[:, :, 0] > 180) & (top_m[:, :, 1] > 80) & (top_m[:, :, 2] < 90)).sum())
    assert orange_t > orange_m + 300, f"Expected CLIMAX HOOK pill during teaser: {orange_t} vs {orange_m}"

    out_mp4 = render_clip_ultra(
        master_video_path=str(src_mp4),
        clip_id=901,
        start_time=0.0,
        duration=12.0,
        words=words,
        frame_layout="fit_blur",
        cold_open=True,
        burn_captions=True,
        quality_mode="fast_preview",
        clip_size="480p",
        hardware_mode="cpu",
    )
    assert Path(out_mp4).exists()
    meta = probe_video(out_mp4)
    assert meta["width"] % 2 == 0 and meta["height"] % 2 == 0
    # 12.0s main + 2.80s concat teaser ~= 14.80s
    assert 14.4 <= float(meta["duration"]) <= 15.2, f"Unexpected cold-open duration: {meta['duration']}"


def test_trim_dead_air_cuts_pauses_and_remaps_captions(tmp_path):
    # Speech in 0..2.2s, dead silence 2.2..5.8s (3.6s gap!), speech in 5.8..9.5s
    def env_fn(t: float) -> float:
        return 0.0 if 2.25 <= t <= 5.75 else 0.45

    src_mp4 = _build_synthetic_video(
        tmp_path / "dead_air_src.mp4",
        duration_sec=10.0,
        width=640,
        height=360,
        fps=12,
        audio_env_fn=env_fn,
    )
    words = [
        {"word": "First", "start": 0.3, "end": 0.9},
        {"word": "part", "start": 1.0, "end": 1.9},
        {"word": "Resumed", "start": 6.0, "end": 6.8},
        {"word": "ending", "start": 7.0, "end": 9.2},
    ]

    plan = build_timeline_edit_plan(
        video_path=str(src_mp4),
        clip_start_time=0.0,
        clip_duration=10.0,
        words=words,
        cold_open=False,
        trim_dead_air=True,
        silence_threshold=DEAD_AIR_MIN_PAUSE_SEC,
        punch_zoom=False,
    )
    assert plan.has_cuts is True
    assert plan.dead_air_cuts_count >= 1
    assert plan.dead_air_saved_sec >= 2.0
    assert plan.main_duration < 8.0
    assert "pauses cut" in str(plan.timeline_fx_summary.get("dead_air_label") or "")

    resumed_w = next(w for w in plan.remapped_words if w["word"] == "Resumed")
    # Original word at 6.0s should shift backward by >= 2.0s into compressed timeline (~2.5..3.5s)
    assert float(resumed_w["start"]) < 4.5, f"Expected remapped word < 4.5s, got {resumed_w}"
    # Confirm 0.12s breathing cushion was preserved around speech boundaries
    assert len(plan.keep_intervals) >= 2
    assert (plan.keep_intervals[0][1] - 1.9) >= (DEAD_AIR_BREATH_BUFFER_SEC * 0.75)

    out_mp4 = render_clip_ultra(
        master_video_path=str(src_mp4),
        clip_id=902,
        start_time=0.0,
        duration=10.0,
        words=words,
        frame_layout="fit_blur",
        trim_dead_air=True,
        silence_threshold=DEAD_AIR_MIN_PAUSE_SEC,
        burn_captions=True,
        quality_mode="fast_preview",
        clip_size="480p",
        hardware_mode="cpu",
    )
    assert Path(out_mp4).exists()
    meta = probe_video(out_mp4)
    assert float(meta["duration"]) < 8.1, f"Expected trimmed duration < 8.1s, got {meta['duration']}"


def test_punch_zoom_fx_peaks_spacing_preview_zoom_and_export(tmp_path):
    peaks_src = [1.8, 5.5, 9.4, 13.3]

    def env_fn(t: float) -> float:
        for pt in peaks_src:
            if abs(t - pt) <= 0.18:
                return 0.95
        return 0.12

    def draw_fn(fr, fi, t, w, h):
        # Concentric colored rings so a zoom crop noticeably changes edge vs center ratios
        fr[:, :] = (25, 35, 190)  # Blue outer frame
        cv2.rectangle(fr, (int(w * 0.18), int(h * 0.18)), (int(w * 0.82), int(h * 0.82)), (30, 210, 45), -1)
        cv2.circle(fr, (w // 2, h // 2), int(h * 0.18), (240, 220, 40), -1)

    src_mp4 = _build_synthetic_video(
        tmp_path / "punch_src.mp4",
        duration_sec=16.0,
        width=640,
        height=360,
        fps=12,
        audio_env_fn=env_fn,
        frame_draw_fn=draw_fn,
    )

    pk = detect_punch_zoom_peaks(
        video_path=str(src_mp4),
        start_sec=0.0,
        end_sec=16.0,
        punch_intensity=1.20,
    )
    pts = pk["punch_timestamps"]
    assert 1 <= len(pts) <= 6
    for i in range(len(pts) - 1):
        assert (pts[i + 1] - pts[i]) >= 3.19, f"Peaks too close: {pts}"

    plan = build_timeline_edit_plan(
        video_path=str(src_mp4),
        clip_start_time=0.0,
        clip_duration=16.0,
        words=[],
        punch_zoom=True,
        punch_zoom_intensity=1.22,
    )
    assert len(plan.punch_intervals) >= 1
    assert "vocal peaks" in str(plan.timeline_fx_summary.get("punch_zoom_label") or "")

    # Sample right inside the first detected punch interval
    ps, pe, pz = plan.punch_intervals[0]
    peak_t = (ps + pe) * 0.5
    prev_off = render_exact_preview_frame(
        video_path=str(src_mp4),
        timestamp=peak_t,
        clip_start_time=0.0,
        clip_duration=16.0,
        clip_rel_ts_override=peak_t,
        frame_layout="fit_blur",
        burn_captions=False,
        dynamic_punch_zoom=False,
        punch_zoom=False,
        clip_size="480p",
    )
    prev_on = render_exact_preview_frame(
        video_path=str(src_mp4),
        timestamp=peak_t,
        clip_start_time=0.0,
        clip_duration=16.0,
        clip_rel_ts_override=peak_t,
        frame_layout="fit_blur",
        burn_captions=False,
        punch_zoom=True,
        punch_zoom_intensity=1.22,
        clip_size="480p",
    )
    diff = float(np.abs(_load_preview_png(prev_off) - _load_preview_png(prev_on)).mean())
    assert diff > 1.2, f"Expected punch zoom at peak_t={peak_t:.2f}s to alter pixels visibly, diff={diff}"

    out_mp4 = render_clip_ultra(
        master_video_path=str(src_mp4),
        clip_id=903,
        start_time=0.0,
        duration=8.0,
        words=[],
        frame_layout="fit_blur",
        punch_zoom=True,
        punch_zoom_intensity=1.20,
        burn_captions=False,
        quality_mode="fast_preview",
        clip_size="480p",
        hardware_mode="cpu",
    )
    assert Path(out_mp4).exists()
    meta = probe_video(out_mp4)
    assert meta["width"] % 2 == 0 and meta["height"] % 2 == 0


def test_all_10_framing_templates_render_distinctly_on_pure_gameplay_and_corner_overrides(tmp_path):
    # Quad-zone 16:9 test frame where every corner + center + HUD strip has distinct BGR color
    def draw_fn(fr, fi, t, w, h):
        hw, hh = w // 2, h // 2
        fr[:hh, :hw] = (230, 40, 30)       # Top-left: Blue
        fr[:hh, hw:] = (30, 40, 235)       # Top-right: Red
        fr[hh:, :hw] = (35, 220, 45)       # Bottom-left: Green
        fr[hh:, hw:] = (210, 40, 210)      # Bottom-right: Magenta
        # Distinct bright center action box
        cv2.rectangle(fr, (int(w * 0.34), int(h * 0.25)), (int(w * 0.66), int(h * 0.72)), (30, 215, 240), -1)
        # Distinct bottom center HUD mini-map strip
        cv2.rectangle(fr, (int(w * 0.25), int(h * 0.78)), (int(w * 0.75), h - 4), (240, 240, 30), -1)

    src_mp4 = _build_synthetic_video(
        tmp_path / "gameplay_10tpl.mp4",
        duration_sec=3.0,
        width=960,
        height=540,
        fps=8,
        frame_draw_fn=draw_fn,
    )
    pure_gameplay_analysis = {
        "face_detected": False,
        "webcam_detection": {"detected": False},
        "source_webcam_region": {"detected": False, "locked_zone": "top_left"},
        "keyframes": [{"t": 0.0, "cx": 0.50, "cy": 0.45, "action_cx": 0.50, "action_cy": 0.45}],
    }

    expected_templates = (
        "auto_track",
        "fit_blur",
        "webcam_gameplay_split",
        "gameplay_webcam_pip",
        "split_podcast",
        "cinema_letterbox_blur",
        "triple_gaming_hud",
        "portrait_pip_badge",
        "dynamic_punch_crop",
        "side_by_side_50_50",
    )
    assert tuple(ALL_FRAMING_TEMPLATES) == expected_templates

    imgs: Dict[str, np.ndarray] = {}
    for tpl in expected_templates:
        reg = resolve_template_regions(
            frame_w=960,
            frame_h=540,
            template_id=tpl,
            analysis_data=pure_gameplay_analysis,
            source_corner="top_right",
            output_corner="bottom_left",
        )
        for box_key in ("primary_box", "secondary_box", "hud_box"):
            bx, by, bw, bh = reg[box_key]
            assert bx % 2 == 0 and by % 2 == 0 and bw % 2 == 0 and bh % 2 == 0, f"{tpl}.{box_key}={reg[box_key]}"
            assert bw > 0 and bh > 0

        fg = build_multi_aspect_filter(
            aspect_ratio="9:16",
            frame_layout=tpl,
            source_w=960,
            source_h=540,
            webcam_corner="top_right",
            source_corner="top_left",
            face_track_data=pure_gameplay_analysis,
            is_video_render=False,
        )
        if tpl in ("gameplay_webcam_pip", "portrait_pip_badge"):
            assert "overlay=" in fg and "geq=" in fg, f"{tpl} must overlay rounded PiP even with zero faces"
        if tpl == "webcam_gameplay_split":
            assert "vstack=" in fg and "h=2" in fg
        if tpl == "triple_gaming_hud":
            assert "vstack=" in fg
        if tpl == "side_by_side_50_50":
            assert "hstack=" in fg

        res = render_exact_preview_frame(
            video_path=str(src_mp4),
            timestamp=1.0,
            clip_start_time=0.0,
            clip_duration=3.0,
            frame_layout=tpl,
            webcam_corner="top_right",
            source_corner="top_left",
            source_webcam_region={"detected": False, "locked_zone": "top_left"},
            burn_captions=False,
            clip_size="480p",
        )
        imgs[tpl] = _load_preview_png(res)

    # Assert structural distinctness across every pair of visually distinct templates
    distinct_pairs = [
        ("fit_blur", "auto_track"),
        ("webcam_gameplay_split", "gameplay_webcam_pip"),
        ("webcam_gameplay_split", "triple_gaming_hud"),
        ("gameplay_webcam_pip", "portrait_pip_badge"),
        ("cinema_letterbox_blur", "fit_blur"),
        ("dynamic_punch_crop", "auto_track"),
        ("side_by_side_50_50", "split_podcast"),
        ("split_podcast", "webcam_gameplay_split"),
        ("portrait_pip_badge", "triple_gaming_hud"),
    ]
    for a, b in distinct_pairs:
        diff = float(np.abs(imgs[a] - imgs[b]).mean())
        assert diff > 2.5, f"Templates {a} and {b} look indistinguishable on pure gameplay (diff={diff:.2f})"

    # Verify source_corner + output corner overrides change pixels in BOTH preview_frame and renderer.py
    p_tl_tr = _load_preview_png(
        render_exact_preview_frame(
            video_path=str(src_mp4),
            timestamp=1.0,
            clip_start_time=0.0,
            clip_duration=3.0,
            frame_layout="gameplay_webcam_pip",
            webcam_corner="top_right",
            source_corner="top_left",
            source_webcam_region={"detected": False, "locked_zone": "top_left"},
            burn_captions=False,
            clip_size="480p",
        )
    )
    p_br_bl = _load_preview_png(
        render_exact_preview_frame(
            video_path=str(src_mp4),
            timestamp=1.0,
            clip_start_time=0.0,
            clip_duration=3.0,
            frame_layout="gameplay_webcam_pip",
            webcam_corner="bottom_left",
            source_corner="bottom_right",
            source_webcam_region={"detected": False, "locked_zone": "bottom_right"},
            burn_captions=False,
            clip_size="480p",
        )
    )
    assert float(np.abs(p_tl_tr - p_br_bl).mean()) > 5.0

    # Render an actual MP4 for triple_gaming_hud and side_by_side_50_50 to verify zero FFmpeg errors
    for tpl in ("triple_gaming_hud", "side_by_side_50_50", "portrait_pip_badge"):
        out_mp4 = render_clip_ultra(
            master_video_path=str(src_mp4),
            clip_id=910,
            start_time=0.0,
            duration=2.5,
            words=[],
            frame_layout=tpl,
            webcam_corner="top_right",
            source_webcam_region={"detected": False, "locked_zone": "top_left"},
            burn_captions=False,
            quality_mode="fast_preview",
            clip_size="480p",
            hardware_mode="cpu",
        )
        meta = probe_video(out_mp4)
        assert meta["width"] % 2 == 0 and meta["height"] % 2 == 0


def test_user_locked_framing_template_not_overwritten_by_auto_frame(tmp_path, monkeypatch):
    src_mp4 = _build_synthetic_video(
        tmp_path / "lock_test.mp4",
        duration_sec=4.0,
        width=640,
        height=360,
        fps=8,
    )
    db_file = tmp_path / "isolated_lock.db"
    engine = create_engine(f"sqlite:///{db_file}", connect_args={"check_same_thread": False})
    SQLModel.metadata.create_all(engine)

    with Session(engine) as session:
        proj = Project(title="Lock Test", source_path=str(src_mp4), status="completed")
        session.add(proj)
        session.commit()
        session.refresh(proj)

        locked_clip = ViralClip(
            project_id=proj.id,
            start_time=0.0,
            end_time=3.5,
            duration=3.5,
            frame_layout="fit_blur",
        )
        unlocked_clip = ViralClip(
            project_id=proj.id,
            start_time=0.0,
            end_time=3.5,
            duration=3.5,
            frame_layout="auto_track",
        )
        session.add(locked_clip)
        session.add(unlocked_clip)
        session.commit()
        session.refresh(locked_clip)
        session.refresh(unlocked_clip)

        # User explicitly switches locked_clip to triple_gaming_hud via update_clip
        updated = main_mod.update_clip(
            locked_clip.id,
            UpdateClipRequest(frame_layout="triple_gaming_hud"),
            session=session,
        )
        assert updated["status"] == "updated"
        session.refresh(locked_clip)
        assert locked_clip.frame_layout == "triple_gaming_hud"
        assert locked_clip.get_options().get("framing_user_locked") is True

        # Mock visual analyzer + auto framer so auto_frame_all_clips_endpoint would recommend webcam_gameplay_split
        monkeypatch.setattr(
            "app.services.vision_analyzer.analyze_clip_visuals",
            lambda *a, **kw: {"face_detected": True, "summary": {"dominant_scene_type": "gaming_cam"}},
        )
        monkeypatch.setattr(
            "app.services.auto_framer.recommend_optimal_framing",
            lambda *a, **kw: {
                "frame_layout": "webcam_gameplay_split",
                "webcam_corner": "top_left",
                "scene_type": "gaming_cam",
                "reason": "Mock recommendation",
                "confidence": 0.9,
            },
        )

        res = main_mod.auto_frame_all_clips_endpoint(proj.id, session=session)
        assert res["status"] == "completed"

        session.refresh(locked_clip)
        session.refresh(unlocked_clip)
        assert locked_clip.frame_layout == "triple_gaming_hud"
        assert unlocked_clip.frame_layout == "webcam_gameplay_split"
