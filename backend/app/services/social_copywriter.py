import re
from typing import Any, Dict, List, Optional

from app.services.hook_generator import _extract_anchor_subject, _invoke_gemini_json_prompt


def _extract_hashtags(words: List[Dict[str, Any]], fallback_title: str = "") -> List[str]:
    stop = {
        "the", "and", "that", "this", "with", "for", "are", "was", "just",
        "they", "from", "have", "what", "when", "your", "about", "like", "really",
    }
    tags: List[str] = []
    seen = set()
    for w in words or []:
        tok = re.sub(r"[^a-zA-Z0-9]", "", str(w.get("word", ""))).lower()
        if len(tok) >= 4 and tok not in stop and tok not in seen:
            seen.add(tok)
            tags.append(f"#{tok}")
        if len(tags) >= 3:
            break

    base_pool = ["#shorts", "#viral", "#clip", "#storytime", "#trending"]
    for b in base_pool:
        if len(tags) >= 5:
            break
        if b not in tags:
            tags.append(b)
    return tags[:5]


def _generate_offline_platform_copy(
    transcript_words: List[Dict[str, Any]],
    hook_headline: str = "",
    selected_title: str = "",
    existing_description: str = "",
) -> Dict[str, Dict[str, Any]]:
    subject = _extract_anchor_subject(transcript_words, hook_headline or selected_title)
    pretty_subj = subject.title()
    tags = _extract_hashtags(transcript_words, selected_title)
    snippet = " ".join(
        str(w.get("word", "")).strip()
        for w in (transcript_words or [])[:24]
        if not w.get("deleted") and not w.get("cut_from_video")
    ).strip()
    summary = existing_description.strip() or (f"{snippet}..." if snippet else f"Watch what happens with {pretty_subj}.")

    return {
        "youtube_shorts": {
            "platform": "YouTube Shorts",
            "title": (selected_title or f"{pretty_subj} Changed Everything #Shorts")[:70],
            "description": f"{summary}\n\nSubscribe for daily breakdowns & highlights.",
            "pinned_comment": f"Wait until the ending — did you expect {pretty_subj.lower()}?",
            "hashtags": ["#Shorts", tags[0], tags[1], "#YouTubeShorts", "#Viral"],
        },
        "tiktok": {
            "platform": "TikTok",
            "title": f"Wait for it… {pretty_subj} 😳",
            "description": f"{summary} Which part surprised you most? 👇",
            "pinned_comment": "Part 2 or full breakdown? Comment below 👇",
            "hashtags": ["#fyp", "#viral", tags[0], tags[1], "#storytime"],
        },
        "instagram_reels": {
            "platform": "Instagram Reels",
            "title": f"{pretty_subj} — Watch Till The End ✨",
            "description": f"{summary}\n\nSave this Reel so you don't miss it later.",
            "pinned_comment": "Tag someone who needs to see this 🔥",
            "hashtags": ["#reels", "#reelsinstagram", tags[0], tags[1], "#explorepage"],
        },
        "x_twitter": {
            "platform": "X (Twitter)",
            "title": f"Most people completely miss this about {pretty_subj}:",
            "description": f"{summary[:180]} Watch the full 30s clip ↓",
            "pinned_comment": "Agree or disagree? Drop your take below.",
            "hashtags": [tags[0], tags[1], "#Threads", "#Insights", "#Video"],
        },
    }


def generate_multi_platform_copy(
    transcript_words: List[Dict[str, Any]],
    hook_headline: str = "",
    selected_title: str = "",
    existing_description: str = "",
    api_key: Optional[str] = None,
    preferred_model: Optional[str] = None,
) -> Dict[str, Any]:
    """
    Generate ready-to-post copy for YouTube Shorts, TikTok, Instagram Reels, and X (Twitter)
    via active Gemini model with offline NLP/keyword fallback.
    """
    transcript_text = " ".join(
        str(w.get("word", "")).strip()
        for w in (transcript_words or [])
        if not w.get("deleted") and not w.get("cut_from_video")
    ).strip()

    if transcript_text:
        prompt = (
            "You are an expert short-form social strategist.\n"
            "Generate platform-specific post copy for 4 keys: "
            "\"youtube_shorts\", \"tiktok\", \"instagram_reels\", \"x_twitter\".\n"
            "Each key must map to: "
            "{\"title\": \"...\", \"description\": \"...\", \"pinned_comment\": \"...\", "
            "\"hashtags\": [\"#tag1\", \"#tag2\", \"#tag3\", \"#tag4\", \"#tag5\"]}.\n"
            f"Hook headline: {hook_headline or 'None'}\n"
            f"Clip transcript:\n{transcript_text[:1500]}\n\n"
            'Return JSON: {"platforms": {"youtube_shorts": {...}, "tiktok": {...}, '
            '"instagram_reels": {...}, "x_twitter": {...}}}'
        )
        parsed = _invoke_gemini_json_prompt(prompt, api_key=api_key, preferred_model=preferred_model)
        raw_platforms = parsed.get("platforms") if isinstance(parsed, dict) else None
        required_keys = ("youtube_shorts", "tiktok", "instagram_reels", "x_twitter")
        display_names = {
            "youtube_shorts": "YouTube Shorts",
            "tiktok": "TikTok",
            "instagram_reels": "Instagram Reels",
            "x_twitter": "X (Twitter)",
        }
        if isinstance(raw_platforms, dict) and all(isinstance(raw_platforms.get(k), dict) for k in required_keys):
            normalized: Dict[str, Dict[str, Any]] = {}
            for k in required_keys:
                entry = raw_platforms[k]
                raw_tags = entry.get("hashtags") or []
                clean_tags = [
                    t if str(t).startswith("#") else f"#{str(t).strip()}"
                    for t in raw_tags
                    if str(t).strip()
                ][:5]
                while len(clean_tags) < 5:
                    clean_tags.append(["#Shorts", "#Viral", "#Content", "#Video", "#Trending"][len(clean_tags)])
                normalized[k] = {
                    "platform": display_names[k],
                    "title": str(entry.get("title") or selected_title or hook_headline or "Viral Moment")[:100],
                    "description": str(entry.get("description") or existing_description or "Check out this clip!"),
                    "pinned_comment": str(entry.get("pinned_comment") or "What do you think?"),
                    "hashtags": clean_tags,
                }
            return {"platforms": normalized, "source": "gemini"}

    return {
        "platforms": _generate_offline_platform_copy(
            transcript_words=transcript_words,
            hook_headline=hook_headline,
            selected_title=selected_title,
            existing_description=existing_description,
        ),
        "source": "offline_nlp",
    }
