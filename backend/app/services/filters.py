import logging
from typing import Dict, Any, List, Optional

logger = logging.getLogger(__name__)

COLOR_PRESETS = {
    "none": {
        "name": "Original",
        "description": "True-to-source natural master colors",
        "filter": "",
    },
    "cinematic": {
        "name": "Cinematic Film",
        "description": "S-curve contrast with teal shadows and warm highlights",
        "filter": "colorbalance=rs=0.05:gs=-0.02:bs=-0.08:rm=0.08:gm=0.02:bm=-0.05:rh=0.10:gh=0.04:bh=-0.06,eq=contrast=1.15:saturation=1.10",
    },
    "vibrant_gaming": {
        "name": "Vibrant Gaming",
        "description": "Punchy saturation boost with crisp unsharp texture",
        "filter": "eq=contrast=1.20:saturation=1.40:gamma=1.05,unsharp=5:5:1.0:5:5:0.0",
    },
    "warm_vintage": {
        "name": "Warm Vintage",
        "description": "Golden hour warmth with soft highlights",
        "filter": "colorbalance=rs=0.22:gs=0.05:bs=-0.22:rm=0.18:gm=0.04:bm=-0.18:rh=0.14:bh=-0.14,eq=contrast=1.06:saturation=1.10:gamma=1.02",
    },
    "cool_tech": {
        "name": "Cool Tech",
        "description": "Modern crisp cyber blue and cyan balance",
        "filter": "colorbalance=rs=-0.16:gs=0.02:bs=0.22:rm=-0.14:gm=0.04:bm=0.18:rh=-0.12:bh=0.16,eq=contrast=1.12:saturation=1.15",
    },
    "bw_noir": {
        "name": "B&W Noir",
        "description": "High-contrast dramatic monochrome",
        "filter": "colorchannelmixer=.3:.4:.3:0:.3:.4:.3:0:.3:.4:.3,eq=contrast=1.35:brightness=-0.02",
    },
    "hdr_pop": {
        "name": "HDR Pop",
        "description": "High dynamic range pop with vivid clarity",
        "filter": "eq=gamma=1.10:saturation=1.45:contrast=1.22:brightness=0.02,unsharp=3:3:0.8:3:3:0.0",
    },
}


def _norm_signed(val: float) -> float:
    """Normalize either normalized [-1..1] or percentage [-100..100] values."""
    v = float(val or 0.0)
    if abs(v) > 1.0:
        return max(-1.0, min(1.0, v / 100.0))
    return max(-1.0, min(1.0, v))


def _norm_unit(val: float) -> float:
    """Normalize either normalized [0..1] or percentage [0..100] values."""
    v = float(val or 0.0)
    if v > 1.0:
        return max(0.0, min(1.0, v / 100.0))
    return max(0.0, min(1.0, v))


def _norm_mult(val: float, default: float = 1.0) -> float:
    """Normalize multiplier sliders (e.g. contrast/saturation 0.0..2.5) or percentage (-100..100)."""
    if val is None:
        return default
    v = float(val)
    if v < 0.0 or v > 3.0:
        return max(0.0, min(2.5, 1.0 + v / 100.0))
    return max(0.0, min(2.5, v))


