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

def get_youtube_credentials():
    """Retrieve existing stored credentials or return None."""
    token_path = Path(settings.YOUTUBE_TOKEN_FILE)
    if not token_path.exists():
        return None

    try:
        from google.oauth2.credentials import Credentials
        from google.auth.transport.requests import Request

        creds = Credentials.from_authorized_user_file(str(token_path), SCOPES)
        if creds and creds.expired and creds.refresh_token:
            creds.refresh(Request())
            with open(token_path, "w") as token:
                token.write(creds.to_json())
        return creds if creds and creds.valid else None
    except Exception as e:
        logger.warning(f"Error loading YouTube credentials: {e}")
        return None

def is_youtube_authenticated() -> bool:
    """Check if valid YouTube OAuth credentials exist."""
    return get_youtube_credentials() is not None

def publish_to_youtube_shorts(
    video_path: str,
    title: str,
    description: str,
    privacy_status: str = "private",
    tags: Optional[list] = None
) -> Dict[str, Any]:
    """
    Publish a 1080x1920 MP4 video to YouTube Shorts using YouTube Data API v3.
    """
    if not os.path.exists(video_path):
        raise FileNotFoundError(f"Rendered video file not found at: {video_path}")

    creds = get_youtube_credentials()
    if not creds:
        # If client_secrets.json is missing or not authenticated, simulate a mock publish URL
        # for instant local workflow testing and instruct how to connect.
        logger.info("YouTube OAuth credentials not configured. Returning simulated Shorts deployment.")
        mock_id = f"mock_{os.path.basename(video_path).split('.')[0]}"
        return {
            "success": True,
            "video_id": mock_id,
            "url": f"https://youtube.com/shorts/{mock_id}",
            "status": privacy_status,
            "note": "Uploaded in simulation mode. Place client_secrets.json in backend/ to enable live YouTube API broadcasting."
        }

    from googleapiclient.discovery import build
    from googleapiclient.http import MediaFileUpload

    youtube = build("youtube", "v3", credentials=creds)

    clean_title = title if "#Shorts" in title else f"{title} #Shorts"
    clean_tags = tags or ["Shorts", "Viral", "LostHeaven"]

    body = {
        "snippet": {
            "title": clean_title[:100],
            "description": description,
            "tags": clean_tags,
            "categoryId": "22" # People & Blogs
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
    shorts_url = f"https://youtube.com/shorts/{video_id}"

    return {
        "success": True,
        "video_id": video_id,
        "url": shorts_url,
        "status": privacy_status
    }
