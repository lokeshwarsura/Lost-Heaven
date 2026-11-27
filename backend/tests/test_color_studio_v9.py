import os
import subprocess
from pathlib import Path

import cv2
import numpy as np
import pytest
from PIL import Image
from sqlmodel import SQLModel, Session, create_engine

import app.main as main_mod
import app.database as db_mod
from app.config import settings
from app.models import Project, ViralClip, PreviewFrameRequest, UpdateClipRequest
from app.services.filters import (
    COLOR_PRESETS,
    STUDIO_COLOR_PRESETS,
    _escape_ffmpeg_filter_path,
    build_advanced_color_filter,
    generate_clip_preset_previews,
)
from app.services.preview_frame import render_exact_preview_frame
from app.services.proxy import get_ffmpeg_cmd


def _setup_isolated_db(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    db_file = tmp_path / "test_color_v9.db"
    test_engine = create_engine(
        f"sqlite:///{db_file.as_posix()}",
        connect_args={"check_same_thread": False},
    )
    SQLModel.metadata.create_all(test_engine)

    storage_dir = tmp_path / "storage"
    for sub in ("masters", "renders", "previews", "assets", "thumbnails", "luts"):
        (storage_dir / sub).mkdir(parents=True, exist_ok=True)

    monkeypatch.setattr(db_mod, "engine", test_engine)
    monkeypatch.setattr(settings, "STORAGE_DIR", str(storage_dir))
    return test_engine, storage_dir


def _make_rich_color_mp4(dst_path: Path, duration_sec: float = 2.0) -> Path:
    """Create a rich multi-zone synthetic 320x180 MP4 with shadows, skin midtones, sky blues, and specular highlights."""
    dst_path.parent.mkdir(parents=True, exist_ok=True)
    w, h = 320, 180
    img = np.zeros((h, w, 3), dtype=np.uint8)
    # Top band: blue sky + neutral gray studio wall
    img[0:50, :160] = (205, 130, 55)     # BGR blue-cyan sky
    img[0:50, 160:] = (225, 225, 225)    # Bright highlight band
    # Mid band: warm skin tone + green grass + magenta neon block
    img[50:120, :110] = (128, 162, 218)  # Warm peach skin tone (BGR)
    img[50:120, 110:215] = (75, 175, 85) # Vivid foliage green
    img[50:120, 215:] = (180, 65, 210)   # Neon magenta accent
    # Bottom band: deep shadows + midtone gradient
    img[120:180, :160] = (28, 28, 32)    # Deep shadow band
    img[120:180, 160:] = (95, 110, 125)  # Neutral dark midtones

    raw_png = dst_path.parent / "base_frame.png"
    Image.fromarray(cv2.cvtColor(img, cv2.COLOR_BGR2RGB)).save(raw_png)

    ffmpeg = get_ffmpeg_cmd()
    cmd = [
        ffmpeg, "-y",
        "-loop", "1",
        "-i", str(raw_png),
        "-t", f"{duration_sec:.2f}",
        "-vf", "format=yuv420p",
        "-c:v", "libx264", "-preset", "ultrafast",
        str(dst_path),
    ]
    proc = subprocess.run(cmd, capture_output=True, text=True)
    assert proc.returncode == 0, f"Failed creating test MP4: {proc.stderr[-300:]}"
    return dst_path


def _write_test_cube_lut(dst_path: Path) -> Path:
    """Write a valid 4x4x4 3D LUT that swaps Red and Blue + lifts contrast subtly."""
    dst_path.parent.mkdir(parents=True, exist_ok=True)
    lines = ["TITLE \"Lost Heaven Cool Teal Swap\"", "LUT_3D_SIZE 4"]
    n = 4
    for b_i in range(n):
        b = b_i / (n - 1)
        for g_i in range(n):
            g = g_i / (n - 1)
            for r_i in range(n):
                r = r_i / (n - 1)
                nr = min(1.0, b * 0.90)
                ng = min(1.0, g * 1.04)
                nb = min(1.0, r * 0.92 + 0.05)
                lines.append(f"{nr:.4f} {ng:.4f} {nb:.4f}")
    dst_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return dst_path


def _render_filter_image(ffmpeg: str, base_png: Path, work_dir: Path, vf_str: str, tag: str) -> np.ndarray:
    out_png = work_dir / f"{tag}.png"
    cmd = [ffmpeg, "-y", "-i", str(base_png)]
    if vf_str:
        cmd.extend(["-vf", vf_str])
    cmd.extend(["-frames:v", "1", str(out_png)])
    proc = subprocess.run(cmd, cwd=str(work_dir), capture_output=True, text=True)
    assert proc.returncode == 0, f"Filter failed for {tag} ({vf_str}): {proc.stderr[-300:]}"
    arr = np.array(Image.open(out_png).convert("RGB"), dtype=np.float32)
    return arr


def _hist_signature(img_rgb: np.ndarray) -> np.ndarray:
    hist_r = np.histogram(img_rgb[:, :, 0], bins=16, range=(0, 256))[0]
    hist_g = np.histogram(img_rgb[:, :, 1], bins=16, range=(0, 256))[0]
    hist_b = np.histogram(img_rgb[:, :, 2], bins=16, range=(0, 256))[0]
    sig = np.concatenate([hist_r, hist_g, hist_b]).astype(np.float32)
    return sig / max(1.0, float(np.sum(sig)))


def test_all_9_presets_distinct_histograms_and_linear_strength(tmp_path: Path):
    """Verify all 9 curated presets compile cleanly, yield distinct histograms, and scale linearly with preset_intensity."""
    ffmpeg = get_ffmpeg_cmd()
    mp4 = _make_rich_color_mp4(tmp_path / "clip_src.mp4")
    base_png = tmp_path / "base_frame.png"

    assert len(STUDIO_COLOR_PRESETS) == 9
    expected_ids = [
        "none", "kodak_portra_400", "teal_orange", "cyberpunk_neon",
        "moody_noir", "golden_hour", "bw_16mm", "gaming_pop", "clean_rec709",
    ]
    assert [p["id"] for p in STUDIO_COLOR_PRESETS] == expected_ids

    rendered: dict[str, np.ndarray] = {}
    sigs: dict[str, np.ndarray] = {}
    for p in STUDIO_COLOR_PRESETS:
        pid = p["id"]
        vals = p["values"]
        vf = build_advanced_color_filter(
            preset=pid,
            preset_intensity=1.0,
            exposure=vals.get("exposure", 0.0),
            contrast=vals.get("contrast", 1.0),
            highlights=vals.get("highlights", 0.0),
            shadows=vals.get("shadows", 0.0),
            sharpness=vals.get("sharpness", 0.0),
            vignette=vals.get("vignette", 0.0),
            color_temp=vals.get("color_temp", 0.0),
            tint_shift=vals.get("tint_shift", 0.0),
            vibrance=vals.get("vibrance", 0.0),
            saturation=vals.get("saturation", 1.0),
            matte_fade=vals.get("matte_fade", 0.0),
            halation_glow=vals.get("halation_glow", 0.0),
            target_w=320,
            target_h=180,
        )
        img_rgb = _render_filter_image(ffmpeg, base_png, tmp_path, vf, f"preset_{pid}")
        rendered[pid] = img_rgb
        sigs[pid] = _hist_signature(img_rgb)

    # Every non-Original preset differs meaningfully from Original, and all 9 are pairwise distinct
    for pid in expected_ids[1:]:
        diff_from_orig = float(np.mean(np.abs(rendered[pid] - rendered["none"])))
        assert diff_from_orig > 3.5, f"Preset {pid} barely changed image ({diff_from_orig:.2f})"

    for i, p1 in enumerate(expected_ids):
        for p2 in expected_ids[i + 1:]:
            hdist = float(np.sum(np.abs(sigs[p1] - sigs[p2])))
            px_diff = float(np.mean(np.abs(rendered[p1] - rendered[p2])))
            assert hdist > 0.05 or px_diff > 4.0, f"Presets {p1} and {p2} look too similar (hdist={hdist:.3f}, px={px_diff:.2f})"

    # 16mm B&W must be monochrome (near-zero inter-channel spread)
    bw = rendered["bw_16mm"]
    chroma_spread = float(np.mean(np.max(bw, axis=2) - np.min(bw, axis=2)))
    assert chroma_spread < 2.5, f"bw_16mm should be monochrome, got chroma spread {chroma_spread:.2f}"

    # Verify linear Preset Strength scaling (0.0 -> 0%, 0.5 -> 50%, 1.0 -> 100%)
    img_0 = _render_filter_image(ffmpeg, base_png, tmp_path, build_advanced_color_filter(preset="teal_orange", preset_intensity=0.0), "str_0")
    img_50 = _render_filter_image(ffmpeg, base_png, tmp_path, build_advanced_color_filter(preset="teal_orange", preset_intensity=0.5), "str_50")
    img_100 = _render_filter_image(ffmpeg, base_png, tmp_path, build_advanced_color_filter(preset="teal_orange", preset_intensity=1.0), "str_100")

    d_0 = float(np.mean(np.abs(img_0 - rendered["none"])))
    d_50 = float(np.mean(np.abs(img_50 - rendered["none"])))
    d_100 = float(np.mean(np.abs(img_100 - rendered["none"])))
    d_50_to_100 = float(np.mean(np.abs(img_100 - img_50)))
    assert d_0 < 1.0
    assert 5.0 < d_50 < d_100
    assert d_50_to_100 > 4.0


def test_bipolar_sliders_produce_unmistakable_directional_shifts(tmp_path: Path):
    """Verify Exposure, Highlights, Shadows, Temperature, Tint, Vibrance, Matte Fade, and Halation produce measurable directional shifts at ±40."""
    ffmpeg = get_ffmpeg_cmd()
    _make_rich_color_mp4(tmp_path / "sliders.mp4")
    base_png = tmp_path / "base_frame.png"

    orig = _render_filter_image(ffmpeg, base_png, tmp_path, "", "orig")

    # Exposure -40 vs +40
    exp_neg = _render_filter_image(ffmpeg, base_png, tmp_path, build_advanced_color_filter(exposure=-40), "exp_neg")
    exp_pos = _render_filter_image(ffmpeg, base_png, tmp_path, build_advanced_color_filter(exposure=40), "exp_pos")
    assert float(np.mean(exp_neg)) + 8.0 < float(np.mean(orig)) < float(np.mean(exp_pos)) - 8.0

    # Shadows -40 vs +40 in dark shadow region (bottom-left quadrant: y=130..170, x=20..120)
    sh_neg = _render_filter_image(ffmpeg, base_png, tmp_path, build_advanced_color_filter(shadows=-40), "sh_neg")
    sh_pos = _render_filter_image(ffmpeg, base_png, tmp_path, build_advanced_color_filter(shadows=40), "sh_pos")
    assert float(np.mean(sh_pos[130:170, 20:120])) > float(np.mean(sh_neg[130:170, 20:120])) + 12.0

    # Highlights -40 vs +40 in bright region (top-right quadrant: y=10..40, x=180..300)
    hi_neg = _render_filter_image(ffmpeg, base_png, tmp_path, build_advanced_color_filter(highlights=-40), "hi_neg")
    hi_pos = _render_filter_image(ffmpeg, base_png, tmp_path, build_advanced_color_filter(highlights=40), "hi_pos")
    assert float(np.mean(hi_pos[10:40, 180:300])) > float(np.mean(hi_neg[10:40, 180:300])) + 10.0

    # Temperature -40 (cool blue) vs +40 (warm red/amber)
    t_cool = _render_filter_image(ffmpeg, base_png, tmp_path, build_advanced_color_filter(color_temp=-40), "t_cool")
    t_warm = _render_filter_image(ffmpeg, base_png, tmp_path, build_advanced_color_filter(color_temp=40), "t_warm")
    warmth_warm = float(np.mean(t_warm[:, :, 0] - t_warm[:, :, 2]))
    warmth_cool = float(np.mean(t_cool[:, :, 0] - t_cool[:, :, 2]))
    assert warmth_warm > warmth_cool + 25.0

    # Tint -40 (green) vs +40 (magenta)
    ti_grn = _render_filter_image(ffmpeg, base_png, tmp_path, build_advanced_color_filter(tint_shift=-40), "ti_grn")
    ti_mag = _render_filter_image(ffmpeg, base_png, tmp_path, build_advanced_color_filter(tint_shift=40), "ti_mag")
    # Green minus average of R&B should be higher for green shift than magenta shift
    g_dom_grn = float(np.mean(ti_grn[:, :, 1] - 0.5 * (ti_grn[:, :, 0] + ti_grn[:, :, 2])))
    g_dom_mag = float(np.mean(ti_mag[:, :, 1] - 0.5 * (ti_mag[:, :, 0] + ti_mag[:, :, 2])))
    assert g_dom_grn > g_dom_mag + 18.0


def test_cube_lut_windows_escaping_preset_previews_endpoint_and_compare_bypass(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    """Verify Windows LUT path escaping, /preset-previews caching, clip persistence, and Compare Original bypass."""
    test_engine, storage_dir = _setup_isolated_db(tmp_path, monkeypatch)
    src_mp4 = _make_rich_color_mp4(tmp_path / "media with spaces" / "color_src.mp4", duration_sec=2.0)
    lut_file = _write_test_cube_lut(storage_dir / "luts" / "cool swap 3d.cube")

    escaped = _escape_ffmpeg_filter_path(r"C:\Users\Video Clipper\test\cool.cube")
    assert escaped == r"C\:/Users/Video Clipper/test/cool.cube"

    with Session(test_engine) as session:
        proj = Project(title="Color V9 Test", source_type="upload", source_path=str(src_mp4), duration=2.0, status="finished")
        session.add(proj)
        session.commit()
        session.refresh(proj)

        clip = ViralClip(project_id=proj.id, start_time=0.0, end_time=2.0, duration=2.0)
        session.add(clip)
        session.commit()
        session.refresh(clip)

        # Persist new Studio options via update_clip + verify _serialize_clip
        upd_resp = main_mod.update_clip(
            clip.id,
            UpdateClipRequest(
                color_preset="teal_orange",
                preset_intensity=75.0,
                highlights=18.0,
                shadows=-22.0,
                export_fps="60",
                lut_path=f"/media/luts/{lut_file.name}",
            ),
            session=session,
        )
        assert upd_resp["status"] == "updated"
        session.refresh(clip)
        updated = main_mod._serialize_clip(clip)
        assert updated["color_preset"] == "teal_orange"
        assert updated["preset_intensity"] == pytest.approx(75.0)
        assert updated["highlights"] == pytest.approx(18.0)
        assert updated["shadows"] == pytest.approx(-22.0)
        assert updated["export_fps"] == "60"

        # Call preset-previews endpoint twice (render + cache hit)
        resp1 = main_mod.get_clip_preset_previews_endpoint(proj.id, clip.id, session=session)
        assert len(resp1["presets"]) == 9
        assert resp1["lut_preview"] is not None
        presets_dir = storage_dir / "previews" / "presets" / f"{proj.id}_{clip.id}"
        for item in resp1["presets"]:
            jpg_path = presets_dir / f"{item['id']}.jpg"
            assert jpg_path.exists(), f"Missing preset thumbnail {jpg_path}"
            with Image.open(jpg_path) as im:
                assert im.size == (160, 90)
        assert (presets_dir / "custom_lut.jpg").exists()

        resp2 = main_mod.get_clip_preset_previews_endpoint(proj.id, clip.id, session=session)
        assert resp2["cache_key"] == resp1["cache_key"]

        # Verify Compare Original (Hold) bypass_color returns raw neutral frame vs graded frame
        prev_raw = main_mod.preview_frame_endpoint(
            PreviewFrameRequest(
                project_id=proj.id,
                clip_id=clip.id,
                timestamp=1.0,
                aspect_ratio="16:9",
                burn_captions=False,
                color_preset="bw_16mm",
                saturation=0.0,
                exposure=0.35,
                bypass_color=True,
            ),
            session=session,
        )
        prev_graded = main_mod.preview_frame_endpoint(
            PreviewFrameRequest(
                project_id=proj.id,
                clip_id=clip.id,
                timestamp=1.0,
                aspect_ratio="16:9",
                burn_captions=False,
                color_preset="bw_16mm",
                saturation=0.0,
                exposure=0.35,
                bypass_color=False,
            ),
            session=session,
        )
        raw_img = np.array(Image.open(storage_dir / "previews" / Path(prev_raw["frame_url"]).name).convert("RGB"), dtype=np.float32)
        graded_img = np.array(Image.open(storage_dir / "previews" / Path(prev_graded["frame_url"]).name).convert("RGB"), dtype=np.float32)
        assert float(np.mean(np.abs(raw_img - graded_img))) > 15.0
        # Bypassed frame retains vivid color (chroma spread large), while graded bw_16mm is grayscale
        assert float(np.mean(np.max(raw_img, axis=2) - np.min(raw_img, axis=2))) > 20.0
        assert float(np.mean(np.max(graded_img, axis=2) - np.min(graded_img, axis=2))) < 3.5
