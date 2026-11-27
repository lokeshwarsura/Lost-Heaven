import os
import re
import json
import urllib.parse
import urllib.request
import logging
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from app.config import settings

logger = logging.getLogger(__name__)

_STOPWORDS = {
    "this", "that", "with", "from", "have", "what", "when", "where", "which",
    "there", "their", "they", "them", "been", "were", "will", "would", "could",
    "should", "about", "after", "before", "because", "really", "actually", "just",
    "like", "know", "your", "more", "some", "into", "than", "then", "also", "only",
    "here", "make", "made", "want", "need", "going", "right", "look", "good", "very",
}

VIDEO_EXTS = {".mp4", ".mov", ".mkv", ".webm"}
IMAGE_EXTS = {".jpg", ".jpeg", ".png", ".webp"}


def get_broll_root() -> Path:
    root = Path(settings.STORAGE_DIR) / "broll"
    root.mkdir(parents=True, exist_ok=True)
    return root


def extract_broll_keywords(
    words: List[Dict[str, Any]],
    max_keywords: int = 5,
) -> List[Dict[str, Any]]:
    """
    Extract salient noun/topic keywords with suggested [start, end] cutaway intervals.
    """
    candidates: List[Dict[str, Any]] = []
    seen_tokens = set()
    for w in (words or []):
        if not isinstance(w, dict) or w.get("deleted") or w.get("cut_from_video"):
            continue
        raw = str(w.get("word", "")).strip()
        tok = re.sub(r"[^a-zA-Z0-9_-]", "", raw).lower()
        if len(tok) < 4 or tok in _STOPWORDS or tok in seen_tokens:
            continue
        seen_tokens.add(tok)
        ws = float(w.get("start", 0.0))
        candidates.append({
            "keyword": tok,
            "anchor_time": round(ws, 2),
            "len_score": len(tok),
        })
    candidates.sort(key=lambda x: (-x["len_score"], x["anchor_time"]))
    return candidates[:max_keywords]


def _scan_local_broll(keyword: str) -> Optional[str]:
    root = get_broll_root()
    kw = keyword.lower()
    for p in sorted(root.rglob("*")):
        if not p.is_file():
            continue
        if p.suffix.lower() not in (VIDEO_EXTS | IMAGE_EXTS):
            continue
        stem = p.stem.lower()
        if kw in stem or any(part in kw for part in re.split(r"[_\-\s]+", stem) if len(part) >= 3):
            return str(p.resolve())
    return None


def _search_and_download_pexels(keyword: str, api_key: str) -> Optional[str]:
    if not api_key or not api_key.strip():
        return None
    cache_dir = get_broll_root() / "pexels"
    cache_dir.mkdir(parents=True, exist_ok=True)
    safe_kw = re.sub(r"[^a-zA-Z0-9_-]", "_", keyword.lower())
    cached = cache_dir / f"{safe_kw}.mp4"
    if cached.exists() and cached.stat().st_size > 1024:
        return str(cached.resolve())

    try:
        q = urllib.parse.quote(keyword)
        req = urllib.request.Request(
            f"https://api.pexels.com/videos/search?query={q}&per_page=1&orientation=portrait",
            headers={"Authorization": api_key.strip(), "User-Agent": "LostHeavenStudio/4.0"},
        )
        with urllib.request.urlopen(req, timeout=8) as resp:
            payload = json.loads(resp.read().decode("utf-8"))
        videos = payload.get("videos") or []
        if not videos:
            return None
        files = videos[0].get("video_files") or []
        mp4_files = [f for f in files if str(f.get("file_type", "")).endswith("mp4")]
        if not mp4_files:
            return None
        chosen = sorted(mp4_files, key=lambda x: abs(int(x.get("width") or 720) - 720))[0]
        dl_url = chosen.get("link")
        if not dl_url:
            return None
        dl_req = urllib.request.Request(dl_url, headers={"User-Agent": "LostHeavenStudio/4.0"})
        with urllib.request.urlopen(dl_req, timeout=20) as dl_resp:
            data = dl_resp.read()
        if data:
            cached.write_bytes(data)
            return str(cached.resolve())
    except Exception as e:
        logger.debug(f"Optional Pexels B-Roll lookup failed for '{keyword}': {e}")
    return None


def suggest_broll_cutaways(
    words: List[Dict[str, Any]],
    clip_start_time: float,
    clip_duration: float,
    pexels_api_key: str = "",
) -> List[Dict[str, Any]]:
    """
    Generate non-overlapping B-Roll cutaway suggestions in clip-relative timestamps.
    Prefers matching local files in `storage/broll/` and optionally queries Pexels if configured.
    """
    dur = max(1.0, float(clip_duration or 1.0))
    norm_words: List[Dict[str, Any]] = []
    max_end = max((float(w.get("end", 0.0)) for w in (words or []) if isinstance(w, dict)), default=0.0)
    is_abs = clip_start_time > 0.0 and max_end > dur + 1.0
    for w in (words or []):
        if not isinstance(w, dict):
            continue
        nw = dict(w)
        if is_abs:
            nw["start"] = max(0.0, float(nw.get("start", 0.0)) - clip_start_time)
            nw["end"] = min(dur, float(nw.get("end", 0.0)) - clip_start_time)
        norm_words.append(nw)

    kws = extract_broll_keywords(norm_words, max_keywords=5)
    suggestions: List[Dict[str, Any]] = []
    last_end = -1.0

    for item in kws:
        kw = item["keyword"]
        s = max(1.0, round(item["anchor_time"], 2))
        e = min(dur - 0.8, round(s + 2.2, 2))
        if e <= s + 0.6 or s < last_end + 1.2:
            continue

        local_asset = _scan_local_broll(kw)
        source = "local" if local_asset else "unassigned"
        asset_path = local_asset
        if not asset_path and pexels_api_key:
            px_asset = _search_and_download_pexels(kw, pexels_api_key)
            if px_asset:
                asset_path = px_asset
                source = "pexels"

        suggestions.append({
            "keyword": kw,
            "start": s,
            "end": e,
            "asset_path": asset_path or "",
            "source": source,
            "enabled": bool(asset_path),
        })
        last_end = e
        if len(suggestions) >= 3:
            break

    return suggestions


