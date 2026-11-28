from app.services.reframer import (
    resolve_target_dimensions,
    clamp_crop_coordinates,
    calculate_ema_smooth,
    get_webcam_crop_coordinates,
    build_multi_aspect_filter
)

def test_resolve_target_dimensions():
    assert resolve_target_dimensions("9:16") == (1080, 1920)
    assert resolve_target_dimensions("16:9") == (1920, 1080)
    assert resolve_target_dimensions("1:1") == (1080, 1080)
    assert resolve_target_dimensions("4:5") == (1080, 1350)
    assert resolve_target_dimensions("21:9") == (2560, 1080)
    # Custom dimensions should be rounded to even
    assert resolve_target_dimensions("custom", 721, 1281) == (720, 1280)

def test_clamp_crop_coordinates():
    video_w = 1920
    crop_w = 608
    assert clamp_crop_coordinates(500, crop_w, video_w) == 500
    assert clamp_crop_coordinates(-50, crop_w, video_w) == 0
    assert clamp_crop_coordinates(2000, crop_w, video_w) == (video_w - crop_w)

def test_get_webcam_crop_coordinates():
    cx, cy, cw, ch = get_webcam_crop_coordinates("top_right", 1920, 1080)
    assert cx > 0
    assert cy == 0
    assert cw % 2 == 0
    assert ch % 2 == 0

def test_build_multi_aspect_filter_layouts():
    # 1. auto_track with zoom and pan
    vf_auto = build_multi_aspect_filter("9:16", "auto_track", 1920, 1080, zoom=1.2, pan_x_offset=0.05)
    assert "crop=" in vf_auto
    assert "scale=1080:1920" in vf_auto

    # 2. webcam_gameplay_split
    vf_split = build_multi_aspect_filter("9:16", "webcam_gameplay_split", 1920, 1080, webcam_corner="top_left", webcam_split_ratio=0.35)
    assert "vstack=inputs=2" in vf_split
    assert "split=2" in vf_split

    # 3. webcam_gameplay_pip
    vf_pip = build_multi_aspect_filter("16:9", "webcam_gameplay_pip", 1920, 1080, pip_border_color="#10B981")
    assert "pad=" in vf_pip
    assert "overlay=" in vf_pip

    # 4. split_podcast
    vf_pod = build_multi_aspect_filter("9:16", "split_podcast", 1920, 1080)
    assert "vstack=inputs=2" in vf_pod

    # 5. fit_blur
    vf_blur = build_multi_aspect_filter("9:16", "fit_blur", 1920, 1080)
    assert "gblur=sigma=24" in vf_blur
