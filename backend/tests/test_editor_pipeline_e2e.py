import json
import subprocess
from pathlib import Path
from typing import Dict, List

import cv2
import numpy as np
import pytest
from sqlmodel import Session, SQLModel, create_engine

from app.config import settings
from app.main import update_clip_metadata
from app.models import Project, UpdateClipMetadataRequest, ViralClip
from app.services import face_tracker
from app.services.preview_frame import render_exact_preview_frame
from app.services.proxy import get_ffmpeg_cmd, probe_video
from app.services.renderer import render_clip_ultra, scale_dimensions_for_clip_size


def _create_color_video(
    out_path: Path,
    duration_sec: float = 3.0,
    width: int = 640,
    height: int = 360,
) -> Path:
    """Create a real H.264 + AAC testsrc2 color video with sine tone audio."""
    out_path.parent.mkdir(parents=True, exist_ok=True)
    cmd = [
        get_ffmpeg_cmd(),
        "-y",
        "-f", "lavfi",
        "-i", f"testsrc2=size={width}x{height}:rate=15:duration={duration_sec}",
        "-f", "lavfi",
        "-i", f"sine=frequency=260:sample_rate=44100:duration={duration_sec}",
        "-af", "volume=0.08",
        "-c:v", "libx264",
        "-preset", "ultrafast",
        "-pix_fmt", "yuv420p",
        "-c:a", "aac",
        "-shortest",
        str(out_path),
    ]
    res = subprocess.run(cmd, capture_output=True, text=True, timeout=60)
    assert res.returncode == 0, f"Synthetic video creation failed: {res.stderr}"
    return out_path


def _read_img_unicode(path: Path) -> np.ndarray:
    data = np.fromfile(str(path), dtype=np.uint8)
    img = cv2.imdecode(data, cv2.IMREAD_COLOR)
    assert img is not None, f"Failed to decode image at {path}"
    return img


@pytest.fixture()
def unicode_e2e_env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    unicode_root = tmp_path / "ドキュメント with spaces"
    storage_dir = unicode_root / "storage_e2e"
    storage_dir.mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr(settings, "STORAGE_DIR", str(storage_dir))

    short_vid = _create_color_video(unicode_root / "color clip source.mp4", duration_sec=3.0)
    return storage_dir, unicode_root, short_vid


def test_step3_grading_resolution_and_captions_stay_colored(unicode_e2e_env):
    storage_dir, _, video_path = unicode_e2e_env

    # Verify export resolution mapping for 16:9 and 9:16
    assert scale_dimensions_for_clip_size(1920, 1080, "480p") == (854, 480)
    assert scale_dimensions_for_clip_size(1080, 1920, "480p") == (480, 854)
    assert scale_dimensions_for_clip_size(1080, 1920, "720p") == (720, 1280)

    words: List[Dict] = [
        {"word": "COLORED", "start": 0.2, "end": 1.2},
        {"word": "CAPTION", "start": 1.2, "end": 2.4},
    ]

    ungraded_mp4 = storage_dir / "renders" / "e2e_ungraded_480p.mp4"
    bw_dark_mp4 = storage_dir / "renders" / "e2e_bw_dark_480p.mp4"

    render_clip_ultra(
        master_video_path=str(video_path),
        clip_id=201,
        start_time=0.0,
        duration=2.5,
        words=words,
        aspect_ratio="16:9",
        export_resolution="480p",
        frame_layout="auto_track",
        burn_captions=False,
        brightness=0.0,
        black_and_white=0.0,
        hardware_mode="cpu",
        show_hook_banner=False,
        output_path_override=str(ungraded_mp4),
    )

    render_clip_ultra(
        master_video_path=str(video_path),
        clip_id=202,
        start_time=0.0,
        duration=2.5,
        words=words,
        aspect_ratio="16:9",
        export_resolution="480p",
        frame_layout="auto_track",
        burn_captions=True,
        caption_style="Hormozi",
        font_size_percent=140,
        base_color="#FFFF00",
        active_color="#FF2200",
        position_y=75.0,
        brightness=-32.0,
        black_and_white=100.0,
        hardware_mode="cpu",
        show_hook_banner=False,
        output_path_override=str(bw_dark_mp4),
    )

    meta = probe_video(str(bw_dark_mp4))
    assert meta["height"] == 480
    assert meta["width"] == 854

    cap_u = cv2.VideoCapture(str(ungraded_mp4))
    cap_u.set(cv2.CAP_PROP_POS_MSEC, 700.0)
    ok_u, frame_u = cap_u.read()
    cap_u.release()

    cap_g = cv2.VideoCapture(str(bw_dark_mp4))
    cap_g.set(cv2.CAP_PROP_POS_MSEC, 700.0)
    ok_g, frame_g = cap_g.read()
    cap_g.release()

    assert ok_u and ok_g and frame_u is not None and frame_g is not None

    # Top half footage must be grayscale (R ~= G ~= B) AND darker than ungraded
    h = frame_g.shape[0]
    top_u = frame_u[:int(h * 0.40), :].astype(np.float32)
    top_g = frame_g[:int(h * 0.40), :].astype(np.float32)

    assert np.mean(top_g) < np.mean(top_u) - 10.0, "brightness=-32 did not darken footage"
    chroma_spread_top = float(np.mean(np.max(top_g, axis=2) - np.min(top_g, axis=2)))
    assert chroma_spread_top < 6.0, f"black_and_white=100 footage is not monochrome (spread={chroma_spread_top:.2f})"

    # Subtitle band must STILL have strongly saturated yellow/red caption pixels (grading before captions!)
    sub_band = frame_g[int(h * 0.58):int(h * 0.92), :].astype(np.float32)
    max_chroma_in_captions = float(np.max(np.max(sub_band, axis=2) - np.min(sub_band, axis=2)))
    assert max_chroma_in_captions > 60.0, "Colored captions lost color when footage B&W=100"


