import os
import wave
import shutil
import logging
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import numpy as np

from app.config import settings

logger = logging.getLogger(__name__)

SFX_SAMPLE_RATE = 48000


def _write_stereo_wav(path: Path, mono_signal: np.ndarray, sr: int = SFX_SAMPLE_RATE) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    clipped = np.clip(mono_signal, -0.98, 0.98)
    pcm = (clipped * 32767.0).astype(np.int16)
    stereo = np.column_stack((pcm, pcm)).ravel()
    with wave.open(str(path), "wb") as wf:
        wf.setnchannels(2)
        wf.setsampwidth(2)
        wf.setframerate(sr)
        wf.writeframes(stereo.tobytes())


def ensure_procedural_sfx_pack(sfx_dir: Optional[Path] = None) -> Dict[str, str]:
    """
    Generate crisp procedural WAV micro-SFX into `storage/sfx/` (100% local, offline, deterministic):
    - `whoosh.wav` (~0.40s filtered noise sweep for hook banner & reveals)
    - `pop.wav` (~0.15s punchy downward chirp transient for punch-in zooms)
    - `riser.wav` (~0.80s upward harmonic swell)
    """
    target_dir = Path(sfx_dir) if sfx_dir else (Path(settings.STORAGE_DIR) / "sfx")
    target_dir.mkdir(parents=True, exist_ok=True)
    sr = SFX_SAMPLE_RATE
    rng = np.random.default_rng(seed=2026)

    # 1. Whoosh (0.40s bell-shaped band-limited sweep)
    whoosh_path = target_dir / "whoosh.wav"
    if not whoosh_path.exists():
        n = int(0.40 * sr)
        t = np.linspace(0.0, 0.40, n, endpoint=False)
        noise = rng.normal(0.0, 1.0, n)
        # Running moving-average smoothing + bell envelope
        kernel = np.ones(48) / 48.0
        smooth_noise = np.convolve(noise, kernel, mode="same")
        env = np.sin(np.pi * (t / 0.40)) ** 1.6
        sweep = 0.25 * np.sin(2.0 * np.pi * (140.0 + 480.0 * (t / 0.40)) * t)
        sig = (smooth_noise * 0.85 + sweep) * env * 0.42
        _write_stereo_wav(whoosh_path, sig, sr)

    # 2. Pop (0.15s percussive pitch drop 920 -> 190 Hz)
    pop_path = target_dir / "pop.wav"
    if not pop_path.exists():
        n = int(0.15 * sr)
        t = np.linspace(0.0, 0.15, n, endpoint=False)
        freq = 190.0 + 730.0 * np.exp(-32.0 * t)
        phase = 2.0 * np.pi * np.cumsum(freq) / sr
        env = np.exp(-26.0 * t) * np.minimum(1.0, t / 0.005)
        sig = np.sin(phase) * env * 0.55
        _write_stereo_wav(pop_path, sig, sr)

    # 3. Riser (0.80s upward swell)
    riser_path = target_dir / "riser.wav"
    if not riser_path.exists():
        n = int(0.80 * sr)
        t = np.linspace(0.0, 0.80, n, endpoint=False)
        freq = 160.0 + 680.0 * (t / 0.80) ** 1.7
        phase = 2.0 * np.pi * np.cumsum(freq) / sr
        env = ((t / 0.80) ** 1.4) * np.where(t > 0.74, (0.80 - t) / 0.06, 1.0)
        sig = (0.7 * np.sin(phase) + 0.25 * np.sin(2.0 * phase)) * env * 0.35
        _write_stereo_wav(riser_path, sig, sr)

    return {
        "whoosh": str(whoosh_path.resolve()),
        "pop": str(pop_path.resolve()),
        "riser": str(riser_path.resolve()),
    }


def plan_sfx_events(
    duration: float,
    *,
    show_hook_banner: bool = False,
    hook_headline: str = "",
    punch_timestamps: Optional[List[float]] = None,
    callout_timestamps: Optional[List[float]] = None,
    transition_times: Optional[List[float]] = None,
) -> List[Dict[str, Any]]:
    """
    Compute deterministic micro-SFX trigger events:
    - Hook banner entrance -> `whoosh` at t=0.05s (-10 dB / gain 0.32)
    - Cold-open transition times -> `whoosh` at transition points (gain 0.25)
    - Dynamic punch-in zoom & stat callouts -> `pop` at timestamps (-10 dB / gain 0.32)
    """
    dur = max(0.5, float(duration or 1.0))
    events: List[Dict[str, Any]] = []

    if show_hook_banner and str(hook_headline or "").strip():
        events.append({"kind": "whoosh", "time": 0.05, "gain": 0.32})

    for tt in (transition_times or []):
        t_val = float(tt)
        if 0.05 <= t_val <= dur - 0.15:
            events.append({"kind": "whoosh", "time": round(t_val, 3), "gain": 0.25})

    last_t = -2.0
    combined_pops = sorted([float(x) for x in (punch_timestamps or [])] + [float(x) for x in (callout_timestamps or [])])
    for ft in combined_pops:
        if 0.25 <= ft <= dur - 0.25 and (ft - last_t) >= 1.2:
            events.append({"kind": "pop", "time": round(ft, 3), "gain": 0.32})
            last_t = ft
        if len(events) >= 8:
            break

    return events


def stage_sfx_inputs(
    events: List[Dict[str, Any]],
    work_dir: Path,
    sfx_dir: Optional[Path] = None,
) -> List[Dict[str, Any]]:
    """Copy required WAVs into ASCII `work_dir` and return staging metadata for FFmpeg."""
    if not events:
        return []
    pack = ensure_procedural_sfx_pack(sfx_dir=sfx_dir)
    staged: List[Dict[str, Any]] = []
    for idx, ev in enumerate(events):
        src = pack.get(ev["kind"])
        if not src or not os.path.exists(src):
            continue
        dst_name = f"sfx_{idx}_{ev['kind']}.wav"
        shutil.copy2(src, work_dir / dst_name)
        staged.append({
            "filename": dst_name,
            "time": float(ev["time"]),
            "gain": float(ev.get("gain", 0.32)),
        })
    return staged


def build_sfx_mix_filter(
    base_audio_label: str,
    sfx_input_specs: List[Tuple[int, float, float]],
    duration: float,
    out_label: str = "a_sfx_mix",
) -> Tuple[str, str]:
    """
    Place delayed SFX events over `base_audio_label` using FFmpeg `adelay` + `amix`.
    Each tuple in `sfx_input_specs` is (ffmpeg_input_index, timestamp_sec, linear_gain).
    """
    if not sfx_input_specs:
        return "", base_audio_label

    dur = max(0.2, float(duration or 1.0))
    parts: List[str] = []
    mix_inputs: List[str] = [f"[{base_audio_label}]"]

    for idx, (in_idx, ts, gain) in enumerate(sfx_input_specs):
        delay_ms = max(0, int(round(ts * 1000.0)))
        lbl = f"sfx_d_{idx}"
        parts.append(
            f"[{in_idx}:a]aresample=48000,aformat=sample_rates=48000:channel_layouts=stereo,"
            f"volume={gain:.3f},adelay={delay_ms}|{delay_ms},apad=whole_dur={dur:.3f},atrim=0:{dur:.3f}[{lbl}]"
        )
        mix_inputs.append(f"[{lbl}]")

    parts.append(
        f"{''.join(mix_inputs)}amix=inputs={len(mix_inputs)}:duration=first:dropout_transition=0:normalize=0[{out_label}]"
    )
    return ";".join(parts), out_label
