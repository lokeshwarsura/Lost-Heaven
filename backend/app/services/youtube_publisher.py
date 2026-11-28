import json
import logging
import os
import re
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from app.config import settings

logger = logging.getLogger(__name__)

SCOPES = [
    "https://www.googleapis.com/auth/youtube.upload",
    "https://www.googleapis.com/auth/youtube.readonly",
]

VALID_PRIVACY_STATUSES = {"private", "unlisted", "public"}


class YouTubeAuthError(Exception):
    """Raised when YouTube OAuth credentials are missing, invalid, or cannot refresh (HTTP 401)."""


class YouTubeConfigError(Exception):
    """Raised when Google Cloud OAuth client secrets are missing or malformed (HTTP 400)."""


def _require_google_api_deps():
    try:
        from googleapiclient.discovery import build
        from googleapiclient.http import MediaFileUpload
        from google.oauth2.credentials import Credentials
        from google.auth.transport.requests import Request
        from google_auth_oauthlib.flow import InstalledAppFlow
    except ImportError as exc:
        raise YouTubeConfigError(
            "Missing YouTube Data API dependencies. Install them with: "
            "pip install google-api-python-client google-auth-oauthlib google-auth-httplib2"
        ) from exc
    return build, MediaFileUpload, Credentials, Request, InstalledAppFlow


def get_client_secrets_file() -> Path:
    return Path(settings.STORAGE_DIR) / "youtube_client_secrets.json"


def get_token_file() -> Path:
    return Path(settings.STORAGE_DIR) / "youtube_token.json"


def get_channel_meta_file() -> Path:
    return Path(settings.STORAGE_DIR) / "youtube_channel.json"


def _is_valid_client_secrets_payload(data: Any) -> bool:
    if not isinstance(data, dict):
        return False
    block = data.get("installed") or data.get("web")
    if not isinstance(block, dict):
        return False
    return bool(str(block.get("client_id") or "").strip() and str(block.get("client_secret") or "").strip())


def is_client_secrets_configured() -> bool:
    secrets_path = get_client_secrets_file()
    if not secrets_path.exists() or not secrets_path.is_file():
        return False
    try:
        payload = json.loads(secrets_path.read_text(encoding="utf-8-sig"))
        return _is_valid_client_secrets_payload(payload)
    except Exception:
        return False


def _load_channel_meta() -> Tuple[Optional[str], Optional[str]]:
    meta_path = get_channel_meta_file()
    if not meta_path.exists():
        return None, None
    try:
        data = json.loads(meta_path.read_text(encoding="utf-8"))
        title = str(data.get("channel_title") or "").strip() or None
        cid = str(data.get("channel_id") or "").strip() or None
        return title, cid
    except Exception:
        return None, None


def _save_channel_meta(channel_title: Optional[str], channel_id: Optional[str]) -> None:
    meta_path = get_channel_meta_file()
    meta_path.parent.mkdir(parents=True, exist_ok=True)
    meta_path.write_text(
        json.dumps(
            {
                "channel_title": channel_title,
                "channel_id": channel_id,
            },
            indent=2,
        ),
        encoding="utf-8",
    )


def _fetch_and_persist_channel_info(creds: Any) -> Tuple[Optional[str], Optional[str]]:
    build, _, _, _, _ = _require_google_api_deps()
    youtube = build("youtube", "v3", credentials=creds)
    resp = youtube.channels().list(part="snippet", mine=True).execute()
    items = (resp or {}).get("items") or []
    if not items:
        return None, None
    first = items[0] or {}
    cid = str(first.get("id") or "").strip() or None
    title = str((first.get("snippet") or {}).get("title") or "").strip() or None
    _save_channel_meta(title, cid)
    return title, cid