def test_presets_warm_vs_cool_and_effects_audio_export(unicode_e2e_env):
    storage_dir, unicode_root, video_path = unicode_e2e_env

    # Create a mid-tone neutral video so colorbalance warm vs cool shifts R-B cleanly without primary clipping
    mid_vid = unicode_root / "midtone neutral.mp4"
    subprocess.run(
        [
            get_ffmpeg_cmd(),
            "-y",
            "-f", "lavfi",
            "-i", "color=c=0x888480:size=640x360:rate=15:duration=2.5",
            "-f", "lavfi",
            "-i", "sine=frequency=260:sample_rate=44100:duration=2.5",
            "-c:v", "libx264",
            "-preset", "ultrafast",
            "-pix_fmt", "yuv420p",
            "-c:a", "aac",
            "-shortest",
            str(mid_vid),
        ],
        check=True,
        capture_output=True,
    )

    warm_res = render_exact_preview_frame(
        video_path=str(mid_vid),
        timestamp=1.0,
        aspect_ratio="9:16",
        frame_layout="auto_track",
        color_grading={"preset": "warm_vintage"},
        burn_captions=False,
    )
    cool_res = render_exact_preview_frame(
        video_path=str(mid_vid),
        timestamp=1.0,
        aspect_ratio="9:16",
        frame_layout="auto_track",
        color_grading={"preset": "cool_tech"},
        burn_captions=False,
    )

    warm_img = _read_img_unicode(storage_dir / "previews" / Path(warm_res["frame_url"]).name).astype(np.float32)
    cool_img = _read_img_unicode(storage_dir / "previews" / Path(cool_res["frame_url"]).name).astype(np.float32)

    # OpenCV BGR channels: channel 2 = Red, channel 0 = Blue
    warm_rb_bias = float(np.mean(warm_img[:, :, 2] - warm_img[:, :, 0]))
    cool_rb_bias = float(np.mean(cool_img[:, :, 2] - cool_img[:, :, 0]))
    assert warm_rb_bias > cool_rb_bias + 12.0, (
        f"Expected Warm Vintage red-blue bias ({warm_rb_bias:.2f}) > Cool Tech ({cool_rb_bias:.2f})"
    )

    # Full MP4 render exercising glow, sharpness, vignette, bottom_fade, tint & vocal clarity audio filters
    fx_mp4 = storage_dir / "renders" / "e2e_fx_audio.mp4"
    out = render_clip_ultra(
        master_video_path=str(video_path),
        clip_id=203,
        start_time=0.0,
        duration=2.0,
        words=[],
        aspect_ratio="9:16",
        export_resolution="720p",
        frame_layout="fit_blur",
        burn_captions=False,
        tint_color="Blue",
        tint_strength=0.3,
        glow=0.5,
        sharpness=0.4,
        vignette=0.35,
        bottom_fade=0.3,
        audio_volume=1.35,
        normalize_audio=True,
        vocal_clarity=True,
        hardware_mode="cpu",
        output_path_override=str(fx_mp4),
    )
    assert Path(out).exists() and probe_video(out)["width"] == 720


