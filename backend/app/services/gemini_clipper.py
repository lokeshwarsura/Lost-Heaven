import os
import re
import json
import time
import random
import logging
import traceback
from pathlib import Path
from typing import List, Dict, Any, Optional

from app.config import settings
from app.models import GeminiAnalysisResponse, GeminiClipItem

logger = logging.getLogger(__name__)

# -------------------------------------------------------------
# High-Availability Fallback Cascade
# -------------------------------------------------------------
DEFAULT_MODEL_CASCADE = [
    "gemini-3.5-flash-lite",  # Default primary — fastest, highest throughput, 1M context
    "gemini-3.6-flash",       # Balanced multimodal
    "gemini-4-hydrogen",      # High reasoning
    "gemini-2.5-flash-lite"   # High-availability legacy fallback
]

# Built-in 2026 baseline fallback appended after user's chosen & live-discovered models
BUILTIN_BASELINE_CASCADE = [
    "gemini-3.5-flash-lite",
    "gemini-3.8-flash",
    "gemini-4-hydrogen",
    "gemini-3.6-flash",
    "gemini-4-carbon",
]

# Recommended priority for live-discovered models
_PRIORITY_ORDER = [
    "gemini-3.5-flash-lite", "gemini-3.8-flash", "gemini-4-hydrogen",
    "gemini-3.6-flash", "gemini-4-carbon", "gemini-4-neon", "gemini-4-argon",
    "gemini-3.1-pro-preview", "gemini-2.5-flash-lite", "gemini-2.5-flash",
    "gemini-2.5-pro", "gemini-2.0-flash-lite", "gemini-2.0-flash",
]

# Substrings that identify non-generateContent model endpoints to exclude
_EXCLUDED_PATTERNS = ("-tts", "-image", "-live", "embedding", "veo-", "imagen-", "-aqa", "aqa")


def _is_auth_error(err: Any) -> bool:
    """Return True if an SDK exception represents an invalid/unauthorized API key (401/403/API_KEY_INVALID)."""
    err_str = str(err)
    err_upper = err_str.upper()
    if any(k in err_upper for k in [
        "API_KEY_INVALID",
        "API KEY NOT VALID",
        "INVALID API KEY",
        "INVALID_API_KEY",
        "API_KEY_HTTP_REFERRER_BLOCKED",
        "PERMISSION_DENIED",
        "UNAUTHENTICATED",
    ]):
        return True
    if re.search(r"\b(401|403)\b", err_str):
        return True
    if "400" in err_str and ("api_key" in err_str.lower() or "api key" in err_str.lower()):
        return True
    return False


def _extract_http_code(err: Any) -> Optional[str]:
    """Extract numeric HTTP status code from a GenAI SDK exception string."""
    err_str = str(err)
    m = re.search(r"\b(400|401|403|404|408|429|500|502|503|504)\b", err_str)
    if m:
        return m.group(1)
    upper = err_str.upper()
    if "NOT_FOUND" in upper:
        return "404"
    if "INVALID_ARGUMENT" in upper:
        return "400"
    if "RESOURCE_EXHAUSTED" in upper:
        return "429"
    if "UNAVAILABLE" in upper or "OVERLOADED" in upper:
        return "503"
    if "UNAUTHENTICATED" in upper:
        return "401"
    if "PERMISSION_DENIED" in upper:
        return "403"
    return None


