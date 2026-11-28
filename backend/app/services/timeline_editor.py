import os
import re
import json
import hashlib
import threading
import wave
import logging
import subprocess
import tempfile
from collections import OrderedDict
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import numpy as np

from app.services.proxy import get_ffmpeg_cmd, probe_video

logger = logging.getLogger(__name__)

_PLAN_CACHE: "OrderedDict[Tuple[Any, ...], TimelineEditPlan]" = OrderedDict()
_PLAN_CACHE_LOCK = threading.Lock()
_PLAN_CACHE_MAX = 64


def _clone_timeline_plan(plan: "TimelineEditPlan") -> "TimelineEditPlan":
    return TimelineEditPlan(
        keep_intervals=list(plan.keep_intervals),
        kept_segments_abs=list(plan.kept_segments_abs),
        has_cuts=plan.has_cuts,
        main_duration=plan.main_duration,
        effective_duration=plan.effective_duration,
        crossfade_sec=plan.crossfade_sec,
        remapped_words=[dict(w) for w in plan.remapped_words],
        cold_open_active=plan.cold_open_active,
        climax_window=plan.climax_window,
        climax_rel_start=plan.climax_rel_start,
        teaser_duration=plan.teaser_duration,
        flash_duration=plan.flash_duration,
        punch_timestamps=list(plan.punch_timestamps),
        punch_intervals=list(plan.punch_intervals),
        dead_air_cuts_count=plan.dead_air_cuts_count,
        dead_air_saved_sec=plan.dead_air_saved_sec,
        timeline_fx_summary=dict(plan.timeline_fx_summary),
    )

FILLER_TOKENS = {"um", "uh", "erm", "ah", "er", "hmm"}
COLD_OPEN_WINDOW_SEC = 2.80
COLD_OPEN_FLASH_SEC = 0.20
DEAD_AIR_MIN_PAUSE_SEC = 0.55
DEAD_AIR_BREATH_BUFFER_SEC = 0.12


@dataclass
class TimelineEditPlan:
    """Unified edit plan shared identically between preview_frame.py and renderer.py."""

    keep_intervals: List[Tuple[float, float]]
    kept_segments_abs: List[Tuple[float, float]]
    has_cuts: bool
    main_duration: float
    effective_duration: float
    crossfade_sec: float
    remapped_words: List[Dict[str, Any]]
    cold_open_active: bool = False
    climax_window: Optional[Tuple[float, float]] = None
    climax_rel_start: float = 0.0
    teaser_duration: float = 0.0
    flash_duration: float = 0.0
    punch_timestamps: List[float] = field(default_factory=list)
    punch_intervals: List[Tuple[float, float, float]] = field(default_factory=list)
    dead_air_cuts_count: int = 0
    dead_air_saved_sec: float = 0.0
    timeline_fx_summary: Dict[str, Any] = field(default_factory=dict)


def _compute_heatmap_metrics(
    bins: List[float],
    win_start: float,
    win_end: float,
    transcript_words: Optional[List[Dict[str, Any]]] = None,
) -> Dict[str, Any]:
    """
    Compute aligned `rms`, `speech_density` (0..1 normalized), and `is_silence` arrays
    matching the N waveform bins across `[win_start, win_end]`.
    """
    n = len(bins)
    if n == 0 or win_end <= win_start:
        return {
            "rms": list(bins),
            "speech_density": [0.0] * n,
            "is_silence": [b < 0.14 for b in bins],
        }

    bin_span = (win_end - win_start) / float(n)
    counts = [0.0] * n

    for w in (transcript_words or []):
        if not isinstance(w, dict):
            continue
        if w.get("deleted") or w.get("cut_from_video"):
            continue
        ws = float(w.get("start", 0.0))
        we = max(ws + 0.05, float(w.get("end", ws + 0.25)))
        if we < win_start or ws > win_end:
            continue
        i0 = max(0, min(n - 1, int((ws - win_start) / bin_span)))
        i1 = max(0, min(n - 1, int((we - win_start) / bin_span)))
        for bi in range(i0, i1 + 1):
            counts[bi] += 1.0

    max_cnt = max(counts) if counts else 0.0
    speech_density = [
        round(min(1.0, c / max(1.0, max_cnt)), 3) if max_cnt > 0 else 0.0
        for c in counts
    ]
    rms = [round(float(b), 4) for b in bins]
    is_silence = [
        bool(rms[i] < 0.14 and speech_density[i] <= 0.01)
        for i in range(n)
    ]
    return {
        "rms": rms,
        "speech_density": speech_density,
        "is_silence": is_silence,
    }


