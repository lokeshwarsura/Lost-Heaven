"""
Lost Heaven — Deep Acoustic, Loudness & Prosody Analyzer (`audio_analyzer.py`).

Produces a 0.25s-resolution acoustic telemetry map combining:
  1. FFmpeg EBU R128 loudness pass (Integrated LUFS, LRA, True Peak dBFS, momentary M-LUFS)
  2. NumPy spectral & prosody DSP features (RMS, spectral flux, zero-crossing rate, rolling 10s pitch/energy delta)
  3. Faster-whisper word-segment cadence fusion (rolling WPM, emphasis peaks, hook/question inflections, silence spans)
"""

from __future__ import annotations

import logging
import math
import os
import re
import shutil
import subprocess
import tempfile
import wave
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import numpy as np

from app.services.proxy import get_ffmpeg_cmd

logger = logging.getLogger("lost_heaven.audio_analyzer")


def _stage_to_ascii_wav(media_path: str, work_dir: Path) -> Path:
    """
    Ensure FFmpeg & Python `wave` read from an ASCII-only WAV path inside `work_dir`
    so paths containing spaces or Unicode characters (e.g. ドキュメント) never fail.
    """
    src = Path(media_path).resolve()
    out_wav = work_dir / "audio_16k.wav"

    # Fast copy if already a valid 16k PCM mono/stereo WAV
    if src.suffix.lower() == ".wav" and src.exists():
        try:
            with wave.open(str(src), "rb") as wf:
                if wf.getframerate() == 16000 and wf.getsampwidth() in (2, 4) and wf.getnchannels() in (1, 2):
                    shutil.copy2(str(src), str(out_wav))
                    return out_wav
        except Exception:
            pass

    ffmpeg = get_ffmpeg_cmd()
    cmd = [
        ffmpeg,
        "-y",
        "-hide_banner",
        "-loglevel",
        "error",
        "-i",
        str(src),
        "-vn",
        "-ac",
        "1",
        "-ar",
        "16000",
        "-c:a",
        "pcm_s16le",
        out_wav.name,
    ]
    proc = subprocess.run(cmd, cwd=str(work_dir), capture_output=True, text=True, timeout=180)
    if proc.returncode != 0 or not out_wav.exists():
        raise RuntimeError(f"Audio extraction for analysis failed: {proc.stderr[-300:]}")
    return out_wav


