import json
import logging
import os
import re
from typing import Any, Dict, List, Optional

from app.config import settings
from app.services.gemini_clipper import GeminiAuthError, get_model_cascade

logger = logging.getLogger(__name__)

_STOP_WORDS = {
    "the", "and", "that", "this", "with", "for", "are", "was", "were", "but",
    "not", "you", "your", "have", "has", "had", "just", "very", "really",
    "they", "them", "from", "into", "about", "what", "when", "where", "which",
    "who", "will", "would", "could", "should", "there", "their", "then", "than",
    "here", "like", "know", "going", "make", "made", "been", "some", "more",
}


def _extract_anchor_subject(
    transcript_words: List[Dict[str, Any]],
    current_headline: str = "",
    topic_focus: Optional[str] = None,
) -> str:
    """Derive a punchy 2-3 word subject anchor from spoken words or current headline."""
    tokens = [
        re.sub(r"[^\w']", "", str(w.get("word", ""))).strip()
        for w in (transcript_words or [])
        if not w.get("deleted") and not w.get("cut_from_video")
    ]
    keywords: List[str] = []
    seen = set()
    for t in tokens:
        low = t.lower()
        if len(low) >= 4 and low not in _STOP_WORDS and low not in seen:
            seen.add(low)
            keywords.append(t.upper())
        if len(keywords) >= 4:
            break

    if len(keywords) >= 2:
        return " ".join(keywords[:3])
    if current_headline.strip():
        cleaned = re.sub(r"[^\w\s]", "", current_headline).upper().split()
        if cleaned:
            return " ".join(cleaned[:3])
    if topic_focus and topic_focus.strip():
        return topic_focus.strip().upper()[:24]
    return "THIS HIDDEN SECRET"


def _generate_offline_hook_variants(
    transcript_words: List[Dict[str, Any]],
    current_headline: str = "",
    topic_focus: Optional[str] = None,
) -> List[Dict[str, Any]]:
    """Deterministic local NLP/transcript generator producing 3 high-converting hook styles."""
    subject = _extract_anchor_subject(transcript_words, current_headline, topic_focus)
    words_clean = subject.split()
    short_subj = " ".join(words_clean[:2]) if len(words_clean) >= 2 else subject

    return [
        {
            "variant_type": "Curiosity Gap",
            "hook_headline": f"WHY {short_subj} CHANGES EVERYTHING",
            "predicted_retention_lift": "+19%",
            "rationale": "Withholds the main payoff inside an open loop to hold viewers past the 3-second swipe window.",
        },
        {
            "variant_type": "Bold / Contrarian Claim",
            "hook_headline": f"STOP IGNORING {short_subj}",
            "predicted_retention_lift": "+16%",
            "rationale": "Pattern-interrupt imperative opening challenges viewers' assumptions immediately.",
        },
        {
            "variant_type": "High-Stakes Action",
            "hook_headline": f"{short_subj} IS ACTUALLY REAL",
            "predicted_retention_lift": "+14%",
            "rationale": "Punchy tabloid/action framing pairs high energy with an immediate reveal.",
        },
    ]


