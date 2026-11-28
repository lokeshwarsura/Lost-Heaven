import re
from typing import Any, Dict, List, Optional

from app.services.hook_generator import _extract_anchor_subject, _invoke_gemini_json_prompt

_TOPIC_CATEGORY_HINTS = {
    "20": {"game", "gaming", "clutch", "valorant", "fortnite", "minecraft", "boss", "ranked", "stream", "fps", "gameplay"},
    "28": {"ai", "code", "coding", "software", "tech", "app", "model", "python", "gpu", "cpu", "algorithm", "build"},
    "27": {"learn", "lesson", "explain", "tutorial", "guide", "how", "science", "history", "tips", "strategy", "mistake"},
    "24": {"story", "comedy", "crazy", "prank", "reaction", "movie", "music", "podcast", "shock", "secret"},
}


def _extract_topic_keywords(words: List[Dict[str, Any]], fallback_title: str = "", hook: str = "") -> List[str]:
    stop = {
        "the", "and", "that", "this", "with", "for", "are", "was", "were", "just",
        "they", "from", "have", "what", "when", "your", "about", "like", "really",
        "here", "there", "their", "then", "than", "into", "been", "some", "more",
        "will", "would", "could", "should", "watch", "clip", "shorts",
    }
    plain: List[str] = []
    seen = set()

    def _scan_text(text: str):
        for raw_tok in re.findall(r"[A-Za-z0-9]{3,}", text or ""):
            low = raw_tok.lower()
            if len(low) >= 4 and low not in stop and low not in seen:
                seen.add(low)
                plain.append(low)

    _scan_text(hook)
    _scan_text(fallback_title)
    for w in words or []:
        if not isinstance(w, dict) or w.get("deleted") or w.get("cut_from_video"):
            continue
        _scan_text(str(w.get("word", "")))
        if len(plain) >= 10:
            break

    defaults = ["shorts", "viral", "highlights", "creator", "trending", "storytime", "reels", "fyp", "clips", "video"]
    for d in defaults:
        if len(plain) >= 12:
            break
        if d not in seen:
            seen.add(d)
            plain.append(d)
    return plain[:12]


def _extract_hashtags(words: List[Dict[str, Any]], fallback_title: str = "") -> List[str]:
    plain = _extract_topic_keywords(words, fallback_title=fallback_title)
    out = [f"#{k}" for k in plain[:5]]
    while len(out) < 5:
        out.append(["#shorts", "#viral", "#clip", "#storytime", "#trending"][len(out)])
    return out[:5]


def _infer_category_id(words: List[Dict[str, Any]], title: str = "", hook: str = "", rationale: str = "") -> str:
    combined = " ".join([
        title or "",
        hook or "",
        rationale or "",
        " ".join(str(w.get("word", "")) for w in (words or [])[:60] if isinstance(w, dict)),
    ]).lower()
    tokens = set(re.findall(r"[a-z0-9]+", combined))
    best_cat = "24"
    best_hits = 0
    for cat_id, vocab in _TOPIC_CATEGORY_HINTS.items():
        hits = len(tokens & vocab)
        if hits > best_hits:
            best_hits = hits
            best_cat = cat_id
    return best_cat if best_hits > 0 else "22"


def _ensure_shorts_title(title: str, fallback: str = "Must Watch Moment #Shorts") -> str:
    base = re.sub(r"\s+", " ", (title or "").strip()) or fallback
    if "#shorts" not in base.lower():
        if len(base) + 8 <= 100:
            base = f"{base} #Shorts"
        else:
            base = f"{base[:91].rstrip()} #Shorts"
    return base[:100].strip()


def _extract_hook_sentences(transcript_words: List[Dict[str, Any]], max_words: int = 34) -> str:
    spoken = [
        str(w.get("word", "")).strip()
        for w in (transcript_words or [])
        if isinstance(w, dict) and not w.get("deleted") and not w.get("cut_from_video") and str(w.get("word", "")).strip()
    ]
    if not spoken:
        return ""
    snippet = " ".join(spoken[:max_words]).strip()
    if len(spoken) > max_words and not snippet.endswith((".", "!", "?")):
        snippet += "..."
    return snippet


