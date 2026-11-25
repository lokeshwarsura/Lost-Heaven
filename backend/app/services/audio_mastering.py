import os
import logging
import shutil
import subprocess
from pathlib import Path
from typing import List, Optional, Tuple

from app.config import settings
from app.services.proxy import get_ffmpeg_cmd

logger = logging.getLogger(__name__)


def resolve_local_media_asset(asset_ref: Optional[str]) -> Optional[str]:
    """
    Resolve `/media/assets/...` URL or relative/absolute filesystem paths to an
    existing local file path on disk.
    """
    if not asset_ref:
        return None
    ref = str(asset_ref).strip()
    if not ref:
        return None
    if os.path.exists(ref):
        return os.path.abspath(ref)
    if ref.startswith("/media/"):
        rel = ref[len("/media/"):].lstrip("/\\")
        cand = Path(settings.STORAGE_DIR) / rel
        if cand.exists():
            return str(cand.resolve())
    cand2 = Path(settings.STORAGE_DIR) / ref.lstrip("/\\")
    if cand2.exists():
        return str(cand2.resolve())
    cand3 = Path(settings.STORAGE_DIR) / "assets" / os.path.basename(ref)
    if cand3.exists():
        return str(cand3.resolve())
    return None


def stage_ascii_asset(src_path: str, work_dir: Path, target_stem: str = "bgm") -> Optional[str]:
    """Copy a local asset file into ASCII `work_dir` so FFmpeg filtergraphs stay safe on Windows Unicode paths."""
    if not src_path or not os.path.exists(src_path):
        return None
    ext = Path(src_path).suffix.lower() or ".wav"
    dst_name = f"{target_stem}{ext}"
    dst_path = work_dir / dst_name
    shutil.copy2(src_path, dst_path)
    return dst_name


def maybe_run_deepfilternet(input_wav: str, output_wav: str) -> bool:
    """
    Optional DeepFilterNet neural speech enhancement hook if installed offline.
    Returns True if applied successfully, False to fall back to FFmpeg spectral chain.
    """
    try:
        import df  # type: ignore  # noqa: F401
        from df.enhance import enhance, init_df, load_audio, save_audio  # type: ignore

        model, df_state, _ = init_df()
        audio, _ = load_audio(input_wav, sr=df_state.sr())
        enhanced = enhance(model, df_state, audio)
        save_audio(output_wav, enhanced, df_state.sr())
        return os.path.exists(output_wav)
    except Exception:
        return False


def build_voice_filter_chain(
    *,
    auto_trim_dead_air: bool = False,
    tighten_pacing: bool = False,
    voice_isolation: bool = False,
    vocal_clarity: bool = False,
    audio_volume: float = 1.0,
    normalize_audio: bool = True,
) -> str:
    """
    Build the broadcast-grade voice filter chain:
    - Dead air silenceremove (when not already cut via timeline_editor)
    - 1-Click Studio Voice Isolation (90Hz HPF, 12kHz LPF, afftdn NR, 3:1 compressor, +2.5dB vocal presence)
    - Light Vocal Denoise fallback (80Hz HPF, afftdn)
    - Gain scaling + EBU R128 / -14 LUFS loudness normalization
    """
    filters: List[str] = []

    if auto_trim_dead_air and not tighten_pacing:
        filters.append("silenceremove=stop_periods=-1:stop_duration=0.45:stop_threshold=-35dB")

    if voice_isolation:
        filters.extend([
            "highpass=f=90",
            "lowpass=f=12000",
            "afftdn=nr=18:nf=-32:tn=1",
            "acompressor=threshold=-20dB:ratio=3.0:attack=8:release=120:makeup=2.5dB",
            "equalizer=f=3200:t=q:w=1.2:g=2.5",
        ])
    elif vocal_clarity:
        filters.extend(["highpass=f=80", "afftdn=nf=-25"])

    if abs(float(audio_volume or 1.0) - 1.0) > 0.01:
        vol = max(0.0, min(3.0, float(audio_volume)))
        filters.append(f"volume={vol:.2f}")

    if normalize_audio:
        filters.append("loudnorm=I=-14:TP=-1.5:LRA=11")

    filters.extend(["aresample=48000", "aformat=sample_rates=48000:channel_layouts=stereo"])
    return ",".join(filters)


def build_ducked_bgm_filter(
    voice_label: str,
    bgm_input_idx: int,
    duration: float,
    bgm_volume: float = 0.22,
    out_label: str = "a_bgm_mix",
) -> Tuple[str, str]:
    """
    Mix background music with true FFmpeg sidechain compression ducking driven by voice energy.
    Returns (filter_fragment, output_label).
    """
    dur = max(0.2, float(duration or 1.0))
    vol = max(0.02, min(0.95, float(bgm_volume or 0.22)))
    v_main = f"{voice_label}_m"
    v_sc = f"{voice_label}_sc"
    bgm_pre = f"bgm_pre_{bgm_input_idx}"
    bgm_duck = f"bgm_duck_{bgm_input_idx}"

    frag = (
        f"[{voice_label}]asplit=2[{v_main}][{v_sc}];"
        f"[{bgm_input_idx}:a]aresample=48000,aformat=sample_rates=48000:channel_layouts=stereo,"
        f"atrim=0:{dur:.3f},asetpts=PTS-STARTPTS,volume={vol:.3f}[{bgm_pre}];"
        f"[{bgm_pre}][{v_sc}]sidechaincompress=threshold=0.03:ratio=5.0:attack=40:release=350:makeup=1.0[{bgm_duck}];"
        f"[{v_main}][{bgm_duck}]amix=inputs=2:duration=first:dropout_transition=0:normalize=0[{out_label}]"
    )
    return frag, out_label


