import os
import json
import math
import shutil
import tempfile
import logging
from pathlib import Path
from typing import Dict, Any, List, Optional, Tuple

from PIL import Image, ImageDraw, ImageFont, ImageFilter

from app.config import settings
from app.services.captions_ass import CAPTION_STYLES_DEF, EMOJI_KEYWORDS

logger = logging.getLogger(__name__)

FILLER_WORDS = {"um", "uh", "er", "ah", "like", "you know", "basically", "literally"}

# Map UI font names cleanly to bundled font stems and Windows/system font filenames
FONT_CANDIDATES: Dict[str, Dict[str, List[str]]] = {
    "montserrat": {
        "regular": ["Montserrat-Regular.ttf", "Montserrat-Bold.ttf", "arial.ttf", "segoeui.ttf"],
        "bold": ["Montserrat-ExtraBold.ttf", "Montserrat-Bold.ttf", "arialbd.ttf", "segoeuib.ttf"],
        "italic": ["Montserrat-Italic.ttf", "ariali.ttf", "segoeuii.ttf"],
        "bold_italic": ["Montserrat-BoldItalic.ttf", "arialbi.ttf", "segoeuiz.ttf"],
    },
    "anton": {
        "regular": ["Anton-Regular.ttf", "impact.ttf", "arialbd.ttf"],
        "bold": ["Anton-Regular.ttf", "impact.ttf", "arialbd.ttf"],
        "italic": ["Anton-Regular.ttf", "impact.ttf", "arialbi.ttf"],
        "bold_italic": ["Anton-Regular.ttf", "impact.ttf", "arialbi.ttf"],
    },
    "bebas neue": {
        "regular": ["BebasNeue-Regular.ttf", "impact.ttf", "ARIALNB.TTF", "arialbd.ttf"],
        "bold": ["BebasNeue-Regular.ttf", "impact.ttf", "ARIALNB.TTF", "arialbd.ttf"],
        "italic": ["BebasNeue-Regular.ttf", "ARIALNBI.TTF", "arialbi.ttf"],
        "bold_italic": ["BebasNeue-Regular.ttf", "ARIALNBI.TTF", "arialbi.ttf"],
    },
    "impact": {
        "regular": ["impact.ttf", "arialbd.ttf"],
        "bold": ["impact.ttf", "arialbd.ttf"],
        "italic": ["impact.ttf", "arialbi.ttf"],
        "bold_italic": ["impact.ttf", "arialbi.ttf"],
    },
    "poppins": {
        "regular": ["Poppins-Regular.ttf", "Poppins-SemiBold.ttf", "segoeui.ttf", "arial.ttf"],
        "bold": ["Poppins-ExtraBold.ttf", "Poppins-Bold.ttf", "segoeuib.ttf", "arialbd.ttf"],
        "italic": ["Poppins-Italic.ttf", "segoeuii.ttf", "ariali.ttf"],
        "bold_italic": ["Poppins-BoldItalic.ttf", "segoeuiz.ttf", "arialbi.ttf"],
    },
    "roboto": {
        "regular": ["Roboto-Regular.ttf", "segoeui.ttf", "arial.ttf"],
        "bold": ["Roboto-Black.ttf", "Roboto-Bold.ttf", "segoeuib.ttf", "arialbd.ttf"],
        "italic": ["Roboto-Italic.ttf", "segoeuii.ttf", "ariali.ttf"],
        "bold_italic": ["Roboto-BoldItalic.ttf", "segoeuiz.ttf", "arialbi.ttf"],
    },
    "playfair display": {
        "regular": ["PlayfairDisplay-Regular.ttf", "georgia.ttf", "times.ttf"],
        "bold": ["PlayfairDisplay-Bold.ttf", "georgiab.ttf", "timesbd.ttf"],
        "italic": ["PlayfairDisplay-Italic.ttf", "georgiai.ttf", "timesi.ttf"],
        "bold_italic": ["PlayfairDisplay-BoldItalic.ttf", "georgiaz.ttf", "timesbi.ttf"],
    },
    "courier prime": {
        "regular": ["CourierPrime-Regular.ttf", "cour.ttf", "consola.ttf"],
        "bold": ["CourierPrime-Bold.ttf", "courbd.ttf", "consolab.ttf"],
        "italic": ["CourierPrime-Italic.ttf", "couri.ttf", "consolai.ttf"],
        "bold_italic": ["CourierPrime-BoldItalic.ttf", "courbi.ttf", "consolaz.ttf"],
    },
}


def _parse_rgba(hex_color: Optional[str], alpha_float: float = 1.0) -> Tuple[int, int, int, int]:
    clean = (hex_color or "#FFFFFF").strip().lstrip("#")
    if len(clean) == 3:
        clean = "".join(c * 2 for c in clean)
    if len(clean) != 6:
        r, g, b = 255, 255, 255
    else:
        try:
            r = int(clean[0:2], 16)
            g = int(clean[2:4], 16)
            b = int(clean[4:6], 16)
        except ValueError:
            r, g, b = 255, 255, 255
    a = max(0, min(255, int(round(max(0.0, min(1.0, float(alpha_float))) * 255))))
    return (r, g, b, a)


def _font_search_dirs() -> List[Path]:
    dirs: List[Path] = []
    data_dir = os.environ.get("LOST_HEAVEN_DATA_DIR") or os.environ.get("CLIPSHIP_DATA_DIR")
    if data_dir:
        dirs.append(Path(data_dir) / "fonts")
    dirs.append(Path(settings.STORAGE_DIR) / "fonts")
    dirs.append(Path(r"C:\Windows\Fonts"))
    dirs.append(Path("/usr/share/fonts"))
    dirs.append(Path("/System/Library/Fonts"))
    return dirs


