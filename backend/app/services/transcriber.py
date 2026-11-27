import re
import os
import sys
import threading
import subprocess
import logging
import traceback
from pathlib import Path
from typing import List, Dict, Any, Optional

from app.config import settings
from app.services.proxy import get_ffmpeg_cmd

logger = logging.getLogger(__name__)

FILLER_WORDS = {
    "um", "uh", "ah", "er", "hmm", "like", "you know", "i mean", "sort of", "kind of"
}

_whisper_model_instance = None
_WHISPER_LOAD_LOCK = threading.Lock()
_WHISPER_INFER_LOCK = threading.Semaphore(1)
_CUDA_RUNTIME_FAILED = False
_CUDA_DLLS_REGISTERED = False
_whisper_runtime_info: Dict[str, Any] = {
    "device": "cpu",
    "compute_type": "int8",
    "model_name": "base",
    "batch_size": 4,
    "badge": "CPU int8",
}


def _fmt_duration(sec: float) -> str:
    s = max(0, int(round(sec)))
    m, rem = divmod(s, 60)
    if m > 0:
        return f"{m}m {rem:02d}s"
    return f"{rem}s"


def _probe_wav_duration(audio_path: str) -> float:
    try:
        import wave
        with wave.open(audio_path, "rb") as wf:
            sr = max(1, wf.getframerate())
            return float(wf.getnframes()) / float(sr)
    except Exception:
        return 0.0


def register_cuda_dll_directories():
    """
    Register nvidia/cublas/bin and nvidia/cudnn/bin in site-packages
    and CUDA Toolkit paths on Windows using os.add_dll_directory before loading WhisperModel.
    """
    global _CUDA_DLLS_REGISTERED
    if _CUDA_DLLS_REGISTERED or sys.platform != "win32":
        return
    _CUDA_DLLS_REGISTERED = True

    import site
    candidates = []
    # 1. Check all site-packages directories for nvidia/cublas/bin & nvidia/cudnn/bin
    try:
        for sp in site.getsitepackages():
            candidates.extend([
                os.path.join(sp, "nvidia", "cublas", "bin"),
                os.path.join(sp, "nvidia", "cudnn", "bin"),
                os.path.join(sp, "nvidia", "cuda_runtime", "bin"),
                os.path.join(sp, "torch", "lib"),
                os.path.join(sp, "ctranslate2")
            ])
    except Exception as e:
        logger.debug(f"Could not iterate site packages: {e}")

    # 2. Check CUDA_PATH if present
    cuda_path = os.environ.get("CUDA_PATH")
    if cuda_path:
        candidates.append(os.path.join(cuda_path, "bin"))

    # 3. Standard NVIDIA Toolkit installations
    candidates.extend([
        r"C:\Program Files\NVIDIA GPU Computing Toolkit\CUDA\v12.6\bin",
        r"C:\Program Files\NVIDIA GPU Computing Toolkit\CUDA\v12.4\bin",
        r"C:\Program Files\NVIDIA GPU Computing Toolkit\CUDA\v12.2\bin",
        r"C:\Program Files\NVIDIA GPU Computing Toolkit\CUDA\v12.1\bin",
        r"C:\Program Files\NVIDIA GPU Computing Toolkit\CUDA\v11.8\bin",
    ])

    for path in candidates:
        if os.path.isdir(path):
            try:
                os.add_dll_directory(path)
                logger.info(f"Registered CUDA DLL directory: {path}")
            except Exception as e:
                logger.debug(f"Could not register DLL directory {path}: {e}")
            if path not in os.environ.get("PATH", ""):
                os.environ["PATH"] = path + os.pathsep + os.environ.get("PATH", "")