def test_caption_absolute_start_time_120s_ffconcat_and_metadata(unicode_e2e_env, tmp_path: Path):
    storage_dir, unicode_root, _ = unicode_e2e_env
    long_vid = _create_color_video(unicode_root / "long master 124s.mp4", duration_sec=124.0)

    words = [
        {"word": "HELLO", "start": 120.2, "end": 120.8},
        {"word": "WORLD", "start": 120.8, "end": 121.9},
    ]

    cap_mp4 = storage_dir / "renders" / "e2e_120s_cap.mp4"
    nocap_mp4 = storage_dir / "renders" / "e2e_120s_nocap.mp4"

    render_clip_ultra(
        master_video_path=str(long_vid),
        clip_id=204,
        start_time=120.0,
        duration=2.5,
        words=words,
        aspect_ratio="9:16",
        export_resolution="540p",
        frame_layout="fit_blur",
        burn_captions=True,
        font_size_percent=140,
        hardware_mode="cpu",
        show_hook_banner=False,
        output_path_override=str(cap_mp4),
    )
    render_clip_ultra(
        master_video_path=str(long_vid),
        clip_id=205,
        start_time=120.0,
        duration=2.5,
        words=words,
        aspect_ratio="9:16",
        export_resolution="540p",
        frame_layout="fit_blur",
        burn_captions=False,
        hardware_mode="cpu",
        show_hook_banner=False,
        output_path_override=str(nocap_mp4),
    )

    c1 = cv2.VideoCapture(str(cap_mp4))
    c1.set(cv2.CAP_PROP_POS_MSEC, 950.0)
    ok1, f1 = c1.read()
    c1.release()

    c2 = cv2.VideoCapture(str(nocap_mp4))
    c2.set(cv2.CAP_PROP_POS_MSEC, 950.0)
    ok2, f2 = c2.read()
    c2.release()

    assert ok1 and ok2 and f1 is not None and f2 is not None
    h = f1.shape[0]
    diff = float(np.mean(np.abs(f1[int(h * 0.55):int(h * 0.90)].astype(np.float32) - f2[int(h * 0.55):int(h * 0.90)].astype(np.float32))))
    assert diff > 5.0

    # Verify YouTube Metadata persistence endpoint against real SQLite session
    engine = create_engine(f"sqlite:///{tmp_path / 'meta_test.db'}", connect_args={"check_same_thread": False})
    SQLModel.metadata.create_all(engine)
    with Session(engine) as session:
        proj = Project(title="Meta Test", source_path=str(long_vid))
        session.add(proj)
        session.commit()
        session.refresh(proj)

        clip = ViralClip(project_id=proj.id, start_time=120.0, end_time=122.5, duration=2.5)
        session.add(clip)
        session.commit()
        session.refresh(clip)

        res = update_clip_metadata(
            project_id=proj.id,
            clip_id=clip.id,
            req=UpdateClipMetadataRequest(
                selected_title="Epic Moment #Shorts",
                youtube_description="Insane comeback",
                hashtags="#Shorts #Gaming",
                pinned_comment="What did you think?",
                privacy_status="unlisted",
            ),
            session=session,
        )
        assert res["status"] == "saved"
        reloaded = session.get(ViralClip, clip.id)
        assert reloaded is not None
        assert reloaded.selected_title == "Epic Moment #Shorts"
        assert reloaded.hashtags == "#Shorts #Gaming"
        assert reloaded.privacy_status == "unlisted"


def test_five_framing_templates_and_face_tracker_e2e(unicode_e2e_env, monkeypatch: pytest.MonkeyPatch):
    storage_dir, _, video_path = unicode_e2e_env

    templates = [
        ("auto_track", "top_left"),
        ("fit_blur", "top_left"),
        ("webcam_gameplay_split", "top_left"),
        ("gameplay_webcam_pip", "bottom_right"),
        ("split_podcast", "top_left"),
    ]
    imgs = []
    for tpl, corner in templates:
        res = render_exact_preview_frame(
            video_path=str(video_path),
            timestamp=1.0,
            aspect_ratio="9:16",
            frame_layout=tpl,
            webcam_corner=corner,
            burn_captions=False,
        )
        imgs.append(_read_img_unicode(storage_dir / "previews" / Path(res["frame_url"]).name).astype(np.float32))

    for i in range(len(imgs)):
        for j in range(i + 1, len(imgs)):
            assert float(np.mean(np.abs(imgs[i] - imgs[j]))) > 3.0

    # Moving subject face tracking test
    idx_box = {"i": 0}

    def move_face(frame_bgr: np.ndarray):
        cx = min(560, 110 + idx_box["i"] * 30)
        idx_box["i"] += 1
        return [{"cx": cx / 640.0, "cy": 140.0 / 360.0, "w": 65.0 / 640.0, "h": 75.0 / 360.0}]

    monkeypatch.setattr(face_tracker, "detect_faces_offline", move_face)
    track = face_tracker.track_faces_in_clip(str(video_path), start_sec=0.0, end_sec=2.8, sample_fps=6.0)
    kfs = track["keyframes"]
    assert len(kfs) >= 8
    assert kfs[-1]["cx"] > kfs[0]["cx"] + 0.15
    assert all(0.0 <= kf["cx"] <= 1.0 and 0.0 <= kf["cy"] <= 1.0 for kf in kfs)
