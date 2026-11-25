import re
import uuid
from typing import Any, Dict, List, Optional, Tuple

from PIL import Image, ImageDraw, ImageFont

# Deterministic stat / metric / number / KPI patterns
STAT_REGEXES: List[Tuple[re.Pattern[str], str, str]] = [
    (re.compile(r"^\$?\d[\d,]*(?:\.\d+)?[kmbKMB%]?\+?$", re.I), "STAT", "#C6F32F"),
    (re.compile(r"^\d+(?:\.\d+)?%$"), "METRIC", "#38BDF8"),
    (re.compile(r"^\d+(?:\.\d+)?[xX]$"), "MULTIPLIER", "#F59E0B"),
]

UNIT_KEYWORDS = {
    "million", "billion", "thousand", "percent", "dollars", "bucks", "years", "months",
    "days", "hours", "minutes", "seconds", "users", "creators", "views", "subscribers",
    "steps", "rules", "secrets", "mistakes", "reasons", "times", "x",
}

CURRENCY_PREFIX = {"$": "USD", "€": "EUR", "£": "GBP"}


def _clean_tok(text: str) -> str:
    return text.strip(".,!?;:\"'()[]{}")


def extract_callout_cards(
    words: List[Dict[str, Any]],
    clip_start_time: float = 0.0,
    clip_duration: float = 0.0,
    max_cards: int = 3,
    min_gap_sec: float = 2.6,
) -> List[Dict[str, Any]]:
    """
    Detect numbers, currencies, percentages, multipliers, and impactful metric phrases from
    clip transcript words and return editable Pop-Up Callout Cards with clip-relative timing.
    Works 100% deterministically offline.
    """
    active = [
        dict(w)
        for w in (words or [])
        if isinstance(w, dict)
        and not w.get("deleted")
        and not w.get("cut_from_video")
        and str(w.get("word", "")).strip()
    ]
    if not active:
        return []

    max_end = max(float(w.get("end", 0.0)) for w in active)
    is_abs = clip_start_time > 0.0 and (
        max_end > max(1.0, clip_duration) + 1.0
        or min(float(w.get("start", 0.0)) for w in active) >= max(0.0, clip_start_time - 0.15)
    ) and not any(bool(w.get("timeline_relative")) for w in active)

    rel_words: List[Dict[str, Any]] = []
    for w in active:
        ws = float(w.get("start", 0.0)) - (clip_start_time if is_abs else 0.0)
        we = float(w.get("end", ws + 0.25)) - (clip_start_time if is_abs else 0.0)
        if we <= 0.0:
            continue
        if clip_duration > 0.0 and ws >= clip_duration:
            continue
        nw = dict(w)
        nw["start"] = round(max(0.0, ws), 3)
        nw["end"] = round(min(clip_duration, we) if clip_duration > 0.0 else we, 3)
        rel_words.append(nw)

    candidates: List[Dict[str, Any]] = []
    n = len(rel_words)
    i = 0
    while i < n:
        raw_tok = str(rel_words[i].get("word", "")).strip()
        clean = _clean_tok(raw_tok)
        matched_badge: Optional[str] = None
        category = "KEY STAT"
        accent = "#C6F32F"
        span_end_idx = i

        has_digit = any(ch.isdigit() for ch in clean)
        if has_digit and len(clean) <= 14:
            for pat, cat, col in STAT_REGEXES:
                if pat.match(clean):
                    matched_badge = raw_tok.strip(".,!?;:\"'")
                    category = cat
                    accent = col
                    break
            if not matched_badge:
                matched_badge = clean
            # Absorb immediate unit word ("5 million", "10 years", "3 steps")
            if i + 1 < n:
                nxt_clean = _clean_tok(str(rel_words[i + 1].get("word", ""))).lower()
                if nxt_clean in UNIT_KEYWORDS:
                    matched_badge = f"{matched_badge} {nxt_clean.upper()}"
                    span_end_idx = i + 1
                    if nxt_clean in {"million", "billion", "dollars", "bucks"}:
                        category = "REVENUE"
                        accent = "#10B981"
                    elif nxt_clean in {"years", "months", "days", "hours", "minutes"}:
                        category = "TIMELINE"
                        accent = "#F59E0B"
        if matched_badge:
            t_start = max(0.0, float(rel_words[i]["start"]))
            ctx_words = [
                _clean_tok(str(rel_words[k].get("word", "")))
                for k in range(max(0, i - 1), min(n, span_end_idx + 3))
            ]
            title_txt = " ".join([cw for cw in ctx_words if cw])[:34]
            dur_cap = clip_duration if clip_duration > 0 else max(t_start + 3.0, max_end)
            t_end = min(dur_cap, max(t_start + 2.0, float(rel_words[span_end_idx]["end"]) + 1.4))
            candidates.append({
                "id": f"callout_{uuid.uuid4().hex[:8]}",
                "start": round(t_start, 2),
                "end": round(t_end, 2),
                "badge": matched_badge.upper()[:18],
                "title": title_txt or matched_badge,
                "category": category,
                "accent_color": accent,
                "enabled": True,
            })
            i = span_end_idx + 1
            continue
        i += 1

    # Greedy non-overlapping spacing
    accepted: List[Dict[str, Any]] = []
    for cand in candidates:
        if not accepted or (cand["start"] - accepted[-1]["start"]) >= min_gap_sec:
            if accepted and cand["start"] < accepted[-1]["end"] + 0.15:
                accepted[-1]["end"] = round(max(accepted[-1]["start"] + 0.8, cand["start"] - 0.1), 2)
            accepted.append(cand)
        if len(accepted) >= max_cards:
            break

    return accepted


