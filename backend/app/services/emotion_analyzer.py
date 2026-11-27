"""
Lost Heaven — 100% Local Facial Emotion & Micro-Expression Engine (`emotion_analyzer.py`).

Maps MediaPipe FaceLandmarker 52 ARKit blendshapes (with automatic 468-landmark
geometric fallback when blendshapes are absent) into:
  - 7 Discrete Emotion Probabilities (`0.0 - 1.0`, summing cleanly to 1.0):
      joy, surprise, anger, fear, sadness, disgust, neutral
  - Continuous Affect Dimensions:
      valence (-1.0 to +1.0), arousal (0.0 to 100.0)
  - Emotional Shift / Micro-Expression Spike (`emotion_delta`, 0.0 - 100.0):
      Measures sudden transitions within a trailing 0.8s window while applying
      temporal EMA smoothing (`alpha = 0.35`) to prevent single-frame flicker.
"""

from __future__ import annotations

import math
from typing import Any, Dict, List, Optional, Sequence, Tuple

EMOTION_LABELS: Tuple[str, ...] = (
    "joy",
    "surprise",
    "anger",
    "fear",
    "sadness",
    "disgust",
    "neutral",
)

EMA_ALPHA = 0.35
MICRO_EXPRESSION_WINDOW_SEC = 0.80


def _clamp01(val: float) -> float:
    return max(0.0, min(1.0, float(val)))


def _extract_blendshape_dict(blendshapes: Any) -> Dict[str, float]:
    """Normalize either a dict[str, float] or MediaPipe Category list into float scores."""
    out: Dict[str, float] = {}
    if not blendshapes:
        return out
    if isinstance(blendshapes, dict):
        for k, v in blendshapes.items():
            try:
                out[str(k)] = _clamp01(float(v))
            except (TypeError, ValueError):
                continue
        return out
    if isinstance(blendshapes, (list, tuple)):
        # Handle nested [[Category...]] or flat [Category...]
        items = blendshapes[0] if (blendshapes and isinstance(blendshapes[0], (list, tuple))) else blendshapes
        for cat in items:
            cname = getattr(cat, "category_name", None)
            score = getattr(cat, "score", None)
            if cname is not None and score is not None:
                out[str(cname)] = _clamp01(float(score))
            elif isinstance(cat, dict) and "category_name" in cat:
                out[str(cat["category_name"])] = _clamp01(float(cat.get("score", 0.0)))
    return out


def _lm_xy(lms: Sequence[Any], idx: int) -> Optional[Tuple[float, float]]:
    if lms is None or idx >= len(lms):
        return None
    p = lms[idx]
    if hasattr(p, "x") and hasattr(p, "y"):
        return float(p.x), float(p.y)
    if isinstance(p, dict) and "x" in p:
        return float(p["x"]), float(p.get("y", 0.0))
    if isinstance(p, (list, tuple)) and len(p) >= 2:
        return float(p[0]), float(p[1])
    return None


def _dist2(a: Optional[Tuple[float, float]], b: Optional[Tuple[float, float]]) -> float:
    if a is None or b is None:
        return 0.0
    return math.hypot(a[0] - b[0], a[1] - b[1])


