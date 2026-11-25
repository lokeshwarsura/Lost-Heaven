import os
import json
import shutil
import logging
from pathlib import Path
from typing import List, Dict, Any, Optional, Tuple

from app.config import settings

logger = logging.getLogger(__name__)

# Fonts available by default
FONT_MAP = {
    "Montserrat": "Montserrat",
    "Montserrat ExtraBold": "Montserrat",
    "Anton": "Anton",
    "Bebas Neue": "Bebas Neue",
    "Impact": "Impact",
    "Poppins ExtraBold": "Poppins",
    "Poppins": "Poppins",
    "Roboto Black": "Roboto",
    "Roboto": "Roboto",
    "Playfair Display": "Playfair Display",
    "Playfair Display Bold": "Playfair Display",
    "Courier Prime": "Courier Prime",
}

# 12 Visual Caption Style Definitions
CAPTION_STYLES_DEF = {
    "Cinematic": {
        "font": "Playfair Display",
        "font_size": 58,
        "base_color": "#FFFFFF",
        "active_color": "#F59E0B",
        "outline_color": "#000000",
        "outline_width": 2.5,
        "shadow_depth": 2.0,
        "border_style": 1,
        "all_caps": False,
    },
    "Karaoke pop": {
        "font": "Montserrat",
        "font_size": 66,
        "base_color": "#FFFFFF",
        "active_color": "#C6F32F",
        "outline_color": "#000000",
        "outline_width": 4.5,
        "shadow_depth": 3.0,
        "border_style": 1,
        "all_caps": True,
    },
    "Bold impact": {
        "font": "Impact",
        "font_size": 72,
        "base_color": "#FFFFFF",
        "active_color": "#EF4444",
        "outline_color": "#000000",
        "outline_width": 5.0,
        "shadow_depth": 4.0,
        "border_style": 1,
        "all_caps": True,
    },
    "Punch Box": {
        "font": "Montserrat",
        "font_size": 62,
        "base_color": "#FFFFFF",
        "active_color": "#FACC15",
        "outline_color": "#18181B",
        "outline_width": 4.0,
        "shadow_depth": 2.0,
        "border_style": 3,
        "all_caps": True,
    },
    "Clean Yellow": {
        "font": "Poppins",
        "font_size": 60,
        "base_color": "#FFFFFF",
        "active_color": "#FACC15",
        "outline_color": "#000000",
        "outline_width": 3.5,
        "shadow_depth": 2.5,
        "border_style": 1,
        "all_caps": False,
    },
    "Neon Glow": {
        "font": "Montserrat",
        "font_size": 64,
        "base_color": "#E0F2FE",
        "active_color": "#06B6D4",
        "outline_color": "#083344",
        "outline_width": 4.0,
        "shadow_depth": 4.0,
        "border_style": 1,
        "all_caps": True,
    },
    "Underline Pop": {
        "font": "Montserrat",
        "font_size": 64,
        "base_color": "#FFFFFF",
        "active_color": "#EC4899",
        "outline_color": "#000000",
        "outline_width": 3.5,
        "shadow_depth": 2.5,
        "border_style": 1,
        "all_caps": True,
    },
    "Documentary": {
        "font": "Roboto",
        "font_size": 52,
        "base_color": "#F4F4F5",
        "active_color": "#E4E4E7",
        "outline_color": "#000000",
        "outline_width": 2.0,
        "shadow_depth": 1.5,
        "border_style": 1,
        "all_caps": False,
    },
    "Hormozi": {
        "font": "Montserrat",
        "font_size": 74,
        "base_color": "#FFFFFF",
        "active_color": "#FFFF00",
        "outline_color": "#000000",
        "outline_width": 6.0,
        "shadow_depth": 5.0,
        "border_style": 1,
        "all_caps": True,
    },
    "Creator White": {
        "font": "Poppins",
        "font_size": 62,
        "base_color": "#FFFFFF",
        "active_color": "#FFFFFF",
        "outline_color": "#18181B",
        "outline_width": 3.5,
        "shadow_depth": 2.5,
        "border_style": 1,
        "all_caps": False,
    },
    "Anton Impact": {
        "font": "Anton",
        "font_size": 72,
        "base_color": "#FFFFFF",
        "active_color": "#F59E0B",
        "outline_color": "#000000",
        "outline_width": 4.5,
        "shadow_depth": 3.5,
        "border_style": 1,
        "all_caps": True,
    },
    "Brand Block": {
        "font": "Montserrat",
        "font_size": 60,
        "base_color": "#000000",
        "active_color": "#000000",
        "outline_color": "#F59E0B",
        "outline_width": 6.0,
        "shadow_depth": 0.0,
        "border_style": 3,
        "all_caps": True,
    },
}