def _validate_plain_json_clips(raw_json_str: str, model_id: str) -> str:
    """Parse extracted JSON string and validate against GeminiAnalysisResponse schema."""
    parsed = json.loads(raw_json_str)
    if not isinstance(parsed, dict) or not isinstance(parsed.get("clips"), list) or len(parsed["clips"]) == 0:
        raise ValueError(f"Model '{model_id}' JSON missing non-empty 'clips' list.")

    # Normalize optional fields before strict Pydantic validation so numeric timestamps or omitted sub-scores validate cleanly
    normalized_clips: List[Dict[str, Any]] = []
    for item in parsed["clips"]:
        if not isinstance(item, dict):
            continue
        c = dict(item)
        if "start_time" in c and not isinstance(c["start_time"], str):
            c.setdefault("start_seconds", float(c["start_time"]))
            c["start_time"] = str(c["start_time"])
        elif "start_time" not in c and "start_seconds" in c:
            c["start_time"] = str(c["start_seconds"])
        if "end_time" in c and not isinstance(c["end_time"], str):
            c.setdefault("end_seconds", float(c["end_time"]))
            c["end_time"] = str(c["end_time"])
        elif "end_time" not in c and "end_seconds" in c:
            c["end_time"] = str(c["end_seconds"])

        c.setdefault("start_time", "00:00")
        c.setdefault("end_time", "00:30")
        c.setdefault("virality_score", 85)
        c.setdefault("hook_score", c.get("virality_score", 85))
        c.setdefault("retention_score", c.get("virality_score", 85))
        c.setdefault("emotional_score", 82)
        c.setdefault("standalone_score", 85)
        c.setdefault("viral_rationale", c.pop("virality_reason", "") or f"High-impact moment identified by {model_id}.")
        c.setdefault("hook_headline", "THE CRITICAL MOMENT")
        c.setdefault("cold_open_quote", "")
        c.setdefault("youtube_titles", [c.get("hook_headline", "Viral Moment") + " #Shorts"])
        if "youtube_description" not in c and "seo_description" in c:
            c["youtube_description"] = c.pop("seo_description")
        c.setdefault("youtube_description", "Extracted with Lost Heaven Studio! #Shorts")
        c.setdefault("pinned_comment", "What did you think about this moment?")
        normalized_clips.append(c)

    validated = GeminiAnalysisResponse.model_validate({"clips": normalized_clips})
    # Preserve start_seconds/end_seconds alongside validated schema
    return validated.model_dump_json()


def list_available_gemini_models(api_key: str) -> List[Dict[str, Any]]:
    """
    Discover Gemini models live from the API key's authorized model list.
    Returns [{id, display_name, description}] sorted by recommended priority.
    Filters to generateContent-capable models only; excludes TTS, image, embed, Veo, Live endpoints.
    Raises GeminiAuthError on 401/403 or invalid API key. Returns [] on other errors.
    """
    key = (api_key or "").strip()
    if not key or key == "your_gemini_api_key_here" or len(key) < 10:
        return []

    try:
        from google import genai
        client = genai.Client(api_key=key, http_options={"timeout": 15000})
        raw_models = list(client.models.list())
    except Exception as e:
        if _is_auth_error(e):
            raise GeminiAuthError(reason="invalid", message=f"API key rejected during model discovery: {str(e)[:120]}")
        logger.warning(f"Live Gemini model discovery failed (non-auth): {str(e)[:120]}. Using fallback cascade.")
        return []

    discovered: List[Dict[str, Any]] = []
    seen_ids: set = set()
    for m in raw_models:
        raw_name: str = getattr(m, "name", "") or ""
        actions = getattr(m, "supported_actions", None) or getattr(m, "supported_generation_methods", None) or []
        actions_list = list(actions) if actions else []

        if "generateContent" not in actions_list:
            continue

        model_id = raw_name.removeprefix("models/").strip()
        if not model_id or model_id in seen_ids:
            continue

        if any(pat in model_id.lower() for pat in _EXCLUDED_PATTERNS):
            continue

        seen_ids.add(model_id)
        display_name = getattr(m, "display_name", None) or model_id
        description = getattr(m, "description", None) or ""

        discovered.append({
            "id": model_id,
            "display_name": display_name,
            "description": description,
        })

    priority_index = {name: i for i, name in enumerate(_PRIORITY_ORDER)}
    discovered.sort(key=lambda m: (priority_index.get(m["id"], 999), m["id"]))

    logger.info(f"Live Gemini model discovery: {len(discovered)} generateContent models found.")
    return discovered