def _generate_offline_platform_copy(
    transcript_words: List[Dict[str, Any]],
    hook_headline: str = "",
    selected_title: str = "",
    existing_description: str = "",
    viral_rationale: str = "",
) -> Dict[str, Dict[str, Any]]:
    subject = _extract_anchor_subject(transcript_words, hook_headline or selected_title)
    pretty_subj = subject.title()
    keywords = _extract_topic_keywords(transcript_words, fallback_title=selected_title, hook=hook_headline)
    cat_id = _infer_category_id(transcript_words, title=selected_title, hook=hook_headline, rationale=viral_rationale)
    snippet = _extract_hook_sentences(transcript_words, max_words=32)
    summary = existing_description.strip() or (
        f'"{snippet}"' if snippet else f"Watch what happens with {pretty_subj}."
    )
    why_note = f"\n\n{viral_rationale.strip()}" if (viral_rationale and viral_rationale.strip() not in summary) else ""

    yt_title = _ensure_shorts_title(selected_title or f"{pretty_subj} Changed Everything")
    yt_tags = ["Shorts"] + [k for k in keywords if k.lower() != "shorts"][:9]
    yt_hashtags_5 = ["#Shorts", f"#{keywords[0]}", f"#{keywords[1]}", "#YouTubeShorts", "#Viral"]
    yt_desc = (
        f"{summary}{why_note}\n\n"
        f"Subscribe for more high-retention drops.\n"
        + " ".join(yt_hashtags_5[:3])
    ).strip()[:5000]
    yt_pinned = f"Wait until the ending — what did you think about {pretty_subj.lower()}?"

    ig_tags = [f"#{k}" for k in dict.fromkeys(["reels", "reelsinstagram", "explorepage", "viralreels", *keywords[:8]])][:11]
    while len(ig_tags) < 8:
        ig_tags.append(f"#creator{len(ig_tags)}")
    ig_hook = f"{hook_headline.title() if hook_headline else pretty_subj} ✨"
    ig_caption = (
        f"{ig_hook}\n\n"
        f"{summary}\n\n"
        f"Save this Reel & send it to someone who needs to see it.\n\n"
        + " ".join(ig_tags)
    )[:2200]
    ig_pinned = "Tag someone who needs to see this breakdown 🔥"

    tt_tags = [f"#{k}" for k in dict.fromkeys(["fyp", "viral", *keywords[:5]])][:7]
    while len(tt_tags) < 5:
        tt_tags.append(f"#tiktokclip{len(tt_tags)}")
    tt_hook = f"Wait for it… {pretty_subj} 👀"
    tt_caption = f"{tt_hook}\n{summary[:300]} 👇\n\n{' '.join(tt_tags)}"[:2200]
    tt_pinned = "Part 2 or full breakdown? Drop a comment 👇"

    x_tags = [f"#{k}" for k in keywords[:2]] + ["#Video"]
    x_tags = x_tags[:3]
    x_hook = hook_headline.title() if hook_headline else f"Most people miss this about {pretty_subj}:"
    x_body_budget = 278 - len(" ".join(x_tags)) - len(x_hook) - 4
    x_mid = snippet[:max(40, x_body_budget)].strip() if snippet else f"Watch the clip on {pretty_subj}."
    x_post_text = f"{x_hook}\n\n{x_mid}\n\n{' '.join(x_tags)}"[:280].strip()

    return {
        # Legacy UI / test_pro_suite_v2 compatibility keys
        "youtube_shorts": {
            "platform": "YouTube Shorts",
            "title": yt_title,
            "description": yt_desc,
            "pinned_comment": yt_pinned,
            "hashtags": yt_hashtags_5,
            "tags": yt_tags,
            "category_id": cat_id,
        },
        "tiktok": {
            "platform": "TikTok",
            "title": tt_hook,
            "description": tt_caption,
            "caption": tt_caption,
            "pinned_comment": tt_pinned,
            "hashtags": tt_tags[:5],
            "all_hashtags": tt_tags,
        },
        "instagram_reels": {
            "platform": "Instagram Reels",
            "title": ig_hook,
            "description": ig_caption,
            "caption": ig_caption,
            "pinned_comment": ig_pinned,
            "hashtags": ig_tags[:5],
            "all_hashtags": ig_tags,
        },
        "x_twitter": {
            "platform": "X (Twitter)",
            "title": x_hook,
            "description": x_post_text,
            "post_text": x_post_text,
            "pinned_comment": "Agree or disagree? Drop your take below.",
            "hashtags": (x_tags + ["#Trending", "#Clip"])[:5],
            "all_hashtags": x_tags,
        },
    }


