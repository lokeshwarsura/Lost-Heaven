"""
Lost Heaven — Platform Demonetization, Headroom & Safe-Zone Pre-Flight Scanner (Phase 3).
Audits audio clipping/loudness, profanity/demonetization keywords, and 9:16/16:9 UI chrome
safe zones, returning an actionable Readiness Score (0-100) with 1-Click Fix patches.
"""

from __future__ import annotations

import logging
import math
import os
import re
import shutil
import subprocess
import tempfile
from typing import Any, Dict, List, Optional

import numpy as np

from app.services.profanity_filter import PROFANITY_WORDS
from app.services.proxy import get_ffmpeg_cmd

logger = logging.getLogger(__name__)

_AD_RISK_KEYWORDS = {
    "kill", "killed", "killing", "murder", "suicide", "self-harm", "overdose",
    "cocaine", "heroin", "meth", "gore", "nsfw", "porn", "scam", "fraud",
}


def _clean_token(word: str) -> str:
    return re.sub(r"[^a-z0-9\-]", "", str(word or "").lower())


def _measure_audio_levels(
    media_path: str,
    start_sec: float = 0.0,
    duration_sec: float = 15.0,
) -> Optional[Dict[str, float]]:
    if not media_path or not os.path.exists(media_path):
        return None
    ffmpeg_bin = get_ffmpeg_cmd() or "ffmpeg"
    exec_dir = tempfile.mkdtemp(prefix="lost_heaven_pf_")
    try:
        ext = os.path.splitext(media_path)[1] or ".mp4"
        staged = os.path.join(exec_dir, f"src{ext}")
        raw_pcm = os.path.join(exec_dir, "audio.s16le")
        try:
            shutil.copy2(media_path, staged)
            in_target = staged
        except OSError:
            in_target = media_path

        dur = min(25.0, max(1.0, float(duration_sec or 15.0)))
        cmd = [
            ffmpeg_bin, "-v", "error", "-y",
            "-ss", f"{max(0.0, float(start_sec or 0.0)):.3f}",
            "-i", in_target,
            "-t", f"{dur:.3f}",
            "-vn", "-ac", "1", "-ar", "22050", "-f", "s16le", "-acodec", "pcm_s16le",
            raw_pcm,
        ]
        res = subprocess.run(cmd, cwd=exec_dir, capture_output=True, text=True, timeout=15)
        if res.returncode != 0 or not os.path.exists(raw_pcm):
            return None
        raw = open(raw_pcm, "rb").read()
        if len(raw) < 4:
            return None
        samples = np.frombuffer(raw, dtype=np.int16).astype(np.float32) / 32768.0
        peak = float(np.max(np.abs(samples)))
        rms = float(np.sqrt(np.mean(samples ** 2)))
        rms_dbfs = 20.0 * math.log10(max(1e-6, rms))
        peak_dbfs = 20.0 * math.log10(max(1e-6, peak))
        return {"peak": peak, "peak_dbfs": round(peak_dbfs, 2), "rms_dbfs": round(rms_dbfs, 2)}
    except Exception as exc:
        logger.debug("Preflight audio scan skipped: %s", exc)
        return None
    finally:
        shutil.rmtree(exec_dir, ignore_errors=True)