def extract_waveform_bins(
    video_or_wav_path: str,
    clip_start: float,
    clip_end: float,
    num_bins: int = 150,
    audio_wav_path: Optional[str] = None,
    transcript_words: Optional[List[Dict[str, Any]]] = None,
) -> Dict[str, Any]:
    """
    Extract 150 normalized RMS amplitude bins + vocal energy heatmap (`rms`, `speech_density`,
    `is_silence`) from `audio_16k.wav` (or source video) over `[clip_start - 5s, clip_end + 5s]`.
    """
    num_bins = max(20, min(400, int(num_bins or 150)))
    video_dur = 0.0
    try:
        if os.path.exists(video_or_wav_path) and not str(video_or_wav_path).lower().endswith(".wav"):
            video_dur = float(probe_video(video_or_wav_path).get("duration") or 0.0)
    except Exception:
        video_dur = 0.0

    wav_to_read: Optional[str] = None
    tmp_wav_dir: Optional[Path] = None

    if audio_wav_path and os.path.exists(audio_wav_path):
        wav_to_read = audio_wav_path
    elif str(video_or_wav_path).lower().endswith(".wav") and os.path.exists(video_or_wav_path):
        wav_to_read = video_or_wav_path
    elif os.path.exists(video_or_wav_path):
        tmp_wav_dir = Path(tempfile.mkdtemp(prefix="lost_heaven_wave_"))
        extracted = tmp_wav_dir / "slice_16k.wav"
        win_s = max(0.0, float(clip_start) - 5.0)
        win_e = float(clip_end) + 5.0
        if video_dur > 0:
            win_e = min(video_dur, win_e)
        cmd = [
            get_ffmpeg_cmd(),
            "-y",
            "-ss", f"{win_s:.3f}",
            "-t", f"{max(1.0, win_e - win_s):.3f}",
            "-i", os.path.abspath(video_or_wav_path),
            "-vn",
            "-ac", "1",
            "-ar", "16000",
            "-c:a", "pcm_s16le",
            str(extracted),
        ]
        res = subprocess.run(cmd, capture_output=True, text=True, timeout=20)
        if res.returncode == 0 and extracted.exists():
            wav_to_read = str(extracted)
            try:
                with wave.open(wav_to_read, "rb") as wf:
                    sr = wf.getframerate() or 16000
                    nframes = wf.getnframes()
                    raw = wf.readframes(nframes)
                    samples = np.frombuffer(raw, dtype=np.int16).astype(np.float32) / 32768.0
                bins = _bin_rms(samples, num_bins)
                actual_end = round(win_s + (nframes / float(sr)), 3)
                hm = _compute_heatmap_metrics(bins, win_s, actual_end, transcript_words)
                return {
                    "window_start": round(win_s, 3),
                    "window_end": actual_end,
                    "clip_start": round(float(clip_start), 3),
                    "clip_end": round(float(clip_end), 3),
                    "video_duration": round(video_dur if video_dur > 0 else win_e, 3),
                    "bins": bins,
                    **hm,
                }
            finally:
                import shutil
                shutil.rmtree(tmp_wav_dir, ignore_errors=True)

    if tmp_wav_dir is not None and tmp_wav_dir.exists():
        import shutil
        shutil.rmtree(tmp_wav_dir, ignore_errors=True)

    if not wav_to_read or not os.path.exists(wav_to_read):
        win_s = max(0.0, float(clip_start) - 5.0)
        win_e = float(clip_end) + 5.0
        bins = [0.15] * num_bins
        hm = _compute_heatmap_metrics(bins, win_s, win_e, transcript_words)
        return {
            "window_start": win_s,
            "window_end": win_e,
            "clip_start": round(float(clip_start), 3),
            "clip_end": round(float(clip_end), 3),
            "video_duration": round(video_dur if video_dur > 0 else win_e, 3),
            "bins": bins,
            **hm,
        }

    with wave.open(wav_to_read, "rb") as wf:
        sr = wf.getframerate() or 16000
        n_channels = wf.getnchannels() or 1
        total_frames = wf.getnframes()
        wav_dur = total_frames / float(sr)
        if video_dur <= 0:
            video_dur = wav_dur

        win_s = max(0.0, float(clip_start) - 5.0)
        win_e = min(video_dur, float(clip_end) + 5.0)
        if win_e <= win_s:
            win_e = min(video_dur, win_s + 5.0)

        start_frame = max(0, min(total_frames, int(win_s * sr)))
        end_frame = max(start_frame + 1, min(total_frames, int(win_e * sr)))
        wf.setpos(start_frame)
        raw = wf.readframes(end_frame - start_frame)
        samples = np.frombuffer(raw, dtype=np.int16).astype(np.float32) / 32768.0
        if n_channels > 1:
            samples = samples.reshape(-1, n_channels).mean(axis=1)

    bins = _bin_rms(samples, num_bins)
    hm = _compute_heatmap_metrics(bins, win_s, win_e, transcript_words)
    return {
        "window_start": round(win_s, 3),
        "window_end": round(win_e, 3),
        "clip_start": round(float(clip_start), 3),
        "clip_end": round(float(clip_end), 3),
        "video_duration": round(video_dur, 3),
        "bins": bins,
        **hm,
    }


def _bin_rms(samples: np.ndarray, num_bins: int) -> List[float]:
    if samples.size == 0:
        return [0.05] * num_bins
    chunks = np.array_split(samples, num_bins)
    rms_vals = np.array([float(np.sqrt(np.mean(np.square(c)))) if c.size else 0.0 for c in chunks], dtype=np.float32)
    peak = float(np.max(rms_vals))
    if peak <= 1e-6:
        return [0.05] * num_bins
    norm = np.clip(rms_vals / peak, 0.03, 1.0)
    return [round(float(v), 4) for v in norm]


def _normalize_word_list(
    words: List[Dict[str, Any]],
    clip_start_time: float,
    clip_duration: float,
    prefer_abs_with_segments: bool = False,
) -> List[Dict[str, Any]]:
    if not words:
        return []
    if any(bool(w.get("timeline_relative")) for w in words if isinstance(w, dict)):
        is_abs = False
    else:
        max_end = max(float(w.get("end", 0.0)) for w in words)
        min_start = min(float(w.get("start", 0.0)) for w in words)
        is_abs = clip_start_time > 0.0 and (
            (clip_duration > 0.0 and max_end > clip_duration + 1.0)
            or (prefer_abs_with_segments and min_start >= max(0.0, clip_start_time - 0.1))
            or (clip_duration <= 0.0 and min_start >= max(0.0, clip_start_time - 0.5))
        )
    out: List[Dict[str, Any]] = []
    for w in words:
        nw = dict(w)
        ws = float(nw.get("start", 0.0))
        we = float(nw.get("end", ws + 0.2))
        if is_abs:
            ws -= clip_start_time
            we -= clip_start_time
        if we <= 0.0:
            continue
        if clip_duration > 0 and ws >= clip_duration:
            continue
        ws = max(0.0, ws)
        we = min(clip_duration, we) if clip_duration > 0 else we
        nw["start"] = round(ws, 3)
        nw["end"] = round(max(ws + 0.06, we), 3)
        out.append(nw)
    out.sort(key=lambda x: x["start"])
    return out


def map_output_time_to_source_time(
    rel_t: float,
    abs_segments: List[Tuple[float, float]],
    crossfade_sec: float = 0.03,
) -> float:
    """Map a stitched clip-relative playback time back to its master-video timestamp."""
    if not abs_segments:
        return max(0.0, float(rel_t))
    t = max(0.0, float(rel_t))
    cf = max(0.0, float(crossfade_sec or 0.0))
    cursor = 0.0
    for idx, (s, e) in enumerate(abs_segments):
        seg_len = max(0.1, e - s)
        step_cf = min(cf, seg_len * 0.25) if idx < len(abs_segments) - 1 else 0.0
        eff_advance = seg_len - step_cf
        if t <= cursor + seg_len or idx == len(abs_segments) - 1:
            return round(s + min(seg_len, max(0.0, t - cursor)), 3)
        cursor += eff_advance
    return round(abs_segments[-1][1], 3)


