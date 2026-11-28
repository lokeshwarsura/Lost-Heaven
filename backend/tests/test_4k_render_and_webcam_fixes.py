import re
import subprocess
import threading
import time
from pathlib import Path

import cv2
import numpy as np
import pytest

from app.config import settings
from app.services import best_frame_engine, renderer
from app.services.auto_framer import recommend_optimal_framing
from app.services.hardware import build_video_encoder_args
from app.services.proxy import get_ffmpeg_cmd, probe_video
from app.services.reframer import build_multi_aspect_filter
from app.services.render_queue import RenderSupersededError, compute_render_signature
from app.services.webcam_detector import detect_source_webcam
from tests.test_realistic_camera_framing import _draw_haar_portrait


def _make_4k_source_mp4(out_path: Path, duration: float = 1.0) -> Path:
    out_path.parent.mkdir(parents=True, exist_ok=True)
    cmd = [
        get_ffmpeg_cmd(),
        "-y",
        "-f", "lavfi",
        "-i", f"testsrc2=size=3840x2160:rate=10:duration={duration}",
        "-f", "lavfi",
        "-i", f"sine=frequency=440:sample_rate=44100:d={duration}",
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


def _make_gameplay_with_moving_npc(
    out_path: Path,
    zone: str = "mid_left",
    present_fraction: float = 1.0,
    move_across_frames: bool = True,
    num_frames: int = 16,
    fps: int = 8,
) -> Path:
    out_path.parent.mkdir(parents=True, exist_ok=True)
    frame_dir = out_path.parent / f"frames_{out_path.stem}"
    frame_dir.mkdir(parents=True, exist_ok=True)
    present_count = int(round(num_frames * present_fraction))

    for idx in range(num_frames):
        canvas = np.full((1080, 1920, 3), (38, 65, 92), dtype=np.uint8)
        # Simulated dynamic gameplay world geometry
        shift = (idx * 37) % 500
        cv2.rectangle(canvas, (450 + shift, 220), (780 + shift, 540), (70, 130, 190), -1)
        cv2.circle(canvas, (1320 - shift // 2, 720), 95, (45, 165, 130), -1)

        if idx < present_count:
            roi = np.full((240, 320, 3), (88, 118, 145), dtype=np.uint8)
            _draw_haar_portrait(roi)
            cw, ch = 360, 270
            roi_resized = cv2.resize(roi, (cw, ch), interpolation=cv2.INTER_AREA)

            if zone == "top_left":
                base_x, base_y = 30, 30
            else:  # mid_left
                base_x, base_y = 30, 390

            dx = int(round((idx * 24) if move_across_frames else 0))
            dy = int(round((idx * 10) if move_across_frames else 0))
            px = max(0, min(1920 - cw, base_x + dx))
            py = max(0, min(1080 - ch, base_y + dy))
            canvas[py:py + ch, px:px + cw] = roi_resized

        cv2.imwrite(str(frame_dir / f"fr_{idx:03d}.png"), canvas)

    cmd = [
        get_ffmpeg_cmd(),
        "-y",
        "-framerate", str(fps),
        "-i", str(frame_dir / "fr_%03d.png"),
        "-c:v", "libx264",
        "-preset", "ultrafast",
        "-pix_fmt", "yuv420p",
        str(out_path),
    ]
    res = subprocess.run(cmd, capture_output=True, text=True, timeout=60)
    assert res.returncode == 0, res.stderr
    return out_path


def test_encoder_flag_isolation_and_4k_even_dimensions_render(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    storage_dir = tmp_path / "storage_4k"
    storage_dir.mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr(settings, "STORAGE_DIR", str(storage_dir))

    # 1. Encoder CLI flags strictly isolated from NVENC-only flags on libx264
    nvenc_only = {"-rc", "-cq", "-spatial-aq", "-temporal-aq", "-tier", "-b_ref_mode", "-tune"}
    cpu_args = build_video_encoder_args("h264_nvenc", quality_mode="final_hq", hardware_mode="cpu")
    assert cpu_args[:2] == ["-c:v", "libx264"]
    assert not (nvenc_only & set(cpu_args))

    x264_args = build_video_encoder_args("libx264", quality_mode="fast_preview", hardware_mode="gpu")
    assert x264_args[:2] == ["-c:v", "libx264"]
    assert not (nvenc_only & set(x264_args))

    nv_args = build_video_encoder_args("h264_nvenc", quality_mode="fast_preview", hardware_mode="gpu")
    assert nv_args[:2] == ["-c:v", "h264_nvenc"]

    # 2. Filtergraphs for 4K -> 720p / 480p never leave an unguarded fit_blur odd height
    for layout in ("fit_blur", "auto_track", "gameplay_webcam_pip", "webcam_gameplay_split"):
        fg = build_multi_aspect_filter(
            aspect_ratio="9:16",
            frame_layout=layout,
            source_w=3840,
            source_h=2160,
            target_size=(720, 1280),
            face_track_data={
                "webcam_detection": {
                    "detected": True,
                    "webcam_detected": True,
                    "source_zone": "top_left",
                    "x": 0.02,
                    "y": 0.02,
                    "w": 0.25,
                    "h": 0.25,
                    "action_cx": 0.5,
                    "action_cy": 0.5,
                }
            },
            is_video_render=True,
        )
        assert fg.endswith("[v]")
        if layout == "fit_blur":
            assert "scale=trunc(iw/2)*2:trunc(ih/2)*2" in fg
        for m in re.finditer(r"(?:scale|crop|pad)=([0-9]+):([0-9]+)", fg):
            w_v, h_v = int(m.group(1)), int(m.group(2))
            assert w_v % 2 == 0 and h_v % 2 == 0

    # 3. Actual 3840x2160 render into 720p fit_blur + libx264 completes cleanly with even dims
    src_4k = _make_4k_source_mp4(tmp_path / "source_4k.mp4", duration=1.0)
    out_mp4 = renderer.render_clip_ultra(
        master_video_path=str(src_4k),
        clip_id=901,
        project_id=77,
        start_time=0.0,
        duration=0.8,
        words=[],
        aspect_ratio="9:16",
        frame_layout="fit_blur",
        clip_size="720p",
        burn_captions=False,
        hardware_mode="cpu",
        quality_mode="fast_preview",
    )
    assert Path(out_mp4).exists()
    meta = probe_video(out_mp4)
    assert meta["width"] == 720 and meta["height"] == 1280


def test_render_queue_deduplicates_identical_inflight_and_supersedes_changed(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
):
    storage_dir = tmp_path / "storage_dedup"
    storage_dir.mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr(settings, "STORAGE_DIR", str(storage_dir))

    # Build lightweight input video so concurrency runs swiftly
    src_mp4 = tmp_path / "src_dedup.mp4"
    res = subprocess.run(
        [
            get_ffmpeg_cmd(), "-y",
            "-f", "lavfi", "-i", "testsrc2=size=640x360:rate=15:duration=2.0",
            "-f", "lavfi", "-i", "sine=frequency=440:sample_rate=44100:d=2.0",
            "-c:v", "libx264", "-preset", "ultrafast", "-pix_fmt", "yuv420p",
            "-c:a", "aac", "-shortest", str(src_mp4),
        ],
        capture_output=True, text=True, timeout=60,
    )
    assert res.returncode == 0, res.stderr

    # Signature equality vs sensitivity check
    sig_a = compute_render_signature(11, 5, 0.0, 1.5, {"frame_layout": "fit_blur", "zoom": 1.0, "options": {}})
    sig_b = compute_render_signature(11, 5, 0.0, 1.5, {"frame_layout": "fit_blur", "zoom": 1.0, "options": None})
    sig_c = compute_render_signature(11, 5, 0.0, 1.5, {"frame_layout": "fit_blur", "zoom": 1.25, "options": {}})
    assert sig_a == sig_b
    assert sig_a != sig_c

    popen_calls = []
    real_popen = subprocess.Popen

    def spy_popen(*args, **kwargs):
        popen_calls.append(args[0])
        return real_popen(*args, **kwargs)

    monkeypatch.setattr(renderer.subprocess, "Popen", spy_popen)

    outcomes = {}
    barrier = threading.Barrier(2)

    def worker(tag: str, zoom_val: float):
        barrier.wait(timeout=5.0)
        try:
            out = renderer.render_clip_ultra(
                master_video_path=str(src_mp4),
                clip_id=55,
                project_id=9,
                start_time=0.0,
                duration=1.2,
                words=[],
                frame_layout="fit_blur",
                zoom=zoom_val,
                burn_captions=False,
                hardware_mode="cpu",
                quality_mode="fast_preview",
            )
            outcomes[tag] = ("ok", out)
        except RenderSupersededError as err:
            outcomes[tag] = ("superseded", str(err))

    # 1. Identical concurrent renders attach & await -> 1 FFmpeg spawn, both get completed path
    t1 = threading.Thread(target=worker, args=("a", 1.0))
    t2 = threading.Thread(target=worker, args=("b", 1.0))
    t1.start()
    t2.start()
    t1.join(timeout=30.0)
    t2.join(timeout=30.0)

    assert outcomes["a"][0] == "ok" and outcomes["b"][0] == "ok"
    assert outcomes["a"][1] == outcomes["b"][1]
    assert Path(outcomes["a"][1]).exists()
    ffmpeg_render_calls = [
        cmd for cmd in popen_calls
        if cmd and "ffmpeg" in Path(str(cmd[0])).name.lower() and any("clip_55_" in str(tok) for tok in cmd)
    ]
    assert len(ffmpeg_render_calls) == 1

    # 2. Changed option supersedes active worker cleanly
    outcomes.clear()
    first_err = {}

    def worker1():
        try:
            out = renderer.render_clip_ultra(
                master_video_path=str(src_mp4),
                clip_id=56,
                project_id=9,
                start_time=0.0,
                duration=1.8,
                words=[],
                frame_layout="fit_blur",
                zoom=1.0,
                burn_captions=False,
                hardware_mode="cpu",
                quality_mode="final_hq",
            )
            first_err["w1"] = ("ok", out)
        except RenderSupersededError:
            first_err["w1"] = ("superseded", "")

    tw1 = threading.Thread(target=worker1)
    tw1.start()
    deadline = time.time() + 10.0
    while time.time() < deadline:
        with renderer._render_lock:
            if "9:56" in renderer._active_renders:
                break
        time.sleep(0.02)

    out2 = renderer.render_clip_ultra(
        master_video_path=str(src_mp4),
        clip_id=56,
        project_id=9,
        start_time=0.0,
        duration=1.8,
        words=[],
        frame_layout="fit_blur",
        zoom=1.35,
        burn_captions=False,
        hardware_mode="cpu",
        quality_mode="fast_preview",
    )
    tw1.join(timeout=30.0)
    assert Path(out2).exists()
    assert first_err.get("w1", ("", ""))[0] == "superseded"


def test_best_frame_engine_skips_reanalysis_and_extracts_ffmpeg_stills(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
):
    src_mp4 = tmp_path / "bf_src.mp4"
    res = subprocess.run(
        [
            get_ffmpeg_cmd(), "-y",
            "-f", "lavfi", "-i", "testsrc2=size=1280x720:rate=15:duration=6.0",
            "-c:v", "libx264", "-preset", "ultrafast", "-pix_fmt", "yuv420p",
            str(src_mp4),
        ],
        capture_output=True, text=True, timeout=60,
    )
    assert res.returncode == 0, res.stderr

    # Even with only 1 sparse frame in the clip span (< 3), analyze_video_track must NOT re-run
    monkeypatch.setattr(
        best_frame_engine,
        "analyze_video_track",
        lambda *a, **kw: pytest.fail("analyze_video_track must not re-run when precomputed frames exist"),
    )

    precomputed = {
        "frames": [
            {
                "t": 1.0,
                "sharpness_score": 82.0,
                "exposure_score": 68.0,
                "action_score": 55.0,
                "composition_score": 74.0,
                "emotion_intensity": 60.0,
                "eyes_open_confidence": 1.0,
                "gaze_score": 72.0,
                "face_count": 0,
                "is_black": False,
            },
            {
                "t": 7.5,
                "sharpness_score": 80.0,
                "exposure_score": 66.0,
                "action_score": 64.0,
                "composition_score": 70.0,
                "emotion_intensity": 55.0,
                "eyes_open_confidence": 1.0,
                "gaze_score": 70.0,
                "face_count": 0,
                "is_black": False,
            },
        ]
    }
    bf_out = best_frame_engine.extract_best_frames(
        video_path=str(src_mp4),
        video_analysis=precomputed,
        audio_analysis=None,
        start_time=0.5,
        end_time=5.5,
        output_dir=str(tmp_path / "bf_stills"),
        clip_id=31,
        project_id=4,
    )
    assert bf_out["frames"]
    for item in bf_out["frames"]:
        assert item["image_path"] and Path(item["image_path"]).exists()
        assert Path(item["image_path"]).stat().st_size > 0


def test_webcam_detector_rejects_moving_npcs_and_unbordered_mid_left(tmp_path: Path):
    moving_mid_left = _make_gameplay_with_moving_npc(
        tmp_path / "gta_moving_npc.mp4",
        zone="mid_left",
        present_fraction=0.75,
        move_across_frames=True,
    )
    reg_moving = detect_source_webcam(str(moving_mid_left), start_time=0.0, end_time=2.0, num_samples=16)
    assert reg_moving["detected"] is False
    assert reg_moving["webcam_detected"] is False
    assert reg_moving["source_zone"] == "none"

    rec = recommend_optimal_framing({
        "webcam": reg_moving,
        "keyframes": [{"t": 0.0, "face_count": 0, "action_cx": 0.5, "action_cy": 0.5}],
        "shots": [],
        "summary": {"has_corner_webcam": False, "face_presence_ratio": 0.0, "dual_speaker_ratio": 0.0},
    })
    assert rec["frame_layout"] != "gameplay_webcam_pip"

    sporadic_tl = _make_gameplay_with_moving_npc(
        tmp_path / "sporadic_tl_npc.mp4",
        zone="top_left",
        present_fraction=0.25,
        move_across_frames=False,
    )
    reg_sporadic = detect_source_webcam(str(sporadic_tl), start_time=0.0, end_time=2.0, num_samples=16)
    assert reg_sporadic["detected"] is False
    assert reg_sporadic["webcam_detected"] is False
    assert reg_sporadic["source_zone"] == "none"
