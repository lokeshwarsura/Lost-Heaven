import math
import subprocess
from pathlib import Path

import cv2
import numpy as np
import pytest

from app.services.emotion_analyzer import EMOTION_LABELS, analyze_facial_emotions
from app.services.video_analyzer import analyze_video_track, classify_shot_scale, compute_graphic_event_score
from app.services.proxy import get_ffmpeg_cmd


def _synthetic_landmarks(jaw_open: float = 0.0, smile: float = 0.0, eye_wide: float = 0.0, brow_up: float = 0.0):
    """Build 468 normalized MediaPipe-topology pseudo-landmarks cleanly for geometric fallback tests."""
    pts = [(0.5, 0.5) for _ in range(478)]
    pts[10] = (0.50, 0.15)    # forehead
    pts[152] = (0.50, 0.85)   # chin (fh = 0.70)
    pts[33] = (0.35, 0.42)    # left outer eye
    pts[263] = (0.65, 0.42)   # right outer eye
    pts[133] = (0.43, 0.42)   # left inner eye
    pts[362] = (0.57, 0.42)   # right inner eye
    pts[159] = (0.39, 0.40 - eye_wide * 0.025)
    pts[145] = (0.39, 0.43 + eye_wide * 0.025)
    pts[386] = (0.61, 0.40 - eye_wide * 0.025)
    pts[374] = (0.61, 0.43 + eye_wide * 0.025)
    pts[107] = (0.44, 0.34 - brow_up * 0.04)
    pts[336] = (0.56, 0.34 - brow_up * 0.04)
    pts[70] = (0.33, 0.35 - brow_up * 0.04)
    pts[300] = (0.67, 0.35 - brow_up * 0.04)
    pts[13] = (0.50, 0.64)
    pts[14] = (0.50, 0.65 + jaw_open * 0.12)
    mouth_y = 0.645 - smile * 0.04
    pts[61] = (0.41, mouth_y)
    pts[291] = (0.59, mouth_y)
    return pts


def test_blendshapes_classify_emotions_affect_and_micro_expression_delta():
    # 1. Neutral baseline
    r_neut = analyze_facial_emotions(landmarks=None, blendshapes={}, prev_state=None, timestamp=0.0)
    assert r_neut["dominant_emotion"] == "neutral"
    assert abs(sum(r_neut["emotion_scores"].values()) - 1.0) < 1e-3
    assert abs(r_neut["valence"]) < 0.25
    assert r_neut["arousal"] < 15.0

    # 2. Sudden Surprise spike right after Neutral (within 0.5s <= 0.8s window)
    bs_surprise = {
        "jawOpen": 0.86,
        "eyeWideLeft": 0.82,
        "eyeWideRight": 0.80,
        "browInnerUp": 0.72,
        "browOuterUpLeft": 0.68,
        "browOuterUpRight": 0.68,
    }
    # Step 2 consecutive frames (EMA alpha=0.35 catches transition while rejecting 1-frame blips)
    state = {"ema_probs": dict(r_neut["emotion_scores"]), "history": list(r_neut["state"]["history"])}
    r_surp1 = analyze_facial_emotions(None, bs_surprise, prev_state=state, timestamp=0.25)
    assert r_surp1["emotion_delta"] >= 26.0, f"Expected sharp neutral->surprise spike, got {r_surp1['emotion_delta']}"
    assert r_surp1["is_micro_expression_spike"] is True

    r_surp2 = analyze_facial_emotions(None, bs_surprise, prev_state=state, timestamp=0.50)
    assert r_surp2["dominant_emotion"] == "surprise"
    assert r_surp2["arousal"] >= 50.0

    # 3. Sustained Joy
    bs_joy = {"mouthSmileLeft": 0.90, "mouthSmileRight": 0.88, "cheekSquintLeft": 0.75, "cheekSquintRight": 0.72}
    joy_state: dict = {}
    for step in range(5):
        r_joy = analyze_facial_emotions(None, bs_joy, prev_state=joy_state, timestamp=step * 0.25)
    assert r_joy["dominant_emotion"] == "joy"
    assert r_joy["valence"] > 0.35
    assert r_joy["arousal"] > 50.0

    # 4. Sustained Anger -> negative valence
    bs_anger = {"browDownLeft": 0.85, "browDownRight": 0.85, "noseSneerLeft": 0.65, "noseSneerRight": 0.65, "mouthPressLeft": 0.70, "mouthPressRight": 0.70}
    ang_state: dict = {}
    for step in range(5):
        r_ang = analyze_facial_emotions(None, bs_anger, prev_state=ang_state, timestamp=step * 0.25)
    assert r_ang["dominant_emotion"] == "anger"
    assert r_ang["valence"] < -0.25
    assert r_ang["arousal"] > 45.0

    # 5. EMA smoothing rejects a brief single-frame weak expression noise spike from flipping Neutral
    ema_test = analyze_facial_emotions(None, {}, None, timestamp=0.0)
    st_ema = dict(ema_test["state"])
    noisy_frame = {"mouthSmileLeft": 0.40, "mouthSmileRight": 0.40}
    r_noise = analyze_facial_emotions(None, noisy_frame, st_ema, timestamp=0.25)
    assert r_noise["dominant_emotion"] == "neutral"

    # 6. Geometric landmark fallback when blendshapes absent
    geo_lms = _synthetic_landmarks(jaw_open=0.85, eye_wide=0.85, brow_up=0.75)
    r_geo = analyze_facial_emotions(landmarks=geo_lms, blendshapes=None, prev_state=None, timestamp=0.0)
    assert all(lbl in r_geo["emotion_scores"] for lbl in EMOTION_LABELS)
    assert r_geo["dominant_emotion"] in ("surprise", "fear") and r_geo["arousal"] > 35.0


