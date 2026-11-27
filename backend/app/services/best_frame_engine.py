"""
Lost Heaven — Best-Frame Selection & Role Classification Engine (`best_frame_engine.py`).

Scores every analyzed video frame within `[start_time, end_time]`:
  - Strictly disqualifies blurry frames (`sharpness < 35`), dark/flash frames
    (`exposure < 30` or `is_black`), and mid-blink faces (`eyes_open_confidence < 0.65`)
  - Applies temporal NMS (`min_gap = 1.2s`) to extract up to 6 distinct keyframes
  - Tags each frame with its primary studio role:
      `peak_emotion`, `peak_action`, `clean_anchor`, `clean_opener`, `highlight_moment`
  - Auto-computes `suggested_start_time` (up to +0.6s forward) if clip start lands on fade/blur/blink
  - Saves full-resolution quality=95 JPG stills safely across Unicode Windows paths
"""

from __future__ import annotations

import logging
import math
import os
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import cv2

from app.services.video_analyzer import analyze_video_track

logger = logging.getLogger("lost_heaven.best_frame_engine")


def _lookup_audio_excitement(audio_analysis: Optional[Dict[str, Any]], t_sec: float) -> float:
    if not isinstance(audio_analysis, dict):
        return 50.0
    tl = audio_analysis.get("timeline") or []
    if not tl:
        return 50.0
    closest = min(tl, key=lambda pt: abs(float(pt.get("t", 0.0)) - t_sec))
    return float(closest.get("excitement_score", 50.0))


def _is_frame_clean(frame: Dict[str, Any]) -> Tuple[bool, Optional[str]]:
    if frame.get("is_black") or float(frame.get("exposure_score", 0.0)) < 30.0:
        return False, "dark"
    if float(frame.get("sharpness_score", 0.0)) < 35.0:
        return False, "blurry"
    if int(frame.get("face_count", 0)) > 0 and float(frame.get("eyes_open_confidence", 1.0)) < 0.65:
        return False, "blink"
    return True, None


def _score_roles(frame: Dict[str, Any], audio_exc: float) -> Dict[str, float]:
    sharp = float(frame.get("sharpness_score", 0.0))
    exp = float(frame.get("exposure_score", 0.0))
    act = float(frame.get("action_score", 0.0))
    comp = float(frame.get("composition_score", 50.0))
    emo = float(frame.get("emotion_intensity", 0.0))
    gaze = float(frame.get("gaze_score", 50.0))
    fc = int(frame.get("face_count", 0))
    eyes = float(frame.get("eyes_open_confidence", 1.0))

    eyes_bonus = eyes * 100.0 if fc > 0 else 75.0
    base_q = 0.35 * sharp + 0.25 * exp + 0.20 * comp + 0.20 * eyes_bonus

    composite = 0.45 * base_q + 0.25 * emo + 0.15 * act + 0.15 * audio_exc

    # Role-specific affinities
    if fc > 0:
        peak_emotion = 0.48 * emo + 0.24 * gaze + 0.18 * sharp + 0.10 * eyes * 100.0
    else:
        peak_emotion = 0.20 * composite + 0.10 * comp

    peak_action = 0.60 * act + 0.25 * sharp + 0.15 * audio_exc
    # Steady anchor: high composition + sharpness + exposure without excessive blur/motion
    clean_anchor = 0.42 * comp + 0.38 * sharp + 0.20 * exp - 0.12 * min(35.0, act)

    return {
        "composite_score": round(float(composite), 1),
        "emotion_rank": round(float(peak_emotion), 1),
        "action_rank": round(float(peak_action), 1),
        "anchor_rank": round(float(clean_anchor), 1),
        "audio_excitement": round(float(audio_exc), 1),
    }


