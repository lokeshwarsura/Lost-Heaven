import subprocess
from pathlib import Path
from typing import List, Dict

import cv2
import numpy as np
import pytest

from app.config import settings
from app.services import face_tracker
from app.services.caption_renderer import (
    normalize_and_chunk_words,
    render_caption_frame,
    build_caption_ffconcat,
)
from app.services.proxy import get_ffmpeg_cmd
from app.services.preview_frame import render_exact_preview_frame
from app.services.renderer import render_clip_ultra


def _create_synthetic_video(out_path: Path, duration_sec: float = 124.0, width: int = 640, height: int = 360) -> Path:
    """Create a real H.264 + AAC testsrc2 MP4 with rich spatial asymmetry for framing/caption diffs."""
    out_path.parent.mkdir(parents=True, exist_ok=True)
    cmd = [
        get_ffmpeg_cmd(),
        "-y",
        "-f", "lavfi",
        "-i", f"testsrc2=size={width}x{height}:rate=15:duration={duration_sec}",
        "-f", "lavfi",
        "-i", f"sine=frequency=220:sample_rate=44100:duration={duration_sec}",
        "-af", "volume=0.05",
        "-c:v", "libx264",
        "-preset", "ultrafast",
        "-pix_fmt", "yuv420p",
        "-c:a", "aac",
        "-shortest",
        str(out_path),
    ]
    res = subprocess.run(cmd, capture_output=True, text=True, timeout=60)
    assert res.returncode == 0, f"Failed to create synthetic video: {res.stderr}"
    return out_path