def _is_cuda_runtime_available() -> bool:
    """Explicitly verify CUDA availability via `ctranslate2.get_cuda_device_count() > 0` first."""
    register_cuda_dll_directories()
    try:
        import ctranslate2
        if int(ctranslate2.get_cuda_device_count()) > 0:
            return True
    except Exception:
        pass
    try:
        import torch  # type: ignore
        if torch.cuda.is_available() and int(torch.cuda.device_count()) > 0:
            return True
    except Exception:
        pass
    return False


def reset_whisper_model_cache() -> None:
    """Reset loaded faster-whisper singleton when user changes model/device settings."""
    global _whisper_model_instance
    _whisper_model_instance = None


def resolve_whisper_device_and_compute() -> tuple[str, str, str]:
    """
    Dynamically select Whisper device, compute type, and model size based on CTranslate2 + hardware detection:
    - If VRAM >= 8 GB: cuda with `float16` and `large-v3-turbo` (`batch_size=16`).
    - If 0 < VRAM < 8 GB: cuda with `int8_float16` (`batch_size=16`).
    - If `ctranslate2.get_cuda_device_count() > 0` without VRAM cap: cuda with `float16` (`batch_size=16`).
    - Otherwise (or WHISPER_DEVICE == 'cpu'): cpu with `int8`, `beam_size=1`, and `batch_size=4`.
    """
    from app.services.hardware import detect_hardware_specs
    hw = detect_hardware_specs()
    vram_mb = int(hw.get("vram_mb", 0) or 0)
    has_cuda = bool(hw.get("has_cuda", False))

    explicit_dev = str(getattr(settings, "WHISPER_DEVICE", "auto") or "auto").strip().lower()
    explicit_comp = str(getattr(settings, "WHISPER_COMPUTE_TYPE", "auto") or "auto").strip().lower()
    explicit_model = (settings.WHISPER_MODEL or "").strip()

    if not has_cuda or explicit_dev == "cpu":
        comp = explicit_comp if explicit_comp not in ("auto", "") and explicit_dev == "cpu" else "int8"
        return "cpu", comp, explicit_model or "base"

    # Also verify CTranslate2 CUDA count when CUDA hardware is detected (CPU-only builds fall back cleanly in get_whisper_model)
    try:
        register_cuda_dll_directories()
        import ctranslate2
        _ = int(ctranslate2.get_cuda_device_count())
    except Exception:
        pass

    if explicit_dev == "cuda" and explicit_comp not in ("auto", "") and explicit_model:
        return "cuda", explicit_comp, explicit_model

    if vram_mb >= 8000:
        return "cuda", "float16", "large-v3-turbo"
    if 0 < vram_mb < 8000:
        return "cuda", "int8_float16", explicit_model or "base"
    return "cuda", "float16", explicit_model or "base"


def get_whisper_runtime_info() -> Dict[str, Any]:
    """Return active/projected Whisper device, precision, model name, batch size, and badge."""
    dev, compute, model_name = resolve_whisper_device_and_compute()
    if _CUDA_RUNTIME_FAILED and dev == "cuda":
        dev = "cpu"
        compute = "int8"
    active_dev = _whisper_runtime_info.get("device") if _whisper_model_instance is not None else dev
    active_comp = _whisper_runtime_info.get("compute_type") if _whisper_model_instance is not None else compute
    active_model = _whisper_runtime_info.get("model_name") if _whisper_model_instance is not None else model_name
    configured_bs = int(getattr(settings, "WHISPER_BATCH_SIZE", 0) or 0)
    if active_dev == "cuda":
        bs = configured_bs if configured_bs > 4 else 16
    else:
        bs = min(4, max(1, configured_bs)) if configured_bs > 0 else 4
    badge = f"GPU: CUDA {active_comp}" if active_dev == "cuda" else f"CPU {active_comp}"
    return {
        "device": active_dev,
        "compute_type": active_comp,
        "model_name": active_model,
        "batch_size": bs,
        "badge": badge,
    }


