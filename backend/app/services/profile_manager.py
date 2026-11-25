import json
import uuid
from pathlib import Path
from typing import Any, Dict, List, Optional

from app.config import settings

DEFAULT_STARTER_PROFILES: List[Dict[str, Any]] = [
    {
        "id": "hormozi_high_contrast",
        "name": "Hormozi High-Contrast",
        "description": "Fast punchy karaoke words, tightened dead air, punch-in zooms, and retention bar.",
        "is_builtin": True,
        "settings": {
            "aspect_ratio": "9:16",
            "frame_layout": "auto_track",
            "caption_style": "Karaoke pop",
            "caption_font": "Montserrat",
            "font_size_percent": 115,
            "all_caps": True,
            "base_color": "#FFFFFF",
            "active_color": "#C6F32F",
            "highlight_shape": "Filled box",
            "tighten_pacing": True,
            "silence_threshold": 0.45,
            "clean_filler_words": True,
            "dynamic_punch_zoom": True,
            "punch_zoom_intensity": 1.14,
            "voice_isolation": True,
            "auto_sfx": True,
            "retention_bar_enabled": True,
            "retention_bar_color": "#C6F32F",
            "color_preset": "vibrant_gaming",
        },
    },
    {
        "id": "podcast_clean_dual",
        "name": "Podcast Clean Dual",
        "description": "Balanced split-screen speaker layout, clean readable captions, broadcast voice isolation.",
        "is_builtin": True,
        "settings": {
            "aspect_ratio": "9:16",
            "frame_layout": "split_podcast",
            "caption_style": "Clean Minimal",
            "caption_font": "Poppins",
            "font_size_percent": 95,
            "all_caps": False,
            "base_color": "#FFFFFF",
            "active_color": "#38BDF8",
            "highlight_shape": "Colour only",
            "tighten_pacing": True,
            "silence_threshold": 0.60,
            "clean_filler_words": True,
            "dynamic_punch_zoom": False,
            "voice_isolation": True,
            "auto_sfx": False,
            "retention_bar_enabled": True,
            "retention_bar_color": "#38BDF8",
            "color_preset": "cinematic",
        },
    },
    {
        "id": "gaming_pip_neon",
        "name": "Gaming PiP Neon",
        "description": "Gameplay + top-left webcam PiP, neon glow highlight captions, aggressive punch zooms & micro-SFX.",
        "is_builtin": True,
        "settings": {
            "aspect_ratio": "9:16",
            "frame_layout": "gameplay_webcam_pip",
            "webcam_corner": "top_left",
            "pip_border_color": "#F59E0B",
            "caption_style": "Cyberpunk",
            "caption_font": "Anton",
            "font_size_percent": 110,
            "all_caps": True,
            "base_color": "#FFFFFF",
            "active_color": "#F59E0B",
            "highlight_shape": "Colour only",
            "glow_intensity": 4.0,
            "tighten_pacing": True,
            "silence_threshold": 0.45,
            "dynamic_punch_zoom": True,
            "punch_zoom_intensity": 1.22,
            "auto_sfx": True,
            "retention_bar_enabled": True,
            "retention_bar_color": "#F59E0B",
            "color_preset": "hdr_pop",
        },
    },
]


def _profiles_file() -> Path:
    return Path(settings.STORAGE_DIR) / "profiles.json"


def list_profiles() -> List[Dict[str, Any]]:
    pf = _profiles_file()
    if not pf.exists():
        pf.parent.mkdir(parents=True, exist_ok=True)
        pf.write_text(json.dumps(DEFAULT_STARTER_PROFILES, indent=2), encoding="utf-8")
        return [dict(p) for p in DEFAULT_STARTER_PROFILES]
    try:
        data = json.loads(pf.read_text(encoding="utf-8"))
        if isinstance(data, list) and data:
            return data
    except Exception:
        pass
    pf.parent.mkdir(parents=True, exist_ok=True)
    pf.write_text(json.dumps(DEFAULT_STARTER_PROFILES, indent=2), encoding="utf-8")
    return [dict(p) for p in DEFAULT_STARTER_PROFILES]


def get_profile(profile_id: str) -> Optional[Dict[str, Any]]:
    for p in list_profiles():
        if p.get("id") == profile_id:
            return p
    return None


def create_profile(name: str, description: str, settings_payload: Dict[str, Any]) -> Dict[str, Any]:
    profiles = list_profiles()
    new_p = {
        "id": f"custom_{uuid.uuid4().hex[:8]}",
        "name": (name or "Custom Profile").strip(),
        "description": (description or "Saved Lost Heaven Studio preset").strip(),
        "is_builtin": False,
        "settings": dict(settings_payload or {}),
    }
    profiles.append(new_p)
    pf = _profiles_file()
    pf.parent.mkdir(parents=True, exist_ok=True)
    pf.write_text(json.dumps(profiles, indent=2), encoding="utf-8")
    return new_p


def delete_profile(profile_id: str) -> bool:
    profiles = list_profiles()
    filtered = [p for p in profiles if not (p.get("id") == profile_id and not p.get("is_builtin"))]
    if len(filtered) == len(profiles):
        return False
    _profiles_file().write_text(json.dumps(filtered, indent=2), encoding="utf-8")
    return True
