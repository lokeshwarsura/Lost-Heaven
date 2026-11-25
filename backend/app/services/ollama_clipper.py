import os
import json
import logging
from typing import List, Dict, Any, Optional, Tuple
import requests

from app.config import settings
from app.services.gemini_clipper import (
    get_duration_range,
    parse_time_str,
    format_transcript_as_text
)

logger = logging.getLogger(__name__)

def check_ollama_status(endpoint: str = "http://127.0.0.1:11434") -> Tuple[bool, List[str], str]:
    """
    Check if local Ollama server is running and return list of downloaded models.
    Returns (is_available, model_names, status_message).
    """
    clean_endpoint = endpoint.rstrip("/")
    try:
        res = requests.get(f"{clean_endpoint}/api/tags", timeout=3)
        if res.status_code == 200:
            data = res.json()
            models = [m.get("name", "") for m in data.get("models", []) if m.get("name")]
            return True, models, f"Ollama online with {len(models)} model(s)."
        return False, [], f"Ollama returned HTTP {res.status_code}"
    except Exception as e:
        return False, [], f"Ollama server not reachable at {clean_endpoint} ({str(e)[:60]})"

def extract_viral_clips_ollama(
    transcript_words: List[Dict[str, Any]],
    total_duration: float = 120.0,
    duration_preset: str = "30-58s",
    clip_count: int = 5,
    topic_focus: Optional[str] = None,
    endpoint: str = "http://127.0.0.1:11434",
    model: str = "qwen2.5:7b",
    on_log: Optional[Any] = None
) -> List[Dict[str, Any]]:
    """
    Extract viral moments locally via Ollama JSON API.
    Provides 100% local, private, zero-rate-limit LLM reasoning on RTX GPUs.
    """
    def emit_log(msg: str):
        logger.info(msg)
        if on_log:
            try:
                on_log("OLLAMA", msg)
            except Exception:
                pass

    clean_endpoint = endpoint.rstrip("/")
    min_dur, max_dur = get_duration_range(duration_preset)
    formatted_transcript = format_transcript_as_text(transcript_words)

    if not formatted_transcript:
        raise ValueError("Cannot run Ollama transcript extraction: transcript is empty.")

    focus_clause = f"\nCREATIVE TOPIC FOCUS: Prioritize moments focusing on '{topic_focus}'." if topic_focus else ""

    prompt = f"""You are an elite YouTube Shorts virality director and video systems architect.
Analyze the following timestamped spoken transcript and extract the top {clip_count} most compelling, high-retention viral moments.

CRITICAL REQUIREMENTS:
1. Target Count: Return exactly {clip_count} distinct clip moments from the transcript.
2. Duration: Each clip MUST be strictly between {int(min_dur)} and {int(max_dur)} seconds in length.{focus_clause}
3. Complete Sentences: Start and end timestamps must align with natural sentence boundaries.
4. Hook Headline: Provide a punchy 4 to 6 word ALL-CAPS banner headline summarizing the insight.
5. YouTube Packaging: 3 distinct titles (with #Shorts), 1 description, 1 pinned comment question.

TIMESTAMPED TRANSCRIPT:
{formatted_transcript}

Respond ONLY with a valid JSON object matching this structure:
{{
  "clips": [
    {{
      "start_time": 10.5,
      "end_time": 42.0,
      "virality_score": 92,
      "hook_score": 90,
      "retention_score": 88,
      "emotional_score": 85,
      "standalone_score": 90,
      "viral_rationale": "High curiosity opening and clean conclusion.",
      "hook_headline": "HOW TO 10X YOUR PRODUCTIVITY",
      "cold_open_quote": "Most people get this completely backwards.",
      "youtube_titles": ["The Secret to 10X Work #Shorts", "Why You Are Doing This Wrong #Shorts", "Productivity Hack #Shorts"],
      "youtube_description": "Stop working harder and start working smarter! #Shorts #Productivity",
      "pinned_comment": "Do you use this technique? Let me know below!"
    }}
  ]
}}
"""

    emit_log(f"Connecting to local Ollama ({clean_endpoint}) using model '{model}'...")
    payload = {
        "model": model,
        "prompt": prompt,
        "format": "json",
        "stream": False,
        "options": {
            "temperature": 0.4
        }
    }

    try:
        res = requests.post(f"{clean_endpoint}/api/generate", json=payload, timeout=120)
        if res.status_code != 200:
            raise RuntimeError(f"Ollama returned HTTP {res.status_code}: {res.text[:200]}")
        data = res.json()
        raw_response = data.get("response", "")
    except Exception as e:
        emit_log(f"Ollama query failed: {e}")
        raise

    try:
        parsed = json.loads(raw_response)
    except Exception as pe:
        raise RuntimeError(f"Failed to parse Ollama JSON response: {pe}. Raw: {raw_response[:300]}")

    clips_data = parsed.get("clips", [])
    if not clips_data:
        raise RuntimeError(f"Ollama returned 0 clips in JSON response.")

    results = []
    for item in clips_data:
        start = parse_time_str(item.get("start_time", 0.0))
        end = parse_time_str(item.get("end_time", start + min_dur))
        dur = end - start
        if dur < min_dur or dur > max_dur:
            dur = min(max(dur, min_dur), max_dur)
            end = start + dur

        if total_duration > 0 and start >= total_duration:
            start = max(0.0, total_duration - dur)
            end = total_duration

        results.append({
            "start_time": round(start, 2),
            "end_time": round(end, 2),
            "duration": round(dur, 2),
            "virality_score": int(item.get("virality_score", 88)),
            "hook_score": int(item.get("hook_score", 88)),
            "retention_score": int(item.get("retention_score", 85)),
            "emotional_score": int(item.get("emotional_score", 82)),
            "standalone_score": int(item.get("standalone_score", 88)),
            "viral_rationale": item.get("viral_rationale", f"Local LLM detected viral moment via Ollama ({model})."),
            "hook_headline": item.get("hook_headline", "THE VIRAL MOMENT"),
            "cold_open_quote": item.get("cold_open_quote", ""),
            "youtube_titles": item.get("youtube_titles", ["Viral Moment #Shorts"]),
            "youtube_description": item.get("youtube_description", "Extracted with Lost Heaven Studio! #Shorts"),
            "pinned_comment": item.get("pinned_comment", "What do you think about this?"),
            "analysis_source": f"ollama:{model}"
        })

    results.sort(key=lambda x: x["virality_score"], reverse=True)
    emit_log(f"Ollama extracted {len(results)} viral moments locally.")
    return results[:clip_count]
