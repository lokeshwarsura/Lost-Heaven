"""Phase 2 tests: CFR A/V sync engine, two-stage frame seek, normalized crop box, and Start->End keyframe framing."""

import json
import subprocess
from pathlib import Path
from sqlmodel import Session, SQLModel, create_engine


def _make_color_grid_video(out_path: Path, dur: float = 4.0, fps: int = 24) -> Path:
    """Generate a synthetic 1280x720 16:9 test video with two horizontally distinct color halves + audio sine."""
    from app.services.proxy import get_ffmpeg_cmd

    ffmpeg = get_ffmpeg_cmd()
    # Left half bright red, right half bright blue -> crop X shifts pixel color cleanly
    vf = (
        "drawbox=x=0:y=0:w=640:h=720:color=red@1.0:t=fill,"
        "drawbox=x=640:y=0:w=640:h=720:color=blue@1.0:t=fill,"
        "drawbox=x=580:y=280:w=120:h=160:color=white@1.0:t=fill"
    )
    cmd = [
        ffmpeg,
        "-y",
        "-f", "lavfi",
        "-i", f"color=c=black:s=1280x720:r={fps}:d={dur}",
        "-f", "lavfi",
        "-i", f"sine=frequency=440:sample_rate=48000:duration={dur}",
        "-vf", vf,
        "-c:v", "libx264",
        "-pix_fmt", "yuv420p",
        "-c:a", "aac",
        str(out_path),
    ]
    res = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
    assert res.returncode == 0, f"Test source generation failed: {res.stderr[-400:]}"
    return out_path


def test_cfr_audio_sync_chain_and_render(tmp_path, monkeypatch):
    from app.config import settings
    from app.services.audio_mastering import build_voice_filter_chain
    from app.services.renderer import render_clip_ultra
    from app.services.proxy import get_ffprobe_cmd

    monkeypatch.setattr(settings, "STORAGE_DIR", str(tmp_path / "storage"))
    af = build_voice_filter_chain()
    assert "aresample=async=1:first_pts=0" in af

    src_video = _make_color_grid_video(tmp_path / "src_sync.mp4", dur=3.5, fps=24)
    out_mp4 = render_clip_ultra(
        master_video_path=str(src_video),
        clip_id=9201,
        start_time=0.2,
        duration=2.0,
        words=[],
        project_id=92,
        aspect_ratio="9:16",
        frame_layout="auto_track",
        burn_captions=False,
        hardware_mode="cpu",
        clip_size="540p",
        options={
            "export_fps": "30",
            "frame_crop": {"x": 0.0, "y": 0.0, "w": 0.4, "h": 1.0},
        },
        framing_override={
            "keyframe_start_crop": {"x": 0.0, "y": 0.05, "w": 0.35, "h": 0.90},
            "keyframe_end_crop": {"x": 0.65, "y": 0.05, "w": 0.35, "h": 0.90},
        },
        show_hook_banner=False,
    )
    assert Path(out_mp4).exists()

    probe = subprocess.run(
        [
            get_ffprobe_cmd(),
            "-v", "error",
            "-select_streams", "v:0",
            "-show_entries", "stream=r_frame_rate,avg_frame_rate",
            "-of", "json",
            out_mp4,
        ],
        capture_output=True,
        text=True,
        timeout=15,
    )
    assert probe.returncode == 0
    info = json.loads(probe.stdout)["streams"][0]
    assert info["r_frame_rate"] == "30/1"
    assert info["avg_frame_rate"] == "30/1"