def _invoke_model_compatible(
    client: Any,
    model_id: str,
    contents: Any,
    system_instruction: str,
) -> str:
    """
    2-tier schema-adaptive generate_content wrapper.
    Strips 'models/' prefix and omits thinking_config / deprecated temperature/top_p/top_k.
    Attempt A: structured JSON with response_schema + system_instruction.
    Attempt B on HTTP 400 (INVALID_ARGUMENT): same model with instructions in user prompt,
               no response_schema/response_mime_type/system_instruction, regex JSON extraction + GeminiAnalysisResponse validation.
    """
    from google.genai import types

    model_id = model_id.removeprefix("models/").strip()

    # --- Attempt A: Structured JSON schema ---
    try:
        response = client.models.generate_content(
            model=model_id,
            contents=contents,
            config=types.GenerateContentConfig(
                response_mime_type="application/json",
                response_schema=GeminiAnalysisResponse,
                system_instruction=system_instruction,
            )
        )
        if response and response.text:
            return response.text
        raise ValueError("Empty response from model")
    except Exception as e_a:
        if _is_auth_error(e_a):
            raise GeminiAuthError(reason="invalid", message=f"API key rejected by {model_id}: {str(e_a)[:120]}")

        err_str_a = str(e_a)
        err_upper_a = err_str_a.upper()

        is_cascade = (
            "404" in err_str_a or "NOT_FOUND" in err_upper_a
            or "503" in err_str_a or "UNAVAILABLE" in err_upper_a
            or "429" in err_str_a or "RESOURCE_EXHAUSTED" in err_upper_a
            or "OVERLOADED" in err_upper_a
            or isinstance(e_a, TimeoutError)
            or any(t in err_upper_a for t in ["TIMEOUT", "TIMED OUT", "DEADLINE_EXCEEDED"])
        )
        if is_cascade:
            raise

        is_schema_error = (
            "400" in err_str_a
            or "INVALID_ARGUMENT" in err_upper_a
            or "response_schema" in err_str_a.lower()
            or "response_mime_type" in err_str_a.lower()
            or "system_instruction" in err_str_a.lower()
            or "developer instruction" in err_str_a.lower()
        )
        if not is_schema_error:
            raise

        logger.info(f"Model '{model_id}' rejected structured schema/config (HTTP 400). Retrying plain-JSON extraction on '{model_id}'.")

    # --- Attempt B: Plain prompt + regex JSON extraction + GeminiAnalysisResponse validation ---
    schema_instructions = (
        f"\n\nSYSTEM ROLE: {system_instruction}\n\n"
        "IMPORTANT: Return ONLY a valid raw JSON object matching this exact schema (no markdown fences):\n"
        '{"clips": [{"start_time": "MM:SS", "end_time": "MM:SS", "start_seconds": 0.0, "end_seconds": 30.0, '
        '"virality_score": 90, "hook_score": 88, "retention_score": 88, "emotional_score": 85, "standalone_score": 90, '
        '"viral_rationale": "Why it is viral", "hook_headline": "PUNCHY 4 TO 6 WORD HEADLINE", '
        '"cold_open_quote": "Best 1-sentence hook quote", "youtube_titles": ["Title 1 #Shorts", "Title 2 #Shorts", "Title 3 #Shorts"], '
        '"youtube_description": "SEO description with #Shorts", "pinned_comment": "Engagement question?"}]}'
    )

    try:
        if isinstance(contents, list):
            plain_contents = list(contents) + [schema_instructions]
        else:
            plain_contents = f"{contents}{schema_instructions}"

        response_b = client.models.generate_content(
            model=model_id,
            contents=plain_contents,
            config=types.GenerateContentConfig(),
        )
        raw_text = response_b.text if response_b else ""
        if not raw_text:
            raise ValueError(f"Model '{model_id}' returned empty text in plain-JSON fallback mode.")

        json_match = re.search(r'\{[\s\S]*\}', raw_text)
        if not json_match:
            raise ValueError(f"Model '{model_id}' plain-JSON response contained no JSON object.")

        validated_json = _validate_plain_json_clips(json_match.group(0), model_id)
        logger.info(f"Model '{model_id}' plain-JSON fallback succeeded and validated against GeminiAnalysisResponse.")
        return validated_json

    except GeminiAuthError:
        raise
    except Exception as e_b:
        if _is_auth_error(e_b):
            raise GeminiAuthError(reason="invalid", message=str(e_b)[:120])
        raise