def detect_ffmpeg_silences(
    video_path: str,
    start_sec: float = 0.0,
    duration_sec: float = 0.0,
    noise_db: float = -34.0,
    min_dur: float = 0.35,
) -> List[Tuple[float, float]]:
    """
    Run FFmpeg `silencedetect=noise={noise_db}dB:d={min_dur}` over `[start_sec, start_sec + duration_sec]`
    and return clip-relative `(silence_start, silence_end)` intervals.
    """
    if not video_path or not os.path.exists(video_path):
        return []
    dur = max(0.2, float(duration_sec or 0.0))
    if duration_sec <= 0.0:
        try:
            meta = probe_video(video_path)
            dur = max(0.2, float(meta.get("duration") or 0.0) - max(0.0, float(start_sec)))
        except Exception:
            dur = 0.0
    if dur <= min_dur:
        return []

    ffmpeg = get_ffmpeg_cmd()
    cmd = [
        ffmpeg,
        "-hide_banner",
        "-ss", f"{max(0.0, float(start_sec)):.3f}",
        "-t", f"{dur:.3f}",
        "-i", os.path.abspath(video_path),
        "-vn",
        "-af", f"silencedetect=noise={float(noise_db):.1f}dB:d={float(min_dur):.2f}",
        "-f", "null",
        "-",
    ]
    try:
        res = subprocess.run(cmd, capture_output=True, text=True, timeout=25)
        stderr = res.stderr or ""
    except Exception as exc:
        logger.debug("FFmpeg silencedetect failed: %s", exc)
        return []

    silences: List[Tuple[float, float]] = []
    cur_start: Optional[float] = None
    for line in stderr.splitlines():
        m_s = re.search(r"silence_start:\s*([-0-9.]+)", line)
        if m_s:
            cur_start = max(0.0, float(m_s.group(1)))
            continue
        m_e = re.search(r"silence_end:\s*([-0-9.]+)", line)
        if m_e and cur_start is not None:
            end_val = min(dur, float(m_e.group(1)))
            if end_val - cur_start >= min_dur * 0.9:
                silences.append((round(cur_start, 3), round(end_val, 3)))
            cur_start = None
    if cur_start is not None and dur - cur_start >= min_dur * 0.9:
        silences.append((round(cur_start, 3), round(dur, 3)))
    return silences


def select_cold_open_climax_window(
    video_path: str,
    clip_start_time: float,
    clip_duration: float,
    words: Optional[List[Dict[str, Any]]] = None,
    cold_open_quote: str = "",
    window_sec: float = COLD_OPEN_WINDOW_SEC,
) -> Optional[Tuple[float, float]]:
    """
    Pick the highest-excitement `window_sec` (default 2.80s) segment from the latter 65%
    of a clip (only when clip duration >= 8.0s). Fuses vocal arousal spikes, acoustic RMS energy,
    emphasis punctuation/speech density, and optional `cold_open_quote` anchor (fallback: 65% mark).
    Returns `(abs_start, abs_end)` in master-video seconds, or `None` if clip < 8.0s.
    """
    dur = float(clip_duration or 0.0)
    if dur < 8.0:
        return None
    w_len = min(max(1.5, float(window_sec or COLD_OPEN_WINDOW_SEC)), dur * 0.40)
    search_start_rel = dur * 0.35
    search_end_rel = max(search_start_rel + 0.1, dur - w_len)
    abs_anchor = max(0.0, float(clip_start_time or 0.0))

    # Quote match hint if inside search range
    quote_rel_start: Optional[float] = None
    clean_q = re.sub(r"[^\w\s]", "", str(cold_open_quote or "")).lower().strip()
    if clean_q and words:
        norm_w = _normalize_word_list(words, abs_anchor, dur)
        q_toks = clean_q.split()
        w_toks = [re.sub(r"[^\w\s]", "", str(w.get("word", ""))).lower().strip() for w in norm_w]
        if q_toks and len(w_toks) >= len(q_toks):
            for idx in range(len(w_toks) - len(q_toks) + 1):
                if w_toks[idx:idx + len(q_toks)] == q_toks:
                    cand = float(norm_w[idx].get("start", 0.0))
                    if cand >= dur * 0.35:
                        quote_rel_start = max(search_start_rel, min(search_end_rel, cand))
                        break

    # Extract fine audio RMS profile (10 Hz) over latter 65% via face_tracker helper
    rms_bins: List[float] = []
    if video_path and os.path.exists(video_path):
        try:
            from app.services.face_tracker import extract_audio_rms_profile
            rms_bins = extract_audio_rms_profile(
                video_path,
                abs_anchor + search_start_rel,
                abs_anchor + dur,
                sample_fps=10.0,
            )
        except Exception:
            rms_bins = []

    rms_arr = np.array(rms_bins, dtype=np.float32) if rms_bins else np.zeros(0, dtype=np.float32)
    rms_median = float(np.median(rms_arr)) if rms_arr.size > 0 else 0.0
    rms_span = float(np.max(rms_arr) - np.min(rms_arr)) if rms_arr.size > 0 else 0.0

    norm_w = _normalize_word_list(words or [], abs_anchor, dur) if words else []
    step = 0.25
    fallback_rel = min(search_end_rel, max(search_start_rel, dur * 0.65))
    best_rel = fallback_rel
    best_score = -1.0
    scores_seen: List[float] = []

    cur = search_start_rel
    while cur <= search_end_rel + 1e-4:
        win_e = cur + w_len
        score = 0.0
        # Audio RMS energy + vocal arousal dynamics component
        if rms_arr.size > 0 and rms_span > 0.05:
            i0 = max(0, int(round((cur - search_start_rel) * 10.0)))
            i1 = min(int(rms_arr.size), max(i0 + 1, int(round((win_e - search_start_rel) * 10.0))))
            seg_vals = rms_arr[i0:i1]
            if seg_vals.size > 0:
                arousal = float(np.mean(np.maximum(0.0, seg_vals - rms_median)))
                score += float(np.mean(seg_vals)) * 1.5 + float(np.max(seg_vals)) * 0.9 + arousal * 1.1
        # Word density & exclamation/question excitement component
        win_words = [
            w for w in norm_w
            if not w.get("deleted") and not w.get("cut_from_video")
            and float(w.get("end", 0.0)) >= cur and float(w.get("start", 0.0)) <= win_e
        ]
        if win_words:
            score += min(1.0, len(win_words) / 8.0) * 0.9
            if any(any(p in str(w.get("word", "")) for p in ("!", "?")) for w in win_words):
                score += 0.35
        if quote_rel_start is not None and abs(cur - quote_rel_start) <= 1.2:
            score += 1.25 * max(0.0, 1.0 - abs(cur - quote_rel_start) / 1.2)
        scores_seen.append(score)
        if score > best_score:
            best_score = score
            best_rel = cur
        cur += step

    # Explicit flat-signal fallback to 65% mark when neither audio peaks nor transcript arousal distinguish any window
    if not scores_seen or best_score <= 0.05 or (max(scores_seen) - min(scores_seen) < 0.04 and quote_rel_start is None):
        best_rel = fallback_rel

    co_s = round(abs_anchor + best_rel, 3)
    co_e = round(min(abs_anchor + dur, co_s + w_len), 3)
    return co_s, co_e


