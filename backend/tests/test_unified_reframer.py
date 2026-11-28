import os
import subprocess
from pathlib import Path
from typing import List, Tuple

import cv2
import numpy as np
import pytest
from PIL import Image

from app.config import settings
from app.services.preview_frame import render_exact_preview_frame
from app.services.proxy import get_ffmpeg_cmd, probe_video
from app.services.reframer import build_multi_aspect_filter, prepare_clip_framing_data
from app.services.renderer import render_clip_ultra
from app.services.vision_analyzer import (
    analyze_clip_visuals,
    compute_head_and_shoulders_box,
)


def _draw_portrait(canvas: np.ndarray, cx: int, cy: int, scale: float = 1.0, shirt_bgr: Tuple[int, int, int] = (70, 80, 110)) -> None:
    """Draw a high-contrast Haar/MediaPipe-detectable studio portrait centered at (cx, cy)."""
    roi = np.full((240, 320, 3), -1, dtype=np.int16)
    cv2.ellipse(roi, (160, 95), (58, 74), 0, 0, 360, (55, 45, 40), -1)
    cv2.ellipse(roi, (160, 115), (52, 68), 0, 0, 360, (160, 195, 232), -1)
    cv2.ellipse(roi, (140, 100), (11, 6), 0, 0, 360, (245, 245, 245), -1)
    cv2.ellipse(roi, (180, 100), (11, 6), 0, 0, 360, (245, 245, 245), -1)
    cv2.circle(roi, (140, 100), 5, (35, 30, 25), -1)
    cv2.circle(roi, (180, 100), 5, (35, 30, 25), -1)
    cv2.line(roi, (126, 87), (152, 85), (45, 35, 30), 3)
    cv2.line(roi, (168, 85), (194, 87), (45, 35, 30), 3)
    cv2.line(roi, (160, 102), (156, 128), (130, 150, 185), 3)
    cv2.ellipse(roi, (160, 146), (18, 7), 0, 0, 180, (80, 75, 140), 3)
    cv2.rectangle(roi, (105, 178), (215, 240), shirt_bgr, -1)

    tw = max(40, int(round(320 * scale)))
    th = max(40, int(round(240 * scale)))
    mask = (roi[:, :, 0] >= 0).astype(np.uint8) * 255
    roi_u8 = np.clip(roi, 0, 255).astype(np.uint8)
    roi_s = cv2.resize(roi_u8, (tw, th), interpolation=cv2.INTER_AREA)
    mask_s = cv2.resize(mask, (tw, th), interpolation=cv2.INTER_NEAREST)

    x0 = cx - tw // 2
    y0 = cy - th // 2
    H, W = canvas.shape[:2]
    sx0, sy0 = max(0, -x0), max(0, -y0)
    dx0, dy0 = max(0, x0), max(0, y0)
    dx1, dy1 = min(W, x0 + tw), min(H, y0 + th)
    if dx1 > dx0 and dy1 > dy0:
        sub_roi = roi_s[sy0:sy0 + (dy1 - dy0), sx0:sx0 + (dx1 - dx0)]
        sub_mask = mask_s[sy0:sy0 + (dy1 - dy0), sx0:sx0 + (dx1 - dx0)] > 127
        target = canvas[dy0:dy1, dx0:dx1]
        target[sub_mask] = sub_roi[sub_mask]


