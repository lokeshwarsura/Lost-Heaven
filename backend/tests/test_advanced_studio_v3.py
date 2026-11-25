import json
import os
import subprocess
import wave
from pathlib import Path

import numpy as np
import pytest
from PIL import Image
from sqlmodel import Session, SQLModel, create_engine

import app.database as app_db
import app.main as main_mod
from app.config import settings
from app.main import (
    compile_project_trailer_endpoint,
    diarize_clip_endpoint,
    dub_clip_endpoint,
    extract_clip_callouts_endpoint,
    merge_project_clips_endpoint,
    retention_audit_endpoint,
    translate_clip_endpoint,
    update_clip_segments_endpoint,
    update_clip_speakers_endpoint,
)
from app.models import (
    CompileTrailerRequest,
    DiarizeClipRequest,
    DubClipRequest,
    ExtractCalloutsRequest,
    MergeClipsRequest,
    Project,
    TranslateClipRequest,
    UpdateSegmentsRequest,
    UpdateSpeakersRequest,
    ViralClip,
)
from app.services.caption_renderer import render_caption_frame
from app.services.nle_exporter import export_clip_nle
from app.services.proxy import get_ffmpeg_cmd, probe_video
from app.services.renderer import render_clip_ultra
from app.services.timeline_editor import compute_kept_segments_and_remapped_words