def get_whisper_model(force_device: Optional[str] = None):
    """
    Thread-safe singleton getter for faster-whisper model with dynamic VRAM scaling.
    """
    global _whisper_model_instance, _whisper_runtime_info, _CUDA_RUNTIME_FAILED
    if _whisper_model_instance is not None and not force_device:
        return _whisper_model_instance

    with _WHISPER_LOAD_LOCK:
        if _whisper_model_instance is not None and not force_device:
            return _whisper_model_instance

        register_cuda_dll_directories()

        from faster_whisper import WhisperModel

        dev, compute, model_name = resolve_whisper_device_and_compute()
        if _CUDA_RUNTIME_FAILED and not force_device:
            dev = "cpu"
            compute = "int8"
        if force_device:
            dev = force_device
            compute = "int8" if force_device == "cpu" else compute

        models_dir = str(Path(settings.STORAGE_DIR) / "models")
        Path(models_dir).mkdir(parents=True, exist_ok=True)

        # If models_dir already contains model.bin, use it directly (100% offline & fast)
        model_target = models_dir if os.path.exists(os.path.join(models_dir, "model.bin")) else model_name

        # 1. Try CUDA if selected and runtime CUDA device confirmed
        if dev == "cuda" and not _CUDA_RUNTIME_FAILED and _is_cuda_runtime_available():
            try:
                logger.info(f"Loading faster-whisper '{model_name}' from '{model_target}' on CUDA ({compute}, batch_size=16)...")
                model = WhisperModel(
                    model_target,
                    device="cuda",
                    compute_type=compute,
                    download_root=models_dir
                )
                # Warm-up check: verify CUDA / cuBLAS DLL execution doesn't crash
                import numpy as np
                dummy_pcm = np.zeros(16000, dtype=np.float32)
                list(model.transcribe(dummy_pcm, beam_size=1)[0])
                logger.info(f"faster-whisper CUDA ({compute}) initialization verified successfully!")
                _whisper_model_instance = model
                _whisper_runtime_info = {
                    "device": "cuda",
                    "compute_type": compute,
                    "model_name": model_name,
                    "batch_size": 16,
                    "badge": f"GPU: CUDA {compute}",
                }
                return _whisper_model_instance
            except Exception as cuda_err:
                _CUDA_RUNTIME_FAILED = True
                logger.warning(
                    f"CUDA faster-whisper initialization/warmup failed ({cuda_err}). "
                    f"Cleanly falling back to CPU (int8)..."
                )
                try:
                    import torch  # type: ignore
                    if torch.cuda.is_available():
                        torch.cuda.empty_cache()
                except Exception:
                    pass

        # 2. CPU fallback with int8
        try:
            logger.info(f"Loading faster-whisper '{model_name}' from '{model_target}' on CPU (int8, batch_size=4)...")
            model = WhisperModel(
                model_target,
                device="cpu",
                compute_type="int8",
                download_root=models_dir,
                cpu_threads=min(8, os.cpu_count() or 4)
            )
            logger.info("faster-whisper CPU initialization verified successfully!")
            _whisper_model_instance = model
            _whisper_runtime_info = {
                "device": "cpu",
                "compute_type": "int8",
                "model_name": model_name,
                "batch_size": 4,
                "badge": "CPU int8",
            }
            return _whisper_model_instance
        except Exception as cpu_err:
            logger.error(f"Failed to load faster-whisper on CPU: {cpu_err}\n{traceback.format_exc()}")
            raise RuntimeError(f"Failed to initialize faster-whisper model '{model_name}': {cpu_err}")

def extract_audio_segment(video_path: str, start_time: float, duration: float, output_wav_path: str) -> str:
    """Extract a 16kHz mono WAV audio segment for whisper voice recognition."""
    ffmpeg = get_ffmpeg_cmd()
    Path(output_wav_path).parent.mkdir(parents=True, exist_ok=True)

    cmd = [
        ffmpeg,
        "-y",
        "-ss", str(max(0.0, start_time)),
        "-t", str(max(0.1, duration)),
        "-i", video_path,
        "-vn",
        "-ac", "1",
        "-ar", "16000",
        "-c:a", "pcm_s16le",
        output_wav_path
    ]
    res = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    if res.returncode != 0:
        err_msg = res.stderr.decode("utf-8", errors="replace")
        raise RuntimeError(f"FFmpeg audio extraction failed: {err_msg[-300:]}")
    return output_wav_path

