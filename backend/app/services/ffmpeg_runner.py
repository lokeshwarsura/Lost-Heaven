"""
Centralized FFmpeg Runner (`ffmpeg_runner.py`) for Lost Heaven.

Enforces:
- Strict video encoder flag isolation (no NVENC flags on libx264/libx265/prores_ks; automatic CPU retry if NVENC fails)
- Strict CFR sync (`-fps_mode cfr`) + mod-2 yuv420p output
- Safe sample-aligned AAC audio (`aresample=async=1:first_pts=0`) and clean silent `anullsrc` synthesis
  (bypassing `loudnorm` so digital-zero audio never triggers FFmpeg 9.1 AAC NaN/Inf crashes)
- ASCII-safe temp workspace execution (`tempfile.mkdtemp(prefix="lost_heaven_")`) + atomic file replacement
- Single clean single-segment fallback retry on complex filtergraph/timeline errors + untruncated stderr logging
"""

from __future__ import annotations

import logging
import os
import shutil
import subprocess
import tempfile
import time
from typing import Any, Callable, Dict, List, Optional, Tuple

from app.services.hardware import build_video_encoder_args
from app.services.proxy import detect_hardware_encoder, get_ffmpeg_cmd
from app.services.reframer import build_filter_complex_args

logger = logging.getLogger(__name__)