def extract_best_frames(
    video_path: str,
    video_analysis: Optional[Dict[str, Any]],
    audio_analysis: Optional[Dict[str, Any]],
    start_time: float,
    end_time: float,
    output_dir: str,
    *,
    clip_id: int = 0,
    project_id: int = 0,
    min_gap: float = 1.2,
    max_frames: int = 6,
) -> Dict[str, Any]:
    """
    Identify Top 6 Best Frames inside `[start_time, end_time]`, enforce `min_gap = 1.2s`
    temporal NMS, tag studio roles (`peak_emotion`, `peak_action`, `clean_anchor`,
    `clean_opener`, `highlight_moment`), and save quality=95 JPEG stills.
    """
    s_t = max(0.0, float(start_time or 0.0))
    e_t = max(s_t + 0.25, float(end_time or (s_t + 5.0)))

    # Reuse matching span from video_analysis or analyze window on demand
    window_frames: List[Dict[str, Any]] = []
    if isinstance(video_analysis, dict) and video_analysis.get("frames"):
        window_frames = [
            dict(fr) for fr in video_analysis["frames"]
            if (s_t - 0.08) <= float(fr.get("t", -1.0)) <= (e_t + 0.08)
        ]

    if len(window_frames) < 3 and video_path and os.path.exists(video_path):
        local_va = analyze_video_track(video_path, sample_fps=4.0, start_time=s_t, end_time=e_t)
        window_frames = [
            dict(fr) for fr in (local_va.get("frames") or [])
            if (s_t - 0.08) <= float(fr.get("t", -1.0)) <= (e_t + 0.08)
        ]

    rejected = {"blurry": 0, "dark": 0, "blink": 0}
    candidates: List[Dict[str, Any]] = []
    all_sorted = sorted(window_frames, key=lambda x: float(x.get("t", 0.0)))

    for fr in all_sorted:
        ok, reason = _is_frame_clean(fr)
        if not ok:
            if reason in rejected:
                rejected[reason] += 1
            continue
        t_val = float(fr.get("t", s_t))
        aud_exc = _lookup_audio_excitement(audio_analysis, t_val)
        ranks = _score_roles(fr, aud_exc)
        candidates.append({**fr, **ranks})

    # Clean-opener auto-snap check within +0.6s if raw clip start fails quality gates
    suggested_start_time: Optional[float] = None
    if all_sorted:
        first_fr = all_sorted[0]
        first_ok, _ = _is_frame_clean(first_fr)
        if not first_ok:
            for cand in candidates:
                ct = float(cand.get("t", s_t))
                if s_t < ct <= min(e_t, s_t + 0.65):
                    suggested_start_time = round(ct, 3)
                    break

    selected: List[Dict[str, Any]] = []
    used_ts: List[float] = []

    def _can_pick(ts: float) -> bool:
        return all(abs(ts - prev) >= float(min_gap) for prev in used_ts)

    def _add_pick(cand: Dict[str, Any], role: str) -> None:
        ts = float(cand["t"])
        item = dict(cand)
        item["role"] = role
        selected.append(item)
        used_ts.append(ts)

    if candidates:
        # 1. clean_opener within first 1.5s of clip
        opener_pool = [c for c in candidates if float(c["t"]) <= min(e_t, s_t + 1.5)]
        if opener_pool:
            best_opener = max(opener_pool, key=lambda c: float(c["composite_score"]))
            _add_pick(best_opener, "clean_opener")

        # 2. peak_emotion (thumbnail cover winner)
        emo_pool = [c for c in candidates if _can_pick(float(c["t"]))]
        if emo_pool:
            face_emo_pool = [c for c in emo_pool if int(c.get("face_count", 0)) > 0]
            pick_from = face_emo_pool if face_emo_pool else emo_pool
            best_emo = max(pick_from, key=lambda c: float(c["emotion_rank"]))
            # If clip opener already took the only face peak and there is a stronger face frame not yet assigned, keep roles clear
            _add_pick(best_emo, "peak_emotion")

        # 3. peak_action
        act_pool = [c for c in candidates if _can_pick(float(c["t"]))]
        if act_pool:
            best_act = max(act_pool, key=lambda c: float(c["action_rank"]))
            _add_pick(best_act, "peak_action")

        # 4. clean_anchor
        anc_pool = [c for c in candidates if _can_pick(float(c["t"]))]
        if anc_pool:
            best_anc = max(anc_pool, key=lambda c: float(c["anchor_rank"]))
            _add_pick(best_anc, "clean_anchor")

        # 5. Secondary high-retention highlight_moment frames up to max_frames (6)
        for cand in sorted(candidates, key=lambda c: float(c["composite_score"]), reverse=True):
            if len(selected) >= max_frames:
                break
            if _can_pick(float(cand["t"])):
                _add_pick(cand, "highlight_moment")

        # Smart role backfill: on short clips if clean_opener absorbed the sole peak_emotion frame cleanly, promote its secondary label cleanly when appropriate
        roles_present = {item["role"] for item in selected}
        if "peak_emotion" not in roles_present and selected:
            # Find highest emotion frame among selected with face_count > 0
            with_face = [item for item in selected if int(item.get("face_count", 0)) > 0]
            if with_face:
                best_face_item = max(with_face, key=lambda c: float(c["emotion_rank"]))
                if best_face_item["role"] == "highlight_moment":
                    best_face_item["role"] = "peak_emotion"

    # Extract full-resolution quality=95 JPEG stills safely across Unicode paths
    out_path = Path(output_dir)
    out_path.mkdir(parents=True, exist_ok=True)
    selected.sort(key=lambda item: float(item["composite_score"]), reverse=True)

    final_frames: List[Dict[str, Any]] = []
    cap = cv2.VideoCapture(str(Path(video_path).resolve())) if (video_path and os.path.exists(video_path)) else None
    try:
        for idx, item in enumerate(selected[:max_frames]):
            t_sec = round(float(item["t"]), 3)
            img_name = f"best_frame_{project_id}_{clip_id}_{idx}_{item['role']}.jpg"
            img_file = out_path / img_name
            saved_ok = False

            if cap is not None and cap.isOpened():
                cap.set(cv2.CAP_PROP_POS_MSEC, t_sec * 1000.0)
                ok, raw_fr = cap.read()
                if ok and raw_fr is not None:
                    enc_ok, buf = cv2.imencode(".jpg", raw_fr, [int(cv2.IMWRITE_JPEG_QUALITY), 95])
                    if enc_ok:
                        img_file.write_bytes(buf.tobytes())
                        saved_ok = True

            final_frames.append({
                "index": idx,
                "role": item["role"],
                "t": t_sec,
                "rel_t": round(max(0.0, t_sec - s_t), 3),
                "composite_score": float(item["composite_score"]),
                "sharpness_score": float(item.get("sharpness_score", 0.0)),
                "exposure_score": float(item.get("exposure_score", 0.0)),
                "action_score": float(item.get("action_score", 0.0)),
                "composition_score": float(item.get("composition_score", 50.0)),
                "emotion_intensity": float(item.get("emotion_intensity", 0.0)),
                "eyes_open_confidence": float(item.get("eyes_open_confidence", 1.0)),
                "gaze_score": float(item.get("gaze_score", 50.0)),
                "audio_excitement": float(item.get("audio_excitement", 50.0)),
                "face_count": int(item.get("face_count", 0)),
                "image_path": str(img_file.resolve()) if saved_ok else "",
                "image_url": f"/api/clips/{project_id}/{clip_id}/best-frames/{idx}/image" if (project_id and clip_id and saved_ok) else "",
            })
    finally:
        if cap is not None:
            cap.release()

    return {
        "clip_id": int(clip_id),
        "project_id": int(project_id),
        "start_time": round(s_t, 3),
        "end_time": round(e_t, 3),
        "suggested_start_time": suggested_start_time,
        "rejected_counts": rejected,
        "frames": final_frames,
    }