def extract_room_tone_sample(
    media_path: str,
    exec_dir: str,
    start_sec: float = 0.0,
    duration_sec: float = 30.0,
    sample_len: float = 0.50,
    out_filename: str = "room_tone.wav",
) -> Optional[str]:
    """
    Scan clip audio for the quietest non-silent `sample_len` window of ambient room tone
    so jump-cut splices never drop to jarring digital -inf silence. Falls back to subtle
    -52 dBFS pink room noise if no clean ambient bed exists.
    """
    import numpy as np
    import wave

    os.makedirs(exec_dir, exist_ok=True)
    out_path = os.path.join(exec_dir, out_filename)
    sr = 48000
    target_samples = max(4800, int(round(sample_len * sr)))

    if media_path and os.path.exists(media_path):
        ffmpeg_bin = get_ffmpeg_cmd() or "ffmpeg"
        raw_pcm = os.path.join(exec_dir, "_rt_scan.s16le")
        cmd = [ffmpeg_bin, "-v", "error", "-y"]
        if start_sec and start_sec > 0.001:
            cmd += ["-ss", f"{float(start_sec):.3f}"]
        cmd += ["-i", media_path, "-t", f"{min(90.0, max(1.0, float(duration_sec or 30.0))):.3f}"]
        cmd += ["-vn", "-ac", "1", "-ar", str(sr), "-f", "s16le", "-acodec", "pcm_s16le", raw_pcm]
        res = subprocess.run(cmd, cwd=exec_dir, capture_output=True, text=True)
        if res.returncode == 0 and os.path.exists(raw_pcm):
            raw_bytes = open(raw_pcm, "rb").read()
            try:
                os.remove(raw_pcm)
            except OSError:
                pass
            samples = np.frombuffer(raw_bytes, dtype=np.int16).astype(np.float32) / 32768.0
            if len(samples) >= target_samples:
                hop = max(2400, target_samples // 4)
                best_rms = 1e9
                best_seg = None
                for i in range(0, len(samples) - target_samples + 1, hop):
                    seg = samples[i:i + target_samples]
                    rms = float(np.sqrt(np.mean(seg ** 2)))
                    if 1e-5 < rms < best_rms:
                        best_rms = rms
                        best_seg = seg
                if best_seg is not None:
                    # Normalize gentle ambient bed to ~ -44 dBFS and apply 25ms fade in/out for seamless looping
                    scale = min(1.0, 0.0063 / max(1e-5, best_rms))
                    bed = np.clip(best_seg * scale, -0.15, 0.15)
                    fade_n = min(1200, len(bed) // 6)
                    if fade_n > 0:
                        ramp = np.linspace(0.0, 1.0, fade_n, dtype=np.float32)
                        bed[:fade_n] *= ramp
                        bed[-fade_n:] *= ramp[::-1]
                    pcm_i16 = (bed * 32767.0).astype(np.int16)
                    with wave.open(out_path, "wb") as wf:
                        wf.setnchannels(2)
                        wf.setsampwidth(2)
                        wf.setframerate(sr)
                        stereo = np.column_stack([pcm_i16, pcm_i16]).ravel()
                        wf.writeframes(stereo.tobytes())
                    return out_path

    # Deterministic ultra-gentle ambient noise fallback (-52 dBFS)
    rng = np.random.default_rng(seed=42)
    noise = rng.normal(0.0, 0.0025, size=target_samples).astype(np.float32)
    pcm_i16 = np.clip(noise * 32767.0, -32767, 32767).astype(np.int16)
    with wave.open(out_path, "wb") as wf:
        wf.setnchannels(2)
        wf.setsampwidth(2)
        wf.setframerate(sr)
        stereo = np.column_stack([pcm_i16, pcm_i16]).ravel()
        wf.writeframes(stereo.tobytes())
    return out_path


def build_room_tone_bed_filter(
    voice_label: str,
    rt_input_idx: int,
    duration: float,
    out_label: str = "a_room",
    bed_volume: float = 0.22,
) -> Tuple[str, str]:
    """Loop extracted 0.5s room-tone sample gently beneath jump-cut speech splices."""
    dur = max(0.5, float(duration or 30.0))
    vol = max(0.05, min(0.65, float(bed_volume)))
    rt_lbl = f"_rtbed_{rt_input_idx}"
    v_dup = f"_v_rt_{rt_input_idx}"
    frag = (
        f"[{voice_label}]aresample=48000,aformat=sample_rates=48000:channel_layouts=stereo[{v_dup}];"
        f"[{rt_input_idx}:a]aloop=loop=-1:size=24000,aresample=48000,"
        f"aformat=sample_rates=48000:channel_layouts=stereo,"
        f"atrim=0:{dur:.3f},asetpts=PTS-STARTPTS,volume={vol:.3f}[{rt_lbl}];"
        f"[{v_dup}][{rt_lbl}]amix=inputs=2:duration=first:dropout_transition=0:normalize=0[{out_label}]"
    )
    return frag, out_label

