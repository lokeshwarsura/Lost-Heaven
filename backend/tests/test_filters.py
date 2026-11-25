from app.services.filters import COLOR_PRESETS, build_color_filter

def test_color_presets_definitions():
    expected_presets = ["none", "cinematic", "vibrant_gaming", "warm_vintage", "cool_tech", "bw_noir", "hdr_pop"]
    for p in expected_presets:
        assert p in COLOR_PRESETS
        assert "name" in COLOR_PRESETS[p]
        assert "description" in COLOR_PRESETS[p]

def test_build_color_filter_none():
    # Original should be empty when sliders are default
    assert build_color_filter("none", 0.0, 1.0, 1.0, False) == ""

def test_build_color_filter_cinematic_with_vignette():
    f = build_color_filter("cinematic", 0.0, 1.0, 1.0, True)
    assert "colorbalance=" in f
    assert "vignette=PI/4" in f

def test_build_color_filter_vibrant_gaming():
    f = build_color_filter("vibrant_gaming", 0.0, 1.0, 1.0, False)
    assert "unsharp=" in f
    assert "saturation=" in f

def test_build_color_filter_bw_noir():
    f = build_color_filter("bw_noir", 0.0, 1.0, 1.0, False)
    assert "colorchannelmixer=" in f

def test_build_color_filter_manual_sliders():
    f = build_color_filter("none", brightness=0.15, contrast=1.25, saturation=1.40, vignette=False)
    assert "eq=brightness=0.150:contrast=1.250:saturation=1.400" in f