EMOJI_KEYWORDS = {
    # Money / Business / Finance
    "money": "💰", "cash": "💵", "rich": "🤑", "wealth": "💎", "dollar": "💵", "dollars": "💵",
    "bank": "🏦", "profit": "📈", "revenue": "💰", "income": "💵", "million": "💰", "billion": "💎",
    "price": "🏷️", "cost": "💳", "expensive": "💸", "cheap": "🪙", "invest": "📈", "stock": "📊",
    "crypto": "🪙", "bitcoin": "🪙", "business": "💼", "company": "🏢", "startup": "🚀", "brand": "✨",
    # Reactions / Shock / Laughter
    "crazy": "🤯", "insane": "😱", "unbelievable": "😱", "shocking": "😲", "shocked": "😲",
    "wtf": "🤯", "omg": "😱", "no": "🚫", "never": "🛑", "always": "💯", "literally": "🤯",
    "laugh": "😂", "funny": "😂", "hilarious": "🤣", "lol": "😂", "joke": "😜", "smile": "😁",
    "cry": "😭", "sad": "😢", "angry": "😡", "mad": "🤬", "scary": "😨", "fear": "😨",
    "weird": "🤨", "secret": "🤫", "truth": "💯", "lie": "🤥", "fake": "🎭", "real": "✅",
    # Gaming / Action / Victory / Fail
    "game": "🎮", "gaming": "🎮", "play": "🕹️", "player": "🕹️", "stream": "📺", "live": "🔴",
    "win": "🏆", "won": "🏆", "winner": "🥇", "champion": "🏆", "clutch": "🔥", "gg": "👑",
    "boss": "👹", "kill": "💀", "dead": "💀", "died": "💀", "rip": "💀", "shoot": "💥",
    "boom": "💥", "explosion": "💥", "hit": "🥊", "fight": "⚔️", "war": "⚔️", "attack": "⚔️",
    "fail": "❌", "failed": "❌", "lose": "📉", "lost": "❌", "mistake": "⚠️", "error": "⚠️",
    "wrong": "🚫", "bug": "🐛", "glitch": "👾", "hack": "🧠", "pro": "🏆", "noob": "😅",
    "crab": "🦀", "giant": "🦕", "monster": "👾", "dragon": "🐉",
    # Energy / Speed / Fire / Warning
    "fire": "🔥", "hot": "🔥", "burn": "🔥", "lit": "🔥", "goat": "🐐", "legend": "👑",
    "king": "👑", "queen": "👑", "fast": "⚡", "quick": "⚡", "speed": "⚡", "instant": "⚡",
    "power": "⚡", "energy": "⚡", "strong": "💪", "gym": "🏋️", "muscle": "💪", "hard": "💎",
    "warning": "⚠️", "danger": "🚨", "alert": "🚨", "stop": "🛑", "wait": "⏳", "careful": "⚠️",
    "toxic": "☣️", "broken": "💔",
    # Tech / AI / Mind / Ideas
    "ai": "🤖", "robot": "🤖", "tech": "💻", "code": "💻", "coding": "💻", "software": "⚙️",
    "app": "📱", "phone": "📱", "video": "🎬", "camera": "🎥", "edit": "✂️", "audio": "🎙️",
    "voice": "🎙️", "music": "🎵", "mind": "🧠", "brain": "🧠", "blown": "🤯", "smart": "🧠",
    "think": "💡", "idea": "💡", "learn": "📚", "study": "📖", "Focus": "🎯", "focus": "🎯",
    # Time / Future / Stars / Love / Growth
    "future": "🚀", "rocket": "🚀", "launch": "🚀", "moon": "🌙", "sun": "☀️", "space": "🪐",
    "time": "⏰", "clock": "⏰", "today": "📅", "now": "⚡", "seconds": "⏱️", "hour": "⏳",
    "love": "❤️", "heart": "💖", "like": "👍", "subscribe": "🔔", "comment": "💬", "share": "🔁",
    "happy": "😃", "star": "⭐", "best": "🌟", "top": "🔝", "growth": "📈", "viral": "📈",
    "views": "👀", "watch": "👀", "look": "👀", "see": "👁️", "target": "🎯", "goal": "🎯",
    "hundred": "💯", "perfect": "✨", "magic": "✨", "world": "🌍", "earth": "🌍", "home": "🏠",
    "car": "🏎️", "drive": "🚗", "fly": "✈️", "travel": "🌍", "food": "🍕", "coffee": "☕",
}


