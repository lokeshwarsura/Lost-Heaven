import logging
import math
import shutil
import subprocess
import tempfile
import wave
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import numpy as np

from app.services.proxy import get_ffmpeg_cmd

logger = logging.getLogger("lost_heaven.diarizer")

SPEAKER_PALETTE: List[str] = ["#C6F32F", "#38BDF8", "#F472B6", "#FBBF24"]


def _load_mono_16k(media_path: str, start_sec: float = 0.0, duration_sec: float = 0.0) -> Tuple[np.ndarray, int]:
    """Load 16kHz mono float32 samples from WAV directly or extract cleanly via ASCII temp dir."""
    p = Path(media_path)
    sr = 16000
    if p.suffix.lower() == ".wav" and p.exists():
        try:
            with wave.open(str(p), "rb") as wf:
                wav_sr = wf.getframerate()
                n_ch = wf.getnchannels()
                total_frames = wf.getnframes()
                s_frame = max(0, int(round(start_sec * wav_sr)))
                if duration_sec > 0:
                    n_frames = max(0, min(total_frames - s_frame, int(round(duration_sec * wav_sr))))
                else:
                    n_frames = max(0, total_frames - s_frame)
                if n_frames <= 0:
                    return np.zeros(0, dtype=np.float32), wav_sr
                wf.setpos(s_frame)
                raw = wf.readframes(n_frames)
            data = np.frombuffer(raw, dtype=np.int16).astype(np.float32) / 32768.0
            if n_ch > 1:
                data = data.reshape(-1, n_ch).mean(axis=1)
            if wav_sr != sr and data.size > 0:
                idx = np.linspace(0, data.size - 1, int(round(data.size * sr / float(wav_sr))))
                data = np.interp(idx, np.arange(data.size), data).astype(np.float32)
            return data, sr
        except Exception as exc:
            logger.debug(f"Direct WAV read fallback to FFmpeg: {exc}")

    if not p.exists():
        return np.zeros(0, dtype=np.float32), sr

    exec_dir = Path(tempfile.mkdtemp(prefix="lost_heaven_diar_"))
    try:
        out_wav = exec_dir / "seg_16k.wav"
        ffmpeg = get_ffmpeg_cmd()
        cmd = [ffmpeg, "-y"]
        if start_sec > 0.0:
            cmd.extend(["-ss", f"{start_sec:.3f}"])
        if duration_sec > 0.0:
            cmd.extend(["-t", f"{duration_sec:.3f}"])
        cmd.extend([
            "-i", str(p.resolve()),
            "-vn", "-ac", "1", "-ar", str(sr), "-c:a", "pcm_s16le",
            out_wav.name,
        ])
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=60, cwd=str(exec_dir))
        if proc.returncode != 0 or not out_wav.exists():
            return np.zeros(0, dtype=np.float32), sr
        with wave.open(str(out_wav), "rb") as wf:
            raw = wf.readframes(wf.getnframes())
        return np.frombuffer(raw, dtype=np.int16).astype(np.float32) / 32768.0, sr
    finally:
        shutil.rmtree(exec_dir, ignore_errors=True)


def _hz_to_mel(hz: np.ndarray) -> np.ndarray:
    return 2595.0 * np.log10(1.0 + hz / 700.0)


def _mel_to_hz(mel: np.ndarray) -> np.ndarray:
    return 700.0 * (10.0 ** (mel / 2595.0) - 1.0)


def _mel_filterbank(n_fft: int, sr: int, n_mels: int = 12, fmin: float = 100.0, fmax: float = 6800.0) -> np.ndarray:
    n_freqs = n_fft // 2 + 1
    fft_freqs = np.linspace(0.0, sr / 2.0, n_freqs)
    mels = np.linspace(_hz_to_mel(np.array([fmin]))[0], _hz_to_mel(np.array([min(fmax, sr / 2.0)]))[0], n_mels + 2)
    hz_pts = _mel_to_hz(mels)
    fb = np.zeros((n_mels, n_freqs), dtype=np.float32)
    for m in range(n_mels):
        left, center, right = hz_pts[m], hz_pts[m + 1], hz_pts[m + 2]
        if center > left:
            fb[m] += np.maximum(0.0, np.minimum(1.0, (fft_freqs - left) / (center - left)))
        if right > center:
            slope = (right - fft_freqs) / (right - center)
            fb[m] = np.minimum(fb[m], np.maximum(0.0, slope))
    return fb


