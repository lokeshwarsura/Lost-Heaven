import subprocess
from pathlib import Path

import cv2
import numpy as np
import pytest
from fastapi import BackgroundTasks
from sqlmodel import SQLModel, create_engine, Session

import app.database as db_mod
import app.main as main_mod
from app.config import settings
from app.models import (
    PreviewFrameRequest,
    Project,
    RenderClipRequest,
    UpdateClipRequest,
    ViralClip,
)
from app.services.caption_renderer import render_caption_frame
from app.services.preview_frame import render_exact_preview_frame
from app.services.proxy import get_ffmpeg_cmd
from app.services.render_queue import compute_render_signature
from app.services.renderer import render_clip_ultra


def _setup_isolated_db(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    storage_dir = tmp_path / "storage"
    for sub in ("masters", "proxies", "renders", "previews", "captions", "projects"):
        (storage_dir / sub).mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr(settings, "STORAGE_DIR", str(storage_dir))

    db_path = tmp_path / "hook_toggle_test.db"
    test_engine = create_engine(
        f"sqlite:///{db_path}",
        echo=False,
        connect_args={"check_same_thread": False},
    )
    SQLModel.metadata.create_all(test_engine)
    monkeypatch.setattr(db_mod, "engine", test_engine)
    return test_engine, storage_dir


def _make_clean_video(out_path: Path, duration_sec: float = 2.0) -> Path:
    ffmpeg = get_ffmpeg_cmd()
    out_path.parent.mkdir(parents=True, exist_ok=True)
    res = subprocess.run(
        [
            ffmpeg,
            "-y",
            "-f",
            "lavfi",
            "-i",
            f"color=c=#182436:size=640x360:rate=24:duration={duration_sec:.2f}",
            "-f",
            "lavfi",
            "-i",
            f"sine=frequency=440:sample_rate=44100:duration={duration_sec:.2f}",
            "-c:v",
            "libx264",
            "-preset",
            "ultrafast",
            "-pix_fmt",
            "yuv420p",
            "-c:a",
            "aac",
            "-ar",
            "44100",
            "-ac",
            "1",
            str(out_path),
        ],
        capture_output=True,
        text=True,
        timeout=45,
    )
    assert res.returncode == 0, res.stderr
    return out_path


def _read_preview_bgr(frame_url: str) -> np.ndarray:
    path = Path(settings.STORAGE_DIR) / "previews" / Path(frame_url).name
    img = cv2.imdecode(np.fromfile(str(path), dtype=np.uint8), cv2.IMREAD_COLOR)
    assert img is not None, f"Failed to decode preview image {path}"
    return img


def _read_mp4_frame_at(video_path: str, ms: float = 500.0) -> np.ndarray:
    cap = cv2.VideoCapture(video_path)
    cap.set(cv2.CAP_PROP_POS_MSEC, ms)
    ok, frame = cap.read()
    cap.release()
    assert ok and frame is not None, f"Failed reading frame from {video_path}"
    return frame


def test_caption_renderer_strict_hook_boolean_guard():
    words = [{"word": "Testing", "start": 0.1, "end": 0.9}]
    # Clean frame with empty hook text
    clean = np.array(
        render_caption_frame(
            t=0.5,
            width=540,
            height=960,
            words=words,
            burn_captions=True,
            hook_banner_enabled=False,
            hook_headline="",
            clip_duration=3.0,
        )
    )
    # False via legacy hook_banner_enabled despite stale non-empty headline
    off_legacy = np.array(
        render_caption_frame(
            t=0.5,
            width=540,
            height=960,
            words=words,
            burn_captions=True,
            hook_banner_enabled=False,
            hook_headline="GTA 6 TOLL BOOTH HACKS",
            clip_duration=3.0,
        )
    )
    assert np.array_equal(off_legacy, clean)

    # show_hook=False MUST override even if hook_banner_enabled=True
    off_show_hook = np.array(
        render_caption_frame(
            t=0.5,
            width=540,
            height=960,
            words=words,
            burn_captions=True,
            hook_banner_enabled=True,
            show_hook=False,
            hook_headline="GTA 6 TOLL BOOTH HACKS",
            hook_text="GTA 6 TOLL BOOTH HACKS",
            clip_duration=3.0,
        )
    )
    assert np.array_equal(off_show_hook, clean)

    # String "false" must coerce strictly to False
    off_str_false = np.array(
        render_caption_frame(
            t=0.5,
            width=540,
            height=960,
            words=words,
            burn_captions=True,
            show_hook="false",  # type: ignore[arg-type]
            hook_text="GTA 6 TOLL BOOTH HACKS",
            clip_duration=3.0,
        )
    )
    assert np.array_equal(off_str_false, clean)

    # True + non-empty text draws top hook banner even with burn_captions=False
    on_nocap = np.array(
        render_caption_frame(
            t=0.5,
            width=540,
            height=960,
            words=words,
            burn_captions=False,
            show_hook=True,
            hook_text="GTA 6 TOLL BOOTH HACKS",
            clip_duration=3.0,
        )
    )
    top_alpha_on = on_nocap[: int(960 * 0.25), :, 3]
    assert int(np.sum(top_alpha_on > 20)) > 1500


def test_preview_frame_no_hook_pixels_when_unchecked_with_stale_headline(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    _setup_isolated_db(tmp_path, monkeypatch)
    src = _make_clean_video(tmp_path / "media" / "clean_src.mp4")

    clean_res = render_exact_preview_frame(
        video_path=str(src),
        timestamp=0.5,
        clip_start_time=0.0,
        clip_duration=2.0,
        clip_rel_ts_override=0.5,
        burn_captions=True,
        show_hook=False,
        hook_text="",
        transcript_words=[{"word": "Hello", "start": 0.1, "end": 0.9}],
    )
    unchecked_stale_res = render_exact_preview_frame(
        video_path=str(src),
        timestamp=0.5,
        clip_start_time=0.0,
        clip_duration=2.0,
        clip_rel_ts_override=0.5,
        burn_captions=True,
        show_hook=False,
        show_hook_banner=False,
        hook_banner_enabled=False,
        hook_headline="GTA 6 TOLL BOOTH HACKS",
        hook_text="GTA 6 TOLL BOOTH HACKS",
        caption_options={
            "show_hook": False,
            "show_hook_banner": False,
            "hook_banner_enabled": False,
            "hook_headline": "GTA 6 TOLL BOOTH HACKS",
        },
        transcript_words=[{"word": "Hello", "start": 0.1, "end": 0.9}],
    )
    enabled_res = render_exact_preview_frame(
        video_path=str(src),
        timestamp=0.5,
        clip_start_time=0.0,
        clip_duration=2.0,
        clip_rel_ts_override=0.5,
        burn_captions=True,
        show_hook=True,
        hook_text="GTA 6 TOLL BOOTH HACKS",
        transcript_words=[{"word": "Hello", "start": 0.1, "end": 0.9}],
    )

    img_clean = _read_preview_bgr(clean_res["frame_url"])
    img_unchecked = _read_preview_bgr(unchecked_stale_res["frame_url"])
    img_enabled = _read_preview_bgr(enabled_res["frame_url"])

    h = img_clean.shape[0]
    top_end = int(h * 0.25)
    diff_off = float(
        np.mean(
            np.abs(
                img_unchecked[:top_end].astype(np.float32)
                - img_clean[:top_end].astype(np.float32)
            )
        )
    )
    diff_on = float(
        np.mean(
            np.abs(
                img_enabled[:top_end].astype(np.float32)
                - img_clean[:top_end].astype(np.float32)
            )
        )
    )
    assert diff_off == 0.0, f"Unchecked Top Hook leaked pixels into preview top band (diff={diff_off})"
    changed_on = int(
        np.sum(
            np.max(np.abs(img_enabled[:top_end].astype(np.int32) - img_clean[:top_end].astype(np.int32)), axis=2) > 25
        )
    )
    assert diff_on > 2.0 and changed_on > 2000, f"Checked Top Hook did not visibly render onto preview top band (diff={diff_on}, px={changed_on})"


def test_render_mp4_no_hook_pixels_when_unchecked_and_signature_invalidates(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    _setup_isolated_db(tmp_path, monkeypatch)
    src = _make_clean_video(tmp_path / "media" / "render_src.mp4")

    sig_off = compute_render_signature(
        clip_id=99,
        project_id=1,
        start_time=0.0,
        duration=2.0,
        params={
            "show_hook": False,
            "show_hook_banner": False,
            "hook_headline": "GTA 6 TOLL BOOTH HACKS",
            "options": {"show_hook": False},
        },
    )
    sig_on = compute_render_signature(
        clip_id=99,
        project_id=1,
        start_time=0.0,
        duration=2.0,
        params={
            "show_hook": True,
            "show_hook_banner": True,
            "hook_headline": "GTA 6 TOLL BOOTH HACKS",
            "options": {"show_hook": True},
        },
    )
    assert sig_off != sig_on

    out_clean = render_clip_ultra(
        master_video_path=str(src),
        clip_id=701,
        project_id=70,
        start_time=0.0,
        duration=2.0,
        words=[{"word": "Clean", "start": 0.2, "end": 0.8}],
        aspect_ratio="9:16",
        frame_layout="auto_track",
        burn_captions=True,
        show_hook=False,
        show_hook_banner=False,
        hook_headline="",
        quality_mode="fast_preview",
        hardware_mode="cpu",
        output_path_override=str(tmp_path / "storage" / "renders" / "clean.mp4"),
    )
    out_unchecked = render_clip_ultra(
        master_video_path=str(src),
        clip_id=702,
        project_id=70,
        start_time=0.0,
        duration=2.0,
        words=[{"word": "Clean", "start": 0.2, "end": 0.8}],
        aspect_ratio="9:16",
        frame_layout="auto_track",
        burn_captions=True,
        show_hook=False,
        show_hook_banner=False,
        hook_headline="GTA 6 TOLL BOOTH HACKS",
        hook_text="GTA 6 TOLL BOOTH HACKS",
        options={"show_hook": False},
        quality_mode="fast_preview",
        hardware_mode="cpu",
        output_path_override=str(tmp_path / "storage" / "renders" / "unchecked_stale.mp4"),
    )
    out_enabled = render_clip_ultra(
        master_video_path=str(src),
        clip_id=703,
        project_id=70,
        start_time=0.0,
        duration=2.0,
        words=[{"word": "Clean", "start": 0.2, "end": 0.8}],
        aspect_ratio="9:16",
        frame_layout="auto_track",
        burn_captions=True,
        show_hook=True,
        show_hook_banner=True,
        hook_headline="GTA 6 TOLL BOOTH HACKS",
        hook_text="GTA 6 TOLL BOOTH HACKS",
        options={"show_hook": True},
        quality_mode="fast_preview",
        hardware_mode="cpu",
        output_path_override=str(tmp_path / "storage" / "renders" / "enabled.mp4"),
    )

    frame_clean = _read_mp4_frame_at(out_clean, 500.0)
    frame_unchecked = _read_mp4_frame_at(out_unchecked, 500.0)
    frame_enabled = _read_mp4_frame_at(out_enabled, 500.0)

    h = frame_clean.shape[0]
    top_end = int(h * 0.25)
    diff_off = float(
        np.mean(
            np.abs(
                frame_unchecked[:top_end].astype(np.float32)
                - frame_clean[:top_end].astype(np.float32)
            )
        )
    )
    diff_on = float(
        np.mean(
            np.abs(
                frame_enabled[:top_end].astype(np.float32)
                - frame_clean[:top_end].astype(np.float32)
            )
        )
    )
    assert diff_off < 0.25, f"Unchecked Top Hook burned banner pixels into MP4 (diff={diff_off})"
    changed_on = int(
        np.sum(
            np.max(np.abs(frame_enabled[:top_end].astype(np.int32) - frame_clean[:top_end].astype(np.int32)), axis=2) > 25
        )
    )
    assert diff_on > 2.0 and changed_on > 2000, f"Enabled Top Hook did not burn banner pixels into MP4 (diff={diff_on}, px={changed_on})"


def test_clip_show_hook_sqlite_persistence_and_endpoint_parity(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    test_engine, storage_dir = _setup_isolated_db(tmp_path, monkeypatch)
    src = _make_clean_video(tmp_path / "media" / "db_src.mp4")

    with Session(test_engine) as session:
        proj = Project(
            title="Hook Toggle Test",
            source_type="local",
            source_path=str(src),
            duration=2.0,
        )
        session.add(proj)
        session.commit()
        session.refresh(proj)

        clip = ViralClip(
            project_id=proj.id,
            title="Clip 1",
            hook_headline="GTA 6 TOLL BOOTH HACKS",
            show_hook_banner=True,
            start_time=0.0,
            end_time=2.0,
            duration=2.0,
            virality_score=90,
            hook_score=88,
            burn_captions=True,
        )
        clip.set_transcript([{"word": "Watch", "start": 0.2, "end": 0.8}])
        session.add(clip)
        session.commit()
        session.refresh(clip)

        # Uncheck Top Hook via PATCH /api/clips/{id} while keeping stale headline
        main_mod.update_clip(
            clip.id,
            UpdateClipRequest(show_hook=False, show_hook_banner=False, options={"show_hook": False}),
            session=session,
        )
        session.refresh(clip)
        assert clip.show_hook_banner is False
        assert clip.get_options().get("show_hook") is False
        # Draft headline preserved in DB for UI restore
        assert clip.hook_headline == "GTA 6 TOLL BOOTH HACKS"

        # Reload project serializer MUST expose show_hook=False and show_hook_banner=False
        loaded = main_mod.get_project(proj.id, session=session)
        serialized_clip = loaded["clips"][0]
        assert serialized_clip["show_hook"] is False
        assert serialized_clip["show_hook_banner"] is False

        # /api/preview-frame called only with clip_id respects persisted show_hook=False
        prev_off = main_mod.preview_frame_endpoint(
            PreviewFrameRequest(clip_id=clip.id, timestamp=0.5, clip_rel_ts=0.5),
            session=session,
        )
        prev_explicit_clean = main_mod.preview_frame_endpoint(
            PreviewFrameRequest(
                clip_id=clip.id,
                timestamp=0.5,
                clip_rel_ts=0.5,
                show_hook=False,
                hook_text="",
            ),
            session=session,
        )
        img_prev_off = _read_preview_bgr(prev_off["frame_url"])
        img_prev_clean = _read_preview_bgr(prev_explicit_clean["frame_url"])
        h_p = img_prev_off.shape[0]
        assert (
            float(
                np.mean(
                    np.abs(
                        img_prev_off[: int(h_p * 0.25)].astype(np.float32)
                        - img_prev_clean[: int(h_p * 0.25)].astype(np.float32)
                    )
                )
            )
            == 0.0
        )

        # Trigger fast_preview MP4 render with stale headline + show_hook=False -> zero banner in MP4
        res_off = main_mod.trigger_render(
            background_tasks=BackgroundTasks(),
            project_id=proj.id,
            clip_id=clip.id,
            req=RenderClipRequest(
                quality_mode="fast_preview",
                show_hook=False,
                show_hook_banner=False,
                hook_headline="GTA 6 TOLL BOOTH HACKS",
                hook_text="",
            ),
            session=session,
        )
        assert res_off["status"] == "completed"
        mp4_off = _read_mp4_frame_at(res_off["rendered_path"], 500.0)

        # Re-enable Top Hook -> trigger_render draws the banner in MP4
        res_on = main_mod.trigger_render(
            background_tasks=BackgroundTasks(),
            project_id=proj.id,
            clip_id=clip.id,
            req=RenderClipRequest(
                quality_mode="fast_preview",
                show_hook=True,
                show_hook_banner=True,
                hook_headline="GTA 6 TOLL BOOTH HACKS",
                hook_text="GTA 6 TOLL BOOTH HACKS",
            ),
            session=session,
        )
        assert res_on["status"] == "completed"
        mp4_on = _read_mp4_frame_at(res_on["rendered_path"], 500.0)

        top_diff = float(
            np.mean(
                np.abs(
                    mp4_on[: int(mp4_on.shape[0] * 0.25)].astype(np.float32)
                    - mp4_off[: int(mp4_off.shape[0] * 0.25)].astype(np.float32)
                )
            )
        )
        top_changed = int(
            np.sum(
                np.max(
                    np.abs(
                        mp4_on[: int(mp4_on.shape[0] * 0.25)].astype(np.int32)
                        - mp4_off[: int(mp4_off.shape[0] * 0.25)].astype(np.int32)
                    ),
                    axis=2,
                )
                > 25
            )
        )
        assert top_diff > 2.0 and top_changed > 2000, f"Toggling Top Hook back ON failed to render banner diff (diff={top_diff}, px={top_changed})"
