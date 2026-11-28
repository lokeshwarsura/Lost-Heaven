import os
import re
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
from app.services.diarizer import detect_speaker_lower_third_turns
from app.services.callout_engine import draw_callout_card, draw_speaker_lower_third

logger = logging.getLogger(__name__)

FILLER_WORDS = {"um", "uh", "er", "ah", "like", "you know", "basically", "literally"}


def coerce_strict_bool(val: Any) -> bool:
    """Strictly parse booleans so strings like 'false' or '0' evaluate to False."""
    if isinstance(val, bool):
        return val
    if isinstance(val, (int, float)):
        return bool(val)
    if isinstance(val, str):
        norm = val.strip().lower()
        if norm in ("1", "true", "yes", "on"):
            return True
        if norm in ("", "0", "false", "no", "off", "disabled", "none", "null"):
            return False
        return False
    return False


def resolve_hook_banner_state(
    *,
    show_hook: Any = None,
    show_hook_banner: Any = None,
    hook_banner_enabled: Any = None,
    enable_hook_banner: Any = None,
    top_hook_banner: Any = None,
    hook_text: Any = None,
    hook_headline: Any = None,
    options: Optional[Dict[str, Any]] = None,
    default_show: bool = False,
) -> Tuple[bool, str]:
    """
    Resolve effective `(show_hook, hook_text)` strictly:
    returns `(True, clean_text)` ONLY when both boolean flag is strictly True
    AND headline text is non-empty after stripping; otherwise `(False, "")`.
    """
    opts = options if isinstance(options, dict) else {}
    flag_val: Any = None
    for candidate in (
        show_hook,
        top_hook_banner,
        enable_hook_banner,
        hook_banner_enabled,
        show_hook_banner,
    ):
        if candidate is not None:
            flag_val = candidate
            break
    if flag_val is None:
        for opt_key in (
            "show_hook",
            "top_hook_banner",
            "enable_hook_banner",
            "hook_banner_enabled",
            "show_hook_banner",
        ):
            if opt_key in opts and opts.get(opt_key) is not None:
                flag_val = opts.get(opt_key)
                break

    eff_show = coerce_strict_bool(flag_val) if flag_val is not None else bool(default_show)
    if eff_show is not True:
        return False, ""

    raw_text = hook_text if hook_text is not None else hook_headline
    if raw_text is None:
        raw_text = opts.get("hook_text", opts.get("hook_headline", ""))
    eff_text = str(raw_text or "").strip()
    if not eff_text:
        return False, ""
    return True, eff_text

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

    req_candidates: List[str] = []
    stem = raw.replace(" ", "")
    # Prioritize direct custom font filenames first so uploaded fonts are never shadowed
    req_candidates.extend([
        f"{raw}.ttf", f"{raw}.otf",
        f"{stem}.ttf", f"{stem}.otf",
        f"{stem}-Bold.ttf", f"{stem}-Regular.ttf",
    ])
    if norm_base in FONT_CANDIDATES:
        req_candidates.extend(FONT_CANDIDATES[norm_base][style_key])

    search_dirs = _font_search_dirs()
    # First pass: exact/fuzzy match for the requested font across storage/fonts & system dirs
    for d in search_dirs:
        if not d.exists():
            continue
        if str(d).lower().endswith("fonts") and d != Path(r"C:\Windows\Fonts"):
            try:
                norm_flat = norm_base.replace(" ", "")
                for p in sorted(d.iterdir()):
                    if p.suffix.lower() in (".ttf", ".otf"):
                        p_flat = p.stem.lower().replace(" ", "")
                        if p_flat == norm_flat or (norm_flat and norm_flat in p_flat):
                            try:
                                return ImageFont.truetype(str(p), size=size_px)
                            except Exception:
                                continue
            except Exception:
                pass
        for fname in req_candidates:
            candidate_path = d / fname
            if candidate_path.is_file():
                try:
                    return ImageFont.truetype(str(candidate_path), size=size_px)
                except Exception:
                    continue

    # Second pass: bundled Montserrat fallback
    for d in search_dirs:
        if not d.exists():
            continue
        for fname in FONT_CANDIDATES["montserrat"][style_key]:
            candidate_path = d / fname
            if candidate_path.is_file():
                try:
                    return ImageFont.truetype(str(candidate_path), size=size_px)
                except Exception:
                    continue

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
    pacing_density: str = "3-4 Words Viral",
) -> List[Dict[str, Any]]:
    """
    Normalize transcript words to clip-relative [0.0, clip_duration] timestamps,
    group into punchy chunks controlled by `pacing_density`, and bridge micro-silences (< 0.15s).
    Returns list of chunks: [{"start": float, "end": float, "words": [word_dict, ...]}].
    """
    dens = str(pacing_density or "3-4 Words Viral").strip().lower()
    if not word_by_word:
        if "1" in dens or "rapid" in dens:
            max_words_per_chunk = 2
            max_chars_per_line = 12
        elif "full" in dens or "phrase" in dens:
            max_words_per_chunk = 8
            max_chars_per_line = 34
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

        item_d: Dict[str, Any] = {"word": text, "start": round(rel_s, 3), "end": round(rel_e, 3)}
        if w.get("is_climax_teaser"):
            item_d["is_climax_teaser"] = True
        if w.get("speaker_id"):
            item_d["speaker_id"] = w["speaker_id"]
        if w.get("translated_text"):
            item_d["translated_text"] = w["translated_text"]
        if w.get("translated_phrase"):
            item_d["translated_phrase"] = w["translated_phrase"]
        if w.get("translated_chunk_id") is not None:
            item_d["translated_chunk_id"] = w["translated_chunk_id"]
        normalized.append(item_d)

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
            elif bool(curr[-1].get("is_climax_teaser")) != bool(w.get("is_climax_teaser")):
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


def _canonicalize_caption_animation(anim: Optional[str]) -> str:
    raw = str(anim or "Instant Cut").strip().lower().replace("-", " ").replace("_", " ")
    if raw in ("pop bounce", "pop", "soft bounce", "hard bounce", "elastic"):
        return "pop_bounce"
    if raw in ("slide up", "slide across"):
        return "slide_up"
    if raw in ("scale slam", "zoom", "pulse"):
        return "scale_slam"
    if raw in ("blur fade", "fade in", "blur scroll"):
        return "blur_fade"
    if raw in ("typewriter reveal", "typewriter"):
        return "typewriter_reveal"
    return "instant_cut"