def is_filler_word(word: str) -> bool:
    clean = re.sub(r"[^\w\s]", "", word).strip().lower()
    return clean in FILLER_WORDS

def transcribe_clip(video_path: str, start_time: float, duration: float, language: Optional[str] = None) -> List[Dict[str, Any]]:
    """
    Transcribe a video clip segment using faster-whisper with millisecond word timestamps.
    Returns real word dictionaries: [{'word': '...', 'start': 0.12, 'end': 0.45, 'is_filler': False, 'deleted': False}].
    Never returns dummy or mock words. Raises RuntimeError on unrecoverable failure.
    """
    model = get_whisper_model()
    temp_wav = str(Path(settings.STORAGE_DIR) / "temp" / f"transcribe_{int(start_time*100)}_{int(duration*100)}_{os.getpid()}.wav")

    words_list: List[Dict[str, Any]] = []

    try:
        extract_audio_segment(video_path, start_time, duration, temp_wav)

        whisper_lang = None
        initial_prompt = None
        if language and language != "As spoken":
            if language == "Hinglish":
                whisper_lang = "hi"
                initial_prompt = "Transcribe in Hinglish using Roman script (e.g. yaar, kya kar rahe ho, bilkul, video, subscribe, mast)."
            else:
                lang_map = {
                    "English": "en", "Spanish": "es", "French": "fr",
                    "German": "de", "Hindi": "hi", "Japanese": "ja",
                    "Portuguese": "pt", "Arabic": "ar"
                }
                whisper_lang = lang_map.get(language)

        # 1. Transcribe with VAD filter first, catching any CUDA runtime crash
        try:
            segments, info = model.transcribe(
                temp_wav,
                language=whisper_lang,
                initial_prompt=initial_prompt,
                word_timestamps=True,
                vad_filter=True,
                vad_parameters=dict(min_silence_duration_ms=400)
            )
            segments_list = list(segments)
        except Exception as cuda_run_err:
            logger.warning(f"Whisper inference error ({cuda_run_err}). Falling back to CPU model...")
            model = get_whisper_model(force_device="cpu")
            segments, info = model.transcribe(
                temp_wav,
                language=whisper_lang,
                initial_prompt=initial_prompt,
                word_timestamps=True,
                vad_filter=True,
                vad_parameters=dict(min_silence_duration_ms=400)
            )
            segments_list = list(segments)

        for segment in segments_list:
            if segment.words:
                for w in segment.words:
                    cleaned = w.word.strip()
                    if not cleaned:
                        continue
                    words_list.append({
                        "word": cleaned,
                        "start": round(w.start, 3),
                        "end": round(w.end, 3),
                        "is_filler": is_filler_word(cleaned),
                        "deleted": False
                    })

        # 2. If VAD filtered everything (e.g. background music / whisper quiet), retry without VAD
        if not words_list:
            logger.info(f"VAD returned no words for clip [{start_time}s, {duration}s]. Retrying without VAD filter...")
            try:
                segments, info = model.transcribe(
                    temp_wav,
                    language=whisper_lang,
                    initial_prompt=initial_prompt,
                    word_timestamps=True,
                    vad_filter=False
                )
                segments_list = list(segments)
            except Exception as e:
                model = get_whisper_model(force_device="cpu")
                segments, info = model.transcribe(
                    temp_wav,
                    language=whisper_lang,
                    initial_prompt=initial_prompt,
                    word_timestamps=True,
                    vad_filter=False
                )
                segments_list = list(segments)

            for segment in segments_list:
                if segment.words:
                    for w in segment.words:
                        cleaned = w.word.strip()
                        if not cleaned:
                            continue
                        words_list.append({
                            "word": cleaned,
                            "start": round(w.start, 3),
                            "end": round(w.end, 3),
                            "is_filler": is_filler_word(cleaned),
                            "deleted": False
                        })

    except Exception as e:
        logger.error(f"Whisper transcription error: {e}\n{traceback.format_exc()}")
        raise RuntimeError(f"Whisper voice recognition failed: {e}")
    finally:
        if os.path.exists(temp_wav):
            try:
                os.remove(temp_wav)
            except Exception:
                pass

    return words_list

