import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest
from fastapi import HTTPException
from sqlmodel import Session, SQLModel, create_engine

import app.database as app_db
import app.main as main_mod
from app.config import settings
from app.models import Project, PublishYouTubeRequest, ViralClip
from app.services import youtube_publisher


def _drive_coro(coro):
    try:
        while True:
            coro.send(None)
    except StopIteration as stop:
        return stop.value


def _setup_isolated_env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    storage_dir = tmp_path / "yt_storage"
    for sub in ("masters", "renders", "previews", "assets", "thumbnails", "projects"):
        (storage_dir / sub).mkdir(parents=True, exist_ok=True)

    monkeypatch.setattr(settings, "STORAGE_DIR", str(storage_dir))
    db_path = tmp_path / "yt_test.db"
    test_engine = create_engine(f"sqlite:///{db_path}", connect_args={"check_same_thread": False})
    SQLModel.metadata.create_all(test_engine)
    monkeypatch.setattr(app_db, "engine", test_engine)

    # Ensure no cached global/real YouTube credentials can leak into tests
    monkeypatch.setattr(settings, "YOUTUBE_CLIENT_SECRETS_FILE", str(storage_dir / "youtube_client_secrets.json"))
    monkeypatch.setattr(settings, "YOUTUBE_TOKEN_FILE", str(storage_dir / "youtube_token.json"))

    rendered_mp4 = storage_dir / "renders" / "clip_1_ready.mp4"
    rendered_mp4.write_bytes(b"\x00\x00\x00\x18ftypmp42\x00\x00\x00\x00mp42isom")

    with Session(test_engine) as session:
        proj = Project(title="YouTube Auth Guard Test", source_path=str(rendered_mp4))
        session.add(proj)
        session.commit()
        session.refresh(proj)

        clip = ViralClip(
            project_id=proj.id,
            start_time=0.0,
            end_time=8.0,
            duration=8.0,
            selected_title="Sample Clip #Shorts",
            rendered_video_path=str(rendered_mp4),
            rendered_path=str(rendered_mp4),
            status="ready",
            render_status="completed",
        )
        session.add(clip)
        session.commit()
        session.refresh(clip)
        proj_id, clip_id = proj.id, clip.id

    return test_engine, storage_dir, proj_id, clip_id, rendered_mp4


def test_youtube_routes_registered_on_fastapi_app():
    route_paths = {getattr(r, "path", "") for r in main_mod.app.routes}
    for expected in (
        "/api/youtube/auth/status",
        "/api/youtube/auth/client-secrets",
        "/api/youtube/auth/connect",
        "/api/youtube/auth/disconnect",
        "/api/clips/{project_id}/{clip_id}/publish/youtube",
    ):
        assert expected in route_paths


