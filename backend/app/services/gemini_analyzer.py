"""
Gemini Analyzer service alias for ClipShlip AI Studio.
Provides multimodal virality detection using the official Google GenAI SDK.
"""

from app.services.gemini_clipper import (
    extract_viral_clips,
    build_gemini_prompt,
    get_duration_range,
    parse_time_str
)

__all__ = [
    "extract_viral_clips",
    "build_gemini_prompt",
    "get_duration_range",
    "parse_time_str"
]
