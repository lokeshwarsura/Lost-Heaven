"""
Tests for v18 Publish & Multi-Platform Social Studio rework:
  1. POST /api/clips/{project_id}/{clip_id}/generate-social-copy in both Offline AI and Gemini AI modes.
  2. GET /api/clips/{project_id}/{clip_id}/social-pack auto-renders MP4 if missing and bundles all 6 files.
  3. POST /api/youtube/auth/credentials saves valid Desktop OAuth JSON & upload forwards category_id/tags.
"""

import json
import re
import subprocess
import zipfile
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest
from fastapi import HTTPException
from PIL import Image
from sqlmodel import Session, SQLModel, create_engine

import app.database as app_db
import app.main as main_mod
from app.config import settings
from app.models import Project, PublishYouTubeRequest, ViralClip, YouTubeCredentialsRequest
from app.services import youtube_publisher
from app.services.proxy import get_ffmpeg_cmd


def _setup_isolated_env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    storage_dir = tmp_path / "social_studio_storage"
    for sub in ("masters", "renders", "previews", "assets", "thumbnails", "projects"):
        (storage_dir / sub).mkdir(parents=True, exist_ok=True)

    monkeypatch.setattr(settings, "STORAGE_DIR", str(storage_dir))
    monkeypatch.setattr(settings, "YOUTUBE_CLIENT_SECRETS_FILE", str(storage_dir / "youtube_client_secrets.json"))
    monkeypatch.setattr(settings, "YOUTUBE_TOKEN_FILE", str(storage_dir / "youtube_token.json"))

    db_path = tmp_path / "v18_social.db"
    test_engine = create_engine(f"sqlite:///{db_path}", connect_args={"check_same_thread": False})
    SQLModel.metadata.create_all(test_engine)
    monkeypatch.setattr(app_db, "engine", test_engine)
    return test_engine, storage_dir


def _make_sample_av_mp4(out_path: Path, duration: float = 2.5) -> Path:
    ffmpeg_bin = get_ffmpeg_cmd()
    cmd = [
        ffmpeg_bin,
        "-y",
        "-f", "lavfi",
        "-i", f"color=c=0x1D4ED8:s=640x360:r=15:d={duration:.2f}",
        "-f", "lavfi",
        "-i", "sine=frequency=440:sample_rate=48000",
        "-t", f"{duration:.2f}",
        "-c:v", "libx264",
        "-preset", "ultrafast",
        "-pix_fmt", "yuv420p",
        "-c:a", "aac",
        "-shortest",
        str(out_path),
    ]
    res = subprocess.run(cmd, capture_output=True, text=True, timeout=45)
    assert res.returncode == 0, f"Sample MP4 build failed: {res.stderr[-300:]}"
    return out_path