class GeminiAuthError(Exception):
    """Raised when Gemini API key is missing, invalid, unauthorized, or revoked."""
    def __init__(self, reason: str = "invalid", message: str = ""):
        super().__init__(message)
        self.reason = reason
        self.message = message

class GeminiUnavailableError(Exception):
    """Raised when all models in the cascade return 503/429/timeout or are overloaded."""
    def __init__(self, message: str = "", models_attempted: Optional[List[str]] = None):
        super().__init__(message)
        self.message = message
        self.models_attempted = models_attempted or []

def get_model_cascade(
    preferred_model: Optional[str] = None,
    api_key: Optional[str] = None,
) -> List[str]:
    """
    Return a deduplicated, prioritized list of Gemini models.
    Order: user's preferred model → live-discovered models → built-in fallback cascade.
    Live discovery is attempted when api_key is provided; failures degrade gracefully.
    """
    seen: set = set()
    result: List[str] = []

    def add(m: str) -> None:
        m = m.strip().removeprefix("models/")
        if m and m not in seen:
            seen.add(m)
            result.append(m)

    # 1. User's explicitly chosen model goes first
    pref = (preferred_model or "").strip().removeprefix("models/")
    if pref:
        add(pref)

    # 2. Live-discovered models (best-effort; non-blocking on failure)
    if api_key:
        try:
            live = list_available_gemini_models(api_key)
            for m in live:
                add(m["id"])
        except GeminiAuthError:
            raise
        except Exception as disc_err:
            logger.debug(f"Live model discovery skipped in cascade build: {disc_err}")

    # 3. Built-in fallback cascade as the safety net
    baseline = BUILTIN_BASELINE_CASCADE if api_key else DEFAULT_MODEL_CASCADE
    for m in baseline:
        add(m)

    return result

def verify_gemini_api_key(api_key: str, model: str = "gemini-3.5-flash-lite") -> tuple[bool, str]:
    """
    Perform a quick live validation of a Google Gemini API key.
    Returns (is_valid, message).
    """
    key = (api_key or "").strip()
    if not key or key == "your_gemini_api_key_here" or len(key) < 10:
        return False, "API key cannot be empty or too short."

    clean_model = (model or "gemini-3.5-flash-lite").strip().removeprefix("models/")
    try:
        from google import genai
        client = genai.Client(api_key=key, http_options={"timeout": 15000})
        try:
            client.models.get(model=clean_model)
            return True, f"API key is valid and connected to {clean_model}."
        except Exception as get_err:
            if _is_auth_error(get_err):
                raise
            # Selected custom/preview model may return 404 on models.get while key itself is valid
            live = list_available_gemini_models(key)
            if live:
                return True, f"API key is valid ({len(live)} models discovered)."
            raise get_err
    except GeminiAuthError as ae:
        return False, ae.message or "Google rejected API key."
    except Exception as e:
        err = str(e)
        logger.warning(f"Gemini API key verification failed on {clean_model}: {err}")
        return False, f"Google rejected API key: {err[:120]}"

