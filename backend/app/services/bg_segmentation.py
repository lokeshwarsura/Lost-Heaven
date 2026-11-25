import logging
import shutil
import subprocess
import tempfile
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import cv2
import numpy as np

from app.config import settings
from app.services.face_tracker import detect_faces_offline
from app.services.proxy import get_ffmpeg_cmd
from app.services.reframer import hex_to_ffmpeg_color

logger = logging.getLogger("lost_heaven.bg_segmentation")

BACKDROP_MODES = {
    "none",
    "original",
    "studio_blur",
    "solid_charcoal",
    "accent_gradient",
    "gameplay_blur",
}

_MP_SEGMENTER_FAILED = False


def canonicalize_backdrop_mode(mode: Optional[str]) -> str:
    raw = str(mode or "none").strip().lower().replace(" ", "_")
    mapping = {
        "original": "none",
        "none": "none",
        "studio_bokeh_blur": "studio_blur",
        "studio_blur": "studio_blur",
        "solid_charcoal": "solid_charcoal",
        "accent_gradient": "accent_gradient",
        "gameplay_blur": "gameplay_blur",
    }
    return mapping.get(raw, "none")


def _segment_mediapipe(frame_bgr: np.ndarray) -> Optional[np.ndarray]:
    """Tier 1: MediaPipe Tasks ImageSegmenter if a local `.tflite` segmentation model exists."""
    global _MP_SEGMENTER_FAILED
    if _MP_SEGMENTER_FAILED:
        return None

    models_dir = Path(settings.STORAGE_DIR) / "models"
    model_path: Optional[Path] = None
    for cand_name in ("selfie_segmenter.tflite", "selfie_multiclass_256x256.tflite", "deeplab_v3.tflite"):
        p = models_dir / cand_name
        if p.exists():
            model_path = p
            break
    if model_path is None:
        _MP_SEGMENTER_FAILED = True
        return None

    try:
        import mediapipe as mp
        from mediapipe.tasks import python as mp_python
        from mediapipe.tasks.python import vision as mp_vision

        cache_dir = Path(tempfile.gettempdir()) / "lost_heaven_mp_cache"
        cache_dir.mkdir(parents=True, exist_ok=True)
        ascii_model = cache_dir / model_path.name
        if not ascii_model.exists() or ascii_model.stat().st_size != model_path.stat().st_size:
            shutil.copy2(str(model_path), str(ascii_model))

        base_options = mp_python.BaseOptions(model_asset_path=str(ascii_model))
        options = mp_vision.ImageSegmenterOptions(
            base_options=base_options,
            output_confidence_masks=True,
        )
        rgb = cv2.cvtColor(frame_bgr, cv2.COLOR_BGR2RGB)
        mp_image = mp.Image(image_format=mp.ImageFormat.SRGB, data=rgb)

        with mp_vision.ImageSegmenter.create_from_options(options) as segmenter:
            res = segmenter.segment(mp_image)
            masks = getattr(res, "confidence_masks", None)
            if not masks:
                return None
            # Binary selfie model -> mask[0] is person or background depending on model; pick foreground confidence
            conf = masks[0].numpy_view()
            if len(masks) > 1:
                # Multi-class model: combine person classes (1..N-1) vs background (0)
                bg = masks[0].numpy_view()
                conf = np.clip(1.0 - bg, 0.0, 1.0)
            matte = np.clip(conf * 255.0, 0, 255).astype(np.uint8)
            if matte.shape[:2] != frame_bgr.shape[:2]:
                matte = cv2.resize(matte, (frame_bgr.shape[1], frame_bgr.shape[0]), interpolation=cv2.INTER_LINEAR)
            return matte
    except Exception as exc:
        logger.debug(f"MediaPipe ImageSegmenter unavailable, using OpenCV fallback: {exc}")
        _MP_SEGMENTER_FAILED = True
        return None