def _tail_popen(
    cmd: List[str],
    *,
    timeout_sec: float = 900.0,
    is_cancelled_cb: Optional[Callable[[], bool]] = None,
    on_proc_started: Optional[Callable[[Optional[subprocess.Popen]], None]] = None,
) -> Tuple[int, str]:
    proc = subprocess.Popen(
        cmd,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    if on_proc_started:
        try:
            on_proc_started(proc)
        except Exception:
            pass
    try:
        deadline = time.monotonic() + float(timeout_sec)
        while proc.poll() is None:
            if is_cancelled_cb and is_cancelled_cb():
                try:
                    proc.terminate()
                    proc.wait(timeout=3)
                except Exception:
                    try:
                        proc.kill()
                    except Exception:
                        pass
                return -99, "Render superseded or cancelled"
            if time.monotonic() > deadline:
                try:
                    proc.kill()
                except Exception:
                    pass
                return -98, f"FFmpeg timed out after {timeout_sec}s"
            time.sleep(0.08)
        _, err = proc.communicate()
        return int(proc.returncode or 0), str(err or "")
    finally:
        if on_proc_started:
            try:
                on_proc_started(None)
            except Exception:
                pass


def _assemble_ffmpeg_cmd(
    *,
    ffmpeg: str,
    master_video_path: str,
    input_segments: List[Tuple[float, float]],
    filter_complex: str,
    v_out_label: str,
    a_out_label: Optional[str],
    has_audio: bool,
    effective_duration: float,
    target_fps: float,
    extra_input_args: Optional[List[List[str]]] = None,
    extra_af_after: Optional[str] = None,
    encoder: str,
    hardware_mode: str,
    quality_mode: str,
    codec_family: str,
    quality_preset: str,
    out_path: str,
    exec_dir: str,
    progress_cb: Optional[Callable[[str], None]] = None,
) -> Tuple[List[str], Optional[str]]:
    cmd: List[str] = [ffmpeg, "-y", "-hide_banner"]

    # Segment inputs using seek ONLY at input (never mixing with filter `trim=`)
    for ks, ke in input_segments:
        seg_dur = max(0.12, float(ke) - float(ks))
        cmd.extend(["-ss", f"{float(ks):.3f}", "-t", f"{seg_dur:.3f}", "-i", master_video_path])

    extra_count = 0
    for extra in (extra_input_args or []):
        cmd.extend(extra)
        extra_count += 1

    # Add silent stereo anullsrc ONLY when source has no usable audio stream
    anull_idx: Optional[int] = None
    if not has_audio or not a_out_label:
        anull_idx = len(input_segments) + extra_count
        cmd.extend([
            "-f", "lavfi",
            "-t", f"{max(0.2, float(effective_duration)):.3f}",
            "-i", "anullsrc=channel_layout=stereo:sample_rate=48000",
        ])

    full_fc = filter_complex
    map_audio_spec: str
    if anull_idx is not None:
        # Clean silent AAC without loudnorm (loudnorm on pure zeros hits FFmpeg 9.1 AAC NaN bug!)
        full_fc = f"{full_fc};[{anull_idx}:a]asetpts=PTS-STARTPTS[a_silent_out]"
        map_audio_spec = "[a_silent_out]"
    elif extra_af_after:
        full_fc = f"{full_fc};[{a_out_label}]{extra_af_after}[a_post_out]"
        map_audio_spec = "[a_post_out]"
    else:
        map_audio_spec = f"[{a_out_label}]"

    # Write filtergraph script to ASCII-safe exec_dir and pass version-compatible filter args
    fc_path = os.path.join(exec_dir, f"fc_{len(input_segments)}_{os.getpid()}.txt")
    with open(fc_path, "w", encoding="utf-8") as fh:
        fh.write(full_fc)

    cmd.extend(build_filter_complex_args(ffmpeg, fc_path, full_fc))
    cmd.extend(["-map", f"[{v_out_label}]", "-map", map_audio_spec])

    # Video encoder args strictly isolated per codec
    is_prores = str(codec_family or "").lower() == "prores"
    v_enc_args = build_video_encoder_args(
        encoder,
        quality_mode=quality_mode,
        hardware_mode=hardware_mode,
        codec_family=codec_family,
        quality_preset=quality_preset,
    )
    cmd.extend(v_enc_args)
    cmd.extend(["-fps_mode", "cfr", "-r", f"{float(target_fps):.3f}"])
    if not is_prores and "-pix_fmt" not in v_enc_args:
        cmd.extend(["-pix_fmt", "yuv420p"])

    # Audio encoding (PCM for ProRes MOV vs sample-aligned AAC for MP4)
    if is_prores:
        cmd.extend(["-c:a", "pcm_s16le", "-ar", "48000", "-ac", "2"])
    else:
        cmd.extend(["-c:a", "aac", "-b:a", "192k", "-ar", "48000", "-ac", "2", "-movflags", "+faststart"])

    cmd.extend(["-shortest", out_path])
    return cmd, fc_path


def run_render_ffmpeg_command(
    *,
    master_video_path: str,
    output_path: str,
    primary_build: Dict[str, Any],
    fallback_builder: Optional[Callable[[], Dict[str, Any]]] = None,
    target_fps: float = 30.0,
    hardware_mode: str = "gpu",
    quality_mode: str = "final_hq",
    codec_family: str = "h264",
    quality_preset: str = "social_balanced",
    extra_input_args: Optional[List[List[str]]] = None,
    extra_af_after: Optional[str] = None,
    is_cancelled_cb: Optional[Callable[[], bool]] = None,
    on_proc_started: Optional[Callable[[Optional[subprocess.Popen]], None]] = None,
    timeout_sec: float = 900.0,
) -> str:
    """
    Execute the FFmpeg render safely in an ASCII temp directory with:
    1. Hardware encoder isolation + automatic CPU retry if NVENC initialization fails
    2. Single automatic clean fallback render if complex multi-segment timeline graph fails
    3. Full untruncated FFmpeg stderr logging on failure
    """
    ffmpeg = get_ffmpeg_cmd()
    hw_norm = str(hardware_mode or "gpu").lower()
    detected_enc = detect_hardware_encoder() if hw_norm != "cpu" else "libx264"
    exec_dir = tempfile.mkdtemp(prefix="lost_heaven_")

    try:
        ext = os.path.splitext(output_path)[1] or (".mov" if str(codec_family).lower() == "prores" else ".mp4")
        tmp_out = os.path.join(exec_dir, f"render_out{ext}")

        def _try_run(build_dict: Dict[str, Any], enc: str, hw_mode: str, ex_inputs: Optional[List[List[str]]], ex_af: Optional[str]) -> Tuple[int, str]:
            if os.path.exists(tmp_out):
                try:
                    os.remove(tmp_out)
                except OSError:
                    pass
            cmd, _ = _assemble_ffmpeg_cmd(
                ffmpeg=ffmpeg,
                master_video_path=master_video_path,
                input_segments=build_dict["input_segments"],
                filter_complex=build_dict["filter_complex"],
                v_out_label=build_dict["v_out_label"],
                a_out_label=build_dict.get("a_out_label"),
                has_audio=bool(build_dict.get("has_audio", False)),
                effective_duration=float(build_dict.get("effective_duration", 1.0)),
                target_fps=target_fps,
                extra_input_args=ex_inputs,
                extra_af_after=ex_af,
                encoder=enc,
                hardware_mode=hw_mode,
                quality_mode=quality_mode,
                codec_family=codec_family,
                quality_preset=quality_preset,
                out_path=tmp_out,
                exec_dir=exec_dir,
            )
            return _tail_popen(
                cmd,
                timeout_sec=timeout_sec,
                is_cancelled_cb=is_cancelled_cb,
                on_proc_started=on_proc_started,
            )

        rc, stderr = _try_run(primary_build, detected_enc, hw_norm, extra_input_args, extra_af_after)

        # Cancelled / superseded exits immediately without fallback
        if rc == -99:
            raise InterruptedError(stderr)

        # Retry with clean CPU libx264 if NVENC / hardware failed
        if rc != 0 and detected_enc != "libx264" and hw_norm != "cpu":
            logger.warning("Primary hardware encoder %s exited (%s); retrying with CPU libx264.\nStderr:\n%s", detected_enc, rc, stderr)
            rc, stderr = _try_run(primary_build, "libx264", "cpu", extra_input_args, extra_af_after)
            if rc == -99:
                raise InterruptedError(stderr)

        # Automatic single clean single-segment fallback if complex graph failed
        if rc != 0 and fallback_builder is not None:
            logger.error("Primary render graph failed (rc=%s). Full FFmpeg stderr:\n%s", rc, stderr)
            fb_build = fallback_builder()
            fb_ex_inputs = list(extra_input_args or [])
            # Ensure caption input stays cleanly wired on fallback while dropping risky extra audio filters
            rc_fb, err_fb = _try_run(fb_build, "libx264", "cpu", fb_ex_inputs, None)
            if rc_fb == -99:
                raise InterruptedError(err_fb)
            if rc_fb != 0:
                logger.error("Single-segment fallback render also failed (rc=%s). Full FFmpeg stderr:\n%s", rc_fb, err_fb)
                raise RuntimeError(f"FFmpeg render failed (primary rc={rc}, fallback rc={rc_fb}): {err_fb}")
            rc = rc_fb
        elif rc != 0:
            logger.error("FFmpeg render failed (rc=%s). Full FFmpeg stderr:\n%s", rc, stderr)
            raise RuntimeError(f"FFmpeg render failed (rc={rc}): {stderr}")

        if not os.path.exists(tmp_out) or os.path.getsize(tmp_out) <= 0:
            raise RuntimeError(f"FFmpeg completed with rc={rc} but output file is empty: {tmp_out}")

        os.makedirs(os.path.dirname(os.path.abspath(output_path)), exist_ok=True)
        shutil.move(tmp_out, output_path)
        return output_path
    finally:
        shutil.rmtree(exec_dir, ignore_errors=True)
