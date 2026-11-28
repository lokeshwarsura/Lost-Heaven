"""
Compatibility shim forwarding legacy `youtube_uploader` imports to `youtube_publisher`.
Strictly enforces real OAuth 2.0 authentication (never simulates fake uploads).
"""

from typing import Any, Dict, List, Optional

from app.services.youtube_publisher import (
    SCOPES,
    YouTubeAuthError,
    YouTubeConfigError,
    connect_youtube_account,
    disconnect_youtube_account,
    get_client_secrets_file,
    get_youtube_credentials,
    get_youtube_status,
    is_youtube_authenticated,
    save_youtube_client_secrets,
    upload_clip_to_youtube,
)


def publish_video_to_youtube(
    video_path: str,
    title: str,
    description: str,
    privacy_status: str = "private",
    aspect_ratio: str = "9:16",
    tags: Optional[List[str]] = None,
) -> Dict[str, Any]:
    return upload_clip_to_youtube(
        video_path=video_path,
        title=title,
        description=description,
        tags=tags,
        privacy_status=privacy_status,
        aspect_ratio=aspect_ratio,
    )
