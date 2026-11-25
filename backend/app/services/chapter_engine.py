"""
Lost Heaven — Animated Mini-Chapter Pills Engine (Phase 2).
Generates 2–4 concise timeline mini-chapters from timed words (via Gemini with deterministic
offline segmentation fallback) and draws sleek frosted-glass corner badges with slide-in ease.
"""

from __future__ import annotations

import json
import logging
import re
from typing import Any, Callable, Dict, List, Optional, Tuple

from PIL import Image, ImageDraw

logger = logging.getLogger(__name__)

_STOPWORDS = {
    "the", "a", "an", "and", "or", "but", "if", "so", "that", "this", "these", "those",
    "it", "its", "is", "are", "was", "were", "be", "been", "being", "to", "of", "in",
    "on", "for", "with", "at", "by", "from", "up", "out", "you", "your", "i", "we",
    "they", "he", "she", "just", "like", "really", "very", "actually", "literally",
    "then", "when", "what", "why", "how", "here", "there", "now", "about",
}


def _parse_hex_rgb(hex_str: Optional[str], default: Tuple[int, int, int] = (139, 92, 246)) -> Tuple[int, int, int]:
    raw = str(hex_str or "").strip().lstrip("#")
    if len(raw) == 3:
        raw = "".join(ch * 2 for ch in raw)
    if len(raw) >= 6:
        try:
            return (int(raw[0:2], 16), int(raw[2:4], 16), int(raw[4:6], 16))
        except ValueError:
            pass
    return default


def normalize_chapters(chapters: Any, clip_duration: float = 30.0) -> List[Dict[str, Any]]:
    """Sanitize and clamp chapter objects into monotonic non-overlapping clip-relative intervals."""
    if not isinstance(chapters, list):
        return []
    dur = max(1.0, float(clip_duration or 30.0))
    cleaned: List[Dict[str, Any]] = []
    for idx, ch in enumerate(chapters):
        if not isinstance(ch, dict):
            continue
        title = str(ch.get("title") or "").strip()
        if not title:
            title = f"Part {idx + 1}"
        # Keep concise pill titles
        words = title.split()
        if len(words) > 5:
            title = " ".join(words[:5])
        s = max(0.0, min(dur - 0.5, float(ch.get("start", 0.0))))
        e = max(s + 0.5, min(dur, float(ch.get("end", dur))))
        cleaned.append({
            "id": str(ch.get("id") or f"ch_{idx + 1}"),
            "start": round(s, 3),
            "end": round(e, 3),
            "title": title,
        })

    cleaned.sort(key=lambda item: item["start"])
    if not cleaned:
        return []

    # Enforce seamless contiguous boundaries without overlap
    cleaned[0]["start"] = 0.0
    for i in range(len(cleaned) - 1):
        nxt_s = max(cleaned[i]["start"] + 0.5, cleaned[i + 1]["start"])
        cleaned[i]["end"] = round(min(dur, nxt_s), 3)
        cleaned[i + 1]["start"] = cleaned[i]["end"]
    cleaned[-1]["end"] = round(max(cleaned[-1]["start"] + 0.5, dur), 3)
    return cleaned


def _summarize_segment_title(tokens: List[str], idx: int) -> str:
    clean_words = []
    for raw in tokens:
        w = re.sub(r"[^A-Za-z0-9'\-]", "", raw)
        if w and w.lower() not in _STOPWORDS and len(w) >= 2:
            clean_words.append(w)
        if len(clean_words) >= 3:
            break
    if not clean_words:
        defaults = ["The Hook", "Core Breakdown", "Key Turning Point", "The Payoff"]
        return defaults[min(idx, len(defaults) - 1)]
    return " ".join(w.capitalize() if w.islower() else w for w in clean_words[:3])


