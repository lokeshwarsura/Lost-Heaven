import os
from pathlib import Path

import cv2
import numpy as np
import pytest
from PIL import Image
from sqlmodel import Session, SQLModel, create_engine

import app.database as app_db
import app.main as main_mod
from app.config import settings
from app.models import Project, UpdateClipRequest, ViralClip
from app.services.bg_segmentation import segment_foreground_mask
from app.services.broll_engine import build_broll_overlay_filter
from app.services.filters import build_depth_pop_foreground_filter
from app.services.preview_frame import render_exact_preview_frame
from app.services.proxy import probe_video
from app.services.reframer import (
    build_camera_motion_zoom_expr,
    build_multi_aspect_filter,
    canonicalize_camera_motion,
    compute_camera_motion_zoom_at_t,
    prepare_clip_framing_data,
)
from app.services.renderer import render_clip_ultra
from tests.test_unified_reframer import _draw_portrait, _encode_png_sequence_video


def _setup_db(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    storage_dir = tmp_path / "storage"
    for sub in ("previews", "renders", "masters", "assets", "thumbnails", "models"):
        (storage_dir / sub).mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr(settings, "STORAGE_DIR", str(storage_dir))
    monkeypatch.setattr(settings, "GEMINI_API_KEY", "")
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    monkeypatch.setattr(main_mod, "get_persisted_settings", lambda: {})
    db_path = tmp_path / "v5_phase3.db"
    test_engine = create_engine(f"sqlite:///{db_path}", connect_args={"check_same_thread": False})
    SQLModel.metadata.create_all(test_engine)
    monkeypatch.setattr(app_db, "engine", test_engine)
    return test_engine, storage_dir


def _extract_frame_bgr(video_path: str, t_sec: float) -> np.ndarray:
    cap = cv2.VideoCapture(str(video_path))
    assert cap.isOpened(), f"Failed opening {video_path}"
    fps = cap.get(cv2.CAP_PROP_FPS) or 15.0
    frame_idx = max(0, int(round(t_sec * fps)))
    cap.set(cv2.CAP_PROP_POS_FRAMES, frame_idx)
    ok, frame = cap.read()
    cap.release()
    assert ok and frame is not None, f"Failed reading frame at t={t_sec}s"
    return frame


def test_virtual_camera_motion_dolly_and_beat_punch(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
):
    test_engine, storage_dir = _setup_db(tmp_path, monkeypatch)

    # Unit checks for canonicalize / dolly zoom ramp / beat punch
    assert canonicalize_camera_motion("Slow Dolly In") == "dolly_in"
    assert canonicalize_camera_motion("dolly_out") == "dolly_out"
    assert canonicalize_camera_motion("beat_punch") == "beat_punch"
    assert canonicalize_camera_motion("unknown") == "steady"

    assert compute_camera_motion_zoom_at_t("dolly_in", 0.0, 3.0) == pytest.approx(1.0, abs=1e-4)
    assert compute_camera_motion_zoom_at_t("dolly_in", 3.0, 3.0) == pytest.approx(1.12, abs=1e-4)
    assert compute_camera_motion_zoom_at_t("dolly_out", 0.0, 3.0) == pytest.approx(1.12, abs=1e-4)
    assert compute_camera_motion_zoom_at_t("dolly_out", 3.0, 3.0) == pytest.approx(1.0, abs=1e-4)

    beat_peak = compute_camera_motion_zoom_at_t("beat_punch", 1.0, 3.0, beat_timestamps=[1.0])
    beat_rest = compute_camera_motion_zoom_at_t("beat_punch", 2.2, 3.0, beat_timestamps=[1.0])
    assert beat_peak >= 1.04
    assert beat_rest == pytest.approx(1.0, abs=1e-4)

    dolly_expr = build_camera_motion_zoom_expr("dolly_in", duration=3.0, fps=15.0)
    assert "1.0+0.12" in dolly_expr

    # Build studio clip with centered high-contrast square/portrait target over neutral backdrop
    frame = np.full((1080, 1920, 3), (38, 34, 42), dtype=np.uint8)
    _draw_portrait(frame, 960, 480, scale=1.15, shirt_bgr=(55, 190, 235))
    # Bright horizontal calibration bar across portrait chest to measure magnification accurately
    cv2.rectangle(frame, (860, 560), (1060, 620), (45, 220, 245), -1)

    src_mp4 = _encode_png_sequence_video(tmp_path / "dolly_src.mp4", [(frame, 2.6)], fps=15)

    fg_dolly = build_multi_aspect_filter(
        aspect_ratio="9:16",
        frame_layout="center",
        source_w=1920,
        source_h=1080,
        clip_size="720p" if False else None,
        is_video_render=True,
        camera_motion="dolly_in",
        clip_duration=2.6,
        fps=15.0,
    ) if False else build_multi_aspect_filter(
        aspect_ratio="9:16",
        frame_layout="center",
        source_w=1920,
        source_h=1080,
        target_size=(720, 1280),
        is_video_render=True,
        camera_motion="dolly_in",
        clip_duration=2.6,
        fps=15.0,
    )
    assert "zoompan=" in fg_dolly
    assert "d=1:" in fg_dolly
    assert "s=720x1280:fps=15" in fg_dolly

    # Exact preview frames at t=0.05s vs t=2.45s should zoom the bright calibration bar wider (~+10-12%)
    prev_start = render_exact_preview_frame(
        video_path=str(src_mp4),
        timestamp=0.05,
        clip_start_time=0.0,
        clip_duration=2.6,
        clip_rel_ts_override=0.05,
        frame_layout="center",
        camera_motion="dolly_in",
        clip_size="720p",
        burn_captions=False,
    )
    prev_end = render_exact_preview_frame(
        video_path=str(src_mp4),
        timestamp=2.45,
        clip_start_time=0.0,
        clip_duration=2.6,
        clip_rel_ts_override=2.45,
        frame_layout="center",
        camera_motion="dolly_in",
        clip_size="720p",
        burn_captions=False,
    )

    p0 = cv2.imread(str(storage_dir / "previews" / Path(prev_start["frame_url"]).name))
    p1 = cv2.imread(str(storage_dir / "previews" / Path(prev_end["frame_url"]).name))
    assert p0 is not None and p1 is not None

    def _gold_bar_width(bgr: np.ndarray) -> int:
        # Gold BGR (45, 220, 245) -> high R+G, lower B
        mask = (bgr[:, :, 2].astype(np.int16) > 190) & (bgr[:, :, 1].astype(np.int16) > 170) & (bgr[:, :, 0].astype(np.int16) < 110)
        ys, xs = np.where(mask)
        assert len(xs) > 20
        return int(xs.max() - xs.min())

    w_start = _gold_bar_width(p0)
    w_end = _gold_bar_width(p1)
    assert w_end > int(w_start * 1.07), f"Expected dolly_in magnification: start={w_start}px, end={w_end}px"

    # Full MP4 render with dolly_in via zoompan
    with Session(test_engine) as session:
        proj = Project(title="Dolly Test", source_path=str(src_mp4), duration=2.6, status="finished")
        session.add(proj)
        session.commit()
        session.refresh(proj)

        out_mp4 = str(storage_dir / "dolly_out.mp4")
        rendered = render_clip_ultra(
            master_video_path=str(src_mp4),
            clip_id=501,
            start_time=0.0,
            duration=2.4,
            words=[],
            project_id=proj.id,
            aspect_ratio="9:16",
            frame_layout="center",
            camera_motion="dolly_in",
            clip_size="540p",
            burn_captions=False,
            hardware_mode="cpu",
            quality_mode="fast_preview",
            output_path_override=out_mp4,
        )
        assert os.path.exists(rendered)
        meta = probe_video(rendered)
        assert meta["width"] == 540 and meta["height"] == 960
        mp4_start = _extract_frame_bgr(rendered, 0.1)
        mp4_end = _extract_frame_bgr(rendered, 2.2)
        assert _gold_bar_width(mp4_end) > _gold_bar_width(mp4_start)


def test_whip_pan_scene_cuts_preview_and_render_parity(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
):
    test_engine, storage_dir = _setup_db(tmp_path, monkeypatch)

    # Shot 1 (0..1.5s): crisp high-contrast vertical stripes across left/center
    shot1 = np.full((1080, 1920, 3), (85, 50, 30), dtype=np.uint8)
    for x in range(740, 1180, 24):
        cv2.rectangle(shot1, (x, 220), (x + 8, 860), (245, 245, 250), -1)
    _draw_portrait(shot1, 660, 480, scale=1.0, shirt_bgr=(60, 120, 180))

    # Shot 2 (1.5..3.0s): contrasting scene + vertical stripes + portrait on right
    shot2 = np.full((1080, 1920, 3), (35, 95, 175), dtype=np.uint8)
    for x in range(740, 1180, 24):
        cv2.rectangle(shot2, (x, 220), (x + 8, 860), (240, 240, 240), -1)
    _draw_portrait(shot2, 1260, 480, scale=1.0, shirt_bgr=(180, 75, 55))

    src_mp4 = _encode_png_sequence_video(tmp_path / "whip_pan_src.mp4", [(shot1, 1.5), (shot2, 1.5)], fps=15)

    ft, _, _ = prepare_clip_framing_data(str(src_mp4), [(0.0, 3.0)], frame_layout="auto_track")
    cut_ts = list(ft.get("cut_timestamps") or [])
    assert len(cut_ts) >= 1, f"Expected internal scene cut detected, got {ft}"

    fg_whip = build_multi_aspect_filter(
        aspect_ratio="9:16",
        frame_layout="auto_track",
        source_w=1920,
        source_h=1080,
        target_size=(720, 1280),
        face_track_data=ft,
        is_video_render=True,
        whip_pan_cuts=True,
        clip_duration=3.0,
        fps=15.0,
    )
    assert "avgblur=sizeX=28:sizeY=1:enable='between(t," in fg_whip

    # Preview exactly at the internal cut timestamp with whip_pan_cuts=False vs True
    cut_t = float(cut_ts[0])
    prev_crisp = render_exact_preview_frame(
        video_path=str(src_mp4),
        timestamp=cut_t,
        clip_start_time=0.0,
        clip_duration=3.0,
        clip_rel_ts_override=cut_t,
        frame_layout="center",
        visual_map={"cut_timestamps": cut_ts, "keyframes": [], "shots": [], "webcam": {"detected": False}, "summary": {}},
        whip_pan_cuts=False,
        clip_size="720p",
        burn_captions=False,
    )
    prev_whip = render_exact_preview_frame(
        video_path=str(src_mp4),
        timestamp=cut_t,
        clip_start_time=0.0,
        clip_duration=3.0,
        clip_rel_ts_override=cut_t,
        frame_layout="center",
        visual_map={"cut_timestamps": cut_ts, "keyframes": [], "shots": [], "webcam": {"detected": False}, "summary": {}},
        whip_pan_cuts=True,
        clip_size="720p",
        burn_captions=False,
    )

    img_crisp = cv2.imread(str(storage_dir / "previews" / Path(prev_crisp["frame_url"]).name), cv2.IMREAD_GRAYSCALE)
    img_whip = cv2.imread(str(storage_dir / "previews" / Path(prev_whip["frame_url"]).name), cv2.IMREAD_GRAYSCALE)
    assert img_crisp is not None and img_whip is not None

    # Horizontal gradient energy must drop sharply under avgblur(sizeX=28, sizeY=1)
    gx_crisp = float(np.abs(np.diff(img_crisp.astype(np.float32), axis=1)).mean())
    gx_whip = float(np.abs(np.diff(img_whip.astype(np.float32), axis=1)).mean())
    assert gx_whip < gx_crisp * 0.65, f"Expected horizontal whip-pan blur drop: crisp={gx_crisp:.2f}, whip={gx_whip:.2f}"

    # Render full MP4 cleanly with whip_pan_cuts=True
    with Session(test_engine) as session:
        proj = Project(title="Whip Pan Render", source_path=str(src_mp4), duration=3.0, status="finished")
        session.add(proj)
        session.commit()
        session.refresh(proj)

        out_mp4 = str(storage_dir / "whip_pan_out.mp4")
        rendered = render_clip_ultra(
            master_video_path=str(src_mp4),
            clip_id=502,
            start_time=0.0,
            duration=3.0,
            words=[],
            project_id=proj.id,
            aspect_ratio="9:16",
            frame_layout="auto_track",
            whip_pan_cuts=True,
            clip_size="540p",
            burn_captions=False,
            hardware_mode="cpu",
            quality_mode="fast_preview",
            output_path_override=out_mp4,
        )
        assert os.path.exists(rendered)
        assert probe_video(rendered)["width"] == 540


def test_broll_dual_angle_fullscreen_vs_reaction_pip(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
):
    test_engine, storage_dir = _setup_db(tmp_path, monkeypatch)

    # Filtergraph construction test for dual-angle reaction_pip
    broll_fg, broll_out = build_broll_overlay_filter(
        base_video_label="v_base",
        broll_specs=[(1, 0.6, 1.8, True, "reaction_pip")],
        target_w=540,
        target_h=960,
        fps=15.0,
        out_label="v_broll",
        default_layout="reaction_pip",
        reaction_corner="bottom_right",
    )
    assert "v_br_main_" in broll_fg
    assert "v_br_pip_" in broll_fg
    assert "v_br_card_" in broll_fg
    assert "between(t,0.600,1.800)" in broll_fg
    assert "geq=" in broll_fg
    assert broll_out == "v_broll"

    # Real dual-angle MP4 verification:
    # Base studio speaker: deep cobalt-navy studio backdrop + warm portrait at center
    studio = np.full((1080, 1920, 3), (125, 45, 25), dtype=np.uint8)
    _draw_portrait(studio, 960, 480, scale=1.45, shirt_bgr=(60, 75, 220))
    # Ensure corners stay unmistakably navy/cobalt on base video (no portrait leak into corners)
    src_mp4 = _encode_png_sequence_video(tmp_path / "broll_speaker.mp4", [(studio, 2.6)], fps=15)

    # Distinct neon lime-green B-Roll plate (BGR: 30, 235, 45) active during 0.6s..1.8s
    broll_png = tmp_path / "cutaway_green.png"
    green_img = np.full((720, 1280, 3), (30, 235, 45), dtype=np.uint8)
    Image.fromarray(cv2.cvtColor(green_img, cv2.COLOR_BGR2RGB)).save(broll_png)

    segs = [
        {
            "keyword": "growth",
            "start": 0.6,
            "end": 1.8,
            "asset_path": str(broll_png),
            "source": "local",
            "enabled": True,
        }
    ]

    with Session(test_engine) as session:
        proj = Project(title="Dual Broll", source_path=str(src_mp4), duration=2.6, status="finished")
        session.add(proj)
        session.commit()
        session.refresh(proj)

        # Render 1: Full-screen cutaway
        out_full = render_clip_ultra(
            master_video_path=str(src_mp4),
            clip_id=503,
            start_time=0.0,
            duration=2.5,
            words=[],
            project_id=proj.id,
            aspect_ratio="9:16",
            frame_layout="center",
            broll_segments=segs,
            broll_layout="fullscreen",
            clip_size="540p",
            burn_captions=False,
            hardware_mode="cpu",
            quality_mode="fast_preview",
            output_path_override=str(storage_dir / "broll_full.mp4"),
        )
        f_full_mid = _extract_frame_bgr(out_full, 1.2)
        h, w = f_full_mid.shape[:2]
        # Both center and bottom-right corner must be neon green in fullscreen mode
        center_px = f_full_mid[h // 2, w // 2]
        corner_px = f_full_mid[int(h * 0.78), int(w * 0.78)]
        assert center_px[1] > 180 and center_px[0] < 80
        assert corner_px[1] > 180 and corner_px[0] < 80

        # Render 2: Dual-Angle B-Roll + Speaker Reaction PiP (bottom_right corner)
        out_pip = render_clip_ultra(
            master_video_path=str(src_mp4),
            clip_id=504,
            start_time=0.0,
            duration=2.5,
            words=[],
            project_id=proj.id,
            aspect_ratio="9:16",
            frame_layout="center",
            webcam_corner="bottom_right",
            broll_segments=segs,
            broll_layout="reaction_pip",
            clip_size="540p",
            burn_captions=False,
            hardware_mode="cpu",
            quality_mode="fast_preview",
            output_path_override=str(storage_dir / "broll_reaction_pip.mp4"),
        )
        f_before = _extract_frame_bgr(out_pip, 0.2)
        f_during = _extract_frame_bgr(out_pip, 1.2)
        f_after = _extract_frame_bgr(out_pip, 2.2)

        # Before 0.6s & After 1.8s -> studio navy background (corner is NOT green and NOT green center)
        assert f_before[h // 2, 30][0] > f_before[h // 2, 30][1]
        assert f_after[h // 2, 30][0] > f_after[h // 2, 30][1]

        # During 0.6s..1.8s -> main frame center is green B-roll, WHILE bottom-right reaction PiP box shows speaker!
        c_during = f_during[h // 2, w // 2]
        assert c_during[1] > 180 and c_during[0] < 80, f"Center should show green B-roll, got {c_during}"

        # Reaction PiP box sits safely above bottom caption band (overlay ~265:501, 166x190 on 540x960)
        pip_patch = f_during[int(h * 0.54):int(h * 0.70), int(w * 0.52):int(w * 0.78)]
        # PiP card contains speaker face/shirt/navy backdrop (definitely not solid green B-roll!)
        non_green_ratio = float(np.mean(pip_patch[:, :, 1] < 180))
        assert non_green_ratio > 0.65, f"Expected live speaker reaction inside corner PiP, non_green_ratio={non_green_ratio:.2f}"


def test_subject_depth_pop_preview_and_render_bokeh_and_clarity(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
):
    test_engine, storage_dir = _setup_db(tmp_path, monkeypatch)

    pop_filter = build_depth_pop_foreground_filter(0.85)
    assert "eq=contrast=" in pop_filter
    assert "unsharp=" in pop_filter
    assert build_depth_pop_foreground_filter(0.0) == ""

    # High-frequency checkered background + central portrait subject (matches test_vision_studio_v4 pattern)
    # Build 640x360 with sharp 8x8 checkerboard around borders + centered studio portrait
    tile = np.zeros((360, 640, 3), dtype=np.uint8)
    for y in range(0, 360, 12):
        for x in range(0, 640, 12):
            c = (240, 240, 240) if ((x // 12 + y // 12) % 2 == 0) else (25, 25, 30)
            tile[y:y + 12, x:x + 12] = c
    _draw_portrait(tile, 320, 180, scale=1.05, shirt_bgr=(50, 75, 165))

    matte = segment_foreground_mask(tile)
    assert matte.shape == tile.shape[:2]
    assert int(matte.max()) > 200

    src_mp4 = _encode_png_sequence_video(tmp_path / "depth_pop_src.mp4", [(tile, 2.2)], fps=15)

    prev_flat = render_exact_preview_frame(
        video_path=str(src_mp4),
        timestamp=0.6,
        frame_layout="center",
        burn_captions=False,
        backdrop_mode="none",
        depth_pop=0.0,
        clip_size="540p",
    )
    prev_pop = render_exact_preview_frame(
        video_path=str(src_mp4),
        timestamp=0.6,
        frame_layout="center",
        burn_captions=False,
        backdrop_mode="none",
        depth_pop=0.85,
        clip_size="540p",
    )

    p_flat = cv2.imread(str(storage_dir / "previews" / Path(prev_flat["frame_url"]).name))
    p_pop = cv2.imread(str(storage_dir / "previews" / Path(prev_pop["frame_url"]).name))
    assert p_flat is not None and p_pop is not None

    # Background top banner patch should be visibly bokeh-blurred under depth_pop=0.85 vs depth_pop=0.0
    bg_flat = cv2.cvtColor(p_flat[16:120, 40:500], cv2.COLOR_BGR2GRAY)
    bg_pop = cv2.cvtColor(p_pop[16:120, 40:500], cv2.COLOR_BGR2GRAY)
    sharp_flat = float(cv2.Laplacian(bg_flat, cv2.CV_64F).var())
    sharp_pop = float(cv2.Laplacian(bg_pop, cv2.CV_64F).var())
    assert sharp_pop < sharp_flat * 0.45, f"Expected bokeh blur on background: flat={sharp_flat:.1f}, pop={sharp_pop:.1f}"

    # Full MP4 render with depth_pop=0.85 & captions active should keep crisp bokeh + foreground
    with Session(test_engine) as session:
        proj = Project(title="Depth Pop", source_path=str(src_mp4), duration=2.2, status="finished")
        session.add(proj)
        session.commit()
        session.refresh(proj)

        out_mp4 = render_clip_ultra(
            master_video_path=str(src_mp4),
            clip_id=505,
            start_time=0.0,
            duration=2.0,
            words=[{"word": "f1.8", "start": 0.1, "end": 1.2}],
            project_id=proj.id,
            aspect_ratio="9:16",
            frame_layout="center",
            backdrop_mode="none",
            depth_pop=0.85,
            clip_size="540p",
            burn_captions=True,
            hardware_mode="cpu",
            quality_mode="fast_preview",
            output_path_override=str(storage_dir / "depth_pop_out.mp4"),
        )
        assert os.path.exists(out_mp4)
        mp4_frame = _extract_frame_bgr(out_mp4, 0.8)
        bg_mp4 = cv2.cvtColor(mp4_frame[16:120, 40:500], cv2.COLOR_BGR2GRAY)
        sharp_mp4 = float(cv2.Laplacian(bg_mp4, cv2.CV_64F).var())
        assert sharp_mp4 < sharp_flat * 0.55, f"Expected rendered MP4 bokeh background blur, got {sharp_mp4:.1f} vs flat {sharp_flat:.1f}"


def test_phase3_api_persistence_and_snapshot(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
):
    test_engine, _ = _setup_db(tmp_path, monkeypatch)
    dummy_vid = tmp_path / "dummy.mp4"
    frame = np.full((360, 640, 3), 50, dtype=np.uint8)
    _encode_png_sequence_video(dummy_vid, [(frame, 1.0)], fps=15)

    with Session(test_engine) as session:
        proj = Project(title="Persist Phase 3", source_path=str(dummy_vid), duration=1.0, status="finished")
        session.add(proj)
        session.commit()
        session.refresh(proj)

        clip = ViralClip(project_id=proj.id, start_time=0.0, end_time=1.0, duration=1.0, virality_score=88)
        session.add(clip)
        session.commit()
        session.refresh(clip)

        updated = main_mod.update_clip(
            clip.id,
            UpdateClipRequest(
                camera_motion="beat_punch",
                whip_pan_cuts=True,
                broll_layout="reaction_pip",
                depth_pop=0.75,
            ),
            session=session,
        )
        assert updated["status"] == "updated"
        assert updated["clip_id"] == clip.id

        fetched = main_mod.get_clip(clip.id, session=session)
        assert fetched["camera_motion"] == "beat_punch"
        assert fetched["whip_pan_cuts"] is True
        assert fetched["broll_layout"] == "reaction_pip"
        assert fetched["depth_pop"] == pytest.approx(0.75, abs=1e-4)

        reloaded = session.get(ViralClip, clip.id)
        snap = main_mod._snapshot_clip(reloaded)
        assert snap["camera_motion"] == "beat_punch"
        assert snap["whip_pan_cuts"] is True
        assert snap["broll_layout"] == "reaction_pip"
        assert snap["depth_pop"] == pytest.approx(0.75, abs=1e-4)