def _encode_png_sequence_video(
    out_path: Path,
    segments: List[Tuple[np.ndarray, float]],
    fps: int = 15,
) -> Path:
    """Encode multi-shot BGR canvases cleanly to H.264 + AAC MP4."""
    out_path.parent.mkdir(parents=True, exist_ok=True)
    ffmpeg = get_ffmpeg_cmd()
    tmp_dir = out_path.parent / f"seq_{out_path.stem}"
    tmp_dir.mkdir(parents=True, exist_ok=True)
    concat_lines = ["ffconcat version 1.0"]
    total_dur = 0.0
    last_png = None
    for idx, (frame_bgr, seg_dur) in enumerate(segments):
        p = tmp_dir / f"seg_{idx}.png"
        cv2.imwrite(str(p), frame_bgr)
        concat_lines.append(f"file '{p.name}'")
        concat_lines.append(f"duration {seg_dur:.3f}")
        total_dur += seg_dur
        last_png = p
    if last_png is not None:
        concat_lines.append(f"file '{last_png.name}'")

    concat_file = tmp_dir / "segs.ffconcat"
    concat_file.write_text("\n".join(concat_lines), encoding="utf-8")

    cmd = [
        ffmpeg, "-y",
        "-f", "concat", "-safe", "0", "-i", concat_file.name,
        "-f", "lavfi", "-i", f"sine=frequency=440:sample_rate=44100:d={total_dur:.3f}",
        "-vf", f"fps={fps}",
        "-c:v", "libx264", "-preset", "ultrafast", "-pix_fmt", "yuv420p",
        "-c:a", "aac", "-shortest",
        str(out_path.resolve()),
    ]
    res = subprocess.run(cmd, capture_output=True, text=True, cwd=str(tmp_dir), timeout=60)
    assert res.returncode == 0, res.stderr
    return out_path