def _read_pcm_mono(wav_path: Path) -> Tuple[np.ndarray, int, float]:
    """Load mono float32 samples [-1.0, 1.0], sample_rate, and duration."""
    with wave.open(str(wav_path), "rb") as wf:
        n_channels = wf.getnchannels()
        sampwidth = wf.getsampwidth()
        sr = max(1, wf.getframerate())
        n_frames = wf.getnframes()
        raw = wf.readframes(n_frames)

    if n_frames <= 0 or not raw:
        return np.zeros(max(1, sr // 4), dtype=np.float32), sr, 0.25

    if sampwidth == 2:
        arr = np.frombuffer(raw, dtype=np.int16).astype(np.float32) / 32768.0
    elif sampwidth == 4:
        arr = np.frombuffer(raw, dtype=np.int32).astype(np.float32) / 2147483648.0
    elif sampwidth == 1:
        arr = (np.frombuffer(raw, dtype=np.uint8).astype(np.float32) - 128.0) / 128.0
    else:
        arr = np.frombuffer(raw, dtype=np.int16).astype(np.float32) / 32768.0

    if n_channels > 1:
        arr = arr.reshape(-1, n_channels).mean(axis=1)

    dur = float(len(arr)) / float(sr)
    return arr, sr, dur


def _run_ebur128_pass(wav_path: Path, work_dir: Path, step_sec: float, n_bins: int) -> Dict[str, Any]:
    """
    Execute FFmpeg `ebur128=metadata=1:peak=true,ametadata=mode=print:file=-`
    and map Integrated LUFS, LRA, True Peak dBFS, and momentary M-LUFS into `n_bins`.
    """
    ffmpeg = get_ffmpeg_cmd()
    cmd = [
        ffmpeg,
        "-hide_banner",
        "-i",
        wav_path.name,
        "-af",
        "ebur128=metadata=1:peak=true,ametadata=mode=print:file=-",
        "-f",
        "null",
        "-",
    ]
    integrated_lufs: Optional[float] = None
    lra: Optional[float] = None
    true_peak_db: Optional[float] = None
    m_samples: List[Tuple[float, float]] = []

    try:
        proc = subprocess.run(cmd, cwd=str(work_dir), capture_output=True, text=True, timeout=120)
        stderr = proc.stderr or ""
        stdout = proc.stdout or ""

        # Summary block near end of stderr
        m_i = re.search(r"^\s*I:\s*([-\d.]+)\s*LUFS", stderr, flags=re.MULTILINE)
        if m_i:
            integrated_lufs = float(m_i.group(1))
        m_lra = re.search(r"^\s*LRA:\s*([-\d.]+)\s*LU", stderr, flags=re.MULTILINE)
        if m_lra:
            lra = float(m_lra.group(1))
        m_pk = re.search(r"^\s*Peak:\s*([-\d.]+)\s*dBFS", stderr, flags=re.MULTILINE)
        if m_pk:
            true_peak_db = float(m_pk.group(1))

        cur_t: Optional[float] = None
        for line in stdout.splitlines():
            if line.startswith("frame:"):
                idx_pts = line.find("pts_time:")
                if idx_pts != -1:
                    try:
                        cur_t = float(line[idx_pts + 9:].split()[0])
                    except (ValueError, IndexError):
                        pass
            elif "lavfi.r128.M=" in line and cur_t is not None:
                try:
                    val = float(line.split("lavfi.r128.M=", 1)[1].split()[0])
                    m_samples.append((cur_t, max(-70.0, val)))
                except (ValueError, IndexError):
                    pass
            elif "lavfi.r128.true_peak=" in line and true_peak_db is None:
                try:
                    tp_lin = max(1e-6, float(line.split("lavfi.r128.true_peak=", 1)[1].split()[0]))
                    cand_db = 20.0 * math.log10(tp_lin)
                    true_peak_db = cand_db if true_peak_db is None else max(true_peak_db, cand_db)
                except (ValueError, IndexError):
                    pass
    except Exception as e:
        logger.debug(f"FFmpeg ebur128 metadata pass skipped: {e}")

    # Bin momentary LUFS samples onto exact step_sec buckets
    binned_m: List[Optional[float]] = [None] * max(1, n_bins)
    if m_samples:
        acc: List[List[float]] = [[] for _ in range(max(1, n_bins))]
        for ts, m_val in m_samples:
            b = int(ts / step_sec)
            if 0 <= b < n_bins and m_val > -115.0:
                acc[b].append(m_val)
        for b_idx, vals in enumerate(acc):
            if vals:
                binned_m[b_idx] = float(max(vals))

    return {
        "integrated_lufs": integrated_lufs,
        "lra": lra,
        "true_peak_db": true_peak_db,
        "binned_m": binned_m,
    }


VOCAL_EMOTIONS: Tuple[str, ...] = (
    "excited_hype",
    "laughing",
    "urgent_tense",
    "calm_storytelling",
    "deadpan",
)

ACOUSTIC_EVENT_LABELS: Dict[str, str] = {
    "laughter_burst": "Laughter Burst",
    "crowd_cheering": "Crowd Cheering / Roar",
    "bass_music_drop": "Bass / Music Drop",
    "dramatic_pause": "Dramatic Pause",
}


_MEL_CACHE: Dict[Tuple[int, int, int], Tuple[np.ndarray, np.ndarray]] = {}

_FLUENCY_FILLERS_SINGLE = {"um", "uh", "ah", "er", "hmm", "like", "basically", "literally"}
_FLUENCY_FILLERS_BIGRAM = {("you", "know"), ("sort", "of"), ("kind", "of"), ("i", "mean")}


def _get_mel_dct_matrices(n_freqs: int, sr: int, n_mels: int = 26, n_mfcc: int = 13) -> Tuple[np.ndarray, np.ndarray]:
    """Cached vectorized 26-band Mel filterbank & 13-band DCT-II transform matrices (pure NumPy)."""
    key = (n_freqs, sr, n_mels)
    if key in _MEL_CACHE:
        return _MEL_CACHE[key]

    fft_freqs = np.linspace(0.0, float(sr) / 2.0, n_freqs, dtype=np.float32)
    fmin = 80.0
    fmax = min(7600.0, float(sr) * 0.49)
    mel_min = 2595.0 * math.log10(1.0 + fmin / 700.0)
    mel_max = 2595.0 * math.log10(1.0 + fmax / 700.0)
    mel_pts = np.linspace(mel_min, mel_max, n_mels + 2, dtype=np.float32)
    hz_pts = 700.0 * (10.0 ** (mel_pts / 2595.0) - 1.0)

    fb = np.zeros((n_mels, n_freqs), dtype=np.float32)
    for m in range(n_mels):
        left, center, right = float(hz_pts[m]), float(hz_pts[m + 1]), float(hz_pts[m + 2])
        if center > left:
            up = (fft_freqs - left) / (center - left)
            fb[m] += np.maximum(0.0, np.minimum(1.0, up))
        if right > center:
            down = (right - fft_freqs) / (right - center)
            fb[m] = np.minimum(fb[m], np.maximum(0.0, down))

    dct = (
        np.cos(np.pi * np.arange(n_mfcc, dtype=np.float32)[:, None] * (np.arange(n_mels, dtype=np.float32)[None, :] + 0.5) / float(n_mels))
        * math.sqrt(2.0 / float(n_mels))
    ).astype(np.float32)
    _MEL_CACHE[key] = (fb, dct)
    return fb, dct


def _estimate_pitch_hnr_dual_batch(
    chunks: np.ndarray,
    sr: int,
) -> Tuple[np.ndarray, np.ndarray, np.ndarray, np.ndarray]:
    """
    Vectorized batched FFT autocorrelation across `(B, win_samples)` returning:
      - f0_hz: (B,) primary fundamental frequency in [75..420 Hz]
      - hnr_norm: (B,) harmonicity proxy in [0..0.995]
      - dual_pitch_ratio: (B,) strength of competing non-octave secondary F0 peak [0..1]
      - dual_f0_hz: (B,) secondary F0 frequency if detected
    Runs ~2400 bins (10-minute WAV) in < 30ms with zero Python per-sample loops.
    """
    b_cnt, n_win = chunks.shape
    f0_out = np.zeros(b_cnt, dtype=np.float32)
    hnr_out = np.zeros(b_cnt, dtype=np.float32)
    dual_ratio_out = np.zeros(b_cnt, dtype=np.float32)
    dual_f0_out = np.zeros(b_cnt, dtype=np.float32)

    if b_cnt == 0 or n_win < int(sr * 0.04):
        return f0_out, hnr_out, dual_ratio_out, dual_f0_out

    min_lag = max(1, int(sr / 420.0))
    max_lag = min(n_win // 2, int(sr / 75.0))
    n_sub = min(n_win, int(sr * 0.08))
    if max_lag <= min_lag + 2 or n_sub < max_lag + 2:
        return f0_out, hnr_out, dual_ratio_out, dual_f0_out

    sub = chunks[:, :n_sub]
    sub = sub - np.mean(sub, axis=1, keepdims=True)
    rms = np.sqrt(np.mean(sub * sub, axis=1))
    n_fft = 1 << int(math.ceil(math.log2(n_sub * 2)))

    spec = np.fft.rfft(sub, n=n_fft, axis=1)
    acf = np.fft.irfft(spec * np.conj(spec), n=n_fft, axis=1)[:, : max_lag + 1]

    lags = np.arange(max_lag + 1, dtype=np.float32)
    overlap_weight = np.maximum(0.25, 1.0 - (lags / float(max(1, n_sub))))
    denom = np.maximum(1e-8, acf[:, :1]) * overlap_weight[None, :]
    acf_norm = np.clip(acf / denom, 0.0, 0.995)

    band = acf_norm[:, min_lag : max_lag + 1]  # (B, L)
    cand_freqs = float(sr) / np.maximum(1.0, lags[min_lag : max_lag + 1])  # (L,)
    best_rel = np.argmax(band, axis=1)
    rows = np.arange(b_cnt)
    peak_corr = band[rows, best_rel]
    voiced = (rms >= 0.006) & (acf[:, 0] > 1e-8)

    hnr_out = np.where(voiced, peak_corr, 0.0).astype(np.float32)
    has_pitch = voiced & (peak_corr >= 0.30)
    best_lag = min_lag + best_rel
    f0_vals = float(sr) / np.maximum(1.0, best_lag.astype(np.float32))
    f0_out = np.where(has_pitch, f0_vals, 0.0).astype(np.float32)

    # Secondary non-octave F0 peak detection for simultaneous cross-talk overlap
    if np.any(has_pitch):
        freq_ratio = cand_freqs[None, :] / np.maximum(40.0, f0_vals[:, None])
        harm_mask = (
            (np.abs(freq_ratio - 1.0) < 0.16)
            | (np.abs(freq_ratio - 0.5) < 0.12)
            | (np.abs(freq_ratio - 2.0) < 0.16)
            | (np.abs(freq_ratio - 3.0) < 0.16)
            | (np.abs(freq_ratio - (1.0 / 3.0)) < 0.12)
        )
        masked_band = np.where(harm_mask, 0.0, band)
        sec_rel = np.argmax(masked_band, axis=1)
        sec_corr = masked_band[rows, sec_rel]
        sec_lag = min_lag + sec_rel
        sec_f0 = float(sr) / np.maximum(1.0, sec_lag.astype(np.float32))
        ratio = np.where(has_pitch & (sec_corr >= 0.22), sec_corr / np.maximum(0.28, peak_corr), 0.0)
        dual_ratio_out = np.clip(ratio, 0.0, 1.0).astype(np.float32)
        dual_f0_out = np.where(ratio > 0.0, sec_f0, 0.0).astype(np.float32)

    return f0_out, hnr_out, dual_ratio_out, dual_f0_out


def _count_fillers_in_words(words: List[Dict[str, Any]]) -> int:
    """Count single-word and bigram hesitation fillers inside `words`."""
    if not words:
        return 0
    cleaned = [re.sub(r"[^\w']", "", str(w.get("word", "")).strip()).lower() for w in words]
    flagged = set()
    count = 0
    for i, w in enumerate(words):
        if bool(w.get("is_filler")):
            count += 1
            flagged.add(i)
            continue
        tok = cleaned[i]
        if tok in _FLUENCY_FILLERS_SINGLE:
            count += 1
            flagged.add(i)
            continue
        if i + 1 < len(words) and (tok, cleaned[i + 1]) in _FLUENCY_FILLERS_BIGRAM and i not in flagged:
            count += 1
            flagged.add(i)
            flagged.add(i + 1)
    return count


def _kmeans_deterministic(X: np.ndarray, k: int, max_iter: int = 25) -> Tuple[np.ndarray, float, np.ndarray]:
    n = X.shape[0]
    if n <= k:
        labels = np.arange(n, dtype=np.int32) % max(1, k)
        cents = np.stack([X[min(i, n - 1)] for i in range(max(1, k))], axis=0)
        return labels, 0.0, cents

    centers = [X[0].copy()]
    for _ in range(1, k):
        dists = np.min(np.stack([np.sum((X - c) ** 2, axis=1) for c in centers], axis=0), axis=0)
        centers.append(X[int(np.argmax(dists))].copy())
    centroids = np.stack(centers, axis=0)

    labels = np.zeros(n, dtype=np.int32)
    for _ in range(max_iter):
        dmat = np.sum((X[:, None, :] - centroids[None, :, :]) ** 2, axis=2)
        new_labels = np.argmin(dmat, axis=1).astype(np.int32)
        if np.array_equal(new_labels, labels):
            break
        labels = new_labels
        for ci in range(k):
            mask = labels == ci
            if np.any(mask):
                centroids[ci] = np.mean(X[mask], axis=0)

    uniq = np.unique(labels)
    if len(uniq) < 2:
        return labels, 0.0, centroids
    dist_mat = np.sqrt(np.maximum(0.0, np.sum((X[:, None, :] - X[None, :, :]) ** 2, axis=2)))
    sils = np.zeros(n, dtype=np.float32)
    for i in range(n):
        own = labels[i]
        same_mask = labels == own
        same_mask[i] = False
        a = float(np.mean(dist_mat[i, same_mask])) if np.any(same_mask) else 0.0
        b = float("inf")
        for other in uniq:
            if other == own:
                continue
            other_mask = labels == other
            if np.any(other_mask):
                b = min(b, float(np.mean(dist_mat[i, other_mask])))
        sils[i] = 0.0 if b == float("inf") else (b - a) / max(1e-6, max(a, b))
    return labels, float(np.mean(sils)), centroids


def _analyze_speaker_diarization(
    *,
    samples: np.ndarray,
    sr: int,
    step: float,
    total_dur: float,
    timeline: List[Dict[str, Any]],
    clean_words: List[Dict[str, Any]],
    raw_mfcc: np.ndarray,
    raw_centroid: np.ndarray,
    raw_rolloff: np.ndarray,
    raw_pitch: np.ndarray,
    raw_entropy: np.ndarray,
    raw_dual_ratio: np.ndarray,
    raw_dual_f0: np.ndarray,
    mel_fb_small: np.ndarray,
    dct_small: np.ndarray,
) -> Dict[str, Any]:
    """
    Vectorized acoustic utterance speaker diarization (`Speaker A..D`), dominance ratios,
    and cross-talk overlap detection (`high spectral entropy + dual-pitch energy bursts`).
    """
    n_bins = len(timeline)
    if n_bins == 0:
        return {
            "speaker_count": 0,
            "speakers": {},
            "dominance_ratios": {},
            "turns": [],
            "speaker_switch_rate": 0.0,
            "crosstalk_overlaps": [],
            "crosstalk_count": 0,
        }

    # Build micro-utterances either from word segments or speech energy + pitch-step windows
    utterances: List[Dict[str, Any]] = []
    if clean_words:
        cur_w: List[Dict[str, Any]] = []
        for idx_w, w in enumerate(clean_words):
            cur_w.append(w)
            ws0 = float(cur_w[0]["start"])
            we_cur = float(w["end"])
            next_gap = (float(clean_words[idx_w + 1]["start"]) - we_cur) if (idx_w + 1 < len(clean_words)) else 9.9
            has_term = bool(re.search(r"[\.\?\!\;]$", str(w["word"]).strip()))
            if next_gap >= 0.35 or has_term or (we_cur - ws0) >= 2.2 or len(cur_w) >= 7:
                utterances.append({"start": ws0, "end": max(ws0 + 0.15, we_cur), "word_count": len(cur_w)})
                cur_w = []
        if cur_w:
            utterances.append({
                "start": float(cur_w[0]["start"]),
                "end": max(float(cur_w[0]["start"]) + 0.15, float(cur_w[-1]["end"])),
                "word_count": len(cur_w),
            })
    else:
        u_start: Optional[int] = None
        last_voiced_f0: float = 0.0
        for b in range(n_bins):
            pt = timeline[b]
            active = bool(pt["is_speech"] and float(pt["rms"]) >= 0.12)
            cur_f0 = float(raw_pitch[b])
            f0_jump = False
            if active and u_start is not None and cur_f0 > 45.0 and last_voiced_f0 > 45.0:
                semi_diff = abs(12.0 * math.log2(cur_f0 / last_voiced_f0))
                if semi_diff >= 3.2:
                    f0_jump = True
            if active and (u_start is None or (b - u_start) * step >= 2.0 or f0_jump):
                if u_start is not None and (b - u_start) * step >= 0.25:
                    utterances.append({
                        "start": round(u_start * step, 3),
                        "end": round(b * step, 3),
                        "word_count": 0,
                    })
                u_start = b
            elif not active and u_start is not None:
                if (b - u_start) * step >= 0.25:
                    utterances.append({
                        "start": round(u_start * step, 3),
                        "end": round(b * step, 3),
                        "word_count": 0,
                    })
                u_start = None
            if active and cur_f0 > 45.0:
                last_voiced_f0 = cur_f0
        if u_start is not None and (n_bins - u_start) * step >= 0.25:
            utterances.append({
                "start": round(u_start * step, 3),
                "end": round(n_bins * step, 3),
                "word_count": 0,
            })

    # Filter out micro zero-energy blips and extract 29-dim embedding per utterance
    valid_utts: List[Dict[str, Any]] = []
    feats: List[np.ndarray] = []
    for u in utterances:
        b0 = max(0, int(math.floor(u["start"] / step)))
        b1 = min(n_bins, max(b0 + 1, int(math.ceil(u["end"] / step))))
        mfcc_slice = raw_mfcc[b0:b1]
        rms_slice = np.array([float(timeline[bi]["rms"]) for bi in range(b0, b1)], dtype=np.float32)
        if len(rms_slice) == 0 or float(np.max(rms_slice)) < 0.08:
            continue

        # Refine MFCC mean/std on 25ms sub-frames when bin count is small so std captures formant spread
        i0 = max(0, int(round(u["start"] * sr)))
        i1 = min(len(samples), int(round(u["end"] * sr)))
        if (b1 - b0) < 4 and (i1 - i0) >= 512:
            sub_len = 512
            sub_hop = 160
            n_frames_u = max(1, (i1 - i0 - sub_len) // sub_hop + 1)
            idx_mat = i0 + np.arange(n_frames_u, dtype=np.int64)[:, None] * sub_hop + np.arange(sub_len, dtype=np.int64)[None, :]
            sub_chunks = samples[idx_mat]
            sub_hann = np.hanning(sub_len).astype(np.float32)
            sub_pwr = (np.abs(np.fft.rfft(sub_chunks * sub_hann[None, :], axis=1)) ** 2).astype(np.float32)
            sub_mel = np.log(np.maximum(1e-7, sub_pwr @ mel_fb_small.T))
            sub_mfcc = sub_mel @ dct_small.T  # (F, 13)
            mfcc_mean = np.mean(sub_mfcc, axis=0)
            mfcc_std = np.std(sub_mfcc, axis=0)
        else:
            mfcc_mean = np.mean(mfcc_slice, axis=0)
            mfcc_std = np.std(mfcc_slice, axis=0)

        pitches = raw_pitch[b0:b1]
        vp = pitches[pitches > 45.0]
        med_f0 = float(np.median(vp)) if vp.size > 0 else 150.0
        mean_cent = float(np.mean(raw_centroid[b0:b1]))
        mean_roll = float(np.mean(raw_rolloff[b0:b1]))

        vec = np.concatenate([
            mfcc_mean.astype(np.float32),
            mfcc_std.astype(np.float32),
            np.array([med_f0, mean_cent, mean_roll], dtype=np.float32),
        ])
        valid_utts.append({**u, "b0": b0, "b1": b1, "med_f0": med_f0})
        feats.append(vec)

    if not valid_utts:
        for pt in timeline:
            pt["speaker_id"] = "Speaker A" if pt["is_speech"] else None
        return {
            "speaker_count": 1 if any(pt["is_speech"] for pt in timeline) else 0,
            "speakers": {"Speaker A": {"id": "Speaker A", "dominance_ratio": 1.0, "duration_sec": 0.0, "turn_count": 0}} if any(pt["is_speech"] for pt in timeline) else {},
            "dominance_ratios": {"Speaker A": 1.0} if any(pt["is_speech"] for pt in timeline) else {},
            "turns": [],
            "speaker_switch_rate": 0.0,
            "crosstalk_overlaps": [],
            "crosstalk_count": 0,
        }

    labels = np.zeros(len(valid_utts), dtype=np.int32)
    if len(valid_utts) >= 2:
        X = np.stack(feats, axis=0)
        mu = np.mean(X, axis=0)
        std = np.maximum(1e-3, np.std(X, axis=0))
        Xn = (X - mu) / std
        # Weight vocal tract MFCC mean + pitch + brightness/rolloff strongest for clean speaker separation
        Xn[:, 0:6] *= 1.35
        Xn[:, 13:26] *= 0.45
        Xn[:, 26] *= 2.40  # median F0 pitch
        Xn[:, 27] *= 1.15  # centroid
        Xn[:, 28] *= 1.10  # rolloff

        max_k = min(4, len(valid_utts))
        if len(valid_utts) >= 4:
            max_k = min(4, len(valid_utts) // 2)
        best_sil = 0.0
        best_labels = np.zeros(len(valid_utts), dtype=np.int32)
        for k in range(2, max_k + 1):
            lbls, sil, cents = _kmeans_deterministic(Xn, k)
            counts = [int(np.sum(lbls == c)) for c in range(k)]
            min_req = max(2, len(valid_utts) // 8) if len(valid_utts) >= 6 else 1
            if min(counts) < min_req:
                continue
            # Ensure clusters genuinely diverge acoustically (F0 shift >= 2.2 semitones OR embedding gap >= 2.2)
            pair_dists = [
                float(np.linalg.norm(cents[a] - cents[b]))
                for a in range(k)
                for b in range(a + 1, k)
            ]
            f0_cl = [float(np.median([valid_utts[i]["med_f0"] for i in range(len(valid_utts)) if lbls[i] == c])) for c in range(k)]
            f0_spread_semi = max(
                abs(12.0 * math.log2(max(40.0, f0_cl[a]) / max(40.0, f0_cl[b])))
                for a in range(k)
                for b in range(a + 1, k)
            )
            if (min(pair_dists) >= 2.0 or f0_spread_semi >= 2.2) and sil > best_sil:
                best_sil = sil
                best_labels = lbls

        if best_sil >= 0.24:
            labels = best_labels

    # Smooth isolated micro-glitches between identical neighbors
    smoothed = labels.copy()
    for i in range(1, len(smoothed) - 1):
        if smoothed[i - 1] == smoothed[i + 1] and smoothed[i] != smoothed[i - 1]:
            if (valid_utts[i]["end"] - valid_utts[i]["start"]) < 0.85:
                smoothed[i] = smoothed[i - 1]

    # Map chronologically to "Speaker A", "Speaker B", ...
    cluster_to_name: Dict[int, str] = {}
    for lbl in smoothed.tolist():
        if lbl not in cluster_to_name:
            idx_c = len(cluster_to_name)
            cluster_to_name[lbl] = f"Speaker {chr(65 + idx_c)}"

    for u_i, u in enumerate(valid_utts):
        sp_name = cluster_to_name[int(smoothed[u_i])]
        u["speaker_id"] = sp_name
        for b in range(u["b0"], u["b1"]):
            timeline[b]["speaker_id"] = sp_name

    # Coalesce consecutive turns per speaker
    turns: List[Dict[str, Any]] = []
    dur_by_sp: Dict[str, float] = {}
    f0_by_sp: Dict[str, List[float]] = {}
    words_by_sp: Dict[str, int] = {}
    for u in valid_utts:
        sp = u["speaker_id"]
        u_dur = max(step, float(u["end"] - u["start"]))
        dur_by_sp[sp] = dur_by_sp.get(sp, 0.0) + u_dur
        words_by_sp[sp] = words_by_sp.get(sp, 0) + int(u.get("word_count", 0))
        f0_by_sp.setdefault(sp, []).append(float(u["med_f0"]))
        if turns and turns[-1]["speaker_id"] == sp and (u["start"] - turns[-1]["end"]) <= 0.55:
            turns[-1]["end"] = round(max(turns[-1]["end"], u["end"]), 3)
            turns[-1]["duration"] = round(turns[-1]["end"] - turns[-1]["start"], 3)
            turns[-1]["word_count"] += int(u.get("word_count", 0))
        else:
            turns.append({
                "speaker_id": sp,
                "start": round(u["start"], 3),
                "end": round(u["end"], 3),
                "duration": round(u_dur, 3),
                "median_f0_hz": round(u["med_f0"], 1),
                "word_count": int(u.get("word_count", 0)),
            })

    tot_sp_dur = max(1e-3, sum(dur_by_sp.values()))
    dominance_ratios = {
        sp: round(d / tot_sp_dur, 4)
        for sp, d in sorted(dur_by_sp.items(), key=lambda kv: kv[0])
    }
    speakers_meta = {
        sp: {
            "id": sp,
            "dominance_ratio": dominance_ratios[sp],
            "duration_sec": round(dur_by_sp[sp], 2),
            "median_f0_hz": round(float(np.median(f0_by_sp[sp])), 1) if f0_by_sp.get(sp) else 0.0,
            "word_count": words_by_sp.get(sp, 0),
        }
        for sp in dominance_ratios
    }
    switches = sum(1 for i in range(1, len(turns)) if turns[i]["speaker_id"] != turns[i - 1]["speaker_id"])
    switch_rate = round((switches / max(1.0, float(total_dur))) * 10.0, 2)

    # Cross-talk overlap detection: high spectral entropy + dual-pitch competing bursts
    turn_change_times = [turns[i]["start"] for i in range(1, len(turns))]
    ct_overlaps: List[Dict[str, Any]] = []
    ct_start_idx: Optional[int] = None
    ct_confs: List[float] = []
    ct_ents: List[float] = []
    for b, pt in enumerate(timeline):
        rms_v = float(pt["rms"])
        ent = float(raw_entropy[b])
        dual_r = float(raw_dual_ratio[b])
        hnr_v = float(pt["hnr_norm"])
        near_switch = any(abs(float(pt["t"]) - st) <= 0.65 for st in turn_change_times)
        dual_hit = dual_r >= 0.44 and float(raw_dual_f0[b]) >= 75.0 and (ent >= 0.28 or hnr_v < 0.85)
        dense_clash = ent >= 0.68 and rms_v >= 0.28 and (dual_r >= 0.28 or near_switch or float(pt["flux"]) >= 0.28)
        is_ct = bool(rms_v >= 0.20 and (dual_hit or dense_clash))
        pt["is_crosstalk"] = is_ct
        pt["spectral_entropy"] = round(ent, 4)

        if is_ct:
            conf_b = min(0.98, 0.45 + 0.35 * dual_r + 0.25 * ent)
            if ct_start_idx is None:
                ct_start_idx = b
                ct_confs = []
                ct_ents = []
            ct_confs.append(conf_b)
            ct_ents.append(ent)
        elif ct_start_idx is not None:
            t0 = float(timeline[ct_start_idx]["t"])
            t1 = round(float(timeline[b - 1]["t"]) + step, 3)
            ct_overlaps.append({
                "t": round(t0, 2),
                "end": round(t1, 2),
                "confidence": round(sum(ct_confs) / max(1, len(ct_confs)), 3),
                "spectral_entropy": round(max(ct_ents), 3),
            })
            ct_start_idx = None
    if ct_start_idx is not None:
        t0 = float(timeline[ct_start_idx]["t"])
        t1 = round(float(timeline[-1]["t"]) + step, 3)
        ct_overlaps.append({
            "t": round(t0, 2),
            "end": round(t1, 2),
            "confidence": round(sum(ct_confs) / max(1, len(ct_confs)), 3),
            "spectral_entropy": round(max(ct_ents), 3),
        })

    return {
        "speaker_count": len(speakers_meta),
        "speakers": speakers_meta,
        "dominance_ratios": dominance_ratios,
        "turns": turns,
        "speaker_switch_rate": switch_rate,
        "crosstalk_overlaps": ct_overlaps,
        "crosstalk_count": len(ct_overlaps),
    }


def _analyze_speech_fluency(
    *,
    clean_words: List[Dict[str, Any]],
    timeline: List[Dict[str, Any]],
    step: float,
    total_dur: float,
) -> Dict[str, Any]:
    """
    Combine Whisper word probability confidence, inter-word pause variance, and filler words
    into a rolling 0..100 `fluency_score` plus `clean_hook_candidates` and `hesitation_intervals`.
    """
    n_bins = len(timeline)
    if not clean_words:
        # Derive conservative acoustic continuity score when transcript is absent
        for pt in timeline:
            pt["fluency_score"] = round(min(92.0, 58.0 + 30.0 * float(pt["hnr_norm"])), 1) if pt["is_speech"] else 75.0
        sp_pts = [float(pt["fluency_score"]) for pt in timeline if pt["is_speech"]]
        return {
            "avg_fluency_score": round(sum(sp_pts) / len(sp_pts), 1) if sp_pts else 75.0,
            "filler_count": 0,
            "filler_rate_per_min": 0.0,
            "clean_hook_candidates": [],
            "hesitation_intervals": [],
        }

    total_fillers = _count_fillers_in_words(clean_words)
    fluency_vals: List[float] = []

    for b, pt in enumerate(timeline):
        t_cur = float(pt["t"])
        t_mid = min(total_dur, t_cur + step * 0.5)
        w_s, w_e = max(0.0, t_mid - 1.5), min(total_dur, t_mid + 1.5)
        win_words = [w for w in clean_words if float(w["end"]) >= w_s and float(w["start"]) <= w_e]

        if not win_words:
            f_sc = 80.0 if not pt["is_speech"] else 68.0
        else:
            probs = [
                max(0.10, min(1.0, float(w.get("probability", w.get("confidence", 0.90)) or 0.90)))
                for w in win_words
            ]
            avg_prob = sum(probs) / len(probs)
            conf_pts = avg_prob * 40.0

            n_fill = _count_fillers_in_words(win_words)
            fill_ratio = n_fill / float(max(1, len(win_words)))
            filler_pen = min(46.0, n_fill * 14.0 + fill_ratio * 34.0)

            gaps = [
                max(0.0, float(win_words[i + 1]["start"]) - float(win_words[i]["end"]))
                for i in range(len(win_words) - 1)
            ]
            if gaps:
                gap_arr = np.array(gaps, dtype=np.float32)
                gap_std = float(np.std(gap_arr))
                stall_gap = float(np.max(gap_arr))
                # Penalize irregular hesitation gaps (>0.32s) and stalls (>0.8s)
                stall_pen = min(22.0, max(0.0, stall_gap - 0.32) * 20.0)
                var_pen = min(14.0, max(0.0, gap_std - 0.12) * 35.0)
                pause_pts = max(4.0, 35.0 - stall_pen - var_pen)
            else:
                pause_pts = 32.0

            non_fill = max(0, len(win_words) - n_fill)
            flow_pts = min(25.0, non_fill * 5.0)
            f_sc = max(5.0, min(100.0, conf_pts + pause_pts + flow_pts - filler_pen))

        pt["fluency_score"] = round(f_sc, 1)
        if pt["is_speech"]:
            fluency_vals.append(f_sc)

    # Tag punchy, hesitation-free opening sentences as clean_hook_candidates
    sentences: List[List[Dict[str, Any]]] = []
    cur_sent: List[Dict[str, Any]] = []
    for i, w in enumerate(clean_words):
        cur_sent.append(w)
        nxt_gap = (float(clean_words[i + 1]["start"]) - float(w["end"])) if i + 1 < len(clean_words) else 9.9
        is_term = bool(re.search(r"[\.\?\!\;]$", str(w["word"]).strip()))
        if is_term or nxt_gap >= 0.60 or len(cur_sent) >= 20:
            sentences.append(cur_sent)
            cur_sent = []
    if cur_sent:
        sentences.append(cur_sent)

    clean_hooks: List[Dict[str, Any]] = []
    for sent in sentences:
        if len(sent) < 2:
            continue
        s0 = float(sent[0]["start"])
        s1 = float(sent[-1]["end"])
        open_words = [w for w in sent if float(w["start"]) < (s0 + 2.0)]
        if _count_fillers_in_words(open_words) > 0:
            continue
        # Require clean sentence-level fluency (>= 82)
        sent_bins = [pt for pt in timeline if (s0 - 0.1) <= float(pt["t"]) <= min(s1, s0 + 3.0) + 0.1]
        if not sent_bins:
            continue
        mean_fluency = sum(float(pt["fluency_score"]) for pt in sent_bins) / len(sent_bins)
        if mean_fluency < 82.0:
            continue
        # Require strong initial vocal transient / clear onset energy
        onset_bins = [pt for pt in timeline if (s0 - 0.25) <= float(pt["t"]) <= (s0 + 1.0)]
        peak_rms = max((float(pt["rms"]) for pt in onset_bins), default=0.0)
        peak_exc = max((float(pt["excitement_score"]) for pt in onset_bins), default=0.0)
        if peak_rms < 0.20 and peak_exc < 28.0:
            continue
        first_bin = min(timeline, key=lambda pt: abs(float(pt["t"]) - s0)) if timeline else {}
        clean_hooks.append({
            "t": round(s0, 3),
            "end": round(s1, 3),
            "text": " ".join(str(w["word"]).strip() for w in sent[:12]),
            "fluency_score": round(mean_fluency, 1),
            "onset_rms": round(peak_rms, 3),
            "speaker_id": first_bin.get("speaker_id") or "Speaker A",
        })

    # Detect low-fluency hesitation stretches >= 1.2s
    hesitation_intervals: List[Dict[str, Any]] = []
    for i in range(len(clean_words) - 1):
        g_s = float(clean_words[i]["end"])
        g_e = float(clean_words[i + 1]["start"])
        if (g_e - g_s) >= 1.2:
            hesitation_intervals.append({"t": round(g_s, 2), "end": round(g_e, 2), "duration": round(g_e - g_s, 2), "reason": "mid_speech_stall"})
    run_s: Optional[float] = None
    run_scores: List[float] = []
    for pt in timeline:
        if pt["is_speech"] and float(pt["fluency_score"]) < 60.0:
            if run_s is None:
                run_s = float(pt["t"])
                run_scores = []
            run_scores.append(float(pt["fluency_score"]))
        elif run_s is not None:
            t_end = float(pt["t"])
            if (t_end - run_s) >= 1.2:
                hesitation_intervals.append({
                    "t": round(run_s, 2),
                    "end": round(t_end, 2),
                    "duration": round(t_end - run_s, 2),
                    "fluency_avg": round(sum(run_scores) / len(run_scores), 1),
                    "reason": "low_fluency_fillers",
                })
            run_s = None

    spoken_dur_min = max(1.0 / 60.0, (clean_words[-1]["end"] - clean_words[0]["start"]) / 60.0)
    return {
        "avg_fluency_score": round(sum(fluency_vals) / len(fluency_vals), 1) if fluency_vals else 75.0,
        "filler_count": total_fillers,
        "filler_rate_per_min": round(total_fillers / spoken_dur_min, 2),
        "clean_hook_candidates": clean_hooks[:24],
        "hesitation_intervals": hesitation_intervals,
    }


def _analyze_music_bed(
    *,
    samples: np.ndarray,
    sr: int,
    step: float,
    timeline: List[Dict[str, Any]],
    clean_words: List[Dict[str, Any]],
    raw_low_music_ratio: np.ndarray,
    raw_tonal_ratio: np.ndarray,
    raw_bin_db: np.ndarray,
    raw_bin_rms: np.ndarray,
) -> Dict[str, Any]:
    """
    Detect existing background music beds via 50-180 Hz sub-band rhythmic periodicity
    and non-speech harmonic tonal persistence.
    """
    n_bins = len(timeline)
    if n_bins == 0 or len(samples) < int(sr * 0.5):
        return {
            "has_existing_music": False,
            "avg_music_intensity": 0.0,
            "beat_periodicity": 0.0,
            "estimated_bpm": 0.0,
            "music_intensity_timeline": [],
        }

    # Vectorized 20 Hz (50ms) low-frequency (50..180 Hz) amplitude envelope for rhythmic beat detection
    sub_win = max(64, int(round(0.05 * sr)))
    n_sub = len(samples) // sub_win
    beat_periodicity = 0.0
    estimated_bpm = 0.0
    if n_sub >= 24:
        reshaped = samples[: n_sub * sub_win].reshape(n_sub, sub_win)
        sub_hann = np.hanning(sub_win).astype(np.float32)
        sub_fft = np.abs(np.fft.rfft(reshaped * sub_hann[None, :], axis=1))
        sub_freqs = np.fft.rfftfreq(sub_win, d=1.0 / float(sr))
        band_50_180 = (sub_freqs >= 45.0) & (sub_freqs <= 185.0)
        if np.any(band_50_180):
            low_env = np.sqrt(np.mean(sub_fft[:, band_50_180] ** 2, axis=1)) / float(sub_win)
            # Detrend slow 0..0.7 Hz speech pause transitions so rhythmic beats (54..198 BPM) stand out clearly
            k_smooth = min(len(low_env), 17)
            if k_smooth >= 5:
                kernel = np.ones(k_smooth, dtype=np.float32) / float(k_smooth)
                low_trend = np.convolve(np.pad(low_env, (k_smooth // 2, k_smooth - 1 - k_smooth // 2), mode="edge"), kernel, mode="valid")
                low_ac = low_env - low_trend
            else:
                low_ac = low_env - float(np.mean(low_env))
            if float(np.mean(low_env)) > 0.0015 and float(np.std(low_ac)) > 1e-4:
                env_rate = float(sr) / float(sub_win)  # 20 Hz
                # Compute normalized autocorrelation over BPM beat lags (54..198 BPM -> 0.30s..1.11s)
                min_b_lag = max(2, int(round(0.30 * env_rate)))
                max_b_lag = min(len(low_ac) // 2, int(round(1.11 * env_rate)))
                n_ac = len(low_ac)
                n_fft_ac = 1 << int(math.ceil(math.log2(max(8, n_ac * 2))))
                sp_ac = np.fft.rfft(low_ac, n=n_fft_ac)
                acf_full = np.fft.irfft(sp_ac * np.conj(sp_ac), n=n_fft_ac)[: n_ac]
                if acf_full[0] > 1e-10 and max_b_lag > min_b_lag:
                    lags_ac = np.arange(n_ac, dtype=np.float32)
                    # Unbiased lag normalization over overlap count (n_ac - lag)
                    norm_acf = acf_full / (acf_full[0] * np.maximum(0.25, 1.0 - lags_ac / float(max(1, n_ac))))
                    bpm_slice = norm_acf[min_b_lag : max_b_lag + 1]
                    pk_rel = int(np.argmax(bpm_slice))
                    pk_corr = float(max(0.0, min(1.0, bpm_slice[pk_rel])))
                    best_lag = min_b_lag + pk_rel
                    beat_periodicity = round(pk_corr, 4)
                    estimated_bpm = round((env_rate / float(max(1, best_lag))) * 60.0, 1)

    music_tl: List[Dict[str, Any]] = []
    non_speech_hits = 0
    non_speech_total = 0
    non_speech_intensities: List[float] = []
    all_intensities: List[float] = []

    for b, pt in enumerate(timeline):
        t_p = float(pt["t"])
        tonal_n = max(0.0, min(1.0, (float(raw_tonal_ratio[b]) - 2.2) / 14.0))
        low_n = max(0.0, min(1.0, float(raw_low_music_ratio[b]) / 0.35))
        b_db = float(raw_bin_db[b])
        b_rms = float(raw_bin_rms[b])
        db_active = max(0.0, min(1.0, (b_db + 46.0) / 30.0))
        if b_db <= -44.0 or b_rms <= 0.006:
            m_int = 0.0
        else:
            m_raw = 100.0 * db_active * (0.40 * tonal_n + 0.32 * low_n + 0.28 * beat_periodicity)
            m_int = round(max(0.0, min(100.0, m_raw)), 1)

        pt["music_bed_intensity"] = m_int
        music_tl.append({"t": pt["t"], "intensity": m_int})
        all_intensities.append(m_int)

        if clean_words:
            # Guard 0.35s around every Whisper word boundary so voice onsets/tails don't leak into pauses
            in_spoken_word = any(
                float(w["end"]) >= (t_p - 0.35) and float(w["start"]) <= (t_p + step + 0.35)
                for w in clean_words
            )
            is_ns = not in_spoken_word
        else:
            is_ns = not bool(pt["is_speech"])

        if is_ns:
            non_speech_total += 1
            if m_int >= 14.0:
                non_speech_hits += 1
            non_speech_intensities.append(m_int)

    ns_ratio = (non_speech_hits / float(non_speech_total)) if non_speech_total > 0 else 0.0
    ns_avg = (sum(non_speech_intensities) / len(non_speech_intensities)) if non_speech_intensities else 0.0
    overall_avg = (sum(all_intensities) / len(all_intensities)) if all_intensities else 0.0

    # When clear non-speech windows exist, strictly rely on harmonic persistence + rhythm in non-speech windows
    # (prevents voiced 120-200 Hz speech formants from mimicking background music).
    if non_speech_total >= 3:
        has_music = bool(
            (ns_ratio >= 0.25 and ns_avg >= 18.0)
            or (ns_ratio >= 0.20 and beat_periodicity >= 0.35)
        )
    else:
        has_music = bool(beat_periodicity >= 0.42 and overall_avg >= 25.0)

    return {
        "has_existing_music": has_music,
        "avg_music_intensity": round(overall_avg, 1),
        "beat_periodicity": round(beat_periodicity, 4),
        "estimated_bpm": estimated_bpm if has_music else 0.0,
        "music_intensity_timeline": music_tl,
    }


def _estimate_pitch_and_hnr(window: np.ndarray, sr: int) -> Tuple[float, float]:
    """
    Fast unbiased FFT autocorrelation fundamental pitch (`f0_hz` in [75..420 Hz])
    plus harmonicity proxy (`hnr_norm` in [0.0..0.995]).
    """
    if len(window) < int(sr * 0.04):
        return 0.0, 0.0
    x = window - float(np.mean(window))
    rms = float(np.sqrt(np.mean(x * x)))
    if rms < 0.006:
        return 0.0, 0.0

    min_lag = max(1, int(sr / 420.0))
    max_lag = min(len(x) // 2, int(sr / 75.0))
    if max_lag <= min_lag + 2:
        return 0.0, 0.0

    sub = x[: min(len(x), int(sr * 0.08))]
    n_sub = len(sub)
    n_fft = 1 << int(math.ceil(math.log2(n_sub * 2)))
    spec = np.fft.rfft(sub, n=n_fft)
    acf = np.fft.irfft(spec * np.conj(spec), n=n_fft)[: max_lag + 1]
    if acf[0] <= 1e-8:
        return 0.0, 0.0

    lags = np.arange(max_lag + 1, dtype=np.float32)
    overlap_weight = np.maximum(0.25, 1.0 - (lags / float(max(1, n_sub))))
    acf_norm = acf / (acf[0] * overlap_weight)
    band = acf_norm[min_lag : max_lag + 1]
    best_rel = int(np.argmax(band))
    peak_corr = float(max(0.0, min(0.995, band[best_rel])))
    if peak_corr < 0.30:
        return 0.0, peak_corr
    lag = min_lag + best_rel
    return float(sr) / float(max(1, lag)), peak_corr


def _estimate_pitch_hz(window: np.ndarray, sr: int) -> float:
    """Fast autocorrelation fundamental pitch estimator in conversational vocal band [75..420 Hz]."""
    f0, _ = _estimate_pitch_and_hnr(window, sr)
    return f0


def _compute_laughter_modulation(env_slice: np.ndarray, sr: int) -> float:
    """
    Detect rhythmic 3.2..9.5 Hz amplitude-envelope syllabic bursts ("ha-ha-ha")
    characteristic of genuine/synthetic human laughter while rejecting single step-edges.
    """
    sub_samples = max(1, int(round(0.025 * sr)))  # 25ms sub-frame = 40 Hz envelope rate
    n_sub = len(env_slice) // sub_samples
    if n_sub < 12:
        return 0.0

    trimmed = env_slice[: n_sub * sub_samples].reshape(n_sub, sub_samples)
    env = np.sqrt(np.mean(trimmed * trimmed, axis=1) + 1e-12)
    mu = float(np.mean(env))
    if mu < 0.015:
        return 0.0

    # Reject monotonic step edges (silence <-> tone transitions) by requiring multiple distinct syllable peaks
    peaks = 0
    for idx in range(1, len(env) - 1):
        if env[idx] > mu * 0.90 and env[idx] >= env[idx - 1] and env[idx] > env[idx + 1]:
            peaks += 1
    if peaks < 3:
        return 0.0

    env_ac = env - mu
    cv = float(np.std(env_ac) / (mu + 1e-6))
    if cv < 0.22:
        return 0.0

    env_rate = float(sr) / float(sub_samples)  # ~40 Hz
    mag2 = np.abs(np.fft.rfft(env_ac)) ** 2
    freqs = np.fft.rfftfreq(len(env_ac), d=1.0 / env_rate)
    total_ac_pow = float(np.sum(mag2[1:])) + 1e-9
    laugh_band = (freqs >= 3.2) & (freqs <= 10.0)
    mod_ratio = float(np.sum(mag2[laugh_band]) / total_ac_pow) if np.any(laugh_band) else 0.0

    cv_score = max(0.0, min(1.0, (cv - 0.24) / 0.45))
    return round(max(0.0, min(1.0, 0.52 * cv_score + 0.48 * mod_ratio)), 4)


def _classify_vocal_emotion(
    *,
    rms_norm: float,
    flux_norm: float,
    zcr_norm: float,
    energy_delta_db: float,
    pitch_semitone_delta: float,
    f0_hz: float,
    brightness: float,
    hnr_norm: float,
    laugh_score: float,
    wpm_val: float,
    is_speech: bool,
    excitement_score: float,
) -> Dict[str, Any]:
    """
    Classify 0.25s bin into one of:
      excited_hype | laughing | urgent_tense | calm_storytelling | deadpan
    and compute continuous vocal_valence (-1..+1) and vocal_arousal (0..100).
    """
    def _c01(v: float) -> float:
        return max(0.0, min(1.0, float(v)))

    # 1. Laughing: strong rhythmic syllable pulses + moderate-to-high energy
    s_laugh = 0.0
    if laugh_score >= 0.42 and rms_norm >= 0.16:
        s_laugh = _c01(0.68 * laugh_score + 0.20 * rms_norm + 0.12 * min(1.0, brightness * 2.0))

    # 2. Excited hype: surge in loudness / high vocal SPL + elevated pitch/cadence boost
    pitch_up = _c01(max(0.0, pitch_semitone_delta) / 5.0)
    if f0_hz >= 210.0:
        pitch_up = max(pitch_up, _c01((f0_hz - 190.0) / 110.0))
    surge = _c01((energy_delta_db + 1.0) / 10.0)
    high_spl_boost = _c01((rms_norm - 0.50) / 0.36)
    cadence_fast = _c01(wpm_val / 210.0) if wpm_val > 0 else 0.0
    s_hype = _c01(
        0.36 * rms_norm + 0.24 * max(surge, high_spl_boost) + 0.22 * pitch_up + 0.18 * max(flux_norm, cadence_fast)
        - (0.28 if laugh_score >= 0.60 else 0.0)
    )
    if rms_norm < 0.28:
        s_hype *= 0.45

    # 3. Urgent / tense: fast WPM, high brightness / harsh ZCR / onset flux without jovial laugh
    fast_urgency = _c01((wpm_val - 135.0) / 85.0) if wpm_val > 0 else 0.0
    s_tense = _c01(
        0.30 * _c01(brightness * 2.4)
        + 0.26 * zcr_norm
        + 0.24 * flux_norm
        + 0.20 * fast_urgency
        - 0.25 * laugh_score
    )
    if rms_norm < 0.18:
        s_tense *= 0.35

    # 4. Calm storytelling: active moderate conversational voice (rms <= 0.62), warm spectrum, steady pitch
    steady_pitch = 1.0 - _c01(abs(pitch_semitone_delta) / 5.0)
    calm_vol_fit = 1.0 if (0.14 <= rms_norm <= 0.62) else max(0.15, 1.0 - (rms_norm - 0.62) * 2.5)
    s_calm = _c01(
        (
            0.34 * steady_pitch
            + 0.28 * hnr_norm
            + 0.22 * (1.0 - flux_norm)
            + 0.16 * (1.0 if (is_speech and rms_norm >= 0.14) else 0.45)
        )
        * calm_vol_fit
    )
    if not is_speech and rms_norm < 0.18:
        s_calm *= 0.30

    # 5. Deadpan: quiet/flat/monotone or unexciting drone
    low_eng = 1.0 - _c01(rms_norm / 0.42)
    flat_pitch = 1.0 - _c01(abs(pitch_semitone_delta) / 2.5)
    s_deadpan = _c01(0.45 * low_eng + 0.30 * flat_pitch + 0.25 * (1.0 - flux_norm))
    if excitement_score >= 34.0 or (is_speech and rms_norm >= 0.28 and hnr_norm >= 0.45):
        s_deadpan *= 0.50

    scores = {
        "excited_hype": s_hype,
        "laughing": s_laugh,
        "urgent_tense": s_tense,
        "calm_storytelling": s_calm,
        "deadpan": s_deadpan,
    }
    dominant = max(VOCAL_EMOTIONS, key=lambda k: scores[k])

    arousal = round(
        max(0.0, min(100.0, 0.55 * excitement_score + 25.0 * rms_norm + 20.0 * max(laugh_score, surge))),
        1,
    )
    val_raw = (
        0.60 * s_laugh
        + 0.45 * s_hype
        + 0.18 * s_calm
        - 0.62 * s_tense
        - 0.15 * s_deadpan
    )
    valence = round(max(-1.0, min(1.0, val_raw)), 3)
    return {
        "vocal_emotion": dominant,
        "vocal_arousal": arousal,
        "vocal_valence": valence,
    }


def _detect_acoustic_events(
    timeline: List[Dict[str, Any]],
    silence_intervals: List[List[float]],
    step: float,
) -> List[Dict[str, Any]]:
    """
    Detect localized non-verbal & structural acoustic events from 0.25s DSP features:
      - laughter_burst
      - crowd_cheering
      - bass_music_drop
      - dramatic_pause
    """
    events: List[Dict[str, Any]] = []
    n = len(timeline)

    def _collect_runs(pred_fn, event_type: str, min_bins: int, min_conf_floor: float):
        run_start: Optional[int] = None
        confs: List[float] = []
        for idx, pt in enumerate(timeline):
            ok, conf = pred_fn(idx, pt)
            if ok:
                if run_start is None:
                    run_start = idx
                    confs = []
                confs.append(conf)
            else:
                if run_start is not None:
                    if (idx - run_start) >= min_bins:
                        t0 = float(timeline[run_start]["t"])
                        t1 = round(float(timeline[idx - 1]["t"]) + step, 3)
                        events.append({
                            "t": round(t0, 2),
                            "end": round(t1, 2),
                            "event_type": event_type,
                            "confidence": round(max(min_conf_floor, sum(confs) / max(1, len(confs))), 3),
                            "label": ACOUSTIC_EVENT_LABELS[event_type],
                        })
                    run_start = None
                    confs = []
        if run_start is not None and (n - run_start) >= min_bins:
            t0 = float(timeline[run_start]["t"])
            t1 = round(float(timeline[-1]["t"]) + step, 3)
            events.append({
                "t": round(t0, 2),
                "end": round(t1, 2),
                "event_type": event_type,
                "confidence": round(max(min_conf_floor, sum(confs) / max(1, len(confs))), 3),
                "label": ACOUSTIC_EVENT_LABELS[event_type],
            })

    # 1. Laughter burst (2+ bins or >= 0.62 modulated spike)
    _collect_runs(
        lambda idx, pt: (
            pt.get("laugh_score", 0.0) >= 0.48 and pt.get("rms", 0.0) >= 0.16,
            float(pt.get("laugh_score", 0.5)),
        ),
        "laughter_burst",
        min_bins=2,
        min_conf_floor=0.55,
    )

    # 2. Crowd cheering / roar: loud broadband unvoiced/noisy wall of sound without speech periodicity
    _collect_runs(
        lambda idx, pt: (
            pt.get("rms", 0.0) >= 0.50
            and pt.get("hnr_norm", 0.5) <= 0.42
            and pt.get("high_air_ratio", 0.0) >= 0.25
            and pt.get("laugh_score", 0.0) < 0.48,
            min(0.95, 0.55 + 0.40 * float(pt.get("rms", 0.5))),
        ),
        "crowd_cheering",
        min_bins=2,
        min_conf_floor=0.60,
    )

    # 3. Bass / music drop: sudden surge in sub-bass energy + overall dB surge after calmer lead-in
    _collect_runs(
        lambda idx, pt: (
            pt.get("sub_bass_ratio", 0.0) >= 0.35
            and pt.get("sub_bass_delta_db", 0.0) >= 4.0
            and pt.get("drop_onset_db", 0.0) >= 2.0
            and pt.get("rms", 0.0) >= 0.45,
            min(0.96, 0.58 + 0.04 * float(pt.get("sub_bass_delta_db", 4.0))),
        ),
        "bass_music_drop",
        min_bins=1,
        min_conf_floor=0.62,
    )

    # 4. Dramatic pause: abrupt silence (>= 0.45s and <= 3.5s) right after energetic delivery OR before spike
    for sil in silence_intervals:
        if len(sil) < 2:
            continue
        s0, s1 = float(sil[0]), float(sil[1])
        sil_dur = s1 - s0
        if sil_dur < 0.45 or sil_dur > 3.5:
            continue
        pre_pts = [pt for pt in timeline if (s0 - 1.5) <= float(pt["t"]) < s0]
        post_pts = [pt for pt in timeline if s1 <= float(pt["t"]) <= (s1 + 1.25)]
        pre_peak = max((float(pt.get("excitement_score", 0.0)) for pt in pre_pts), default=0.0)
        pre_rms = max((float(pt.get("rms", 0.0)) for pt in pre_pts), default=0.0)
        post_peak = max((float(pt.get("excitement_score", 0.0)) for pt in post_pts), default=0.0)
        if pre_peak >= 22.0 or post_peak >= 36.0 or (pre_rms >= 0.18 and post_peak >= 25.0):
            conf = min(0.96, 0.55 + 0.004 * max(pre_peak, post_peak))
            events.append({
                "t": round(s0, 2),
                "end": round(s1, 2),
                "event_type": "dramatic_pause",
                "confidence": round(conf, 3),
                "label": ACOUSTIC_EVENT_LABELS["dramatic_pause"],
            })

    events.sort(key=lambda e: (e["t"], -e["confidence"]))
    return events


def analyze_audio_track(
    audio_16k_path: str,
    word_segments: Optional[List[Dict[str, Any]]] = None,
    *,
    step_sec: float = 0.25,
) -> Dict[str, Any]:
    """
    Analyze acoustic loudness (EBU R128), spectral dynamics (RMS, flux, ZCR, F0 pitch Hz, HNR,
    spectral brightness, vocal emotion & non-verbal acoustic events), and faster-whisper word cadence
    into a 0.25s telemetry timeline.
    """
    if not audio_16k_path or not os.path.exists(audio_16k_path):
        raise FileNotFoundError(f"Audio source not found: {audio_16k_path}")

    step = max(0.10, float(step_sec or 0.25))
    work_dir = Path(tempfile.mkdtemp(prefix="lost_heaven_aud_"))
    try:
        wav_path = _stage_to_ascii_wav(audio_16k_path, work_dir)
        samples, sr, duration = _read_pcm_mono(wav_path)
        total_dur = max(step, float(duration))
        n_bins = max(1, int(math.ceil(total_dur / step)))

        ebur = _run_ebur128_pass(wav_path, work_dir, step, n_bins)
    finally:
        shutil.rmtree(work_dir, ignore_errors=True)

    # Pre-clean transcript word timestamps (preserve Whisper word confidence probabilities)
    clean_words: List[Dict[str, Any]] = []
    for w in word_segments or []:
        txt = str(w.get("word", "")).strip()
        if not txt or w.get("deleted") or w.get("cut_from_video"):
            continue
        ws = float(w.get("start", 0.0))
        we = max(ws + 0.02, float(w.get("end", ws + 0.2)))
        prob_raw = w.get("probability", w.get("confidence", 0.90))
        try:
            prob_val = max(0.05, min(1.0, float(0.90 if prob_raw is None else prob_raw)))
        except (TypeError, ValueError):
            prob_val = 0.90
        clean_words.append({
            "word": txt,
            "start": ws,
            "end": we,
            "probability": prob_val,
            "is_filler": bool(w.get("is_filler", False)),
        })
    clean_words.sort(key=lambda item: item["start"])

    # Compute window DSP features per 0.25s bucket
    hop_samples = max(1, int(round(step * sr)))
    win_samples = max(hop_samples, int(round(max(step * 2.0, 0.50) * sr)))
    env_samples = max(win_samples, int(round(0.75 * sr)))
    hann = np.hanning(win_samples).astype(np.float32)
    freqs = np.fft.rfftfreq(win_samples, d=1.0 / float(max(1, sr)))
    sub_bass_mask = (freqs >= 25.0) & (freqs <= 180.0)
    music_low_mask = (freqs >= 50.0) & (freqs <= 180.0)
    tonal_band_mask = (freqs >= 50.0) & (freqs <= 2000.0)
    entropy_band_mask = (freqs >= 100.0) & (freqs <= 4500.0)
    high_air_mask = (freqs >= 1200.0) & (freqs <= 6000.0)
    bright_mask = freqs >= 2000.0
    mel_fb_main, dct_main = _get_mel_dct_matrices(len(freqs), sr, n_mels=26, n_mfcc=13)
    mel_fb_small, dct_small = _get_mel_dct_matrices(257, sr, n_mels=26, n_mfcc=13)

    raw_rms = np.zeros(n_bins, dtype=np.float32)
    raw_bin_rms = np.zeros(n_bins, dtype=np.float32)
    raw_db = np.full(n_bins, -70.0, dtype=np.float32)
    raw_bin_db = np.full(n_bins, -70.0, dtype=np.float32)
    raw_flux = np.zeros(n_bins, dtype=np.float32)
    raw_zcr = np.zeros(n_bins, dtype=np.float32)
    raw_pitch = np.zeros(n_bins, dtype=np.float32)
    raw_hnr = np.zeros(n_bins, dtype=np.float32)
    raw_dual_ratio = np.zeros(n_bins, dtype=np.float32)
    raw_dual_f0 = np.zeros(n_bins, dtype=np.float32)
    raw_centroid = np.zeros(n_bins, dtype=np.float32)
    raw_rolloff = np.zeros(n_bins, dtype=np.float32)
    raw_entropy = np.zeros(n_bins, dtype=np.float32)
    raw_mfcc = np.zeros((n_bins, 13), dtype=np.float32)
    raw_brightness = np.zeros(n_bins, dtype=np.float32)
    raw_sub_bass_ratio = np.zeros(n_bins, dtype=np.float32)
    raw_sub_bass_db = np.full(n_bins, -70.0, dtype=np.float32)
    raw_low_music_ratio = np.zeros(n_bins, dtype=np.float32)
    raw_tonal_ratio = np.zeros(n_bins, dtype=np.float32)
    raw_high_air = np.zeros(n_bins, dtype=np.float32)
    raw_laugh = np.zeros(n_bins, dtype=np.float32)
    prev_norm_mag: Optional[np.ndarray] = None

    # Fast vectorized 0.25s NumPy DSP pass (batched 2D windowing + single-shot batch rfft & MFCC/ACF)
    pad_left = win_samples // 2 + env_samples // 2
    pad_right = win_samples + env_samples
    padded = np.pad(samples, (pad_left, pad_right), mode="constant").astype(np.float32, copy=False)
    win_arange = np.arange(win_samples, dtype=np.int64)[None, :]
    win_half = win_samples // 2

    bin_starts = np.minimum(len(samples), np.arange(n_bins, dtype=np.int64) * hop_samples)
    bin_ends = np.minimum(len(samples), (np.arange(n_bins, dtype=np.int64) + 1) * hop_samples)
    ent_log_denom = max(1.0, math.log2(max(2, int(np.sum(entropy_band_mask)))))

    BATCH_BINS = 512
    for b0 in range(0, n_bins, BATCH_BINS):
        b1 = min(n_bins, b0 + BATCH_BINS)
        b_idx = np.arange(b0, b1, dtype=np.int64)
        centers = np.round((b_idx + 0.5) * hop_samples).astype(np.int64) + pad_left
        starts = centers - win_half
        chunks = padded[starts[:, None] + win_arange]  # (B, win_samples)

        rms_batch = np.sqrt(np.mean(chunks * chunks, axis=1))
        raw_rms[b0:b1] = rms_batch
        raw_db[b0:b1] = np.maximum(-70.0, 20.0 * np.log10(np.maximum(1e-5, rms_batch)))

        # Vectorized ZCR
        signs = np.sign(chunks)
        signs[signs == 0] = 1
        zcr_batch = 0.5 * np.mean(np.abs(np.diff(signs, axis=1)), axis=1)

        # Vectorized batched FFT spectrum
        spec_mat = np.abs(np.fft.rfft(chunks * hann[None, :], axis=1))  # (B, F)
        spec_l2 = np.linalg.norm(spec_mat, axis=1)
        valid = (rms_batch > 5e-4) & (spec_l2 > 1e-6)

        norm_specs = np.zeros_like(spec_mat)
        norm_specs[valid] = spec_mat[valid] / spec_l2[valid, None]

        raw_zcr[b0:b1] = np.where(rms_batch > 5e-4, zcr_batch, 0.0)
        spec_sums = np.sum(spec_mat, axis=1) + 1e-8
        power_mat = spec_mat * spec_mat
        tot_pows = np.sum(power_mat, axis=1) + 1e-12

        raw_centroid[b0:b1] = np.where(valid, np.sum(spec_mat * freqs[None, :], axis=1) / spec_sums, 0.0)
        cum_pow = np.cumsum(power_mat, axis=1)
        roll_idx = np.argmax(cum_pow >= (0.85 * tot_pows[:, None]), axis=1)
        raw_rolloff[b0:b1] = np.where(valid, freqs[roll_idx], 0.0)

        # Vectorized 13-band MFCC
        mel_e = np.log(np.maximum(1e-7, power_mat @ mel_fb_main.T))
        mfcc_b = mel_e @ dct_main.T
        raw_mfcc[b0:b1] = np.where(valid[:, None], mfcc_b, 0.0)

        # Vectorized spectral entropy (vocal band) & tonal peak ratio (music bed cues)
        if np.any(entropy_band_mask):
            p_ent = power_mat[:, entropy_band_mask]
            p_ent_norm = p_ent / np.maximum(1e-12, np.sum(p_ent, axis=1, keepdims=True))
            ent_vals = -np.sum(p_ent_norm * np.log2(np.maximum(1e-12, p_ent_norm)), axis=1) / ent_log_denom
            raw_entropy[b0:b1] = np.where(valid, np.clip(ent_vals, 0.0, 1.0), 0.0)

        if np.any(tonal_band_mask):
            p_ton = power_mat[:, tonal_band_mask]
            ton_max = np.max(p_ton, axis=1)
            ton_med = np.maximum(1e-12, np.median(p_ton, axis=1))
            raw_tonal_ratio[b0:b1] = np.where(valid, np.minimum(100.0, ton_max / ton_med), 0.0)

        if np.any(music_low_mask):
            raw_low_music_ratio[b0:b1] = np.where(valid, np.sum(power_mat[:, music_low_mask], axis=1) / tot_pows, 0.0)

        # Vectorized pitch + HNR + dual-pitch cross-talk cue
        f0_b, hnr_b, dual_r_b, dual_f0_b = _estimate_pitch_hnr_dual_batch(chunks, sr)
        raw_pitch[b0:b1] = f0_b
        raw_hnr[b0:b1] = hnr_b
        raw_dual_ratio[b0:b1] = dual_r_b
        raw_dual_f0[b0:b1] = dual_f0_b

        if np.any(bright_mask):
            raw_brightness[b0:b1] = np.where(valid, np.sum(power_mat[:, bright_mask], axis=1) / tot_pows, 0.0)
        if np.any(sub_bass_mask):
            sb_pows = np.sum(power_mat[:, sub_bass_mask], axis=1)
            raw_sub_bass_ratio[b0:b1] = np.where(valid, sb_pows / tot_pows, 0.0)
            sb_rms = np.sqrt(sb_pows) / float(max(1, win_samples))
            raw_sub_bass_db[b0:b1] = np.where(valid, np.maximum(-70.0, 20.0 * np.log10(np.maximum(1e-6, sb_rms))), -70.0)
        if np.any(high_air_mask):
            raw_high_air[b0:b1] = np.where(valid, np.sum(power_mat[:, high_air_mask], axis=1) / tot_pows, 0.0)

        for local_i, b in enumerate(range(b0, b1)):
            bs0, bs1 = int(bin_starts[b]), int(bin_ends[b])
            if bs1 > bs0:
                seg = samples[bs0:bs1]
                b_rms = float(np.sqrt(np.mean(seg * seg)))
            else:
                b_rms = float(rms_batch[local_i])
            raw_bin_rms[b] = b_rms
            raw_bin_db[b] = max(-70.0, 20.0 * math.log10(max(1e-5, b_rms)))

            if bool(valid[local_i]):
                if prev_norm_mag is not None and len(prev_norm_mag) == norm_specs.shape[1]:
                    diff_pos = np.maximum(0.0, norm_specs[local_i] - prev_norm_mag)
                    raw_flux[b] = float(np.sqrt(np.sum(diff_pos * diff_pos)))
                prev_norm_mag = norm_specs[local_i]
                if float(rms_batch[local_i]) >= 0.015:
                    env_s = int(centers[local_i]) - (env_samples // 2)
                    raw_laugh[b] = _compute_laughter_modulation(padded[env_s : env_s + env_samples], sr)
            else:
                prev_norm_mag = None
                raw_flux[b] = 0.0
                raw_laugh[b] = 0.0

    # Global loudness fallback values if FFmpeg omitted Summary on ultra-short tracks
    peak_sample = float(np.max(np.abs(samples))) if len(samples) > 0 else 1e-5
    fallback_peak_db = max(-70.0, 20.0 * math.log10(max(1e-5, peak_sample)))
    active_dbs = raw_db[raw_db > -58.0]
    fallback_int_lufs = float(np.mean(active_dbs) - 0.7) if len(active_dbs) > 0 else -70.0
    fallback_lra = float(np.percentile(active_dbs, 90) - np.percentile(active_dbs, 10)) if len(active_dbs) >= 4 else 0.0

    integrated_lufs = ebur["integrated_lufs"] if (ebur["integrated_lufs"] is not None and ebur["integrated_lufs"] > -69.5) else fallback_int_lufs
    lra = ebur["lra"] if ebur["lra"] is not None else max(0.0, fallback_lra)
    true_peak_db = ebur["true_peak_db"] if ebur["true_peak_db"] is not None else fallback_peak_db

    # Rolling 10s baseline (20 bins either side @ 0.25s = 10s span)
    half_roll = max(2, int(round(5.0 / step)))
    binned_m = ebur["binned_m"]
    timeline: List[Dict[str, Any]] = []

    peak_rms = max(float(np.percentile(raw_rms, 98)) if n_bins > 0 else 0.1, 0.05)
    peak_flux = max(float(np.percentile(raw_flux, 98)) if n_bins > 0 else 0.2, 0.15)
    peak_zcr = max(float(np.percentile(raw_zcr, 98)) if n_bins > 0 else 0.25, 0.15)

    wpm_values: List[float] = []
    silence_intervals: List[List[float]] = []
    sil_start: Optional[float] = None
    hook_inflection_timestamps: List[float] = []
    vocal_counts: Dict[str, int] = {k: 0 for k in VOCAL_EMOTIONS}
    voiced_f0_list: List[float] = []

    for b in range(n_bins):
        t_cur = round(b * step, 3)
        t_mid = min(total_dur, t_cur + step * 0.5)

        w0 = max(0, b - half_roll)
        w1 = min(n_bins, b + half_roll + 1)
        win_dbs = raw_db[w0:w1]
        active_win_dbs = win_dbs[win_dbs > -58.0]
        base_db = float(np.median(active_win_dbs)) if len(active_win_dbs) > 0 else float(np.median(win_dbs))
        energy_delta_db = float(raw_db[b]) - base_db

        # Causal trailing baseline (strictly before bin b) for crisp bass/drop onset detection
        sb_w0 = max(0, b - max(6, half_roll))
        if b > sb_w0:
            sb_win = raw_sub_bass_db[sb_w0:b]
            db_win_pre = raw_db[sb_w0:b]
            base_sb_db = float(np.median(sb_win))
            base_pre_db = float(np.median(db_win_pre))
        else:
            base_sb_db = float(raw_sub_bass_db[b])
            base_pre_db = float(raw_db[b])
        sub_bass_delta_db = float(raw_sub_bass_db[b]) - base_sb_db
        drop_onset_db = float(raw_db[b]) - base_pre_db

        win_pitches = raw_pitch[w0:w1]
        voiced_pitches = win_pitches[win_pitches > 40.0]
        f0_hz = float(raw_pitch[b])
        if f0_hz > 40.0 and len(voiced_pitches) > 0:
            base_f0 = float(np.median(voiced_pitches))
            pitch_delta = max(-1.0, min(1.0, (f0_hz - base_f0) / max(30.0, base_f0)))
            pitch_semitone_delta = 12.0 * math.log2(max(40.0, f0_hz) / max(40.0, base_f0))
            voiced_f0_list.append(f0_hz)
        else:
            pitch_delta = 0.0
            pitch_semitone_delta = 0.0

        db_norm = max(0.0, min(1.0, (float(raw_db[b]) + 54.0) / 48.0))
        rel_rms = max(0.0, min(1.0, float(raw_rms[b]) / peak_rms))
        rms_norm = round(0.55 * db_norm + 0.45 * rel_rms if raw_rms[b] > 2e-4 else 0.0, 4)
        flux_norm = round(max(0.0, min(1.0, float(raw_flux[b]) / peak_flux)), 4)
        zcr_norm = round(max(0.0, min(1.0, float(raw_zcr[b]) / peak_zcr)), 4)

        m_val = binned_m[b]
        lufs_m = round(float(m_val), 2) if (m_val is not None and m_val > -69.5) else round(max(-70.0, float(raw_db[b]) - 0.691), 2)

        # Cadence fusion with word segments
        win_half_cadence = 1.25
        c_s = max(0.0, t_mid - win_half_cadence)
        c_e = min(total_dur, t_mid + win_half_cadence)
        c_span = max(0.5, c_e - c_s)

        if clean_words:
            words_in_win = [cw for cw in clean_words if cw["end"] >= c_s and cw["start"] <= c_e]
            bin_end = t_cur + step
            in_bin_words = [
                cw for cw in clean_words
                if cw["end"] >= (t_cur - 0.06) and cw["start"] <= (bin_end + 0.06)
            ]
            is_speech = len(in_bin_words) > 0 or (raw_db[b] > -34.0 and len(words_in_win) > 0)
            wpm_val = round(len(words_in_win) * (60.0 / c_span), 1)
            for cw in in_bin_words:
                if ("?" in cw["word"] or "!" in cw["word"]) and (not hook_inflection_timestamps or (t_cur - hook_inflection_timestamps[-1]) >= 1.2):
                    hook_inflection_timestamps.append(t_cur)
        else:
            is_speech = bool(raw_db[b] > -38.0 and raw_rms[b] > 0.006)
            wpm_val = 0.0

        if is_speech and wpm_val > 0:
            wpm_values.append(wpm_val)

        # Exact-bin silence / dramatic-pause interval detection (unblurred by 0.50s spectral window)
        bin_rms_rel = float(raw_bin_rms[b]) / peak_rms
        is_quiet_bin = (not is_speech) and (bin_rms_rel < 0.14 or float(raw_bin_db[b]) < -42.0)
        if is_quiet_bin:
            if sil_start is None:
                sil_start = t_cur
        elif sil_start is not None:
            if (t_cur - sil_start) >= 0.45:
                silence_intervals.append([round(sil_start, 2), round(t_cur, 2)])
            sil_start = None

        # Excitement / vocal emphasis score (0..100)
        delta_boost = max(0.0, min(1.0, (energy_delta_db + 2.0) / 14.0))
        pitch_boost = max(0.0, min(1.0, abs(pitch_delta) * 1.8))
        cadence_norm = max(0.0, min(1.0, wpm_val / 210.0)) if wpm_val > 0 else (0.35 if is_speech else 0.0)
        raw_exc = 100.0 * (
            0.46 * rms_norm
            + 0.22 * flux_norm
            + 0.16 * delta_boost
            + 0.08 * pitch_boost
            + 0.08 * cadence_norm
        )
        if rms_norm < 0.08:
            raw_exc *= 0.25
        excitement_score = round(max(0.0, min(100.0, raw_exc)), 1)

        hnr_norm = float(raw_hnr[b])
        hnr_db = round(10.0 * math.log10(max(0.05, hnr_norm) / max(0.005, 1.0 - hnr_norm)), 2) if rms_norm > 0.05 else -12.0
        hnr_db = max(-12.0, min(26.0, hnr_db))

        v_emo = _classify_vocal_emotion(
            rms_norm=rms_norm,
            flux_norm=flux_norm,
            zcr_norm=zcr_norm,
            energy_delta_db=energy_delta_db,
            pitch_semitone_delta=pitch_semitone_delta,
            f0_hz=f0_hz,
            brightness=float(raw_brightness[b]),
            hnr_norm=hnr_norm,
            laugh_score=float(raw_laugh[b]),
            wpm_val=wpm_val,
            is_speech=bool(is_speech),
            excitement_score=excitement_score,
        )
        vocal_counts[v_emo["vocal_emotion"]] += 1

        timeline.append({
            "t": t_cur,
            "rms": rms_norm,
            "flux": flux_norm,
            "zcr": zcr_norm,
            "lufs_m": lufs_m,
            "wpm": wpm_val,
            "pitch_delta": round(pitch_delta, 4),
            "f0_hz": round(f0_hz, 1),
            "pitch_semitone_delta": round(pitch_semitone_delta, 2),
            "spectral_centroid_hz": round(float(raw_centroid[b]), 1),
            "spectral_brightness": round(float(raw_brightness[b]), 4),
            "hnr_norm": round(hnr_norm, 4),
            "hnr_db": hnr_db,
            "laugh_score": round(float(raw_laugh[b]), 4),
            "sub_bass_ratio": round(float(raw_sub_bass_ratio[b]), 4),
            "sub_bass_delta_db": round(sub_bass_delta_db, 2),
            "drop_onset_db": round(drop_onset_db, 2),
            "high_air_ratio": round(float(raw_high_air[b]), 4),
            "energy_delta_db": round(energy_delta_db, 2),
            "is_speech": bool(is_speech),
            "excitement_score": excitement_score,
            "vocal_emotion": v_emo["vocal_emotion"],
            "vocal_arousal": v_emo["vocal_arousal"],
            "vocal_valence": v_emo["vocal_valence"],
        })

    if sil_start is not None and (round(n_bins * step, 2) - sil_start) >= 0.45:
        silence_intervals.append([round(sil_start, 2), round(min(total_dur, n_bins * step), 2)])

    # Detect non-verbal & structural acoustic events and annotate matching bins
    acoustic_events = _detect_acoustic_events(timeline, silence_intervals, step)
    for pt in timeline:
        t_p = float(pt["t"])
        matched_ev = next(
            (ev for ev in acoustic_events if ev["t"] <= t_p + 0.05 and t_p <= ev["end"] + 0.05),
            None,
        )
        pt["acoustic_event"] = matched_ev["event_type"] if matched_ev else None

    # Non-overlapping excitement peaks (>= 1.5s apart)
    sorted_peaks = sorted(
        [pt for pt in timeline if pt["excitement_score"] >= 35.0],
        key=lambda pt: pt["excitement_score"],
        reverse=True,
    )
    peak_timestamps: List[float] = []
    for pt in sorted_peaks:
        if all(abs(pt["t"] - prev_t) >= 1.5 for prev_t in peak_timestamps):
            peak_timestamps.append(pt["t"])
            if len(peak_timestamps) >= 12:
                break
    peak_timestamps.sort()

    if clean_words and total_dur > 0:
        first_w = clean_words[0]["start"]
        last_w = clean_words[-1]["end"]
        spoken_span = max(1.0, last_w - first_w)
        avg_wpm = round(len(clean_words) / (spoken_span / 60.0), 1)
    elif wpm_values:
        avg_wpm = round(float(sum(wpm_values) / len(wpm_values)), 1)
    else:
        avg_wpm = 0.0

    tot_bins = max(1, len(timeline))
    vocal_dist = {k: round(vocal_counts[k] / float(tot_bins), 4) for k in VOCAL_EMOTIONS}
    dominant_vocal = max(VOCAL_EMOTIONS, key=lambda k: vocal_counts[k])
    avg_pitch_hz = round(sum(voiced_f0_list) / len(voiced_f0_list), 1) if voiced_f0_list else 0.0
    if len(voiced_f0_list) >= 4:
        p10, p90 = np.percentile(voiced_f0_list, [10, 90])
        pitch_range_semi = round(12.0 * math.log2(max(40.0, float(p90)) / max(40.0, float(p10))), 2)
    else:
        pitch_range_semi = 0.0
    event_counts: Dict[str, int] = {k: 0 for k in ACOUSTIC_EVENT_LABELS}
    for ev in acoustic_events:
        etype = ev.get("event_type", "")
        if etype in event_counts:
            event_counts[etype] += 1

    speaker_diarization = _analyze_speaker_diarization(
        samples=samples,
        sr=sr,
        step=step,
        total_dur=total_dur,
        timeline=timeline,
        clean_words=clean_words,
        raw_mfcc=raw_mfcc,
        raw_centroid=raw_centroid,
        raw_rolloff=raw_rolloff,
        raw_pitch=raw_pitch,
        raw_entropy=raw_entropy,
        raw_dual_ratio=raw_dual_ratio,
        raw_dual_f0=raw_dual_f0,
        mel_fb_small=mel_fb_small,
        dct_small=dct_small,
    )
    speech_fluency = _analyze_speech_fluency(
        clean_words=clean_words,
        timeline=timeline,
        step=step,
        total_dur=total_dur,
    )
    music_bed_analysis = _analyze_music_bed(
        samples=samples,
        sr=sr,
        step=step,
        timeline=timeline,
        clean_words=clean_words,
        raw_low_music_ratio=raw_low_music_ratio,
        raw_tonal_ratio=raw_tonal_ratio,
        raw_bin_db=raw_bin_db,
        raw_bin_rms=raw_bin_rms,
    )

    return {
        "summary": {
            "integrated_lufs": round(float(integrated_lufs), 2),
            "lra": round(float(lra), 2),
            "true_peak_db": round(float(true_peak_db), 2),
            "avg_wpm": float(avg_wpm),
            "peak_excitement_timestamps": peak_timestamps,
            "silence_intervals": silence_intervals,
            "hook_inflection_timestamps": hook_inflection_timestamps,
            "dominant_vocal_emotion": dominant_vocal,
            "vocal_emotion_distribution": vocal_dist,
            "avg_pitch_hz": avg_pitch_hz,
            "pitch_range_semitones": pitch_range_semi,
            "acoustic_event_counts": event_counts,
            "acoustic_events": acoustic_events,
            "speaker_count": speaker_diarization["speaker_count"],
            "dominance_ratios": speaker_diarization["dominance_ratios"],
            "crosstalk_count": speaker_diarization["crosstalk_count"],
            "avg_fluency_score": speech_fluency["avg_fluency_score"],
            "filler_count": speech_fluency["filler_count"],
            "clean_hook_count": len(speech_fluency["clean_hook_candidates"]),
            "has_existing_music": music_bed_analysis["has_existing_music"],
            "avg_music_intensity": music_bed_analysis["avg_music_intensity"],
            "beat_periodicity": music_bed_analysis["beat_periodicity"],
            "duration": round(float(total_dur), 3),
            "step_sec": step,
        },
        "acoustic_events": acoustic_events,
        "speaker_diarization": speaker_diarization,
        "speech_fluency": speech_fluency,
        "music_bed_analysis": music_bed_analysis,
        "timeline": timeline,
    }