def scan_clip_preflight(clip: Any, media_path: Optional[str] = None) -> Dict[str, Any]:
    """Run platform demonetization, audio clipping, and safe-zone UI overlap audits on a clip."""
    checks: List[Dict[str, Any]] = []

    # 1. Profanity & Ad-Safety Keyword Scan
    all_text_tokens: List[str] = []
    if hasattr(clip, "get_transcript"):
        for w in (clip.get_transcript() or []):
            all_text_tokens.append(str(w.get("word") or ""))
    all_text_tokens.extend(str(getattr(clip, "hook_headline", "") or "").split())
    all_text_tokens.extend(str(getattr(clip, "selected_title", "") or "").split())

    flagged_terms: List[str] = []
    seen = set()
    for raw_tok in all_text_tokens:
        tok = _clean_token(raw_tok)
        if not tok:
            continue
        if (tok in PROFANITY_WORDS or tok in _AD_RISK_KEYWORDS) and tok not in seen:
            seen.add(tok)
            flagged_terms.append(tok)

    censor_on = bool(getattr(clip, "censor_profanity", False))
    if flagged_terms and not censor_on:
        checks.append({
            "id": "ad_safety",
            "title": "Ad-Safety & Profanity",
            "status": "fail",
            "detail": f"Detected {len(flagged_terms)} sensitive term(s) ({', '.join(flagged_terms[:3])}) without Auto-Bleep.",
            "fix_label": "Enable Auto-Bleep",
            "fix_patch": {"censor_profanity": True},
        })
    elif flagged_terms and censor_on:
        checks.append({
            "id": "ad_safety",
            "title": "Ad-Safety & Profanity",
            "status": "pass",
            "detail": f"{len(flagged_terms)} flagged term(s) safely masked by Auto-Bleep.",
        })
    else:
        checks.append({
            "id": "ad_safety",
            "title": "Ad-Safety & Profanity",
            "status": "pass",
            "detail": "Transcript & hook headline passed demonetization scan.",
        })

    # 2. Safe-Zone Overlap Check (Shorts / Reels / TikTok UI Chrome)
    aspect = str(getattr(clip, "aspect_ratio", "9:16") or "9:16").strip()
    burn_caps = bool(getattr(clip, "burn_captions", True))
    pos_x = float(getattr(clip, "position_x", 50.0) if getattr(clip, "position_x", None) is not None else 50.0)
    pos_y = float(getattr(clip, "position_y", 74.0) if getattr(clip, "position_y", None) is not None else 74.0)
    spectrum_on = bool(getattr(clip, "spectrum_enabled", False))
    spectrum_pos = str(getattr(clip, "spectrum_position", "bottom") or "bottom").lower()

    if burn_caps and aspect in {"9:16", "4:5"} and (pos_y > 83.0 or pos_y < 11.0 or (pos_x > 80.0 and pos_y > 45.0)):
        checks.append({
            "id": "safe_zone",
            "title": "Platform Safe-Zone",
            "status": "fail",
            "detail": f"Captions at ({int(pos_x)}%, {int(pos_y)}%) overlap Shorts/TikTok UI chrome.",
            "fix_label": "Snap to Safe Zone",
            "fix_patch": {"position_x": 50.0, "position_y": 74.0, "position_grid": "bottom_center"},
        })
    elif burn_caps and spectrum_on and spectrum_pos == "bottom" and pos_y >= 80.0:
        checks.append({
            "id": "safe_zone",
            "title": "Platform Safe-Zone",
            "status": "warn",
            "detail": f"Captions at y={int(pos_y)}% sit close to the bottom audio spectrum visualizer.",
            "fix_label": "Lift Captions Above Bar",
            "fix_patch": {"position_y": 70.0, "position_grid": "bottom_center"},
        })
    else:
        checks.append({
            "id": "safe_zone",
            "title": "Platform Safe-Zone",
            "status": "pass",
            "detail": f"Captions & overlays positioned cleanly within {aspect} safe bounds.",
        })

    # 3. Audio Clipping & Headroom Check
    norm_on = bool(getattr(clip, "normalize_audio", True))
    vol = float(getattr(clip, "audio_volume", 1.0) or 1.0)
    levels = _measure_audio_levels(
        media_path or "",
        start_sec=float(getattr(clip, "start_time", 0.0) or 0.0),
        duration_sec=float(getattr(clip, "duration", 15.0) or 15.0),
    )

    if levels is not None:
        eff_peak = levels["peak"] * vol
        eff_rms_db = levels["rms_dbfs"] + (20.0 * math.log10(max(0.1, vol)))
        if not norm_on and eff_peak >= 0.96:
            checks.append({
                "id": "audio_headroom",
                "title": "Audio Clipping & LUFS",
                "status": "fail",
                "detail": f"Estimated peaks reach {levels['peak_dbfs']:.1f} dBFS (risks distortion).",
                "fix_label": "Normalize Audio (-14 LUFS)",
                "fix_patch": {"normalize_audio": True, "audio_volume": 0.92},
            })
        elif not norm_on and eff_rms_db < -28.0:
            checks.append({
                "id": "audio_headroom",
                "title": "Audio Clipping & LUFS",
                "status": "warn",
                "detail": f"Dialogue level is quiet ({eff_rms_db:.1f} dBFS) without normalization.",
                "fix_label": "Boost & Normalize",
                "fix_patch": {"normalize_audio": True, "audio_volume": 1.15},
            })
        else:
            checks.append({
                "id": "audio_headroom",
                "title": "Audio Clipping & LUFS",
                "status": "pass",
                "detail": f"Peak {levels['peak_dbfs']:.1f} dBFS · RMS {levels['rms_dbfs']:.1f} dBFS{' (Normalized)' if norm_on else ''}.",
            })
    elif not norm_on and vol > 1.35:
        checks.append({
            "id": "audio_headroom",
            "title": "Audio Clipping & LUFS",
            "status": "warn",
            "detail": f"Manual gain at {int(vol * 100)}% without Loudness Normalization risks clipping.",
            "fix_label": "Enable Normalization",
            "fix_patch": {"normalize_audio": True, "audio_volume": 1.0},
        })
    else:
        checks.append({
            "id": "audio_headroom",
            "title": "Audio Clipping & LUFS",
            "status": "pass",
            "detail": "Loudness normalization & gain headroom within safe broadcast range.",
        })

    # 4. Silent Autoplay Retention Check
    if not burn_caps:
        checks.append({
            "id": "autoplay_captions",
            "title": "Silent Autoplay Readiness",
            "status": "warn",
            "detail": "Burned captions are OFF — mobile feed viewers watch up to 70% muted.",
            "fix_label": "Enable Captions",
            "fix_patch": {"burn_captions": True},
        })
    else:
        checks.append({
            "id": "autoplay_captions",
            "title": "Silent Autoplay Readiness",
            "status": "pass",
            "detail": "Animated captions enabled for mute-first social feeds.",
        })

    score = 100
    for chk in checks:
        if chk["status"] == "fail":
            score -= 26
        elif chk["status"] == "warn":
            score -= 12
    readiness_score = max(0, min(100, int(score)))
    overall = "pass" if readiness_score >= 90 else ("warn" if readiness_score >= 65 else "risk")

    return {
        "clip_id": getattr(clip, "id", 0),
        "readiness_score": readiness_score,
        "status": overall,
        "checks": checks,
    }