def hex_to_ass_color(hex_str: str, alpha: str = "00") -> str:
    """Convert CSS hex color (#RRGGBB) to ASS color format (&H<alpha><blue><green><red>&)."""
    clean = (hex_str or "#FFFFFF").strip().lstrip("#")
    if len(clean) == 3:
        clean = "".join([c * 2 for c in clean])
    if len(clean) != 6:
        return f"&H{alpha}FFFFFF&"
    r = clean[0:2]
    g = clean[2:4]
    b = clean[4:6]
    return f"&H{alpha}{b}{g}{r}&"


def float_alpha_to_hex(alpha: float) -> str:
    """Convert 1.0 (opaque) -> '00', 0.0 (transparent) -> 'FF'."""
    clamped = max(0.0, min(1.0, float(alpha)))
    ass_alpha_int = int((1.0 - clamped) * 255)
    return f"{ass_alpha_int:02X}"


def format_ass_time(seconds: float) -> str:
    """Format seconds into ASS timestamp string: H:MM:SS.cs"""
    seconds = max(0.0, float(seconds))
    hrs = int(seconds // 3600)
    mins = int((seconds % 3600) // 60)
    secs = int(seconds % 60)
    centis = int(round((seconds - int(seconds)) * 100))
    if centis >= 100:
        secs += 1
        centis = 0
    return f"{hrs}:{mins:02d}:{secs:02d}.{centis:02d}"


def get_3x3_position_coords(
    grid: str,
    pos_x_pct: float,
    pos_y_pct: float,
    video_width: int,
    video_height: int,
    text_align: Optional[str] = None,
) -> Tuple[int, int, int]:
    """Compute ASS alignment (1-9) and pixel X, Y for placement & alignment."""
    align_map = {
        "top_left": 7, "top_center": 8, "top_right": 9,
        "mid_left": 4, "center": 5, "mid_right": 6,
        "bottom_left": 1, "bottom_center": 2, "bottom_right": 3,
    }
    an = align_map.get(grid, 2)

    if text_align:
        norm_align = text_align.lower()
        row_base = 7 if pos_y_pct < 33 else (4 if pos_y_pct < 66 else 1)
        if norm_align == "left":
            an = row_base
        elif norm_align == "right":
            an = row_base + 2
        elif norm_align in ("centre", "center") and grid not in (
            "top_left", "mid_left", "bottom_left",
            "top_right", "mid_right", "bottom_right",
        ):
            an = row_base + 1

    px = int((float(pos_x_pct) / 100.0) * video_width)
    py = int((float(pos_y_pct) / 100.0) * video_height)
    return an, px, py


ANIMATION_TAGS: Dict[str, str] = {
    "pop": "\\fscx118\\fscy118",
    "pop_scale": "\\fscx118\\fscy118",
    "soft_bounce": "\\fscx126\\fscy126\\t(0,120,\\fscx104\\fscy104)",
    "bounce_spring": "\\fscx126\\fscy126\\t(0,120,\\fscx104\\fscy104)",
    "hard_bounce": "\\fscx140\\fscy140\\t(0,140,\\fscx100\\fscy100)",
    "elastic": "\\fscx135\\fscy85\\t(0,100,\\fscx90\\fscy115)\\t(100,200,\\fscx100\\fscy100)",
    "zoom": "\\fscx125\\fscy125",
    "pulse": "\\fscx115\\fscy115\\t(0,150,\\fscx100\\fscy100)",
    "fade_in": "\\fad(80,0)",
    "slide_up": "\\move(0,15,0,0)",
    "slide_across": "\\fad(60,0)\\fscx112\\fscy112",
    "typewriter": "\\fad(60,0)",
    "typewriter_reveal": "\\fad(60,0)",
    "word_pill": "\\fscx112\\fscy112",
    "karaoke_clean": "\\fscx108\\fscy108",
    "shake": "\\t(0,50,\\frz2)\\t(50,100,\\frz-2)\\t(100,150,\\frz0)",
    "blur_scroll": "\\blur3\\t(0,120,\\blur0)",
    "neon_glow": "\\blur6\\fscx112\\fscy112",
    "none": "",
}


def get_animation_tags(animation: str, is_active: bool) -> str:
    """Return ASS override tags for chosen animation."""
    anim = (animation or "Karaoke").lower().replace(" ", "_")
    if not is_active:
        return ""
    return ANIMATION_TAGS.get(anim, "\\fscx116\\fscy116")


def normalize_clip_words(
    words: Optional[List[Dict[str, Any]]],
    clip_start_time: float = 0.0,
    clip_duration: float = 0.0,
    project_id: Optional[int] = None,
    clean_filler_words: bool = False,
) -> List[Dict[str, Any]]:
    """
    Normalize transcript words to clip-relative [0.0, clip_duration] timestamps:
    1. Fallback to slicing storage/projects/{project_id}/transcript.json if words empty
    2. Detect video-absolute timestamps (max_end > clip_duration + 1.0) and subtract clip_start_time
    3. Filter muted/deleted words (and optional filler words)
    4. Enforce rel_end >= rel_start + 0.08
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

    max_end = max(float(w.get("end", 0.0)) for w in raw_words)
    is_absolute = False
    if clip_duration > 0 and max_end > clip_duration + 1.0 and clip_start_time > 0.0:
        is_absolute = True

    normalized: List[Dict[str, Any]] = []
    for w in raw_words:
        if w.get("deleted") or w.get("muted"):
            continue
        if clean_filler_words and w.get("is_filler", False):
            continue
        text = str(w.get("word", "")).strip()
        if not text:
            continue

        raw_s = float(w.get("start", 0.0))
        raw_e = float(w.get("end", raw_s + 0.25))

        if is_absolute:
            rel_s = raw_s - clip_start_time
            rel_e = raw_e - clip_start_time
        else:
            rel_s = raw_s
            rel_e = raw_e

        if clip_duration > 0:
            if rel_e <= 0.0 or rel_s >= clip_duration:
                continue
            rel_s = max(0.0, rel_s)
            rel_e = min(clip_duration, rel_e)
        else:
            rel_s = max(0.0, rel_s)

        if rel_e < rel_s + 0.08:
            rel_e = rel_s + 0.08

        normalized.append({
            **w,
            "word": text,
            "start": round(rel_s, 3),
            "end": round(rel_e, 3),
        })

    return normalized


def group_words_into_lines(words: List[Dict[str, Any]], words_per_line: int = 3) -> List[List[Dict[str, Any]]]:
    """Group active words into punchy lines."""
    active_words = [w for w in words if not w.get("deleted", False) and not w.get("muted", False)]
    lines = []
    current_line = []
    for w in active_words:
        current_line.append(w)
        if len(current_line) >= words_per_line:
            lines.append(current_line)
            current_line = []
    if current_line:
        lines.append(current_line)
    return lines


def prepare_work_fonts_dir(work_dir: Path) -> str:
    """Copy custom fonts into work_dir/fonts so FFmpeg sees a relative ASCII fontsdir."""
    dst_fonts = work_dir / "fonts"
    dst_fonts.mkdir(parents=True, exist_ok=True)
    src_fonts = Path(settings.STORAGE_DIR) / "fonts"
    if src_fonts.exists():
        for ext in ("*.ttf", "*.otf", "*.TTF", "*.OTF"):
            for font_file in src_fonts.glob(ext):
                target = dst_fonts / font_file.name
                if not target.exists():
                    try:
                        shutil.copy2(font_file, target)
                    except Exception:
                        pass
    return "fonts"


def generate_ass_script(
    words: List[Dict[str, Any]],
    output_ass_path: str,
    caption_style: str = "Karaoke pop",
    caption_font: str = "Montserrat",
    caption_animation: str = "Karaoke",
    font_size_percent: int = 100,
    base_color: str = "#FFFFFF",
    active_color: str = "#C6F32F",
    outline_color: str = "#000000",
    hold_back_alpha: float = 1.0,
    highlight_shape: str = "Colour only",
    position_grid: str = "bottom_center",
    position_x: float = 50.0,
    position_y: float = 78.0,
    outline_width: float = 4.0,
    shadow_depth: float = 3.0,
    glow_intensity: float = 0.0,
    letter_spacing: float = 0.0,
    bold: bool = True,
    italic: bool = False,
    all_caps: bool = True,
    text_align: str = "Centre",
    timing_offset: float = 0.0,
    emoji_enabled: bool = False,
    clean_filler_words: bool = False,
    how_phrase_arrives: str = "All at once",
    hook_headline: Optional[str] = None,
    show_hook_banner: bool = True,
    video_width: int = 1080,
    video_height: int = 1920,
    clip_start_time: float = 0.0,
    clip_duration: float = 0.0,
    project_id: Optional[int] = None,
    preset_key: Optional[str] = None,
) -> str:
    """
    Generate full-featured Advanced SubStation Alpha (.ass) subtitle file:
    - Normalizes video-absolute word timestamps to clip-relative [0, clip_duration]
    - Supports all 12 style presets, custom font/size/alignment/colors/highlight/effects
    - Supports Word-by-word or All-at-once line modes
    - Renders wrapped Top Hook Banner in Layer 1
    """
    Path(output_ass_path).parent.mkdir(parents=True, exist_ok=True)

    # Support legacy preset_key ('hormozi', 'podcast', 'neon', 'minimalist')
    if preset_key and preset_key in ("hormozi", "podcast", "neon", "minimalist"):
        legacy_map = {
            "hormozi": "Hormozi",
            "podcast": "Documentary",
            "neon": "Neon Glow",
            "minimalist": "Creator White",
        }
        caption_style = legacy_map[preset_key]

    preset_def = CAPTION_STYLES_DEF.get(caption_style, CAPTION_STYLES_DEF["Karaoke pop"])
    font_name = FONT_MAP.get(caption_font, caption_font or preset_def.get("font", "Montserrat"))
    width_scale = video_width / 1080.0
    calculated_font_size = max(18, int(preset_def["font_size"] * (float(font_size_percent) / 100.0) * max(0.55, width_scale)))

    inactive_alpha_hex = float_alpha_to_hex(hold_back_alpha)
    ass_primary = hex_to_ass_color(base_color, alpha=inactive_alpha_hex)
    ass_active = hex_to_ass_color(active_color, alpha="00")
    ass_outline = hex_to_ass_color(outline_color, alpha="00")
    ass_back = "&H00000000&"
    border_style = preset_def.get("border_style", 1)

    if highlight_shape in ("Filled box", "Pill Box") or caption_style in ("Punch Box", "Brand Block"):
        border_style = 3
        ass_back = hex_to_ass_color(outline_color, alpha="40")

    an, pos_px, pos_py = get_3x3_position_coords(
        position_grid, position_x, position_y, video_width, video_height, text_align=text_align
    )

    b_flag = 1 if bold else 0
    i_flag = 1 if italic else 0

    header = f"""[Script Info]
Title: ClipShip AI Studio Captions
ScriptType: v4.00+
WrapStyle: 1
PlayResX: {video_width}
PlayResY: {video_height}
ScaledBorderAndShadow: yes

[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding
Style: MainCaption,{font_name},{calculated_font_size},{ass_primary},{ass_active},{ass_outline},{ass_back},{b_flag},{i_flag},0,0,100,100,{letter_spacing},0,{border_style},{outline_width},{shadow_depth},{an},40,40,60,1
Style: HookBanner,Montserrat,{max(18, int(44 * width_scale))},&H00FFFFFF&,&H0000FFFF&,&H00000000&,&H80000000&,1,0,0,0,100,100,2,0,3,6,2,8,90,90,{max(40, int(140 * (video_height / 1920.0)))},1

[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
"""

    events: List[str] = []

    # 1. Top Hook Banner in Layer 1 (0.0s to 3.5s)
    if show_hook_banner and hook_headline and hook_headline.strip():
        clean_headline = hook_headline.strip().upper()
        banner_margin_v = max(40, int(140 * (video_height / 1920.0)))
        banner_text = f"{{\\an8\\q1\\bord6\\shad2\\c&H0000FFFF&\\3c&H00000000&}}★ {clean_headline} ★"
        events.append(
            f"Dialogue: 1,{format_ass_time(0.0)},{format_ass_time(3.50)},HookBanner,,90,90,{banner_margin_v},,{banner_text}"
        )

    # 2. Normalize words to clip-relative timestamps
    norm_words = normalize_clip_words(
        words=words,
        clip_start_time=clip_start_time,
        clip_duration=clip_duration,
        project_id=project_id,
        clean_filler_words=clean_filler_words,
    )

    is_word_by_word = (how_phrase_arrives or "All at once").lower().replace("-", " ") == "word by word"
    lines = group_words_into_lines(norm_words, words_per_line=1 if is_word_by_word else 3)

    for line_idx, line in enumerate(lines):
        if not line:
            continue
        line_start = max(0.0, line[0].get("start", 0.0) + timing_offset)

        for i, target_word in enumerate(line):
            w_start = max(0.0, target_word.get("start", line_start) + timing_offset)
            raw_end = target_word.get("end", w_start + 0.25) + timing_offset
            w_end = max(raw_end, w_start + 0.12)

            if i == len(line) - 1:
                if line_idx + 1 < len(lines) and lines[line_idx + 1]:
                    next_line_start = max(0.0, lines[line_idx + 1][0].get("start", w_end) + timing_offset)
                    if next_line_start > w_end and (next_line_start - w_end) < 0.8:
                        w_end = next_line_start
                else:
                    w_end = max(w_end, w_start + 0.5)

            formatted_words = []
            for j, w in enumerate(line):
                raw_w = w.get("word", "").strip()
                text_w = raw_w.upper() if all_caps else raw_w

                if emoji_enabled and raw_w.lower() in EMOJI_KEYWORDS:
                    text_w = f"{EMOJI_KEYWORDS[raw_w.lower()]} {text_w}"

                if j == i:
                    anim_tags = get_animation_tags(caption_animation, is_active=True)
                    shape_tags = "\\u1" if highlight_shape == "Underline" else ""
                    glow_tags = f"\\blur{int(glow_intensity * 1.5)}" if glow_intensity > 0.0 else ""
                    tags = f"\\c{ass_active}{anim_tags}{shape_tags}{glow_tags}"
                    formatted_words.append(f"{{{tags}}}{text_w}{{\\rMainCaption}}")
                else:
                    formatted_words.append(text_w)

            pos_tag = f"{{\\an{an}\\pos({pos_px},{pos_py})}}"
            line_text = pos_tag + " ".join(formatted_words)
            events.append(
                f"Dialogue: 0,{format_ass_time(w_start)},{format_ass_time(w_end)},MainCaption,,0,0,0,,{line_text}"
            )

    content = header + "\n".join(events) + "\n"
    with open(output_ass_path, "w", encoding="utf-8") as f:
        f.write(content)

    return output_ass_path