def test_publish_youtube_returns_401_when_not_linked(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    test_engine, storage_dir, proj_id, clip_id, rendered_mp4 = _setup_isolated_env(tmp_path, monkeypatch)

    st = main_mod.youtube_auth_status_endpoint()
    assert st == {
        "client_configured": False,
        "connected": False,
        "channel_title": None,
        "channel_id": None,
    }

    with Session(test_engine) as session:
        # Scoped endpoint MUST fail closed with HTTP 401 when no YouTube token is linked
        with pytest.raises(HTTPException) as exc_scoped:
            main_mod.publish_clip_to_youtube_scoped(
                project_id=proj_id,
                clip_id=clip_id,
                req=PublishYouTubeRequest(
                    title="Test Upload",
                    description="Testing",
                    privacy_status="private",
                ),
                session=session,
            )
        assert exc_scoped.value.status_code == 401
        assert "not linked" in str(exc_scoped.value.detail).lower()

        # Legacy alias MUST also raise HTTP 401 (never fake 200 Published)
        with pytest.raises(HTTPException) as exc_legacy:
            main_mod.publish_to_youtube_generic(
                req=PublishYouTubeRequest(title="Test Upload", description="Testing", privacy_status="private"),
                clip_id=clip_id,
                session=session,
            )
        assert exc_legacy.value.status_code == 401

        # Corrupted / invalid token must still fail closed with HTTP 401
        (storage_dir / "youtube_token.json").write_text("{corrupt-json", encoding="utf-8")
        assert youtube_publisher.get_youtube_status()["connected"] is False
        with pytest.raises(HTTPException) as exc_corrupt:
            main_mod.publish_clip_to_youtube_scoped(
                project_id=proj_id,
                clip_id=clip_id,
                req=PublishYouTubeRequest(title="Corrupt Token Test"),
                session=session,
            )
        assert exc_corrupt.value.status_code == 401

    with pytest.raises(youtube_publisher.YouTubeAuthError):
        youtube_publisher.upload_clip_to_youtube(
            video_path=str(rendered_mp4),
            title="Direct Guard Check",
        )

    with Session(test_engine) as s:
        clip_row = s.get(ViralClip, clip_id)
        assert not clip_row.youtube_url
        assert not clip_row.youtube_video_id


def test_client_secrets_upload_authenticated_upload_and_disconnect(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
):
    test_engine, storage_dir, proj_id, clip_id, rendered_mp4 = _setup_isolated_env(tmp_path, monkeypatch)

    class _DummyRequest:
        async def body(self) -> bytes:
            return b""

    class _MemoryUpload:
        def __init__(self, payload: bytes):
            self._payload = payload

        async def read(self) -> bytes:
            return self._payload

    # Reject invalid / service-account JSON payloads with 400
    bad_upload = _MemoryUpload(b'{"type": "service_account"}')
    with pytest.raises(HTTPException) as exc_bad:
        _drive_coro(main_mod.upload_youtube_client_secrets_endpoint(_DummyRequest(), file=bad_upload))
    assert exc_bad.value.status_code == 400

    valid_secrets = json.dumps({
        "installed": {
            "client_id": "123456.apps.googleusercontent.com",
            "client_secret": "test-secret-xyz",
            "auth_uri": "https://accounts.google.com/o/oauth2/auth",
            "token_uri": "https://oauth2.googleapis.com/token",
        }
    }).encode("utf-8")
    good_upload = _MemoryUpload(valid_secrets)
    sec_res = _drive_coro(main_mod.upload_youtube_client_secrets_endpoint(_DummyRequest(), file=good_upload))
    assert sec_res["client_configured"] is True
    assert sec_res["connected"] is False
    assert (storage_dir / "youtube_client_secrets.json").exists()

    # Simulate connected channel with valid credentials and mocked Google API v3 client
    fake_creds = SimpleNamespace(valid=True, expired=False, refresh_token="refresh-123")
    (storage_dir / "youtube_token.json").write_text('{"token":"abc"}', encoding="utf-8")
    youtube_publisher._save_channel_meta("Lost Heaven Official", "UC_LOST_HEAVEN_99")
    monkeypatch.setattr(youtube_publisher, "get_youtube_credentials", lambda: fake_creds)

    captured = {}

    class FakeMediaFileUpload:
        def __init__(self, filename, chunksize=-1, resumable=False, mimetype=None):
            captured["media"] = {
                "filename": filename,
                "chunksize": chunksize,
                "resumable": resumable,
                "mimetype": mimetype,
            }

    class FakeInsertRequest:
        def execute(self):
            return {"id": "ytRealVid_77"}

    class FakeVideosResource:
        def insert(self, part=None, body=None, media_body=None):
            captured["insert"] = {"part": part, "body": body, "media_body": media_body}
            return FakeInsertRequest()

    class FakeChannelsResource:
        def list(self, part=None, mine=False):
            req = MagicMock()
            req.execute.return_value = {
                "items": [{"id": "UC_LOST_HEAVEN_99", "snippet": {"title": "Lost Heaven Official"}}]
            }
            return req

    class FakeYouTubeClient:
        def videos(self):
            return FakeVideosResource()

        def channels(self):
            return FakeChannelsResource()

    monkeypatch.setattr(
        youtube_publisher,
        "_require_google_api_deps",
        lambda: (lambda *a, **kw: FakeYouTubeClient(), FakeMediaFileUpload, MagicMock(), MagicMock(), MagicMock()),
    )

    st_conn = main_mod.youtube_auth_status_endpoint()
    assert st_conn == {
        "client_configured": True,
        "connected": True,
        "channel_title": "Lost Heaven Official",
        "channel_id": "UC_LOST_HEAVEN_99",
    }

    dirty_title = "<script>alert(1)</script> " + ("X" * 130)
    huge_desc = "Intro <tag> " + ("A" * 5200)
    with Session(test_engine) as session:
        data = main_mod.publish_clip_to_youtube_scoped(
            project_id=proj_id,
            clip_id=clip_id,
            req=PublishYouTubeRequest(
                title=dirty_title,
                description=huge_desc,
                tags=["<Shorts>", "LostHeaven", "Gameplay"],
                privacy_status="unlisted",
            ),
            session=session,
        )

    assert data["video_id"] == "ytRealVid_77"
    assert data["url"] == "https://www.youtube.com/shorts/ytRealVid_77"
    assert data["privacy_status"] == "unlisted"
    assert data["channel_title"] == "Lost Heaven Official"

    # Verify metadata sanitization + resumable upload arguments
    insert_body = captured["insert"]["body"]
    assert captured["insert"]["part"] == "snippet,status"
    assert "<" not in insert_body["snippet"]["title"] and ">" not in insert_body["snippet"]["title"]
    assert len(insert_body["snippet"]["title"]) <= 100
    assert len(insert_body["snippet"]["description"].encode("utf-8")) <= 5000
    assert "<" not in insert_body["snippet"]["description"]
    assert insert_body["status"]["privacyStatus"] == "unlisted"
    assert captured["media"]["chunksize"] == 1024 * 1024
    assert captured["media"]["resumable"] is True

    with Session(test_engine) as s:
        clip_row = s.get(ViralClip, clip_id)
        assert clip_row.youtube_video_id == "ytRealVid_77"
        assert clip_row.youtube_url == "https://www.youtube.com/shorts/ytRealVid_77"

    # Disconnect revokes local token while preserving client_secrets.json
    main_mod.disconnect_youtube_endpoint()
    assert not (storage_dir / "youtube_token.json").exists()
    assert (storage_dir / "youtube_client_secrets.json").exists()