def test_generate_social_copy_offline_and_gemini_modes(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
):
    test_engine, _ = _setup_isolated_env(tmp_path, monkeypatch)
    monkeypatch.setattr(main_mod, "_resolve_gemini_runtime", lambda: (None, None))

    words = [
        {"word": "Insane", "start": 0.1, "end": 0.4},
        {"word": "Valorant", "start": 0.4, "end": 0.8},
        {"word": "clutch", "start": 0.8, "end": 1.2},
        {"word": "strategy", "start": 1.2, "end": 1.7},
        {"word": "guide", "start": 1.7, "end": 2.1},
    ]
    with Session(test_engine) as s:
        proj = Project(title="Gaming Clip Project", source_path=str(tmp_path / "dummy.mp4"), duration=12.0)
        s.add(proj)
        s.commit()
        s.refresh(proj)

        clip = ViralClip(
            project_id=proj.id,
            start_time=0.0,
            end_time=4.0,
            duration=4.0,
            hook_headline="UNBELIEVABLE CLUTCH PLAY",
            selected_title="How This Clutch Strategy Won Ranked",
            viral_rationale="Fast opening payoff with high mechanical tension.",
            emotional_score=88,
            transcript_json=json.dumps(words),
        )
        s.add(clip)
        s.commit()
        s.refresh(clip)

        # 1. Offline AI Mode (zero Gemini API key)
        off_res = main_mod.generate_clip_social_copy_endpoint(proj.id, clip.id, session=s)
        assert off_res["mode"] == "offline"
        assert off_res["source"] == "offline_nlp"

        for k in ("youtube", "instagram", "tiktok", "x", "platforms"):
            assert k in off_res

        yt = off_res["youtube"]
        assert len(yt["title"]) <= 100 and "#shorts" in yt["title"].lower()
        assert yt["description"]
        assert isinstance(yt["tags"], list) and len(yt["tags"]) >= 5
        assert yt["pinned_comment"]
        assert yt["category_id"] == "20"  # Gaming keywords detected

        ig = off_res["instagram"]
        assert len(ig["caption"]) <= 2200
        assert len(re.findall(r"#\w+", ig["caption"])) >= 8
        assert ig["pinned_comment"]

        tt = off_res["tiktok"]
        assert len(tt["caption"]) <= 2200
        assert 5 <= len(re.findall(r"#\w+", tt["caption"])) <= 8
        assert tt["pinned_comment"]

        xp = off_res["x"]
        assert len(xp["post_text"]) <= 280
        assert 2 <= len(re.findall(r"#\w+", xp["post_text"])) <= 4

        # 2. Gemini AI Mode (mocked contextual LLM payload)
        monkeypatch.setattr(main_mod, "_resolve_gemini_runtime", lambda: ("fake-key", "gemini-2.5-flash"))
        monkeypatch.setattr(
            "app.services.social_copywriter._invoke_gemini_json_prompt",
            lambda *a, **kw: {
                "youtube": {
                    "title": "1v4 Ranked Comeback Secret #Shorts",
                    "description": "Breakdown of every swing in that 1v4 round.",
                    "tags": ["Valorant", "Clutch", "Shorts", "FPS"],
                    "pinned_comment": "Would you peek here?",
                    "category_id": "20",
                },
                "instagram": {
                    "title": "1v4 Ranked Comeback",
                    "caption": "Never rush this angle without a flash.",
                    "hashtags": ["#valorant", "#gaming", "#reels", "#clutch", "#fps", "#gamer", "#tips", "#ranked"],
                    "pinned_comment": "Tag your duo!",
                },
                "tiktok": {
                    "title": "Wait for the last shot",
                    "caption": "Watch the mini-map timing on shot 3.",
                    "hashtags": ["#fyp", "#valorant", "#gaming", "#clutch", "#viral"],
                    "pinned_comment": "Sensitivity settings?",
                },
                "x": {
                    "title": "1v4 Breakdown",
                    "post_text": "Most players panic after pick #2. Wait 0.4s before swinging the crossfire.",
                    "hashtags": ["#Valorant", "#Gaming"],
                },
            },
        )
        gem_res = main_mod.generate_clip_social_copy_endpoint(proj.id, clip.id, session=s)
        assert gem_res["mode"] == "gemini"
        assert gem_res["source"] == "gemini"
        assert gem_res["youtube"]["title"].startswith("1v4 Ranked")
        assert len(gem_res["x"]["post_text"]) <= 280


def test_social_pack_zip_download_and_auto_render(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
):
    test_engine, _ = _setup_isolated_env(tmp_path, monkeypatch)
    master_mp4 = _make_sample_av_mp4(tmp_path / "master_pack_src.mp4", duration=2.5)
    monkeypatch.setattr(main_mod, "_resolve_gemini_runtime", lambda: (None, None))

    words = [
        {"word": "Breaking", "start": 0.2, "end": 0.7},
        {"word": "viral", "start": 0.8, "end": 1.3},
        {"word": "editing", "start": 1.4, "end": 1.9},
    ]
    with Session(test_engine) as s:
        proj = Project(
            title="Pack Test Project",
            source_path=str(master_mp4),
            duration=2.5,
            hardware_mode="cpu",
        )
        s.add(proj)
        s.commit()
        s.refresh(proj)

        clip = ViralClip(
            project_id=proj.id,
            start_time=0.0,
            end_time=2.2,
            duration=2.2,
            selected_title="Viral Editing Trick #Shorts",
            hook_headline="EDIT FASTER NOW",
            burn_captions=True,
            hardware_mode="cpu",
            rendered_video_path=None,  # Intentionally unrendered -> MUST auto-render cleanly
            transcript_json=json.dumps(words),
        )
        s.add(clip)
        s.commit()
        s.refresh(clip)

        resp = main_mod.download_social_pack_endpoint(proj.id, clip.id, session=s)
        assert resp.filename.endswith("_social_pack.zip")
        zip_path = Path(resp.path)
        assert zip_path.exists()

        try:
            with zipfile.ZipFile(zip_path, "r") as zf:
                names = set(zf.namelist())
                mp4_entries = [n for n in names if n.endswith("_final.mp4")]
                assert len(mp4_entries) == 1, f"Expected <clip_slug>_final.mp4, got {names}"
                assert zf.getinfo(mp4_entries[0]).file_size > 1000
                for req_name in (
                    "cover_thumbnail.png",
                    "captions.srt",
                    "youtube_shorts.txt",
                    "instagram_reels.txt",
                    "tiktok.txt",
                ):
                    assert req_name in names, f"Missing {req_name} in {names}"

                srt_txt = zf.read("captions.srt").decode("utf-8")
                assert "Breaking" in srt_txt
                yt_txt = zf.read("youtube_shorts.txt").decode("utf-8")
                assert "Comma-Separated Tags:" in yt_txt and "Pinned Comment:" in yt_txt
                ig_txt = zf.read("instagram_reels.txt").decode("utf-8")
                assert "Pinned Comment:" in ig_txt
                tt_txt = zf.read("tiktok.txt").decode("utf-8")
                assert "Pinned Comment:" in tt_txt

                cov_bytes = zf.read("cover_thumbnail.png")
                cov_tmp = tmp_path / "cov_check.png"
                cov_tmp.write_bytes(cov_bytes)
                with Image.open(cov_tmp) as im:
                    assert im.width > 0 and im.height > 0
        finally:
            zip_path.unlink(missing_ok=True)