@pytest.fixture()
def unicode_storage_and_video(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    unicode_root = tmp_path / "ドキュメント with spaces"
    storage_dir = unicode_root / "storage_root"
    storage_dir.mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr(settings, "STORAGE_DIR", str(storage_dir))

    video_path = _create_synthetic_video(unicode_root / "source sample video.mp4", duration_sec=124.0)
    return storage_dir, video_path


def _load_preview_image(storage_dir: Path, frame_url: str) -> np.ndarray:
    filename = Path(frame_url).name
    img_path = storage_dir / "previews" / filename
    assert img_path.exists(), f"Missing preview output at {img_path}"
    raw_bytes = np.fromfile(str(img_path), dtype=np.uint8)
    img = cv2.imdecode(raw_bytes, cv2.IMREAD_COLOR)
    assert img is not None, f"Could not decode preview PNG: {img_path}"
    return img


def test_captions_burn_unicode_path_and_absolute_timestamps(unicode_storage_and_video):
    storage_dir, video_path = unicode_storage_and_video

    # Video-absolute timestamps around 120.0s..123.0s
    words: List[Dict] = [
        {"word": "HELLO", "start": 120.1, "end": 120.6},
        {"word": "VIRAL", "start": 120.6, "end": 121.3},
        {"word": "WORLD", "start": 121.3, "end": 122.2},
    ]

    caption_opts = {
        "style_preset": "Karaoke pop",
        "font_family": "Montserrat",
        "font_size_pct": 140,
        "bold": True,
        "uppercase": True,
        "base_color": "#FFFFFF",
        "highlight_color": "#FFE600",
        "outline_width": 6.0,
        "pos_x": 50.0,
        "pos_y": 75.0,
    }

    no_cap = render_exact_preview_frame(
        video_path=str(video_path),
        timestamp=120.9,
        aspect_ratio="9:16",
        frame_layout="fit_blur",
        burn_captions=False,
        caption_options=caption_opts,
        transcript_words=words,
        clip_start_time=120.0,
        clip_duration=3.0,
    )
    with_cap = render_exact_preview_frame(
        video_path=str(video_path),
        timestamp=120.9,
        aspect_ratio="9:16",
        frame_layout="fit_blur",
        burn_captions=True,
        caption_options=caption_opts,
        transcript_words=words,
        clip_start_time=120.0,
        clip_duration=3.0,
    )

    img_no_cap = _load_preview_image(storage_dir, no_cap["frame_url"])
    img_with_cap = _load_preview_image(storage_dir, with_cap["frame_url"])
    assert img_no_cap.shape == img_with_cap.shape

    # Subtitle band around y=55%..90% must differ clearly when burned in
    h = img_no_cap.shape[0]
    band_no = img_no_cap[int(h * 0.55):int(h * 0.90), :]
    band_yes = img_with_cap[int(h * 0.55):int(h * 0.90), :]
    mean_diff = float(np.mean(np.abs(band_yes.astype(np.float32) - band_no.astype(np.float32))))
    assert mean_diff > 5.0, f"Burned captions did not visibly alter preview subtitle band (diff={mean_diff:.2f})"

    # Also test full final MP4 renders (captioned vs uncaptioned) via Pillow + ffconcat under Unicode+space directory
    out_mp4_cap = storage_dir / "renders" / "rendered_unicode_cap.mp4"
    out_mp4_nocap = storage_dir / "renders" / "rendered_unicode_nocap.mp4"

    rendered_cap = render_clip_ultra(
        master_video_path=str(video_path),
        clip_id=99,
        start_time=120.0,
        duration=2.5,
        words=words,
        aspect_ratio="9:16",
        frame_layout="fit_blur",
        burn_captions=True,
        caption_style="Karaoke pop",
        font_size_percent=140,
        hardware_mode="cpu",
        show_hook_banner=False,
        output_path_override=str(out_mp4_cap),
    )
    rendered_nocap = render_clip_ultra(
        master_video_path=str(video_path),
        clip_id=100,
        start_time=120.0,
        duration=2.5,
        words=words,
        aspect_ratio="9:16",
        frame_layout="fit_blur",
        burn_captions=False,
        hardware_mode="cpu",
        show_hook_banner=False,
        output_path_override=str(out_mp4_nocap),
    )
    assert Path(rendered_cap).exists() and Path(rendered_cap).stat().st_size > 10_000
    assert Path(rendered_nocap).exists() and Path(rendered_nocap).stat().st_size > 10_000

    # Read frames at t=0.9s inside both MP4s and verify subtitle band differs cleanly
    cap_yes = cv2.VideoCapture(rendered_cap)
    cap_yes.set(cv2.CAP_PROP_POS_MSEC, 900.0)
    ok_y, frame_yes = cap_yes.read()
    cap_yes.release()

    cap_no = cv2.VideoCapture(rendered_nocap)
    cap_no.set(cv2.CAP_PROP_POS_MSEC, 900.0)
    ok_n, frame_no = cap_no.read()
    cap_no.release()

    assert ok_y and frame_yes is not None
    assert ok_n and frame_no is not None
    assert frame_yes.shape == (1920, 1080, 3)

    fh = frame_yes.shape[0]
    mp4_diff = float(np.mean(np.abs(
        frame_yes[int(fh * 0.55):int(fh * 0.90), :].astype(np.float32)
        - frame_no[int(fh * 0.55):int(fh * 0.90), :].astype(np.float32)
    )))
    assert mp4_diff > 5.0, f"MP4 ffconcat captions did not visibly alter subtitle band (diff={mp4_diff:.2f})"


def test_pillow_caption_chunking_gap_bridge_and_ffconcat(tmp_path: Path):
    words = [
        {"word": "micro", "start": 120.10, "end": 120.30},
        {"word": "gap", "start": 120.40, "end": 120.60},  # 0.10s gap < 0.15s should bridge cleanly
        {"word": "muted", "start": 120.65, "end": 120.80, "muted": True},
        {"word": "next", "start": 120.85, "end": 121.20},
    ]
    chunks = normalize_and_chunk_words(words, clip_start_time=120.0, clip_duration=2.5)
    assert len(chunks) >= 1
    flat_words = [w for ch in chunks for w in ch["words"]]
    assert [w["word"] for w in flat_words] == ["micro", "gap", "next"]
    assert flat_words[0]["end"] == flat_words[1]["start"]

    rgba = render_caption_frame(
        t=0.5,
        width=540,
        height=960,
        words=words,
        clip_start_time=120.0,
        clip_duration=2.5,
        highlight_shape="filled_box",
        glow=2.0,
        hook_banner_enabled=True,
        hook_headline="TEST HOOK BANNER",
    )
    assert rgba.mode == "RGBA" and rgba.size == (540, 960)
    assert rgba.getbbox() is not None

    temp_dir, ffconcat_path, count = build_caption_ffconcat(
        words=words,
        clip_duration=2.5,
        width=540,
        height=960,
        clip_start_time=120.0,
    )
    try:
        assert count >= 2
        lines = ffconcat_path.read_text(encoding="utf-8").strip().splitlines()
        assert lines[0] == "ffconcat version 1.0"
        # Final file line MUST be repeated for FFmpeg concat demuxer
        assert lines[-1].startswith("file '")
        assert (temp_dir / "blank.png").exists()
    finally:
        import shutil
        shutil.rmtree(temp_dir, ignore_errors=True)


def test_all_five_framing_templates_and_zoom_pan_diff(unicode_storage_and_video):
    storage_dir, video_path = unicode_storage_and_video

    templates = [
        ("auto_track", "top_left"),
        ("fit_blur", "top_left"),
        ("webcam_gameplay_split", "top_left"),
        ("gameplay_webcam_pip", "bottom_right"),
        ("split_podcast", "top_left"),
    ]
    rendered_imgs: Dict[str, np.ndarray] = {}

    for tpl, corner in templates:
        res = render_exact_preview_frame(
            video_path=str(video_path),
            timestamp=120.5,
            aspect_ratio="9:16",
            frame_layout=tpl,
            webcam_corner=corner,
            zoom=1.0,
            pan_x=0,
            burn_captions=False,
        )
        img = _load_preview_image(storage_dir, res["frame_url"])
        rendered_imgs[tpl] = img

    # Every template pair must yield distinct pixel output
    tpl_names = [t[0] for t in templates]
    for i in range(len(tpl_names)):
        for j in range(i + 1, len(tpl_names)):
            a = rendered_imgs[tpl_names[i]].astype(np.float32)
            b = rendered_imgs[tpl_names[j]].astype(np.float32)
            assert a.shape == b.shape
            diff = float(np.mean(np.abs(a - b)))
            assert diff > 3.0, f"Templates {tpl_names[i]} and {tpl_names[j]} produced near-identical images (diff={diff:.2f})"

    # Verify zoom=1.5 + pan_x=40 significantly changes auto_track output
    base = rendered_imgs["auto_track"].astype(np.float32)
    zoomed_panned_res = render_exact_preview_frame(
        video_path=str(video_path),
        timestamp=120.5,
        aspect_ratio="9:16",
        frame_layout="auto_track",
        zoom=1.5,
        pan_x=40,
        burn_captions=False,
    )
    zoomed_panned = _load_preview_image(storage_dir, zoomed_panned_res["frame_url"]).astype(np.float32)
    zp_diff = float(np.mean(np.abs(base - zoomed_panned)))
    assert zp_diff > 8.0, f"Zoom=1.5 & Pan=40 had negligible effect on preview (diff={zp_diff:.2f})"


def test_face_tracker_smoothing_clamping_and_caching(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    unicode_dir = tmp_path / "ドキュメント with spaces"
    video_path = _create_synthetic_video(unicode_dir / "track_source.mp4", duration_sec=2.5, width=640, height=360)
    cache_dir = unicode_dir / "track_cache"

    # Sanity-check offline detector works cleanly on a blank frame without crashing
    blank = np.zeros((360, 640, 3), dtype=np.uint8)
    assert face_tracker.detect_faces_offline(blank) == []

    # Verify clamping stays strictly within bounds even on extreme edge coordinates
    for cx, cy, pan in [(0.0, 0.0, -1.0), (640.0, 360.0, 1.0), (320.0, 180.0, 0.0)]:
        crop_w, crop_h = 202, 360
        x, y = face_tracker.compute_clamped_crop_coords(cx, cy, crop_w, crop_h, 640, 360, pan_norm=pan)
        assert 0 <= x <= 640 - crop_w
        assert 0 <= y <= 360 - crop_h

    # Simulate a smooth left -> right moving subject across sampled frames
    call_idx = {"n": 0}

    def fake_detect(frame_bgr: np.ndarray):
        idx = call_idx["n"]
        call_idx["n"] += 1
        cx = min(560, 120 + idx * 32)
        return [{"cx": cx / 640.0, "cy": 140.0 / 360.0, "w": 70.0 / 640.0, "h": 80.0 / 360.0}]

    monkeypatch.setattr(face_tracker, "detect_faces_offline", fake_detect)

    track = face_tracker.track_faces_in_clip(
        video_path=str(video_path),
        start_sec=0.0,
        end_sec=2.4,
        mode="single",
        cache_dir=str(cache_dir),
        sample_fps=6.0,
    )

    keyframes = track["keyframes"]
    assert len(keyframes) >= 6
    assert (cache_dir / "face_track.json").exists()
    # Normalized center x should monotonically advance rightwards cleanly
    assert keyframes[-1]["cx"] > keyframes[0]["cx"] + 0.15
    assert all(0.0 <= kf["cx"] <= 1.0 and 0.0 <= kf["cy"] <= 1.0 for kf in keyframes)

    pts = [
        (float(kf["t"]), face_tracker.compute_clamped_crop_coords(kf["cx"], kf["cy"], 202, 360, 640, 360)[0])
        for kf in keyframes
    ]
    expr = face_tracker.compile_piecewise_linear_expr(pts, min_val=0, max_val=640 - 202)
    assert "if(lt(t," in expr and expr.startswith("clip(")
