import re
import os
import sys
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

def register_cuda_dll_directories():
    """
    Register nvidia/cublas/bin and nvidia/cudnn/bin in site-packages
    and CUDA Toolkit paths on Windows using os.add_dll_directory before loading WhisperModel.
    """
    if sys.platform != "win32":
        return

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

def resolve_whisper_device_and_compute() -> tuple[str, str, str]:
    """
    Dynamically select Whisper device, compute type, and model size based on real detected hardware:
    - If detected NVIDIA VRAM >= 8 GB (e.g. RTX 5070 with 12GB), use cuda with float16 and large-v3-turbo (or configured model).
    - If detected NVIDIA VRAM < 8 GB (e.g. RTX 3050 with 4GB), use cuda with int8_float16.
    - If CUDA fails or no NVIDIA GPU, fallback to cpu with int8 and base.
    """
    from app.services.hardware import detect_hardware_specs
    hw = detect_hardware_specs()
    vram_mb = hw.get("vram_mb", 0)
    has_cuda = hw.get("has_cuda", False)

    if not has_cuda or settings.WHISPER_DEVICE == "cpu":
        return "cpu", "int8", settings.WHISPER_MODEL or "base"

    if vram_mb >= 8000:
        return "cuda", "float16", "large-v3-turbo"
    else:
        return "cuda", "int8_float16", settings.WHISPER_MODEL or "base"

def get_whisper_model(force_device: Optional[str] = None):
    """
    Robust singleton getter for faster-whisper model with dynamic VRAM scaling.
    """
    global _whisper_model_instance
    if _whisper_model_instance is not None and not force_device:
        return _whisper_model_instance

    register_cuda_dll_directories()

    from faster_whisper import WhisperModel

    dev, compute, model_name = resolve_whisper_device_and_compute()
    if force_device:
        dev = force_device
        compute = "int8" if force_device == "cpu" else compute

    models_dir = str(Path(settings.STORAGE_DIR) / "models")
    Path(models_dir).mkdir(parents=True, exist_ok=True)

    # If models_dir already contains model.bin, use it directly (100% offline & fast)
    model_target = models_dir if os.path.exists(os.path.join(models_dir, "model.bin")) else model_name

    # 1. Try CUDA if selected
    if dev == "cuda":
        try:
            logger.info(f"Loading faster-whisper from '{model_target}' on CUDA ({compute})...")
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
            return _whisper_model_instance
        except Exception as cuda_err:
            logger.warning(
                f"CUDA faster-whisper initialization/warmup failed ({cuda_err}). "
                f"Cleanly falling back to CPU (int8)..."
            )
            try:
                import torch
                if torch.cuda.is_available():
                    torch.cuda.empty_cache()
            except Exception:
                pass

    # 2. CPU fallback with int8
    try:
        logger.info(f"Loading faster-whisper from '{model_target}' on CPU (int8)...")
        model = WhisperModel(
            model_target,
            device="cpu",
            compute_type="int8",
            download_root=models_dir,
            cpu_threads=min(8, os.cpu_count() or 4)
        )
        logger.info("faster-whisper CPU initialization verified successfully!")
        _whisper_model_instance = model
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

def transcribe_audio_full(
    audio_path: str,
    language: Optional[str] = None,
    on_progress: Optional[Any] = None
) -> List[Dict[str, Any]]:
    """
    Transcribe a full 16kHz mono audio WAV using faster-whisper with millisecond word timestamps.
    Returns real word dictionaries: [{'word': '...', 'start': 0.12, 'end': 0.45, 'is_filler': False, 'deleted': False}].
    Logs real decoding progress and cleanly falls back to CPU if CUDA fails.
    """
    if not os.path.exists(audio_path):
        raise FileNotFoundError(f"Audio file not found: {audio_path}")

    model = get_whisper_model()

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

    words_list: List[Dict[str, Any]] = []

    try:
        try:
            segments, info = model.transcribe(
                audio_path,
                language=whisper_lang,
                initial_prompt=initial_prompt,
                word_timestamps=True,
                vad_filter=True,
                vad_parameters=dict(min_silence_duration_ms=400)
            )
            total_duration = info.duration if hasattr(info, "duration") and info.duration else 0.0

            for seg in segments:
                if seg.words:
                    for w in seg.words:
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
                if on_progress and total_duration > 0:
                    pct = min(100, int((seg.end / total_duration) * 100))
                    on_progress(pct, f"Decoded up to {seg.end:.1f}s / {total_duration:.1f}s")
        except Exception as cuda_err:
            logger.warning(f"CUDA faster-whisper transcription error: {cuda_err}. Falling back to CPU model...")
            model = get_whisper_model(force_device="cpu")
            segments, info = model.transcribe(
                audio_path,
                language=whisper_lang,
                initial_prompt=initial_prompt,
                word_timestamps=True,
                vad_filter=True,
                vad_parameters=dict(min_silence_duration_ms=400)
            )
            total_duration = info.duration if hasattr(info, "duration") and info.duration else 0.0
            for seg in segments:
                if seg.words:
                    for w in seg.words:
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
                if on_progress and total_duration > 0:
                    pct = min(100, int((seg.end / total_duration) * 100))
                    on_progress(pct, f"Decoded up to {seg.end:.1f}s / {total_duration:.1f}s (CPU)")

        # If VAD filtered everything, retry without VAD
        if not words_list:
            logger.info("VAD returned 0 words. Retrying full audio without VAD filter...")
            segments, info = model.transcribe(
                audio_path,
                language=whisper_lang,
                initial_prompt=initial_prompt,
                word_timestamps=True,
                vad_filter=False
            )
            for seg in segments:
                if seg.words:
                    for w in seg.words:
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
        logger.error(f"Full audio transcription error: {e}\n{traceback.format_exc()}")
        raise RuntimeError(f"Whisper voice recognition failed: {e}")

    return words_list

def filter_filler_words(words: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Return words with filler words excluded."""
    return [w for w in words if not w.get("is_filler", False) and not w.get("deleted", False)]