def compute_kept_segments_and_remapped_words(
    words: List[Dict[str, Any]],
    clip_start_time: float,
    clip_duration: float,
    tighten_pacing: bool = False,
    silence_threshold: float = 0.45,
    clean_filler_words: bool = False,
    crossfade_sec: float = 0.03,
    explicit_keep_segments: Optional[List[Tuple[float, float]]] = None,
    auto_trim_dead_air: bool = False,
    video_path: Optional[str] = None,
) -> Dict[str, Any]:
    """
    Convert explicit blade `keep_segments`, transcript word cuts (`cut_from_video`), optional filler-word cuts,
    and optional dead-air silence cuts (`tighten_pacing`) into kept video segments + post-cut word timestamps.
    """
    clip_dur = max(0.2, float(clip_duration or 1.0))

    # Normalize explicit keep segments relative to clip_start_time if supplied
    norm_explicit: Optional[List[Tuple[float, float]]] = None
    is_abs_seg = False
    if explicit_keep_segments:
        abs_pairs: List[Tuple[float, float]] = []
        for item in explicit_keep_segments:
            if len(item) >= 2:
                s, e = max(0.0, float(item[0])), max(0.0, float(item[1]))
                if e > s + 0.1:
                    abs_pairs.append((round(s, 3), round(e, 3)))
        if abs_pairs:
            # Determine if coordinates are master-absolute or already clip-relative
            is_abs_seg = clip_start_time > 0.0 and min(s for s, _ in abs_pairs) >= max(0.0, clip_start_time - 0.5)
            rel_pairs = [
                (round(max(0.0, s - clip_start_time), 3), round(max(0.1, e - clip_start_time), 3))
                if is_abs_seg else (round(s, 3), round(e, 3))
                for s, e in abs_pairs
            ]
            norm_explicit = rel_pairs
            clip_dur = max(clip_dur, max(e for _, e in rel_pairs))

    norm_words = _normalize_word_list(words or [], clip_start_time, clip_dur, prefer_abs_with_segments=is_abs_seg)

    raw_cuts: List[Tuple[float, float]] = []

    def _is_filler(tok: str) -> bool:
        clean = tok.strip(".,!?;:\"'").lower()
        return clean in FILLER_TOKENS

    active_spoken: List[Dict[str, Any]] = []
    for w in norm_words:
        is_cut = bool(w.get("cut_from_video", False))
        if not is_cut and clean_filler_words and not w.get("keep_override", False):
            if bool(w.get("is_filler")) or _is_filler(str(w.get("word", ""))):
                is_cut = True
        if is_cut:
            raw_cuts.append((float(w["start"]), float(w["end"])))
        else:
            active_spoken.append(w)

    # Dead-air silence cuts when auto_trim_dead_air or tighten_pacing is active
    # Non-speech pauses >= 0.55s are trimmed while preserving 0.12s breathing buffers around spoken words
    thresh = (
        DEAD_AIR_MIN_PAUSE_SEC
        if (auto_trim_dead_air and not tighten_pacing)
        else max(0.25, min(1.5, float(silence_threshold or DEAD_AIR_MIN_PAUSE_SEC)))
    )
    if tighten_pacing or auto_trim_dead_air:
        cushion = DEAD_AIR_BREATH_BUFFER_SEC if auto_trim_dead_air else 0.08
        if auto_trim_dead_air and video_path and os.path.exists(video_path):
            # Combine FFmpeg silencedetect with high-res audio RMS envelope
            rms_silences: List[Tuple[float, float]] = []
            try:
                from app.services.face_tracker import extract_audio_rms_profile
                rms_fps = 20.0
                rms_env = extract_audio_rms_profile(
                    video_path,
                    clip_start_time,
                    clip_start_time + clip_dur,
                    sample_fps=rms_fps,
                )
                if rms_env:
                    env_arr = np.array(rms_env, dtype=np.float32)
                    low_thr = max(0.06, float(np.percentile(env_arr, 15)) * 1.25)
                    if low_thr < 0.28:
                        in_sil = False
                        s_i = 0
                        for idx_r, val_r in enumerate(rms_env):
                            if val_r <= low_thr and not in_sil:
                                in_sil = True
                                s_i = idx_r
                            elif val_r > low_thr and in_sil:
                                s_t = s_i / rms_fps
                                e_t = idx_r / rms_fps
                                if e_t - s_t >= thresh:
                                    rms_silences.append((round(s_t, 3), round(e_t, 3)))
                                in_sil = False
                        if in_sil:
                            s_t = s_i / rms_fps
                            e_t = len(rms_env) / rms_fps
                            if e_t - s_t >= thresh:
                                rms_silences.append((round(s_t, 3), round(clip_dur, 3)))
            except Exception:
                rms_silences = []

            ff_silences = detect_ffmpeg_silences(
                video_path,
                start_sec=clip_start_time,
                duration_sec=clip_dur,
                noise_db=-34.0,
                min_dur=thresh,
            )
            for ss, se in (ff_silences + rms_silences):
                c_s = round(ss + cushion, 3)
                c_e = round(se - cushion, 3)
                if c_e <= c_s + 0.08:
                    continue
                # Protect spoken word intervals with breathing buffer
                sub_cuts: List[Tuple[float, float]] = [(c_s, c_e)]
                for w in active_spoken:
                    ws = max(0.0, float(w["start"]) - cushion * 0.5)
                    we = min(clip_dur, float(w["end"]) + cushion * 0.5)
                    next_subs: List[Tuple[float, float]] = []
                    for a, b in sub_cuts:
                        if we <= a or ws >= b:
                            next_subs.append((a, b))
                        else:
                            if ws - a > 0.08:
                                next_subs.append((a, round(ws, 3)))
                            if b - we > 0.08:
                                next_subs.append((round(we, 3), b))
                    sub_cuts = next_subs
                raw_cuts.extend(sub_cuts)

        if active_spoken:
            # Leading silence >= thresh
            if active_spoken[0]["start"] > thresh + cushion:
                raw_cuts.append((0.0, round(active_spoken[0]["start"] - cushion, 3)))
            # Inter-word pauses >= thresh (keeping 0.12s breathing buffer on both sides)
            for idx in range(len(active_spoken) - 1):
                gap_s = active_spoken[idx]["end"]
                gap_e = active_spoken[idx + 1]["start"]
                if gap_e - gap_s >= thresh:
                    c_s = round(gap_s + cushion, 3)
                    c_e = round(gap_e - cushion, 3)
                    if c_e > c_s + 0.08:
                        raw_cuts.append((c_s, c_e))
            # Trailing silence >= thresh
            if clip_dur - active_spoken[-1]["end"] > thresh + cushion:
                raw_cuts.append((round(active_spoken[-1]["end"] + cushion, 3), clip_dur))

    # Merge overlapping or nearly touching cut intervals
    raw_cuts = [(max(0.0, s), min(clip_dur, e)) for s, e in raw_cuts if e > s + 0.04]
    raw_cuts.sort(key=lambda pair: pair[0])
    merged_cuts: List[Tuple[float, float]] = []
    for cs, ce in raw_cuts:
        if merged_cuts and cs <= merged_cuts[-1][1] + 0.06:
            merged_cuts[-1] = (merged_cuts[-1][0], max(merged_cuts[-1][1], ce))
        else:
            merged_cuts.append((cs, ce))

    has_explicit_edits = bool(
        norm_explicit
        and (
            len(norm_explicit) > 1
            or norm_explicit[0][0] > 0.05
            or norm_explicit[0][1] < clip_dur - 0.05
        )
    )

    if not merged_cuts and not has_explicit_edits:
        base_segs = norm_explicit if norm_explicit else [(0.0, clip_dur)]
        eff = round(sum(e - s for s, e in base_segs), 3)
        return {
            "kept_segments": base_segs,
            "kept_segments_abs": [(round(clip_start_time + s, 3), round(clip_start_time + e, 3)) for s, e in base_segs],
            "has_cuts": False,
            "effective_duration": max(0.2, eff),
            "remapped_words": norm_words,
            "crossfade_sec": crossfade_sec,
            "dead_air_cuts_count": 0,
            "dead_air_saved_sec": 0.0,
        }

    # Subtract word/silence cut intervals from each kept base segment
    base_kept = norm_explicit if norm_explicit else [(0.0, clip_dur)]
    kept: List[Tuple[float, float]] = []
    for bs, be in base_kept:
        cursor = bs
        for cs, ce in merged_cuts:
            if ce <= cursor:
                continue
            if cs >= be:
                break
            if cs - cursor >= 0.12:
                kept.append((round(cursor, 3), round(min(be, cs), 3)))
            cursor = max(cursor, min(be, ce))
        if be - cursor >= 0.12:
            kept.append((round(cursor, 3), round(be, 3)))

    if not kept:
        kept = [(0.0, clip_dur)]

    # Check if single full segment matches full clip cleanly with no cuts
    if (
        not has_explicit_edits
        and len(kept) == 1
        and kept[0][0] <= 0.02
        and kept[0][1] >= clip_dur - 0.02
    ):
        return {
            "kept_segments": [(0.0, clip_dur)],
            "kept_segments_abs": [(round(clip_start_time, 3), round(clip_start_time + clip_dur, 3))],
            "has_cuts": False,
            "effective_duration": round(clip_dur, 3),
            "remapped_words": norm_words,
            "crossfade_sec": crossfade_sec,
        }

    cf = max(0.0, min(0.6, float(crossfade_sec or 0.0)))
    min_seg = min(e - s for s, e in kept)
    if min_seg <= max(0.10, cf * 2.1):
        cf = min(cf, max(0.0, round(min_seg * 0.25, 3)))

    # Compute exact output timeline offsets per segment (accounting for cf overlap at joins)
    seg_out_starts: List[float] = []
    out_cursor = 0.0
    for idx, (ks, ke) in enumerate(kept):
        seg_out_starts.append(out_cursor)
        seg_len = ke - ks
        step_cf = min(cf, seg_len * 0.25) if idx < len(kept) - 1 else 0.0
        out_cursor += seg_len - step_cf

    effective_duration = round(max(0.1, out_cursor), 3)

    # Check if words were already remapped onto [0, sum_kept] (e.g. from clip_merger)
    sum_kept = sum(e - s for s, e in kept)
    already_stitched_words = any(bool(w.get("timeline_relative")) for w in active_spoken) or bool(
        has_explicit_edits
        and active_spoken
        and max(w["end"] for w in active_spoken) <= sum_kept + 0.8
        and kept[0][1] < clip_dur - 1.0
        and any(w["end"] <= sum_kept and w["start"] >= kept[0][1] for w in active_spoken)
        and not any(
            any(ks <= 0.5 * (w["start"] + w["end"]) <= ke for ks, ke in kept[1:])
            for w in active_spoken
        )
    )

    if already_stitched_words:
        remapped = [dict(w) for w in active_spoken]
    else:
        remapped = []
        for w in active_spoken:
            mid = 0.5 * (w["start"] + w["end"])
            for idx, (ks, ke) in enumerate(kept):
                if ks - 0.03 <= mid <= ke + 0.03:
                    rel_s = max(0.0, w["start"] - ks)
                    rel_e = min(ke - ks, max(rel_s + 0.06, w["end"] - ks))
                    nw = dict(w)
                    nw["start"] = round(seg_out_starts[idx] + rel_s, 3)
                    nw["end"] = round(seg_out_starts[idx] + rel_e, 3)
                    remapped.append(nw)
                    break

    return {
        "kept_segments": kept,
        "kept_segments_abs": [(round(clip_start_time + ks, 3), round(clip_start_time + ke, 3)) for ks, ke in kept],
        "has_cuts": True,
        "effective_duration": effective_duration,
        "remapped_words": remapped,
        "crossfade_sec": cf,
        "dead_air_cuts_count": len(merged_cuts),
        "dead_air_saved_sec": round(max(0.0, clip_dur - effective_duration), 2),
    }