def resolve_pillow_font(
    font_family: str = "Montserrat",
    size_px: int = 64,
    bold: bool = True,
    italic: bool = False,
) -> ImageFont.FreeTypeFont | ImageFont.ImageFont:
    """
    Resolve bundled fonts from storage/fonts or CLIPSHIP_DATA_DIR/fonts first,
    then Windows system fonts, and finally Pillow's default font.
    """
    size_px = max(14, int(round(size_px)))
    raw = (font_family or "Montserrat").strip()
    norm = raw.lower()
    if norm.endswith(" extrabold"):
        norm_base = norm[:-10].strip()
        bold = True
    elif norm.endswith(" black"):
        norm_base = norm[:-6].strip()
        bold = True
    elif norm.endswith(" bold"):
        norm_base = norm[:-5].strip()
        bold = True
    else:
        norm_base = norm

    style_key = (
        "bold_italic" if (bold and italic)
        else "bold" if bold
        else "italic" if italic
        else "regular"
    )

    candidates: List[str] = []
    if norm_base in FONT_CANDIDATES:
        candidates.extend(FONT_CANDIDATES[norm_base][style_key])
    # Also try direct custom font filename matching (e.g. uploaded fonts)
    stem = raw.replace(" ", "")
    candidates.extend([
        f"{raw}.ttf", f"{raw}.otf",
        f"{stem}.ttf", f"{stem}.otf",
        f"{stem}-Bold.ttf", f"{stem}-Regular.ttf",
    ])
    candidates.extend(FONT_CANDIDATES["montserrat"][style_key])

    search_dirs = _font_search_dirs()
    for d in search_dirs:
        if not d.exists():
            continue
        for fname in candidates:
            candidate_path = d / fname
            if candidate_path.is_file():
                try:
                    return ImageFont.truetype(str(candidate_path), size=size_px)
                except Exception:
                    continue
        # Substring match inside custom font folders (non-Windows system)
        if str(d).lower().endswith("fonts") and d != Path(r"C:\Windows\Fonts"):
            try:
                for p in d.iterdir():
                    if p.suffix.lower() in (".ttf", ".otf") and norm_base.replace(" ", "") in p.stem.lower().replace(" ", ""):
                        return ImageFont.truetype(str(p), size=size_px)
            except Exception:
                pass

    # Guaranteed Pillow fallback (never crashes)
    try:
        return ImageFont.load_default(size=size_px)
    except TypeError:
        return ImageFont.load_default()


def normalize_and_chunk_words(
    words: Optional[List[Dict[str, Any]]],
    clip_start_time: float = 0.0,
    clip_duration: float = 0.0,
    project_id: Optional[int] = None,
    timing_offset: float = 0.0,
    clean_filler_words: bool = False,
    word_by_word: bool = False,
    max_words_per_chunk: int = 4,
    max_chars_per_line: int = 18,
    bridge_gap_sec: float = 0.15,
) -> List[Dict[str, Any]]:
    """
    Normalize transcript words to clip-relative [0.0, clip_duration] timestamps,
    group into punchy 2-4 word chunks (~14-18 chars max), and bridge micro-silences (< 0.15s).
    Returns list of chunks: [{"start": float, "end": float, "words": [word_dict, ...]}].
    """
    raw_words = list(words or [])
    if not raw_words and project_id is not None:
        t_path = Path(settings.STORAGE_DIR) / "projects" / str(project_id) / "transcript.json"
        if t_path.exists():
            try:
                with open(t_path, "r", encoding="utf-8") as f:
                    full_words = json.load(f)
                if isinstance(full_words, list):
                    win_end = clip_start_time + clip_duration if clip_duration > 0 else float("inf")
                    raw_words = [
                        dict(w)
                        for w in full_words
                        if float(w.get("end", 0.0)) > clip_start_time
                        and float(w.get("start", 0.0)) < win_end
                    ]
            except Exception as e:
                logger.warning(f"Failed loading fallback transcript.json for project {project_id}: {e}")

    if not raw_words:
        return []

    max_raw_end = max(float(w.get("end", 0.0)) for w in raw_words)
    is_absolute = (
        clip_start_time > 0.0
        and (
            (clip_duration > 0.0 and max_raw_end > clip_duration + 1.0)
            or (clip_duration <= 0.0 and min(float(w.get("start", 0.0)) for w in raw_words) >= max(0.0, clip_start_time - 0.5))
        )
    )

    normalized: List[Dict[str, Any]] = []
    for w in raw_words:
        if w.get("deleted") or w.get("muted") or w.get("excluded"):
            continue
        text = str(w.get("word", "")).strip()
        if not text:
            continue
        if clean_filler_words and text.lower().strip(".,!?") in FILLER_WORDS:
            continue

        ws = float(w.get("start", 0.0))
        we = float(w.get("end", ws + 0.25))
        if is_absolute:
            rel_s = ws - clip_start_time
            rel_e = we - clip_start_time
            if rel_e <= 0.0:
                continue
            if clip_duration > 0.0 and rel_s >= clip_duration:
                continue
            rel_s = max(0.0, rel_s)
            if clip_duration > 0.0:
                rel_e = min(clip_duration, rel_e)
        else:
            rel_s = max(0.0, ws)
            rel_e = min(clip_duration, we) if clip_duration > 0.0 else we

        rel_s = max(0.0, rel_s + float(timing_offset or 0.0))
        rel_e = max(rel_s + 0.08, rel_e + float(timing_offset or 0.0))
        if clip_duration > 0.0:
            rel_e = min(clip_duration, max(rel_s + 0.08, rel_e))

        normalized.append({"word": text, "start": round(rel_s, 3), "end": round(rel_e, 3)})

    normalized.sort(key=lambda item: item["start"])
    if not normalized:
        return []

    if word_by_word:
        chunks = [
            {"start": w["start"], "end": w["end"], "words": [dict(w)]}
            for w in normalized
        ]
        # Bridge micro-gaps between consecutive solo words too
        for idx in range(len(chunks) - 1):
            gap = chunks[idx + 1]["start"] - chunks[idx]["end"]
            if 0.0 < gap < bridge_gap_sec:
                chunks[idx]["end"] = chunks[idx + 1]["start"]
                chunks[idx]["words"][0]["end"] = chunks[idx + 1]["start"]
        return chunks

    # Group into 2-4 word punchy chunks (~14-18 chars max)
    chunks: List[Dict[str, Any]] = []
    curr: List[Dict[str, Any]] = []
    curr_chars = 0

    for w in normalized:
        word_text = w["word"]
        projected_chars = curr_chars + (1 if curr else 0) + len(word_text)
        gap_prev = (w["start"] - curr[-1]["end"]) if curr else 0.0

        should_flush = False
        if curr:
            if len(curr) >= max_words_per_chunk:
                should_flush = True
            elif len(curr) >= 2 and projected_chars > max_chars_per_line:
                should_flush = True
            elif gap_prev > 0.65:
                should_flush = True

        if should_flush:
            chunks.append({"start": curr[0]["start"], "end": curr[-1]["end"], "words": curr})
            curr = []
            curr_chars = 0

        curr.append(dict(w))
        curr_chars += (1 if len(curr) > 1 else 0) + len(word_text)

        if word_text.endswith((".", "?", "!")) and len(curr) >= 2:
            chunks.append({"start": curr[0]["start"], "end": curr[-1]["end"], "words": curr})
            curr = []
            curr_chars = 0

    if curr:
        chunks.append({"start": curr[0]["start"], "end": curr[-1]["end"], "words": curr})

    # Bridge micro-silences shorter than 0.15s inside each chunk & across adjacent chunks
    for ch in chunks:
        cwords = ch["words"]
        for i in range(len(cwords) - 1):
            gap = cwords[i + 1]["start"] - cwords[i]["end"]
            if 0.0 <= gap < bridge_gap_sec:
                cwords[i]["end"] = cwords[i + 1]["start"]
        ch["start"] = cwords[0]["start"]
        ch["end"] = max(ch["start"] + 0.08, cwords[-1]["end"])

    for i in range(len(chunks) - 1):
        gap = chunks[i + 1]["start"] - chunks[i]["end"]
        if 0.0 < gap < bridge_gap_sec:
            chunks[i]["end"] = chunks[i + 1]["start"]
            chunks[i]["words"][-1]["end"] = chunks[i + 1]["start"]

    return chunks