def _segment_opencv_fallback(frame_bgr: np.ndarray) -> np.ndarray:
    """
    Tier 2: Offline-safe OpenCV portrait/subject matte generator using face detection prior +
    GrabCut refinement + feathered alpha smoothing.
    """
    h, w = frame_bgr.shape[:2]
    max_dim = 224.0
    scale = min(1.0, max_dim / max(h, w))
    sw = max(32, int(round(w * scale)))
    sh = max(32, int(round(h * scale)))
    small = cv2.resize(frame_bgr, (sw, sh), interpolation=cv2.INTER_AREA) if scale < 1.0 else frame_bgr.copy()

    faces = detect_faces_offline(small)
    gc_mask = np.full((sh, sw), cv2.GC_PR_BGD, dtype=np.uint8)

    if faces:
        # Definite background margins along far top & upper side borders
        gc_mask[:, :max(2, int(sw * 0.04))] = cv2.GC_BGD
        gc_mask[:, max(0, sw - max(2, int(sw * 0.04))):] = cv2.GC_BGD
        gc_mask[:max(2, int(sh * 0.03)), :] = cv2.GC_BGD

        for f in faces:
            fx = int(round(f["cx"] * sw))
            fy = int(round(f["cy"] * sh))
            fw = max(12, int(round(f["w"] * sw)))
            fh = max(12, int(round(f["h"] * sh)))
            # Head + face interior as definite foreground
            cv2.ellipse(
                gc_mask,
                (fx, fy),
                (max(4, int(fw * 0.42)), max(5, int(fh * 0.52))),
                0, 0, 360, int(cv2.GC_FGD), -1,
            )
            # Upper torso polygon below chin as probable foreground
            shoulder_w = int(max(fw * 1.45, sw * 0.24))
            top_torso_y = min(sh - 1, fy + int(fh * 0.35))
            pts = np.array([
                [max(0, fx - fw // 2), top_torso_y],
                [min(sw - 1, fx + fw // 2), top_torso_y],
                [min(sw - 1, fx + shoulder_w), sh - 1],
                [max(0, fx - shoulder_w), sh - 1],
            ], dtype=np.int32)
            cv2.fillConvexPoly(gc_mask, pts, int(cv2.GC_PR_FGD))
        init_mode = cv2.GC_INIT_WITH_MASK
        rect = (0, 0, sw - 1, sh - 1)
    else:
        # Center subject prior ellipse when no face bbox is detected (works cleanly on synthetic test patterns too)
        gc_mask[:] = cv2.GC_BGD
        cx, cy = sw // 2, int(sh * 0.52)
        ax_x, ax_y = max(10, int(sw * 0.30)), max(12, int(sh * 0.40))
        cv2.ellipse(gc_mask, (cx, cy), (ax_x, ax_y), 0, 0, 360, int(cv2.GC_PR_FGD), -1)
        cv2.ellipse(gc_mask, (cx, int(sh * 0.46)), (max(5, ax_x // 2), max(6, ax_y // 2)), 0, 0, 360, int(cv2.GC_FGD), -1)
        init_mode = cv2.GC_INIT_WITH_MASK
        rect = (max(1, int(sw * 0.14)), max(1, int(sh * 0.08)), max(10, int(sw * 0.72)), max(10, int(sh * 0.86)))

    fg_ratio = 0.0
    binary = np.zeros((sh, sw), dtype=np.uint8)
    try:
        bg_model = np.zeros((1, 65), np.float64)
        fg_model = np.zeros((1, 65), np.float64)
        cv2.grabCut(small, gc_mask, rect, bg_model, fg_model, 2, init_mode)
        binary = np.where((gc_mask == cv2.GC_FGD) | (gc_mask == cv2.GC_PR_FGD), 255, 0).astype(np.uint8)
        fg_ratio = float(np.mean(binary > 0))
    except Exception:
        fg_ratio = 0.0

    # Safety fallback if GrabCut collapsed on an atypical/flat synthetic frame
    if fg_ratio < 0.05 or fg_ratio > 0.94:
        binary = np.zeros((sh, sw), dtype=np.uint8)
        if faces:
            f0 = max(faces, key=lambda x: x.get("area", 0.0))
            fx = int(round(f0["cx"] * sw))
            fy = int(round(f0["cy"] * sh))
            fw = max(14, int(round(f0["w"] * sw)))
            fh = max(14, int(round(f0["h"] * sh)))
            cv2.ellipse(binary, (fx, fy), (int(fw * 0.65), int(fh * 0.75)), 0, 0, 360, 255, -1)
            cv2.ellipse(binary, (fx, min(sh - 1, fy + int(fh * 1.35))), (int(fw * 1.4), int(sh * 0.45)), 0, 0, 360, 255, -1)
        else:
            cv2.ellipse(binary, (sw // 2, int(sh * 0.44)), (int(sw * 0.24), int(sh * 0.34)), 0, 0, 360, 255, -1)
            cv2.ellipse(binary, (sw // 2, int(sh * 0.82)), (int(sw * 0.36), int(sh * 0.32)), 0, 0, 360, 255, -1)

    kernel = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (5, 5))
    binary = cv2.morphologyEx(binary, cv2.MORPH_CLOSE, kernel)
    binary = cv2.morphologyEx(binary, cv2.MORPH_OPEN, cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (3, 3)))
    soft = cv2.GaussianBlur(binary, (9, 9), 2.2)
    return cv2.resize(soft, (w, h), interpolation=cv2.INTER_LINEAR)


def segment_foreground_mask(
    frame_bgr: np.ndarray,
    prev_mask: Optional[np.ndarray] = None,
) -> np.ndarray:
    """
    Generate an `uint8` (0..255) foreground subject alpha matte for `frame_bgr`.
    Tries local MediaPipe ImageSegmenter first and seamlessly falls back to OpenCV GrabCut + priors.
    """
    if frame_bgr is None or frame_bgr.size == 0:
        return np.full((64, 64), 255, dtype=np.uint8)

    matte = _segment_mediapipe(frame_bgr)
    if matte is None:
        matte = _segment_opencv_fallback(frame_bgr)

    if prev_mask is not None and prev_mask.shape == matte.shape:
        blended = 0.74 * matte.astype(np.float32) + 0.26 * prev_mask.astype(np.float32)
        matte = np.clip(blended, 0, 255).astype(np.uint8)
    return matte


def _parse_hex_bgr(hex_color: str, fallback: Tuple[int, int, int] = (27, 24, 24)) -> Tuple[int, int, int]:
    clean = str(hex_color or "").strip().lstrip("#")
    if len(clean) == 3:
        clean = "".join(c * 2 for c in clean)
    if len(clean) != 6:
        return fallback
    try:
        r = int(clean[0:2], 16)
        g = int(clean[2:4], 16)
        b = int(clean[4:6], 16)
        return (b, g, r)
    except Exception:
        return fallback


def build_backdrop_image(
    frame_bgr: np.ndarray,
    backdrop_mode: str = "studio_blur",
    blur_intensity: float = 18.0,
    backdrop_color: str = "#18181B",
) -> np.ndarray:
    """Build the full-resolution BGR backdrop matching `frame_bgr` dimensions."""
    h, w = frame_bgr.shape[:2]
    mode = canonicalize_backdrop_mode(backdrop_mode)
    sigma = max(4.0, min(55.0, float(blur_intensity or 18.0)))

    if mode == "studio_blur":
        blurred = cv2.GaussianBlur(frame_bgr, (0, 0), sigmaX=sigma, sigmaY=sigma)
        return np.clip(blurred.astype(np.float32) * 0.86, 0, 255).astype(np.uint8)

    if mode == "gameplay_blur":
        # Subtle punch zoom (1.12x) + heavier motion bokeh + slightly darkened studio contrast
        zh, zw = int(h / 1.12), int(w / 1.12)
        y0, x0 = max(0, (h - zh) // 2), max(0, (w - zw) // 2)
        cropped = cv2.resize(frame_bgr[y0:y0 + zh, x0:x0 + zw], (w, h), interpolation=cv2.INTER_LINEAR)
        blurred = cv2.GaussianBlur(cropped, (0, 0), sigmaX=sigma * 1.35, sigmaY=sigma * 1.35)
        return np.clip(blurred.astype(np.float32) * 0.80, 0, 255).astype(np.uint8)

    if mode == "solid_charcoal":
        base_bgr = _parse_hex_bgr(backdrop_color or "#18181B", fallback=(27, 24, 24))
        yy, xx = np.mgrid[0:h, 0:w].astype(np.float32)
        dist = np.sqrt(((xx / max(1.0, float(w))) - 0.5) ** 2 + ((yy / max(1.0, float(h))) - 0.42) ** 2)
        vign = np.clip(1.18 - dist * 0.65, 0.68, 1.18)[:, :, None]
        canvas = np.full((h, w, 3), base_bgr, dtype=np.float32) * vign
        return np.clip(canvas, 0, 255).astype(np.uint8)

    if mode == "accent_gradient":
        accent_bgr = np.array(_parse_hex_bgr(backdrop_color or "#6366F1", fallback=(241, 102, 99)), dtype=np.float32)
        deep_bgr = np.array([28.0, 20.0, 18.0], dtype=np.float32)
        top_bgr = np.clip(deep_bgr * 0.65 + accent_bgr * 0.35, 0, 255)
        bot_bgr = np.clip(deep_bgr * 0.45 + accent_bgr * 0.55, 0, 255)
        gx = np.linspace(0.0, 1.0, w, dtype=np.float32)[None, :]
        gy = np.linspace(0.0, 1.0, h, dtype=np.float32)[:, None]
        t = np.clip(0.55 * gy + 0.45 * gx, 0.0, 1.0)[:, :, None]
        grad = top_bgr[None, None, :] * (1.0 - t) + bot_bgr[None, None, :] * t
        return np.clip(grad, 0, 255).astype(np.uint8)

    return frame_bgr.copy()


def composite_foreground_over_backdrop(
    frame_bgr: np.ndarray,
    alpha_u8: np.ndarray,
    backdrop_mode: str = "studio_blur",
    blur_intensity: float = 18.0,
    backdrop_color: str = "#18181B",
) -> np.ndarray:
    """Alpha-composite sharp subject `frame_bgr` over generated backdrop."""
    mode = canonicalize_backdrop_mode(backdrop_mode)
    if mode == "none":
        return frame_bgr
    bg_bgr = build_backdrop_image(
        frame_bgr,
        backdrop_mode=mode,
        blur_intensity=blur_intensity,
        backdrop_color=backdrop_color,
    )
    a = (alpha_u8.astype(np.float32) / 255.0)[:, :, None]
    out = frame_bgr.astype(np.float32) * a + bg_bgr.astype(np.float32) * (1.0 - a)
    return np.clip(out, 0, 255).astype(np.uint8)


def generate_clip_matte_video_and_backdrop(
    exec_dir: Path,
    base_input_args: List[str],
    pre_fg_chain: str,
    target_w: int,
    target_h: int,
    fps: float,
    duration: float,
    backdrop_mode: str,
    blur_intensity: float = 18.0,
    backdrop_color: str = "#18181B",
) -> Tuple[Optional[str], Optional[str]]:
    """
    Sample framed `[v]` video frames via FFmpeg pipe at low cost, compute temporal-smoothed
    alpha matte track `fg_matte.mp4` (strictly ASCII inside `exec_dir`), and optionally render
    `bg_static.png` for solid/gradient backdrops so the main encode uses `alphamerge` + `overlay`.
    Returns `(matte_filename, static_backdrop_filename_or_None)`.
    """
    mode = canonicalize_backdrop_mode(backdrop_mode)
    if mode == "none":
        return None, None

    ffmpeg = get_ffmpeg_cmd()
    seg_w = max(160, min(256, target_w // 4 * 2))
    seg_h = max(160, int(round(seg_w * (target_h / max(1.0, float(target_w))))) // 2 * 2)
    matte_fps = max(8.0, min(24.0, float(fps or 24.0)))

    probe_fg = f"{pre_fg_chain};[v]fps={matte_fps:.2f},scale={seg_w}:{seg_h}[msrc]"
    cmd_read = [
        ffmpeg,
        "-y",
        *base_input_args,
        "-filter_complex", probe_fg,
        "-map", "[msrc]",
        "-t", f"{max(0.2, duration):.3f}",
        "-f", "rawvideo",
        "-pix_fmt", "bgr24",
        "pipe:1",
    ]
    proc = subprocess.run(cmd_read, capture_output=True, timeout=120, cwd=str(exec_dir))
    if proc.returncode != 0 or not proc.stdout:
        logger.warning(f"Matte frame extraction failed ({proc.returncode}): {proc.stderr[-300:] if proc.stderr else ''}")
        return None, None

    frame_bytes = seg_w * seg_h * 3
    raw = proc.stdout
    n_frames = len(raw) // frame_bytes
    if n_frames <= 0:
        return None, None

    matte_name = "fg_matte.mp4"
    writer = subprocess.Popen(
        [
            ffmpeg, "-y",
            "-f", "rawvideo",
            "-pix_fmt", "gray",
            "-s", f"{seg_w}x{seg_h}",
            "-r", f"{matte_fps:.2f}",
            "-i", "pipe:0",
            "-vf", f"scale={target_w}:{target_h}:flags=bilinear",
            "-c:v", "libx264",
            "-preset", "ultrafast",
            "-crf", "18",
            "-pix_fmt", "yuv420p",
            matte_name,
        ],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        cwd=str(exec_dir),
    )

    prev_m: Optional[np.ndarray] = None
    assert writer.stdin is not None
    try:
        for fi in range(n_frames):
            chunk = raw[fi * frame_bytes:(fi + 1) * frame_bytes]
            frame = np.frombuffer(chunk, dtype=np.uint8).reshape((seg_h, seg_w, 3))
            m = segment_foreground_mask(frame, prev_mask=prev_m)
            prev_m = m
            writer.stdin.write(m.tobytes())
        writer.stdin.close()
    except Exception as exc:
        logger.warning(f"Failed piping matte video: {exc}")
        try:
            writer.kill()
        except Exception:
            pass
        return None, None

    _, w_err = writer.communicate(timeout=60)
    if writer.returncode != 0 or not (exec_dir / matte_name).exists():
        logger.warning(f"Matte encode failed: {w_err[-300:] if w_err else ''}")
        return None, None

    bg_png_name: Optional[str] = None
    if mode in ("solid_charcoal", "accent_gradient"):
        dummy = np.zeros((target_h, target_w, 3), dtype=np.uint8)
        bg_img = build_backdrop_image(dummy, backdrop_mode=mode, blur_intensity=blur_intensity, backdrop_color=backdrop_color)
        bg_png_name = "bg_backdrop.png"
        cv2.imwrite(str(exec_dir / bg_png_name), bg_img)

    return matte_name, bg_png_name


def append_backdrop_filter_stage(
    fg_segments: List[str],
    curr_v_lbl: str,
    matte_input_idx: int,
    backdrop_mode: str,
    target_w: int,
    target_h: int,
    blur_intensity: float = 18.0,
    static_bg_input_idx: Optional[int] = None,
    out_label: str = "v_backdrop",
) -> str:
    """
    Append true FFmpeg `alphamerge` + `overlay` filtergraph nodes into `fg_segments`.
    Returns new video stream label.
    """
    mode = canonicalize_backdrop_mode(backdrop_mode)
    sigma = max(4.0, min(50.0, float(blur_intensity or 18.0)))
    fg_lbl = f"fg_src_{matte_input_idx}"
    fg_rgba = f"fg_rgba_{matte_input_idx}"
    matte_fmt = f"matte_g_{matte_input_idx}"
    bg_lbl = f"bg_layer_{matte_input_idx}"

    fg_segments.append(f"[{matte_input_idx}:v]scale={target_w}:{target_h},format=gray[{matte_fmt}]")

    if static_bg_input_idx is not None:
        fg_segments.append(f"[{static_bg_input_idx}:v]scale={target_w}:{target_h},format=yuv420p[{bg_lbl}]")
        fg_segments.append(f"[{curr_v_lbl}]format=rgba[{fg_lbl}]")
    elif mode == "gameplay_blur":
        fg_segments.append(f"[{curr_v_lbl}]split=2[{fg_lbl}_raw][{bg_lbl}_src]")
        fg_segments.append(
            f"[{bg_lbl}_src]crop=iw/1.12:ih/1.12:(iw-iw/1.12)/2:(ih-ih/1.12)/2,"
            f"scale={target_w}:{target_h},gblur=sigma={sigma * 1.25:.1f},eq=brightness=-0.06[{bg_lbl}]"
        )
        fg_segments.append(f"[{fg_lbl}_raw]format=rgba[{fg_lbl}]")
    else:
        fg_segments.append(f"[{curr_v_lbl}]split=2[{fg_lbl}_raw][{bg_lbl}_src]")
        fg_segments.append(
            f"[{bg_lbl}_src]gblur=sigma={sigma:.1f},eq=brightness=-0.05[{bg_lbl}]"
        )
        fg_segments.append(f"[{fg_lbl}_raw]format=rgba[{fg_lbl}]")

    fg_segments.append(f"[{fg_lbl}][{matte_fmt}]alphamerge[{fg_rgba}]")
    fg_segments.append(f"[{bg_lbl}][{fg_rgba}]overlay=0:0:shortest=1[{out_label}]")
    return out_label
