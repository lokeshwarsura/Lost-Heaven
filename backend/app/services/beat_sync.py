"""
Lost Heaven — Beat-Synced Auto-Cuts & Live Audio Spectrum Visualizer Engine (Phase 2).
Pure-NumPy DSP (STFT spectral flux onset detection, BPM estimation, 24-band spectrum)
plus Pillow glassmorphic live spectrum bar rendering. Zero external scipy/librosa dependency.
"""

from __future__ import annotations

import logging
import os
import shutil
import subprocess
import tempfile
from typing import Any, Dict, List, Optional, Tuple

import numpy as np
from PIL import Image, ImageDraw

from app.services.proxy import get_ffmpeg_cmd

logger = logging.getLogger(__name__)


def _parse_hex_rgb(hex_str: Optional[str], default: Tuple[int, int, int] = (198, 243, 47)) -> Tuple[int, int, int]:
    raw = str(hex_str or "").strip().lstrip("#")
    if len(raw) == 3:
        raw = "".join(ch * 2 for ch in raw)
    if len(raw) >= 6:
        try:
            return (int(raw[0:2], 16), int(raw[2:4], 16), int(raw[4:6], 16))
        except ValueError:
            pass
    return default


def _load_mono_pcm(
    media_path: str,
    start_sec: float = 0.0,
    duration_sec: Optional[float] = None,
    sample_rate: int = 22050,
) -> np.ndarray:
    """Decode mono float32 PCM samples from media_path inside an ASCII-safe temp dir."""
    if not media_path or not os.path.exists(media_path):
        return np.zeros(0, dtype=np.float32)

    ffmpeg_bin = get_ffmpeg_cmd() or "ffmpeg"
    exec_dir = tempfile.mkdtemp(prefix="lost_heaven_beat_")
    try:
        ext = os.path.splitext(media_path)[1] or ".mp4"
        staged_in = os.path.join(exec_dir, f"src{ext}")
        raw_pcm = os.path.join(exec_dir, "mono.s16le")
        try:
            shutil.copy2(media_path, staged_in)
            in_target = staged_in
        except OSError:
            in_target = media_path

        cmd: List[str] = [ffmpeg_bin, "-v", "error", "-y"]
        if start_sec and start_sec > 0.001:
            cmd += ["-ss", f"{float(start_sec):.3f}"]
        cmd += ["-i", in_target]
        if duration_sec and duration_sec > 0.05:
            cmd += ["-t", f"{float(duration_sec):.3f}"]
        cmd += [
            "-vn",
            "-ac", "1",
            "-ar", str(int(sample_rate)),
            "-f", "s16le",
            "-acodec", "pcm_s16le",
            raw_pcm,
        ]
        res = subprocess.run(cmd, cwd=exec_dir, capture_output=True, text=True)
        if res.returncode != 0 or not os.path.exists(raw_pcm):
            return np.zeros(0, dtype=np.float32)
        raw_bytes = open(raw_pcm, "rb").read()
        if len(raw_bytes) < 4:
            return np.zeros(0, dtype=np.float32)
        samples = np.frombuffer(raw_bytes, dtype=np.int16).astype(np.float32) / 32768.0
        return samples
    except Exception as exc:
        logger.debug("PCM extraction failed for beat_sync: %s", exc)
        return np.zeros(0, dtype=np.float32)
    finally:
        shutil.rmtree(exec_dir, ignore_errors=True)