def _resolve_style_settings(
    style_preset: str = "Karaoke pop",
    font_family: Optional[str] = None,
    font_size_pct: int = 100,
    bold: Optional[bool] = None,
    italic: Optional[bool] = None,
    uppercase: Optional[bool] = None,
    base_color: Optional[str] = None,
    highlight_color: Optional[str] = None,
    outline_color: Optional[str] = None,
    highlight_shape: Optional[str] = None,
    hold_back_opacity: float = 1.0,
    outline: Optional[float] = None,
    shadow: Optional[float] = None,
    glow: float = 0.0,
    letter_spacing: float = 0.0,
    alignment: str = "Centre",
    pos_x: Optional[float] = None,
    pos_y: Optional[float] = None,
    position_grid: str = "bottom_center",
) -> Dict[str, Any]:
    preset_def = CAPTION_STYLES_DEF.get(style_preset, CAPTION_STYLES_DEF["Karaoke pop"])
    norm_shape = (highlight_shape or "").strip().lower().replace(" ", "_")
    if not norm_shape:
        if preset_def.get("border_style") == 3 or style_preset in ("Punch Box", "Brand Block"):
            norm_shape = "filled_box"
        elif style_preset == "Underline Pop":
            norm_shape = "underline"
        else:
            norm_shape = "colour_only"
    elif norm_shape in ("pill_box", "filled_box"):
        norm_shape = "filled_box"
    elif norm_shape in ("colour_only", "color_only"):
        norm_shape = "colour_only"

    eff_glow = float(glow or 0.0)
    if eff_glow == 0.0 and style_preset == "Neon Glow":
        eff_glow = 3.0

    # Grid fallback if pos_x/pos_y not passed explicitly
    grid_coords = {
        "top_left": (15.0, 18.0), "top_center": (50.0, 18.0), "top_right": (85.0, 18.0),
        "mid_left": (15.0, 50.0), "center": (50.0, 50.0), "mid_right": (85.0, 50.0),
        "bottom_left": (15.0, 74.0), "bottom_center": (50.0, 74.0), "bottom_right": (85.0, 74.0),
    }
    gx, gy = grid_coords.get(position_grid, (50.0, 74.0))

    return {
        "style_preset": style_preset,
        "font_family": font_family or preset_def.get("font", "Montserrat"),
        "base_font_size": int(preset_def.get("font_size", 64)),
        "font_size_pct": int(font_size_pct or 100),
        "bold": bool(bold) if bold is not None else (style_preset not in ("Cinematic", "Documentary")),
        "italic": bool(italic) if italic is not None else (style_preset == "Cinematic"),
        "uppercase": bool(uppercase) if uppercase is not None else bool(preset_def.get("all_caps", True)),
        "base_color": base_color or preset_def.get("base_color", "#FFFFFF"),
        "highlight_color": highlight_color or preset_def.get("active_color", "#C6F32F"),
        "outline_color": outline_color or preset_def.get("outline_color", "#000000"),
        "highlight_shape": norm_shape,
        "hold_back_opacity": max(0.05, min(1.0, float(hold_back_opacity if hold_back_opacity is not None else 1.0))),
        "outline": float(outline if outline is not None else preset_def.get("outline_width", 4.0)),
        "shadow": float(shadow if shadow is not None else preset_def.get("shadow_depth", 3.0)),
        "glow": eff_glow,
        "letter_spacing": float(letter_spacing or 0.0),
        "alignment": (alignment or "Centre").strip().lower(),
        "pos_x": float(pos_x if pos_x is not None else gx),
        "pos_y": float(pos_y if pos_y is not None else gy),
        "is_full_line_block": style_preset == "Brand Block",
    }


def _measure_text(
    draw: ImageDraw.ImageDraw,
    text: str,
    font: ImageFont.FreeTypeFont | ImageFont.ImageFont,
    letter_spacing: float = 0.0,
) -> Tuple[float, float]:
    if not text:
        return 0.0, 0.0
    if abs(letter_spacing) < 0.25:
        bbox = draw.textbbox((0, 0), text, font=font)
        return float(bbox[2] - bbox[0]), float(bbox[3] - bbox[1])
    total_w = 0.0
    max_h = 0.0
    for idx, ch in enumerate(text):
        cb = draw.textbbox((0, 0), ch, font=font)
        total_w += float(cb[2] - cb[0])
        if idx < len(text) - 1:
            total_w += letter_spacing
        max_h = max(max_h, float(cb[3] - cb[1]))
    return total_w, max_h