def _apply_text_case(tok: str, text_case_mode: str) -> str:
    if text_case_mode == "all_caps":
        return tok.upper()
    if text_case_mode == "title_case":
        # Preserve apostrophes inside contractions (e.g. Don't -> Don't)
        return re.sub(r"[A-Za-z]+('[A-Za-z]+)?", lambda m: m.group(0)[0].upper() + m.group(0)[1:].lower(), tok)
    return tok


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
    pill_color: Optional[str] = None,
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
    caption_x: Optional[float] = None,
    caption_y: Optional[float] = None,
    caption_custom_position: bool = False,
    caption_rotation: float = 0.0,
    caption_max_width: float = 0.82,
    caption_animation: Optional[str] = None,
    text_case: Optional[str] = None,
) -> Dict[str, Any]:
    preset_def = CAPTION_STYLES_DEF.get(style_preset, CAPTION_STYLES_DEF["Karaoke pop"])
    norm_shape = (highlight_shape or "").strip().lower().replace(" ", "_").replace(".", "_")
    if not norm_shape:
        if preset_def.get("border_style") == 3 or style_preset in ("Punch Box", "Brand Block"):
            norm_shape = "filled_box"
        elif style_preset == "Underline Pop":
            norm_shape = "underline"
        elif style_preset == "Neon Glow":
            norm_shape = "neon_glow"
        else:
            norm_shape = "colour_only"
    elif norm_shape in ("pill_box", "filled_box", "rounded_pill_box"):
        norm_shape = "filled_box"
    elif norm_shape in ("colour_only", "color_only", "color_pop"):
        norm_shape = "colour_only"
    elif norm_shape in ("underline", "marker_underline"):
        norm_shape = "underline"
    elif norm_shape in ("neon_glow", "neon_outer_glow"):
        norm_shape = "neon_glow"
    elif norm_shape in ("kinetic_pop", "1_18x_kinetic_pop", "118x_kinetic_pop", "scale_118"):
        norm_shape = "kinetic_pop"

    eff_glow = float(glow or 0.0)
    if eff_glow == 0.0 and (style_preset == "Neon Glow" or norm_shape == "neon_glow"):
        eff_glow = 3.5

    raw_tc = str(text_case or "").strip().lower()
    if "title" in raw_tc:
        tc_mode = "title_case"
        eff_upper = False
    elif "as" in raw_tc or "spoke" in raw_tc or "orig" in raw_tc:
        tc_mode = "as_spoken"
        eff_upper = False
    elif "all" in raw_tc or "upper" in raw_tc:
        tc_mode = "all_caps"
        eff_upper = True
    else:
        eff_upper = bool(uppercase) if uppercase is not None else bool(preset_def.get("all_caps", True))
        tc_mode = "all_caps" if eff_upper else "as_spoken"

    eff_shadow = float(shadow if shadow is not None else preset_def.get("shadow_depth", 3.0))
    if eff_shadow > 10.5:
        eff_shadow = eff_shadow / 10.0

    # Grid fallback if pos_x/pos_y not passed explicitly
    grid_coords = {
        "top_left": (15.0, 18.0), "top_center": (50.0, 18.0), "top_right": (85.0, 18.0),
        "mid_left": (15.0, 50.0), "center": (50.0, 50.0), "mid_right": (85.0, 50.0),
        "bottom_left": (15.0, 74.0), "bottom_center": (50.0, 74.0), "bottom_right": (85.0, 74.0),
    }
    gx, gy = grid_coords.get(position_grid, (50.0, 74.0))

    resolved_hl = highlight_color or preset_def.get("active_color", "#C6F32F")
    resolved_pill = (pill_color or "").strip() or resolved_hl

    eff_pos_x = max(
        5.0,
        min(
            95.0,
            float(caption_x) * 100.0 if caption_x is not None else float(pos_x if pos_x is not None else gx),
        ),
    )
    eff_pos_y = max(
        8.0,
        min(
            92.0,
            float(caption_y) * 100.0 if caption_y is not None else float(pos_y if pos_y is not None else gy),
        ),
    )
    eff_rot = max(-15.0, min(15.0, float(caption_rotation or 0.0)))
    eff_mw = max(0.45, min(0.90, float(caption_max_width or 0.82)))

    return {
        "style_preset": style_preset,
        "font_family": font_family or preset_def.get("font", "Montserrat"),
        "base_font_size": int(preset_def.get("font_size", 64)),
        "font_size_pct": max(65, min(155, int(font_size_pct or 100))),
        "bold": bool(bold) if bold is not None else (style_preset not in ("Cinematic", "Documentary")),
        "italic": bool(italic) if italic is not None else (style_preset == "Cinematic"),
        "uppercase": eff_upper,
        "text_case": tc_mode,
        "base_color": base_color or preset_def.get("base_color", "#FFFFFF"),
        "highlight_color": resolved_hl,
        "pill_color": resolved_pill,
        "outline_color": outline_color or preset_def.get("outline_color", "#000000"),
        "highlight_shape": norm_shape,
        "hold_back_opacity": max(0.05, min(1.0, float(hold_back_opacity if hold_back_opacity is not None else 1.0))),
        "outline": max(0.0, min(12.0, float(outline if outline is not None else preset_def.get("outline_width", 4.0)))),
        "shadow": max(0.0, min(10.0, eff_shadow)),
        "glow": eff_glow,
        "letter_spacing": float(letter_spacing or 0.0),
        "alignment": (alignment or "Centre").strip().lower(),
        "pos_x": eff_pos_x,
        "pos_y": eff_pos_y,
        "caption_x": eff_pos_x / 100.0,
        "caption_y": eff_pos_y / 100.0,
        "caption_custom_position": bool(caption_custom_position),
        "caption_rotation": eff_rot,
        "caption_max_width": eff_mw,
        "caption_animation": _canonicalize_caption_animation(caption_animation),
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


def _canonicalize_safe_zone(safe_zone: Any = None) -> str:
    norm = str(safe_zone or "").strip().lower().replace("-", " ").replace("_", " ")
    norm = " ".join(norm.split())
    if norm in ("tiktok", "tik tok", "tt"):
        return "tiktok"
    if norm in ("reels", "reel", "instagram", "instagram reels", "ig", "ig reels"):
        return "reels"
    if norm in ("shorts", "short", "yt shorts", "youtube shorts", "youtube"):
        return "shorts"
    if norm == "all":
        return "all"
    return "none"


def _draw_cold_open_climax_badge(
    canvas: Image.Image,
    width: int,
    height: int,
    has_hook_banner: bool = False,
    safe_zone: str = "none",
):
    """Draw a high-contrast pill badge (`CLIMAX HOOK`) during the prepended Cold-Open teaser."""
    scale = max(0.45, width / 1080.0)
    font = resolve_pillow_font("Montserrat", int(round(24 * scale)), bold=True, italic=False)
    draw = ImageDraw.Draw(canvas)
    label = "CLIMAX HOOK"
    tw, th = _measure_text(draw, label, font)
    pad_x = int(round(22 * scale))
    pad_y = int(round(10 * scale))
    flame_w = int(round(22 * scale))
    gap = int(round(10 * scale))
    pill_w = int(round(tw)) + pad_x * 2 + flame_w + gap
    pill_h = int(round(th)) + pad_y * 2
    x0 = (width - pill_w) // 2
    y0 = max(14, int(round(height * (0.025 if has_hook_banner else 0.065))))
    if _canonicalize_safe_zone(safe_zone) in ("tiktok", "reels", "shorts", "all"):
        safe_top_y = int(math.ceil(height * 0.13)) + max(2, int(round(4 * scale)))
        y0 = max(safe_top_y, y0)
    x1 = x0 + pill_w
    y1 = y0 + pill_h

    draw.rounded_rectangle(
        [x0, y0, x1, y1],
        radius=pill_h // 2,
        fill=(220, 38, 38, 235),
        outline=(251, 191, 36, 255),
        width=max(2, int(round(2 * scale))),
    )
    # Crisp vector flame icon on the left side of the pill (avoids missing emoji glyph squares)
    fx = x0 + pad_x + flame_w * 0.45
    fy_bot = y1 - pad_y * 0.85
    fy_top = y0 + pad_y * 0.7
    fy_mid = 0.5 * (fy_bot + fy_top)
    r = max(4, int(round(flame_w * 0.42)))
    draw.polygon(
        [
            (fx, fy_top),
            (fx + r * 0.9, fy_mid + r * 0.1),
            (fx + r * 0.6, fy_bot),
            (fx - r * 0.6, fy_bot),
            (fx - r * 0.9, fy_mid + r * 0.1),
        ],
        fill=(254, 240, 138, 255),
    )
    draw.text(
        (x0 + pad_x + flame_w + gap, y0 + pad_y),
        label,
        font=font,
        fill=(255, 255, 255, 255),
        stroke_width=max(1, int(round(1.5 * scale))),
        stroke_fill=(0, 0, 0, 200),
    )


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
    cta_headline: str = "",
    cta_handle: str = "",
    logo_path: Optional[str] = None,
) -> None:
    """Render an animated 2-second Branded End-Card Outro overlay in the final 2.0s of the clip."""
    if clip_duration <= 2.2 or t < (clip_duration - 2.0):
        return
    elapsed_in_card = max(0.0, t - (clip_duration - 2.0))
    fade = min(1.0, elapsed_in_card / 0.35)
    alpha_bg = int(round(228 * fade))
    alpha_fg = int(round(255 * fade))
    if alpha_fg < 10:
        return

    scale = max(0.45, width / 1080.0)
    r, g, b, _ = _parse_rgba(accent_hex or "#C6F32F", 1.0)
    draw = ImageDraw.Draw(canvas)

    logo_img: Optional[Image.Image] = None
    logo_sz = int(round(78 * scale))
    if logo_path:
        try:
            from app.services.audio_mastering import resolve_local_media_asset
            resolved_l = resolve_local_media_asset(logo_path)
            if resolved_l and os.path.exists(resolved_l):
                with Image.open(resolved_l) as raw_l:
                    l_rgba = raw_l.convert("RGBA")
                    l_rgba.thumbnail((logo_sz, logo_sz), Image.Resampling.LANCZOS)
                    if fade < 0.99:
                        la = l_rgba.getchannel("A").point(lambda v: int(round(v * fade)))
                        l_rgba.putalpha(la)
                    logo_img = l_rgba
        except Exception:
            logo_img = None

    headline_clean = str(cta_headline or "").strip()
    handle_clean = str(cta_handle or "").strip()
    btn_text = (cta_text or "Subscribe for More").strip() or "Subscribe for More"
    btn_label = f"{btn_text}  →"

    has_rich_brand = bool(headline_clean or handle_clean or logo_img is not None)
    if not has_rich_brand:
        font_px = max(20, int(round(36 * scale)))
        font = resolve_pillow_font("Montserrat", font_px, bold=True)
        tw, th = _measure_text(draw, btn_label, font)
        pad_x = int(round(28 * scale))
        pad_y = int(round(14 * scale))
        box_w = min(width - 40, int(round(tw)) + pad_x * 2)
        box_h = int(round(th)) + pad_y * 2
        x0 = (width - box_w) // 2
        y0 = max(40, int(round(height * 0.82)) - box_h // 2)
        draw.rounded_rectangle(
            [x0, y0, x0 + box_w, y0 + box_h],
            radius=max(12, box_h // 2),
            fill=(14, 15, 20, alpha_bg),
            outline=(r, g, b, alpha_fg),
            width=max(2, int(round(3 * scale))),
        )
        draw.text(
            ((width - tw) / 2.0, y0 + pad_y - int(round(2 * scale))),
            btn_label,
            font=font,
            fill=(r, g, b, alpha_fg),
        )
        return

    # Full glassmorphic Branded End-Card stack (Logo + Headline + @Handle + Button Badge)
    f_head = resolve_pillow_font("Montserrat", max(22, int(round(38 * scale))), bold=True)
    f_handle = resolve_pillow_font("Montserrat", max(18, int(round(26 * scale))), bold=False)
    f_btn = resolve_pillow_font("Montserrat", max(18, int(round(30 * scale))), bold=True)

    card_w = min(width - int(round(80 * scale)), int(round(width * 0.78)))
    pad_card = int(round(26 * scale))
    inner_h = 0
    gap = int(round(12 * scale))
    if logo_img is not None:
        inner_h += logo_img.height + gap
    if headline_clean:
        _, hh = _measure_text(draw, headline_clean.upper(), f_head)
        inner_h += int(round(hh)) + gap
    if handle_clean:
        _, hnh = _measure_text(draw, handle_clean, f_handle)
        inner_h += int(round(hnh)) + gap
    btw, bth = _measure_text(draw, btn_label, f_btn)
    btn_h = int(round(bth)) + int(round(18 * scale))
    inner_h += btn_h
    card_h = inner_h + pad_card * 2

    cx0 = (width - card_w) // 2
    cy0 = max(36, min(height - card_h - 36, int(round(height * 0.70)) - card_h // 2))
    draw.rounded_rectangle(
        [cx0, cy0, cx0 + card_w, cy0 + card_h],
        radius=max(16, int(round(24 * scale))),
        fill=(14, 15, 22, alpha_bg),
        outline=(r, g, b, alpha_fg),
        width=max(2, int(round(3 * scale))),
    )

    cur_y = cy0 + pad_card
    if logo_img is not None:
        lx = (width - logo_img.width) // 2
        canvas.paste(logo_img, (lx, int(cur_y)), logo_img)
        cur_y += logo_img.height + gap

    if headline_clean:
        ht = headline_clean.upper()
        hw, hh = _measure_text(draw, ht, f_head)
        draw.text(((width - hw) / 2.0, cur_y), ht, font=f_head, fill=(255, 255, 255, alpha_fg))
        cur_y += int(round(hh)) + gap

    if handle_clean:
        hn_w, hn_h = _measure_text(draw, handle_clean, f_handle)
        draw.text(((width - hn_w) / 2.0, cur_y), handle_clean, font=f_handle, fill=(203, 213, 225, alpha_fg))
        cur_y += int(round(hn_h)) + gap

    bw = min(card_w - pad_card, int(round(btw)) + int(round(44 * scale)))
    bx0 = (width - bw) // 2
    draw.rounded_rectangle(
        [bx0, cur_y, bx0 + bw, cur_y + btn_h],
        radius=btn_h // 2,
        fill=(r, g, b, alpha_fg),
    )
    lum = 0.299 * r + 0.587 * g + 0.114 * b
    ink = (12, 12, 18, alpha_fg) if lum > 140 else (255, 255, 255, alpha_fg)
    draw.text(((width - btw) / 2.0, cur_y + int(round(8 * scale))), btn_label, font=f_btn, fill=ink)


def _resolve_bilingual_font(px_size: int, text: str = "") -> Any:
    """Resolve a Unicode-capable secondary caption font so accents, Devanagari, Telugu, CJK & Arabic render crisply."""
    needs_unicode = any(ord(ch) > 255 for ch in text)
    candidates = [
        r"C:\Windows\Fonts\Nirmala.ttf",
        r"C:\Windows\Fonts\segoeui.ttf",
        r"C:\Windows\Fonts\malgun.ttf",
        r"C:\Windows\Fonts\arial.ttf",
        "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
    ] if needs_unicode else [
        r"C:\Windows\Fonts\segoeui.ttf",
        r"C:\Windows\Fonts\arial.ttf",
    ]
    for cp in candidates:
        if os.path.exists(cp):
            try:
                return ImageFont.truetype(cp, px_size)
            except Exception:
                continue
    return resolve_pillow_font("Montserrat", px_size, bold=False)


def _compute_face_safe_anchor_y(
    t: float,
    default_anchor_y: float,
    total_h: float,
    margin_pad: float,
    height: int,
    face_keyframes: Optional[List[Dict[str, Any]]],
) -> float:
    """Shift caption vertical center cleanly out of the active face bbox when overlapping."""
    if not face_keyframes:
        return default_anchor_y
    closest = min(face_keyframes, key=lambda kf: abs(float(kf.get("t", 0.0)) - t))
    if "face_count" in closest and int(closest.get("face_count", 0) or 0) <= 0:
        return default_anchor_y
    fcy = float(closest.get("cy", 0.4))
    fh_norm = float(closest.get("face_h", 0.26) or 0.26)
    cy_px = fcy * height
    fh_px = max(height * 0.14, min(height * 0.45, fh_norm * height))
    face_top = max(margin_pad, cy_px - 0.58 * fh_px)
    face_bot = min(height - margin_pad, cy_px + 0.65 * fh_px)

    pad = max(14.0, height * 0.025)
    cap_top = default_anchor_y - total_h / 2.0
    cap_bot = default_anchor_y + total_h / 2.0
    overlaps = not (cap_bot < face_top - pad or cap_top > face_bot + pad)
    if not overlaps:
        return default_anchor_y

    below_anchor = face_bot + pad + total_h / 2.0
    above_anchor = face_top - pad - total_h / 2.0
    min_anchor = margin_pad + total_h / 2.0
    max_anchor = height - margin_pad - total_h / 2.0

    below_ok = below_anchor <= max_anchor
    above_ok = above_anchor >= min_anchor

    if below_ok and fcy < 0.60:
        return below_anchor
    if above_ok:
        return above_anchor
    if below_ok:
        return below_anchor
    return max(min_anchor, min(max_anchor, below_anchor if fcy < 0.5 else above_anchor))


def _compute_handle_pulse_visibility(t: float, display_mode: str) -> float:
    """Return [0.0..1.0] alpha/entrance multiplier for creator handle watermark."""
    mode = str(display_mode or "always").strip().lower()
    if mode not in {"pulse", "pulse_12s", "periodic_pulse", "periodic"}:
        return 1.0
    phase = float(t) % 12.0
    if phase < 0.35:
        return max(0.05, phase / 0.35)
    if phase <= 3.2:
        return 1.0
    if phase <= 3.6:
        return max(0.0, 1.0 - (phase - 3.2) / 0.4)
    return 0.0


def _draw_creator_handle_watermark(
    canvas: Image.Image,
    t: float,
    width: int,
    height: int,
    *,
    handle_text: str,
    platform_icon: str = "none",
    corner: str = "bottom_right",
    opacity: float = 0.65,
    display_mode: str = "always",
    font_resolver: Any = None,
) -> Image.Image:
    txt = str(handle_text or "").strip()
    if not txt:
        return canvas
    vis = _compute_handle_pulse_visibility(t, display_mode)
    if vis <= 0.02:
        return canvas

    eff_op = max(0.20, min(0.90, float(opacity or 0.65))) * vis
    base = canvas.convert("RGBA") if canvas.mode != "RGBA" else canvas
    overlay = Image.new("RGBA", (width, height), (0, 0, 0, 0))
    draw = ImageDraw.Draw(overlay, "RGBA")

    px = max(13, int(width * 0.026))
    font = font_resolver(px) if font_resolver else None
    if font and hasattr(draw, "textbbox"):
        tb = draw.textbbox((0, 0), txt, font=font)
        tw = tb[2] - tb[0]
        th = tb[3] - tb[1]
    else:
        tw = int(len(txt) * px * 0.58)
        th = px

    icon_name = str(platform_icon or "none").strip().lower()
    has_icon = icon_name in {"youtube", "tiktok", "instagram", "x"}
    icon_sz = max(12, int(px * 1.05))
    icon_gap = int(px * 0.42) if has_icon else 0
    pad_x = max(10, int(width * 0.018))
    pad_y = max(6, int(height * 0.007))
    pill_w = min(int(width * 0.72), pad_x * 2 + (icon_sz if has_icon else 0) + icon_gap + tw)
    pill_h = max(th + pad_y * 2, icon_sz + pad_y * 2)

    mx = int(width * 0.045)
    c_key = str(corner or "bottom_right").strip().lower()
    if c_key == "top_left":
        x0, y0 = mx, int(height * 0.045)
    elif c_key == "top_right":
        x0, y0 = width - mx - pill_w, int(height * 0.045)
    elif c_key == "bottom_left":
        x0, y0 = mx, int(height * 0.895) - pill_h
    else:  # bottom_right
        x0, y0 = width - mx - pill_w, int(height * 0.895) - pill_h

    # Subtle slide nudge during periodic pulse fade-in
    if vis < 0.99:
        slide_shift = int((1.0 - vis) * max(10, width * 0.02))
        x0 += (-slide_shift if "left" in c_key else slide_shift)

    bg_a = int(round(170 * eff_op))
    bd_a = int(round(90 * eff_op))
    fg_a = int(round(248 * eff_op))

    draw.rounded_rectangle(
        [x0, y0, x0 + pill_w, y0 + pill_h],
        radius=max(8, pill_h // 2),
        fill=(12, 16, 26, bg_a),
        outline=(255, 255, 255, bd_a),
        width=max(1, int(width * 0.002)),
    )

    cur_x = x0 + pad_x
    cy = y0 + pill_h // 2
    if has_icon:
        ix0, iy0 = cur_x, cy - icon_sz // 2
        ix1, iy1 = ix0 + icon_sz, iy0 + icon_sz
        if icon_name == "youtube":
            draw.rounded_rectangle([ix0, iy0 + 1, ix1, iy1 - 1], radius=max(2, icon_sz // 5), fill=(239, 68, 68, fg_a))
            draw.polygon(
                [(ix0 + icon_sz * 0.38, iy0 + icon_sz * 0.28), (ix0 + icon_sz * 0.38, iy0 + icon_sz * 0.72), (ix0 + icon_sz * 0.72, cy)],
                fill=(255, 255, 255, fg_a),
            )
        elif icon_name == "tiktok":
            draw.ellipse([ix0 + 1, iy0 + icon_sz * 0.45, ix0 + icon_sz * 0.62, iy1], fill=(34, 211, 238, fg_a))
            draw.line([(ix0 + icon_sz * 0.56, iy0 + icon_sz * 0.75), (ix0 + icon_sz * 0.56, iy0 + 1), (ix1 - 1, iy0 + icon_sz * 0.25)], fill=(255, 255, 255, fg_a), width=max(2, icon_sz // 6))
        elif icon_name == "instagram":
            draw.rounded_rectangle([ix0 + 1, iy0 + 1, ix1 - 1, iy1 - 1], radius=max(3, icon_sz // 4), outline=(244, 114, 182, fg_a), width=max(2, icon_sz // 7))
            r_in = max(2, icon_sz // 5)
            draw.ellipse([ix0 + icon_sz // 2 - r_in, cy - r_in, ix0 + icon_sz // 2 + r_in, cy + r_in], outline=(255, 255, 255, fg_a), width=max(1, icon_sz // 8))
        else:  # x
            w_line = max(2, icon_sz // 6)
            draw.line([(ix0 + 2, iy0 + 2), (ix1 - 2, iy1 - 2)], fill=(255, 255, 255, fg_a), width=w_line)
            draw.line([(ix1 - 2, iy0 + 2), (ix0 + 2, iy1 - 2)], fill=(255, 255, 255, fg_a), width=w_line)
        cur_x = ix1 + icon_gap

    draw.text((cur_x, cy), txt, font=font, fill=(248, 250, 252, fg_a), anchor="lm")
    return Image.alpha_composite(base, overlay)


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
    caption_x: Optional[float] = None,
    caption_y: Optional[float] = None,
    caption_custom_position: bool = False,
    caption_rotation: float = 0.0,
    caption_max_width: float = 0.82,
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
    show_hook: Optional[bool] = None,
    show_hook_banner: Optional[bool] = None,
    hook_text: Optional[str] = None,
    retention_bar_enabled: bool = False,
    retention_bar_color: str = "#C6F32F",
    outro_cta_enabled: bool = False,
    outro_cta_text: str = "Subscribe for More",
    outro_cta_headline: str = "",
    outro_cta_handle: str = "",
    outro_logo_path: Optional[str] = None,
    clip_start_time: float = 0.0,
    clip_duration: float = 0.0,
    project_id: Optional[int] = None,
    timing_offset: float = 0.0,
    clean_filler_words: bool = False,
    highlight_power_words: bool = False,
    precomputed_chunks: Optional[List[Dict[str, Any]]] = None,
    speakers: Optional[Dict[str, Any]] = None,
    speaker_lower_thirds: bool = False,
    face_safe_captions: bool = False,
    face_keyframes: Optional[List[Dict[str, Any]]] = None,
    callout_cards: Optional[List[Dict[str, Any]]] = None,
    auto_callouts: bool = False,
    precomputed_turns: Optional[List[Dict[str, Any]]] = None,
    bilingual_enabled: bool = False,
    secondary_language: str = "",
    spectrum_enabled: bool = False,
    spectrum_position: str = "bottom",
    spectrum_color: str = "#C6F32F",
    spectrum_data: Optional[Dict[str, Any]] = None,
    chapters_enabled: bool = False,
    chapters: Optional[List[Dict[str, Any]]] = None,
    handle_watermark_enabled: bool = False,
    handle_text: str = "",
    handle_platform_icon: str = "none",
    handle_corner: str = "bottom_right",
    handle_opacity: float = 0.65,
    handle_display_mode: str = "always",
    cold_open_active: bool = False,
    cold_open_teaser_dur: float = 2.3,
    safe_zone: str = "none",
    caption_animation: Optional[str] = None,
    pill_color: Optional[str] = None,
    pacing_density: str = "3-4 Words Viral",
    text_case: Optional[str] = None,
) -> Image.Image:
    """
    Render a full-resolution transparent RGBA caption + retention/CTA overlay frame at clip-relative time `t`.
    Returns a Pillow RGBA Image pixel-identical between preview_frame.py and renderer.py.
    """
    canvas = Image.new("RGBA", (int(width), int(height)), (0, 0, 0, 0))

    if retention_bar_enabled:
        dur_safe = max(0.5, float(clip_duration or 1.0))
        _draw_retention_bar(canvas, t / dur_safe, width, height, color_hex=retention_bar_color)

    if spectrum_enabled and spectrum_data:
        from app.services.beat_sync import draw_spectrum_visualizer, get_spectrum_bars_at_time
        bars = get_spectrum_bars_at_time(spectrum_data, t)
        if bars:
            canvas = draw_spectrum_visualizer(
                canvas,
                bars=bars,
                position=spectrum_position,
                color_hex=spectrum_color,
                width=width,
                height=height,
            )

    if outro_cta_enabled and clip_duration > 2.2 and t >= (clip_duration - 2.0):
        _draw_outro_cta_card(
            canvas, t, clip_duration, width, height,
            cta_text=outro_cta_text,
            accent_hex=highlight_color or retention_bar_color or "#C6F32F",
            cta_headline=outro_cta_headline,
            cta_handle=outro_cta_handle,
            logo_path=outro_logo_path,
        )

    eff_show_hook, eff_hook_text = resolve_hook_banner_state(
        show_hook=show_hook,
        show_hook_banner=show_hook_banner,
        hook_banner_enabled=hook_banner_enabled,
        hook_text=hook_text,
        hook_headline=hook_headline,
    )
    hook_visible_now = bool(eff_show_hook is True and bool(eff_hook_text) is True and 0.0 <= t <= 3.5)
    cold_open_visible_now = bool(cold_open_active and 0.0 <= t <= max(0.5, float(cold_open_teaser_dur or 2.3)))
    if cold_open_visible_now:
        _draw_cold_open_climax_badge(canvas, width, height, has_hook_banner=hook_visible_now, safe_zone=safe_zone)

    if chapters_enabled and chapters:
        from app.services.chapter_engine import draw_chapter_pill
        canvas = draw_chapter_pill(
            canvas,
            chapters=chapters,
            t=t,
            width=width,
            height=height,
            accent_color=highlight_color or "#8B5CF6",
            font_resolver=lambda px: resolve_pillow_font(font_family or "Montserrat", px, bold=True),
            has_hook_banner=hook_visible_now,
        )

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
            pacing_density=pacing_density,
        )
    )

    if auto_callouts and callout_cards:
        for card in callout_cards:
            if isinstance(card, dict):
                draw_callout_card(
                    canvas,
                    card,
                    t=t,
                    width=width,
                    height=height,
                    font_resolver=lambda fam, px, bold=True: resolve_pillow_font(fam, px, bold=bold),
                    has_hook_banner=hook_visible_now,
                )

    if speaker_lower_thirds:
        turns = precomputed_turns
        if turns is None:
            flat_rel_words = [w for ch in chunks for w in (ch.get("words") or [])]
            turns = detect_speaker_lower_third_turns(flat_rel_words, speakers)
        for trn in turns:
            draw_speaker_lower_third(
                canvas,
                trn,
                t=t,
                width=width,
                height=height,
                font_resolver=lambda fam, px, bold=True: resolve_pillow_font(fam, px, bold=bold),
                has_hook_banner=hook_visible_now,
            )

    if handle_watermark_enabled and str(handle_text or "").strip():
        canvas = _draw_creator_handle_watermark(
            canvas,
            t=t,
            width=width,
            height=height,
            handle_text=handle_text,
            platform_icon=handle_platform_icon,
            corner=handle_corner,
            opacity=handle_opacity,
            display_mode=handle_display_mode,
            font_resolver=lambda px: resolve_pillow_font(font_family or "Montserrat", px, bold=True),
        )

    if not burn_captions:
        if hook_visible_now:
            _draw_hook_banner(canvas, eff_hook_text, width, height)
        return canvas

    if hook_visible_now:
        _draw_hook_banner(canvas, eff_hook_text, width, height)

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
        pill_color=pill_color,
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
        caption_x=caption_x,
        caption_y=caption_y,
        caption_custom_position=caption_custom_position,
        caption_rotation=caption_rotation,
        caption_max_width=caption_max_width,
        caption_animation=caption_animation,
        text_case=text_case,
    )

    scale = max(0.45, width / 1080.0)
    dummy_draw = ImageDraw.Draw(canvas)

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

    def _get_emoji_font(epx: int):
        if epx not in emoji_font_cache:
            loaded_efont = None
            for cand_epath in (
                r"C:\Windows\Fonts\seguiemj.ttf",
                "/usr/share/fonts/truetype/noto/NotoColorEmoji.ttf",
                "/System/Library/Fonts/Apple Color Emoji.ttc",
            ):
                if os.path.exists(cand_epath):
                    try:
                        loaded_efont = ImageFont.truetype(cand_epath, epx)
                        break
                    except Exception:
                        continue
            emoji_font_cache[epx] = loaded_efont
        return emoji_font_cache[epx]

    stroke_px = max(0, int(round(cfg["outline"] * scale)))
    spacing_px = cfg["letter_spacing"] * scale
    box_pad_px = (
        int(round(36 * scale))
        if (cfg["is_full_line_block"] or cfg["highlight_shape"] == "filled_box")
        else int(round(8 * scale))
    )
    edge_margin = max(24.0, float(int(round(24.0 * max(1.0, scale))))) if (width >= 320 and height >= 320) else max(8.0, width * 0.05)
    max_safe_width = min(max(120, int(round(width - edge_margin * 2.0))), max(120, int(round(width * cfg["caption_max_width"]))))
    inner_max_w = max(80.0, float(max_safe_width - box_pad_px - stroke_px * 2))
    side_margin = edge_margin

    req_font_px = max(18, int(round(cfg["base_font_size"] * (cfg["font_size_pct"] / 100.0) * scale)))

    # Measure & shrink proportionally until every wrapped line + pill box fits cleanly inside 0.82 * width
    font_px = req_font_px
    font = resolve_pillow_font(cfg["font_family"], font_px, bold=cfg["bold"], italic=cfg["italic"])
    power_font = font
    emoji_font = None
    emoji_px = max(14, int(round(req_font_px * 0.88)))
    space_w = 10.0
    display_tokens: List[str] = []
    token_widths: List[float] = []
    token_heights: List[float] = []
    token_emojis: List[Optional[str]] = []
    token_emoji_offsets: List[float] = []
    token_is_power: List[bool] = []
    token_fonts: List[Any] = []
    lines_idx: List[List[int]] = []
    line_widths: List[float] = []
    line_heights: List[float] = []
    max_block_w = 0.0

    for shrink_step in range(12):
        cand_px = max(14, int(round(req_font_px * (0.90 ** shrink_step))))
        font_px = cand_px
        font = resolve_pillow_font(cfg["font_family"], font_px, bold=cfg["bold"], italic=cfg["italic"])
        space_w, _ = _measure_text(dummy_draw, " ", font, letter_spacing=0.0)
        if space_w < 4.0:
            space_w = font_px * 0.28

        base_font_px = max(12, int(getattr(font, "size", font_px)))
        font_path = getattr(font, "path", None)
        power_font = font
        if highlight_power_words and font_path:
            try:
                power_font = ImageFont.truetype(font_path, max(12, int(round(base_font_px * 1.08))))
            except Exception:
                power_font = font

        kinetic_pop_font = font
        if cfg["highlight_shape"] == "kinetic_pop":
            kinetic_pop_font = resolve_pillow_font(
                cfg["font_family"],
                max(14, int(round(base_font_px * 1.18))),
                bold=cfg["bold"],
                italic=cfg["italic"],
            )

        emoji_px = max(12, int(round(base_font_px * 0.88)))
        emoji_font = _get_emoji_font(emoji_px)

        display_tokens = []
        token_widths = []
        token_heights = []
        token_emojis = []
        token_emoji_offsets = []
        token_is_power = []
        token_fonts = []

        for tok_i, w in enumerate(cwords):
            raw_tok = str(w.get("word", "")).strip()
            clean_key = raw_tok.lower().strip(".,!?:;\"'()[]")
            tok = _apply_text_case(raw_tok, cfg["text_case"])
            is_power = bool(highlight_power_words and clean_key in power_words)
            tok_font = power_font if is_power else font

            matched_emoji: Optional[str] = None
            emoji_prefix_w = 0.0
            if auto_emoji:
                matched_emoji = EMOJI_KEYWORDS.get(clean_key)
                if matched_emoji:
                    emoji_gap = max(6.0, base_font_px * 0.16)
                    if emoji_font is not None:
                        try:
                            ebb = dummy_draw.textbbox((0, 0), matched_emoji, font=emoji_font, embedded_color=True)
                            emoji_prefix_w = max(float(emoji_px), float(ebb[2] - ebb[0])) + emoji_gap
                        except Exception:
                            emoji_prefix_w = float(emoji_px) + emoji_gap
                    else:
                        emoji_prefix_w = float(emoji_px) + emoji_gap

            tw, _ = _measure_text(dummy_draw, tok, tok_font, letter_spacing=spacing_px)
            display_tokens.append(tok)
            token_widths.append(tw + emoji_prefix_w)
            token_emojis.append(matched_emoji)
            token_emoji_offsets.append(emoji_prefix_w)
            token_is_power.append(is_power)
            token_fonts.append(tok_font)

        try:
            asc, desc = font.getmetrics()
            metric_h = float(abs(asc) + abs(desc))
        except Exception:
            metric_h = float(font_px) * 1.25
        fixed_line_h = max(float(font_px) * 1.25, metric_h)

        lines_idx = []
        curr_line: List[int] = []
        curr_w = 0.0
        for idx in range(len(display_tokens)):
            add_w = token_widths[idx] + (space_w if curr_line else 0.0)
            if curr_line and curr_w + add_w > inner_max_w:
                lines_idx.append(curr_line)
                curr_line = [idx]
                curr_w = token_widths[idx]
            else:
                curr_line.append(idx)
                curr_w += add_w
        if curr_line:
            lines_idx.append(curr_line)

        line_heights = [fixed_line_h for _ in lines_idx]
        line_widths = [
            sum(token_widths[i] for i in ln) + max(0, len(ln) - 1) * space_w
            for ln in lines_idx
        ]
        max_block_w = max(line_widths) if line_widths else 0.0
        max_single_tok = max(token_widths) if token_widths else 0.0

        # Fits horizontally within 0.82 * width (accounting for box padding) and reasonable vertical line count
        if max_single_tok <= inner_max_w and max_block_w <= inner_max_w and (len(lines_idx) <= 3 or cand_px <= 20):
            break

    base_font_px = max(12, int(getattr(font, "size", font_px)))
    line_gap = max(6.0, float(font_px) * 0.14)
    total_h = sum(line_heights) + max(0, len(lines_idx) - 1) * line_gap
    try:
        ref_bb = dummy_draw.textbbox((0, 0), "HÉpg", font=font)
        ref_vis_mid = 0.5 * float(ref_bb[1] + ref_bb[3])
    except Exception:
        ref_vis_mid = float(font_px) * 0.52

    sec_line_txt = ""
    sec_font = None
    sec_w = 0.0
    sec_h = 0.0
    sec_gap = max(6.0, 10.0 * scale)
    if bilingual_enabled:
        for cw in cwords:
            ph = str(cw.get("translated_phrase") or "").strip()
            if ph:
                sec_line_txt = ph
                break
        if not sec_line_txt:
            toks_sec = [str(cw.get("translated_text") or "").strip() for cw in cwords if str(cw.get("translated_text") or "").strip()]
            sec_line_txt = " ".join(toks_sec).strip()
        if sec_line_txt:
            sec_px = max(16, int(round(font_px * 0.68)))
            sec_font = _resolve_bilingual_font(sec_px, sec_line_txt)
            sbb = dummy_draw.textbbox((0, 0), sec_line_txt, font=sec_font)
            sec_w = float(sbb[2] - sbb[0])
            sec_h = float(sbb[3] - sbb[1])
            total_h += sec_gap + sec_h + 10.0 * scale

    anchor_x = int(round(cfg["caption_x"] * width))
    anchor_y = int(round(cfg["caption_y"] * height))

    # Clamp cleanly inside edge margins (>= 24px from left/right/top/bottom on real frames)
    margin_pad = side_margin
    is_custom_pos = bool(cfg.get("caption_custom_position", False))
    top_safe_pad = (
        edge_margin
        if is_custom_pos
        else (max(edge_margin, height * 0.16) if hook_visible_now else edge_margin)
    )
    bot_safe_pad = edge_margin
    if face_safe_captions and not is_custom_pos and face_keyframes:
        anchor_y = _compute_face_safe_anchor_y(
            t=t,
            default_anchor_y=float(anchor_y),
            total_h=total_h,
            margin_pad=top_safe_pad,
            height=height,
            face_keyframes=face_keyframes,
        )
    start_y = max(top_safe_pad, min(height - total_h - bot_safe_pad, float(anchor_y) - total_h / 2.0))
    block_mid_y = start_y + total_h / 2.0

    shadow_px = max(0, int(round(cfg["shadow"] * scale)))

    inactive_rgba = _parse_rgba(cfg["base_color"], alpha_float=cfg["hold_back_opacity"])
    active_hex = cfg["highlight_color"]
    if speakers and 0 <= active_word_idx < len(cwords):
        w_sid = str(cwords[active_word_idx].get("speaker_id") or "").strip()
        if w_sid and w_sid in speakers and speakers[w_sid].get("color"):
            active_hex = str(speakers[w_sid]["color"])
    active_rgba = _parse_rgba(active_hex, alpha_float=1.0)
    pill_rgba = _parse_rgba(cfg["pill_color"], alpha_float=1.0)
    outline_rgba = _parse_rgba(cfg["outline_color"], alpha_float=1.0)
    shadow_rgba = (0, 0, 0, 185)
    anim_mode = cfg["caption_animation"]
    max_visible_tok = active_word_idx if anim_mode == "typewriter_reveal" else len(cwords) - 1

    # Compute exact token coordinates clamped inside [side_margin, width - side_margin]
    token_positions: List[Tuple[int, float, float, float, float]] = []
    cy = start_y
    for ln_i, ln in enumerate(lines_idx):
        lw = line_widths[ln_i]
        lh = line_heights[ln_i]
        if cfg["alignment"] == "left":
            lx = max(side_margin, min(width - lw - side_margin, anchor_x - max_block_w / 2.0))
        elif cfg["alignment"] == "right":
            lx = max(side_margin, min(width - lw - side_margin, anchor_x + max_block_w / 2.0 - lw))
        else:
            lx = max(side_margin, min(width - lw - side_margin, anchor_x - lw / 2.0))

        cx = lx
        for tok_i in ln:
            token_positions.append((tok_i, cx, cy, token_widths[tok_i], lh))
            cx += token_widths[tok_i] + space_w
        cy += lh + line_gap

    # Build isolated caption block layer so kinetic entry physics transform cleanly inside safe bounds
    caption_layer = Image.new("RGBA", (int(width), int(height)), (0, 0, 0, 0))

    # 1. Backdrop pill boxes / Brand Block / Underlines / Neon Halo layer
    bg_layer = Image.new("RGBA", (int(width), int(height)), (0, 0, 0, 0))
    bg_draw = ImageDraw.Draw(bg_layer)

    if cfg["is_full_line_block"]:
        pad_x = max(14, int(round(18 * scale)))
        pad_y = max(8, int(round(10 * scale)))
        for ln_i, ln in enumerate(lines_idx):
            matching = [tp for tp in token_positions if tp[0] in ln and tp[0] <= max_visible_tok]
            if not matching:
                continue
            x0 = max(4.0, min(tp[1] for tp in matching) - pad_x)
            y0 = min(tp[2] for tp in matching) - pad_y
            x1 = min(width - 4.0, max(tp[1] + tp[3] for tp in matching) + pad_x)
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
            if tok_i == active_word_idx and tok_i <= max_visible_tok:
                pill_fill = (pill_rgba[0], pill_rgba[1], pill_rgba[2], 242)
                x0 = max(4.0, tx - pad_x)
                x1 = min(width - 4.0, tx + tw + pad_x)
                bg_draw.rounded_rectangle(
                    [x0, ty - pad_y, x1, ty + th + pad_y],
                    radius=max(8, int(round(12 * scale))),
                    fill=pill_fill,
                )
    elif cfg["highlight_shape"] == "underline":
        bar_h = max(4, int(round(6 * scale)))
        for tok_i, tx, ty, tw, th in token_positions:
            if tok_i == active_word_idx and tok_i <= max_visible_tok:
                bg_draw.rounded_rectangle(
                    [tx, ty + th + max(3, int(round(4 * scale))), tx + tw, ty + th + max(3, int(round(4 * scale))) + bar_h],
                    radius=bar_h // 2,
                    fill=active_rgba,
                )

    caption_layer = Image.alpha_composite(caption_layer, bg_layer)

    # 2. Glow layer (Gaussian blurred duplicate — enhanced for Neon Outer Glow)
    def _token_draw_xy(tok_idx: int, tx: float, ty: float, tok_h: float, is_active: bool, tok_font: Any):
        tok_str = display_tokens[tok_idx]
        emoji_off = token_emoji_offsets[tok_idx] if tok_idx < len(token_emoji_offsets) else 0.0
        base_draw_y = ty + (tok_h * 0.5) - ref_vis_mid
        if cfg["highlight_shape"] == "kinetic_pop" and is_active:
            pop_font = kinetic_pop_font
            base_text_w = max(1.0, token_widths[tok_idx] - emoji_off)
            pop_text_w, _ = _measure_text(dummy_draw, tok_str, pop_font, letter_spacing=spacing_px * 1.18)
            slot_cx = tx + emoji_off + base_text_w * 0.5
            slot_cy = ty + tok_h * 0.5
            try:
                pop_bb = dummy_draw.textbbox((0, 0), "HÉpg", font=pop_font)
                pop_vis_mid = 0.5 * float(pop_bb[1] + pop_bb[3])
            except Exception:
                pop_vis_mid = float(getattr(pop_font, "size", font_px)) * 0.52
            return (
                slot_cx - pop_text_w * 0.5,
                slot_cy - pop_vis_mid,
                pop_font,
                spacing_px * 1.18,
            )
        is_pow = token_is_power[tok_idx] if tok_idx < len(token_is_power) else False
        pow_off = -max(1.0, base_font_px * 0.04) if is_pow and tok_font is not font else 0.0
        return tx + emoji_off, base_draw_y + pow_off, tok_font, spacing_px

    if cfg["glow"] > 0.0 or cfg["highlight_shape"] == "neon_glow":
        glow_layer = Image.new("RGBA", (int(width), int(height)), (0, 0, 0, 0))
        glow_draw = ImageDraw.Draw(glow_layer)
        glow_str = max(cfg["glow"], 3.5 if cfg["highlight_shape"] == "neon_glow" else 0.0)
        for tok_i, tx, ty, _, tok_h in token_positions:
            if tok_i > max_visible_tok:
                continue
            tok = display_tokens[tok_i]
            is_active = tok_i == active_word_idx
            tok_font_base = token_fonts[tok_i] if tok_i < len(token_fonts) else font
            if cfg["highlight_shape"] == "neon_glow" and not is_active:
                continue
            gx, gy, g_font, g_sp = _token_draw_xy(tok_i, tx, ty, tok_h, is_active, tok_font_base)
            glow_col = active_rgba if is_active else _parse_rgba(cfg["base_color"], alpha_float=0.45)
            _draw_spaced_text(
                glow_draw,
                (gx, gy),
                tok,
                g_font,
                fill=glow_col,
                letter_spacing=g_sp,
                stroke_width=max(2, stroke_px // 2 + 2),
                stroke_fill=glow_col,
            )
        glow_layer = glow_layer.filter(ImageFilter.GaussianBlur(radius=max(2.0, glow_str * 2.4 * scale)))
        caption_layer = Image.alpha_composite(caption_layer, glow_layer)

    # 3. Shadow + Crisp Foreground Text Layer
    fg_layer = Image.new("RGBA", (int(width), int(height)), (0, 0, 0, 0))
    fg_draw = ImageDraw.Draw(fg_layer)

    if shadow_px > 0 and not cfg["is_full_line_block"]:
        for tok_i, tx, ty, _, tok_h in token_positions:
            if tok_i > max_visible_tok:
                continue
            is_active = tok_i == active_word_idx
            tok_font_base = token_fonts[tok_i] if tok_i < len(token_fonts) else font
            sx_t, sy_t, s_font, s_sp = _token_draw_xy(tok_i, tx, ty, tok_h, is_active, tok_font_base)
            _draw_spaced_text(
                fg_draw,
                (sx_t + shadow_px, sy_t + shadow_px),
                display_tokens[tok_i],
                s_font,
                fill=shadow_rgba,
                letter_spacing=s_sp,
                stroke_width=stroke_px,
                stroke_fill=shadow_rgba,
            )

    for tok_i, tx, ty, _, tok_h in token_positions:
        if tok_i > max_visible_tok:
            continue
        is_active = tok_i == active_word_idx
        is_power = token_is_power[tok_i] if tok_i < len(token_is_power) else False
        tok_font_base = token_fonts[tok_i] if tok_i < len(token_fonts) else font
        emoji_char = token_emojis[tok_i] if tok_i < len(token_emojis) else None
        emoji_x_off = token_emoji_offsets[tok_i] if tok_i < len(token_emoji_offsets) else 0.0

        if cfg["highlight_shape"] == "filled_box" and is_active:
            # High-contrast dark or white ink paired cleanly against custom pill_color
            r, g, b, _ = pill_rgba
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
            ey = ty + max(0.0, (tok_h - emoji_px) * 0.5)
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

        dx_t, dy_t, d_font, d_sp = _token_draw_xy(tok_i, tx, ty, tok_h, is_active, tok_font_base)
        _draw_spaced_text(
            fg_draw,
            (dx_t, dy_t),
            display_tokens[tok_i],
            d_font,
            fill=text_fill,
            letter_spacing=d_sp,
            stroke_width=tok_stroke,
            stroke_fill=outline_rgba if tok_stroke > 0 else None,
        )

    if bilingual_enabled and sec_line_txt and sec_font is not None:
        sec_y = cy + sec_gap * 0.45
        sx = max(margin_pad, min(width - sec_w - margin_pad, anchor_x - sec_w / 2.0))
        pad_bx = max(10.0, 12.0 * scale)
        pad_by = max(4.0, 6.0 * scale)
        fg_draw.rounded_rectangle(
            [sx - pad_bx, sec_y - pad_by, sx + sec_w + pad_bx, sec_y + sec_h + pad_by],
            radius=max(6, int(round(10 * scale))),
            fill=(12, 13, 18, 210),
            outline=(active_rgba[0], active_rgba[1], active_rgba[2], 140),
            width=max(1, int(round(1.5 * scale))),
        )
        fg_draw.text((sx, sec_y), sec_line_txt, font=sec_font, fill=(248, 250, 252, 250))

    caption_layer = Image.alpha_composite(caption_layer, fg_layer)

    # Apply kinetic entry physics (scale, Y-offset, blur, alpha) while keeping within horizontal safe bounds
    dt_entry = max(0.0, float(t) - float(active_chunk["start"]))
    k_scale = 1.0
    k_dy = 0
    k_alpha = 1.0
    k_blur = 0.0
    if anim_mode == "pop_bounce" and dt_entry < 0.22:
        u = dt_entry / 0.22
        k_scale = 0.80 + 0.30 * math.sin(u * math.pi * 0.84)
        k_alpha = min(1.0, 0.35 + u * 0.65)
    elif anim_mode == "slide_up" and dt_entry < 0.22:
        u = dt_entry / 0.22
        ease = 1.0 - (1.0 - u) ** 3
        k_dy = int(round(32.0 * scale * (1.0 - ease)))
        k_alpha = max(0.20, ease)
    elif anim_mode == "scale_slam" and dt_entry < 0.18:
        u = dt_entry / 0.18
        k_scale = 1.32 - 0.32 * (u * u)
        k_alpha = min(1.0, 0.35 + u * 0.65)
    elif anim_mode == "blur_fade" and dt_entry < 0.22:
        u = dt_entry / 0.22
        k_blur = max(0.0, (1.0 - u) * 5.0 * scale)
        k_alpha = max(0.18, u)

    rot_deg = float(cfg.get("caption_rotation", 0.0) or 0.0)
    bb = caption_layer.getbbox()
    if bb is not None and (abs(k_scale - 1.0) > 0.01 or k_dy != 0 or k_alpha < 0.99 or k_blur > 0.2 or abs(rot_deg) > 0.05):
        # Transform & rotate around caption center, clamping strictly inside >= edge_margin on all 4 sides
        bw = max(1, bb[2] - bb[0])
        max_k = min(k_scale, max(1.0, (max_safe_width / float(bw))))
        crop_patch = caption_layer.crop(bb)
        if abs(max_k - 1.0) > 0.01:
            nw = max(2, int(round(crop_patch.width * max_k)))
            nh = max(2, int(round(crop_patch.height * max_k)))
            crop_patch = crop_patch.resize((nw, nh), Image.Resampling.BICUBIC)
        if k_blur > 0.2:
            crop_patch = crop_patch.filter(ImageFilter.GaussianBlur(radius=k_blur))
        if abs(rot_deg) > 0.05:
            crop_patch = crop_patch.rotate(-rot_deg, resample=Image.Resampling.BICUBIC, expand=True)
            # Shrink proportionally only if rotated bounding box exceeds available safe canvas span
            avail_w = max(32, int(width - edge_margin * 2.0))
            avail_h = max(32, int(height - top_safe_pad - bot_safe_pad))
            if crop_patch.width > avail_w or crop_patch.height > avail_h:
                fit_ratio = min(avail_w / max(1, crop_patch.width), avail_h / max(1, crop_patch.height))
                crop_patch = crop_patch.resize(
                    (max(2, int(round(crop_patch.width * fit_ratio))), max(2, int(round(crop_patch.height * fit_ratio)))),
                    Image.Resampling.BICUBIC,
                )
        if k_alpha < 0.99:
            a_ch = crop_patch.getchannel("A").point(lambda v: int(round(v * k_alpha)))
            crop_patch.putalpha(a_ch)
        ox = 0.5 * (bb[0] + bb[2]) - float(anchor_x)
        oy = 0.5 * (bb[1] + bb[3]) - float(block_mid_y)
        rad = math.radians(rot_deg)
        cos_r = math.cos(rad)
        sin_r = math.sin(rad)
        cx_mid = float(anchor_x) + (ox * max_k) * cos_r - (oy * max_k) * sin_r
        cy_mid = float(block_mid_y) + (ox * max_k) * sin_r + (oy * max_k) * cos_r + k_dy
        min_x = int(edge_margin)
        max_x = max(min_x, int(width - edge_margin - crop_patch.width))
        min_y = int(top_safe_pad)
        max_y = max(min_y, int(height - bot_safe_pad - crop_patch.height))
        px = int(max(min_x, min(max_x, round(cx_mid - crop_patch.width / 2.0))))
        py = int(max(min_y, min(max_y, round(cy_mid - crop_patch.height / 2.0))))
        anim_layer = Image.new("RGBA", (int(width), int(height)), (0, 0, 0, 0))
        anim_layer.paste(crop_patch, (px, py), crop_patch)
        caption_layer = anim_layer
    elif bb is not None:
        # Enforce strict >= edge_margin safety clamp even on unrotated/static pills & shadows
        min_x = int(edge_margin)
        max_x = max(min_x, int(width - edge_margin - (bb[2] - bb[0])))
        min_y = int(top_safe_pad)
        max_y = max(min_y, int(height - bot_safe_pad - (bb[3] - bb[1])))
        shift_x = max(min_x - bb[0], min(max_x - bb[0], 0))
        shift_y = max(min_y - bb[1], min(max_y - bb[1], 0))
        if shift_x != 0 or shift_y != 0:
            crop_patch = caption_layer.crop(bb)
            shifted_layer = Image.new("RGBA", (int(width), int(height)), (0, 0, 0, 0))
            shifted_layer.paste(crop_patch, (bb[0] + shift_x, bb[1] + shift_y), crop_patch)
            caption_layer = shifted_layer

    canvas = Image.alpha_composite(canvas, caption_layer)
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
    show_hook: Optional[bool] = None,
    show_hook_banner: Optional[bool] = None,
    hook_text: Optional[str] = None,
    clip_start_time: float = 0.0,
    project_id: Optional[int] = None,
    timing_offset: float = 0.0,
    clean_filler_words: bool = False,
    word_by_word: bool = False,
    retention_bar_enabled: bool = False,
    retention_bar_color: str = "#C6F32F",
    outro_cta_enabled: bool = False,
    outro_cta_text: str = "Subscribe for More",
    outro_cta_headline: str = "",
    outro_cta_handle: str = "",
    outro_logo_path: Optional[str] = None,
    speakers: Optional[Dict[str, Any]] = None,
    speaker_lower_thirds: bool = False,
    face_safe_captions: bool = False,
    face_keyframes: Optional[List[Dict[str, Any]]] = None,
    caption_custom_position: bool = False,
    callout_cards: Optional[List[Dict[str, Any]]] = None,
    auto_callouts: bool = False,
    bilingual_enabled: bool = False,
    secondary_language: str = "",
    spectrum_enabled: bool = False,
    spectrum_position: str = "bottom",
    spectrum_color: str = "#C6F32F",
    spectrum_data: Optional[Dict[str, Any]] = None,
    chapters_enabled: bool = False,
    chapters: Optional[List[Dict[str, Any]]] = None,
    handle_watermark_enabled: bool = False,
    handle_text: str = "",
    handle_platform_icon: str = "none",
    handle_corner: str = "bottom_right",
    handle_opacity: float = 0.65,
    handle_display_mode: str = "always",
    cold_open_active: bool = False,
    cold_open_teaser_dur: float = 2.3,
    safe_zone: str = "none",
    caption_animation: Optional[str] = None,
    pill_color: Optional[str] = None,
    pacing_density: str = "3-4 Words Viral",
    text_case: Optional[str] = None,
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
            pacing_density=pacing_density,
        )
        if (burn_captions or speaker_lower_thirds)
        else []
    )
    anim_mode = _canonicalize_caption_animation(caption_animation)

    turns = (
        detect_speaker_lower_third_turns([w for ch in chunks for w in (ch.get("words") or [])], speakers)
        if speaker_lower_thirds
        else []
    )
    active_cards = [c for c in (callout_cards or []) if isinstance(c, dict) and c.get("enabled", True)] if auto_callouts else []

    eff_show_hook, eff_hook_text = resolve_hook_banner_state(
        show_hook=show_hook,
        show_hook_banner=show_hook_banner,
        hook_banner_enabled=hook_banner_enabled,
        hook_text=hook_text,
        hook_headline=hook_headline,
    )

    breakpoints = {0.0, round(dur_total, 3)}
    co_teaser_end = max(0.5, float(cold_open_teaser_dur or 2.3))
    if cold_open_active and co_teaser_end < dur_total:
        breakpoints.add(round(co_teaser_end, 3))
    if eff_show_hook is True and bool(eff_hook_text) is True:
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

    eff_face_safe = bool(face_safe_captions and not caption_custom_position)
    if eff_face_safe and face_keyframes:
        fk_step = 0.5
        cur_ft = fk_step
        while cur_ft < dur_total:
            breakpoints.add(round(cur_ft, 3))
            cur_ft += fk_step

    for trn in turns:
        breakpoints.add(max(0.0, min(dur_total, round(float(trn["start"]), 3))))
        breakpoints.add(max(0.0, min(dur_total, round(float(trn["end"]), 3))))

    for card in active_cards:
        cs = float(card.get("start", 0.0))
        ce = float(card.get("end", cs + 2.0))
        for off in (0.0, 0.07, 0.14):
            breakpoints.add(max(0.0, min(dur_total, round(cs + off, 3))))
        breakpoints.add(max(0.0, min(dur_total, round(ce, 3))))

    active_chapters = chapters if (chapters_enabled and chapters) else []
    for ch_item in active_chapters:
        cs = float(ch_item.get("start", 0.0))
        ce = float(ch_item.get("end", dur_total))
        for off in (0.0, 0.08, 0.16, 0.25):
            breakpoints.add(max(0.0, min(dur_total, round(cs + off, 3))))
        breakpoints.add(max(0.0, min(dur_total, round(ce, 3))))

    spec_frames = (spectrum_data or {}).get("spectrum_frames") or [] if spectrum_enabled else []
    spec_fps = int((spectrum_data or {}).get("spectrum_fps") or 10)
    if spectrum_enabled and spec_frames:
        step_s = 1.0 / float(max(2, min(10, spec_fps)))
        cur_st = 0.0
        while cur_st < dur_total:
            breakpoints.add(round(cur_st, 3))
            cur_st += step_s

    handle_active = bool(handle_watermark_enabled and str(handle_text or "").strip())
    if handle_active and str(handle_display_mode or "always").strip().lower() in {"pulse", "pulse_12s", "periodic_pulse", "periodic"}:
        cyc = 0.0
        while cyc < dur_total:
            for off in (0.0, 0.14, 0.28, 0.40, 3.20, 3.40, 3.62):
                pt = round(cyc + off, 3)
                if 0.0 <= pt <= dur_total:
                    breakpoints.add(pt)
            cyc += 12.0

    if burn_captions:
        for ch in chunks:
            breakpoints.add(max(0.0, min(dur_total, round(ch["start"], 3))))
            if anim_mode != "instant_cut" and anim_mode != "typewriter_reveal":
                for k_off in (0.04, 0.09, 0.14, 0.22):
                    pt = round(ch["start"] + k_off, 3)
                    if pt < ch["end"] and pt < dur_total:
                        breakpoints.add(pt)
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

    state_to_filename: Dict[Tuple[Any, ...], str] = {}
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

        hook_vis = bool(eff_show_hook is True and bool(eff_hook_text) is True and mid_t <= 3.5)
        cold_open_vis = bool(cold_open_active and mid_t <= co_teaser_end)
        bar_bucket = int(round((mid_t / dur_total) * 100)) if retention_bar_enabled else -1
        cta_bucket = -1
        if outro_cta_enabled and dur_total > 2.2 and mid_t >= (dur_total - 2.0):
            cta_bucket = min(3, int((mid_t - (dur_total - 2.0)) / 0.12))

        turn_idx = -1
        for ti, trn in enumerate(turns):
            if trn["start"] <= mid_t <= trn["end"]:
                turn_idx = ti
                break

        callout_state: Tuple[Tuple[int, int], ...] = tuple(
            (ci, min(3, int(max(0.0, mid_t - float(c.get("start", 0.0))) / 0.06)))
            for ci, c in enumerate(active_cards)
            if float(c.get("start", 0.0)) <= mid_t <= float(c.get("end", 0.0))
        )

        face_bucket = -1
        if burn_captions and eff_face_safe and face_keyframes:
            closest_kf = min(face_keyframes, key=lambda kf: abs(float(kf.get("t", 0.0)) - mid_t))
            if not ("face_count" in closest_kf and int(closest_kf.get("face_count", 0) or 0) <= 0):
                face_bucket = int(round(float(closest_kf.get("cy", 0.4)) * 20))

        chap_idx = -1
        chap_ease = 0
        for chi, ch_item in enumerate(active_chapters):
            cs = float(ch_item.get("start", 0.0))
            ce = float(ch_item.get("end", dur_total))
            if cs <= mid_t < ce or (chi == len(active_chapters) - 1 and mid_t >= cs):
                chap_idx = chi
                chap_ease = min(3, int(max(0.0, mid_t - cs) / 0.07))
                break

        spec_bucket = -1
        if spectrum_enabled and spec_frames:
            spec_bucket = min(len(spec_frames) - 1, max(0, int(round(mid_t * spec_fps))))

        handle_bucket = -1
        if handle_active:
            vis_h = _compute_handle_pulse_visibility(mid_t, handle_display_mode)
            handle_bucket = int(round(vis_h * 10)) if vis_h > 0.02 else -1

        ch_idx = -1
        word_idx = -1
        anim_bucket = 4
        if burn_captions:
            for ci, ch in enumerate(chunks):
                if ch["start"] <= mid_t <= ch["end"]:
                    ch_idx = ci
                    if anim_mode != "instant_cut" and anim_mode != "typewriter_reveal":
                        dt_a = mid_t - ch["start"]
                        anim_bucket = 4 if dt_a >= 0.22 else min(3, int(dt_a / 0.055))
                    for wi, w in enumerate(ch["words"]):
                        if w["start"] <= mid_t <= w["end"]:
                            word_idx = wi
                            break
                        if w["start"] <= mid_t:
                            word_idx = wi
                    break

        if (
            ch_idx == -1
            and not hook_vis
            and not cold_open_vis
            and bar_bucket == -1
            and cta_bucket == -1
            and turn_idx == -1
            and not callout_state
            and chap_idx == -1
            and spec_bucket == -1
            and handle_bucket == -1
        ):
            fname = "blank.png"
        else:
            state_key = (
                ch_idx, word_idx, anim_bucket, hook_vis, cold_open_vis, _canonicalize_safe_zone(safe_zone),
                bar_bucket, cta_bucket, turn_idx, callout_state, face_bucket, chap_idx, chap_ease,
                spec_bucket, handle_bucket,
            )
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
                    hook_banner_enabled=eff_show_hook,
                    hook_headline=eff_hook_text,
                    show_hook=eff_show_hook,
                    hook_text=eff_hook_text,
                    retention_bar_enabled=retention_bar_enabled,
                    retention_bar_color=retention_bar_color,
                    outro_cta_enabled=outro_cta_enabled,
                    outro_cta_text=outro_cta_text,
                    outro_cta_headline=outro_cta_headline,
                    outro_cta_handle=outro_cta_handle,
                    outro_logo_path=outro_logo_path,
                    clip_start_time=clip_start_time,
                    clip_duration=dur_total,
                    project_id=project_id,
                    timing_offset=timing_offset,
                    clean_filler_words=clean_filler_words,
                    word_by_word=word_by_word,
                    precomputed_chunks=chunks if burn_captions else [],
                    speakers=speakers,
                    speaker_lower_thirds=speaker_lower_thirds,
                    face_safe_captions=eff_face_safe,
                    face_keyframes=face_keyframes,
                    caption_custom_position=caption_custom_position,
                    callout_cards=callout_cards,
                    auto_callouts=auto_callouts,
                    precomputed_turns=turns,
                    bilingual_enabled=bilingual_enabled,
                    secondary_language=secondary_language,
                    spectrum_enabled=spectrum_enabled,
                    spectrum_position=spectrum_position,
                    spectrum_color=spectrum_color,
                    spectrum_data=spectrum_data,
                    chapters_enabled=chapters_enabled,
                    chapters=chapters,
                    handle_watermark_enabled=handle_watermark_enabled,
                    handle_text=handle_text,
                    handle_platform_icon=handle_platform_icon,
                    handle_corner=handle_corner,
                    handle_opacity=handle_opacity,
                    handle_display_mode=handle_display_mode,
                    cold_open_active=cold_open_active,
                    cold_open_teaser_dur=co_teaser_end,
                    safe_zone=safe_zone,
                    caption_animation=caption_animation,
                    pill_color=pill_color,
                    pacing_density=pacing_density,
                    text_case=text_case,
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
