import os
import re
import wave
import logging
import subprocess
import tempfile
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import numpy as np

from app.services.proxy import get_ffmpeg_cmd, probe_video

logger = logging.getLogger(__name__)

FILLER_TOKENS = {"um", "uh", "erm", "ah", "er", "hmm"}


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
    window_sec: float = 2.50,
) -> Optional[Tuple[float, float]]:
    """
    Pick the highest-excitement `window_sec` (default 2.5s) segment from the SECOND HALF
    of a clip (only when clip duration >= 8.0s). Combines acoustic RMS energy, vocal
    emphasis punctuation/speech density, and optional `cold_open_quote` anchor.
    Returns `(abs_start, abs_end)` in master-video seconds, or `None` if clip < 8.0s.
    """
    dur = float(clip_duration or 0.0)
    if dur < 8.0:
        return None
    w_len = min(max(1.5, float(window_sec or 2.5)), dur * 0.35)
    search_start_rel = dur * 0.50
    search_end_rel = max(search_start_rel + 0.1, dur - w_len)
    abs_anchor = max(0.0, float(clip_start_time or 0.0))

    # Quote match hint if inside second half
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
                    if cand >= dur * 0.42:
                        quote_rel_start = max(search_start_rel, min(search_end_rel, cand))
                        break

    # Extract fine audio RMS profile (10 Hz) over second half via face_tracker helper
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

    norm_w = _normalize_word_list(words or [], abs_anchor, dur) if words else []
    step = 0.25
    best_rel = min(search_end_rel, max(search_start_rel, dur * 0.65))
    best_score = -1.0
    cur = search_start_rel
    while cur <= search_end_rel + 1e-4:
        win_e = cur + w_len
        score = 0.0
        # Audio RMS energy component (0..1)
        if rms_bins:
            i0 = max(0, int(round((cur - search_start_rel) * 10.0)))
            i1 = min(len(rms_bins), max(i0 + 1, int(round((win_e - search_start_rel) * 10.0))))
            seg_vals = rms_bins[i0:i1]
            if seg_vals:
                score += float(np.mean(seg_vals)) * 1.6 + float(np.max(seg_vals)) * 0.8
        # Word density & exclamation/question excitement component
        win_words = [
            w for w in norm_w
            if not w.get("deleted") and not w.get("cut_from_video")
            and float(w.get("end", 0.0)) >= cur and float(w.get("start", 0.0)) <= win_e
        ]
        score += min(1.0, len(win_words) / 8.0) * 0.9
        if any(any(p in str(w.get("word", "")) for p in ("!", "?")) for w in win_words):
            score += 0.35
        if quote_rel_start is not None and abs(cur - quote_rel_start) <= 1.2:
            score += 1.25 * max(0.0, 1.0 - abs(cur - quote_rel_start) / 1.2)
        if score > best_score:
            best_score = score
            best_rel = cur
        cur += step

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
    thresh = (
        0.35
        if (auto_trim_dead_air and not tighten_pacing)
        else max(0.25, min(1.5, float(silence_threshold or 0.45)))
    )
    if tighten_pacing or auto_trim_dead_air:
        cushion = 0.09 if auto_trim_dead_air else 0.08
        if auto_trim_dead_air and video_path and os.path.exists(video_path):
            ff_silences = detect_ffmpeg_silences(
                video_path,
                start_sec=clip_start_time,
                duration_sec=clip_dur,
                noise_db=-34.0,
                min_dur=0.35,
            )
            for ss, se in ff_silences:
                c_s = round(ss + cushion, 3)
                c_e = round(se - cushion, 3)
                if c_e <= c_s + 0.08:
                    continue
                # Protect spoken word intervals against cutting inside speech
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
            # Leading silence
            if active_spoken[0]["start"] > thresh + cushion:
                raw_cuts.append((0.0, round(active_spoken[0]["start"] - cushion, 3)))
            # Inter-word gaps
            for idx in range(len(active_spoken) - 1):
                gap_s = active_spoken[idx]["end"]
                gap_e = active_spoken[idx + 1]["start"]
                if gap_e - gap_s > thresh:
                    c_s = round(gap_s + cushion, 3)
                    c_e = round(gap_e - cushion, 3)
                    if c_e > c_s + 0.08:
                        raw_cuts.append((c_s, c_e))
            # Trailing silence
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
    }


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
            step_cf = min(cf, running_dur * 0.25, seg_dur * 0.25)
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