def _run_batched_or_standard_transcribe(
    model: Any,
    audio_path: str,
    *,
    language: Optional[str],
    initial_prompt: Optional[str],
    word_timestamps: bool,
    vad_filter: bool,
    batch_size: int,
    beam_size: int = 1,
):
    """
    Execute faster-whisper BatchedInferencePipeline (`batch_size=16` GPU float16 / `batch_size=4, beam_size=1` CPU int8)
    with transparent fallback to standard `model.transcribe(...)` when needed.
    """
    vad_params = dict(min_silence_duration_ms=400) if vad_filter else None
    try:
        from faster_whisper import BatchedInferencePipeline
        batched = BatchedInferencePipeline(model=model)
        kwargs: Dict[str, Any] = {
            "language": language,
            "initial_prompt": initial_prompt,
            "word_timestamps": word_timestamps,
            "vad_filter": vad_filter,
            "batch_size": batch_size,
            "beam_size": beam_size,
        }
        if vad_params:
            kwargs["vad_parameters"] = vad_params
        return batched.transcribe(audio_path, **kwargs)
    except Exception as batch_err:
        logger.debug(f"BatchedInferencePipeline unavailable/bypassed ({batch_err}); using direct model.transcribe")
        kwargs = {
            "language": language,
            "initial_prompt": initial_prompt,
            "word_timestamps": word_timestamps,
            "vad_filter": vad_filter,
            "beam_size": beam_size,
        }
        if vad_params:
            kwargs["vad_parameters"] = vad_params
        return model.transcribe(audio_path, **kwargs)