def _setup_isolated_db(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    storage_dir = tmp_path / "storage"
    storage_dir.mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr(settings, "STORAGE_DIR", str(storage_dir))
    monkeypatch.setattr(settings, "GEMINI_API_KEY", "")
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    monkeypatch.setattr(main_mod, "get_persisted_settings", lambda: {})

    db_path = tmp_path / "studio_v3.db"
    test_engine = create_engine(
        f"sqlite:///{db_path}",
        connect_args={"check_same_thread": False},
    )
    SQLModel.metadata.create_all(test_engine)
    monkeypatch.setattr(app_db, "engine", test_engine)
    return test_engine, storage_dir


def _create_segmented_av_and_wav(
    target_dir: Path,
    duration_sec: float = 8.0,
    audio_expr: str = "0.35*sin(2*PI*380*t)",
) -> tuple[str, str]:
    target_dir.mkdir(parents=True, exist_ok=True)
    ffmpeg = get_ffmpeg_cmd()
    video_path = target_dir / "master_v3.mp4"
    wav_path = target_dir / "audio_16k.wav"

    cmd_mp4 = [
        ffmpeg,
        "-y",
        "-f", "lavfi", "-i", f"testsrc2=size=640x360:rate=24:duration={duration_sec}",
        "-f", "lavfi", "-i", f"aevalsrc='{audio_expr}':s=44100:d={duration_sec}",
        "-c:v", "libx264", "-preset", "ultrafast", "-pix_fmt", "yuv420p",
        "-c:a", "aac", "-ar", "44100", "-ac", "1",
        str(video_path),
    ]
    res = subprocess.run(cmd_mp4, capture_output=True, text=True, timeout=60)
    assert res.returncode == 0, f"Synthetic MP4 failed: {res.stderr}"

    cmd_wav = [
        ffmpeg,
        "-y",
        "-i", str(video_path),
        "-vn", "-ac", "1", "-ar", "16000", "-c:a", "pcm_s16le",
        str(wav_path),
    ]
    res2 = subprocess.run(cmd_wav, capture_output=True, text=True, timeout=30)
    assert res2.returncode == 0, f"Synthetic WAV failed: {res2.stderr}"
    return str(video_path), str(wav_path)


# ---------------------------------------------------------------------------
# PHASE 1: Multi-Segment Blade Cutter, Clip Merger & Retention Auditor
# ---------------------------------------------------------------------------

def test_phase1_blade_cutter_segments_and_render(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    test_engine, storage_dir = _setup_isolated_db(tmp_path, monkeypatch)
    video_path, _ = _create_segmented_av_and_wav(tmp_path / "media", duration_sec=6.0)

    words = [
        {"word": "First", "start": 0.4, "end": 0.9},
        {"word": "segment", "start": 1.0, "end": 1.6},
        {"word": "Removed", "start": 2.1, "end": 3.4},
        {"word": "Final", "start": 4.2, "end": 4.9},
        {"word": "payoff", "start": 5.0, "end": 5.6},
    ]

    with Session(test_engine) as session:
        project = Project(
            title="Blade Cutter Test",
            source_type="upload",
            source_path=video_path,
            duration=6.0,
            status="finished",
        )
        session.add(project)
        session.commit()
        session.refresh(project)

        clip = ViralClip(
            project_id=project.id,
            title="Full 6s Clip",
            start_time=0.0,
            end_time=6.0,
            duration=6.0,
            viral_score=88,
            aspect_ratio="9:16",
            frame_layout="center",
            hardware_mode="cpu",
        )
        clip.set_transcript(words)
        session.add(clip)
        session.commit()
        session.refresh(clip)

        # Blade cut out the middle [1.8..4.0] -> keep [[0.2, 1.8], [4.0, 5.8]] (~3.4s total)
        res = update_clip_segments_endpoint(
            project.id,
            clip.id,
            UpdateSegmentsRequest(keep_segments=[[0.2, 1.8], [4.0, 5.8]], crossfade_sec=0.03),
            session=session,
        )
        assert res["status"] == "updated"
        assert res["clip"]["keep_segments"] == [[0.2, 1.8], [4.0, 5.8]]
        assert 3.3 <= res["clip"]["duration"] <= 3.5

        session.refresh(clip)
        out_path = str(storage_dir / "blade_out.mp4")
        rendered = render_clip_ultra(
            master_video_path=video_path,
            clip_id=clip.id,
            start_time=clip.start_time,
            duration=float(clip.end_time - clip.start_time),
            words=clip.get_transcript(),
            project_id=project.id,
            aspect_ratio="9:16",
            frame_layout="center",
            burn_captions=True,
            hardware_mode="cpu",
            quality_mode="fast_preview",
            output_path_override=out_path,
            keep_segments=clip.get_keep_segments(),
            merge_crossfade_sec=clip.merge_crossfade_sec,
        )
        assert os.path.exists(rendered)
        probed = probe_video(rendered)
        assert 3.0 <= probed["duration"] <= 3.8

        tl = compute_kept_segments_and_remapped_words(
            words=words,
            clip_start_time=clip.start_time,
            clip_duration=6.0,
            explicit_keep_segments=clip.get_keep_segments(),
            crossfade_sec=clip.merge_crossfade_sec,
        )
        remapped_texts = [w["word"] for w in tl["remapped_words"]]
        assert remapped_texts == ["First", "segment", "Final", "payoff"]
        # "Final" was at 4.2s in source; after keeping 1.6s of seg 1 + 0.2s offset into seg 2 minus 0.03s xfade -> ~1.77s
        final_word = tl["remapped_words"][2]
        assert 1.6 <= final_word["start"] <= 1.95


def test_phase1_merge_clips_endpoint_with_crossfade(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    test_engine, _ = _setup_isolated_db(tmp_path, monkeypatch)
    video_path, _ = _create_segmented_av_and_wav(tmp_path / "media", duration_sec=8.0)

    with Session(test_engine) as session:
        project = Project(
            title="Merge Test",
            source_type="upload",
            source_path=video_path,
            duration=8.0,
            status="finished",
        )
        session.add(project)
        session.commit()
        session.refresh(project)

        clip_a = ViralClip(
            project_id=project.id,
            title="Hook Clip A",
            start_time=0.5,
            end_time=2.5,
            duration=2.0,
            viral_score=85,
            aspect_ratio="9:16",
            frame_layout="center",
            hardware_mode="cpu",
        )
        clip_a.set_transcript([{"word": "Hook", "start": 0.6, "end": 1.1}, {"word": "one", "start": 1.2, "end": 2.0}])

        clip_b = ViralClip(
            project_id=project.id,
            title="Payoff Clip B",
            start_time=5.5,
            end_time=7.5,
            duration=2.0,
            viral_score=91,
            aspect_ratio="9:16",
            frame_layout="center",
            hardware_mode="cpu",
        )
        clip_b.set_transcript([{"word": "Big", "start": 5.6, "end": 6.2}, {"word": "payoff", "start": 6.4, "end": 7.2}])

        session.add(clip_a)
        session.add(clip_b)
        session.commit()
        session.refresh(clip_a)
        session.refresh(clip_b)

        res = merge_project_clips_endpoint(
            project.id,
            MergeClipsRequest(
                clip_ids=[clip_a.id, clip_b.id],
                smooth_transition=True,
                transition_duration=0.35,
            ),
            session=session,
        )
        assert res["status"] == "merged"
        merged_dto = res["clip"]
        assert merged_dto["keep_segments"] == [[0.5, 2.5], [5.5, 7.5]]
        assert pytest.approx(merged_dto["merge_crossfade_sec"], abs=0.01) == 0.35
        assert merged_dto["status"] == "ready"
        assert os.path.exists(merged_dto["rendered_path"])

        probed = probe_video(merged_dto["rendered_path"])
        # 2.0 + 2.0 - 0.35 crossfade = 3.65s
        assert 3.2 <= probed["duration"] <= 4.1

        words_merged = merged_dto["transcript_words"]
        assert [w["word"] for w in words_merged] == ["Hook", "one", "Big", "payoff"]
        assert words_merged[0]["start"] < words_merged[1]["start"] < words_merged[2]["start"] < words_merged[3]["start"]


def test_phase1_retention_audit_detects_issues_and_fixes(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    test_engine, storage_dir = _setup_isolated_db(tmp_path, monkeypatch)
    # 10s video:
    # [0.0-1.6s]: silent slow intro
    # [1.6-3.0s]: speech tone
    # [3.0-5.0s]: 2.0s silent dead air
    # [5.0-10.0s]: speech tone
    audio_expr = (
        "if(lt(t,1.6), 0.0, "
        "if(lt(t,3.0), 0.35*sin(2*PI*360*t), "
        "if(lt(t,5.0), 0.0, 0.35*sin(2*PI*420*t))))"
    )
    video_path, wav_path = _create_segmented_av_and_wav(
        tmp_path / "media",
        duration_sec=10.0,
        audio_expr=audio_expr,
    )

    words = [
        {"word": "Finally", "start": 1.65, "end": 2.1},
        {"word": "talking", "start": 2.2, "end": 2.8},
        # 2.2s dead-air pause [2.8 .. 5.0]
        {"word": "Later", "start": 5.05, "end": 5.5},
        {"word": "still", "start": 6.4, "end": 6.9},
        {"word": "going", "start": 7.8, "end": 8.3},
        {"word": "done", "start": 9.1, "end": 9.7},
    ]

    with Session(test_engine) as session:
        project = Project(
            title="Audit Test",
            source_type="upload",
            source_path=video_path,
            duration=10.0,
            status="finished",
        )
        session.add(project)
        session.commit()
        session.refresh(project)

        proj_dir = storage_dir / "projects" / str(project.id)
        proj_dir.mkdir(parents=True, exist_ok=True)
        (proj_dir / "audio.wav").write_bytes(Path(wav_path).read_bytes())

        clip = ViralClip(
            project_id=project.id,
            title="Slow Intro Clip",
            start_time=0.0,
            end_time=10.0,
            duration=10.0,
            viral_score=72,
            zoom=1.0,
            dynamic_punch_zoom=False,
        )
        clip.set_transcript(words)
        session.add(clip)
        session.commit()
        session.refresh(clip)

        report = retention_audit_endpoint(project.id, clip.id, session=session)
        assert report["clip_id"] == clip.id
        assert len(report["retention_curve"]) == 10
        assert report["time_to_first_word"] >= 1.5
        assert report["dead_air_count"] >= 1

        warning_ids = [w["id"] for w in report["drop_off_warnings"]]
        assert "slow_hook_intro" in warning_ids
        assert any(wid.startswith("dead_air_") for wid in warning_ids)
        assert any(wid.startswith("static_visual_") for wid in warning_ids)

        slow_intro = next(w for w in report["drop_off_warnings"] if w["id"] == "slow_hook_intro")
        assert slow_intro["fix_action"]["type"] == "set_segments"
        fixed = update_clip_segments_endpoint(
            project.id,
            clip.id,
            UpdateSegmentsRequest(keep_segments=slow_intro["fix_action"]["keep_segments"]),
            session=session,
        )
        assert fixed["clip"]["start_time"] >= 1.4


# ---------------------------------------------------------------------------
# PHASE 2: Local Speaker Diarization, Face-Safe Captions & Smart Callout Cards
# ---------------------------------------------------------------------------

def _write_two_speaker_wav(out_path: Path) -> Path:
    """Create a 5.0s 16kHz WAV with distinct male-like (115Hz) and bright female-like (245Hz) turns."""
    out_path.parent.mkdir(parents=True, exist_ok=True)
    sr = 16000
    t = np.linspace(0.0, 5.0, int(5.0 * sr), endpoint=False)
    sig = np.zeros_like(t)
    # Turn 1 [0.1..2.3s]: deep fundamental + warm harmonics
    m1 = (t >= 0.1) & (t <= 2.3)
    sig[m1] = 0.45 * np.sin(2 * np.pi * 115.0 * t[m1]) + 0.18 * np.sin(2 * np.pi * 230.0 * t[m1])
    # Turn 2 [2.6..4.8s]: higher pitch + richer upper harmonics
    m2 = (t >= 2.6) & (t <= 4.8)
    sig[m2] = (
        0.35 * np.sin(2 * np.pi * 245.0 * t[m2])
        + 0.28 * np.sin(2 * np.pi * 980.0 * t[m2])
        + 0.20 * np.sin(2 * np.pi * 2400.0 * t[m2])
    )
    pcm = np.clip(sig * 32000.0, -32767, 32767).astype(np.int16)
    with wave.open(str(out_path), "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(sr)
        wf.writeframes(pcm.tobytes())
    return out_path


def test_phase2_local_speaker_diarization_rename_and_lower_third(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    test_engine, storage_dir = _setup_isolated_db(tmp_path, monkeypatch)
    video_path, _ = _create_segmented_av_and_wav(tmp_path / "media", duration_sec=5.0)
    words = [
        {"word": "First", "start": 0.2, "end": 0.7},
        {"word": "speaker", "start": 0.8, "end": 1.4},
        {"word": "talking", "start": 1.5, "end": 2.1},
        {"word": "Second", "start": 2.7, "end": 3.3},
        {"word": "speaker", "start": 3.4, "end": 4.0},
        {"word": "replies", "start": 4.1, "end": 4.7},
    ]

    with Session(test_engine) as session:
        project = Project(
            title="Diarization Test",
            source_type="upload",
            source_path=video_path,
            duration=5.0,
            status="finished",
        )
        session.add(project)
        session.commit()
        session.refresh(project)

        proj_dir = storage_dir / "projects" / str(project.id)
        _write_two_speaker_wav(proj_dir / "audio_16k.wav")

        clip = ViralClip(
            project_id=project.id,
            title="Two Speaker Clip",
            start_time=0.0,
            end_time=5.0,
            duration=5.0,
        )
        clip.set_transcript(words)
        session.add(clip)
        session.commit()
        session.refresh(clip)

        # Save distinct spatial face_track keyframes as well (left -> right switch)
        clip_dir = proj_dir / f"clip_{clip.id}"
        clip_dir.mkdir(parents=True, exist_ok=True)
        (clip_dir / "face_track.json").write_text(
            json.dumps({
                "keyframes": [
                    {"t": 0.5, "cx": 0.25, "cy": 0.4, "face_h": 0.26},
                    {"t": 1.8, "cx": 0.25, "cy": 0.4, "face_h": 0.26},
                    {"t": 2.9, "cx": 0.75, "cy": 0.4, "face_h": 0.26},
                    {"t": 4.2, "cx": 0.75, "cy": 0.4, "face_h": 0.26},
                ]
            }),
            encoding="utf-8",
        )

        res = diarize_clip_endpoint(project.id, clip.id, DiarizeClipRequest(num_speakers=2), session=session)
        assert res["status"] == "diarized"
        assert res["speaker_count"] == 2
        w_out = res["words"]
        assert len({w["speaker_id"] for w in w_out[:3]}) == 1
        assert len({w["speaker_id"] for w in w_out[3:]}) == 1
        assert w_out[0]["speaker_id"] != w_out[4]["speaker_id"]

        # Rename Speaker 1 -> Alex & verify persistence + nameplate rendering
        speakers_edited = dict(res["speakers"])
        speakers_edited["S1"] = {**speakers_edited["S1"], "name": "Alex", "color": "#FF5500"}
        saved = update_clip_speakers_endpoint(
            project.id,
            clip.id,
            UpdateSpeakersRequest(speakers=speakers_edited),
            session=session,
        )
        assert saved["clip"]["speakers"]["S1"]["name"] == "Alex"

        img_plain = render_caption_frame(
            t=0.4, width=720, height=1280, words=w_out,
            speakers=speakers_edited, speaker_lower_thirds=False, clip_duration=5.0,
        )
        img_badge = render_caption_frame(
            t=0.4, width=720, height=1280, words=w_out,
            speakers=speakers_edited, speaker_lower_thirds=True, clip_duration=5.0,
        )
        assert img_plain.tobytes() != img_badge.tobytes()
        # Verify S1's custom accent color (#FF5500) appears on active caption/badge pixels
        arr = np.array(img_badge)
        orange_mask = (arr[:, :, 0] > 230) & (arr[:, :, 1] < 120) & (arr[:, :, 2] < 40) & (arr[:, :, 3] > 200)
        assert int(np.sum(orange_mask)) > 50


def test_phase2_face_safe_caption_placement_avoids_face_bbox():
    words = [{"word": "LOOK", "start": 0.2, "end": 1.2}, {"word": "HERE", "start": 1.2, "end": 2.0}]
    # Default position_y=78% puts caption right inside y=[70%..88%]. Place face directly at cy=0.78!
    face_kfs = [{"t": 0.5, "cx": 0.5, "cy": 0.78, "face_h": 0.24}]

    img_unsafe = render_caption_frame(
        t=0.5, width=540, height=960, words=words, pos_y=78.0,
        face_safe_captions=False, face_keyframes=face_kfs, clip_duration=2.5,
    )
    img_safe = render_caption_frame(
        t=0.5, width=540, height=960, words=words, pos_y=78.0,
        face_safe_captions=True, face_keyframes=face_kfs, clip_duration=2.5,
    )

    arr_unsafe = np.array(img_unsafe)
    arr_safe = np.array(img_safe)
    # Center of blocked bottom zone (y=720..780 out of 960): unsafe draws right over face, safe clears it
    unsafe_face_zone = int(np.sum(arr_unsafe[710:785, :, 3] > 20))
    safe_face_zone = int(np.sum(arr_safe[710:785, :, 3] > 20))
    safe_upper_zone = int(np.sum(arr_safe[200:600, :, 3] > 20))
    assert unsafe_face_zone > 500
    assert safe_face_zone == 0
    assert safe_upper_zone > 500


def test_phase2_callout_cards_extract_render_and_sfx(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    test_engine, storage_dir = _setup_isolated_db(tmp_path, monkeypatch)
    video_path, _ = _create_segmented_av_and_wav(tmp_path / "media", duration_sec=6.0)
    words = [
        {"word": "We", "start": 0.3, "end": 0.6},
        {"word": "made", "start": 0.6, "end": 0.9},
        {"word": "$5M", "start": 1.0, "end": 1.6},
        {"word": "and", "start": 1.7, "end": 2.0},
        {"word": "grew", "start": 2.1, "end": 2.5},
        {"word": "250%", "start": 3.8, "end": 4.4},
        {"word": "faster", "start": 4.5, "end": 5.2},
    ]

    with Session(test_engine) as session:
        project = Project(
            title="Callouts Test",
            source_type="upload",
            source_path=video_path,
            duration=6.0,
            status="finished",
        )
        session.add(project)
        session.commit()
        session.refresh(project)

        clip = ViralClip(
            project_id=project.id,
            title="Stats Clip",
            start_time=0.0,
            end_time=6.0,
            duration=6.0,
            aspect_ratio="9:16",
            frame_layout="center",
            hardware_mode="cpu",
        )
        clip.set_transcript(words)
        session.add(clip)
        session.commit()
        session.refresh(clip)

        res = extract_clip_callouts_endpoint(project.id, clip.id, ExtractCalloutsRequest(max_cards=3), session=session)
        assert res["status"] == "generated"
        cards = res["callout_cards"]
        assert len(cards) >= 2
        badges = [c["badge"] for c in cards]
        assert any("$5M" in b for b in badges)
        assert any("250%" in b for b in badges)

        img_no_card = render_caption_frame(
            t=1.2, width=540, height=960, words=words,
            auto_callouts=False, callout_cards=cards, clip_duration=6.0,
        )
        img_with_card = render_caption_frame(
            t=1.2, width=540, height=960, words=words,
            auto_callouts=True, callout_cards=cards, clip_duration=6.0,
        )
        assert img_no_card.tobytes() != img_with_card.tobytes()

        out_path = str(storage_dir / "callout_sfx_out.mp4")
        rendered = render_clip_ultra(
            master_video_path=video_path,
            clip_id=clip.id,
            start_time=0.0,
            duration=6.0,
            words=words,
            project_id=project.id,
            aspect_ratio="9:16",
            frame_layout="center",
            burn_captions=True,
            auto_callouts=True,
            callout_cards=cards,
            hardware_mode="cpu",
            quality_mode="fast_preview",
            output_path_override=out_path,
        )
        assert os.path.exists(rendered)
        probed = probe_video(rendered)
        assert probed["duration"] >= 5.2
        assert probed["has_audio"] is True


# ---------------------------------------------------------------------------
# PHASE 3: Bilingual Stacked Captions, 1-Click Voice Dubbing & Trailer Compiler
# ---------------------------------------------------------------------------

def test_phase3_bilingual_stacked_captions_and_dual_srt(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    test_engine, _ = _setup_isolated_db(tmp_path, monkeypatch)
    video_path, _ = _create_segmented_av_and_wav(tmp_path / "media", duration_sec=4.0)
    words = [
        {"word": "Hello", "start": 0.2, "end": 0.7},
        {"word": "world", "start": 0.8, "end": 1.4},
        {"word": "today", "start": 1.5, "end": 2.2},
        {"word": "money", "start": 2.3, "end": 3.0},
    ]

    with Session(test_engine) as session:
        project = Project(
            title="Bilingual Test",
            source_type="upload",
            source_path=video_path,
            duration=4.0,
            status="finished",
        )
        session.add(project)
        session.commit()
        session.refresh(project)

        clip = ViralClip(
            project_id=project.id,
            title="Bilingual Clip",
            start_time=0.0,
            end_time=4.0,
            duration=4.0,
        )
        clip.set_transcript(words)
        session.add(clip)
        session.commit()
        session.refresh(clip)

        res = translate_clip_endpoint(
            project.id,
            clip.id,
            TranslateClipRequest(target_language="Spanish"),
            session=session,
        )
        assert res["status"] == "translated"
        assert res["target_language"] == "Spanish"
        assert res["clip"]["bilingual_enabled"] is True
        tr_words = res["clip"]["transcript_words"]
        assert "hola" in tr_words[0]["translated_phrase"].lower()
        assert "dinero" in tr_words[3]["translated_phrase"].lower()

        img_single = render_caption_frame(
            t=0.5, width=540, height=960, words=tr_words,
            bilingual_enabled=False, clip_duration=4.0,
        )
        img_dual = render_caption_frame(
            t=0.5, width=540, height=960, words=tr_words,
            bilingual_enabled=True, secondary_language="Spanish", clip_duration=4.0,
        )
        assert img_single.tobytes() != img_dual.tobytes()

        session.refresh(clip)
        srt_bundle = export_clip_nle(clip, project, "srt")
        srt_text = srt_bundle["content"].decode("utf-8")
        assert "Hello world" in srt_text
        assert "Hola mundo" in srt_text


def test_phase3_local_voice_dubbing_atempo_and_mp4_mix(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    test_engine, storage_dir = _setup_isolated_db(tmp_path, monkeypatch)
    video_path, _ = _create_segmented_av_and_wav(tmp_path / "media", duration_sec=4.0)
    words = [
        {"word": "Hello", "start": 0.3, "end": 0.8},
        {"word": "world", "start": 0.9, "end": 1.6},
        {"word": "today", "start": 2.0, "end": 3.2},
    ]

    with Session(test_engine) as session:
        project = Project(
            title="Dubbing Test",
            source_type="upload",
            source_path=video_path,
            duration=4.0,
            status="finished",
        )
        session.add(project)
        session.commit()
        session.refresh(project)

        clip = ViralClip(
            project_id=project.id,
            title="Dub Clip",
            start_time=0.0,
            end_time=4.0,
            duration=4.0,
            aspect_ratio="9:16",
            frame_layout="center",
            hardware_mode="cpu",
        )
        clip.set_transcript(words)
        session.add(clip)
        session.commit()
        session.refresh(clip)

        res = dub_clip_endpoint(
            project.id,
            clip.id,
            DubClipRequest(target_language="Spanish", voice="female", original_bed_volume=0.15),
            session=session,
        )
        assert res["status"] == "dubbed"
        assert res["tts_backend"] in {"edge_tts", "windows_sapi", "formant"}
        dub_wav = Path(res["dubbed_audio_path"])
        assert dub_wav.exists()
        with wave.open(str(dub_wav), "rb") as wf:
            assert wf.getnframes() > 44100 * 3

        out_path = str(storage_dir / "dubbed_final.mp4")
        rendered = render_clip_ultra(
            master_video_path=video_path,
            clip_id=clip.id,
            start_time=0.0,
            duration=4.0,
            words=res["clip"]["transcript_words"],
            project_id=project.id,
            aspect_ratio="9:16",
            frame_layout="center",
            burn_captions=True,
            dubbing_enabled=True,
            dubbed_audio_path=str(dub_wav),
            dubbing_bed_volume=0.15,
            hardware_mode="cpu",
            quality_mode="fast_preview",
            output_path_override=out_path,
        )
        assert os.path.exists(rendered)
        probed = probe_video(rendered)
        assert probed["duration"] >= 3.5
        assert probed["has_audio"] is True


def test_phase3_compile_viral_trailer_endpoint_and_render(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    test_engine, _ = _setup_isolated_db(tmp_path, monkeypatch)
    video_path, _ = _create_segmented_av_and_wav(tmp_path / "media", duration_sec=9.0)

    with Session(test_engine) as session:
        project = Project(
            title="Trailer Test",
            source_type="upload",
            source_path=video_path,
            duration=9.0,
            status="finished",
        )
        session.add(project)
        session.commit()
        session.refresh(project)

        specs = [
            (0.4, 2.6, 84, [{"word": "First", "start": 0.5, "end": 1.1}, {"word": "moment", "start": 1.2, "end": 2.0}]),
            (3.2, 5.4, 96, [{"word": "Peak", "start": 3.3, "end": 4.0}, {"word": "opener", "start": 4.1, "end": 5.0}]),
            (6.2, 8.6, 91, [{"word": "Big", "start": 6.3, "end": 7.0}, {"word": "climax", "start": 7.2, "end": 8.2}]),
        ]
        for idx, (s, e, score, cwords) in enumerate(specs):
            c = ViralClip(
                project_id=project.id,
                title=f"Moment {idx + 1}",
                start_time=s,
                end_time=e,
                duration=round(e - s, 2),
                virality_score=score,
                aspect_ratio="9:16",
                frame_layout="center",
                hardware_mode="cpu",
            )
            c.set_transcript(cwords)
            session.add(c)
        session.commit()

        res = compile_project_trailer_endpoint(
            project.id,
            CompileTrailerRequest(max_moments=3, target_duration_sec=10.0),
            session=session,
        )
        assert res["status"] == "compiled"
        tr_clip = res["clip"]
        assert len(tr_clip["keep_segments"]) == 3
        assert tr_clip["analysis_source"] == "supercut_trailer"
        assert tr_clip["status"] == "ready"
        assert os.path.exists(tr_clip["rendered_path"])

        probed = probe_video(tr_clip["rendered_path"])
        assert 5.5 <= probed["duration"] <= 7.8
        assert probed["has_audio"] is True
        words_tr = tr_clip["transcript_words"]
        assert len(words_tr) == 6
        assert words_tr[0]["word"] == "Peak"
        assert words_tr[-1]["word"] == "climax"