def _heuristic_chapters(
    words: List[Dict[str, Any]],
    clip_start_time: float,
    clip_duration: float,
    max_chapters: int = 4,
) -> List[Dict[str, Any]]:
    dur = max(1.0, float(clip_duration or 30.0))
    rel_words: List[Dict[str, Any]] = []
    for w in (words or []):
        txt = str(w.get("word") or w.get("text") or "").strip()
        if not txt:
            continue
        ws = float(w.get("start", 0.0))
        # Handle both clip-relative and project-absolute timestamps gracefully
        if clip_start_time > 0.5 and ws >= (clip_start_time - 0.5):
            ws -= clip_start_time
        we = float(w.get("end", ws + 0.25))
        if clip_start_time > 0.5 and we >= (clip_start_time - 0.5):
            we -= clip_start_time
        if -0.2 <= ws <= dur + 0.2:
            rel_words.append({
                "start": max(0.0, ws),
                "end": min(dur, max(ws + 0.05, we)),
                "word": txt,
            })

    target_count = min(max(2, max_chapters), max(2, min(4, int(round(dur / 7.5)) or 2)))
    if len(rel_words) < 4:
        # Fallback even without rich transcript
        labels = ["Opening Hook", "Key Insight", "Main Payoff", "Final Takeaway"][:target_count]
        step = dur / len(labels)
        return normalize_chapters(
            [
                {
                    "id": f"ch_{i + 1}",
                    "start": round(i * step, 3),
                    "end": round((i + 1) * step, 3),
                    "title": label,
                }
                for i, label in enumerate(labels)
            ],
            clip_duration=dur,
        )

    # Find split indices via pause gaps + sentence boundaries
    candidates: List[Tuple[float, int]] = []
    for i in range(1, len(rel_words)):
        gap = rel_words[i]["start"] - rel_words[i - 1]["end"]
        prev_txt = rel_words[i - 1]["word"]
        punct_bonus = 0.6 if prev_txt.endswith((".", "?", "!")) else 0.0
        score = gap + punct_bonus
        if score >= 0.35:
            candidates.append((score, i))

    split_indices: List[int] = [0]
    min_seg_sec = max(1.5, dur / (target_count * 1.8))
    for _, idx in sorted(candidates, key=lambda x: -x[0]):
        if len(split_indices) >= target_count:
            break
        t_cand = rel_words[idx]["start"]
        if t_cand < min_seg_sec or (dur - t_cand) < min_seg_sec:
            continue
        if all(abs(t_cand - rel_words[s]["start"]) >= min_seg_sec for s in split_indices):
            split_indices.append(idx)

    while len(split_indices) < target_count and len(rel_words) >= target_count * 2:
        desired_t = (dur * len(split_indices)) / target_count
        best_i = min(
            range(1, len(rel_words) - 1),
            key=lambda idx: abs(rel_words[idx]["start"] - desired_t),
        )
        if best_i not in split_indices:
            split_indices.append(best_i)
        else:
            break

    split_indices = sorted(set(split_indices))
    chapters: List[Dict[str, Any]] = []
    for c_idx, start_i in enumerate(split_indices):
        end_i = split_indices[c_idx + 1] if c_idx + 1 < len(split_indices) else len(rel_words)
        seg_tokens = [w["word"] for w in rel_words[start_i:end_i]]
        s_t = 0.0 if c_idx == 0 else rel_words[start_i]["start"]
        e_t = dur if c_idx == len(split_indices) - 1 else rel_words[end_i]["start"]
        chapters.append({
            "id": f"ch_{c_idx + 1}",
            "start": round(s_t, 3),
            "end": round(e_t, 3),
            "title": _summarize_segment_title(seg_tokens, c_idx),
        })

    return normalize_chapters(chapters, clip_duration=dur)


def generate_clip_chapters(
    words: List[Dict[str, Any]],
    clip_start_time: float = 0.0,
    clip_duration: float = 30.0,
    max_chapters: int = 4,
    api_key: Optional[str] = None,
    model: Optional[str] = None,
) -> List[Dict[str, Any]]:
    """
    Generate 2-4 crisp mini-chapters for a clip. Uses Gemini if an API key is provided
    and falls back deterministically to pause/sentence segmentation when offline.
    """
    fallback = _heuristic_chapters(words, clip_start_time, clip_duration, max_chapters=max_chapters)
    if not api_key or not words:
        return fallback

    try:
        from google import genai
        client = genai.Client(api_key=api_key)
        model_id = str(model or "gemini-2.5-flash").replace("models/", "")
        lines = []
        for w in words[:260]:
            ws = float(w.get("start", 0.0))
            if clip_start_time > 0.5 and ws >= (clip_start_time - 0.5):
                ws -= clip_start_time
            lines.append(f"[{ws:.2f}s] {w.get('word', '')}")
        prompt = (
            f"Divide this {clip_duration:.1f}s vertical video transcript into 2 to {max_chapters} punchy "
            f"mini-chapters with 2-4 word titles.\n"
            f"Return ONLY valid JSON array: [{{\"start\": 0.0, \"end\": 8.5, \"title\": \"Short Title\"}}].\n"
            f"Transcript:\n" + " ".join(lines)
        )
        resp = client.models.generate_content(model=model_id, contents=prompt)
        text = str(getattr(resp, "text", "") or "").strip()
        m = re.search(r"\[.*\]", text, re.S)
        if m:
            parsed = json.loads(m.group(0))
            norm = normalize_chapters(parsed, clip_duration=clip_duration)
            if len(norm) >= 2:
                return norm[:max_chapters]
    except Exception as exc:
        logger.debug("Gemini chapter refinement unavailable, using heuristic chapters: %s", exc)

    return fallback


