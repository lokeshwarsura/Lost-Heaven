import json
import os
import subprocess
import tempfile
import zipfile
from pathlib import Path
from unittest.mock import patch

import pytest
from PIL import Image
from sqlmodel import Session, SQLModel, create_engine

import app.database as app_db
import app.main as main_mod
from app.config import settings
from app.main import (
    export_publish_pack_endpoint,
    generate_clip_hooks_endpoint,
    generate_clip_social_copy_endpoint,
    get_clip_waveform,
    update_clip,
)
from app.models import Project, UpdateClipRequest, ViralClip
from app.services.caption_renderer import render_caption_frame
from app.services.captions_ass import EMOJI_KEYWORDS
from app.services.hook_generator import generate_hook_variants
from app.services.proxy import get_ffmpeg_cmd, probe_video
from app.services.social_copywriter import generate_multi_platform_copy


def _create_synthetic_av_mp4(out_path: Path, duration_sec: float = 3.0) -> Path:
    out_path.parent.mkdir(parents=True, exist_ok=True)
    cmd = [
        get_ffmpeg_cmd(),
        "-y",
        "-f", "lavfi",
        "-i", f"testsrc2=size=640x360:rate=15:duration={duration_sec}",
        "-f", "lavfi",
        "-i", f"sine=frequency=440:sample_rate=44100:duration={duration_sec}",
        "-c:v", "libx264",
        "-preset", "ultrafast",
        "-pix_fmt", "yuv420p",
        "-c:a", "aac",
        "-shortest",
        str(out_path),
    ]
    proc = subprocess.run(cmd, capture_output=True, text=True, timeout=60)
    assert proc.returncode == 0, proc.stderr
    return out_path


def _create_half_tone_half_silence_wav(out_path: Path, tone_sec: float = 3.0, silence_sec: float = 3.0) -> Path:
    out_path.parent.mkdir(parents=True, exist_ok=True)
    cmd = [
        get_ffmpeg_cmd(),
        "-y",
        "-f", "lavfi", "-t", str(tone_sec), "-i", "sine=frequency=440:sample_rate=16000",
        "-f", "lavfi", "-t", str(silence_sec), "-i", "anullsrc=channel_layout=mono:sample_rate=16000",
        "-filter_complex", "[0:a][1:a]concat=n=2:v=0:a=1[aout]",
        "-map", "[aout]",
        "-ac", "1", "-ar", "16000",
        str(out_path),
    ]
    proc = subprocess.run(cmd, capture_output=True, text=True, timeout=45)
    assert proc.returncode == 0, proc.stderr
    return out_path


