import asyncio
import importlib
import logging
import re
import shutil
import subprocess
import tempfile
import wave
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import numpy as np

from app.config import settings
from app.services.proxy import get_ffmpeg_cmd
from app.services.translator import translate_clip_transcript

logger = logging.getLogger("lost_heaven.dubbing")

EDGE_VOICE_MAP: Dict[str, Dict[str, str]] = {
    "spanish": {"default": "es-ES-AlvaroNeural", "female": "es-ES-ElviraNeural", "male": "es-ES-AlvaroNeural"},
    "hindi": {"default": "hi-IN-MadhurNeural", "female": "hi-IN-SwaraNeural", "male": "hi-IN-MadhurNeural"},
    "telugu": {"default": "te-IN-MohanNeural", "female": "te-IN-ShrutiNeural", "male": "te-IN-MohanNeural"},
    "french": {"default": "fr-FR-HenriNeural", "female": "fr-FR-DeniseNeural", "male": "fr-FR-HenriNeural"},
    "german": {"default": "de-DE-ConradNeural", "female": "de-DE-KatjaNeural", "male": "de-DE-ConradNeural"},
    "portuguese": {"default": "pt-BR-AntonioNeural", "female": "pt-BR-FranciscaNeural", "male": "pt-BR-AntonioNeural"},
    "japanese": {"default": "ja-JP-KeitaNeural", "female": "ja-JP-NanamiNeural", "male": "ja-JP-KeitaNeural"},
    "korean": {"default": "ko-KR-InJoonNeural", "female": "ko-KR-SunHiNeural", "male": "ko-KR-InJoonNeural"},
    "arabic": {"default": "ar-SA-HamedNeural", "female": "ar-SA-ZariyahNeural", "male": "ar-SA-HamedNeural"},
    "english": {"default": "en-US-AndrewNeural", "female": "en-US-AriaNeural", "male": "en-US-GuyNeural"},
}


def _build_atempo_chain(ratio: float) -> str:
    """
    Build valid FFmpeg `atempo` chain for any target speed multiplier `ratio`
    (each filter accepts 0.5..2.0).
    """
    r = max(0.35, min(3.5, float(ratio)))
    parts: List[str] = []
    while r > 2.0:
        parts.append("atempo=2.0")
        r /= 2.0
    while r < 0.5:
        parts.append("atempo=0.5")
        r /= 0.5
    parts.append(f"atempo={r:.4f}")
    return ",".join(parts)


def _synth_edge_tts(text: str, lang_key: str, voice_pref: str, out_mp3: Path) -> bool:
    try:
        edge_tts = importlib.import_module("edge_tts")
    except Exception:
        return False
    voice_table = EDGE_VOICE_MAP.get(lang_key, EDGE_VOICE_MAP["english"])
    voice_name = voice_table.get(voice_pref.lower(), voice_table["default"])

    async def _run():
        comm = edge_tts.Communicate(text, voice_name)
        await comm.save(str(out_mp3))

    try:
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            loop = None
        if loop and loop.is_running():
            # Run inside fresh thread if already inside an async loop
            import concurrent.futures
            with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
                pool.submit(lambda: asyncio.run(_run())).result(timeout=18)
        else:
            asyncio.run(_run())
        return out_mp3.exists() and out_mp3.stat().st_size > 400
    except Exception as exc:
        logger.debug(f"edge_tts unavailable/offline fallback: {exc}")
        return False


