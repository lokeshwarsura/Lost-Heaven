import logging
import subprocess
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import cv2
import numpy as np

from app.services.face_tracker import compile_piecewise_linear_expr
from app.services.proxy import get_ffmpeg_cmd

logger = logging.getLogger("lost_heaven.privacy_blur")


def normalize_blur_boxes(raw_boxes: Optional[List[Dict[str, Any]]], max_boxes: int = 3) -> List[Dict[str, Any]]:
    """Sanitize up to 3 normalized privacy/HUD/watermark blur rectangles inside `[0, 1]`."""
    out: List[Dict[str, Any]] = []
    for idx, item in enumerate(raw_boxes or []):
        if not isinstance(item, dict) or item.get("enabled", True) is False:
            continue
        x = max(0.0, min(0.95, float(item.get("x", 0.1))))
        y = max(0.0, min(0.95, float(item.get("y", 0.1))))
        w = max(0.04, min(1.0 - x, float(item.get("w", 0.25))))
        h = max(0.04, min(1.0 - y, float(item.get("h", 0.12))))
        mode = str(item.get("mode", "gaussian")).strip().lower()
        if mode not in ("gaussian", "pixelate"):
            mode = "gaussian"
        out.append({
            "id": str(item.get("id") or f"blur_{idx + 1}"),
            "x": round(x, 4),
            "y": round(y, 4),
            "w": round(w, 4),
            "h": round(h, 4),
            "mode": mode,
            "track_motion": bool(item.get("track_motion", False)),
            "intensity": max(4, min(36, int(item.get("intensity", 18) or 18))),
        })
        if len(out) >= max_boxes:
            break
    return out


def _create_csrt_tracker():
    if hasattr(cv2, "TrackerCSRT_create"):
        return cv2.TrackerCSRT_create()
    legacy = getattr(cv2, "legacy", None)
    if legacy is not None and hasattr(legacy, "TrackerCSRT_create"):
        return legacy.TrackerCSRT_create()
    if hasattr(cv2, "TrackerKCF_create"):
        return cv2.TrackerKCF_create()
    return None


def track_roi_frames(
    frames_bgr: List[np.ndarray],
    fps: float,
    box: Dict[str, Any],
) -> List[Dict[str, float]]:
    """
    Track normalized `box = {x, y, w, h}` over ordered `frames_bgr` at `fps` using OpenCV CSRT
    with automatic normalized cross-correlation template-matching fallback.
    Returns normalized keyframes `[{t, x, y, w, h}]`.
    """
    if not frames_bgr:
        return [{"t": 0.0, "x": box["x"], "y": box["y"], "w": box["w"], "h": box["h"]}]

    fh, fw = frames_bgr[0].shape[:2]
    bw = max(8, min(fw - 2, int(round(box["w"] * fw))))
    bh = max(8, min(fh - 2, int(round(box["h"] * fh))))
    bx = max(0, min(fw - bw, int(round(box["x"] * fw))))
    by = max(0, min(fh - bh, int(round(box["y"] * fh))))

    tracker = _create_csrt_tracker() if box.get("track_motion", True) else None
    init_ok = False
    if tracker is not None:
        try:
            init_ok = bool(tracker.init(frames_bgr[0], (bx, by, bw, bh)))
        except Exception:
            init_ok = False

    template = frames_bgr[0][by:by + bh, bx:bx + bw].copy()
    cur_x, cur_y = bx, by
    kfs: List[Dict[str, float]] = [{
        "t": 0.0,
        "x": round(cur_x / float(fw), 4),
        "y": round(cur_y / float(fh), 4),
        "w": round(bw / float(fw), 4),
        "h": round(bh / float(fh), 4),
    }]

    dt = 1.0 / max(1.0, float(fps or 6.0))
    for i in range(1, len(frames_bgr)):
        fr = frames_bgr[i]
        updated = False
        if init_ok and tracker is not None:
            try:
                ok, bbox = tracker.update(fr)
                if ok and bbox[2] > 4 and bbox[3] > 4:
                    cur_x = max(0, min(fw - bw, int(round(bbox[0]))))
                    cur_y = max(0, min(fh - bh, int(round(bbox[1]))))
                    updated = True
            except Exception:
                updated = False

        if not updated and template.size > 0 and fh > bh and fw > bw:
            # Template matching search inside expanded local window
            pad_x, pad_y = max(20, bw), max(20, bh)
            sx0 = max(0, cur_x - pad_x)
            sy0 = max(0, cur_y - pad_y)
            sx1 = min(fw, cur_x + bw + pad_x)
            sy1 = min(fh, cur_y + bh + pad_y)
            search = fr[sy0:sy1, sx0:sx1]
            if search.shape[0] >= template.shape[0] and search.shape[1] >= template.shape[1]:
                res = cv2.matchTemplate(search, template, cv2.TM_CCOEFF_NORMED)
                _, max_val, _, max_loc = cv2.minMaxLoc(res)
                if max_val > 0.45:
                    cur_x = max(0, min(fw - bw, sx0 + int(max_loc[0])))
                    cur_y = max(0, min(fh - bh, sy0 + int(max_loc[1])))

        kfs.append({
            "t": round(i * dt, 3),
            "x": round(cur_x / float(fw), 4),
            "y": round(cur_y / float(fh), 4),
            "w": round(bw / float(fw), 4),
            "h": round(bh / float(fh), 4),
        })
    return kfs