def _map_source_rel_to_output_time(
    src_rel_t: float,
    kept_segments: List[Tuple[float, float]],
    crossfade_sec: float = 0.03,
) -> float:
    """Map an un-cut clip-relative timestamp forward onto the post-cut output timeline."""
    if not kept_segments:
        return max(0.0, float(src_rel_t))
    cursor = 0.0
    cf = max(0.0, float(crossfade_sec or 0.0))
    for idx, (ks, ke) in enumerate(kept_segments):
        seg_len = max(0.1, ke - ks)
        step_cf = min(cf, seg_len * 0.25) if idx < len(kept_segments) - 1 else 0.0
        if src_rel_t <= ke + 0.05 or idx == len(kept_segments) - 1:
            local = max(0.0, min(seg_len, src_rel_t - ks))
            return round(cursor + local, 3)
        cursor += seg_len - step_cf
    return round(cursor, 3)


def build_timeline_edit_plan(
    video_path: str,
    clip_start_time: float,
    clip_duration: float,
    words: Optional[List[Dict[str, Any]]] = None,
    *,
    cold_open: bool = False,
    cold_open_quote: str = "",
    trim_dead_air: bool = False,
    tighten_pacing: bool = False,
    silence_threshold: float = DEAD_AIR_MIN_PAUSE_SEC,
    clean_filler_words: bool = False,
    punch_zoom: bool = False,
    punch_zoom_intensity: float = 1.16,
    explicit_keep_segments: Optional[List[Tuple[float, float]]] = None,
    crossfade_sec: float = 0.03,
) -> TimelineEditPlan:
    """
    Single Source of Truth planner for:
      1. `trim_dead_air` (`keep_intervals` + word timestamp remap + pause count / seconds saved)
      2. `cold_open` (2.8s climax teaser prepended with 0.2s white flash + `+2.8s` word shift + teaser words)
      3. `punch_zoom` (up to 6 top vocal peaks >= 3.2s apart shifted cleanly onto the final output timeline)
    """
    from app.services.face_tracker import detect_punch_zoom_peaks
    from app.services.proxy import probe_video

    anchor_s = max(0.0, float(clip_start_time or 0.0))
    span_d = max(0.5, float(clip_duration or 1.0))

    media_ceiling: Optional[float] = None
    if video_path and os.path.exists(video_path):
        try:
            meta = probe_video(video_path)
            cand_durs = [
                float(meta.get(k) or 0.0)
                for k in ("duration", "video_duration")
                if float(meta.get(k) or 0.0) > 0.25
            ]
            if meta.get("has_audio") and float(meta.get("audio_duration") or 0.0) > 0.25:
                cand_durs.append(float(meta["audio_duration"]))
            if cand_durs:
                media_ceiling = max(0.2, round(min(cand_durs) - 0.05, 3))
                if anchor_s + span_d > media_ceiling:
                    anchor_s = round(min(anchor_s, max(0.0, media_ceiling - 0.2)), 3)
                    span_d = round(max(0.2, min(span_d, media_ceiling - anchor_s)), 3)
        except Exception:
            media_ceiling = None

    src_words = [dict(w) for w in (words or []) if isinstance(w, dict) and not w.get("muted", False)]

    eff_cf = (
        max(0.03, float(crossfade_sec or 0.03))
        if (tighten_pacing or trim_dead_air)
        else float(crossfade_sec or 0.03)
    )

    try:
        abs_v = os.path.abspath(video_path) if video_path else ""
        st = os.stat(abs_v) if (abs_v and os.path.exists(abs_v)) else None
        v_stat = (abs_v, int(st.st_size), int(st.st_mtime_ns)) if st else (str(video_path or ""), 0, 0)
    except OSError:
        v_stat = (str(video_path or ""), 0, 0)

    words_sig = hashlib.md5(
        json.dumps(
            [
                (w.get("word"), round(float(w.get("start", 0.0)), 3), round(float(w.get("end", 0.0)), 3))
                for w in src_words
            ],
            separators=(",", ":"),
        ).encode("utf-8")
    ).hexdigest()
    exp_sig = tuple((round(float(a), 3), round(float(b), 3)) for a, b in (explicit_keep_segments or []))
    cache_key = (
        v_stat,
        round(anchor_s, 3),
        round(span_d, 3),
        words_sig,
        bool(cold_open),
        str(cold_open_quote or ""),
        bool(trim_dead_air),
        bool(tighten_pacing),
        round(float(silence_threshold), 3),
        bool(clean_filler_words),
        bool(punch_zoom),
        round(float(punch_zoom_intensity or 1.16), 3),
        exp_sig,
        round(eff_cf, 3),
        id(detect_ffmpeg_silences),
        id(compute_kept_segments_and_remapped_words),
        id(select_cold_open_climax_window),
        id(detect_punch_zoom_peaks),
    )
    with _PLAN_CACHE_LOCK:
        cached_plan = _PLAN_CACHE.get(cache_key)
        if cached_plan is not None:
            _PLAN_CACHE.move_to_end(cache_key)
            return _clone_timeline_plan(cached_plan)
    tl_cuts = compute_kept_segments_and_remapped_words(
        words=src_words,
        clip_start_time=anchor_s,
        clip_duration=span_d,
        tighten_pacing=tighten_pacing,
        silence_threshold=silence_threshold,
        clean_filler_words=clean_filler_words,
        crossfade_sec=eff_cf,
        explicit_keep_segments=explicit_keep_segments,
        auto_trim_dead_air=trim_dead_air,
        video_path=video_path,
    )
    kept_rel: List[Tuple[float, float]] = sanitize_timeline_intervals(
        list(tl_cuts.get("kept_segments") or []),
        min_bound=0.0,
        max_bound=span_d,
        min_slice_sec=0.12,
        fallback_interval=(0.0, round(max(0.2, span_d), 3)),
    )
    raw_abs = list(
        tl_cuts.get("kept_segments_abs")
        or [(round(anchor_s + s, 3), round(anchor_s + e, 3)) for s, e in kept_rel]
    )
    abs_ceiling = media_ceiling if media_ceiling is not None else round(anchor_s + span_d, 3)
    kept_abs: List[Tuple[float, float]] = sanitize_timeline_intervals(
        raw_abs,
        min_bound=anchor_s,
        max_bound=abs_ceiling,
        min_slice_sec=0.12,
        fallback_interval=(round(anchor_s, 3), round(max(anchor_s + 0.2, min(abs_ceiling, anchor_s + span_d)), 3)),
    )
    kept_rel = [(round(max(0.0, s - anchor_s), 3), round(max(0.12, e - anchor_s), 3)) for s, e in kept_abs]
    has_cuts = len(kept_rel) > 1 or bool(tl_cuts.get("has_cuts", False))
    eff_cf_final = float(tl_cuts.get("crossfade_sec", eff_cf))
    raw_seg_sum = sum(max(0.0, e - s) for s, e in kept_abs)
    main_dur = max(0.2, round(raw_seg_sum - max(0, len(kept_abs) - 1) * min(eff_cf_final, 0.08), 3))
    cuts_count = int(tl_cuts.get("dead_air_cuts_count") or 0)
    saved_sec = float(tl_cuts.get("dead_air_saved_sec") or 0.0)
    post_words = [dict(w) for w in (tl_cuts.get("remapped_words") or [])]

    # Cold-Open Climax Teaser (`>= 8.0s` clips): teaser prepended at t=0.0s with brief white flash
    co_active = False
    co_window: Optional[Tuple[float, float]] = None
    co_rel_start = 0.0
    teaser_dur = 0.0
    flash_dur = 0.0
    if cold_open and main_dur >= 8.0:
        co_window = select_cold_open_climax_window(
            video_path,
            clip_start_time=anchor_s,
            clip_duration=span_d,
            words=src_words,
            cold_open_quote=cold_open_quote,
            window_sec=COLD_OPEN_WINDOW_SEC,
        )
        if co_window is not None:
            co_s, co_e = co_window
            co_s = max(anchor_s, float(co_s))
            co_e = min(abs_ceiling, anchor_s + span_d, float(co_e))
            if co_e - co_s >= 0.5:
                co_window = (round(co_s, 3), round(co_e, 3))
            else:
                co_window = None
        if co_window is not None:
            co_s, co_e = co_window
            teaser_dur = max(0.8, round(co_e - co_s, 3))
            flash_dur = min(COLD_OPEN_FLASH_SEC, round(teaser_dur * 0.25, 3))
            co_rel_start = max(0.0, round(co_s - anchor_s, 3))
            co_active = True

            # Teaser captions rendered over [0.0, teaser_dur] from words inside the climax window
            norm_src = _normalize_word_list(src_words, anchor_s, span_d)
            teaser_words: List[Dict[str, Any]] = []
            for tw in norm_src:
                ws = float(tw.get("start", 0.0))
                we = float(tw.get("end", ws + 0.15))
                if we > co_rel_start and ws < co_rel_start + teaser_dur:
                    nw = dict(tw)
                    ns = max(0.0, round(ws - co_rel_start, 3))
                    ne = min(teaser_dur, round(max(ns + 0.06, we - co_rel_start), 3))
                    nw["start"] = ns
                    nw["end"] = ne
                    nw["is_climax_teaser"] = True
                    nw["timeline_relative"] = True
                    teaser_words.append(nw)

            shifted_main: List[Dict[str, Any]] = []
            for pw in post_words:
                npw = dict(pw)
                npw["start"] = round(float(npw.get("start", 0.0)) + teaser_dur, 3)
                npw["end"] = round(float(npw.get("end", 0.0)) + teaser_dur, 3)
                npw["timeline_relative"] = True
                shifted_main.append(npw)
            post_words = teaser_words + shifted_main

    eff_dur = round(main_dur + teaser_dur, 3)

    # Punch Zoom peaks: up to 6 vocal peaks >= 3.2s apart mapped cleanly onto output timeline
    p_times: List[float] = []
    p_intervals: List[Tuple[float, float, float]] = []
    if punch_zoom and video_path:
        pz = detect_punch_zoom_peaks(
            video_path,
            start_sec=anchor_s,
            end_sec=anchor_s + span_d,
            base_zoom=1.0,
            punch_intensity=float(punch_zoom_intensity or 1.16),
            max_peaks=6,
            min_spacing=3.2,
        )
        raw_intervals = list(pz.get("punch_intervals") or [])
        mapped_intervals: List[Tuple[float, float, float]] = []
        mapped_times: List[float] = []
        last_pt = -3.2
        for t0, t1, pz_fac in raw_intervals:
            mid_src = 0.5 * (float(t0) + float(t1))
            if has_cuts:
                mid_out = _map_source_rel_to_output_time(mid_src, kept_rel, eff_cf_final)
            else:
                mid_out = mid_src
            mid_final = round(mid_out + teaser_dur, 3)
            if mid_final - last_pt < 3.2 or mid_final > eff_dur - 0.15:
                continue
            p0 = round(max(teaser_dur, mid_final - 0.15), 3)
            p1 = round(min(eff_dur, p0 + 0.45), 3)
            if p1 > p0 + 0.12:
                mapped_intervals.append((p0, p1, float(pz_fac)))
                mapped_times.append(round(mid_final, 2))
                last_pt = mid_final
            if len(mapped_intervals) >= 6:
                break
        p_intervals = mapped_intervals
        p_times = mapped_times

    # Build diagnostic summary badges for UI toggle pills
    mmss = f"{int(co_rel_start // 60)}:{int(co_rel_start % 60):02d}"
    co_label = (
        f"{teaser_dur:.1f}s climax @ {mmss}"
        if co_active
        else ("Clip < 8s" if cold_open else "")
    )
    if trim_dead_air:
        da_label = (
            f"{cuts_count} pause{'s' if cuts_count != 1 else ''} cut (-{saved_sec:.1f}s)"
            if cuts_count > 0
            else "0 pauses >= 0.55s"
        )
    else:
        da_label = ""
    pz_label = f"{len(p_intervals)} vocal peak{'s' if len(p_intervals) != 1 else ''}" if punch_zoom else ""

    summary = {
        "cold_open_active": co_active,
        "cold_open_label": co_label,
        "teaser_duration": teaser_dur,
        "climax_start": round(co_window[0], 3) if co_window else None,
        "climax_end": round(co_window[1], 3) if co_window else None,
        "trim_dead_air_active": bool(trim_dead_air),
        "dead_air_label": da_label,
        "dead_air_cuts_count": cuts_count,
        "dead_air_saved_sec": saved_sec,
        "punch_zoom_active": bool(punch_zoom),
        "punch_zoom_label": pz_label,
        "punch_peaks_count": len(p_intervals),
        "effective_duration": eff_dur,
    }

    plan = TimelineEditPlan(
        keep_intervals=kept_rel,
        kept_segments_abs=kept_abs,
        has_cuts=has_cuts,
        main_duration=main_dur,
        effective_duration=eff_dur,
        crossfade_sec=eff_cf_final,
        remapped_words=post_words,
        cold_open_active=co_active,
        climax_window=co_window,
        climax_rel_start=co_rel_start,
        teaser_duration=teaser_dur,
        flash_duration=flash_dur,
        punch_timestamps=p_times,
        punch_intervals=p_intervals,
        dead_air_cuts_count=cuts_count,
        dead_air_saved_sec=saved_sec,
        timeline_fx_summary=summary,
    )
    with _PLAN_CACHE_LOCK:
        _PLAN_CACHE[cache_key] = _clone_timeline_plan(plan)
        _PLAN_CACHE.move_to_end(cache_key)
        while len(_PLAN_CACHE) > _PLAN_CACHE_MAX:
            _PLAN_CACHE.popitem(last=False)
    return plan