def get_youtube_credentials():
    """Retrieve stored OAuth credentials and refresh if needed; never falls back to browser login."""
    token_path = get_token_file()
    if not token_path.exists() or not token_path.is_file():
        return None

    try:
        _, _, Credentials, Request, _ = _require_google_api_deps()
        creds = Credentials.from_authorized_user_file(str(token_path), SCOPES)
        if creds and creds.expired:
            if not creds.refresh_token:
                return None
            creds.refresh(Request())
            token_path.write_text(creds.to_json(), encoding="utf-8")
        return creds if creds and creds.valid else None
    except Exception as exc:
        logger.warning(f"Failed to load or refresh YouTube OAuth token: {exc}")
        return None


def get_youtube_status() -> Dict[str, Any]:
    """Return real YouTube OAuth configuration and channel connection state."""
    client_configured = is_client_secrets_configured()
    creds = get_youtube_credentials()
    if not creds:
        return {
            "client_configured": client_configured,
            "connected": False,
            "channel_title": None,
            "channel_id": None,
        }

    channel_title, channel_id = _load_channel_meta()
    if not channel_title:
        try:
            channel_title, channel_id = _fetch_and_persist_channel_info(creds)
        except Exception as exc:
            logger.debug(f"Could not fetch channel metadata for active token: {exc}")

    return {
        "client_configured": client_configured,
        "connected": True,
        "channel_title": channel_title,
        "channel_id": channel_id,
    }


def is_youtube_authenticated() -> bool:
    return bool(get_youtube_status().get("connected"))


def save_youtube_client_secrets(raw_bytes: bytes) -> Dict[str, Any]:
    """Validate and persist Google Cloud OAuth 2.0 Desktop App client JSON."""
    if not raw_bytes:
        raise YouTubeConfigError("Uploaded OAuth client JSON file is empty.")
    try:
        text = raw_bytes.decode("utf-8-sig")
        payload = json.loads(text)
    except Exception as exc:
        raise YouTubeConfigError(f"Invalid JSON file: {exc}") from exc

    if not _is_valid_client_secrets_payload(payload):
        raise YouTubeConfigError(
            "Invalid Google OAuth Client JSON. Create an OAuth 2.0 Client ID (Desktop app) in "
            "Google Cloud Console -> APIs & Services -> Credentials and upload that JSON file."
        )

    secrets_path = get_client_secrets_file()
    secrets_path.parent.mkdir(parents=True, exist_ok=True)
    secrets_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    return get_youtube_status()


def connect_youtube_account() -> Dict[str, Any]:
    """Launch desktop OAuth 2.0 local server flow, persist token & channel metadata."""
    secrets_path = get_client_secrets_file()
    if not is_client_secrets_configured():
        raise YouTubeConfigError(
            "Google OAuth Client JSON is not configured yet. Upload your Desktop OAuth client_secret JSON first."
        )

    _, _, _, _, InstalledAppFlow = _require_google_api_deps()
    try:
        flow = InstalledAppFlow.from_client_secrets_file(str(secrets_path), scopes=SCOPES)
        creds = flow.run_local_server(port=0, open_browser=True)
    except Exception as exc:
        logger.error(f"YouTube OAuth connection failed: {exc}")
        raise YouTubeConfigError(f"YouTube OAuth connection failed: {exc}") from exc

    token_path = get_token_file()
    token_path.parent.mkdir(parents=True, exist_ok=True)
    token_path.write_text(creds.to_json(), encoding="utf-8")

    try:
        _fetch_and_persist_channel_info(creds)
    except Exception as exc:
        logger.warning(f"Connected YouTube account, but failed to fetch channel title: {exc}")

    return get_youtube_status()


def disconnect_youtube_account() -> Dict[str, Any]:
    """Clear stored YouTube OAuth token and channel cache while preserving client_secrets.json."""
    for target in (get_token_file(), get_channel_meta_file()):
        try:
            target.unlink(missing_ok=True)
        except Exception as exc:
            logger.warning(f"Could not delete {target.name}: {exc}")
    return get_youtube_status()


def _sanitize_title(raw_title: Optional[str]) -> str:
    cleaned = re.sub(r"[<>]", "", str(raw_title or ""))
    cleaned = re.sub(r"\s+", " ", cleaned).strip()
    if not cleaned:
        cleaned = "Lost Heaven Short"
    return cleaned[:100].strip()