def extract_geometric_blendshapes(landmarks: Any) -> Dict[str, float]:
    """
    Geometric fallback deriving pseudo-ARKit blendshapes from MediaPipe 468/478 face landmarks
    when `output_face_blendshapes` scores are missing.
    """
    if not landmarks:
        return {}
    lms = landmarks
    if (
        isinstance(landmarks, (list, tuple))
        and landmarks
        and len(landmarks) < 16
        and isinstance(landmarks[0], (list, tuple))
        and len(landmarks[0]) >= 390
    ):
        lms = landmarks[0]
    if not isinstance(lms, (list, tuple)) or len(lms) < 390:
        return {}

    forehead = _lm_xy(lms, 10)
    chin = _lm_xy(lms, 152)
    fh = max(1e-3, _dist2(forehead, chin))

    l_outer, r_outer = _lm_xy(lms, 33), _lm_xy(lms, 263)
    eye_span = max(1e-3, _dist2(l_outer, r_outer))

    mouth_l, mouth_r = _lm_xy(lms, 61), _lm_xy(lms, 291)
    lip_top, lip_bot = _lm_xy(lms, 13), _lm_xy(lms, 14)

    jaw_open = 0.0
    if lip_top and lip_bot:
        jaw_open = _clamp01((_dist2(lip_top, lip_bot) / fh - 0.015) * 6.5)

    smile_l = smile_r = frown_l = frown_r = 0.0
    if mouth_l and mouth_r and lip_top:
        # Anchor corners to upper lip so lowering the jaw in surprise never registers as a smile.
        corner_ref_y = lip_top[1] + 0.008 * fh
        lift_l = (corner_ref_y - mouth_l[1]) / fh
        lift_r = (corner_ref_y - mouth_r[1]) / fh
        smile_l = _clamp01((lift_l - 0.006) * 16.0)
        smile_r = _clamp01((lift_r - 0.006) * 16.0)
        frown_ref_y = lip_top[1] + (0.016 + jaw_open * 0.055) * fh
        frown_l = _clamp01(((mouth_l[1] - frown_ref_y) / fh) * 16.0)
        frown_r = _clamp01(((mouth_r[1] - frown_ref_y) / fh) * 16.0)

    mouth_stretch = _clamp01((_dist2(mouth_l, mouth_r) / eye_span - 0.62) * 5.0)

    # Eye aperture (159 upper L, 145 lower L; 386 upper R, 374 lower R)
    eye_open_l = _dist2(_lm_xy(lms, 159), _lm_xy(lms, 145)) / fh
    eye_open_r = _dist2(_lm_xy(lms, 386), _lm_xy(lms, 374)) / fh
    eye_wide_l = _clamp01((eye_open_l - 0.042) * 18.0)
    eye_wide_r = _clamp01((eye_open_r - 0.042) * 18.0)
    eye_blink_l = _clamp01((0.032 - eye_open_l) * 35.0)
    eye_blink_r = _clamp01((0.032 - eye_open_r) * 35.0)

    # Brow vertical gap relative to inner/outer eye corners
    brow_in_l = _dist2(_lm_xy(lms, 107), _lm_xy(lms, 133)) / fh
    brow_in_r = _dist2(_lm_xy(lms, 336), _lm_xy(lms, 362)) / fh
    brow_in_avg = 0.5 * (brow_in_l + brow_in_r)
    brow_inner_up = _clamp01((brow_in_avg - 0.115) * 15.0)
    brow_down = _clamp01((0.095 - brow_in_avg) * 16.0)

    brow_out_l = _dist2(_lm_xy(lms, 70), l_outer) / fh
    brow_out_r = _dist2(_lm_xy(lms, 300), r_outer) / fh
    brow_outer_up_l = _clamp01((brow_out_l - 0.110) * 14.0)
    brow_outer_up_r = _clamp01((brow_out_r - 0.110) * 14.0)

    return {
        "jawOpen": round(jaw_open, 4),
        "mouthSmileLeft": round(smile_l, 4),
        "mouthSmileRight": round(smile_r, 4),
        "mouthFrownLeft": round(frown_l, 4),
        "mouthFrownRight": round(frown_r, 4),
        "mouthStretchLeft": round(mouth_stretch, 4),
        "mouthStretchRight": round(mouth_stretch, 4),
        "mouthPressLeft": round(_clamp01((0.018 - abs((lip_bot[1] - lip_top[1]) if (lip_top and lip_bot) else 0.02) / fh) * 20.0), 4),
        "mouthPressRight": round(_clamp01((0.018 - abs((lip_bot[1] - lip_top[1]) if (lip_top and lip_bot) else 0.02) / fh) * 20.0), 4),
        "cheekSquintLeft": round(_clamp01(smile_l * 0.72), 4),
        "cheekSquintRight": round(_clamp01(smile_r * 0.72), 4),
        "eyeWideLeft": round(eye_wide_l, 4),
        "eyeWideRight": round(eye_wide_r, 4),
        "eyeBlinkLeft": round(eye_blink_l, 4),
        "eyeBlinkRight": round(eye_blink_r, 4),
        "browInnerUp": round(brow_inner_up, 4),
        "browOuterUpLeft": round(brow_outer_up_l, 4),
        "browOuterUpRight": round(brow_outer_up_r, 4),
        "browDownLeft": round(brow_down, 4),
        "browDownRight": round(brow_down, 4),
        "noseSneerLeft": round(_clamp01(brow_down * 0.45), 4),
        "noseSneerRight": round(_clamp01(brow_down * 0.45), 4),
        "mouthUpperUpLeft": round(_clamp01(jaw_open * 0.30), 4),
        "mouthUpperUpRight": round(_clamp01(jaw_open * 0.30), 4),
    }