def _to_canonical_4_platforms(normalized: Dict[str, Dict[str, Any]], cat_id: str) -> Dict[str, Dict[str, Any]]:
    yt_s = normalized.get("youtube_shorts") or {}
    ig_r = normalized.get("instagram_reels") or {}
    tt = normalized.get("tiktok") or {}
    xt = normalized.get("x_twitter") or {}

    raw_yt_tags = yt_s.get("tags") or [str(t).lstrip("#") for t in (yt_s.get("hashtags") or [])]
    clean_yt_tags = [str(t).lstrip("#").strip() for t in raw_yt_tags if str(t).strip()][:12]
    if "Shorts" not in clean_yt_tags:
        clean_yt_tags.insert(0, "Shorts")

    ig_tags = [t if str(t).startswith("#") else f"#{t}" for t in (ig_r.get("all_hashtags") or ig_r.get("hashtags") or []) if str(t).strip()]
    ig_body = str(ig_r.get("caption") or ig_r.get("description") or "").strip()
    if ig_tags and not any(t.lower() in ig_body.lower() for t in ig_tags[:3]):
        ig_body = f"{ig_body}\n\n{' '.join(ig_tags)}"
    ig_body = ig_body[:2200]

    tt_tags = [t if str(t).startswith("#") else f"#{t}" for t in (tt.get("all_hashtags") or tt.get("hashtags") or []) if str(t).strip()]
    tt_body = str(tt.get("caption") or tt.get("description") or "").strip()
    if tt_tags and not any(t.lower() in tt_body.lower() for t in tt_tags[:3]):
        tt_body = f"{tt_body}\n\n{' '.join(tt_tags)}"
    tt_body = tt_body[:2200]

    x_tags = [t if str(t).startswith("#") else f"#{t}" for t in (xt.get("all_hashtags") or xt.get("hashtags") or []) if str(t).strip()][:3]
    x_raw = str(xt.get("post_text") or xt.get("description") or xt.get("title") or "").strip()
    if x_tags and not any(t.lower() in x_raw.lower() for t in x_tags):
        join_tags = " ".join(x_tags)
        budget = max(20, 278 - len(join_tags))
        x_raw = f"{x_raw[:budget].rstrip()} {join_tags}"
    x_post = x_raw[:280].strip()

    return {
        "youtube": {
            "title": _ensure_shorts_title(str(yt_s.get("title") or "Viral Highlight #Shorts")),
            "description": str(yt_s.get("description") or "")[:5000],
            "tags": clean_yt_tags,
            "pinned_comment": str(yt_s.get("pinned_comment") or "What do you think?"),
            "category_id": str(yt_s.get("category_id") or cat_id or "22"),
        },
        "instagram": {
            "title": str(ig_r.get("title") or ""),
            "caption": ig_body,
            "pinned_comment": str(ig_r.get("pinned_comment") or "Save & share this Reel!"),
            "hashtags": ig_tags,
        },
        "tiktok": {
            "title": str(tt.get("title") or ""),
            "caption": tt_body,
            "pinned_comment": str(tt.get("pinned_comment") or "Drop your thoughts below 👇"),
            "hashtags": tt_tags,
        },
        "x": {
            "title": str(xt.get("title") or ""),
            "post_text": x_post,
            "pinned_comment": str(xt.get("pinned_comment") or ""),
            "hashtags": x_tags,
        },
    }


