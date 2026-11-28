import json
import subprocess
from pathlib import Path
from typing import Dict, List

import cv2
import numpy as np
import pytest
from sqlmodel import SQLModel, Session, create_engine

import app.main as main_mod
from app.config import settings
from app.models import Project, ViralClip
from app.services.gemini_clipper import build_gemini_prompt
from app.services.multimodal_fusion import diagnose_clip_retention
from app.services.offline_clipper import NARRATIVE_ARCS, classify_narrative_arc
from app.services.proxy import get_ffmpeg_cmd


def _create_hud_test_video(out_path: Path, duration_sec: float = 6.0) -> Path:
    """Create a 6-second H.264 MP4 with burned-in lower-third subtitle text and AAC audio."""
    w, h = 426, 240
    fps = 4
    n_frames = int(duration_sec * fps)
    raw = bytearray()
    for k in range(n_frames):
        fr = np.full((h, w, 3), 36, dtype=np.uint8)
        cv2.circle(fr, (213, 88), 32, (180, 150, 130), -1)
        cv2.rectangle(fr, (40, 174), (386, 214), (8, 8, 8), -1)
        cv2.putText(fr, f"SUBTITLE TEXT ROW {k + 1}", (52, 201), cv2.FONT_HERSHEY_SIMPLEX, 0.62, (255, 255, 255), 2)
        raw.extend(fr.tobytes())

    out_path.parent.mkdir(parents=True, exist_ok=True)
    raw_mp4 = out_path.parent / "raw_noaud.mp4"
    p1 = subprocess.run(
        [
            get_ffmpeg_cmd(), "-y",
            "-f", "rawvideo", "-vcodec", "rawvideo", "-pix_fmt", "bgr24",
            "-s", f"{w}x{h}", "-r", str(fps), "-i", "-",
            "-f", "lavfi", "-i", f"sine=frequency=160:sample_rate=16000:duration={duration_sec}",
            "-c:v", "libx264", "-preset", "ultrafast", "-pix_fmt", "yuv420p",
            "-c:a", "aac", "-shortest", str(out_path),
        ],
        input=bytes(raw),
        capture_output=True,
        timeout=60,
    )
    assert p1.returncode == 0, p1.stderr.decode(errors="ignore")
    return out_path


def test_phase3_diagnose_clip_retention_risk_zones_grade_and_fixes():
    # Construct 10s multimodal timeline: first 5s static/flat/hesitant (risk zone), last 5s energetic peak
    tl = []
    for i in range(40):
        t = round(i * 0.25, 2)
        if t < 5.0:
            tl.append({
                "t": t,
                "combined_engagement_score": 28.0,
                "audio_prosody_score": 30.0,
                "visual_emotion_action_score": 20.0,
                "visual_novelty_score": 10.0,
                "fused_arousal": 22.0,
                "fluency_score": 45.0,
                "eye_contact_score": 55.0,
                "face_count": 1,
                "speaker_id": "Speaker A",
                "bottom_hud_occupied": True,
                "top_hud_occupied": False,
            })
        else:
            tl.append({
                "t": t,
                "combined_engagement_score": 82.0,
                "audio_prosody_score": 80.0,
                "visual_emotion_action_score": 78.0,
                "visual_novelty_score": 65.0,
                "fused_arousal": 72.0,
                "fluency_score": 92.0,
                "eye_contact_score": 90.0,
                "face_count": 1,
                "speaker_id": "Speaker B" if t >= 7.5 else "Speaker A",
                "bottom_hud_occupied": True,
                "top_hud_occupied": False,
            })

    mm_ctx = {
        "summary": {
            "step_sec": 0.25,
            "duration": 10.0,
            "has_existing_music": True,
            "avg_music_intensity": 42.0,
        },
        "speaker_diarization": {
            "speaker_count": 2,
            "dominance_ratios": {"Speaker A": 0.75, "Speaker B": 0.25},
            "crosstalk_overlaps": [{"t": 6.5, "end": 7.25, "confidence": 0.88}],
        },
        "speech_fluency": {
            "avg_fluency_score": 68.5,
            "filler_count": 4,
            "clean_hook_candidates": [{"t": 5.0, "end": 8.0, "text": "Clean peak!", "fluency_score": 92.0}],
            "hesitation_intervals": [{"t": 1.0, "end": 2.8, "duration": 1.8, "reason": "mid_speech_stall"}],
        },
        "music_bed_analysis": {"has_existing_music": True, "avg_music_intensity": 42.0, "estimated_bpm": 120.0},
        "obstruction_zones": {"top_hud_occupied": False, "bottom_hud_occupied": True, "recommended_caption_y": 0.22},
        "peak_moments": [{"t": 7.5, "combined_score": 84.0, "reason": "Vocal Peak"}],
        "multimodal_timeline": tl,
    }

    diag = diagnose_clip_retention(
        mm_ctx,
        0.0,
        10.0,
        current_caption_y=0.78,
        dynamic_punch_zoom=False,
        auto_trim_dead_air=False,
    )
    assert diag["retention_grade"] in ("S", "A", "B", "C")
    assert diag["risk_zone_count"] >= 1
    assert diag["risk_zones"][0]["duration"] >= 4.0
    fix_ids = {f["id"] for f in diag["smart_fixes"]}
    assert "shift_captions_clear_hud" in fix_ids
    assert "enable_punch_zoom_on_peaks" in fix_ids
    assert "trim_hesitation_dead_air" in fix_ids
    assert diag["speaker_breakdown"]["crosstalk_count"] == 1
    assert diag["music_bed_summary"]["has_existing_music"] is True
    assert diag["hud_obstruction"]["hud_collision"] is True

    # Offline 6th narrative arc & Gemini prompt enrichment
    assert "crosstalk_debate_clash" in NARRATIVE_ARCS
    arc_res = classify_narrative_arc(
        {"crosstalk_bins": 3, "speaker_count": 2, "dominant_vocal_emotion": "urgent_tense", "audio_score": 78.0},
        "Wait let me finish!",
        speaker_switch_rate=2.0,
    )
    assert arc_res["narrative_arc"] == "crosstalk_debate_clash"

    prompt = build_gemini_prompt("30-58s", 3, multimodal_context=mm_ctx)
    assert "Cross-Talk Debate Overlaps" in prompt
    assert "Clean Hesitation-Free Hook Openings" in prompt
    assert "Existing Music Bed Detected: True" in prompt
    assert "Safe Caption Y (HUD Check): 0.22" in prompt