def transcribe_audio_full(
    audio_path: str,
    language: Optional[str] = None,
    on_progress: Optional[Any] = None
) -> List[Dict[str, Any]]:
    """
    Transcribe a full 16kHz mono audio WAV using faster-whisper BatchedInferencePipeline
    (`batch_size=16` on CUDA float16, `batch_size=4, beam_size=1` on CPU int8) with millisecond word timestamps
    and ~15% incremental console progress updates.
    """
    if not os.path.exists(audio_path):
        raise FileNotFoundError(f"Audio file not found: {audio_path}")

    model = get_whisper_model()
    rt = get_whisper_runtime_info()
    batch_size = int(rt.get("batch_size", 4))
    active_dev = str(rt.get("device", "cpu")).lower()
    active_comp = str(rt.get("compute_type", "int8"))
    beam_size = 1 if active_dev != "cuda" else 1

    whisper_lang = None
    initial_prompt = None
    if language and language != "As spoken":
        if language == "Hinglish":
            whisper_lang = "hi"
            initial_prompt = "Transcribe in Hinglish using Roman script (e.g. yaar, kya kar rahe ho, bilkul, video, subscribe, mast)."
        else:
            lang_map = {
                "English": "en", "Spanish": "es", "French": "fr",
                "German": "de", "Hindi": "hi", "Japanese": "ja",
                "Portuguese": "pt", "Arabic": "ar"
            }
            whisper_lang = lang_map.get(language)

    dev_banner = f"{active_dev.upper()} {active_comp}"
    active_log = f"Running faster-whisper on {dev_banner} (batch_size={batch_size}, beam_size={beam_size})..."
    logger.info(active_log)
    if on_progress:
        on_progress(0, f"Running faster-whisper {rt.get('model_name', 'base')} on {dev_banner} (batch_size={batch_size})...")

    words_list: List[Dict[str, Any]] = []
    probed_dur = _probe_wav_duration(audio_path)

    def _consume_segments(segments_iter: Any, total_duration: float) -> None:
        nonlocal words_list
        next_milestone = 15
        last_end = 0.0
        for seg in segments_iter:
            seg_end = float(getattr(seg, "end", 0.0) or 0.0)
            last_end = max(last_end, seg_end)
            if getattr(seg, "words", None):
                for w in seg.words:
                    cleaned = w.word.strip()
                    if not cleaned:
                        continue
                    words_list.append({
                        "word": cleaned,
                        "start": round(float(w.start), 3),
                        "end": round(float(w.end), 3),
                        "is_filler": is_filler_word(cleaned),
                        "deleted": False,
                    })
            if on_progress and total_duration > 0:
                pct = min(100, int((seg_end / total_duration) * 100))
                if pct >= next_milestone or pct >= 99:
                    msg_txt = f"Transcribed {_fmt_duration(seg_end)} / {_fmt_duration(total_duration)} ({pct}%)..."
                    try:
                        on_progress(pct, msg_txt, len(words_list))
                    except TypeError:
                        on_progress(pct, msg_txt)
                    while next_milestone <= pct:
                        next_milestone += 15
        if on_progress and total_duration > 0 and last_end > 0 and next_milestone <= 100:
            msg_done = f"Transcribed {_fmt_duration(total_duration)} / {_fmt_duration(total_duration)} (100%)..."
            try:
                on_progress(100, msg_done, len(words_list))
            except TypeError:
                on_progress(100, msg_done)

    try:
        with _WHISPER_INFER_LOCK:
            try:
                segments, info = _run_batched_or_standard_transcribe(
                    model,
                    audio_path,
                    language=whisper_lang,
                    initial_prompt=initial_prompt,
                    word_timestamps=True,
                    vad_filter=True,
                    batch_size=batch_size,
                    beam_size=beam_size,
                )
                total_duration = float(getattr(info, "duration", 0.0) or 0.0) or probed_dur
                _consume_segments(segments, total_duration)
            except Exception as cuda_err:
                logger.warning(f"CUDA/Batched faster-whisper transcription error: {cuda_err}. Falling back to CPU (int8, batch_size=4, beam_size=1)...")
                model = get_whisper_model(force_device="cpu")
                rt = get_whisper_runtime_info()
                batch_size = min(4, int(rt.get("batch_size", 4)))
                words_list = []
                segments, info = _run_batched_or_standard_transcribe(
                    model,
                    audio_path,
                    language=whisper_lang,
                    initial_prompt=initial_prompt,
                    word_timestamps=True,
                    vad_filter=True,
                    batch_size=batch_size,
                    beam_size=1,
                )
                total_duration = float(getattr(info, "duration", 0.0) or 0.0) or probed_dur
                _consume_segments(segments, total_duration)

            # If VAD filtered everything, retry without VAD
            if not words_list:
                logger.info("VAD returned 0 words. Retrying full audio without VAD filter...")
                segments, info = _run_batched_or_standard_transcribe(
                    model,
                    audio_path,
                    language=whisper_lang,
                    initial_prompt=initial_prompt,
                    word_timestamps=True,
                    vad_filter=False,
                    batch_size=batch_size,
                    beam_size=beam_size,
                )
                total_duration = float(getattr(info, "duration", 0.0) or 0.0) or probed_dur
                _consume_segments(segments, total_duration)

    except Exception as e:
        logger.error(f"Full audio transcription error: {e}\n{traceback.format_exc()}")
        raise RuntimeError(f"Whisper voice recognition failed: {e}")

    return words_list

def filter_filler_words(words: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Return words with filler words excluded."""
    return [w for w in words if not w.get("is_filler", False) and not w.get("deleted", False)]
