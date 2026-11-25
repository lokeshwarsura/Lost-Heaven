import json
import os
import shutil
import subprocess
import tempfile
import wave
from pathlib import Path

import numpy as np
import pytest
from sqlmodel import SQLModel, Session, create_engine

from app.config import settings
from app.main import get_clip_waveform, trim_clip_endpoint
from app.models import Project, TrimClipRequest, ViralClip
from app.services.preview_frame import render_exact_preview_frame
from app.services.profanity_filter import mask_profane_word, mask_words_profanity
from app.services.proxy import get_ffmpeg_cmd, probe_video
from app.services.renderer import render_clip_ultra
from app.services.timeline_editor import (
    compute_kept_segments_and_remapped_words,
    extract_waveform_bins,
)


def _create_synthetic_video_and_wav(target_dir: Path) -> tuple[str, str]:
    """
    Create a 6.0s 1280x720 MP4 + 16kHz mono WAV with two clear speech bursts separated
    by a 1.1s silent pause [2.2s - 3.3s], inside a directory path with spaces & Unicode.
    """
    target_dir.mkdir(parents=True, exist_ok=True)
    ffmpeg = get_ffmpeg_cmd()
    video_path = target_dir / "source_clip_unicode test.mp4"
    wav_path = target_dir / "audio_16k.wav"

    # Compose 6s audio:
    # [0.0-2.2s]: 320 Hz tone (speech burst 1)
    # [2.2-3.3s]: silence (1.1s gap -> exceeds 0.50s tighten_pacing threshold)
    # [3.3-5.8s]: 440 Hz tone (speech burst 2)
    # [5.8-6.0s]: silence
    audio_expr = (
        "if(lt(t,2.2), 0.35*sin(2*PI*320*t), "
        "if(lt(t,3.3), 0.0, "
        "if(lt(t,5.8), 0.35*sin(2*PI*440*t), 0.0)))"
    )

    cmd_mp4 = [
        ffmpeg,
        "-y",
        "-f", "lavfi", "-i", "testsrc2=size=1280x720:rate=30:duration=6.0",
        "-f", "lavfi", "-i", f"aevalsrc='{audio_expr}':s=44100:d=6.0",
        "-c:v", "libx264", "-preset", "ultrafast", "-pix_fmt", "yuv420p",
        "-c:a", "aac", "-ar", "44100", "-ac", "1",
        str(video_path),
    ]
    res = subprocess.run(cmd_mp4, capture_output=True, text=True, timeout=60)
    assert res.returncode == 0, f"Synthetic MP4 generation failed: {res.stderr}"

    cmd_wav = [
        ffmpeg,
        "-y",
        "-i", str(video_path),
        "-vn", "-ac", "1", "-ar", "16000", "-c:a", "pcm_s16le",
        str(wav_path),
    ]
    res2 = subprocess.run(cmd_wav, capture_output=True, text=True, timeout=30)
    assert res2.returncode == 0, f"Synthetic WAV extraction failed: {res2.stderr}"
    return str(video_path), str(wav_path)


def test_waveform_bins_normalized_and_reflects_silence():
    tmp_root = Path(tempfile.mkdtemp(prefix="lost_heaven_p1_ドキュメント test_"))
    try:
        video_path, wav_path = _create_synthetic_video_and_wav(tmp_root)
        wf = extract_waveform_bins(video_path, clip_start=0.0, clip_end=6.0, num_bins=150, audio_wav_path=wav_path)
        bins = wf["bins"]
        assert len(bins) == 150
        assert all(0.0 <= b <= 1.0 for b in bins)
        # Middle silent bin (~2.75s out of 6.0s -> bin index ~68) should be near zero vs speech bursts
        speech_peak = max(bins[10:40])
        silence_trough = min(bins[62:76])
        assert speech_peak > 0.5
        assert silence_trough < 0.1
    finally:
        shutil.rmtree(tmp_root, ignore_errors=True)