def build_broll_overlay_filter(
    base_video_label: str,
    broll_specs: List[Tuple[Any, ...]],
    target_w: int,
    target_h: int,
    fps: float = 30.0,
    out_label: str = "v_broll",
    default_layout: str = "fullscreen",
    reaction_corner: str = "top_right",
) -> Tuple[str, str]:
    """
    Overlay B-Roll video/image assets onto `base_video_label` using center-fill crop
    and gentle Ken Burns zoom for still images cleanly inside `[start, end]`.
    Supports both:
      - `"fullscreen"`: Full-Screen Cutaway
      - `"reaction_pip"`: Full-Screen B-Roll + Speaker Reaction rounded PiP card
    `broll_specs`: list of `(input_idx, start_sec, end_sec, is_image)` or
      `(input_idx, start_sec, end_sec, is_image, layout)`.
    """
    if not broll_specs:
        return "", base_video_label

    from app.services.reframer import _rounded_pip_alpha_filter, _pip_safe_xy

    parts: List[str] = []
    curr_v = base_video_label

    pip_w = max(96, int(round(target_w * 0.30)) // 2 * 2)
    pip_h = max(96, int(round(pip_w * 1.15)) // 2 * 2)
    border_px = 2
    card_w = pip_w + border_px * 2
    card_h = pip_h + border_px * 2
    px, py = _pip_safe_xy(reaction_corner, target_w, target_h, card_w, card_h)
    round_mask_f = _rounded_pip_alpha_filter(radius=16)

    for idx, spec in enumerate(broll_specs):
        in_idx = int(spec[0])
        s_t = float(spec[1])
        e_t = float(spec[2])
        is_image = bool(spec[3])
        seg_layout = str(spec[4] if len(spec) > 4 and spec[4] else default_layout).strip().lower()

        seg_dur = max(0.3, e_t - s_t)
        br_lbl = f"broll_in_{idx}"
        out_step = out_label if idx == len(broll_specs) - 1 else f"v_br_{idx}"
        if is_image:
            frames = max(2, int(round(seg_dur * fps)))
            parts.append(
                f"[{in_idx}:v]scale={int(target_w * 1.15)}:{int(target_h * 1.15)}:force_original_aspect_ratio=increase,"
                f"crop={int(target_w * 1.15)}:{int(target_h * 1.15)},"
                f"zoompan=z='min(1.12,1.0+0.12*on/{frames})':d=1:s={target_w}x{target_h}:fps={fps:.2f},"
                f"trim=0:{seg_dur:.3f},setpts=PTS-STARTPTS+{s_t:.3f}/TB[{br_lbl}]"
            )
        else:
            parts.append(
                f"[{in_idx}:v]scale={target_w}:{target_h}:force_original_aspect_ratio=increase,"
                f"crop={target_w}:{target_h},trim=0:{seg_dur:.3f},setpts=PTS-STARTPTS+{s_t:.3f}/TB[{br_lbl}]"
            )

        if seg_layout == "reaction_pip":
            base_s = f"v_br_main_{idx}"
            pip_src = f"v_br_pip_{idx}"
            pip_card = f"v_br_card_{idx}"
            mid_s = f"v_br_mid_{idx}"
            parts.append(f"[{curr_v}]split=2[{base_s}][{pip_src}]")
            parts.append(
                f"[{base_s}][{br_lbl}]overlay=0:0:enable='between(t,{s_t:.3f},{e_t:.3f})':eof_action=pass[{mid_s}]"
            )
            parts.append(
                f"[{pip_src}]crop=iw*0.72:ih*0.62:iw*0.14:ih*0.10,"
                f"scale={pip_w}:{pip_h}:force_original_aspect_ratio=increase,"
                f"crop={pip_w}:{pip_h},format=rgba,"
                f"pad={card_w}:{card_h}:{border_px}:{border_px}:0xFFFFFFD8,"
                f"{round_mask_f}[{pip_card}]"
            )
            parts.append(
                f"[{mid_s}][{pip_card}]overlay={px}:{py}:enable='between(t,{s_t:.3f},{e_t:.3f})':eof_action=pass[{out_step}]"
            )
        else:
            parts.append(
                f"[{curr_v}][{br_lbl}]overlay=0:0:enable='between(t,{s_t:.3f},{e_t:.3f})':eof_action=pass[{out_step}]"
            )
        curr_v = out_step

    return ";".join(parts), curr_v