def test_dynamic_scale_keyframe_crop_and_two_stage_preview(tmp_path, monkeypatch):
    from PIL import Image
    from app.config import settings
    from app.services.preview_frame import render_exact_preview_frame
    from app.services.renderer import render_clip_ultra

    monkeypatch.setattr(settings, "STORAGE_DIR", str(tmp_path / "storage_prv"))
    src_video = _make_color_grid_video(tmp_path / "src_kf.mp4", dur=4.0, fps=24)

    # Preview near t=2.4s (exercises two-stage -ss coarse -i src -ss fine)
    # Start KF on left red side (x=0.05, w=0.35), End KF on right blue side (x=0.60, w=0.35)
    kf_ov = {
        "keyframe_start_crop": {"x": 0.05, "y": 0.05, "w": 0.35, "h": 0.90},
        "keyframe_end_crop": {"x": 0.60, "y": 0.05, "w": 0.35, "h": 0.90},
    }
    res_start = render_exact_preview_frame(
        video_path=str(src_video),
        timestamp=0.2,
        aspect_ratio="9:16",
        frame_layout="auto_track",
        framing_override=kf_ov,
        clip_start_time=0.0,
        clip_duration=3.0,
        clip_size="540p",
        burn_captions=False,
    )
    res_end = render_exact_preview_frame(
        video_path=str(src_video),
        timestamp=2.9,
        aspect_ratio="9:16",
        frame_layout="auto_track",
        framing_override=kf_ov,
        clip_start_time=0.0,
        clip_duration=3.0,
        clip_size="540p",
        burn_captions=False,
    )
    prv_dir = tmp_path / "storage_prv" / "previews"
    img_start = Image.open(prv_dir / Path(res_start["frame_url"]).name).convert("RGB")
    img_end = Image.open(prv_dir / Path(res_end["frame_url"]).name).convert("RGB")
    ps = img_start.getpixel((img_start.width // 2, img_start.height // 2))
    pe = img_end.getpixel((img_end.width // 2, img_end.height // 2))
    assert ps[0] > ps[2] + 80, f"Expected red left-side crop at t=0.2s, got {ps}"
    assert pe[2] > pe[0] + 80, f"Expected blue right-side crop at t=2.9s, got {pe}"

    # Also test Start->End box zoom change (different width/height keyframes) renders cleanly
    zoom_mp4 = render_clip_ultra(
        master_video_path=str(src_video),
        clip_id=9202,
        start_time=0.0,
        duration=1.5,
        words=[],
        project_id=92,
        aspect_ratio="9:16",
        frame_layout="auto_track",
        burn_captions=False,
        hardware_mode="cpu",
        clip_size="540p",
        framing_override={
            "keyframe_start_crop": {"x": 0.05, "y": 0.05, "w": 0.42, "h": 0.90},
            "keyframe_end_crop": {"x": 0.62, "y": 0.15, "w": 0.28, "h": 0.70},
        },
        show_hook_banner=False,
    )
    assert Path(zoom_mp4).exists() and Path(zoom_mp4).stat().st_size > 1024


def test_framing_override_endpoint_persists_normalized_crop_and_keyframes(tmp_path):
    import app.main as main_mod
    from app.models import Project, ViralClip, FramingOverrideRequest

    db_path = tmp_path / "framing_v14.db"
    test_engine = create_engine(f"sqlite:///{db_path}", connect_args={"check_same_thread": False})
    SQLModel.metadata.create_all(test_engine)

    with Session(test_engine) as session:
        proj = Project(id=901, title="Crop Test", source_path="", status="ready")
        session.add(proj)
        clip = ViralClip(id=911, project_id=901, start_time=0.0, end_time=10.0, duration=10.0)
        session.add(clip)
        session.commit()

        res = main_mod.update_clip_framing_override_endpoint(
            project_id=901,
            clip_id=911,
            req=FramingOverrideRequest(
                frame_crop_x=0.22,
                frame_crop_y=0.06,
                frame_crop_w=0.36,
                frame_crop_h=0.88,
                keyframe_start_crop={"x": 0.10, "y": 0.05, "w": 0.35, "h": 0.90},
                keyframe_end_crop={"x": 0.55, "y": 0.05, "w": 0.35, "h": 0.90},
            ),
            session=session,
        )
        ov = res["framing_override"]
        assert ov["frame_crop"]["x"] == 0.22
        assert ov["frame_crop"]["w"] == 0.36
        assert ov["keyframe_start_crop"]["x"] == 0.10
        assert ov["keyframe_end_crop"]["x"] == 0.55

        reset_res = main_mod.update_clip_framing_override_endpoint(
            project_id=901,
            clip_id=911,
            req=FramingOverrideRequest(reset=True),
            session=session,
        )
        assert reset_res["framing_override"] is None