def test_timeline_cuts_tighten_pacing_and_remaps_words():
    words = [
        {"word": "Hello", "start": 0.4, "end": 1.0},
        {"word": "delete_me", "start": 1.1, "end": 2.1, "cut_from_video": True},
        {"word": "world", "start": 3.4, "end": 3.9},
        {"word": "fuck", "start": 4.0, "end": 4.6},
        {"word": "today", "start": 4.7, "end": 5.4},
    ]
    tl = compute_kept_segments_and_remapped_words(
        clip_duration=6.0,
        words=words,
        clip_start_time=0.0,
        tighten_pacing=True,
        silence_threshold=0.50,
        clean_filler_words=False,
    )
    kept_segs = tl["kept_segments"]
    remapped = tl["remapped_words"]
    eff_dur = tl["effective_duration"]
    # Word [1.1, 2.1] is cut AND the remaining gap [2.1, 3.4] exceeds 0.50s, so eff_dur is notably < 6.0s
    assert eff_dur < 4.5
    assert len(kept_segs) >= 2
    assert all(not w.get("cut_from_video") for w in remapped)
    assert [w["word"] for w in remapped] == ["Hello", "world", "fuck", "today"]
    for w in remapped:
        assert 0.0 <= w["start"] < w["end"] <= eff_dur + 0.05


def test_profanity_masking():
    assert mask_profane_word("fuck") == "f***"
    assert mask_profane_word("Shit!") == "S***!"
    assert mask_profane_word("hello") == "hello"
    masked = mask_words_profanity([
        {"word": "holy", "start": 0.1, "end": 0.3},
        {"word": "shit", "start": 0.4, "end": 0.8},
    ])
    assert masked[0]["word"] == "holy"
    assert masked[1]["word"] == "s***"
    assert masked[1]["is_profane"] is True


def test_render_with_video_cuts_tighten_pacing_and_bleep_in_unicode_dir():
    tmp_root = Path(tempfile.mkdtemp(prefix="lost_heaven_rnd_ドキュメント test_"))
    try:
        video_path, _ = _create_synthetic_video_and_wav(tmp_root)
        words = [
            {"word": "Hello", "start": 0.4, "end": 1.0},
            {"word": "badcut", "start": 1.1, "end": 2.0, "cut_from_video": True},
            {"word": "world", "start": 3.4, "end": 3.9},
            {"word": "fuck", "start": 4.0, "end": 4.7},
            {"word": "today", "start": 4.8, "end": 5.5},
        ]

        out_mp4 = render_clip_ultra(
            master_video_path=video_path,
            clip_id=9901,
            project_id=9901,
            start_time=0.0,
            duration=6.0,
            words=words,
            aspect_ratio="9:16",
            frame_layout="auto_track",
            burn_captions=True,
            tighten_pacing=True,
            silence_threshold=0.50,
            censor_profanity=True,
            quality_mode="fast_preview",
        )
        assert os.path.exists(out_mp4)
        meta = probe_video(out_mp4)
        # Expect significant shrinkage because [1.1, 2.0] and [2.0, 3.4] gap (>0.5s) are cut/tightened
        assert meta["duration"] < 4.6, f"Expected trimmed duration < 4.6s, got {meta['duration']}"

        # Verify 1000Hz bleep tone exists at the remapped profanity window
        tl2 = compute_kept_segments_and_remapped_words(
            clip_duration=6.0,
            words=words,
            clip_start_time=0.0,
            tighten_pacing=True,
            silence_threshold=0.50,
        )
        remapped = tl2["remapped_words"]
        prof_word = [w for w in remapped if w["word"] == "fuck"][0]
        check_wav = tmp_root / "rendered_audio.wav"
        ffmpeg = get_ffmpeg_cmd()
        res = subprocess.run(
            [ffmpeg, "-y", "-i", out_mp4, "-vn", "-ac", "1", "-ar", "16000", "-c:a", "pcm_s16le", str(check_wav)],
            capture_output=True, text=True, timeout=30,
        )
        assert res.returncode == 0
        with wave.open(str(check_wav), "rb") as wf:
            sr = wf.getframerate()
            samples = np.frombuffer(wf.readframes(wf.getnframes()), dtype=np.int16).astype(np.float32) / 32768.0
        s_idx = max(0, int((prof_word["start"] + 0.05) * sr))
        e_idx = min(len(samples), int((prof_word["end"] - 0.05) * sr))
        segment = samples[s_idx:e_idx]
        assert segment.size > 200
        spectrum = np.abs(np.fft.rfft(segment * np.hanning(segment.size)))
        freqs = np.fft.rfftfreq(segment.size, d=1.0 / sr)
        peak_freq = float(freqs[int(np.argmax(spectrum))])
        assert abs(peak_freq - 1000.0) < 35.0, f"Expected 1000Hz censor bleep peak, got {peak_freq:.1f}Hz"

        # Preview frame with profanity masking in Unicode path also succeeds
        prev = render_exact_preview_frame(
            video_path=video_path,
            timestamp=4.3,
            burn_captions=True,
            caption_options={"censor_profanity": True},
            transcript_words=words,
            clip_start_time=0.0,
            clip_duration=6.0,
        )
        assert prev["frame_url"].startswith("/media/previews/")
    finally:
        if "out_mp4" in locals() and os.path.exists(out_mp4):
            try:
                os.remove(out_mp4)
            except Exception:
                pass
        shutil.rmtree(tmp_root, ignore_errors=True)


