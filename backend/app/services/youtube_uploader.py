import os
import json
import logging
from pathlib import Path
from typing import Dict, Any, Optional

from app.config import settings

logger = logging.getLogger(__name__)

SCOPES = [
    "https://www.googleapis.com/auth/youtube.upload",
    "https://www.googleapis.com/auth/youtube.readonly"
]

import os
import json
import logging
from pathlib import Path
from typing import Dict, Any, Optional

from app.config import settings

logger = logging.getLogger(__name__)

SCOPES = [
    "https://www.googleapis.com/auth/youtube.upload",
    "https://www.googleapis.com/auth/youtube.readonly"
]

def get_client_secrets_path() -> Optional[Path]:
    """Find the YouTube client_secrets.json file in backend or root directory."""
    candidates = [
        Path(settings.YOUTUBE_CLIENT_SECRETS_FILE),
        Path("backend/client_secrets.json"),
        Path("client_secrets.json")
    ]
    for c in candidates:
        if c.exists() and c.is_file():
            return c.resolve()
    return None

def get_youtube_credentials():
    """
    Retrieve existing stored credentials or authenticate via OAuth2 InstalledAppFlow.
    Raises FileNotFoundError if client_secrets.json is missing.
    """
    token_path = Path(settings.YOUTUBE_TOKEN_FILE)

    if token_path.exists():
        try:
            from google.oauth2.credentials import Credentials
            from google.auth.transport.requests import Request

            creds = Credentials.from_authorized_user_file(str(token_path), SCOPES)
            if creds and creds.expired and creds.refresh_token:
                creds.refresh(Request())
                with open(token_path, "w") as token:
                    token.write(creds.to_json())
            if creds and creds.valid:
                return creds
        except Exception as e:
            logger.warning(f"Error refreshing stored YouTube credentials: {e}")

    secrets_path = get_client_secrets_path()
    if not secrets_path:
        raise FileNotFoundError(
            "YouTube OAuth client secrets file 'backend/client_secrets.json' not found. "
            "Please download your OAuth 2.0 Client IDs (Desktop app) JSON from Google Cloud Console "
            "and save it to 'backend/client_secrets.json' to authenticate YouTube uploads."
        )

    try:
        from google_auth_oauthlib.flow import InstalledAppFlow
        flow = InstalledAppFlow.from_client_secrets_file(str(secrets_path), SCOPES)
        creds = flow.run_local_server(port=0)
        with open(token_path, "w") as token:
            token.write(creds.to_json())
        return creds
    except Exception as e:
        logger.error(f"Failed to authenticate YouTube OAuth2 flow: {e}")
        raise RuntimeError(f"YouTube authentication failed: {e}")

def is_youtube_authenticated() -> bool:
    """Check if valid YouTube OAuth credentials exist without triggering interactive prompt."""
    token_path = Path(settings.YOUTUBE_TOKEN_FILE)
    if not token_path.exists():
        return False
    try:
        from google.oauth2.credentials import Credentials
        creds = Credentials.from_authorized_user_file(str(token_path), SCOPES)
        return creds is not None and (creds.valid or bool(creds.refresh_token))
    except Exception:
        return False

def publish_video_to_youtube(
    video_path: str,
    title: str,
    description: str,
    privacy_status: str = "private",
    aspect_ratio: str = "9:16",
    tags: Optional[list] = None
) -> Dict[str, Any]:
    """
    Publish a real video to YouTube using YouTube Data API v3 and resumable upload.
    Never returns fake/mock video URLs.
    """
    if not os.path.exists(video_path):
        raise FileNotFoundError(f"Rendered video file not found at: {video_path}")

    creds = get_youtube_credentials()
    if not creds:
        raise RuntimeError("Failed to obtain valid YouTube OAuth credentials.")

    clean_title = title.strip()
    if aspect_ratio == "9:16" and "#Shorts" not in clean_title:
        clean_title = f"{clean_title} #Shorts"

    from googleapiclient.discovery import build
    from googleapiclient.http import MediaFileUpload

    youtube = build("youtube", "v3", credentials=creds)

    clean_tags = tags or ["Viral", "LostHeaven", "Video"]
    if aspect_ratio == "9:16" and "Shorts" not in clean_tags:
        clean_tags.append("Shorts")

    body = {
        "snippet": {
            "title": clean_title[:100],
            "description": description,
            "tags": clean_tags,
            "categoryId": "22"
        },
        "status": {
            "privacyStatus": privacy_status,
            "selfDeclaredMadeForKids": False
        }
    }

    media = MediaFileUpload(video_path, chunksize=-1, resumable=True, mimetype="video/mp4")
    request = youtube.videos().insert(
        part="snippet,status",
        body=body,
        media_body=media
    )

    response = request.execute()
    video_id = response.get("id")
    if not video_id:
        raise RuntimeError(f"YouTube API upload returned no video ID: {response}")

    live_url = f"https://youtube.com/shorts/{video_id}" if aspect_ratio == "9:16" else f"https://youtube.com/watch?v={video_id}"

    return {
        "success": True,
        "video_id": video_id,
        "url": live_url,
        "status": privacy_status,
        "title": clean_title
    }
