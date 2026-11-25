import re
from typing import Any, Dict, List, Tuple

# Local curated profanity list (checked offline on normalized word tokens)
PROFANITY_WORDS = {
    "fuck", "fucking", "fucked", "fucker", "fuckers", "fucks", "motherfucker", "motherfucking",
    "shit", "shitty", "shitting", "bullshit", "horseshit", "dipshit",
    "bitch", "bitches", "bitching",
    "asshole", "dumbass", "jackass", "smartass", "fatass",
    "bastard", "bastards", "cunt", "cunts", "dick", "dicks", "dickhead",
    "cock", "prick", "pussy", "slut", "whore", "twat", "wanker",
}


def _strip_token(word: str) -> str:
    return re.sub(r"^[^A-Za-z0-9']+|[^A-Za-z0-9']+$", "", (word or "").strip().lower())


def is_profane(word: str) -> bool:
    clean = _strip_token(word)
    if not clean:
        return False
    if clean in PROFANITY_WORDS:
        return True
    for root in ("fuck", "shit", "bitch", "asshole", "motherfuck", "cunt"):
        if clean.startswith(root):
            return True
    return False


def mask_profane_word(word: str) -> str:
    """Mask profane word while keeping prefix/suffix punctuation and first letter."""
    raw = (word or "").strip()
    if not is_profane(raw):
        return raw

    m = re.match(r"^([^A-Za-z0-9']*)([A-Za-z0-9']+)([^A-Za-z0-9']*)$", raw)
    if not m:
        return "***"
    pre, core, post = m.groups()
    first = core[0]
    return f"{pre}{first}***{post}"


def mask_words_profanity(words: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    masked: List[Dict[str, Any]] = []
    for w in words or []:
        nw = dict(w)
        if is_profane(str(nw.get("word", ""))):
            nw["word"] = mask_profane_word(str(nw.get("word", "")))
            nw["is_profane"] = True
        masked.append(nw)
    return masked


def detect_profanity_intervals(
    words: List[Dict[str, Any]],
    clip_start_time: float = 0.0,
    clip_duration: float = 0.0,
) -> List[Tuple[float, float]]:
    """Return sorted clip-relative (start, end) windows for every active profane word."""
    if not words:
        return []
    max_end = max(float(w.get("end", 0.0)) for w in words)
    is_abs = clip_start_time > 0.0 and (
        (clip_duration > 0.0 and max_end > clip_duration + 1.0)
        or (clip_duration <= 0.0 and min(float(w.get("start", 0.0)) for w in words) >= max(0.0, clip_start_time - 0.5))
    )

    intervals: List[Tuple[float, float]] = []
    for w in words:
        if w.get("cut_from_video"):
            continue
        if not is_profane(str(w.get("word", ""))):
            continue
        ws = float(w.get("start", 0.0))
        we = float(w.get("end", ws + 0.2))
        if is_abs:
            ws -= clip_start_time
            we -= clip_start_time
        ws = max(0.0, ws)
        we = min(clip_duration, we) if clip_duration > 0 else we
        if we > ws + 0.04:
            intervals.append((round(ws, 3), round(max(ws + 0.08, we), 3)))
    return intervals