def draw_chapter_pill(
    canvas: Image.Image,
    chapters: Optional[List[Dict[str, Any]]],
    t: float,
    width: int,
    height: int,
    accent_color: str = "#8B5CF6",
    font_resolver: Optional[Callable[[int], Any]] = None,
    has_hook_banner: bool = False,
) -> Image.Image:
    """Render frosted glassmorphic active chapter pill with smooth 0.25s slide-in entrance."""
    if not chapters:
        return canvas

    active_idx = -1
    active_ch: Optional[Dict[str, Any]] = None
    for idx, ch in enumerate(chapters):
        s = float(ch.get("start", 0.0))
        e = float(ch.get("end", 9999.0))
        if s <= t < e or (idx == len(chapters) - 1 and t >= s):
            active_idx = idx
            active_ch = ch
            break
    if active_ch is None:
        return canvas

    base = canvas.convert("RGBA") if canvas.mode != "RGBA" else canvas
    overlay = Image.new("RGBA", (width, height), (0, 0, 0, 0))
    draw = ImageDraw.Draw(overlay, "RGBA")

    local_t = max(0.0, float(t) - float(active_ch.get("start", 0.0)))
    prog = min(1.0, local_t / 0.25)
    ease = 1.0 - (1.0 - prog) ** 3
    slide_offset_x = -int((1.0 - ease) * max(18, width * 0.035))
    alpha_scale = 0.35 + 0.65 * ease

    ar, ag, ab = _parse_hex_rgb(accent_color, (139, 92, 246))
    font_size = max(14, int(width * 0.029))
    small_size = max(11, int(font_size * 0.82))
    font_main = font_resolver(font_size) if font_resolver else None
    font_badge = font_resolver(small_size) if font_resolver else None

    badge_txt = f"{active_idx + 1:02d}"
    title_txt = str(active_ch.get("title") or "").strip().upper()

    if font_main and hasattr(draw, "textbbox"):
        tb = draw.textbbox((0, 0), title_txt, font=font_main)
        title_w = tb[2] - tb[0]
        title_h = tb[3] - tb[1]
    else:
        title_w = int(len(title_txt) * font_size * 0.62)
        title_h = font_size

    badge_w = max(int(font_size * 1.45), 24)
    pad_x = max(12, int(width * 0.022))
    pad_y = max(7, int(height * 0.008))
    pill_w = min(int(width * 0.86), badge_w + 10 + title_w + pad_x * 2)
    pill_h = max(title_h + pad_y * 2, int(font_size * 1.8))

    px0 = int(width * 0.045) + slide_offset_x
    py0 = int(height * (0.155 if has_hook_banner else 0.075))
    px1 = px0 + pill_w
    py1 = py0 + pill_h

    draw.rounded_rectangle(
        [px0, py0, px1, py1],
        radius=max(10, pill_h // 2),
        fill=(14, 19, 32, int(205 * alpha_scale)),
        outline=(255, 255, 255, int(68 * alpha_scale)),
        width=max(1, int(width * 0.002)),
    )

    # Glowing chapter index pill
    bx0 = px0 + int(pad_x * 0.65)
    by0 = py0 + 4
    bx1 = bx0 + badge_w
    by1 = py1 - 4
    draw.rounded_rectangle(
        [bx0, by0, bx1, by1],
        radius=max(6, (by1 - by0) // 2),
        fill=(ar, ag, ab, int(235 * alpha_scale)),
    )
    draw.text(
        (bx0 + badge_w // 2, (by0 + by1) // 2),
        badge_txt,
        font=font_badge,
        fill=(255, 255, 255, int(250 * alpha_scale)),
        anchor="mm",
    )

    draw.text(
        (bx1 + 9, (py0 + py1) // 2),
        title_txt,
        font=font_main,
        fill=(248, 250, 252, int(248 * alpha_scale)),
        anchor="lm",
    )

    return Image.alpha_composite(base, overlay)