def test_video_analyzer_shot_scale_emotion_telemetry_and_graphic_popup(tmp_path: Path):
    # Direct unit test on shot scale classifier
    assert classify_shot_scale(1, {"cx": 0.5, "cy": 0.45, "w": 0.34, "h": 0.44}) == "closeup_reaction"
    assert classify_shot_scale(1, {"cx": 0.5, "cy": 0.40, "w": 0.14, "h": 0.22}) == "talking_head"
    assert classify_shot_scale(2, {"cx": 0.3, "cy": 0.40, "w": 0.18, "h": 0.25}) == "dual_speaker"
    assert classify_shot_scale(0, None) == "wide_gameplay"
    assert classify_shot_scale(1, {"cx": 0.12, "cy": 0.15, "w": 0.12, "h": 0.16}) == "wide_gameplay"

    # Sudden graphic / meme banner popup detector check on synthetic grayscale frames
    bg = np.full((360, 640), 38, dtype=np.uint8)
    popup = bg.copy()
    cv2.rectangle(popup, (120, 35), (520, 105), 245, -1)
    cv2.putText(popup, "CRITICAL HIT +999!", (145, 82), cv2.FONT_HERSHEY_SIMPLEX, 0.95, 15, 3)
    steady_score = compute_graphic_event_score(bg, bg, is_cut=False)
    popup_score = compute_graphic_event_score(popup, bg, is_cut=False)
    assert steady_score < 8.0
    assert popup_score >= 30.0

    # End-to-end video analysis on 4s video switching from wide gameplay (0..2s) + popup at t=1.2s
    mp4 = tmp_path / "telemetry_test.mp4"
    ffmpeg = get_ffmpeg_cmd()
    vf = (
        "drawbox=x=120:y=40:w=400:h=75:color=white@1.0:t=fill:enable='between(t,1.1,2.2)',"
        "drawbox=x=180:y=55:w=280:h=45:color=red@1.0:t=fill:enable='between(t,1.1,2.2)'"
    )
    res_ff = subprocess.run(
        [
            ffmpeg, "-y",
            "-f", "lavfi", "-i", "color=c=#1E293B:size=640x360:rate=16:duration=3.5",
            "-vf", vf,
            "-c:v", "libx264", "-preset", "ultrafast", "-pix_fmt", "yuv420p",
            str(mp4),
        ],
        capture_output=True, text=True, timeout=30,
    )
    assert res_ff.returncode == 0, res_ff.stderr

    analysis = analyze_video_track(str(mp4), sample_fps=4.0)
    assert "frames" in analysis and len(analysis["frames"]) >= 10
    first_f = analysis["frames"][0]
    for req_key in ("dominant_emotion", "emotion_scores", "valence", "arousal", "emotion_delta", "shot_scale", "graphic_event_score"):
        assert req_key in first_f
    assert analysis["summary"]["dominant_shot_scale"] == "wide_gameplay"
    assert analysis["summary"]["graphic_event_count"] >= 1