def test_youtube_oauth_credentials_and_category_publish(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
):
    test_engine, storage_dir = _setup_isolated_env(tmp_path, monkeypatch)

    # Empty credentials reject with HTTP 400
    with pytest.raises(HTTPException) as exc:
        main_mod.save_youtube_oauth_credentials_endpoint(
            YouTubeCredentialsRequest(client_id="  ", client_secret="abc")
        )
    assert exc.value.status_code == 400

    # Valid Client ID + Secret writes standard Desktop App JSON
    st = main_mod.save_youtube_oauth_credentials_endpoint(
        YouTubeCredentialsRequest(
            client_id="987654321-test.apps.googleusercontent.com",
            client_secret="GOCSPX-test-secret-key",
        )
    )
    assert st["client_configured"] is True
    assert st["connected"] is False

    sec_file = storage_dir / "youtube_client_secrets.json"
    assert sec_file.exists()
    payload = json.loads(sec_file.read_text(encoding="utf-8"))
    assert payload["installed"]["client_id"] == "987654321-test.apps.googleusercontent.com"
    assert payload["installed"]["client_secret"] == "GOCSPX-test-secret-key"

    # Verify category_id & tags are passed through to resumable YouTube upload
    rendered_mp4 = storage_dir / "renders" / "ready_v18.mp4"
    rendered_mp4.write_bytes(b"\x00\x00\x00\x18ftypmp42\x00\x00\x00\x00mp42isom")

    fake_creds = SimpleNamespace(valid=True, expired=False, refresh_token="r-123")
    monkeypatch.setattr(youtube_publisher, "get_youtube_credentials", lambda: fake_creds)
    youtube_publisher._save_channel_meta("Lost Heaven Studio", "UC_LH_V18")
    captured = {}

    class FakeMedia:
        def __init__(self, filename, chunksize=-1, resumable=False, mimetype=None):
            captured["media"] = filename

    class FakeInsertReq:
        def execute(self):
            return {"id": "ytCatTest_20"}

    class FakeVideosRes:
        def insert(self, part=None, body=None, media_body=None):
            captured["body"] = body
            return FakeInsertReq()

    class FakeYtClient:
        def videos(self):
            return FakeVideosRes()

    monkeypatch.setattr(
        youtube_publisher,
        "_require_google_api_deps",
        lambda: (lambda *a, **kw: FakeYtClient(), FakeMedia, MagicMock(), MagicMock(), MagicMock()),
    )

    with Session(test_engine) as s:
        proj = Project(title="YT Cred Test", source_path=str(rendered_mp4))
        s.add(proj)
        s.commit()
        s.refresh(proj)
        clip = ViralClip(
            project_id=proj.id,
            start_time=0.0,
            end_time=6.0,
            duration=6.0,
            rendered_video_path=str(rendered_mp4),
            rendered_path=str(rendered_mp4),
            status="ready",
            render_status="completed",
        )
        s.add(clip)
        s.commit()
        s.refresh(clip)

        pub_res = main_mod.publish_clip_to_youtube_scoped(
            project_id=proj.id,
            clip_id=clip.id,
            req=PublishYouTubeRequest(
                title="Clutch Gameplay #Shorts",
                description="Full guide",
                tags=["Gaming", "Clutch", "Shorts"],
                privacy_status="unlisted",
                category_id="20",
            ),
            session=s,
        )
        assert pub_res["video_id"] == "ytCatTest_20"
        assert captured["body"]["snippet"]["categoryId"] == "20"
        assert "Clutch" in captured["body"]["snippet"]["tags"]