def test_waveform_and_trim_endpoints_with_sqlite():
    tmp_root = Path(tempfile.mkdtemp(prefix="lost_heaven_api_ドキュメント test_"))
    orig_storage = settings.STORAGE_DIR
    try:
        settings.STORAGE_DIR = str(tmp_root / "storage")
        video_path, wav_path = _create_synthetic_video_and_wav(tmp_root / "media")

        engine = create_engine("sqlite:///:memory:")
        SQLModel.metadata.create_all(engine)

        with Session(engine) as session:
            proj = Project(
                title="Phase 1 Trim Test",
                source_path=video_path,
                duration=6.0,
                status="completed",
            )
            session.add(proj)
            session.commit()
            session.refresh(proj)

            proj_dir = Path(settings.STORAGE_DIR) / "projects" / str(proj.id)
            proj_dir.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(wav_path, proj_dir / "audio_16k.wav")

            full_transcript = [
                {"word": "Intro", "start": 0.2, "end": 0.6},
                {"word": "Hello", "start": 1.0, "end": 1.4},
                {"word": "CutThis", "start": 2.0, "end": 2.5},
                {"word": "World", "start": 3.5, "end": 4.0},
                {"word": "Ending", "start": 5.1, "end": 5.6},
            ]
            (proj_dir / "transcript.json").write_text(json.dumps(full_transcript), encoding="utf-8")

            clip = ViralClip(
                project_id=proj.id,
                start_time=1.0,
                end_time=4.2,
                duration=3.2,
                transcript_json=json.dumps([
                    {"word": "Hello", "start": 1.0, "end": 1.4},
                    {"word": "CutThis", "start": 2.0, "end": 2.5, "cut_from_video": True},
                    {"word": "World", "start": 3.5, "end": 4.0, "deleted": True},
                ]),
            )
            session.add(clip)
            session.commit()
            session.refresh(clip)

            wf = get_clip_waveform(project_id=proj.id, clip_id=clip.id, bins=150, session=session)
            assert len(wf["bins"]) == 150

            trimmed = trim_clip_endpoint(
                project_id=proj.id,
                clip_id=clip.id,
                req=TrimClipRequest(start_time=0.0, end_time=5.7),
                session=session,
            )
            assert trimmed["status"] == "trimmed"
            c = trimmed["clip"]
            assert c["start_time"] == 0.0
            assert c["end_time"] == 5.7
            assert abs(c["duration"] - 5.7) < 0.01

            words_by_text = {w["word"]: w for w in c["transcript_words"]}
            assert "Intro" in words_by_text
            assert "Ending" in words_by_text
            assert words_by_text["CutThis"].get("cut_from_video") is True
            assert words_by_text["World"].get("deleted") is True
    finally:
        settings.STORAGE_DIR = orig_storage
        shutil.rmtree(tmp_root, ignore_errors=True)