def test_all_gemini_models(api_key: Optional[str] = None) -> List[Dict[str, Any]]:
    """
    Diagnostic suite: Pings live-discovered models (or fallback cascade) with a tiny 1-token call
    and returns latency and status (OK, 503 High Demand, 404 Unavailable, Invalid Key).
    Uses list_available_gemini_models() for dynamic model discovery; falls back to DEFAULT_MODEL_CASCADE.
    """
    key = (api_key or settings.GEMINI_API_KEY or os.environ.get("GEMINI_API_KEY", "")).strip()
    if not key or key == "your_gemini_api_key_here" or len(key) < 10:
        return [
            {
                "model": m,
                "status": "Invalid Key",
                "latency_ms": 0,
                "detail": "API key is unconfigured or invalid."
            }
            for m in DEFAULT_MODEL_CASCADE
        ]

    # Discover models live; merge with 2026 baseline fallback
    fallback_models = list(BUILTIN_BASELINE_CASCADE)
    try:
        live_models = list_available_gemini_models(key)
        if live_models:
            seen_ids = set()
            models_to_test = []
            for m in [x["id"] for x in live_models[:8]] + fallback_models:
                if m not in seen_ids:
                    seen_ids.add(m)
                    models_to_test.append(m)
        else:
            models_to_test = fallback_models
    except GeminiAuthError as auth_err:
        return [
            {"model": m, "status": "Invalid Key", "latency_ms": 0, "detail": str(auth_err)[:120]}
            for m in fallback_models
        ]
    except Exception:
        models_to_test = fallback_models

    try:
        from google import genai
        from google.genai import types
        client = genai.Client(api_key=key, http_options={"timeout": 15000})
    except Exception as e:
        return [
            {
                "model": m,
                "status": "Error",
                "latency_ms": 0,
                "detail": f"Failed to initialize GenAI client: {str(e)[:80]}"
            }
            for m in models_to_test
        ]

    results = []
    for model_name in models_to_test:
        start_t = time.perf_counter()
        try:
            # 1-token diagnostic ping with no deprecated temperature
            client.models.generate_content(
                model=model_name,
                contents="Ping",
                config=types.GenerateContentConfig(
                    max_output_tokens=1,
                    system_instruction="Diagnostic ping"
                )
            )
            latency = int((time.perf_counter() - start_t) * 1000)
            results.append({
                "model": model_name,
                "status": "OK",
                "latency_ms": latency,
                "detail": "Operational (Ready)"
            })
        except Exception as e:
            latency = int((time.perf_counter() - start_t) * 1000)
            err_str = str(e)
            err_upper = err_str.upper()

            if _is_auth_error(e):
                status = "Invalid Key"
                detail = "API key was rejected by Google"
            elif any(k in err_upper for k in ["503", "UNAVAILABLE", "OVERLOADED", "RESOURCE_EXHAUSTED", "429"]):
                status = "503 High Demand"
                detail = "Capacity spike — auto-fallback active"
            elif "404" in err_str or "NOT_FOUND" in err_upper:
                status = "404 Unavailable"
                detail = "Model not found or deprecated"
            else:
                status = "Error"
                detail = err_str[:90]

            results.append({
                "model": model_name,
                "status": status,
                "latency_ms": latency,
                "detail": detail
            })

    return results

def parse_time_str(time_val: Any) -> float:
    """Parse time string like 'HH:MM:SS', 'MM:SS', or float seconds into float seconds."""
    if isinstance(time_val, (int, float)):
        return float(time_val)
    s = str(time_val).strip()
    try:
        return float(s)
    except ValueError:
        pass
    parts = s.split(":")
    try:
        if len(parts) == 3:
            return float(parts[0]) * 3600 + float(parts[1]) * 60 + float(parts[2])
        elif len(parts) == 2:
            return float(parts[0]) * 60 + float(parts[1])
    except Exception:
        pass
    return 0.0

def get_duration_range(preset: str) -> tuple[float, float]:
    """Resolve min and max duration bounds from preset."""
    if preset == "15-30s" or preset == "15s":
        return 15.0, 30.0
    elif preset == "45s":
        return 30.0, 45.0
    elif preset == "60s":
        return 45.0, 60.0
    elif preset == "60-180s" or preset == "90s":
        return 60.0, 180.0
    else: # 30-58s (default Shorts / Auto)
        return 30.0, 58.0