_MEL_FB = _mel_filterbank(n_fft=512, sr=16000, n_mels=12)
_DCT_MAT = np.cos(
    np.pi * np.arange(8)[:, None] * (np.arange(12)[None, :] + 0.5) / 12.0
).astype(np.float32) * math.sqrt(2.0 / 12.0)


def _extract_word_embedding(segment: np.ndarray, sr: int = 16000, face_cx: float = 0.5) -> np.ndarray:
    """Extract pure-numpy 12-dim acoustic + face embedding (8 MFCCs, centroid, ZCR, F0 pitch, face_cx)."""
    if segment.size < 320:
        segment = np.pad(segment, (0, max(0, 320 - segment.size)))
    win_len = 400
    hop = 160
    hann = np.hanning(win_len).astype(np.float32)
    freqs = np.linspace(0.0, sr / 2.0, 257).astype(np.float32)

    mfccs: List[np.ndarray] = []
    centroids: List[float] = []
    zcrs: List[float] = []
    pitches: List[float] = []

    min_lag = max(20, int(sr / 360))
    max_lag = min(win_len - 10, int(sr / 75))

    for start in range(0, max(1, segment.size - win_len + 1), hop):
        frame = segment[start:start + win_len]
        if frame.size < win_len:
            frame = np.pad(frame, (0, win_len - frame.size))
        energy = float(np.sqrt(np.mean(frame * frame)))
        windowed = frame * hann
        spec = np.abs(np.fft.rfft(windowed, n=512)) + 1e-8
        mel_e = np.log(np.maximum(1e-6, _MEL_FB @ (spec * spec)))
        mfcc = _DCT_MAT @ mel_e
        mfccs.append(mfcc)

        spec_sum = float(np.sum(spec))
        centroids.append(float(np.sum(freqs * spec) / max(1e-6, spec_sum)) / 4000.0)
        zcrs.append(float(np.mean(np.abs(np.diff(np.signbit(frame).astype(np.int8))))))

        if energy > 0.015:
            centered = frame - float(np.mean(frame))
            ac = np.correlate(centered, centered, mode="full")[win_len - 1:]
            denom = float(ac[0]) if ac[0] > 1e-8 else 1.0
            if max_lag > min_lag + 2:
                sub = ac[min_lag:max_lag] / denom
                best_rel = int(np.argmax(sub))
                if sub[best_rel] > 0.25:
                    pitches.append(sr / float(min_lag + best_rel))

    mean_mfcc = np.mean(np.stack(mfccs, axis=0), axis=0) if mfccs else np.zeros(8, dtype=np.float32)
    mean_cent = float(np.mean(centroids)) if centroids else 0.3
    mean_zcr = float(np.mean(zcrs)) if zcrs else 0.1
    med_f0 = (float(np.median(pitches)) / 250.0) if pitches else 0.6

    return np.concatenate([
        mean_mfcc.astype(np.float32),
        np.array([mean_cent, mean_zcr, med_f0, float(face_cx)], dtype=np.float32),
    ])


def _kmeans_deterministic(X: np.ndarray, k: int, max_iter: int = 25) -> Tuple[np.ndarray, float]:
    n = X.shape[0]
    if n <= k:
        return np.arange(n, dtype=np.int32) % k, 0.0

    # Farthest-point deterministic initialization
    centers = [X[0].copy()]
    for _ in range(1, k):
        dists = np.min(np.stack([np.sum((X - c) ** 2, axis=1) for c in centers], axis=0), axis=0)
        next_idx = int(np.argmax(dists))
        centers.append(X[next_idx].copy())
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

    # Mean silhouette score
    uniq = np.unique(labels)
    if len(uniq) < 2:
        return labels, 0.0
    dist_mat = np.sqrt(np.maximum(0.0, np.sum((X[:, None, :] - X[None, :, :]) ** 2, axis=2)))
    sils: List[float] = []
    for i in range(n):
        own = labels[i]
        same_mask = (labels == own)
        same_mask[i] = False
        a = float(np.mean(dist_mat[i, same_mask])) if np.any(same_mask) else 0.0
        b = float("inf")
        for other in uniq:
            if other == own:
                continue
            other_mask = (labels == other)
            if np.any(other_mask):
                b = min(b, float(np.mean(dist_mat[i, other_mask])))
        if b == float("inf"):
            sils.append(0.0)
        else:
            sils.append((b - a) / max(1e-6, max(a, b)))
    return labels, float(np.mean(sils))