def build_advanced_color_filter(
    preset: str = "none",
    exposure: float = 0.0,
    brightness: float = 0.0,
    contrast: float = 1.0,
    lift_mids: float = 0.0,
    warmth: float = 0.0,
    saturation: float = 1.0,
    black_and_white: float = 0.0,
    vintage: float = 0.0,
    tint_color: str = "none",
    tint_strength: float = 0.0,
    glow: float = 0.0,
    smooth: float = 0.0,
    sharpness: float = 0.0,
    blur: float = 0.0,
    film_grain: float = 0.0,
    vignette: float = 0.0,
    bottom_fade: float = 0.0,
) -> str:
    """
    Build deterministic FFmpeg video filter chain for Step 3 Light, Color, and Effects.
    Safely clamps all FFmpeg parameters and supports both normalized (0..1 / 0.5..1.8)
    and percentage (-100..100 / 0..100) slider ranges.
    """
    filters: List[str] = []

    norm_exp = _norm_signed(exposure)
    norm_bright = _norm_signed(brightness)
    norm_contrast = _norm_mult(contrast, default=1.0)
    norm_warmth = _norm_signed(warmth)
    norm_sat = _norm_mult(saturation, default=1.0)
    bw_factor = _norm_unit(black_and_white)
    vintage_val = _norm_unit(vintage)
    glow_val = _norm_unit(glow)
    smooth_val = _norm_unit(smooth)
    sharp_val = _norm_unit(sharpness)
    blur_val = _norm_unit(blur)
    grain_val = _norm_unit(film_grain)
    vignette_val = _norm_unit(vignette)
    fade_val = _norm_unit(bottom_fade)

    # Interpret lift_mids cleanly: 0.0 or 1.0 = neutral; (0.01..0.55] uses legacy curve lift;
    # UI slider range (0.55..2.0] maps directly to eq gamma.
    raw_lm = float(lift_mids or 0.0)
    use_legacy_curve = 0.01 < raw_lm <= 0.55
    if abs(raw_lm) < 0.01 or abs(raw_lm - 1.0) < 0.01 or use_legacy_curve:
        gamma_val = 1.0
    elif 0.55 < raw_lm <= 2.0:
        gamma_val = max(0.5, min(2.0, raw_lm))
    else:
        gamma_val = max(0.5, min(2.0, 1.0 + raw_lm / 100.0))

    # 1. Preset filter base: apply directly when caller passed a preset with neutral manual Light/Color sliders
    sliders_are_neutral = (
        abs(norm_exp) < 0.005
        and abs(norm_bright) < 0.005
        and abs(norm_contrast - 1.0) < 0.005
        and abs(gamma_val - 1.0) < 0.005
        and abs(norm_warmth) < 0.005
        and abs(norm_sat - 1.0) < 0.005
        and bw_factor < 0.005
        and vintage_val < 0.005
    )
    p = COLOR_PRESETS.get((preset or "none").lower(), COLOR_PRESETS["none"])
    if sliders_are_neutral and p["filter"]:
        filters.append(p["filter"])

    # 2. Light & Contrast & Saturation & Gamma (clamped safely for FFmpeg eq)
    total_bright = max(-0.65, min(0.65, norm_bright + norm_exp * 0.65))
    eq_c = max(0.30, min(2.20, norm_contrast))
    eq_s = max(0.00, min(2.50, norm_sat * (1.0 - bw_factor)))
    eq_g = max(0.30, min(2.20, gamma_val))

    if (
        abs(total_bright) > 0.002
        or abs(eq_c - 1.0) > 0.002
        or abs(eq_s - 1.0) > 0.002
        or abs(eq_g - 1.0) > 0.002
    ):
        filters.append(f"eq=brightness={total_bright:.3f}:contrast={eq_c:.3f}:saturation={eq_s:.3f}:gamma={eq_g:.3f}")

    if bw_factor >= 0.99:
        filters.append("hue=s=0")

    if use_legacy_curve:
        mid_val = 0.5 + (raw_lm * 0.20)
        filters.append(f"curves=m='0/0 0.5/{mid_val:.2f} 1/1'")

    # 3. Warmth adjustment (-100 Cool .. +100 Warm)
    if abs(norm_warmth) > 0.005:
        w = max(-0.80, min(0.80, norm_warmth))
        red_shift = max(-0.6, min(0.6, w * 0.35))
        blue_shift = max(-0.6, min(0.6, -w * 0.35))
        filters.append(
            f"colorbalance=rs={red_shift:.2f}:bs={blue_shift:.2f}:rm={red_shift * 0.75:.2f}:bm={blue_shift * 0.75:.2f}"
        )

    # 4. Vintage faded / sepia look
    if vintage_val > 0.01:
        v = vintage_val
        filters.append(
            f"colorbalance=rs={v * 0.14:.2f}:gs={v * 0.04:.2f}:bs={-v * 0.12:.2f}:rh={v * 0.10:.2f}:bh={-v * 0.08:.2f}"
        )
        filters.append(f"curves=m='0/{v * 0.04:.3f} 0.5/0.5 1/{1.0 - v * 0.04:.3f}'")

    # 5. Color Tint pills (None, Red, Blue, Green, Yellow)
    t_lower = (tint_color or "none").strip().lower()
    t_str_val = _norm_unit(tint_strength)
    if t_lower not in ("", "none") and t_str_val <= 0.01:
        t_str_val = 0.25
    if t_lower not in ("", "none") and t_str_val > 0.01:
        ts = max(0.0, min(0.6, t_str_val * 0.45))
        if t_lower == "red":
            filters.append(f"colorbalance=rs={ts:.2f}:rm={ts:.2f}:rh={ts:.2f}")
        elif t_lower == "blue":
            filters.append(f"colorbalance=bs={ts:.2f}:bm={ts:.2f}:bh={ts:.2f}")
        elif t_lower == "green":
            filters.append(f"colorbalance=gs={ts:.2f}:gm={ts:.2f}:gh={ts:.2f}")
        elif t_lower == "yellow":
            filters.append(f"colorbalance=rs={ts * 0.8:.2f}:gs={ts * 0.8:.2f}:bs={-ts * 0.5:.2f}")

    # 6. Dreamy Glow (subtle highlight bloom + soft contrast veil)
    if glow_val > 0.01:
        gb_sigma = max(1.5, glow_val * 6.0)
        filters.append(
            f"split[_glow_orig][_glow_blur];"
            f"[_glow_blur]gblur=sigma={gb_sigma:.1f},eq=brightness={glow_val * 0.12:.3f}[_glow_soft];"
            f"[_glow_orig][_glow_soft]blend=all_mode=screen:all_opacity={min(0.45, glow_val * 0.40):.2f}"
        )

    # 7. Skin Smooth / smartblur
    if smooth_val > 0.01:
        radius = max(0.5, min(4.0, 0.8 + smooth_val * 2.5))
        filters.append(f"smartblur={radius:.1f}:-0.8:0")

    # 8. Sharpness (unsharp)
    if sharp_val > 0.01:
        amount = min(2.5, sharp_val * 2.0)
        filters.append(f"unsharp=5:5:{amount:.2f}:5:5:0.0")

    # 9. Blur (gblur on footage)
    if blur_val > 0.01:
        sigma = max(0.8, blur_val * 12.0)
        filters.append(f"gblur=sigma={sigma:.1f}")

    # 10. Film grain (noise)
    if grain_val > 0.01:
        strength = max(1, min(35, int(grain_val * 25)))
        filters.append(f"noise=alls={strength}:allf=t+u")

    # 11. Edge Vignette
    if vignette_val >= 0.99:
        filters.append("vignette=PI/4")
    elif vignette_val > 0.01:
        angle_rad = (3.14159 / 4.0) * vignette_val
        filters.append(f"vignette={angle_rad:.3f}")

    # 12. Bottom Fade (Dark box gradient behind caption safe zone)
    if fade_val > 0.01:
        alpha = min(0.80, fade_val * 0.70)
        filters.append(f"drawbox=y=ih*0.62:h=ih*0.38:color=black@{alpha:.2f}:t=fill")

    return ",".join(filters)


def build_color_filter(
    preset: str = "none",
    brightness: float = 0.0,
    contrast: float = 1.0,
    saturation: float = 1.0,
    vignette: bool = False,
) -> str:
    return build_advanced_color_filter(
        preset=preset,
        brightness=brightness,
        contrast=contrast,
        saturation=saturation,
        vignette=1.0 if vignette else 0.0,
    )