def generate_multi_platform_copy(
    transcript_words: List[Dict[str, Any]],
    hook_headline: str = "",
    selected_title: str = "",
    existing_description: str = "",
    api_key: Optional[str] = None,
    preferred_model: Optional[str] = None,
    viral_rationale: str = "",
    emotional_score: Optional[int] = None,
) -> Dict[str, Any]:
    """
    Generate ready-to-post copy for YouTube Shorts, Instagram Reels, TikTok, and X
    via active Gemini model with deterministic Offline AI keyword/hook fallback.
    """
    transcript_text = " ".join(
        str(w.get("word", "")).strip()
        for w in (transcript_words or [])
        if isinstance(w, dict) and not w.get("deleted") and not w.get("cut_from_video")
    ).strip()

    keywords = _extract_topic_keywords(transcript_words, fallback_title=selected_title, hook=hook_headline)
    inferred_cat = _infer_category_id(transcript_words, title=selected_title, hook=hook_headline, rationale=viral_rationale)

    if transcript_text or hook_headline or selected_title:
        prompt = (
            "You are an expert short-form social strategist.\n"
            "Return JSON with platform copy under BOTH keys:\n"
            "\"platforms\": {\"youtube_shorts\": {...}, \"tiktok\": {...}, \"instagram_reels\": {...}, \"x_twitter\": {...}}\n"
            "Rules:\n"
            "- youtube_shorts: title (<= 100 chars including #Shorts), description, pinned_comment, hashtags (5 strings), tags (8-10 keywords), category_id (20=Gaming, 24=Entertainment, 22=People, 27=Education, 28=Tech).\n"
            "- instagram_reels: title, description/caption (hook + body + 8-12 Reels hashtags, <= 2200 chars), pinned_comment, hashtags (8-12).\n"
            "- tiktok: title, description/caption (punchy hook + 5-7 hashtags, <= 2200 chars), pinned_comment, hashtags (5-7).\n"
            "- x_twitter: title, description/post_text (<= 280 chars incl. 2-3 hashtags), pinned_comment, hashtags (2-3).\n"
            f"Clip Title: {selected_title or 'Untitled'}\n"
            f"Hook Headline: {hook_headline or 'None'}\n"
            f"Why Viral / Rationale: {viral_rationale or 'High retention moment'}\n"
            f"Emotion/Arousal Score: {emotional_score if emotional_score is not None else 'N/A'}\n"
            f"Clip transcript:\n{transcript_text[:1500]}\n"
        )
        parsed = _invoke_gemini_json_prompt(prompt, api_key=api_key, preferred_model=preferred_model)
        raw_platforms = parsed.get("platforms") if isinstance(parsed, dict) else None
        if not raw_platforms and isinstance(parsed, dict) and any(k in parsed for k in ("youtube", "instagram", "tiktok", "x")):
            raw_platforms = {
                "youtube_shorts": parsed.get("youtube") or {},
                "instagram_reels": parsed.get("instagram") or {},
                "tiktok": parsed.get("tiktok") or {},
                "x_twitter": parsed.get("x") or {},
            }
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
                ]
                pool = [f"#{kw}" for kw in keywords]
                target_count = 10 if k == "instagram_reels" else (6 if k == "tiktok" else (3 if k == "x_twitter" else 5))
                if k == "youtube_shorts":
                    # Preserve exact 5 #-tags contract for youtube_shorts
                    clean_tags = clean_tags[:5]
                    while len(clean_tags) < 5:
                        cand = ["#Shorts", "#Viral", "#Content", "#Video", "#Trending"][len(clean_tags)]
                        clean_tags.append(cand)
                else:
                    for cand in pool:
                        if len(clean_tags) >= target_count:
                            break
                        if cand.lower() not in {t.lower() for t in clean_tags}:
                            clean_tags.append(cand)
                raw_all_tags = list(clean_tags)
                legacy_tags_5 = clean_tags[:5]
                while len(legacy_tags_5) < 5:
                    legacy_tags_5.append(["#Shorts", "#Viral", "#Content", "#Video", "#Trending"][len(legacy_tags_5)])
                desc_txt = str(entry.get("caption") or entry.get("post_text") or entry.get("description") or existing_description or "Check out this clip!")
                item: Dict[str, Any] = {
                    "platform": display_names[k],
                    "title": (
                        _ensure_shorts_title(str(entry.get("title") or selected_title or hook_headline or "Viral Moment"))
                        if k == "youtube_shorts"
                        else str(entry.get("title") or selected_title or hook_headline or "Viral Moment")[:100]
                    ),
                    "description": desc_txt[:280] if k == "x_twitter" else desc_txt[:2200],
                    "pinned_comment": str(entry.get("pinned_comment") or "What do you think?"),
                    "hashtags": legacy_tags_5,
                    "all_hashtags": raw_all_tags[:3] if k == "x_twitter" else raw_all_tags,
                }
                if k == "youtube_shorts":
                    raw_plain_tags = entry.get("tags") or [t.lstrip("#") for t in clean_tags] + keywords[:5]
                    item["tags"] = list(dict.fromkeys(str(t).lstrip("#").strip() for t in raw_plain_tags if str(t).strip()))[:12]
                    item["category_id"] = str(entry.get("category_id") or inferred_cat or "22")
                elif k in ("instagram_reels", "tiktok"):
                    item["caption"] = item["description"]
                elif k == "x_twitter":
                    item["post_text"] = item["description"]
                normalized[k] = item

            canonical = _to_canonical_4_platforms(normalized, inferred_cat)
            return {
                "platforms": normalized,
                **canonical,
                "source": "gemini",
                "mode": "gemini",
            }

    offline_norm = _generate_offline_platform_copy(
        transcript_words=transcript_words,
        hook_headline=hook_headline,
        selected_title=selected_title,
        existing_description=existing_description,
        viral_rationale=viral_rationale,
    )
    canonical_off = _to_canonical_4_platforms(offline_norm, inferred_cat)
    return {
        "platforms": offline_norm,
        **canonical_off,
        "source": "offline_nlp",
        "mode": "offline",
    }