def diarize_clip_speakers(
    audio_or_video_path: str,
    words: List[Dict[str, Any]],
    face_track: Optional[Dict[str, Any]] = None,
    clip_start_time: float = 0.0,
    num_speakers: int = 0,
) -> Dict[str, Any]:
    """
    Pure-numpy local speaker diarization on word timestamps + optional active face `cx`.
    Assigns sequential `S1..SK` speaker IDs sorted by first appearance, smooths 1-word flips,
    and returns `{speakers, words, speaker_count}`.
    """
    word_list = [dict(w) for w in (words or []) if isinstance(w, dict)]
    if not word_list:
        return {"speakers": {}, "words": [], "speaker_count": 0}

    # Resolve audio coordinate system relative to media start
    max_end = max(float(w.get("end", 0.0)) for w in word_list)
    min_start = min(float(w.get("start", 0.0)) for w in word_list)
    is_abs = clip_start_time > 0.0 and min_start >= max(0.0, clip_start_time - 0.5)
    abs_s0 = min_start if is_abs else (clip_start_time + min_start)
    abs_e0 = (max_end if is_abs else (clip_start_time + max_end)) + 0.5

    samples, sr = _load_mono_16k(
        audio_or_video_path,
        start_sec=0.0 if audio_or_video_path.lower().endswith(".wav") else max(0.0, abs_s0),
        duration_sec=0.0 if audio_or_video_path.lower().endswith(".wav") else max(1.0, abs_e0 - abs_s0),
    )
    sample_base_t = 0.0 if audio_or_video_path.lower().endswith(".wav") else max(0.0, abs_s0)

    keyframes = (face_track or {}).get("keyframes") or []
    feats: List[np.ndarray] = []
    valid_indices: List[int] = []

    for idx, w in enumerate(word_list):
        if w.get("cut_from_video") or w.get("deleted") or not str(w.get("word", "")).strip():
            continue
        ws = float(w.get("start", 0.0))
        we = float(w.get("end", ws + 0.2))
        media_s = ws if is_abs else (clip_start_time + ws)
        media_e = max(media_s + 0.08, we if is_abs else (clip_start_time + we))
        i0 = max(0, int(round((media_s - sample_base_t) * sr)))
        i1 = max(i0 + 1, min(samples.size, int(round((media_e - sample_base_t) * sr))))
        seg = samples[i0:i1] if samples.size > 0 else np.zeros(320, dtype=np.float32)

        rel_mid = max(0.0, 0.5 * (media_s + media_e) - clip_start_time)
        fcx = 0.5
        if keyframes:
            closest = min(keyframes, key=lambda kf: abs(float(kf.get("t", 0.0)) - rel_mid))
            fcx = float(closest.get("cx", 0.5))

        feats.append(_extract_word_embedding(seg, sr=sr, face_cx=fcx))
        valid_indices.append(idx)

    if len(valid_indices) < 4:
        for w in word_list:
            w["speaker_id"] = "S1"
        return {
            "speakers": {
                "S1": {"id": "S1", "name": "Speaker 1", "color": SPEAKER_PALETTE[0], "word_count": len(valid_indices)}
            },
            "words": word_list,
            "speaker_count": 1,
        }

    X = np.stack(feats, axis=0)
    mu = np.mean(X, axis=0)
    std = np.maximum(1e-3, np.std(X, axis=0))
    Xn = (X - mu) / std
    # Amplify pitch & low MFCCs which best separate vocal tract timbre
    Xn[:, :4] *= 1.35
    Xn[:, 10] *= 1.45  # F0 pitch

    if num_speakers >= 2:
        k_best = min(4, num_speakers, max(2, len(valid_indices) // 2))
        best_labels, _ = _kmeans_deterministic(Xn, k_best)
    else:
        max_k = min(4, max(2, len(valid_indices) // 4))
        best_k = 1
        best_sil = 0.0
        best_labels = np.zeros(len(valid_indices), dtype=np.int32)
        for k in range(2, max_k + 1):
            lbls, sil = _kmeans_deterministic(Xn, k)
            counts = [int(np.sum(lbls == c)) for c in range(k)]
            if min(counts) < max(2, len(valid_indices) // 10):
                continue
            if sil > best_sil:
                best_sil = sil
                best_k = k
                best_labels = lbls
        # Require meaningful acoustic/spatial cluster separation to split into multiple speakers
        if best_k == 1 or best_sil < 0.14:
            best_labels = np.zeros(len(valid_indices), dtype=np.int32)

    # Smooth isolated single-word glitches across small inter-word gaps (< 0.30s)
    smoothed = best_labels.copy()
    for i in range(1, len(smoothed) - 1):
        if smoothed[i - 1] == smoothed[i + 1] and smoothed[i] != smoothed[i - 1]:
            w_prev = word_list[valid_indices[i - 1]]
            w_cur = word_list[valid_indices[i]]
            w_next = word_list[valid_indices[i + 1]]
            gap1 = float(w_cur.get("start", 0.0)) - float(w_prev.get("end", 0.0))
            gap2 = float(w_next.get("start", 0.0)) - float(w_cur.get("end", 0.0))
            if gap1 < 0.35 and gap2 < 0.35:
                smoothed[i] = smoothed[i - 1]

    # Map clusters sequentially in chronological order -> S1, S2, ...
    cluster_to_sid: Dict[int, str] = {}
    for lbl in smoothed.tolist():
        if lbl not in cluster_to_sid:
            cluster_to_sid[lbl] = f"S{len(cluster_to_sid) + 1}"

    counts_by_sid: Dict[str, int] = {}
    for v_pos, w_idx in enumerate(valid_indices):
        sid = cluster_to_sid[int(smoothed[v_pos])]
        word_list[w_idx]["speaker_id"] = sid
        counts_by_sid[sid] = counts_by_sid.get(sid, 0) + 1

    # Propagate nearest speaker_id to any skipped/deleted words
    last_sid = word_list[valid_indices[0]].get("speaker_id", "S1")
    for w in word_list:
        if w.get("speaker_id"):
            last_sid = str(w["speaker_id"])
        else:
            w["speaker_id"] = last_sid

    speakers_meta: Dict[str, Dict[str, Any]] = {}
    ordered_sids = sorted(counts_by_sid.keys(), key=lambda x: int(x[1:]) if x[1:].isdigit() else 99)
    for i, sid in enumerate(ordered_sids):
        speakers_meta[sid] = {
            "id": sid,
            "name": f"Speaker {i + 1}",
            "color": SPEAKER_PALETTE[i % len(SPEAKER_PALETTE)],
            "word_count": counts_by_sid[sid],
        }

    return {
        "speakers": speakers_meta,
        "words": word_list,
        "speaker_count": len(speakers_meta),
    }


def detect_speaker_lower_third_turns(
    words: List[Dict[str, Any]],
    speakers: Optional[Dict[str, Dict[str, Any]]],
    display_sec: float = 2.5,
) -> List[Dict[str, Any]]:
    """
    Build timed lower-third nameplate display intervals (`<= 2.5s` each) at speaker switches.
    `words` should already have clip-relative timestamps.
    """
    if not words:
        return []
    spk_map = speakers or {}
    spoken = [w for w in words if not w.get("cut_from_video") and not w.get("deleted") and str(w.get("word", "")).strip()]
    if not spoken:
        return []

    turns: List[Dict[str, Any]] = []
    cur_sid: Optional[str] = None
    for w in spoken:
        sid = str(w.get("speaker_id") or "").strip()
        if not sid:
            continue
        if sid != cur_sid:
            ws = float(w.get("start", 0.0))
            meta = spk_map.get(sid) or {}
            name = str(meta.get("name") or f"Speaker {sid.lstrip('S') or '1'}").strip()
            color = str(meta.get("color") or SPEAKER_PALETTE[0])
            turns.append({
                "speaker_id": sid,
                "name": name,
                "color": color,
                "start": round(max(0.0, ws), 3),
                "end": round(max(0.1, ws + display_sec), 3),
            })
            cur_sid = sid

    # Clamp overlapping turn cards so the previous nameplate vanishes right before next speaker appears
    for i in range(len(turns) - 1):
        turns[i]["end"] = round(min(turns[i]["end"], max(turns[i]["start"] + 0.35, turns[i + 1]["start"] - 0.05)), 3)
    return turns
