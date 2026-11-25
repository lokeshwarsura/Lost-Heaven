from typing import List, Dict, Any, Optional

from app.services.captions_ass import (
    FONT_MAP,
    CAPTION_STYLES_DEF,
    EMOJI_KEYWORDS,
    ANIMATION_TAGS,
    hex_to_ass_color,
    float_alpha_to_hex,
    format_ass_time,
    get_3x3_position_coords,
    get_animation_tags,
    group_words_into_lines,
    normalize_clip_words,
    prepare_work_fonts_dir,
    generate_ass_script,
)

# Backward compatible aliases for all studio services and tests
generate_clipshlip_ass_script = generate_ass_script


def generate_ass_script_pro(
    words: List[Dict[str, Any]],
    output_ass_path: str,
    caption_font: str = "Montserrat",
    caption_animation: str = "pop_scale",
    base_color: str = "#FFFFFF",
    active_color: str = "#C6F32F",
    outline_color: str = "#000000",
    font_size: int = 64,
    y_offset: int = 320,
    stroke_width: float = 4.0,
    shadow_depth: float = 3.0,
    all_caps: bool = True,
    clean_filler_words: bool = False,
    hook_headline: Optional[str] = None,
    show_hook_banner: bool = True,
    video_width: int = 1080,
    video_height: int = 1920,
) -> str:
    return generate_ass_script(
        words=words,
        output_ass_path=output_ass_path,
        caption_style="Karaoke pop",
        caption_font=caption_font,
        caption_animation=caption_animation,
        base_color=base_color,
        active_color=active_color,
        outline_color=outline_color,
        font_size_percent=100,
        outline_width=stroke_width,
        shadow_depth=shadow_depth,
        all_caps=all_caps,
        clean_filler_words=clean_filler_words,
        hook_headline=hook_headline,
        show_hook_banner=show_hook_banner,
        video_width=video_width,
        video_height=video_height,
    )