def _avg_pair(bs: Dict[str, float], left_key: str, right_key: str) -> float:
    return 0.5 * (bs.get(left_key, 0.0) + bs.get(right_key, 0.0))


def analyze_facial_emotions(
    landmarks: Any = None,
    blendshapes: Any = None,
    prev_state: Optional[Dict[str, Any]] = None,
    *,
    timestamp: Optional[float] = None,
) -> Dict[str, Any]:
    """
    Compute 7 normalized discrete emotion probabilities (`joy`, `surprise`, `anger`,
    `fear`, `sadness`, `disgust`, `neutral`), continuous `valence` (-1.0..+1.0),
    `arousal` (0..100), and micro-expression spike `emotion_delta` (0..100)
    with temporal EMA smoothing (`alpha = 0.35`).
    """
    bs = _extract_blendshape_dict(blendshapes)
    if not bs or max(bs.values(), default=0.0) <= 0.0:
        geo = extract_geometric_blendshapes(landmarks)
        if geo:
            bs = geo

    smile = _avg_pair(bs, "mouthSmileLeft", "mouthSmileRight")
    cheek_squint = _avg_pair(bs, "cheekSquintLeft", "cheekSquintRight")
    jaw_open = bs.get("jawOpen", 0.0)
    eye_wide = _avg_pair(bs, "eyeWideLeft", "eyeWideRight")
    brow_inner_up = bs.get("browInnerUp", 0.0)
    brow_outer_up = _avg_pair(bs, "browOuterUpLeft", "browOuterUpRight")
    brow_down = _avg_pair(bs, "browDownLeft", "browDownRight")
    nose_sneer = _avg_pair(bs, "noseSneerLeft", "noseSneerRight")
    mouth_press = _avg_pair(bs, "mouthPressLeft", "mouthPressRight")
    mouth_stretch = _avg_pair(bs, "mouthStretchLeft", "mouthStretchRight")
    mouth_frown = _avg_pair(bs, "mouthFrownLeft", "mouthFrownRight")
    mouth_upper_up = _avg_pair(bs, "mouthUpperUpLeft", "mouthUpperUpRight")

    # 7 raw discrete activations mapped from 52 ARKit blendshapes
    raw_joy = _clamp01(0.64 * smile + 0.36 * max(cheek_squint, smile * 0.55) - 0.30 * brow_down - 0.30 * mouth_frown)
    raw_surprise = _clamp01(
        0.42 * jaw_open + 0.30 * eye_wide + 0.16 * brow_inner_up + 0.12 * brow_outer_up
        - 0.20 * brow_down - 0.18 * mouth_stretch
    )
    raw_anger = _clamp01(
        0.48 * brow_down + 0.28 * nose_sneer + 0.24 * mouth_press - 0.28 * smile - 0.15 * brow_inner_up
    )
    # Fear couples wide eyes + inner brow raise + tense horizontal mouth stretch (suppressed when jaw/smile dominate)
    fear_stretch_gate = 0.18 + 0.82 * min(1.0, mouth_stretch * 2.5)
    raw_fear = _clamp01(
        (0.38 * eye_wide + 0.30 * brow_inner_up) * fear_stretch_gate + 0.42 * mouth_stretch
        - 0.22 * smile - 0.26 * max(0.0, jaw_open - mouth_stretch - 0.20)
    )
    raw_sadness = _clamp01(
        0.60 * mouth_frown + 0.40 * brow_inner_up - 0.25 * smile - 0.20 * eye_wide
    )
    raw_disgust = _clamp01(
        0.56 * nose_sneer + 0.44 * mouth_upper_up - 0.22 * smile - 0.18 * brow_down * 0.4
    )

    active_raw = {
        "joy": raw_joy,
        "surprise": raw_surprise,
        "anger": raw_anger,
        "fear": raw_fear,
        "sadness": raw_sadness,
        "disgust": raw_disgust,
    }
    max_active = max(active_raw.values())
    sum_active = sum(active_raw.values())
    raw_neutral = max(0.02, min(1.0, 1.0 - max_active * 1.35 - max(0.0, sum_active - max_active) * 0.20))

    total_raw = sum_active + raw_neutral
    raw_probs: Dict[str, float] = {
        k: round(v / max(1e-6, total_raw), 4)
        for k, v in {**active_raw, "neutral": raw_neutral}.items()
    }

    # Unpack prior temporal state cleanly (supports passing previous return dict OR dedicated state dict)
    prior_ema: Optional[Dict[str, float]] = None
    history: List[Tuple[float, Dict[str, float], float]] = []
    if isinstance(prev_state, dict):
        if isinstance(prev_state.get("state"), dict):
            st = prev_state["state"]
            prior_ema = st.get("ema_probs")
            history = list(st.get("history") or [])
        else:
            prior_ema = prev_state.get("ema_probs") or prev_state.get("emotion_scores")
            history = list(prev_state.get("history") or [])

    t_now = (
        float(timestamp)
        if timestamp is not None
        else ((history[-1][0] + 0.25) if history else 0.0)
    )

    # Temporal EMA smoothing (alpha = 0.35) to eliminate single-frame flicker
    if isinstance(prior_ema, dict) and all(k in prior_ema for k in EMOTION_LABELS):
        smoothed_raw = {
            k: EMA_ALPHA * raw_probs[k] + (1.0 - EMA_ALPHA) * float(prior_ema.get(k, 0.0))
            for k in EMOTION_LABELS
        }
    else:
        smoothed_raw = dict(raw_probs)

    sm_sum = max(1e-6, sum(smoothed_raw.values()))
    emotion_scores = {k: round(smoothed_raw[k] / sm_sum, 4) for k in EMOTION_LABELS}
    dominant_emotion = max(EMOTION_LABELS, key=lambda k: emotion_scores[k])

    # Continuous affect dimensions: Valence [-1.0..+1.0], Arousal [0..100]
    raw_arousal = max(
        0.0,
        min(
            100.0,
            max_active * 88.0 + min(12.0, max(0.0, sum_active - max_active) * 28.0) + max(0.0, jaw_open + eye_wide) * 6.0,
        ),
    )
    prev_arousal = history[-1][2] if history else raw_arousal
    arousal = round(max(0.0, min(100.0, 0.55 * raw_arousal + 0.45 * prev_arousal if history else raw_arousal)), 1)

    valence_raw = (
        1.00 * emotion_scores["joy"]
        + 0.12 * emotion_scores["surprise"]
        - 1.00 * emotion_scores["anger"]
        - 0.92 * emotion_scores["fear"]
        - 0.92 * emotion_scores["sadness"]
        - 0.96 * emotion_scores["disgust"]
    )
    valence = round(max(-1.0, min(1.0, valence_raw)), 3)

    # Micro-expression spike (`emotion_delta`): max raw transition inside trailing 0.8s window
    recent_hist = [h for h in history if (t_now - h[0]) <= MICRO_EXPRESSION_WINDOW_SEC + 1e-4]
    max_delta_01 = 0.0
    if recent_hist:
        for _, prev_raw, prev_raw_arousal in recent_hist:
            tv_dist = 0.5 * sum(abs(raw_probs[k] - float(prev_raw.get(k, 0.0))) for k in EMOTION_LABELS)
            arousal_jump = abs(raw_arousal - float(prev_raw_arousal)) / 100.0
            max_delta_01 = max(max_delta_01, 0.72 * tv_dist + 0.28 * arousal_jump)
    emotion_delta = round(max(0.0, min(100.0, max_delta_01 * 115.0)), 1)

    new_history = (recent_hist + [(round(t_now, 3), raw_probs, round(raw_arousal, 2))])[-16:]
    next_state = {
        "ema_probs": dict(emotion_scores),
        "history": new_history,
        "prev_arousal": arousal,
    }
    # If caller passed a mutable state tracker dict directly (not a full result payload), update it in place
    if isinstance(prev_state, dict) and "emotion_scores" not in prev_state:
        prev_state.update(next_state)

    return {
        "dominant_emotion": dominant_emotion,
        "emotion_scores": emotion_scores,
        "raw_emotion_scores": raw_probs,
        "valence": valence,
        "arousal": arousal,
        "emotion_delta": emotion_delta,
        "is_micro_expression_spike": emotion_delta >= 26.0,
        "state": next_state,
    }