def sanitize_timeline_intervals(
    intervals: List[Tuple[float, float]],
    *,
    min_bound: float = 0.0,
    max_bound: Optional[float] = None,
    min_slice_sec: float = 0.12,
    fallback_interval: Optional[Tuple[float, float]] = None,
) -> List[Tuple[float, float]]:
    """
    Clamp timeline intervals inside valid media bounds, drop degenerate slices (< 0.12s),
    sort and merge overlapping windows, and return a non-empty fallback when nothing survives.
    """
    cleaned: List[Tuple[float, float]] = []
    lo = max(0.0, float(min_bound))
    hi = float(max_bound) if max_bound is not None else None
    for seg in intervals or []:
        if not seg or len(seg) < 2:
            continue
        s = max(lo, float(seg[0]))
        e = float(seg[1])
        if hi is not None:
            e = min(hi, e)
        if e - s >= min_slice_sec:
            cleaned.append((round(s, 3), round(e, 3)))
    cleaned.sort(key=lambda pair: pair[0])
    merged: List[Tuple[float, float]] = []
    for s, e in cleaned:
        if merged and s <= merged[-1][1] + 0.02:
            merged[-1] = (merged[-1][0], round(max(merged[-1][1], e), 3))
        else:
            merged.append((s, e))
    if not merged and fallback_interval is not None:
        fs = max(lo, float(fallback_interval[0]))
        fe = float(fallback_interval[1])
        if hi is not None:
            fe = min(hi, fe)
        if fe <= fs + 0.05:
            fe = fs + max(0.2, min_slice_sec)
            if hi is not None and hi > lo + 0.05:
                fe = min(hi, fe)
                fs = max(lo, fe - 0.5)
        merged = [(round(fs, 3), round(fe, 3))]
    return merged