def _synth_windows_sapi(text: str, voice_pref: str, out_wav: Path) -> bool:
    """Offline Windows System.Speech synthesis directly to WAV (zero extra pip packages)."""
    if shutil.which("powershell") is None:
        return False
    safe_txt = text.replace("'", "''")
    gender_cmd = ""
    if voice_pref.lower() == "female":
        gender_cmd = "$synth.SelectVoiceByHints([System.Speech.Synthesis.VoiceGender]::Female);"
    elif voice_pref.lower() == "male":
        gender_cmd = "$synth.SelectVoiceByHints([System.Speech.Synthesis.VoiceGender]::Male);"

    ps_script = (
        "Add-Type -AssemblyName System.Speech; "
        "$synth = New-Object System.Speech.Synthesis.SpeechSynthesizer; "
        f"{gender_cmd} "
        "$synth.Rate = 1; "
        f"$synth.SetOutputToWaveFile('{str(out_wav)}'); "
        f"$synth.Speak('{safe_txt}'); "
        "$synth.Dispose()"
    )
    try:
        res = subprocess.run(
            ["powershell", "-NoProfile", "-Command", ps_script],
            capture_output=True,
            text=True,
            timeout=20,
        )
        return res.returncode == 0 and out_wav.exists() and out_wav.stat().st_size > 1000
    except Exception as exc:
        logger.debug(f"Windows SAPI synthesis fallback: {exc}")
        return False


def _synth_formant_cadence_wav(text: str, target_dur: float, voice_pref: str, out_wav: Path) -> None:
    """Guaranteed deterministic offline vocal-formant cadence WAV fallback."""
    sr = 22050
    dur = max(0.35, min(12.0, float(target_dur or 1.2)))
    n_samples = int(round(dur * sr))
    t = np.linspace(0.0, dur, n_samples, endpoint=False)
    syllables = max(1, len(re.findall(r"[aeiouyAEIOUY]+|\w+", text)))
    f0 = 195.0 if voice_pref.lower() == "female" else 132.0

    env = np.abs(np.sin(np.pi * t * (syllables / dur))) ** 0.75
    fade = np.minimum(1.0, t / 0.025) * np.minimum(1.0, (dur - t) / 0.03)
    f0_var = f0 + 18.0 * np.sin(2.0 * np.pi * 1.8 * t)
    phase = 2.0 * np.pi * np.cumsum(f0_var) / sr
    sig = (
        0.55 * np.sin(phase)
        + 0.25 * np.sin(2.0 * phase)
        + 0.14 * np.sin(3.4 * phase)
    ) * env * fade * 0.42

    pcm = np.clip(sig * 32767.0, -32767, 32767).astype(np.int16)
    with wave.open(str(out_wav), "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(sr)
        wf.writeframes(pcm.tobytes())


def _extract_dub_phrases(
    words: List[Dict[str, Any]],
    clip_start_time: float,
    clip_duration: float,
    target_language: str,
    api_key: Optional[str] = None,
    model: Optional[str] = None,
) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]], str]:
    """Ensure translated text exists per phrase and return `[(start, end, text)]` clip-relative slots."""
    out_words = [dict(w) for w in (words or []) if isinstance(w, dict)]
    has_trans = any(bool(w.get("translated_phrase") or w.get("translated_text")) for w in out_words)
    tr_source = "existing"
    if not has_trans or target_language:
        tr = translate_clip_transcript(out_words, target_language=target_language, api_key=api_key, model=model)
        out_words = tr["words"]
        tr_source = tr["source"]

    active = [w for w in out_words if not w.get("deleted") and not w.get("cut_from_video") and str(w.get("word", "")).strip()]
    if not active:
        return [], out_words, tr_source

    max_end = max(float(w.get("end", 0.0)) for w in active)
    min_start = min(float(w.get("start", 0.0)) for w in active)
    is_abs = clip_start_time > 0.0 and (
        max_end > max(1.0, clip_duration) + 1.0
        or min_start >= max(0.0, clip_start_time - 0.15)
    ) and not any(bool(w.get("timeline_relative")) for w in active)

    groups: Dict[int, List[Dict[str, Any]]] = {}
    fallback_gid = 0
    for w in active:
        gid = w.get("translated_chunk_id")
        if gid is None:
            gid = fallback_gid
        ws = max(0.0, float(w.get("start", 0.0)) - (clip_start_time if is_abs else 0.0))
        we = max(ws + 0.1, float(w.get("end", ws + 0.25)) - (clip_start_time if is_abs else 0.0))
        nw = dict(w)
        nw["_rel_s"] = ws
        nw["_rel_e"] = min(clip_duration, we) if clip_duration > 0 else we
        groups.setdefault(int(gid), []).append(nw)

    slots: List[Dict[str, Any]] = []
    for gid in sorted(groups.keys()):
        grp = groups[gid]
        ts = grp[0]["_rel_s"]
        te = max(ts + 0.35, grp[-1]["_rel_e"])
        txt = str(grp[0].get("translated_phrase") or " ".join(str(x.get("translated_text") or x.get("word", "")) for x in grp)).strip()
        if txt:
            slots.append({"start": round(ts, 3), "end": round(te, 3), "text": txt})
    return slots, out_words, tr_source