def _draw_spaced_text(
    draw: ImageDraw.ImageDraw,
    xy: Tuple[float, float],
    text: str,
    font: ImageFont.FreeTypeFont | ImageFont.ImageFont,
    fill: Tuple[int, int, int, int],
    letter_spacing: float = 0.0,
    stroke_width: int = 0,
    stroke_fill: Optional[Tuple[int, int, int, int]] = None,
):
    x, y = xy
    if abs(letter_spacing) < 0.25:
        draw.text(
            (x, y),
            text,
            font=font,
            fill=fill,
            stroke_width=stroke_width,
            stroke_fill=stroke_fill,
        )
        return

    cursor_x = x
    for ch in text:
        draw.text(
            (cursor_x, y),
            ch,
            font=font,
            fill=fill,
            stroke_width=stroke_width,
            stroke_fill=stroke_fill,
        )
        cb = draw.textbbox((0, 0), ch, font=font)
        cursor_x += float(cb[2] - cb[0]) + letter_spacing


def _wrap_text_to_width(
    draw: ImageDraw.ImageDraw,
    text: str,
    font: ImageFont.FreeTypeFont | ImageFont.ImageFont,
    max_width: float,
) -> List[str]:
    words = text.split()
    lines: List[str] = []
    curr: List[str] = []
    for w in words:
        trial = " ".join(curr + [w])
        tw, _ = _measure_text(draw, trial, font)
        if curr and tw > max_width:
            lines.append(" ".join(curr))
            curr = [w]
        else:
            curr.append(w)
    if curr:
        lines.append(" ".join(curr))
    return lines


def _draw_hook_banner(
    canvas: Image.Image,
    hook_headline: str,
    width: int,
    height: int,
):
    clean = (hook_headline or "").strip().upper()
    if not clean:
        return

    scale = max(0.45, width / 1080.0)
    font = resolve_pillow_font("Montserrat", int(round(36 * scale)), bold=True, italic=False)
    draw = ImageDraw.Draw(canvas)

    margin_x = int(round(90 * scale))
    max_text_w = max(120, width - margin_x * 2 - int(round(36 * scale)))
    lines = _wrap_text_to_width(draw, clean, font, max_text_w)
    if not lines:
        return

    line_sizes = [_measure_text(draw, ln, font) for ln in lines]
    max_lw = max(w for w, _ in line_sizes)
    line_h = max(h for _, h in line_sizes)
    pad_x = int(round(26 * scale))
    pad_y = int(round(14 * scale))
    gap_y = int(round(8 * scale))

    box_w = min(width - margin_x * 2, int(round(max_lw)) + pad_x * 2)
    box_h = int(round(len(lines) * line_h + (len(lines) - 1) * gap_y)) + pad_y * 2
    box_x0 = (width - box_w) // 2
    box_y0 = max(24, int(round(height * 0.075)))
    box_x1 = box_x0 + box_w
    box_y1 = box_y0 + box_h

    draw.rounded_rectangle(
        [box_x0, box_y0, box_x1, box_y1],
        radius=max(10, int(round(14 * scale))),
        fill=(14, 14, 18, 220),
        outline=(245, 158, 11, 245),
        width=max(2, int(round(3 * scale))),
    )

    curr_y = box_y0 + pad_y
    for idx, ln in enumerate(lines):
        lw, _ = line_sizes[idx]
        lx = (width - lw) / 2.0
        draw.text(
            (lx, curr_y),
            ln,
            font=font,
            fill=(255, 230, 80, 255),
            stroke_width=max(1, int(round(2 * scale))),
            stroke_fill=(0, 0, 0, 255),
        )
        curr_y += line_h + gap_y


def _draw_retention_bar(
    canvas: Image.Image,
    progress_ratio: float,
    width: int,
    height: int,
    color_hex: str = "#C6F32F",
) -> None:
    """Draw a sleek animated retention progress bar along the bottom edge."""
    ratio = max(0.02, min(1.0, float(progress_ratio)))
    scale = max(0.45, width / 1080.0)
    bar_h = max(6, int(round(9 * scale)))
    margin_x = max(16, int(round(width * 0.05)))
    y1 = height - max(10, int(round(14 * scale)))
    y0 = y1 - bar_h
    track_w = max(40, width - margin_x * 2)
    fill_w = max(bar_h, int(round(track_w * ratio)))

    draw = ImageDraw.Draw(canvas)
    draw.rounded_rectangle(
        [margin_x, y0, margin_x + track_w, y1],
        radius=bar_h // 2,
        fill=(255, 255, 255, 65),
    )
    bar_rgba = _parse_rgba(color_hex or "#C6F32F", alpha_float=0.95)
    draw.rounded_rectangle(
        [margin_x, y0, margin_x + fill_w, y1],
        radius=bar_h // 2,
        fill=bar_rgba,
    )