def build_timeline_trim_concat_filter(
    kept_segments: List[Tuple[float, float]],
    crossfade_sec: float = 0.03,
    has_audio: bool = True,
    multi_input: bool = False,
) -> Tuple[str, str, Optional[str]]:
    """
    Build FFmpeg multi-segment trim/atrim + xfade/acrossfade filtergraph joining kept segments
    with smooth crossfade transition and exact frame-locked video fade.
    When `multi_input=True`, expects each segment `i` as separate FFmpeg input `i` already seeked
    with `-ss s_i -t dur_i`. Returns (filter_fragment, video_out_label, audio_out_label_or_None).
    """
    raw_pairs = [(float(s), float(e)) for s, e in (kept_segments or [])]
    if multi_input:
        kept_segments = [(round(max(0.0, s), 3), round(max(s + 0.12, e), 3)) for s, e in raw_pairs if e > s + 0.08] or [(0.0, 0.5)]
    else:
        kept_segments = sanitize_timeline_intervals(
            raw_pairs,
            min_bound=0.0,
            min_slice_sec=0.10,
            fallback_interval=(0.0, 0.5),
        )

    if len(kept_segments) <= 1:
        ks, ke = kept_segments[0]
        if multi_input:
            parts = ["[0:v]setpts=PTS-STARTPTS[v_tl]"]
            a_lbl = None
            if has_audio:
                parts.append("[0:a]asetpts=PTS-STARTPTS[a_tl]")
                a_lbl = "a_tl"
        else:
            parts = [f"[0:v]trim=start={ks:.3f}:end={ke:.3f},setpts=PTS-STARTPTS[v_tl]"]
            a_lbl = None
            if has_audio:
                parts.append(f"[0:a]atrim=start={ks:.3f}:end={ke:.3f},asetpts=PTS-STARTPTS[a_tl]")
                a_lbl = "a_tl"
        return ";".join(parts), "v_tl", a_lbl

    parts: List[str] = []
    cf = max(0.0, float(crossfade_sec or 0.0))

    for i, (ks, ke) in enumerate(kept_segments):
        if multi_input:
            parts.append(f"[{i}:v]setpts=PTS-STARTPTS[tv{i}]")
            if has_audio:
                parts.append(f"[{i}:a]asetpts=PTS-STARTPTS[ta{i}]")
        else:
            parts.append(f"[0:v]trim=start={ks:.3f}:end={ke:.3f},setpts=PTS-STARTPTS[tv{i}]")
            if has_audio:
                parts.append(f"[0:a]atrim=start={ks:.3f}:end={ke:.3f},asetpts=PTS-STARTPTS[ta{i}]")

    if cf > 0.005:
        curr_v = "tv0"
        curr_a = "ta0"
        running_dur = kept_segments[0][1] - kept_segments[0][0]
        for i in range(1, len(kept_segments)):
            seg_dur = kept_segments[i][1] - kept_segments[i][0]
            step_cf = min(cf, running_dur * 0.20, seg_dur * 0.20)
            offset = max(0.02, running_dur - step_cf)
            next_v = "v_tl" if i == len(kept_segments) - 1 else f"vx{i}"
            parts.append(f"[{curr_v}][tv{i}]xfade=transition=fade:duration={step_cf:.3f}:offset={offset:.3f}[{next_v}]")
            curr_v = next_v

            if has_audio:
                next_a = "a_tl" if i == len(kept_segments) - 1 else f"ax{i}"
                parts.append(f"[{curr_a}][ta{i}]acrossfade=d={step_cf:.3f}:c1=qsin:c2=qsin[{next_a}]")
                curr_a = next_a

            running_dur = offset + seg_dur
    else:
        v_inputs = "".join(f"[tv{i}]" for i in range(len(kept_segments)))
        if has_audio:
            pair_inputs = "".join(f"[tv{i}][ta{i}]" for i in range(len(kept_segments)))
            parts.append(f"{pair_inputs}concat=n={len(kept_segments)}:v=1:a=1[v_tl][a_tl]")
        else:
            parts.append(f"{v_inputs}concat=n={len(kept_segments)}:v=1:a=0[v_tl]")

    return ";".join(parts), "v_tl", ("a_tl" if has_audio else None)


def build_profanity_bleep_filter(
    in_audio_label: str,
    profanity_intervals: List[Tuple[float, float]],
    total_duration: float,
    out_audio_label: str = "a_bleep",
) -> str:
    """
    Mute profane word windows on dialogue (`volume=0`) and mix in a clean -14 dB 1000 Hz
    sine bleep (`aevalsrc`) gated to those exact millisecond intervals.
    """
    if not profanity_intervals:
        return ""
    dur = max(0.2, float(total_duration or 1.0))
    expr_terms = "+".join(f"between(t,{s:.3f},{e:.3f})" for s, e in profanity_intervals)
    # 0.1995 linear amplitude ~= -14 dBFS sine wave
    return (
        f"[{in_audio_label}]volume=enable='{expr_terms}':volume=0[a_prof_muted];"
        f"aevalsrc=exprs='0.1995*sin(2*PI*1000*t)*min(1\\,{expr_terms})':s=48000:c=stereo:d={dur:.3f}[a_bleep_tone];"
        f"[a_prof_muted][a_bleep_tone]amix=inputs=2:duration=first:normalize=0[{out_audio_label}]"
    )