def _setup_temp_storage(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    storage_dir = tmp_path / "storage"
    for sub in ("previews", "renders", "masters", "assets"):
        (storage_dir / sub).mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr(settings, "STORAGE_DIR", str(storage_dir))
    return storage_dir


def test_unified_visual_analyzer_cuts_portrait_and_zero_face_saliency(tmp_path: Path):
    # Ergonomic portrait box places eyes near ~36% from portrait top with shoulder margin
    hs = compute_head_and_shoulders_box({"cx": 0.50, "cy": 0.42, "w": 0.14, "h": 0.20}, 1920, 1080)
    rel_eye_y = (hs["eyes_cy"] - hs["y"]) / max(1e-4, hs["h"])
    assert 0.28 <= rel_eye_y <= 0.44
    assert hs["h"] >= 0.35 and hs["w"] >= 0.25

    # 3-shot multi-scene clip:
    # Shot 0 (0..1.5s): dark teal backdrop + speaker Left (x~480)
    # Shot 1 (1.5..3.0s): warm crimson backdrop + speaker Right (x~1440)
    # Shot 2 (3.0..4.5s): zero faces dark blue gameplay + bright HUD target bottom-right
    s0 = np.full((1080, 1920, 3), (120, 85, 22), dtype=np.uint8)
    _draw_portrait(s0, 480, 460, scale=1.5)

    s1 = np.full((1080, 1920, 3), (30, 45, 165), dtype=np.uint8)
    _draw_portrait(s1, 1440, 460, scale=1.5)

    s2 = np.full((1080, 1920, 3), (45, 25, 18), dtype=np.uint8)
    cv2.rectangle(s2, (1280, 520), (1760, 880), (40, 230, 240), -1)
    cv2.circle(s2, (1520, 700), 90, (240, 240, 240), 8)

    mp4 = _encode_png_sequence_video(
        tmp_path / "multi_shot.mp4",
        [(s0, 1.5), (s1, 1.5), (s2, 1.5)],
    )
    vmap = analyze_clip_visuals(str(mp4), start_time=0.0, end_time=4.5, sample_fps=5.0, use_cache=False)

    assert len(vmap["cut_timestamps"]) >= 2
    assert len(vmap["shots"]) >= 3
    # Shot 0 tracks left, Shot 1 snaps instantaneously to right, Shot 2 tracks zero-face saliency right
    assert vmap["shots"][0]["cx"] < 0.40
    assert vmap["shots"][1]["cx"] > 0.62
    assert vmap["shots"][2]["speaker_mode"] == "zero_face"
    assert vmap["shots"][2]["action_cx"] > 0.58


def test_all_five_templates_and_split_podcast_cutaway(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
):
    storage_dir = _setup_temp_storage(tmp_path, monkeypatch)

    # Shot 0 (0..1.6s): 2-speaker wide shot (Left blue shirt @ x=440, Right green shirt @ x=1480)
    wide = np.full((1080, 1920, 3), (110, 75, 25), dtype=np.uint8)
    _draw_portrait(wide, 440, 460, scale=1.45, shirt_bgr=(220, 80, 30))
    _draw_portrait(wide, 1480, 460, scale=1.45, shirt_bgr=(40, 200, 70))

    # Shot 1 (1.6..3.2s): 1-speaker close-up cutaway (centered large red shirt @ x=960)
    closeup = np.full((1080, 1920, 3), (35, 35, 95), dtype=np.uint8)
    _draw_portrait(closeup, 960, 460, scale=2.1, shirt_bgr=(40, 50, 225))

    pod_mp4 = _encode_png_sequence_video(
        tmp_path / "podcast_wide_and_cutaway.mp4",
        [(wide, 1.6), (closeup, 1.6)],
    )

    # 1. split_podcast cutaway-aware verification (dual stack @ t=0.8s, single cutaway @ t=2.4s)
    prev_dual = render_exact_preview_frame(
        video_path=str(pod_mp4),
        timestamp=0.8,
        aspect_ratio="9:16",
        frame_layout="split_podcast",
        burn_captions=False,
    )
    prev_cut = render_exact_preview_frame(
        video_path=str(pod_mp4),
        timestamp=2.4,
        aspect_ratio="9:16",
        frame_layout="split_podcast",
        burn_captions=False,
    )
    img_dual = np.array(Image.open(storage_dir / "previews" / Path(prev_dual["frame_url"]).name).convert("RGB"))
    img_cut = np.array(Image.open(storage_dir / "previews" / Path(prev_cut["frame_url"]).name).convert("RGB"))
    H, W = img_dual.shape[:2]
    # Top 40% vs bottom 40% differ distinctly on 2-speaker wide shot (Speaker L blue vs Speaker R green)
    assert float(np.mean(np.abs(img_dual[:H // 2 - 40].astype(np.float32) - img_dual[H // 2 + 40:].astype(np.float32)))) > 8.0
    # Cutaway shot shows single close-up centered speaker without repeating identical halves
    top_cut_g = float(np.mean(img_cut[:H // 2 - 40, :, 1]))
    bot_cut_g = float(np.mean(img_cut[H // 2 + 40:, :, 1]))
    assert abs(top_cut_g - bot_cut_g) > 15.0

    # Render full MP4 of cutaway-aware split_podcast -> verify dual stack @ 0.8s & single cutaway @ 2.4s
    pod_out = render_clip_ultra(
        master_video_path=str(pod_mp4),
        clip_id=701,
        start_time=0.0,
        duration=3.2,
        words=[],
        aspect_ratio="9:16",
        frame_layout="split_podcast",
        burn_captions=False,
        show_hook_banner=False,
        hardware_mode="cpu",
        quality_mode="fast_preview",
        output_path_override=str(storage_dir / "renders" / "podcast_cutaway.mp4"),
    )
    assert Path(pod_out).exists() and probe_video(pod_out)["width"] == 1080
    cap_pod = cv2.VideoCapture(pod_out)
    try:
        cap_pod.set(cv2.CAP_PROP_POS_MSEC, 800.0)
        ok_d, rnd_d = cap_pod.read()
        cap_pod.set(cv2.CAP_PROP_POS_MSEC, 2400.0)
        ok_c, rnd_c = cap_pod.read()
        assert ok_d and ok_c and rnd_d is not None and rnd_c is not None
        assert float(np.mean(np.abs(rnd_d[:H // 2 - 40].astype(np.float32) - rnd_d[H // 2 + 40:].astype(np.float32)))) > 8.0
        assert abs(float(np.mean(rnd_c[:H // 2 - 40, :, 1])) - float(np.mean(rnd_c[H // 2 + 40:, :, 1]))) > 15.0
    finally:
        cap_pod.release()

    # 2. fit_blur verification (darkened adaptive backdrop + crisp 1px edge highlight + full width uncropped center)
    vf_blur = build_multi_aspect_filter("9:16", "fit_blur", 1920, 1080)
    assert "brightness=-0.12:saturation=1.15" in vf_blur
    assert "gblur=sigma=24" in vf_blur
    assert "drawbox=x=0:y=0:w=iw:h=1" in vf_blur

    # 3. gameplay_webcam_pip on zero-face gameplay still renders 32% rounded PiP of deterministic secondary_box
    game_only = np.full((1080, 1920, 3), (120, 85, 22), dtype=np.uint8)
    cv2.rectangle(game_only, (400, 300), (900, 700), (40, 210, 180), -1)
    game_mp4 = _encode_png_sequence_video(tmp_path / "game_only.mp4", [(game_only, 1.5)])
    ft_game, _, _ = prepare_clip_framing_data(str(game_mp4), [(0.0, 1.5)], frame_layout="gameplay_webcam_pip")
    vf_pip_gameplay = build_multi_aspect_filter(
        "9:16", "gameplay_webcam_pip", 1920, 1080, face_track_data=ft_game
    )
    assert "geq=" in vf_pip_gameplay and "pad=" in vf_pip_gameplay and "overlay=" in vf_pip_gameplay

    # 4. webcam_gameplay_split filter produces Top 38% / Bottom 62% stack + 3px divider
    vf_split = build_multi_aspect_filter("9:16", "webcam_gameplay_split", 1920, 1080, face_track_data=ft_game)
    assert "vstack=inputs=2" in vf_split and "h=3:color=0xFFFFFF@0.28" in vf_split


def test_preview_and_render_pixel_coordinate_parity(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
):
    storage_dir = _setup_temp_storage(tmp_path, monkeypatch)

    # Scene 0 (0..1.5s): Speaker Left on cool teal; Scene 1 (1.5..3.0s): Speaker Right on warm brown
    s0 = np.full((1080, 1920, 3), (140, 95, 25), dtype=np.uint8)
    _draw_portrait(s0, 420, 460, scale=1.55, shirt_bgr=(210, 70, 40))

    s1 = np.full((1080, 1920, 3), (35, 75, 155), dtype=np.uint8)
    _draw_portrait(s1, 1480, 460, scale=1.55, shirt_bgr=(50, 200, 90))

    mp4 = _encode_png_sequence_video(
        tmp_path / "parity_source.mp4",
        [(s0, 1.5), (s1, 1.5)],
    )

    out_mp4 = render_clip_ultra(
        master_video_path=str(mp4),
        clip_id=702,
        start_time=0.0,
        duration=3.0,
        words=[],
        aspect_ratio="9:16",
        frame_layout="auto_track",
        burn_captions=False,
        show_hook_banner=False,
        hardware_mode="cpu",
        quality_mode="fast_preview",
        output_path_override=str(storage_dir / "renders" / "parity_auto.mp4"),
    )
    assert Path(out_mp4).exists()

    cap = cv2.VideoCapture(out_mp4)
    try:
        for test_t in (0.6, 2.3):
            prev = render_exact_preview_frame(
                video_path=str(mp4),
                timestamp=test_t,
                clip_start_time=0.0,
                clip_duration=3.0,
                aspect_ratio="9:16",
                frame_layout="auto_track",
                burn_captions=False,
            )
            prev_img = np.array(Image.open(storage_dir / "previews" / Path(prev["frame_url"]).name).convert("RGB"))

            cap.set(cv2.CAP_PROP_POS_MSEC, test_t * 1000.0)
            ok, rnd_bgr = cap.read()
            assert ok and rnd_bgr is not None
            rnd_rgb = cv2.cvtColor(rnd_bgr, cv2.COLOR_BGR2RGB)
            assert prev_img.shape == rnd_rgb.shape
            mad = float(np.mean(np.abs(prev_img.astype(np.float32) - rnd_rgb.astype(np.float32))))
            assert mad < 7.0, f"Preview vs render coordinate/pixel mismatch at t={test_t}s (MAD={mad:.2f})"
    finally:
        cap.release()