def _invoke_gemini_json_prompt(
    prompt: str,
    api_key: Optional[str] = None,
    preferred_model: Optional[str] = None,
) -> Optional[Dict[str, Any]]:
    """
    Universal schema-compatible Gemini JSON call supporting all live/custom models
    with structured JSON -> plain JSON fallback and cascade resilience.
    """
    key = (api_key or settings.GEMINI_API_KEY or os.environ.get("GEMINI_API_KEY", "")).strip()
    if not key or key == "your_gemini_api_key_here" or len(key) < 10:
        return None

    try:
        from google import genai
        from google.genai import types

        client = genai.Client(api_key=key, http_options={"timeout": 25000})
        cascade = get_model_cascade(preferred_model or settings.GEMINI_MODEL, api_key=key)
    except GeminiAuthError:
        return None
    except Exception as exc:
        logger.debug(f"Hook/copy Gemini client init skipped: {exc}")
        return None

    for model_id in cascade[:4]:
        clean_id = model_id.removeprefix("models/").strip()
        try:
            try:
                resp = client.models.generate_content(
                    model=clean_id,
                    contents=prompt,
                    config=types.GenerateContentConfig(response_mime_type="application/json"),
                )
            except Exception as err_a:
                err_str = str(err_a)
                if "400" in err_str or "INVALID_ARGUMENT" in err_str.upper() or "response_mime_type" in err_str.lower():
                    resp = client.models.generate_content(
                        model=clean_id,
                        contents=f"{prompt}\nReturn ONLY raw JSON without markdown fences.",
                        config=types.GenerateContentConfig(),
                    )
                else:
                    raise

            raw_text = (resp.text or "") if resp else ""
            match = re.search(r"\{[\s\S]*\}", raw_text)
            if match:
                parsed = json.loads(match.group(0))
                if isinstance(parsed, dict):
                    return parsed
        except Exception as err:
            logger.debug(f"Auxiliary Gemini call failed on {clean_id}: {str(err)[:90]}")
            continue
    return None


def generate_hook_variants(
    transcript_words: List[Dict[str, Any]],
    current_headline: str = "",
    topic_focus: Optional[str] = None,
    api_key: Optional[str] = None,
    preferred_model: Optional[str] = None,
) -> Dict[str, Any]:
    """
    Generate 3 tailored A/B hook banner variants (Curiosity Gap, Bold/Contrarian Claim,
    High-Stakes Action) via the active Gemini model with offline NLP fallback.
    """
    transcript_text = " ".join(
        str(w.get("word", "")).strip()
        for w in (transcript_words or [])
        if not w.get("deleted") and not w.get("cut_from_video")
    ).strip()

    if transcript_text:
        prompt = (
            "You are a viral short-form retention strategist.\n"
            "Write exactly 3 distinct 4-to-6 word UPPERCASE top-banner hook headlines for this clip:\n"
            "1) Curiosity Gap (withholds the payoff to boost 3-second retention)\n"
            "2) Bold / Contrarian Claim (pattern-interrupt statement)\n"
            "3) High-Stakes Action (punchy 4-6 word tabloid/gaming hook)\n\n"
            f"Current hook: {current_headline or 'None'}\n"
            f"Topic focus: {topic_focus or 'General viral highlight'}\n"
            f"Clip transcript:\n{transcript_text[:1500]}\n\n"
            'Return JSON: {"variants": [{"variant_type": "...", "hook_headline": "...", '
            '"predicted_retention_lift": "+18%", "rationale": "..."}]}'
        )
        parsed = _invoke_gemini_json_prompt(prompt, api_key=api_key, preferred_model=preferred_model)
        if parsed and isinstance(parsed.get("variants"), list) and len(parsed["variants"]) >= 3:
            normalized: List[Dict[str, Any]] = []
            default_types = ["Curiosity Gap", "Bold / Contrarian Claim", "High-Stakes Action"]
            for idx, v in enumerate(parsed["variants"][:3]):
                if not isinstance(v, dict):
                    continue
                headline = str(v.get("hook_headline") or "").strip().upper()
                if not headline:
                    continue
                normalized.append({
                    "variant_type": str(v.get("variant_type") or default_types[idx]),
                    "hook_headline": headline[:54],
                    "predicted_retention_lift": str(v.get("predicted_retention_lift") or f"+{18 - idx * 2}%"),
                    "rationale": str(v.get("rationale") or "Optimized for opening 3-second retention."),
                })
            if len(normalized) == 3:
                return {"variants": normalized, "source": "gemini"}

    return {
        "variants": _generate_offline_hook_variants(transcript_words, current_headline, topic_focus),
        "source": "offline_nlp",
    }