def build_gemini_prompt(
    duration_preset: str = "30-58s",
    clip_count: int = 5,
    topic_focus: Optional[str] = None,
    formula_weights: Optional[Dict[str, Any]] = None,
) -> str:
    min_dur, max_dur = get_duration_range(duration_preset)
    focus_clause = f"\nCREATIVE TOPIC FOCUS: Prioritize moments focusing on: '{topic_focus}'." if topic_focus else ""
    guidance = (formula_weights or {}).get("prompt_guidance") or ""
    formula_clause = f"\n{guidance}" if (formula_weights and formula_weights.get("has_data") and guidance) else ""

    return f"""
You are an elite YouTube Shorts & multi-format virality director and video systems architect.
Analyze this video content and extract the top {clip_count} most compelling, high-retention viral moments.

CRITICAL REQUIREMENTS:
1. Target Count: Return exactly {clip_count} distinct, high-impact clip moments from the actual content.
2. Duration: Each clip MUST be strictly between {int(min_dur)} and {int(max_dur)} seconds in length.{focus_clause}{formula_clause}
3. Complete Sentences: Clip start and end timestamps MUST align with natural pauses and full sentences. NEVER cut off speech mid-word or mid-thought.
4. Virality Criteria:
   - Strong hook in opening 3 seconds (curiosity gap, controversial statement, surprising insight, or energetic action).
   - High emotional resonance (humor, shock, vulnerability, breakthrough insight, or high drama).
   - Standalone clarity: A viewer must understand and love the clip without having seen the full video.
5. Hook Headline: Provide a punchy 4 to 6 word top banner headline summarizing the exact clip insight (e.g. "HOW TO DOUBLE YOUR INCOME", "THE REAL REASON YOU PROCRASTINATE").
6. Cold-Open Quote: Identify the single most shocking or impactful 1-sentence quote inside the clip (to serve as an optional cold-open teaser).
7. YouTube Packaging:
   - Provide 3 distinct A/B-testable YouTube titles under 60 characters (with #Shorts).
   - Provide an SEO-optimized description with 3 to 5 trending hashtags.
   - Provide 1 high-engagement pinned comment question to drive viewer comments.

Return the response adhering strictly to the JSON schema.
"""

def format_transcript_as_text(words: List[Dict[str, Any]]) -> str:
    """
    Format word list with timestamps into compact grouped sentence / 5-second blocks
    for Gemini text-first extraction. Keeps token usage small and fast even for 30-60 minute videos.
    """
    if not words:
        return ""
    lines = []
    current_line = []
    line_start = float(words[0].get("start", 0.0))
    max_words_per_block = 20 if len(words) > 2000 else 12

    for w in words:
        word_text = str(w.get("word", "")).strip()
        if not word_text:
            continue
        current_line.append(word_text)
        w_end = float(w.get("end", line_start))
        elapsed_in_block = w_end - line_start

        # Break on punctuation or after reaching word/time threshold (5 seconds or max words)
        is_terminal = any(punct in word_text for punct in [".", "?", "!"])
        if (is_terminal and len(current_line) >= 5) or len(current_line) >= max_words_per_block or elapsed_in_block >= 5.0:
            lines.append(f"[{line_start:.1f}s - {w_end:.1f}s] {' '.join(current_line)}")
            current_line = []
            line_start = w_end

    if current_line:
        lines.append(f"[{line_start:.1f}s - {float(words[-1].get('end', line_start)):.1f}s] {' '.join(current_line)}")

    return "\n".join(lines)