def _setup_isolated_db(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    storage_dir = tmp_path / "storage"
    storage_dir.mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr(settings, "STORAGE_DIR", str(storage_dir))
    monkeypatch.setattr(settings, "GEMINI_API_KEY", "")
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    monkeypatch.setattr(main_mod, "get_persisted_settings", lambda: {})

    db_path = tmp_path / "pro_suite_v2.db"
    test_engine = create_engine(
        f"sqlite:///{db_path}",
        connect_args={"check_same_thread": False},
    )
    SQLModel.metadata.create_all(test_engine)
    monkeypatch.setattr(app_db, "engine", test_engine)
    return test_engine, storage_dir


def test_hook_variants_endpoint_and_social_copy_studio(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """
    Phase B Features 1 & 5:
    - generate_clip_hooks_endpoint returns 3 distinct hook variants (Curiosity, Contrarian, Action).
    - generate_clip_social_copy_endpoint returns all 4 platform entries with 5 hashtags each.
    - update_clip persists highlight_power_words cleanly.
    """
    test_engine, _ = _setup_isolated_db(tmp_path, monkeypatch)
    words = [
        {"word": "Never", "start": 0.1, "end": 0.4},
        {"word": "ignore", "start": 0.45, "end": 0.8},
        {"word": "compound", "start": 0.85, "end": 1.3},
        {"word": "momentum", "start": 1.35, "end": 1.8},
        {"word": "secret", "start": 1.85, "end": 2.3},
    ]
    with Session(test_engine) as s:
        proj = Project(
            id=8101,
            title="Creator Growth Masterclass",
            source_path=str(tmp_path / "missing.mp4"),
            topic_focus="Creator Monetization",
        )
        clip = ViralClip(
            id=9101,
            project_id=8101,
            start_time=0.0,
            end_time=4.0,
            duration=4.0,
            hook_headline="OLD GENERIC HOOK",
            selected_title="How Compound Momentum Builds Channels",
            transcript_json=json.dumps(words),
        )
        s.add(proj)
        s.add(clip)
        s.commit()

        hook_data = generate_clip_hooks_endpoint(project_id=8101, clip_id=9101, session=s)
        variants = hook_data.get("variants") or []
        assert len(variants) == 3
        variant_types = [v["variant_type"] for v in variants]
        assert len(set(variant_types)) == 3
        for v in variants:
            assert isinstance(v["hook_headline"], str) and len(v["hook_headline"].strip()) > 3
            assert v.get("predicted_retention_lift")
            assert v.get("rationale")

        copy_data = generate_clip_social_copy_endpoint(project_id=8101, clip_id=9101, session=s)
        platforms = copy_data.get("platforms") or {}
        for key in ("youtube_shorts", "tiktok", "instagram_reels", "x_twitter"):
            assert key in platforms
            entry = platforms[key]
            assert entry["title"]
            assert entry["description"]
            assert entry["pinned_comment"]
            assert len(entry["hashtags"]) == 5
            assert all(str(tag).startswith("#") for tag in entry["hashtags"])

        updated_clip = update_clip(
            clip_id=9101,
            req=UpdateClipRequest(highlight_power_words=True),
            session=s,
        )
        assert updated_clip["status"] == "updated"
        refreshed_clip = s.get(ViralClip, 9101)
        assert refreshed_clip is not None and refreshed_clip.highlight_power_words is True

    # Verify Gemini JSON normalization path for both services
    with patch("app.services.hook_generator._invoke_gemini_json_prompt") as mock_hook_llm:
        mock_hook_llm.return_value = {
            "variants": [
                {"variant_type": "Curiosity Gap", "hook_headline": "why nobody talks about this", "predicted_retention_lift": "+22%", "rationale": "Open curiosity loop."},
                {"variant_type": "Bold / Contrarian Claim", "hook_headline": "stop copying top creators", "predicted_retention_lift": "+17%", "rationale": "Strong pattern interrupt."},
                {"variant_type": "High-Stakes Action", "hook_headline": "one mistake costs millions", "predicted_retention_lift": "+15%", "rationale": "Urgent stakes."},
            ]
        }
        g_hooks = generate_hook_variants(words, current_headline="OLD")
        assert g_hooks["source"] == "gemini"
        assert g_hooks["variants"][0]["hook_headline"] == "WHY NOBODY TALKS ABOUT THIS"

    with patch("app.services.social_copywriter._invoke_gemini_json_prompt") as mock_copy_llm:
        mock_copy_llm.return_value = {
            "platforms": {
                "youtube_shorts": {"title": "Momentum Secret #Shorts", "description": "Full breakdown.", "pinned_comment": "Agree?", "hashtags": ["shorts", "#viral"]},
                "tiktok": {"title": "Watch til end", "description": "Crazy truth.", "pinned_comment": "Part 2?", "hashtags": ["#fyp"]},
                "instagram_reels": {"title": "Save this Reel", "description": "Bookmark now.", "pinned_comment": "Share!", "hashtags": ["#reels"]},
                "x_twitter": {"title": "Thread below", "description": "Watch 30s.", "pinned_comment": "Thoughts?", "hashtags": ["#growth"]},
            }
        }
        g_copy = generate_multi_platform_copy(words, hook_headline="SECRET")
        assert g_copy["source"] == "gemini"
        assert len(g_copy["platforms"]["youtube_shorts"]["hashtags"]) == 5
        assert all(t.startswith("#") for t in g_copy["platforms"]["youtube_shorts"]["hashtags"])


def test_smart_emoji_120_keywords_and_power_words_pixel_diff():
    """
    Phase B Feature 2:
    - EMOJI_KEYWORDS contains >= 120 keywords across categories.
    - highlight_power_words=True accents power words ('never', 'secret', 'money') in highlight_color
      with 1.08x pop in All-at-once mode, and emoji_enabled=True renders color/accent badges.
    """
    assert len(EMOJI_KEYWORDS) >= 120
    words = [
        {"word": "Never", "start": 0.0, "end": 0.5},
        {"word": "share", "start": 0.55, "end": 0.9},
        {"word": "secret", "start": 0.95, "end": 1.4},
        {"word": "money", "start": 1.45, "end": 1.9},
    ]

    img_plain = render_caption_frame(
        t=0.65,
        width=720,
        height=1280,
        words=words,
        style_preset="Minimal",
        base_color="#FFFFFF",
        highlight_color="#FFD700",
        word_by_word=False,
        auto_emoji=False,
        highlight_power_words=False,
        clip_start_time=0.0,
        clip_duration=2.0,
    )
    img_power_emoji = render_caption_frame(
        t=0.65,
        width=720,
        height=1280,
        words=words,
        style_preset="Minimal",
        base_color="#FFFFFF",
        highlight_color="#FFD700",
        word_by_word=False,
        auto_emoji=True,
        highlight_power_words=True,
        clip_start_time=0.0,
        clip_duration=2.0,
    )

    assert img_plain.size == (720, 1280)
    assert img_power_emoji.size == (720, 1280)
    assert list(img_plain.getdata()) != list(img_power_emoji.getdata())

    gold_pixels = sum(
        1 for (r, g, b, a) in img_power_emoji.getdata()
        if a > 128 and r >= 220 and 180 <= g <= 235 and b <= 60
    )
    plain_gold_pixels = sum(
        1 for (r, g, b, a) in img_plain.getdata()
        if a > 128 and r >= 220 and 180 <= g <= 235 and b <= 60
    )
    assert gold_pixels > plain_gold_pixels + 40


def test_waveform_heatmap_rms_speech_density_and_silence(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """
    Phase B Feature 3:
    - get_clip_waveform returns rms, speech_density, and is_silence arrays aligned to bins.
    - Loud speech segment scores high rms + speech_density; quiet trailing segment flags is_silence.
    """
    test_engine, storage_dir = _setup_isolated_db(tmp_path, monkeypatch)
    proj_dir = storage_dir / "projects" / "8201"
    wav_path = _create_half_tone_half_silence_wav(proj_dir / "audio_16k.wav", tone_sec=3.0, silence_sec=3.0)

    words = [
        {"word": "Action", "start": 0.2, "end": 0.7},
        {"word": "packed", "start": 0.8, "end": 1.3},
        {"word": "opening", "start": 1.4, "end": 2.0},
        {"word": "hook", "start": 2.1, "end": 2.7},
    ]
    with Session(test_engine) as s:
        proj = Project(
            id=8201,
            title="Heatmap Audio Test",
            source_path=str(wav_path),
            duration=6.0,
        )
        clip = ViralClip(
            id=9201,
            project_id=8201,
            start_time=0.0,
            end_time=6.0,
            duration=6.0,
            transcript_json=json.dumps(words),
        )
        s.add(proj)
        s.add(clip)
        s.commit()

        data = get_clip_waveform(project_id=8201, clip_id=9201, bins=60, session=s)

    bins = data["bins"]
    rms = data["rms"]
    density = data["speech_density"]
    is_silence = data["is_silence"]

    assert len(bins) == 60
    assert len(rms) == 60
    assert len(density) == 60
    assert len(is_silence) == 60

    first_half_rms = sum(rms[:25]) / 25.0
    second_half_rms = sum(rms[35:]) / 25.0
    assert first_half_rms > second_half_rms + 0.3

    assert sum(density[:25]) > sum(density[35:])
    assert any(is_silence[35:])
    assert not any(is_silence[5:22])


def test_publish_pack_zip_multi_aspect_cover_srt_and_copy(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """
    Phase B Feature 4:
    - export_publish_pack_endpoint bundles all 6 files:
      01_Shorts_TikTok_9x16.mp4 (1080x1920), 02_Instagram_Feed_4x5.mp4 (1080x1350),
      03_YouTube_X_16x9.mp4 (1920x1080), 04_Thumbnail_Cover.jpg, 05_Captions.srt, 06_Social_Copy.txt.
    """
    test_engine, _ = _setup_isolated_db(tmp_path, monkeypatch)
    src_mp4 = _create_synthetic_av_mp4(tmp_path / "spaced dir" / "master_クリップ.mp4", duration_sec=3.0)

    words = [
        {"word": "Breaking", "start": 0.2, "end": 0.7},
        {"word": "viral", "start": 0.8, "end": 1.4},
        {"word": "secret", "start": 1.5, "end": 2.1},
    ]
    with Session(test_engine) as s:
        proj = Project(
            id=8301,
            title="Publish Pack Export Project",
            source_path=str(src_mp4),
            duration=3.0,
            hardware_mode="cpu",
        )
        clip = ViralClip(
            id=9301,
            project_id=8301,
            start_time=0.0,
            end_time=2.5,
            duration=2.5,
            hook_headline="SECRET REVEALED NOW",
            show_hook_banner=True,
            selected_title="The Secret Everyone Missed",
            burn_captions=True,
            highlight_power_words=True,
            hardware_mode="cpu",
            transcript_json=json.dumps(words),
        )
        s.add(proj)
        s.add(clip)
        s.commit()

        resp = export_publish_pack_endpoint(
            project_id=8301,
            clip_id=9301,
            req={"quality_mode": "fast_preview"},
            session=s,
        )

    zip_path = Path(resp.path)
    assert zip_path.exists()
    try:
        with zipfile.ZipFile(zip_path, "r") as zf:
            names = set(zf.namelist())
            expected = {
                "01_Shorts_TikTok_9x16.mp4",
                "02_Instagram_Feed_4x5.mp4",
                "03_YouTube_X_16x9.mp4",
                "04_Thumbnail_Cover.jpg",
                "05_Captions.srt",
                "06_Social_Copy.txt",
            }
            assert expected.issubset(names), f"Missing pack files: {expected - names}"

            with tempfile.TemporaryDirectory(prefix="lh_verify_pack_") as vdir:
                vpath = Path(vdir)
                for fname, exp_w, exp_h in (
                    ("01_Shorts_TikTok_9x16.mp4", 1080, 1920),
                    ("02_Instagram_Feed_4x5.mp4", 1080, 1350),
                    ("03_YouTube_X_16x9.mp4", 1920, 1080),
                ):
                    out_f = vpath / fname
                    out_f.write_bytes(zf.read(fname))
                    meta = probe_video(str(out_f))
                    assert meta["width"] == exp_w and meta["height"] == exp_h, f"{fname}: {meta}"
                    assert meta["duration"] >= 1.5

            cover_bytes = zf.read("04_Thumbnail_Cover.jpg")
            import io
            with Image.open(io.BytesIO(cover_bytes)) as cover_img:
                assert cover_img.format == "JPEG"
                assert cover_img.size[0] == 1080 and cover_img.size[1] == 1920

            srt_text = zf.read("05_Captions.srt").decode("utf-8")
            assert "-->" in srt_text
            assert "Breaking" in srt_text and "secret" in srt_text

            copy_text = zf.read("06_Social_Copy.txt").decode("utf-8")
            for heading in ("[YOUTUBE SHORTS]", "[TIKTOK]", "[INSTAGRAM REELS]", "[X (TWITTER)]"):
                assert heading in copy_text
    finally:
        zip_path.unlink(missing_ok=True)
