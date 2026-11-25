import os
import tempfile
from app.services.captions import (
    FONT_MAP,
    ANIMATION_TAGS,
    hex_to_ass_color,
    format_ass_time,
    group_words_into_lines,
    generate_ass_script_pro
)

def test_font_map_and_animation_tags():
    assert len(FONT_MAP) >= 8
    assert "Montserrat ExtraBold" in FONT_MAP
    assert "Anton" in FONT_MAP
    assert "Bebas Neue" in FONT_MAP
    assert "Impact" in FONT_MAP

    expected_anims = ["pop_scale", "bounce_spring", "typewriter_reveal", "word_pill", "neon_glow", "karaoke_clean"]
    for a in expected_anims:
        assert a in ANIMATION_TAGS

def test_hex_to_ass_color():
    # #FFFF00 (Yellow) -> &H0000FFFF&
    assert hex_to_ass_color("#FFFF00") == "&H0000FFFF&"
    # #00FF7F (Mint) -> &H007FFF00&
    assert hex_to_ass_color("#00FF7F") == "&H007FFF00&"
    # #000000 (Black) -> &H00000000&
    assert hex_to_ass_color("#000000") == "&H00000000&"

def test_generate_ass_script_pro_all_animations():
    words = [
        {"word": "UNSTOPPABLE", "start": 0.2, "end": 0.6},
        {"word": "VIRAL", "start": 0.6, "end": 1.0},
        {"word": "GROWTH", "start": 1.0, "end": 1.5}
    ]

    with tempfile.TemporaryDirectory() as tmpdir:
        for anim in ["pop_scale", "bounce_spring", "typewriter_reveal", "word_pill", "neon_glow", "karaoke_clean"]:
            out_file = os.path.join(tmpdir, f"captions_{anim}.ass")
            res = generate_ass_script_pro(
                words=words,
                output_ass_path=out_file,
                caption_font="Anton",
                caption_animation=anim,
                base_color="#FFFFFF",
                active_color="#FFFF00",
                hook_headline="THE ULTIMATE HACK",
                show_hook_banner=True
            )
            assert os.path.exists(res)
            with open(res, "r", encoding="utf-8") as f:
                content = f.read()

            assert "[Script Info]" in content
            assert "[V4+ Styles]" in content
            assert "[Events]" in content
            assert "HookBanner" in content
            assert "THE ULTIMATE HACK" in content
            assert "Anton" in content