def extract_viral_clips(
    proxy_path: Optional[str] = None,
    total_duration: float = 120.0,
    duration_preset: str = "30-58s",
    clip_count: int = 5,
    topic_focus: Optional[str] = None,
    transcript_words: Optional[List[Dict[str, Any]]] = None,
    gemini_analysis_mode: str = "transcript",
    preferred_model: Optional[str] = None,
    on_log: Optional[Any] = None,
    formula_weights: Optional[Dict[str, Any]] = None,
) -> List[Dict[str, Any]]:
    """
    Extract viral clips using official Google GenAI SDK.
    Transcript-First by default (skips 480p proxy encoding entirely).
    Uses high-availability 4-model cascade with fast jittered retries.
    Distinguishes GeminiAuthError vs GeminiUnavailableError.
    Never returns dummy or mock clips.
    """
    api_key = settings.GEMINI_API_KEY or os.environ.get("GEMINI_API_KEY", "")
    if not api_key or api_key == "your_gemini_api_key_here":
        raise GeminiAuthError(
            reason="missing",
            message="Google Gemini API Key is missing or unconfigured."
        )

    def emit_log(msg: str, level: str = "INFO"):
        logger.info(msg)
        if on_log:
            try:
                on_log("GEMINI", msg)
            except Exception:
                pass

    from google import genai
    from google.genai import types

    # Enforce strict 60-second HTTP timeout per request
    client = genai.Client(api_key=api_key, http_options={"timeout": 60000})
    prompt = build_gemini_prompt(duration_preset, clip_count, topic_focus, formula_weights=formula_weights)
    min_dur, max_dur = get_duration_range(duration_preset)

    cascade = get_model_cascade(preferred_model or settings.GEMINI_MODEL, api_key=api_key)
    emit_log(f"Universal Gemini Cascade initialized ({len(cascade)} models): {' → '.join(cascade[:5])}{'…' if len(cascade) > 5 else ''}")

    # 1. Determine analysis strategy: Transcript-First vs Multimodal Video Proxy
    has_sparse_words = not transcript_words or len(transcript_words) < 15
    use_multimodal = (gemini_analysis_mode == "multimodal" or has_sparse_words) and bool(proxy_path and os.path.exists(proxy_path))

    video_file = None
    if use_multimodal:
        try:
            emit_log(f"Uploading lightweight video proxy to Gemini Files API: {proxy_path}")
            video_file = client.files.upload(file=proxy_path)
            emit_log(f"File uploaded successfully to Gemini Files API: {video_file.name}")
        except Exception as up_err:
            if _is_auth_error(up_err):
                raise GeminiAuthError(reason="invalid", message=f"Gemini API key rejected during upload: {str(up_err)[:120]}")
            emit_log(f"Proxy upload failed ({up_err}). Seamlessly switching to Transcript-First mode.", "WARN")
            video_file = None
    else:
        emit_log(f"Transcript-First Mode active: Skipping 480p proxy upload for blazing-fast inference.")

    # Format transcript text if needed
    formatted_transcript = format_transcript_as_text(transcript_words or [])

    # Fast, capped retries: 2 retries (3s, 8s with jitter) per model before cascading
    retry_delays = [3, 8]
    response_text = None
    last_error = None
    successful_model = None

    try:
        for model_idx, model_name in enumerate(cascade):
            emit_log(f"Trying {model_name} [{model_idx + 1}/{len(cascade)}] for viral blueprints...")

            # Prepare payload for this attempt
            if video_file:
                contents_payload = [video_file, prompt]
            elif formatted_transcript:
                full_text_prompt = f"{prompt}\n\nTIMESTAMPED AUDIO TRANSCRIPT OF THE VIDEO:\n{formatted_transcript}"
                contents_payload = [full_text_prompt]
            else:
                contents_payload = [prompt]

            model_success = False
            saw_overload_error = False
            error_summaries: List[str] = []

            for attempt, delay in enumerate(retry_delays, start=1):
                try:
                    response_text = _invoke_model_compatible(
                        client=client,
                        model_id=model_name,
                        contents=contents_payload,
                        system_instruction="You are an elite YouTube Shorts virality director and video systems architect.",
                    )
                    if response_text:
                        successful_model = model_name
                        model_success = True
                        emit_log(f"Gemini analysis completed successfully with '{model_name}'!")
                        break
                except GeminiAuthError:
                    raise  # abort cascade immediately on auth failure
                except Exception as e:
                    err_str = str(e)
                    last_error = e
                    err_upper = err_str.upper()

                    if _is_auth_error(e):
                        raise GeminiAuthError(reason="invalid", message=f"Gemini API key was rejected ({model_name}): {err_str[:120]}")

                    http_code = _extract_http_code(e)
                    short_msg = err_str.replace("\n", " ")[:120]
                    error_label = f"HTTP {http_code}: {short_msg}" if http_code else short_msg
                    emit_log(f"{model_name} returned {error_label}", "WARN")
                    error_summaries.append(f"{model_name}({http_code or 'err'})")

                    # Check for 404 NOT_FOUND (model unavailable / deprecated → cascade immediately without retries)
                    if http_code == "404" or "404" in err_str or "NOT_FOUND" in err_upper:
                        emit_log(f"Model '{model_name}' returned 404 (unavailable) — immediately cascading to next model...", "WARN")
                        break

                    # Check for Timeout (60s limit exceeded)
                    is_timeout = isinstance(e, TimeoutError) or any(t_code in err_upper for t_code in ["TIMEOUT", "TIMED OUT", "DEADLINE_EXCEEDED", "READTIMEOUT", "CONNECTTIMEOUT"])
                    if is_timeout:
                        emit_log(f"Model '{model_name}' timed out (exceeded 60s limit) — cascading to next model...", "WARN")
                        break

                    # Check for 503 / 429 / RESOURCE_EXHAUSTED / OVERLOADED
                    is_transient = http_code in ("503", "429") or any(code in err_upper for code in ["503", "429", "RESOURCE_EXHAUSTED", "UNAVAILABLE", "OVERLOADED"])
                    if is_transient:
                        saw_overload_error = True
                    if is_transient and attempt < len(retry_delays):
                        jitter = random.uniform(0.2, 0.8)
                        wait_time = delay + jitter
                        emit_log(
                            f"Gemini '{model_name}' high demand ({http_code or '503/429'}) — quick retry in {wait_time:.1f}s (attempt {attempt}/{len(retry_delays)})...",
                            "WARN"
                        )
                        time.sleep(wait_time)
                    else:
                        emit_log(f"Model '{model_name}' unavailable ({error_label}). Cascading to next model...", "WARN")
                        break

            if model_success:
                break

            # If video failed on this model, try transcript text on remaining models
            if video_file and formatted_transcript:
                emit_log("Switching cascade payload to timestamped transcript text for remaining models.")
                video_file = None

    finally:
        # Clean up temporary remote file if uploaded
        if video_file:
            try:
                client.files.delete(name=video_file.name)
                emit_log(f"Cleaned up remote Gemini temporary file: {video_file.name}")
            except Exception as ex:
                logger.warning(f"Could not delete remote Gemini file {video_file.name}: {ex}")

    # If all models in the cascade failed, raise typed GeminiUnavailableError with accurate error attribution
    if not response_text:
        if last_error and any(code in str(last_error) for code in ["503", "429", "UNAVAILABLE", "RESOURCE_EXHAUSTED", "OVERLOADED"]):
            failure_reason = f"503/429 high demand: {last_error}"
        else:
            failure_reason = f"last error: {last_error}"
        raise GeminiUnavailableError(
            message=f"All {len(cascade)} Gemini models in the cascade ({', '.join(cascade)}) failed ({failure_reason})",
            models_attempted=cascade
        )

    try:
        data = json.loads(response_text)
    except Exception as parse_err:
        raise RuntimeError(f"Could not parse Gemini JSON response: {parse_err}. Raw: {response_text[:300]}")

    clips_data = data.get("clips", [])
    if not clips_data:
        raise RuntimeError(f"Gemini model returned empty clips list for this video. Raw response: {response_text[:200]}")

    results = []
    for item in clips_data:
        start = item.get("start_seconds")
        if start is None:
            start = parse_time_str(item.get("start_time", 0))
        end = item.get("end_seconds")
        if end is None:
            end = parse_time_str(item.get("end_time", start + min_dur))

        dur = end - start
        if dur < min_dur or dur > max_dur:
            dur = min(max(dur, min_dur), max_dur)
            end = start + dur

        # Clamping to total duration
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
            "viral_rationale": item.get("viral_rationale", f"High retention moment identified via Gemini {successful_model}."),
            "hook_headline": item.get("hook_headline", "THE CRITICAL MOMENT"),
            "cold_open_quote": item.get("cold_open_quote", ""),
            "youtube_titles": item.get("youtube_titles", ["Viral Moment #Shorts"]),
            "youtube_description": item.get("youtube_description", "Extracted with Lost Heaven Studio! #Shorts"),
            "pinned_comment": item.get("pinned_comment", "What do you think about this? Drop your thoughts below!"),
            "analysis_source": f"gemini:{successful_model}"
        })

    results.sort(key=lambda x: x["virality_score"], reverse=True)
    return results[:clip_count]