def _truncate_utf8_bytes(text: str, max_bytes: int = 5000) -> str:
    raw = text.encode("utf-8")
    if len(raw) <= max_bytes:
        return text
    truncated = raw[:max_bytes]
    return truncated.decode("utf-8", errors="ignore").rstrip()


def _sanitize_description(raw_desc: Optional[str]) -> str:
    cleaned = re.sub(r"[<>]", "", str(raw_desc or "")).strip()
    return _truncate_utf8_bytes(cleaned, max_bytes=5000)


def _sanitize_tags(tags: Optional[List[str]]) -> List[str]:
    source = tags if isinstance(tags, list) else ["Shorts", "LostHeaven"]
    out: List[str] = []
    total_len = 0
    for item in source:
        cleaned = re.sub(r"[<>]", "", str(item or "")).strip()
        if not cleaned or cleaned in out:
            continue
        if total_len + len(cleaned) + 1 > 450:
            break
        out.append(cleaned[:60])
        total_len += len(cleaned) + 1
    return out


def _sanitize_privacy(privacy_status: Optional[str]) -> str:
    val = str(privacy_status or "private").strip().lower()
    return val if val in VALID_PRIVACY_STATUSES else "private"


def upload_clip_to_youtube(
    video_path: str,
    title: str,
    description: str = "",
    tags: Optional[List[str]] = None,
    privacy_status: str = "private",
    aspect_ratio: str = "9:16",
) -> Dict[str, Any]:
    """
    Upload a rendered clip to YouTube via YouTube Data API v3 resumable upload.
    Hard-fails with YouTubeAuthError (401) if no valid OAuth token is linked; never simulates uploads.
    """
    creds = get_youtube_credentials()
    if not creds:
        raise YouTubeAuthError("YouTube account not linked. Connect your channel before uploading.")

    if not video_path or not os.path.exists(video_path):
        raise FileNotFoundError(f"Rendered video file not found at: {video_path}")

    build, MediaFileUpload, _, _, _ = _require_google_api_deps()

    clean_title = _sanitize_title(title)
    clean_desc = _sanitize_description(description)
    clean_tags = _sanitize_tags(tags)
    clean_privacy = _sanitize_privacy(privacy_status)

    body = {
        "snippet": {
            "title": clean_title,
            "description": clean_desc,
            "tags": clean_tags,
            "categoryId": "22",
        },
        "status": {
            "privacyStatus": clean_privacy,
            "selfDeclaredMadeForKids": False,
        },
    }

    youtube = build("youtube", "v3", credentials=creds)
    media = MediaFileUpload(
        video_path,
        chunksize=1024 * 1024,
        resumable=True,
        mimetype="video/mp4",
    )
    request = youtube.videos().insert(
        part="snippet,status",
        body=body,
        media_body=media,
    )
    response = request.execute()
    video_id = (response or {}).get("id")
    if not video_id:
        raise RuntimeError(f"YouTube API upload returned no video ID: {response}")

    channel_title, channel_id = _load_channel_meta()
    if not channel_title:
        try:
            channel_title, channel_id = _fetch_and_persist_channel_info(creds)
        except Exception:
            pass

    shorts_url = f"https://www.youtube.com/shorts/{video_id}"
    return {
        "success": True,
        "video_id": video_id,
        "url": shorts_url,
        "privacy_status": clean_privacy,
        "status": clean_privacy,
        "channel_title": channel_title,
        "channel_id": channel_id,
        "title": clean_title,
    }


def publish_to_youtube_shorts(
    video_path: str,
    title: str,
    description: str,
    privacy_status: str = "private",
    tags: Optional[List[str]] = None,
) -> Dict[str, Any]:
    """Backwards-compatible alias delegating strictly to real upload_clip_to_youtube."""
    return upload_clip_to_youtube(
        video_path=video_path,
        title=title,
        description=description,
        tags=tags,
        privacy_status=privacy_status,
        aspect_ratio="9:16",
    )