def test_phase3_best_frames_and_apply_retention_fixes_end_to_end(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    storage_dir = tmp_path / "storage"
    for sub in ("assets/best_frames", "previews", "projects", "renders", "temp"):
        (storage_dir / sub).mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr(settings, "STORAGE_DIR", str(storage_dir))

    video_path = _create_hud_test_video(tmp_path / "hud_debate.mp4", duration_sec=6.0)
    db_file = tmp_path / "phase3_e2e.db"
    local_engine = create_engine(f"sqlite:///{db_file.as_posix()}", connect_args={"check_same_thread": False})
    SQLModel.metadata.create_all(local_engine)

    # Inject multimodal timeline spanning a hesitation gap > 1.2s + bottom HUD collision at y=0.78
    mm_tl = []
    for i in range(24):
        t = round(i * 0.25, 2)
        is_risk = t < 4.25
        mm_tl.append({
            "t": t,
            "combined_engagement_score": 30.0 if is_risk else 82.0,
            "audio_prosody_score": 28.0 if is_risk else 84.0,
            "visual_emotion_action_score": 24.0 if is_risk else 76.0,
            "sharpness_score": 78.0,
            "exposure_score": 74.0,
            "emotion_intensity": 20.0 if is_risk else 72.0,
            "action_score": 18.0 if is_risk else 65.0,
            "face_count": 1,
            "eyes_open_confidence": 0.92,
            "dominant_emotion": "neutral" if is_risk else "surprise",
            "vocal_emotion": "calm_storytelling" if is_risk else "excited_hype",
            "fused_arousal": 22.0 if is_risk else 74.0,
            "fused_valence": 0.0 if is_risk else 0.45,
            "visual_novelty_score": 12.0 if is_risk else 58.0,
            "eye_contact_score": 68.0 if is_risk else 92.0,
            "gaze_engagement_score": 70.0 if is_risk else 94.0,
            "fluency_score": 48.0 if is_risk else 93.0,
            "speaker_id": "Speaker A" if t < 3.0 else "Speaker B",
            "is_crosstalk": bool(4.5 <= t <= 5.0),
            "bottom_hud_occupied": True,
            "top_hud_occupied": False,
        })
    mm_payload = {
        "version": 2,
        "summary": {
            "duration": 6.0,
            "step_sec": 0.25,
            "overall_engagement_score": 62.0,
            "integrated_lufs": -16.0,
            "lra": 4.2,
            "true_peak_db": -1.5,
            "avg_wpm": 148.0,
            "avg_sharpness": 78.0,
            "avg_exposure": 74.0,
            "avg_action": 40.0,
            "face_presence_ratio": 1.0,
            "scene_cut_count": 0,
            "black_interval_count": 0,
            "speaker_count": 2,
            "dominance_ratios": {"Speaker A": 0.50, "Speaker B": 0.50},
            "crosstalk_count": 1,
            "avg_fluency_score": 68.0,
            "avg_eye_contact_score": 80.0,
            "avg_visual_novelty": 34.0,
            "has_existing_music": False,
            "top_hud_occupied": False,
            "bottom_hud_occupied": True,
            "recommended_caption_y": 0.22,
        },
        "emotion_summary": {"dominant_facial_emotion": "surprise", "dominant_vocal_emotion": "excited_hype", "peak_arousal": 74.0, "avg_valence": 0.2},
        "emotion_shifts": [],
        "acoustic_events": [],
        "speaker_diarization": {
            "speaker_count": 2,
            "dominance_ratios": {"Speaker A": 0.50, "Speaker B": 0.50},
            "crosstalk_overlaps": [{"t": 4.5, "end": 5.0, "confidence": 0.85}],
        },
        "speech_fluency": {
            "avg_fluency_score": 68.0,
            "filler_count": 3,
            "clean_hook_candidates": [{"t": 4.25, "end": 5.8, "text": "Huge breakthrough!", "fluency_score": 93.0}],
            "hesitation_intervals": [{"t": 0.5, "end": 2.3, "duration": 1.8}],
        },
        "music_bed_analysis": {"has_existing_music": False, "avg_music_intensity": 0.0, "estimated_bpm": 0.0},
        "obstruction_zones": {"top_hud_occupied": False, "bottom_hud_occupied": True, "recommended_caption_y": 0.22},
        "scene_cuts": [],
        "black_intervals": [],
        "peak_moments": [{"t": 4.75, "combined_score": 82.0, "reason": "Vocal Peak"}],
        "multimodal_timeline": mm_tl,
    }

    with Session(local_engine) as session:
        proj = Project(
            title="Retention E2E Project",
            source_path=str(video_path),
            status="completed",
            duration=6.0,
        )
        proj.set_multimodal_analysis(mm_payload)
        session.add(proj)
        session.commit()
        session.refresh(proj)

        clip = ViralClip(
            project_id=proj.id,
            start_time=0.0,
            end_time=6.0,
            duration=6.0,
            position_x=50.0,
            position_y=78.0,
            dynamic_punch_zoom=False,
            auto_trim_dead_air=False,
        )
        clip.set_transcript([
            {"word": "Um", "start": 0.2, "end": 0.5},
            {"word": "Breakthrough", "start": 2.4, "end": 3.0},
            {"word": "moment!", "start": 3.1, "end": 3.8},
        ])
        session.add(clip)
        session.commit()
        session.refresh(clip)

        bf_res = main_mod.get_clip_best_frames_endpoint(proj.id, clip.id, session=session)
        assert "retention_diagnostics" in bf_res
        diag_before = bf_res["retention_diagnostics"]
        assert diag_before["hud_obstruction"]["hud_collision"] is True
        assert {f["id"] for f in diag_before["smart_fixes"]} >= {
            "shift_captions_clear_hud",
            "enable_punch_zoom_on_peaks",
            "trim_hesitation_dead_air",
        }

        fix_res = main_mod.apply_retention_fixes_endpoint(proj.id, clip.id, session=session)
        assert fix_res["status"] == "applied"
        assert set(fix_res["applied_fixes"]) == {
            "shift_captions_clear_hud",
            "enable_punch_zoom_on_peaks",
            "trim_hesitation_dead_air",
        }
        updated_clip = fix_res["clip"]
        assert updated_clip["caption_y"] <= 0.30
        assert updated_clip["dynamic_punch_zoom"] is True
        assert updated_clip["auto_trim_dead_air"] is True

        diag_after = fix_res["retention_diagnostics"]
        assert diag_after["hud_obstruction"]["hud_collision"] is False
        assert len(diag_after["smart_fixes"]) == 0
        assert diag_after["retention_score"] >= diag_before["retention_score"]