def _draw_outro_cta_card(
    canvas: Image.Image,
    t: float,
    clip_duration: float,
    width: int,
    height: int,
    cta_text: str = "Subscribe for More",
    accent_hex: str = "#C6F32F",
) -> None:
    """Render an animated 2-second end-card CTA badge in the final 2.0s of the clip."""
    if clip_duration <= 2.2 or t < (clip_duration - 2.0):
        return
    elapsed_in_card = max(0.0, t - (clip_duration - 2.0))
    fade = min(1.0, elapsed_in_card / 0.35)
    alpha_bg = int(round(220 * fade))
    alpha_fg = int(round(255 * fade))
    if alpha_fg < 10:
        return

    scale = max(0.45, width / 1080.0)
    font_px = max(20, int(round(36 * scale)))
    font = resolve_pillow_font("Montserrat", font_px, bold=True)
    text = (cta_text or "Subscribe for More").strip() or "Subscribe for More"
    label = f"{text}  →"

    draw = ImageDraw.Draw(canvas)
    tw, th = _measure_text(draw, label, font)
    pad_x = int(round(28 * scale))
    pad_y = int(round(14 * scale))
    box_w = min(width - 40, int(round(tw)) + pad_x * 2)
    box_h = int(round(th)) + pad_y * 2
    x0 = (width - box_w) // 2
    y0 = max(40, int(round(height * 0.82)) - box_h // 2)

    r, g, b, _ = _parse_rgba(accent_hex or "#C6F32F", 1.0)
    draw.rounded_rectangle(
        [x0, y0, x0 + box_w, y0 + box_h],
        radius=max(12, box_h // 2),
        fill=(14, 15, 20, alpha_bg),
        outline=(r, g, b, alpha_fg),
        width=max(2, int(round(3 * scale))),
    )
    draw.text(
        ((width - tw) / 2.0, y0 + pad_y - int(round(2 * scale))),
        label,
        font=font,
        fill=(r, g, b, alpha_fg),
    )


def render_caption_frame(
    t: float,
    width: int,
    height: int,
    words: Optional[List[Dict[str, Any]]] = None,
    *,
    burn_captions: bool = True,
    style_preset: str = "Karaoke pop",
    font_family: Optional[str] = None,
    font_size_pct: int = 100,
    bold: Optional[bool] = None,
    italic: Optional[bool] = None,
    uppercase: Optional[bool] = None,
    alignment: str = "Centre",
    position_grid: str = "bottom_center",
    pos_x: Optional[float] = 50.0,
    pos_y: Optional[float] = 74.0,
    base_color: Optional[str] = None,
    highlight_color: Optional[str] = None,
    outline_color: Optional[str] = None,
    highlight_shape: Optional[str] = None,
    hold_back_opacity: float = 1.0,
    outline: Optional[float] = None,
    shadow: Optional[float] = None,
    glow: float = 0.0,
    letter_spacing: float = 0.0,
    word_by_word: bool = False,
    auto_emoji: bool = False,
    hook_banner_enabled: bool = False,
    hook_headline: str = "",
    retention_bar_enabled: bool = False,
    retention_bar_color: str = "#C6F32F",
    outro_cta_enabled: bool = False,
    outro_cta_text: str = "Subscribe for More",
    clip_start_time: float = 0.0,
    clip_duration: float = 0.0,
    project_id: Optional[int] = None,
    timing_offset: float = 0.0,
    clean_filler_words: bool = False,
    highlight_power_words: bool = False,
    precomputed_chunks: Optional[List[Dict[str, Any]]] = None,
) -> Image.Image:
    """
    Render a full-resolution transparent RGBA caption + retention/CTA overlay frame at clip-relative time `t`.
    Returns a Pillow RGBA Image pixel-identical between preview_frame.py and renderer.py.
    """
    canvas = Image.new("RGBA", (int(width), int(height)), (0, 0, 0, 0))

    if retention_bar_enabled:
        dur_safe = max(0.5, float(clip_duration or 1.0))
        _draw_retention_bar(canvas, t / dur_safe, width, height, color_hex=retention_bar_color)

    if outro_cta_enabled and clip_duration > 2.2 and t >= (clip_duration - 2.0):
        _draw_outro_cta_card(
            canvas, t, clip_duration, width, height,
            cta_text=outro_cta_text,
            accent_hex=highlight_color or retention_bar_color or "#C6F32F",
        )

    if not burn_captions:
        return canvas

    if hook_banner_enabled and hook_headline and 0.0 <= t <= 3.5:
        _draw_hook_banner(canvas, hook_headline, width, height)

    chunks = (
        precomputed_chunks
        if precomputed_chunks is not None
        else normalize_and_chunk_words(
            words=words,
            clip_start_time=clip_start_time,
            clip_duration=clip_duration,
            project_id=project_id,
            timing_offset=timing_offset,
            clean_filler_words=clean_filler_words,
            word_by_word=word_by_word,
        )
    )

    active_chunk: Optional[Dict[str, Any]] = None
    for ch in chunks:
        if ch["start"] <= t <= ch["end"] + 1e-4:
            active_chunk = ch
            break
    if active_chunk is None:
        return canvas

    cwords = active_chunk["words"]
    if not cwords:
        return canvas

    active_word_idx = 0
    for idx, w in enumerate(cwords):
        if w["start"] <= t <= w["end"] + 1e-4:
            active_word_idx = idx
            break
        if w["start"] <= t:
            active_word_idx = idx

    cfg = _resolve_style_settings(
        style_preset=style_preset,
        font_family=font_family,
        font_size_pct=font_size_pct,
        bold=bold,
        italic=italic,
        uppercase=uppercase,
        base_color=base_color,
        highlight_color=highlight_color,
        outline_color=outline_color,
        highlight_shape=highlight_shape,
        hold_back_opacity=hold_back_opacity,
        outline=outline,
        shadow=shadow,
        glow=glow,
        letter_spacing=letter_spacing,
        alignment=alignment,
        pos_x=pos_x,
        pos_y=pos_y,
        position_grid=position_grid,
    )

    scale = max(0.45, width / 1080.0)
    font_px = max(18, int(round(cfg["base_font_size"] * (cfg["font_size_pct"] / 100.0) * scale)))
    font = resolve_pillow_font(cfg["font_family"], font_px, bold=cfg["bold"], italic=cfg["italic"])

    dummy_draw = ImageDraw.Draw(canvas)
    space_w, _ = _measure_text(dummy_draw, " ", font, letter_spacing=0.0)
    if space_w < 4.0:
        space_w = font_px * 0.28

    power_words = getattr(render_caption_frame, "_power_words", None)
    if power_words is None:
        power_words = frozenset({
            "secret", "secrets", "never", "always", "stop", "mistake", "mistakes",
            "truth", "hack", "hacks", "insane", "crazy", "free", "viral", "million",
            "millions", "billion", "money", "rich", "broke", "dead", "killed",
            "warning", "exposed", "proven", "guarantee", "guaranteed", "instant",
            "instantly", "literally", "actually", "shocking", "banned", "illegal",
            "hidden", "finally", "urgent", "critical", "forever", "everything",
            "nothing", "zero", "first", "last", "only", "worst", "best", "massive",
            "huge", "deadly", "fear", "win", "winning", "loser", "scam", "lie",
            "lying", "power", "powerful", "obsessed", "destroy", "destroyed",
            "explode", "exploded", "unlock", "unlocked", "master", "cheat", "now",
        })
        render_caption_frame._power_words = power_words

    emoji_font_cache = getattr(render_caption_frame, "_emoji_fonts", None)
    if emoji_font_cache is None:
        emoji_font_cache = {}
        render_caption_frame._emoji_fonts = emoji_font_cache

    base_font_px = max(12, int(getattr(font, "size", 48)))
    font_path = getattr(font, "path", None)
    power_font = font
    if highlight_power_words and font_path:
        try:
            power_font = ImageFont.truetype(font_path, max(12, int(round(base_font_px * 1.08))))
        except Exception:
            power_font = font

    emoji_px = max(14, int(round(base_font_px * 0.88)))
    if emoji_px not in emoji_font_cache:
        loaded_efont = None
        for cand_epath in (
            r"C:\Windows\Fonts\seguiemj.ttf",
            "/usr/share/fonts/truetype/noto/NotoColorEmoji.ttf",
            "/System/Library/Fonts/Apple Color Emoji.ttc",
        ):
            if os.path.exists(cand_epath):
                try:
                    loaded_efont = ImageFont.truetype(cand_epath, emoji_px)
                    break
                except Exception:
                    continue
        emoji_font_cache[emoji_px] = loaded_efont
    emoji_font = emoji_font_cache[emoji_px]

    display_tokens: List[str] = []
    token_widths: List[float] = []
    token_heights: List[float] = []
    token_emojis: List[Optional[str]] = []
    token_emoji_offsets: List[float] = []
    token_is_power: List[bool] = []
    token_fonts: List[Any] = []

    for w in cwords:
        raw_tok = str(w.get("word", "")).strip()
        clean_key = raw_tok.lower().strip(".,!?:;\"'()[]")
        tok = raw_tok.upper() if cfg["uppercase"] else raw_tok
        is_power = bool(highlight_power_words and clean_key in power_words)
        tok_font = power_font if is_power else font

        matched_emoji: Optional[str] = None
        emoji_prefix_w = 0.0
        emoji_h = 0.0
        if auto_emoji:
            matched_emoji = EMOJI_KEYWORDS.get(clean_key)
            if matched_emoji:
                emoji_gap = max(6.0, base_font_px * 0.16)
                if emoji_font is not None:
                    try:
                        ebb = dummy_draw.textbbox((0, 0), matched_emoji, font=emoji_font, embedded_color=True)
                        emoji_prefix_w = max(float(emoji_px), float(ebb[2] - ebb[0])) + emoji_gap
                        emoji_h = float(ebb[3] - ebb[1])
                    except Exception:
                        emoji_prefix_w = float(emoji_px) + emoji_gap
                        emoji_h = float(emoji_px)
                else:
                    emoji_prefix_w = float(emoji_px) + emoji_gap
                    emoji_h = float(emoji_px)

        tw, th = _measure_text(dummy_draw, tok, tok_font, letter_spacing=cfg["letter_spacing"] * scale)
        display_tokens.append(tok)
        token_widths.append(tw + emoji_prefix_w)
        token_heights.append(max(th, emoji_h))
        token_emojis.append(matched_emoji)
        token_emoji_offsets.append(emoji_prefix_w)
        token_is_power.append(is_power)
        token_fonts.append(tok_font)

    max_line_w = max(120.0, width * 0.86)
    lines_idx: List[List[int]] = []
    curr_line: List[int] = []
    curr_w = 0.0
    for idx in range(len(display_tokens)):
        add_w = token_widths[idx] + (space_w if curr_line else 0.0)
        if curr_line and curr_w + add_w > max_line_w:
            lines_idx.append(curr_line)
            curr_line = [idx]
            curr_w = token_widths[idx]
        else:
            curr_line.append(idx)
            curr_w += add_w
    if curr_line:
        lines_idx.append(curr_line)

    line_gap = max(8.0, font_px * 0.22)
    line_heights = [max(token_heights[i] for i in ln) for ln in lines_idx]
    total_h = sum(line_heights) + max(0, len(lines_idx) - 1) * line_gap
    line_widths = [
        sum(token_widths[i] for i in ln) + max(0, len(ln) - 1) * space_w
        for ln in lines_idx
    ]
    max_block_w = max(line_widths) if line_widths else 0.0

    anchor_x = (cfg["pos_x"] / 100.0) * width
    anchor_y = (cfg["pos_y"] / 100.0) * height

    # Keep block cleanly inside frame margins
    margin_pad = max(16.0, 24.0 * scale)
    start_y = max(margin_pad, min(height - total_h - margin_pad, anchor_y - total_h / 2.0))

    stroke_px = max(0, int(round(cfg["outline"] * scale)))
    shadow_px = max(0, int(round(cfg["shadow"] * scale)))
    spacing_px = cfg["letter_spacing"] * scale

    inactive_rgba = _parse_rgba(cfg["base_color"], alpha_float=cfg["hold_back_opacity"])
    active_rgba = _parse_rgba(cfg["highlight_color"], alpha_float=1.0)
    outline_rgba = _parse_rgba(cfg["outline_color"], alpha_float=1.0)
    shadow_rgba = (0, 0, 0, 185)

    # Compute exact token coordinates first
    token_positions: List[Tuple[int, float, float, float, float]] = []
    cy = start_y
    for ln_i, ln in enumerate(lines_idx):
        lw = line_widths[ln_i]
        lh = line_heights[ln_i]
        if cfg["alignment"] == "left":
            lx = max(margin_pad, min(width - lw - margin_pad, anchor_x - max_block_w / 2.0))
        elif cfg["alignment"] == "right":
            lx = max(margin_pad, min(width - lw - margin_pad, anchor_x + max_block_w / 2.0 - lw))
        else:
            lx = max(margin_pad, min(width - lw - margin_pad, anchor_x - lw / 2.0))

        cx = lx
        for tok_i in ln:
            token_positions.append((tok_i, cx, cy, token_widths[tok_i], lh))
            cx += token_widths[tok_i] + space_w
        cy += lh + line_gap

    # 1. Backdrop pill boxes / Brand Block / Underlines layer
    bg_layer = Image.new("RGBA", (int(width), int(height)), (0, 0, 0, 0))
    bg_draw = ImageDraw.Draw(bg_layer)

    if cfg["is_full_line_block"]:
        pad_x = max(14, int(round(18 * scale)))
        pad_y = max(8, int(round(10 * scale)))
        for ln_i, ln in enumerate(lines_idx):
            matching = [tp for tp in token_positions if tp[0] in ln]
            if not matching:
                continue
            x0 = min(tp[1] for tp in matching) - pad_x
            y0 = min(tp[2] for tp in matching) - pad_y
            x1 = max(tp[1] + tp[3] for tp in matching) + pad_x
            y1 = max(tp[2] + tp[4] for tp in matching) + pad_y
            bg_draw.rounded_rectangle(
                [x0, y0, x1, y1],
                radius=max(8, int(round(14 * scale))),
                fill=_parse_rgba(cfg["outline_color"], alpha_float=0.95),
            )
    elif cfg["highlight_shape"] == "filled_box":
        pad_x = max(10, int(round(14 * scale)))
        pad_y = max(5, int(round(7 * scale)))
        for tok_i, tx, ty, tw, th in token_positions:
            if tok_i == active_word_idx:
                pill_fill = _parse_rgba(cfg["highlight_color"], alpha_float=0.95)
                bg_draw.rounded_rectangle(
                    [tx - pad_x, ty - pad_y, tx + tw + pad_x, ty + th + pad_y],
                    radius=max(8, int(round(12 * scale))),
                    fill=pill_fill,
                )
    elif cfg["highlight_shape"] == "underline":
        bar_h = max(4, int(round(6 * scale)))
        for tok_i, tx, ty, tw, th in token_positions:
            if tok_i == active_word_idx:
                bg_draw.rounded_rectangle(
                    [tx, ty + th + max(3, int(round(4 * scale))), tx + tw, ty + th + max(3, int(round(4 * scale))) + bar_h],
                    radius=bar_h // 2,
                    fill=active_rgba,
                )

    canvas = Image.alpha_composite(canvas, bg_layer)

    # 2. Glow layer (Gaussian blurred duplicate)
    if cfg["glow"] > 0.0:
        glow_layer = Image.new("RGBA", (int(width), int(height)), (0, 0, 0, 0))
        glow_draw = ImageDraw.Draw(glow_layer)
        for tok_i, tx, ty, _, _ in token_positions:
            tok = display_tokens[tok_i]
            is_active = tok_i == active_word_idx
            glow_col = active_rgba if is_active else _parse_rgba(cfg["base_color"], alpha_float=0.45)
            _draw_spaced_text(
                glow_draw,
                (tx, ty),
                tok,
                font,
                fill=glow_col,
                letter_spacing=spacing_px,
                stroke_width=max(1, stroke_px // 2),
                stroke_fill=glow_col,
            )
        glow_layer = glow_layer.filter(ImageFilter.GaussianBlur(radius=max(1.5, cfg["glow"] * 2.2 * scale)))
        canvas = Image.alpha_composite(canvas, glow_layer)

    # 3. Shadow + Crisp Foreground Text Layer
    fg_layer = Image.new("RGBA", (int(width), int(height)), (0, 0, 0, 0))
    fg_draw = ImageDraw.Draw(fg_layer)

    if shadow_px > 0 and not cfg["is_full_line_block"]:
        for tok_i, tx, ty, _, _ in token_positions:
            _draw_spaced_text(
                fg_draw,
                (tx + shadow_px, ty + shadow_px),
                display_tokens[tok_i],
                font,
                fill=shadow_rgba,
                letter_spacing=spacing_px,
                stroke_width=stroke_px,
                stroke_fill=shadow_rgba,
            )

    for tok_i, tx, ty, _, tok_h in token_positions:
        is_active = tok_i == active_word_idx
        is_power = token_is_power[tok_i] if tok_i < len(token_is_power) else False
        tok_font = token_fonts[tok_i] if tok_i < len(token_fonts) else font
        emoji_char = token_emojis[tok_i] if tok_i < len(token_emojis) else None
        emoji_x_off = token_emoji_offsets[tok_i] if tok_i < len(token_emoji_offsets) else 0.0

        if cfg["highlight_shape"] == "filled_box" and is_active:
            # High-contrast dark ink when pill box background matches highlight_color
            r, g, b, _ = active_rgba
            luminance = (0.299 * r + 0.587 * g + 0.114 * b)
            text_fill = (12, 12, 16, 255) if luminance > 145 else (255, 255, 255, 255)
            tok_stroke = 0
        elif is_active or is_power:
            text_fill = active_rgba
            tok_stroke = 0 if cfg["is_full_line_block"] else stroke_px
        else:
            text_fill = inactive_rgba
            tok_stroke = 0 if cfg["is_full_line_block"] else stroke_px

        if emoji_char and emoji_x_off > 0:
            ey = ty + max(0.0, (tok_h - emoji_px) * 0.35)
            if emoji_font is not None:
                try:
                    fg_draw.text((tx, ey), emoji_char, font=emoji_font, embedded_color=True)
                except TypeError:
                    fg_draw.text((tx, ey), emoji_char, font=emoji_font, fill=active_rgba)
            else:
                # Fallback crisp accent badge when color emoji font is absent (prevents tofu boxes)
                badge_r = max(4.0, emoji_px * 0.25)
                fg_draw.rounded_rectangle(
                    [tx, ey, tx + emoji_px, ey + emoji_px],
                    radius=badge_r,
                    fill=(active_rgba[0], active_rgba[1], active_rgba[2], 235),
                )
                cx = tx + emoji_px * 0.5
                cy = ey + emoji_px * 0.5
                dot_r = max(2.0, emoji_px * 0.18)
                fg_draw.ellipse(
                    [cx - dot_r, cy - dot_r, cx + dot_r, cy + dot_r],
                    fill=(255, 255, 255, 245),
                )

        power_y_off = -max(1.0, base_font_px * 0.04) if is_power and tok_font is not font else 0.0
        _draw_spaced_text(
            fg_draw,
            (tx + emoji_x_off, ty + power_y_off),
            display_tokens[tok_i],
            tok_font,
            fill=text_fill,
            letter_spacing=spacing_px,
            stroke_width=tok_stroke,
            stroke_fill=outline_rgba if tok_stroke > 0 else None,
        )

    canvas = Image.alpha_composite(canvas, fg_layer)
    return canvas


def build_caption_ffconcat(
    words: Optional[List[Dict[str, Any]]],
    clip_duration: float,
    width: int,
    height: int,
    *,
    burn_captions: bool = True,
    hook_banner_enabled: bool = False,
    hook_headline: str = "",
    clip_start_time: float = 0.0,
    project_id: Optional[int] = None,
    timing_offset: float = 0.0,
    clean_filler_words: bool = False,
    word_by_word: bool = False,
    retention_bar_enabled: bool = False,
    retention_bar_color: str = "#C6F32F",
    outro_cta_enabled: bool = False,
    outro_cta_text: str = "Subscribe for More",
    **style_kwargs: Any,
) -> Tuple[Path, Path, int]:
    """
    Generate unique transparent PNG frames + captions.ffconcat inside an ASCII-only
    temp folder (`tempfile.mkdtemp(prefix='lost_heaven_cap_')`).
    Repeats the final `file '...'` line at the end as required by FFmpeg's concat demuxer.
    Returns (temp_dir, ffconcat_path, unique_png_count).
    """
    temp_dir = Path(tempfile.mkdtemp(prefix="lost_heaven_cap_"))
    dur_total = max(0.1, float(clip_duration or 1.0))

    blank_path = temp_dir / "blank.png"
    Image.new("RGBA", (int(width), int(height)), (0, 0, 0, 0)).save(blank_path, format="PNG")

    chunks = (
        normalize_and_chunk_words(
            words=words,
            clip_start_time=clip_start_time,
            clip_duration=dur_total,
            project_id=project_id,
            timing_offset=timing_offset,
            clean_filler_words=clean_filler_words,
            word_by_word=word_by_word,
        )
        if burn_captions
        else []
    )

    breakpoints = {0.0, round(dur_total, 3)}
    if burn_captions and hook_banner_enabled and hook_headline.strip():
        if 3.5 < dur_total:
            breakpoints.add(3.5)

    if retention_bar_enabled:
        steps = min(80, max(6, int(round(dur_total / 0.25))))
        for si in range(1, steps):
            breakpoints.add(round((si / float(steps)) * dur_total, 3))

    if outro_cta_enabled and dur_total > 2.2:
        breakpoints.add(round(dur_total - 2.0, 3))
        breakpoints.add(round(dur_total - 1.78, 3))
        breakpoints.add(round(dur_total - 1.60, 3))

    for ch in chunks:
        breakpoints.add(max(0.0, min(dur_total, round(ch["start"], 3))))
        breakpoints.add(max(0.0, min(dur_total, round(ch["end"], 3))))
        for w in ch["words"]:
            breakpoints.add(max(0.0, min(dur_total, round(w["start"], 3))))
            breakpoints.add(max(0.0, min(dur_total, round(w["end"], 3))))

    sorted_pts = sorted(breakpoints)
    # Deduplicate points closer than 20ms while keeping endpoints
    clean_pts: List[float] = [sorted_pts[0]]
    for pt in sorted_pts[1:-1]:
        if pt - clean_pts[-1] >= 0.02 and sorted_pts[-1] - pt >= 0.02:
            clean_pts.append(pt)
    if sorted_pts[-1] > clean_pts[-1]:
        clean_pts.append(sorted_pts[-1])
    elif len(clean_pts) == 1:
        clean_pts.append(round(dur_total, 3))

    state_to_filename: Dict[Tuple[int, int, bool, int, int], str] = {}
    ff_lines: List[str] = ["ffconcat version 1.0"]
    last_file = "blank.png"
    frame_counter = 0

    for idx in range(len(clean_pts) - 1):
        t0 = clean_pts[idx]
        t1 = clean_pts[idx + 1]
        interval_dur = t1 - t0
        if interval_dur <= 0.005:
            continue
        mid_t = (t0 + t1) / 2.0

        hook_vis = bool(burn_captions and hook_banner_enabled and hook_headline.strip() and mid_t <= 3.5)
        bar_bucket = int(round((mid_t / dur_total) * 100)) if retention_bar_enabled else -1
        cta_bucket = -1
        if outro_cta_enabled and dur_total > 2.2 and mid_t >= (dur_total - 2.0):
            cta_bucket = min(3, int((mid_t - (dur_total - 2.0)) / 0.12))

        ch_idx = -1
        word_idx = -1
        if burn_captions:
            for ci, ch in enumerate(chunks):
                if ch["start"] <= mid_t <= ch["end"]:
                    ch_idx = ci
                    for wi, w in enumerate(ch["words"]):
                        if w["start"] <= mid_t <= w["end"]:
                            word_idx = wi
                            break
                        if w["start"] <= mid_t:
                            word_idx = wi
                    break

        if ch_idx == -1 and not hook_vis and bar_bucket == -1 and cta_bucket == -1:
            fname = "blank.png"
        else:
            state_key = (ch_idx, word_idx, hook_vis, bar_bucket, cta_bucket)
            fname = state_to_filename.get(state_key, "")
            if not fname:
                frame_counter += 1
                fname = f"frame_{frame_counter:04d}.png"
                state_to_filename[state_key] = fname
                img = render_caption_frame(
                    t=mid_t,
                    width=width,
                    height=height,
                    words=words,
                    burn_captions=burn_captions,
                    hook_banner_enabled=hook_banner_enabled,
                    hook_headline=hook_headline,
                    retention_bar_enabled=retention_bar_enabled,
                    retention_bar_color=retention_bar_color,
                    outro_cta_enabled=outro_cta_enabled,
                    outro_cta_text=outro_cta_text,
                    clip_start_time=clip_start_time,
                    clip_duration=dur_total,
                    project_id=project_id,
                    timing_offset=timing_offset,
                    clean_filler_words=clean_filler_words,
                    word_by_word=word_by_word,
                    precomputed_chunks=chunks,
                    **style_kwargs,
                )
                img.save(temp_dir / fname, format="PNG")

        ff_lines.append(f"file '{fname}'")
        ff_lines.append(f"duration {interval_dur:.3f}")
        last_file = fname

    # FFmpeg ffconcat requires repeating the final file line so the last frame is never dropped
    ff_lines.append(f"file '{last_file}'")

    ffconcat_path = temp_dir / "captions.ffconcat"
    ffconcat_path.write_text("\n".join(ff_lines) + "\n", encoding="utf-8")
    return temp_dir, ffconcat_path, frame_counter
