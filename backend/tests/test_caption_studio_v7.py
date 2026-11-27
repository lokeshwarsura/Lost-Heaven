import os
import shutil
import subprocess
from pathlib import Path

import numpy as np
import pytest
from PIL import Image
from sqlmodel import SQLModel, create_engine, Session

import app.main as main_mod
import app.database as db_mod
from app.config import settings
from app.models import Project, ViralClip, UpdateClipRequest, PreviewFrameRequest
from app.services.caption_renderer import (
    render_caption_frame,
    normalize_and_chunk_words,
    resolve_pillow_font,
    build_caption_ffconcat,
)
from app.services.renderer import render_clip_ultra
from app.services.proxy import get_ffmpeg_cmd, probe_video


def _setup_isolated_db(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    storage_dir = tmp_path / "storage"
    for sub in ("masters", "proxies", "renders", "previews", "captions", "projects", "fonts"):
        (storage_dir / sub).mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr(settings, "STORAGE_DIR", str(storage_dir))

    db_path = tmp_path / "caption_v7_test.db"
    test_engine = create_engine(f"sqlite:///{db_path}", echo=False, connect_args={"check_same_thread": False})
    SQLModel.metadata.create_all(test_engine)
    monkeypatch.setattr(db_mod, "engine", test_engine)
    return test_engine, storage_dir


def test_caption_physics_highlights_text_case_and_pacing_density(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    _setup_isolated_db(tmp_path, monkeypatch)
    words = [
        {"word": "hello", "start": 0.1, "end": 0.45},
        {"word": "world", "start": 0.46, "end": 0.85},
        {"word": "supercalifragilistic", "start": 0.86, "end": 1.35},
        {"word": "awesome", "start": 1.36, "end": 1.75},
        {"word": "creator", "start": 1.76, "end": 2.20},
    ]

    # 1. Pacing density: Rapid (<=2 words) vs Full Phrase
    chunks_rapid = normalize_and_chunk_words(words, clip_start_time=0.0, clip_duration=3.0, pacing_density="1-2 Words Rapid")
    chunks_full = normalize_and_chunk_words(words, clip_start_time=0.0, clip_duration=3.0, pacing_density="Full Phrase")
    assert all(len(ch["words"]) <= 2 for ch in chunks_rapid)
    assert len(chunks_full) < len(chunks_rapid)

    # 2. Pop Bounce entry curve differs between t=0.14s (early spring) and t=0.45s (settled)
    img_early = render_caption_frame(
        t=0.13, width=1080, height=1920, words=words, clip_duration=3.0,
        caption_animation="Pop Bounce", highlight_shape="Rounded Pill Box",
        pill_color="#FF2288", text_case="ALL CAPS", font_size_pct=155,
    )
    img_settled = render_caption_frame(
        t=0.42, width=1080, height=1920, words=words, clip_duration=3.0,
        caption_animation="Pop Bounce", highlight_shape="Rounded Pill Box",
        pill_color="#FF2288", text_case="ALL CAPS", font_size_pct=155,
    )
    arr_early = np.array(img_early)
    arr_settled = np.array(img_settled)
    assert np.sum(arr_early[:, :, 3] > 20) > 100
    assert float(np.mean(np.abs(arr_early.astype(float) - arr_settled.astype(float)))) > 0.2

    # 3. Rounded Pill Box uses custom pill_color (#FF2288)
    magenta_px = arr_settled[
        (arr_settled[:, :, 0] > 200) & (arr_settled[:, :, 1] < 80) & (arr_settled[:, :, 2] > 100) & (arr_settled[:, :, 3] > 200)
    ]
    assert len(magenta_px) > 50, "Expected Rounded Pill Box backdrop in custom pill_color #FF2288"

    # 4. 1.18x Kinetic Pop + scale=155% on long word stays strictly inside horizontal margins (no right-edge overflow)
    img_long = render_caption_frame(
        t=1.05, width=1080, height=1920, words=words, clip_duration=3.0,
        highlight_shape="1.18x Kinetic Pop", font_size_pct=155, text_case="ALL CAPS",
        caption_animation="Pop Bounce",
    )
    alpha_mask = np.array(img_long)[:, :, 3] > 10
    ys, xs = np.where(alpha_mask)
    assert len(xs) > 100
    assert int(xs.min()) >= int(1080 * 0.04)
    assert int(xs.max()) <= int(1080 * 0.96), f"Caption overflowed right edge: x_max={xs.max()}"

    # 5. Title Case vs As Spoken leaves original words unmutated and renders visibly distinct glyphs
    orig_copy = [dict(w) for w in words]
    img_title = render_caption_frame(
        t=0.25, width=1080, height=1920, words=words, clip_duration=3.0,
        text_case="Title Case", caption_animation="Instant Cut",
    )
    img_spoken = render_caption_frame(
        t=0.25, width=1080, height=1920, words=words, clip_duration=3.0,
        text_case="As Spoken", caption_animation="Instant Cut",
    )
    assert words == orig_copy
    assert float(np.mean(np.abs(np.array(img_title).astype(float) - np.array(img_spoken).astype(float)))) > 0.1

    # 6. Blur Fade & Slide Up & Typewriter Reveal render cleanly and build ffconcat
    tdir, ff_path, frame_cnt = build_caption_ffconcat(
        words=words, clip_duration=3.0, width=720, height=1280,
        caption_animation="Blur Fade", highlight_shape="Neon Outer Glow",
        pacing_density="1-2 Words Rapid", text_case="Title Case",
    )
    try:
        assert ff_path.exists() and frame_cnt >= 3
    finally:
        shutil.rmtree(tdir, ignore_errors=True)


def test_custom_ttf_font_upload_and_export(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    test_engine, storage_dir = _setup_isolated_db(tmp_path, monkeypatch)

    # Locate an actual system TTF font to simulate uploading under a custom font name
    win_arial = Path(r"C:\Windows\Fonts\arialbd.ttf")
    if not win_arial.exists():
        pytest.skip("arialbd.ttf not available on test host")

    custom_ttf = tmp_path / "LostHeavenDisplayTest.ttf"
    shutil.copy2(win_arial, custom_ttf)

    # Install directly into storage/fonts (matching /api/system/fonts handler) and register
    dest_font = storage_dir / "fonts" / custom_ttf.name
    shutil.copy2(custom_ttf, dest_font)
    fonts_listed = main_mod.list_fonts()
    assert "LostHeavenDisplayTest" in fonts_listed

    loaded = resolve_pillow_font("LostHeavenDisplayTest", size_px=64, bold=True)
    assert str(dest_font).lower() in str(getattr(loaded, "path", "")).lower()

    # Create tiny 2.0s MP4 and verify full render + persistence with Phase 2 options
    ffmpeg = get_ffmpeg_cmd()
    src_mp4 = tmp_path / "cap_src.mp4"
    res_v = subprocess.run(
        [
            ffmpeg, "-y",
            "-f", "lavfi", "-i", "color=c=#182436:size=640x360:rate=24:duration=2.0",
            "-f", "lavfi", "-i", "aevalsrc='0.3*sin(2*PI*300*t)':s=44100:d=2.0",
            "-c:v", "libx264", "-preset", "ultrafast", "-pix_fmt", "yuv420p",
            "-c:a", "aac", str(src_mp4),
        ],
        capture_output=True, text=True, timeout=30,
    )
    assert res_v.returncode == 0

    words = [{"word": "Custom", "start": 0.2, "end": 0.8}, {"word": "Font", "start": 0.9, "end": 1.6}]
    with Session(test_engine) as session:
        proj = Project(title="CapV7", source_type="upload", source_path=str(src_mp4), duration=2.0, status="finished")
        session.add(proj)
        session.commit()
        session.refresh(proj)
        clip = ViralClip(project_id=proj.id, start_time=0.0, end_time=2.0, duration=2.0)
        session.add(clip)
        session.commit()
        session.refresh(clip)

        main_mod.update_clip(
            clip.id,
            UpdateClipRequest(
                caption_font="LostHeavenDisplayTest",
                caption_animation="Scale Slam",
                highlight_shape="Rounded Pill Box",
                pill_color="#3B82F6",
                pacing_density="1-2 Words Rapid",
                text_case="Title Case",
                font_size_percent=125,
                position_y=76.0,
                outline_width=5.0,
                shadow_depth=6.0,
                transcript_words=words,
            ),
            session=session,
        )
        session.refresh(clip)
        snap = main_mod._snapshot_clip(clip)
        assert snap["options"]["pill_color"] == "#3B82F6"
        assert snap["options"]["pacing_density"] == "1-2 Words Rapid"
        assert snap["options"]["text_case"] == "Title Case"

        out_mp4 = main_mod._render_clip_from_snapshot(snap, str(src_mp4), quality_mode="fast_preview")
        assert os.path.exists(out_mp4)
        assert probe_video(out_mp4)["duration"] >= 1.5
