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

    # Pre-clean transcript word timestamps
    clean_words: List[Dict[str, Any]] = []
    for w in word_segments or []:
        txt = str(w.get("word", "")).strip()
        if not txt or w.get("deleted") or w.get("cut_from_video"):
            continue
        ws = float(w.get("start", 0.0))
        we = max(ws + 0.02, float(w.get("end", ws + 0.2)))
        clean_words.append({"word": txt, "start": ws, "end": we})
    clean_words.sort(key=lambda item: item["start"])

    # Compute window DSP features per 0.25s bucket
    hop_samples = max(1, int(round(step * sr)))
    win_samples = max(hop_samples, int(round(max(step * 2.0, 0.50) * sr)))
    env_samples = max(win_samples, int(round(0.75 * sr)))
    hann = np.hanning(win_samples).astype(np.float32)
    freqs = np.fft.rfftfreq(win_samples, d=1.0 / float(max(1, sr)))
    sub_bass_mask = (freqs >= 25.0) & (freqs <= 180.0)
    high_air_mask = (freqs >= 1200.0) & (freqs <= 6000.0)
    bright_mask = freqs >= 2000.0

    raw_rms = np.zeros(n_bins, dtype=np.float32)
    raw_bin_rms = np.zeros(n_bins, dtype=np.float32)
    raw_db = np.full(n_bins, -70.0, dtype=np.float32)
    raw_bin_db = np.full(n_bins, -70.0, dtype=np.float32)
    raw_flux = np.zeros(n_bins, dtype=np.float32)
    raw_zcr = np.zeros(n_bins, dtype=np.float32)
    raw_pitch = np.zeros(n_bins, dtype=np.float32)
    raw_hnr = np.zeros(n_bins, dtype=np.float32)
    raw_centroid = np.zeros(n_bins, dtype=np.float32)
    raw_brightness = np.zeros(n_bins, dtype=np.float32)
    raw_sub_bass_ratio = np.zeros(n_bins, dtype=np.float32)
    raw_sub_bass_db = np.full(n_bins, -70.0, dtype=np.float32)
    raw_high_air = np.zeros(n_bins, dtype=np.float32)
    raw_laugh = np.zeros(n_bins, dtype=np.float32)
    prev_norm_mag: Optional[np.ndarray] = None

    # Fast vectorized 0.25s NumPy DSP pass (batched 2D windowing + single-shot batch rfft)
    pad_left = win_samples // 2 + env_samples // 2
    pad_right = win_samples + env_samples
    padded = np.pad(samples, (pad_left, pad_right), mode="constant").astype(np.float32, copy=False)
    win_arange = np.arange(win_samples, dtype=np.int64)[None, :]
    win_half = win_samples // 2

    bin_starts = np.minimum(len(samples), np.arange(n_bins, dtype=np.int64) * hop_samples)
    bin_ends = np.minimum(len(samples), (np.arange(n_bins, dtype=np.int64) + 1) * hop_samples)

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
                f0_hz, hnr_norm = _estimate_pitch_and_hnr(chunks[local_i], sr)
                raw_pitch[b] = f0_hz
                raw_hnr[b] = hnr_norm
                env_s = int(centers[local_i]) - (env_samples // 2)
                raw_laugh[b] = _compute_laughter_modulation(padded[env_s : env_s + env_samples], sr)
            else:
                prev_norm_mag = None
                raw_flux[b] = 0.0
                raw_pitch[b] = 0.0
                raw_hnr[b] = 0.0
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
            "duration": round(float(total_dur), 3),
            "step_sec": step,
        },
        "acoustic_events": acoustic_events,
        "timeline": timeline,
    }