def analyze_pcm_beats_and_spectrum(
    samples: np.ndarray,
    sample_rate: int = 22050,
    spectrum_fps: int = 10,
    num_bands: int = 24,
) -> Dict[str, Any]:
    """Pure-NumPy spectral-flux onset/BPM detector and log-spaced spectrum frame extractor."""
    total_samples = int(samples.shape[0]) if samples is not None else 0
    if total_samples < 512:
        return {
            "bpm": 0.0,
            "beats": [],
            "beat_strengths": [],
            "spectrum_fps": spectrum_fps,
            "spectrum_frames": [],
            "duration_sec": float(total_samples) / float(max(1, sample_rate)),
        }

    dur_sec = float(total_samples) / float(sample_rate)
    rms_all = float(np.sqrt(np.mean(samples ** 2)))
    if rms_all < 1e-4:
        empty_frames = int(max(1, round(dur_sec * spectrum_fps)))
        return {
            "bpm": 0.0,
            "beats": [],
            "beat_strengths": [],
            "spectrum_fps": spectrum_fps,
            "spectrum_frames": [[0.04] * num_bands for _ in range(empty_frames)],
            "duration_sec": round(dur_sec, 3),
        }

    n_fft = 1024
    hop = 512
    window = np.hanning(n_fft).astype(np.float32)
    n_frames = max(1, 1 + (total_samples - n_fft) // hop) if total_samples >= n_fft else 1

    # Frame matrix with zero padding if needed
    padded = samples
    if total_samples < n_fft:
        padded = np.pad(samples, (0, n_fft - total_samples))
    frame_starts = np.arange(n_frames) * hop
    idx_matrix = frame_starts[:, None] + np.arange(n_fft)[None, :]
    frames = padded[np.clip(idx_matrix, 0, len(padded) - 1)] * window[None, :]
    spec = np.abs(np.fft.rfft(frames, axis=1))  # shape (n_frames, 513)

    freqs = np.fft.rfftfreq(n_fft, d=1.0 / float(sample_rate))
    # Weight kick/snare punch (<260Hz) more heavily alongside broadband attack
    low_mask = freqs <= 260.0
    if n_frames >= 2:
        diff = np.maximum(0.0, spec[1:] - spec[:-1])
        flux_full = diff.sum(axis=1)
        flux_low = diff[:, low_mask].sum(axis=1) * 1.8 if np.any(low_mask) else 0.0
        flux = np.concatenate([[0.0], flux_full + flux_low])
    else:
        flux = np.zeros(1, dtype=np.float32)

    # Normalize spectral flux
    flux_max = float(np.max(flux)) if flux.size else 0.0
    if flux_max > 1e-6:
        flux_norm = flux / flux_max
    else:
        flux_norm = flux

    # Adaptive peak picking
    fps_flux = float(sample_rate) / float(hop)
    win_half = max(3, int(round(0.32 * fps_flux)))
    min_gap_frames = max(2, int(round(0.26 * fps_flux)))  # cap <= 230 BPM

    beats: List[float] = []
    beat_strengths: List[float] = []
    last_peak_idx = -min_gap_frames
    global_floor = max(0.18, float(np.percentile(flux_norm, 65)) * 1.15) if flux_norm.size else 0.2

    for i in range(1, len(flux_norm) - 1):
        val = float(flux_norm[i])
        if val < global_floor:
            continue
        if val < flux_norm[i - 1] or val < flux_norm[i + 1]:
            continue
        lo = max(0, i - win_half)
        hi = min(len(flux_norm), i + win_half + 1)
        local = flux_norm[lo:hi]
        thresh = float(np.mean(local) + 0.72 * np.std(local))
        if val >= thresh and (i - last_peak_idx) >= min_gap_frames:
            t_beat = round(float(i * hop) / float(sample_rate), 3)
            beats.append(t_beat)
            beat_strengths.append(round(min(1.0, val), 3))
            last_peak_idx = i

    # Estimate BPM via autocorrelation of flux (60..190 BPM) or median beat interval
    bpm = 0.0
    if len(beats) >= 2:
        ibis = np.diff(np.array(beats, dtype=np.float32))
        valid_ibis = ibis[(ibis >= 0.28) & (ibis <= 1.20)]
        if valid_ibis.size > 0:
            med_ibi = float(np.median(valid_ibis))
            bpm = round(60.0 / max(0.25, med_ibi), 1)
    if bpm <= 0.0 and len(flux_norm) >= int(fps_flux * 2.0):
        centered = flux_norm - float(np.mean(flux_norm))
        ac = np.correlate(centered, centered, mode="full")[len(centered) - 1:]
        min_lag = max(2, int(round(fps_flux * 60.0 / 190.0)))
        max_lag = min(len(ac) - 1, int(round(fps_flux * 60.0 / 60.0)))
        if max_lag > min_lag:
            best_lag = min_lag + int(np.argmax(ac[min_lag:max_lag + 1]))
            if ac[best_lag] > 0:
                bpm = round(60.0 * fps_flux / float(best_lag), 1)

    # Extract log-spaced 24-band spectrum timeline at `spectrum_fps`
    edges = np.geomspace(80.0, min(10000.0, float(sample_rate) * 0.48), num_bands + 1)
    band_masks = []
    for b in range(num_bands):
        mask = (freqs >= edges[b]) & (freqs < edges[b + 1])
        if not np.any(mask):
            nearest = int(np.argmin(np.abs(freqs - 0.5 * (edges[b] + edges[b + 1]))))
            mask = np.zeros_like(freqs, dtype=bool)
            mask[nearest] = True
        band_masks.append(mask)

    band_energy = np.zeros((n_frames, num_bands), dtype=np.float32)
    for b, mask in enumerate(band_masks):
        band_energy[:, b] = np.sqrt(np.mean(spec[:, mask] ** 2, axis=1) + 1e-9)

    band_energy = np.log1p(band_energy * 2.4)
    p95 = float(np.percentile(band_energy, 97)) if band_energy.size else 1.0
    scale = max(1e-4, p95)
    band_norm = np.clip(band_energy / scale, 0.04, 1.0)

    target_count = max(1, int(np.ceil(dur_sec * float(spectrum_fps))))
    spectrum_frames: List[List[float]] = []
    prev_bars: Optional[np.ndarray] = None
    for s_idx in range(target_count):
        t_sec = float(s_idx) / float(spectrum_fps)
        f_idx = min(n_frames - 1, max(0, int(round(t_sec * fps_flux))))
        bars = band_norm[f_idx]
        if prev_bars is not None:
            # Fast attack, smooth decay visually
            bars = np.maximum(bars, prev_bars * 0.62)
        prev_bars = bars
        spectrum_frames.append([round(float(x), 3) for x in bars])

    return {
        "bpm": float(bpm),
        "beats": beats,
        "beat_strengths": beat_strengths,
        "spectrum_fps": int(spectrum_fps),
        "spectrum_frames": spectrum_frames,
        "duration_sec": round(dur_sec, 3),
    }


def detect_audio_beats(
    media_path: str,
    start_sec: float = 0.0,
    duration_sec: Optional[float] = None,
    spectrum_fps: int = 10,
    max_analyze_sec: float = 180.0,
) -> Dict[str, Any]:
    """Extract clip audio via FFmpeg into pure NumPy and detect beats + live spectrum bars."""
    capped_dur = min(float(duration_sec), max_analyze_sec) if duration_sec else max_analyze_sec
    samples = _load_mono_pcm(
        media_path=media_path,
        start_sec=max(0.0, float(start_sec or 0.0)),
        duration_sec=capped_dur,
        sample_rate=22050,
    )
    return analyze_pcm_beats_and_spectrum(samples, sample_rate=22050, spectrum_fps=spectrum_fps, num_bands=24)


def snap_timestamps_to_beats(
    timestamps: List[float],
    beats: List[float],
    max_delta: float = 0.25,
    min_gap: float = 0.08,
) -> List[float]:
    """Snap event timestamps to the nearest audio beat within ±max_delta seconds."""
    if not timestamps:
        return []
    if not beats:
        return [round(float(t), 3) for t in timestamps]

    sorted_beats = sorted(float(b) for b in beats)
    snapped: List[float] = []
    for raw_t in timestamps:
        t = float(raw_t)
        best_b = min(sorted_beats, key=lambda b: abs(b - t))
        target = best_b if abs(best_b - t) <= max_delta else t
        target = max(0.0, round(target, 3))
        if snapped and (target - snapped[-1]) < min_gap:
            target = round(snapped[-1] + min_gap, 3)
        snapped.append(target)
    return snapped


def snap_punch_and_broll_to_beats(
    punch_data: Optional[Dict[str, Any]],
    broll_segments: Optional[List[Dict[str, Any]]],
    beats: List[float],
    max_delta: float = 0.25,
) -> Tuple[Optional[Dict[str, Any]], List[Dict[str, Any]]]:
    """Snap punch zoom intervals/timestamps and B-Roll cutaway start timestamps onto nearest beats."""
    if not beats:
        return punch_data, list(broll_segments or [])

    sorted_beats = sorted(float(b) for b in beats)
    new_punch: Optional[Dict[str, Any]] = dict(punch_data) if punch_data else None
    if new_punch:
        if "punch_timestamps" in new_punch and isinstance(new_punch["punch_timestamps"], list):
            new_punch["punch_timestamps"] = snap_timestamps_to_beats(
                new_punch["punch_timestamps"], sorted_beats, max_delta=max_delta
            )
        if "punch_intervals" in new_punch and isinstance(new_punch["punch_intervals"], list):
            updated_intervals = []
            for iv in new_punch["punch_intervals"]:
                s = float(iv.get("start", 0.0))
                e = float(iv.get("end", s + 0.6))
                dur = max(0.25, e - s)
                best_b = min(sorted_beats, key=lambda b: abs(b - s))
                snapped_s = best_b if abs(best_b - s) <= max_delta else s
                updated_intervals.append({
                    **iv,
                    "start": round(max(0.0, snapped_s), 3),
                    "end": round(max(0.0, snapped_s) + dur, 3),
                })
            new_punch["punch_intervals"] = updated_intervals

    new_broll: List[Dict[str, Any]] = []
    for seg in (broll_segments or []):
        s = float(seg.get("start", 0.0))
        e = float(seg.get("end", s + 2.0))
        dur = max(0.4, e - s)
        best_b = min(sorted_beats, key=lambda b: abs(b - s))
        snapped_s = best_b if abs(best_b - s) <= max_delta else s
        new_broll.append({
            **seg,
            "start": round(max(0.0, snapped_s), 3),
            "end": round(max(0.0, snapped_s) + dur, 3),
        })

    return new_punch, new_broll


def get_spectrum_bars_at_time(
    spectrum_data: Optional[Dict[str, Any]],
    t: float,
    num_bands: int = 24,
) -> List[float]:
    """Lookup smoothed frequency bars [0..1] for relative clip time t."""
    if not spectrum_data:
        return []
    frames = spectrum_data.get("spectrum_frames") or []
    fps = int(spectrum_data.get("spectrum_fps") or 10)
    if not frames:
        return []
    idx = min(len(frames) - 1, max(0, int(round(max(0.0, float(t)) * fps))))
    bars = list(frames[idx])
    if len(bars) != num_bands:
        return (bars + [0.06] * num_bands)[:num_bands]
    return bars


def draw_spectrum_visualizer(
    canvas: Image.Image,
    bars: List[float],
    position: str = "bottom",
    color_hex: str = "#C6F32F",
    width: Optional[int] = None,
    height: Optional[int] = None,
) -> Image.Image:
    """Composite a 24-bar glassmorphic live audio spectrum visualizer onto an RGBA/RGB Pillow frame."""
    if not bars:
        return canvas

    base = canvas.convert("RGBA") if canvas.mode != "RGBA" else canvas
    W, H = width or base.width, height or base.height
    pos = (position or "bottom").strip().lower()
    if pos not in {"bottom", "top", "center"}:
        pos = "bottom"

    r, g, b = _parse_hex_rgb(color_hex, (198, 243, 47))
    overlay = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    draw = ImageDraw.Draw(overlay, "RGBA")

    n_bars = len(bars)
    panel_w = int(W * 0.72)
    panel_h = int(max(36, H * (0.065 if pos != "center" else 0.095)))
    px0 = (W - panel_w) // 2

    if pos == "top":
        py0 = int(H * 0.115)
    elif pos == "center":
        py0 = (H - panel_h) // 2
    else:
        py0 = int(H * 0.845)

    if pos != "center":
        draw.rounded_rectangle(
            [px0 - 14, py0 - 8, px0 + panel_w + 14, py0 + panel_h + 8],
            radius=max(10, int(W * 0.018)),
            fill=(10, 14, 24, 145),
            outline=(255, 255, 255, 36),
            width=max(1, int(W * 0.002)),
        )

    gap = max(2, int(panel_w / (n_bars * 2.6)))
    total_gaps = gap * (n_bars - 1)
    bar_w = max(3, int((panel_w - total_gaps) / n_bars))
    actual_w = n_bars * bar_w + total_gaps
    start_x = (W - actual_w) // 2
    alpha_main = 148 if pos == "center" else 230
    radius = max(2, bar_w // 2)

    for idx, norm_val in enumerate(bars):
        v = max(0.06, min(1.0, float(norm_val)))
        bh = max(4, int(panel_h * v))
        x0 = start_x + idx * (bar_w + gap)
        x1 = x0 + bar_w
        if pos == "center":
            cy = py0 + panel_h // 2
            y0 = cy - bh // 2
            y1 = cy + max(2, bh // 2)
        else:
            y1 = py0 + panel_h
            y0 = y1 - bh

        # Glow + crisp core fill
        draw.rounded_rectangle([x0 - 1, y0 - 1, x1 + 1, y1 + 1], radius=radius, fill=(r, g, b, max(35, alpha_main // 4)))
        draw.rounded_rectangle([x0, y0, x1, y1], radius=radius, fill=(r, g, b, alpha_main))
        # Highlight cap on tall peaks
        if bh >= 10:
            cap_h = max(2, min(4, bh // 4))
            draw.rounded_rectangle([x0, y0, x1, y0 + cap_h], radius=1, fill=(255, 255, 255, min(220, alpha_main)))

    return Image.alpha_composite(base, overlay)