def dub_clip_audio(
    words: List[Dict[str, Any]],
    clip_duration: float,
    output_wav_path: str,
    *,
    clip_start_time: float = 0.0,
    target_language: str = "Spanish",
    voice: str = "default",
    api_key: Optional[str] = None,
    model: Optional[str] = None,
    prefer_online_tts: bool = True,
) -> Dict[str, Any]:
    """
    Generate a full-duration 44.1kHz stereo dubbed voice WAV synchronized to clip phrase windows:
      1. edge_tts when online & installed (best neural clarity)
      2. Offline Windows SAPI5 `SpeechSynthesizer` WAV output
      3. Offline formant speech-cadence WAV synthesizer (guaranteed non-empty offline fallback)
    Applies FFmpeg `atempo` timing alignment + `adelay` placement inside an ASCII temp dir.
    """
    dur = max(0.5, float(clip_duration or 1.0))
    lang_key = str(target_language or "Spanish").strip().lower()
    slots, updated_words, tr_source = _extract_dub_phrases(
        words=words,
        clip_start_time=clip_start_time,
        clip_duration=dur,
        target_language=target_language,
        api_key=api_key,
        model=model,
    )
    if not slots:
        slots = [{"start": 0.1, "end": min(dur, 2.0), "text": "Audio dub ready."}]

    ffmpeg = get_ffmpeg_cmd()
    work_dir = Path(tempfile.mkdtemp(prefix="lost_heaven_dub_"))
    try:
        aligned_segments: List[Tuple[Path, float]] = []
        tts_backend = "formant"
        for idx, sl in enumerate(slots):
            slot_dur = max(0.35, float(sl["end"]) - float(sl["start"]))
            raw_mp3 = work_dir / f"raw_{idx}.mp3"
            raw_wav = work_dir / f"raw_{idx}.wav"
            used_file: Optional[Path] = None

            if prefer_online_tts and _synth_edge_tts(sl["text"], lang_key, voice, raw_mp3):
                used_file = raw_mp3
                tts_backend = "edge_tts"
            elif _synth_windows_sapi(sl["text"], voice, raw_wav):
                used_file = raw_wav
                tts_backend = "windows_sapi"
            else:
                _synth_formant_cadence_wav(sl["text"], slot_dur, voice, raw_wav)
                used_file = raw_wav
                if tts_backend == "formant":
                    tts_backend = "formant"

            # Probe synthesized length and align cleanly into slot_dur using FFmpeg atempo
            probe_cmd = [ffmpeg, "-y", "-i", used_file.name, "-ac", "2", "-ar", "44100", f"norm_{idx}.wav"]
            norm_wav = work_dir / f"norm_{idx}.wav"
            p_norm = subprocess.run(probe_cmd, capture_output=True, text=True, timeout=25, cwd=str(work_dir))
            src_wav = norm_wav if (p_norm.returncode == 0 and norm_wav.exists()) else used_file

            nat_dur = slot_dur
            if src_wav.suffix.lower() == ".wav" and src_wav.exists():
                try:
                    with wave.open(str(src_wav), "rb") as wf:
                        nat_dur = max(0.15, wf.getnframes() / float(max(1, wf.getframerate())))
                except Exception:
                    nat_dur = slot_dur

            aligned_wav = work_dir / f"aligned_{idx}.wav"
            tempo_ratio = nat_dur / slot_dur if slot_dur > 0 else 1.0
            af_chain = (
                f"{_build_atempo_chain(tempo_ratio)},apad=whole_dur={slot_dur:.3f},atrim=0:{slot_dur:.3f}"
                if abs(tempo_ratio - 1.0) > 0.08
                else f"atrim=0:{slot_dur:.3f}"
            )
            cmd_align = [
                ffmpeg, "-y", "-i", src_wav.name,
                "-af", af_chain,
                "-ac", "2", "-ar", "44100",
                aligned_wav.name,
            ]
            p_al = subprocess.run(cmd_align, capture_output=True, text=True, timeout=25, cwd=str(work_dir))
            if p_al.returncode == 0 and aligned_wav.exists():
                aligned_segments.append((aligned_wav, float(sl["start"])))

        out_target = Path(output_wav_path)
        out_target.parent.mkdir(parents=True, exist_ok=True)

        # Build composite timeline track mixing all delayed phrases onto exact clip_duration stereo WAV
        local_merged = work_dir / "dub_master.wav"
        cmd_mix: List[str] = [
            ffmpeg, "-y",
            "-f", "lavfi", "-t", f"{dur:.3f}", "-i", "anullsrc=channel_layout=stereo:sample_rate=44100",
        ]
        fg_parts: List[str] = []
        mix_lbls: List[str] = ["[0:a]"]
        for s_i, (seg_path, start_t) in enumerate(aligned_segments):
            in_idx = s_i + 1
            cmd_mix.extend(["-i", seg_path.name])
            ms = max(0, int(round(start_t * 1000.0)))
            lbl = f"d{s_i}"
            fg_parts.append(f"[{in_idx}:a]adelay={ms}|{ms}[{lbl}]")
            mix_lbls.append(f"[{lbl}]")

        if aligned_segments:
            fg_parts.append(f"{''.join(mix_lbls)}amix=inputs={len(mix_lbls)}:duration=first:normalize=0[aout]")
            fc = ";".join(fg_parts)
            cmd_mix.extend(["-filter_complex", fc, "-map", "[aout]", "-ac", "2", "-ar", "44100", local_merged.name])
        else:
            cmd_mix.extend(["-ac", "2", "-ar", "44100", local_merged.name])

        p_mix = subprocess.run(cmd_mix, capture_output=True, text=True, timeout=45, cwd=str(work_dir))
        if p_mix.returncode != 0 or not local_merged.exists():
            raise RuntimeError(f"FFmpeg dubbing timeline assembly failed: {p_mix.stderr[-300:]}")

        shutil.copy2(local_merged, out_target)
        return {
            "dubbed_audio_path": str(out_target.resolve()),
            "target_language": target_language,
            "voice": voice,
            "tts_backend": tts_backend,
            "translation_source": tr_source,
            "segment_count": len(aligned_segments),
            "words": updated_words,
        }
    finally:
        shutil.rmtree(work_dir, ignore_errors=True)


def build_dubbed_audio_mix_filter(
    base_audio_label: str,
    dub_input_idx: int,
    duration: float,
    bed_volume: float = 0.15,
    out_label: str = "a_dub",
) -> Tuple[str, str]:
    """Duck original voice to `bed_volume` and mix the dubbed WAV track cleanly at full clarity."""
    dur = max(0.5, float(duration or 1.0))
    bed = max(0.0, min(1.0, float(bed_volume)))
    frag = (
        f"[{base_audio_label}]volume={bed:.3f}[a_bed_{dub_input_idx}];"
        f"[{dub_input_idx}:a]aresample=44100,aformat=channel_layouts=stereo,atrim=0:{dur:.3f},asetpts=PTS-STARTPTS[a_dub_in_{dub_input_idx}];"
        f"[a_bed_{dub_input_idx}][a_dub_in_{dub_input_idx}]amix=inputs=2:duration=first:normalize=0[{out_label}]"
    )
    return frag, out_label