def _hex_rgba(hex_str: str, alpha: int = 255) -> Tuple[int, int, int, int]:
    cleaned = str(hex_str or "#C6F32F").strip().lstrip("#")
    if len(cleaned) == 3:
        cleaned = "".join(c * 2 for c in cleaned)
    if len(cleaned) != 6:
        return (198, 243, 47, alpha)
    try:
        return (int(cleaned[0:2], 16), int(cleaned[2:4], 16), int(cleaned[4:6], 16), alpha)
    except Exception:
        return (198, 243, 47, alpha)


def draw_callout_card(
    canvas: Image.Image,
    card: Dict[str, Any],
    t: float,
    width: int,
    height: int,
    font_resolver: Any,
    has_hook_banner: bool = False,
) -> None:
    """Draw a high-contrast stat/keyword pop-up pill card safely above mid-frame without caption collision."""
    if not card.get("enabled", True):
        return
    cs = float(card.get("start", 0.0))
    ce = float(card.get("end", cs + 2.0))
    if not (cs <= t <= ce):
        return

    badge_txt = str(card.get("badge", "")).strip() or "STAT"
    title_txt = str(card.get("title", "")).strip() or badge_txt
    accent_rgba = _hex_rgba(str(card.get("accent_color") or "#C6F32F"), 255)

    # Ease-out pop scale during the first 180ms
    dt = max(0.0, t - cs)
    pop_scale = 1.0 + 0.08 * (1.0 - min(1.0, dt / 0.18))

    badge_px = max(18, int(round(height * 0.024 * pop_scale)))
    title_px = max(15, int(round(height * 0.018 * pop_scale)))
    badge_font = font_resolver("Montserrat", badge_px, bold=True)
    title_font = font_resolver("Montserrat", title_px, bold=False)

    draw = ImageDraw.Draw(canvas, "RGBA")
    b_box = draw.textbbox((0, 0), badge_txt, font=badge_font)
    t_box = draw.textbbox((0, 0), title_txt, font=title_font)
    bw, bh = b_box[2] - b_box[0], b_box[3] - b_box[1]
    tw, th = t_box[2] - t_box[0], t_box[3] - t_box[1]

    pad_x = max(14, int(width * 0.02))
    pad_y = max(8, int(height * 0.008))
    pill_w = min(int(width * 0.86), max(bw + pad_x * 2, tw + pad_x * 2) + 18)
    pill_h = bh + th + pad_y * 3 + 4

    x0 = max(int(width * 0.05), width - int(width * 0.06) - pill_w)
    y_pct = 0.21 if has_hook_banner else 0.13
    y0 = int(height * y_pct)

    draw.rounded_rectangle(
        [x0, y0, x0 + pill_w, y0 + pill_h],
        radius=16,
        fill=(14, 15, 19, 224),
        outline=accent_rgba,
        width=max(2, int(round(width * 0.003))),
    )
    # Left accent stripe
    draw.rounded_rectangle(
        [x0 + 4, y0 + 6, x0 + 10, y0 + pill_h - 6],
        radius=3,
        fill=accent_rgba,
    )
    draw.text((x0 + 18, y0 + pad_y - b_box[1]), badge_txt, font=badge_font, fill=accent_rgba)
    draw.text(
        (x0 + 18, y0 + pad_y + bh + pad_y - t_box[1]),
        title_txt,
        font=title_font,
        fill=(244, 244, 248, 240),
    )


def draw_speaker_lower_third(
    canvas: Image.Image,
    turn: Dict[str, Any],
    t: float,
    width: int,
    height: int,
    font_resolver: Any,
    has_hook_banner: bool = False,
) -> None:
    """Render an auto speaker nameplate pill at speaker changeovers."""
    ts = float(turn.get("start", 0.0))
    te = float(turn.get("end", ts + 2.5))
    if not (ts <= t <= te):
        return

    name = str(turn.get("name") or "SPEAKER 1").strip().upper()
    dot_rgba = _hex_rgba(str(turn.get("color") or "#C6F32F"), 255)
    px_size = max(16, int(round(height * 0.019)))
    fnt = font_resolver("Montserrat", px_size, bold=True)

    draw = ImageDraw.Draw(canvas, "RGBA")
    bbox = draw.textbbox((0, 0), name, font=fnt)
    tw, th = bbox[2] - bbox[0], bbox[3] - bbox[1]
    dot_d = max(10, int(round(th * 0.65)))
    pad_x = max(14, int(width * 0.02))
    pad_y = max(8, int(height * 0.008))

    pill_w = pad_x * 2 + dot_d + 10 + tw
    pill_h = pad_y * 2 + th
    x0 = int(width * 0.055)
    y0 = int(height * (0.21 if has_hook_banner else 0.13))

    draw.rounded_rectangle(
        [x0, y0, x0 + pill_w, y0 + pill_h],
        radius=pill_h // 2,
        fill=(12, 13, 18, 220),
        outline=(255, 255, 255, 55),
        width=2,
    )
    dot_y0 = y0 + (pill_h - dot_d) // 2
    draw.ellipse([x0 + pad_x, dot_y0, x0 + pad_x + dot_d, dot_y0 + dot_d], fill=dot_rgba)
    draw.text(
        (x0 + pad_x + dot_d + 10, y0 + (pill_h - th) // 2 - bbox[1]),
        name,
        font=fnt,
        fill=(255, 255, 255, 245),
    )
