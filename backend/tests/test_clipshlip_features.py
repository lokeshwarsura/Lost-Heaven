import os
import pytest
from app.services.hardware import detect_hardware_specs
from app.services.captions import (
    generate_clipshlip_ass_script,
    CAPTION_STYLES_DEF,
    EMOJI_KEYWORDS,
    get_3x3_position_coords
)
from app.services.filters import build_advanced_color_filter
from app.services.preview_frame import render_exact_preview_frame
from app.models import BrandPreset, CaptionStylePreset, GeminiAnalysisResponse, GeminiClipItem

def test_hardware_detection():
    specs = detect_hardware_specs()
    assert "cpu_model" in specs
    assert specs["cpu_cores"] > 0
    assert specs["ram_gb"] > 0
    assert "active_encoder" in specs
    assert specs["active_encoder"] in ["h264_nvenc", "libx264", "h264_videotoolbox"]
    assert "whisper_model" in specs

def test_caption_styles_definitions():
    assert len(CAPTION_STYLES_DEF) == 12
    required_styles = [
        "Cinematic", "Karaoke pop", "Bold impact", "Punch Box",
        "Clean Yellow", "Neon Glow", "Underline Pop", "Documentary",
        "Hormozi", "Creator White", "Anton Impact", "Brand Block"
    ]
    for s in required_styles:
        assert s in CAPTION_STYLES_DEF
        assert "font" in CAPTION_STYLES_DEF[s]
        assert "base_color" in CAPTION_STYLES_DEF[s]
        assert "active_color" in CAPTION_STYLES_DEF[s]

def test_3x3_position_coords():
    # Test bottom_center
    an, px, py = get_3x3_position_coords("bottom_center", 50.0, 78.0, 1080, 1920)
    assert an == 2
    assert px == 540
    assert py == int(1920 * 0.78)

    # Test top_left
    an, px, py = get_3x3_position_coords("top_left", 15.0, 18.0, 1080, 1920)
    assert an == 7
    assert px == int(1080 * 0.15)
    assert py == int(1920 * 0.18)

def test_ass_generation_with_emojis(tmp_path):
    out_ass = str(tmp_path / "test_emojis.ass")
    words = [
        {"word": "This", "start": 0.0, "end": 0.5},
        {"word": "money", "start": 0.5, "end": 1.0},
        {"word": "is", "start": 1.0, "end": 1.3},
        {"word": "crazy", "start": 1.3, "end": 1.8}
    ]
    generate_clipshlip_ass_script(
        words=words,
        output_ass_path=out_ass,
        caption_style="Karaoke pop",
        emoji_enabled=True,
        position_grid="bottom_center",
        position_x=50.0,
        position_y=78.0,
        video_width=1080,
        video_height=1920
    )
    assert os.path.exists(out_ass)
    with open(out_ass, "r", encoding="utf-8") as f:
        content = f.read()
    assert "💰" in content
    assert "🤯" in content
    assert "\\pos(540," in content

def test_advanced_color_filter():
    f = build_advanced_color_filter(
        preset="cinematic",
        exposure=0.10,
        brightness=0.05,
        contrast=1.20,
        lift_mids=0.50,
        warmth=0.20,
        black_and_white=0.0,
        tint_color="blue",
        tint_strength=0.30,
        smooth=0.40,
        sharpness=0.50,
        film_grain=0.20,
        vignette=0.50,
        bottom_fade=0.60
    )
    assert "curves=" in f
    assert "colorbalance=" in f
    assert "smartblur=" in f
    assert "unsharp=" in f
    assert "noise=" in f
    assert "vignette=" in f
    assert "drawbox=" in f

def test_brand_and_style_presets():
    bp = BrandPreset(name="Test Kit", watermark_corner="top_left", aspect_ratio="9:16")
    assert bp.name == "Test Kit"
    assert bp.watermark_corner == "top_left"

    cp = CaptionStylePreset(name="Custom Style", active_color="#C6F32F", hold_back_alpha=0.6)
    assert cp.name == "Custom Style"
    assert cp.active_color == "#C6F32F"
    assert cp.hold_back_alpha == 0.6