def track_privacy_blur_regions(
    exec_dir: Path,
    base_input_args: List[str],
    pre_blur_chain: str,
    target_w: int,
    target_h: int,
    duration: float,
    boxes: List[Dict[str, Any]],
) -> Dict[str, List[Dict[str, float]]]:
    """
    Sample frames via FFmpeg pipe ONLY when at least one blur box has `track_motion=True`.
    Returns `{box_id: [{t, x, y, w, h}]}`.
    """
    clean_boxes = normalize_blur_boxes(boxes)
    trajectories: Dict[str, List[Dict[str, float]]] = {}
    if not clean_boxes:
        return trajectories

    need_track = any(b.get("track_motion") for b in clean_boxes)
    if not need_track:
        for b in clean_boxes:
            trajectories[b["id"]] = [{"t": 0.0, "x": b["x"], "y": b["y"], "w": b["w"], "h": b["h"]}]
        return trajectories

    ffmpeg = get_ffmpeg_cmd()
    sw = max(160, min(320, (target_w // 4) * 2))
    sh = max(160, (int(round(sw * (target_h / max(1.0, float(target_w))))) // 2) * 2)
    sample_fps = 6.0
    fg = f"{pre_blur_chain};[v]fps={sample_fps:.1f},scale={sw}:{sh}[btrk]"
    cmd = [
        ffmpeg, "-y",
        *base_input_args,
        "-filter_complex", fg,
        "-map", "[btrk]",
        "-t", f"{max(0.2, duration):.3f}",
        "-f", "rawvideo",
        "-pix_fmt", "bgr24",
        "pipe:1",
    ]
    proc = subprocess.run(cmd, capture_output=True, timeout=90, cwd=str(exec_dir))
    frames_bgr: List[np.ndarray] = []
    if proc.returncode == 0 and proc.stdout:
        fb = sw * sh * 3
        n = min(300, len(proc.stdout) // fb)
        for idx in range(n):
            chunk = proc.stdout[idx * fb:(idx + 1) * fb]
            frames_bgr.append(np.frombuffer(chunk, dtype=np.uint8).reshape((sh, sw, 3)))

    for b in clean_boxes:
        if b.get("track_motion") and len(frames_bgr) >= 2:
            trajectories[b["id"]] = track_roi_frames(frames_bgr, sample_fps, b)
        else:
            trajectories[b["id"]] = [{"t": 0.0, "x": b["x"], "y": b["y"], "w": b["w"], "h": b["h"]}]
    return trajectories


def _even(v: int, lo: int, hi: int) -> int:
    c = max(lo, min(hi, int(round(v))))
    return c if c % 2 == 0 else max(lo, c - 1)


def build_privacy_blur_filtergraph(
    curr_v_lbl: str,
    boxes: List[Dict[str, Any]],
    trajectories: Optional[Dict[str, List[Dict[str, float]]]],
    target_w: int,
    target_h: int,
    *,
    is_video_render: bool = True,
    out_label: str = "v_pblur",
) -> Tuple[List[str], str]:
    """
    Build FFmpeg filtergraph segments applying up to 3 Gaussian / Pixelate privacy blur boxes
    (with piecewise-linear motion tracking expressions when multiple keyframes exist).
    Returns `(filter_fragments, output_stream_label)`.
    """
    clean = normalize_blur_boxes(boxes)
    if not clean:
        return [], curr_v_lbl

    frags: List[str] = []
    cur = curr_v_lbl
    traj_map = trajectories or {}

    for idx, box in enumerate(clean):
        kfs = traj_map.get(box["id"]) or [{"t": 0.0, "x": box["x"], "y": box["y"], "w": box["w"], "h": box["h"]}]
        bw = _even(int(round(box["w"] * target_w)), 16, max(16, target_w - 2))
        bh = _even(int(round(box["h"] * target_h)), 16, max(16, target_h - 2))
        max_px_x = max(0, target_w - bw)
        max_px_y = max(0, target_h - bh)

        if is_video_render and len(kfs) > 1:
            x_pts = [(float(k["t"]), max(0, min(max_px_x, int(round(k["x"] * target_w))))) for k in kfs]
            y_pts = [(float(k["t"]), max(0, min(max_px_y, int(round(k["y"] * target_h))))) for k in kfs]
            x_expr = compile_piecewise_linear_expr(x_pts, 0, max_px_x)
            y_expr = compile_piecewise_linear_expr(y_pts, 0, max_px_y)
        else:
            px = max(0, min(max_px_x, int(round(kfs[0]["x"] * target_w))))
            py = max(0, min(max_px_y, int(round(kfs[0]["y"] * target_h))))
            x_expr = str(px)
            y_expr = str(py)

        base_lbl = f"pb_base_{idx}"
        roi_raw = f"pb_roi_{idx}"
        roi_blur = f"pb_fx_{idx}"
        nxt_lbl = out_label if idx == len(clean) - 1 else f"pb_step_{idx}"

        if box["mode"] == "pixelate":
            px_w = max(4, bw // max(8, box["intensity"] // 2 + 4))
            px_h = max(4, bh // max(8, box["intensity"] // 2 + 4))
            fx_str = f"scale={px_w}:{px_h}:flags=neighbor,scale={bw}:{bh}:flags=neighbor"
        else:
            sigma = max(4, min(32, int(box["intensity"])))
            fx_str = f"gblur=sigma={sigma}"

        frags.append(f"[{cur}]split=2[{base_lbl}][{roi_raw}]")
        frags.append(f"[{roi_raw}]crop={bw}:{bh}:'{x_expr}':'{y_expr}',{fx_str}[{roi_blur}]")
        frags.append(f"[{base_lbl}][{roi_blur}]overlay=x='{x_expr}':y='{y_expr}':eval=frame[{nxt_lbl}]")
        cur = nxt_lbl

    return frags, cur
