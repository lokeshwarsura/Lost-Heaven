"""
Lost Heaven — Per-Clip Smart Auto-Frame AI Router (`auto_framer.py`).
Inspects `visual_map` scene composition from `vision_analyzer.py` and selects each clip's
ideal framing template + safest output PiP corner placement.
"""

from __future__ import annotations

from typing import Any, Dict, List

CORNER_ZONES = {
    "top_left",
    "top_right",
    "bottom_left",
    "bottom_right",
    "mid_left",
    "mid_right",
}


def recommend_optimal_framing(visual_map: Dict[str, Any]) -> Dict[str, Any]:
    """
    Route a clip's `visual_map` into the optimal Lost Heaven framing configuration:
      1. Corner facecam overlay + gameplay -> `gameplay_webcam_pip` (cleanest opposite corner)
      2. 2 persistent side-by-side speakers -> `split_podcast`
      3. Single talking head / reaction -> `auto_track`
      4. Pure 16:9 gameplay / screen tutorial (0 faces) -> `fit_blur`
    """
    webcam = dict(visual_map.get("webcam") or {})
    summary = dict(visual_map.get("summary") or {})
    shots: List[Dict[str, Any]] = list(visual_map.get("shots") or [])
    kfs: List[Dict[str, Any]] = list(visual_map.get("keyframes") or [])

    src_zone = str(webcam.get("source_zone") or "none").strip().lower()
    has_corner_cam = bool(webcam.get("detected")) and src_zone in CORNER_ZONES

    # 1. Corner webcam + gameplay -> gameplay_webcam_pip with opposite safe corner
    if has_corner_cam:
        if kfs:
            acx = sum(float(k.get("action_cx", 0.5)) for k in kfs) / len(kfs)
            acy = sum(float(k.get("action_cy", 0.48)) for k in kfs) / len(kfs)
        else:
            acx = float(webcam.get("action_cx", 0.5))
            acy = float(webcam.get("action_cy", 0.48))

        if "left" in src_zone:
            out_corner = "bottom_right" if (acx > 0.62 and acy < 0.35) else "top_right"
        else:
            out_corner = "bottom_left" if (acx < 0.38 and acy < 0.35) else "top_left"

        return {
            "frame_layout": "gameplay_webcam_pip",
            "webcam_corner": out_corner,
            "scene_type": "corner_webcam_gameplay",
            "reason": (
                f"Detected {src_zone.replace('_', ' ')} facecam overlay over gameplay; "
                f"positioned rounded PiP cleanly at {out_corner.replace('_', ' ')}."
            ),
            "confidence": 0.94,
            "source_webcam_region": webcam,
        }

    # 2. Persistent dual speakers -> cutaway-aware split_podcast
    dual_ratio = float(summary.get("dual_speaker_ratio", 0.0) or 0.0)
    two_sp_shots = sum(1 for s in shots if s.get("speaker_mode") == "two_speaker")
    shot_dual_ratio = (two_sp_shots / float(len(shots))) if shots else 0.0
    if dual_ratio >= 0.35 or shot_dual_ratio >= 0.35:
        return {
            "frame_layout": "split_podcast",
            "webcam_corner": "auto",
            "scene_type": "dual_speaker",
            "reason": "Detected two side-by-side speakers; enabled cutaway-aware Split Podcast stack.",
            "confidence": 0.92,
            "source_webcam_region": webcam,
        }

    # 3. Single talking head / reaction -> auto_track
    face_ratio = float(summary.get("face_presence_ratio", 0.0) or 0.0)
    if face_ratio == 0.0 and kfs:
        face_ratio = sum(1 for k in kfs if int(k.get("face_count", 0) or 0) > 0) / float(len(kfs))
    if face_ratio >= 0.30:
        return {
            "frame_layout": "auto_track",
            "webcam_corner": "auto",
            "scene_type": "single_speaker",
            "reason": "Detected primary talking-head speaker; enabled smooth Auto-Track Cinema Cam.",
            "confidence": 0.90,
            "source_webcam_region": webcam,
        }

    # 4. Zero faces -> full-width 16:9 Fit + Adaptive Ambient Backdrop
    return {
        "frame_layout": "fit_blur",
        "webcam_corner": "auto",
        "scene_type": "zero_face_action",
        "reason": "Zero-face gameplay/screen recording detected; preserving full 16:9 width over adaptive blur.",
        "confidence": 0.89,
        "source_webcam_region": webcam,
    }
