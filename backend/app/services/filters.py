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
    "kodak_portra_400": {
        "name": "Kodak Portra 400",
        "description": "Creamy skin tones, gentle lifted blacks, warm analog highlights",
        "filter": "colorbalance=rs=0.08:gs=0.04:bs=-0.05:rm=0.10:gm=0.03:bm=-0.06:rh=0.12:gh=0.06:bh=-0.04,eq=contrast=1.08:saturation=1.08:gamma=1.04,curves=m='0/0.035 0.5/0.51 1/0.98'",
    },
    "teal_orange": {
        "name": "Teal & Orange Blockbuster",
        "description": "Cinema blockbuster teal shadows with warm skin highlights",
        "filter": "colorbalance=rs=-0.18:gs=0.04:bs=0.22:rm=0.06:gm=0.0:bm=-0.04:rh=0.20:gh=0.06:bh=-0.16,eq=contrast=1.20:saturation=1.18",
    },
    "cyberpunk_neon": {
        "name": "Cyberpunk Neon",
        "description": "Electric magenta highlights with deep cyan shadow push",
        "filter": "colorbalance=rs=-0.14:gs=0.06:bs=0.26:rm=0.12:gm=-0.06:bm=0.18:rh=0.22:gh=-0.05:bh=0.24,vibrance=intensity=0.45,eq=contrast=1.22:saturation=1.30",
    },
    "moody_dark": {
        "name": "Moody Dark",
        "description": "Low-key matte shadows and rich filmic density",
        "filter": "colorbalance=rs=-0.06:gs=-0.02:bs=0.04:rm=-0.04:gm=-0.03:bm=0.02,eq=contrast=1.24:brightness=-0.06:saturation=0.88:gamma=0.92,curves=m='0/0.04 0.5/0.46 1/0.94'",
    },
    "crisp_daylight": {
        "name": "Crisp Daylight",
        "description": "Clean high-clarity outdoor neutral contrast and vibrant foliage",
        "filter": "eq=contrast=1.12:brightness=0.03:saturation=1.14,vibrance=intensity=0.25,unsharp=5:5:0.75:5:5:0.0",
    },
    "bw_16mm_noir": {
        "name": "16mm B&W Noir",
        "description": "Silver halide monochrome contrast with organic grain",
        "filter": "hue=s=0,eq=contrast=1.40:brightness=-0.03:gamma=0.95,curves=m='0/0.03 0.5/0.50 1/0.97',noise=alls=10:allf=t+u",
    },
    "golden_hour": {
        "name": "Golden Hour",
        "description": "Sun-kissed amber rim warmth with gentle highlight bloom",
        "filter": "colorbalance=rs=0.20:gs=0.08:bs=-0.16:rm=0.18:gm=0.06:bm=-0.14:rh=0.22:gh=0.10:bh=-0.14,eq=contrast=1.10:saturation=1.18:gamma=1.04",
    },
    "vhs_retro": {
        "name": "VHS Retro",
        "description": "90s tape matte blacks, warm chroma bleed, subtle grain",
        "filter": "curves=m='0/0.07 0.5/0.50 1/0.92',colorbalance=rs=0.06:gs=-0.04:bs=0.10:rh=0.10:bh=-0.06,eq=contrast=0.96:saturation=1.15,rgbashift=rh=2:bh=-2:edge=smear,noise=alls=14:allf=t",
    },
    "clean_rec709": {
        "name": "Clean Rec.709",
        "description": "Broadcast-calibrated studio skin tones and crisp dynamic range",
        "filter": "colorbalance=rs=0.02:gs=0.01:bs=-0.02:rm=0.02:gm=0.01:bm=-0.01:rh=0.02:bh=-0.02,eq=contrast=1.08:saturation=1.06:gamma=1.02,unsharp=3:3:0.45:3:3:0.0",
    },
    "moody_noir": {
        "name": "Moody Noir",
        "description": "Crushed cinematic blacks, desaturated cool shadows, dramatic micro-contrast",
        "filter": "colorbalance=rs=-0.10:gs=-0.02:bs=0.14:rm=-0.06:gm=-0.03:bm=0.08:rh=-0.04:bh=0.04,eq=contrast=1.14:brightness=-0.03:saturation=0.84:gamma=0.94,curves=m='0/0.02 0.28/0.22 0.72/0.76 1/0.94'",
    },
    "bw_16mm": {
        "name": "16mm B&W",
        "description": "High-contrast silver-halide monochrome with subtle organic film grain",
        "filter": "eq=contrast=1.16:brightness=-0.01:gamma=0.97,curves=m='0/0.025 0.5/0.50 1/0.975',noise=alls=8:allf=t+u,hue=s=0",
    },
    "gaming_pop": {
        "name": "Gaming Pop",
        "description": "Crisp skin-safe vibrance, razor local clarity sharpening, deep shadow contrast",
        "filter": "eq=contrast=1.12:saturation=1.14:gamma=1.02,vibrance=intensity=0.22,curves=m='0/0 0.25/0.23 0.75/0.78 1/1',unsharp=5:5:0.75:5:5:0.0",
    },
    "high_contrast_gaming": {
        "name": "High-Contrast Gaming",
        "description": "HUD-crisp highlights, punchy vibrance, razor sharpness",
        "filter": "eq=contrast=1.28:saturation=1.38:gamma=1.04,vibrance=intensity=0.35,unsharp=5:5:1.10:5:5:0.0",
    },
}


# Curated 9 Studio Visual Presets with calibrated slider targets matching WYSIWYG thumbnails
STUDIO_COLOR_PRESETS: List[Dict[str, Any]] = [
    {
        "id": "none",
        "name": "Original",
        "description": "Neutral bypass",
        "values": {
            "exposure": 0, "contrast": 1.0, "highlights": 0, "shadows": 0,
            "sharpness": 0, "vignette": 0, "color_temp": 0, "tint_shift": 0,
            "vibrance": 0, "saturation": 1.0, "matte_fade": 0, "halation_glow": 0,
            "warmth": 0, "brightness": 0, "lift_mids": 1.0, "black_and_white": 0, "vintage": 0,
        },
    },
    {
        "id": "kodak_portra_400",
        "name": "Kodak Portra 400",
        "description": "Warm golden highlights, creamy skin tones, lifted film shadows",
        "values": {
            "exposure": 0.03, "contrast": 1.04, "highlights": -8, "shadows": 14,
            "sharpness": 0.12, "vignette": 0.16, "color_temp": 14, "tint_shift": 4,
            "vibrance": 12, "saturation": 1.04, "matte_fade": 14, "halation_glow": 20,
            "warmth": 0, "brightness": 0, "lift_mids": 1.0, "black_and_white": 0, "vintage": 0,
        },
    },
    {
        "id": "teal_orange",
        "name": "Teal & Orange",
        "description": "Deep cyan shadows with warm amber skin & highlight separation",
        "values": {
            "exposure": 0.0, "contrast": 1.08, "highlights": 8, "shadows": -10,
            "sharpness": 0.20, "vignette": 0.26, "color_temp": 8, "tint_shift": -6,
            "vibrance": 16, "saturation": 1.08, "matte_fade": 6, "halation_glow": 12,
            "warmth": 0, "brightness": 0, "lift_mids": 1.0, "black_and_white": 0, "vintage": 0,
        },
    },
    {
        "id": "cyberpunk_neon",
        "name": "Cyberpunk Neon",
        "description": "Cool violet/magenta shadows, electric cyan highlights, punchy contrast",
        "values": {
            "exposure": 0.02, "contrast": 1.10, "highlights": 14, "shadows": -10,
            "sharpness": 0.24, "vignette": 0.24, "color_temp": -18, "tint_shift": 26,
            "vibrance": 28, "saturation": 1.14, "matte_fade": 4, "halation_glow": 32,
            "warmth": 0, "brightness": 0, "lift_mids": 1.0, "black_and_white": 0, "vintage": 0,
        },
    },
    {
        "id": "moody_noir",
        "name": "Moody Noir",
        "description": "Crushed blacks, cool desaturated shadows, dramatic micro-contrast",
        "values": {
            "exposure": -0.06, "contrast": 1.12, "highlights": -10, "shadows": -18,
            "sharpness": 0.18, "vignette": 0.38, "color_temp": -12, "tint_shift": -2,
            "vibrance": -12, "saturation": 0.88, "matte_fade": 18, "halation_glow": 8,
            "warmth": 0, "brightness": 0, "lift_mids": 1.0, "black_and_white": 0, "vintage": 0,
        },
    },
    {
        "id": "golden_hour",
        "name": "Golden Hour",
        "description": "Warm sunset amber glow, lifted midtones, soft halation bloom",
        "values": {
            "exposure": 0.06, "contrast": 1.04, "highlights": 12, "shadows": 14,
            "sharpness": 0.10, "vignette": 0.18, "color_temp": 30, "tint_shift": 6,
            "vibrance": 18, "saturation": 1.08, "matte_fade": 10, "halation_glow": 28,
            "warmth": 0, "brightness": 0, "lift_mids": 1.0, "black_and_white": 0, "vintage": 0,
        },
    },
    {
        "id": "bw_16mm",
        "name": "16mm B&W",
        "description": "High-contrast silver-halide monochrome with organic grain",
        "values": {
            "exposure": -0.02, "contrast": 1.16, "highlights": 10, "shadows": -14,
            "sharpness": 0.22, "vignette": 0.36, "color_temp": 0, "tint_shift": 0,
            "vibrance": 0, "saturation": 0.88, "matte_fade": 16, "halation_glow": 16,
            "warmth": 0, "brightness": 0, "lift_mids": 1.0, "black_and_white": 0, "vintage": 0,
        },
    },
    {
        "id": "gaming_pop",
        "name": "Gaming Pop",
        "description": "Crisp vibrance, local clarity sharpening, deep shadow contrast",
        "values": {
            "exposure": 0.04, "contrast": 1.12, "highlights": 12, "shadows": -8,
            "sharpness": 0.34, "vignette": 0.0, "color_temp": 4, "tint_shift": 0,
            "vibrance": 26, "saturation": 1.18, "matte_fade": 0, "halation_glow": 0,
            "warmth": 0, "brightness": 0, "lift_mids": 1.0, "black_and_white": 0, "vintage": 0,
        },
    },
    {
        "id": "clean_rec709",
        "name": "Clean Rec.709",
        "description": "Broadcast white balance, natural skin protection, crisp dynamic range",
        "values": {
            "exposure": 0.02, "contrast": 1.04, "highlights": 4, "shadows": 4,
            "sharpness": 0.14, "vignette": 0.0, "color_temp": 2, "tint_shift": 0,
            "vibrance": 10, "saturation": 1.04, "matte_fade": 0, "halation_glow": 0,
            "warmth": 0, "brightness": 0, "lift_mids": 1.0, "black_and_white": 0, "vintage": 0,
        },
    },
]


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


def _escape_ffmpeg_filter_path(raw_path: str) -> str:
    """Convert Windows backslashes to forward slashes and escape drive colons for FFmpeg `lut3d=file=...`."""
    clean = str(raw_path).replace("\\", "/")
    if len(clean) >= 2 and clean[1] == ":" and clean[0].isalpha():
        clean = clean[0] + "\\:" + clean[2:]
    return clean.replace("'", "\\'")


def build_advanced_color_filter(
    preset: str = "none",
    preset_intensity: float = 1.0,
    exposure: float = 0.0,
    brightness: float = 0.0,
    contrast: float = 1.0,
    lift_mids: float = 0.0,
    highlights: float = 0.0,
    shadows: float = 0.0,
    warmth: float = 0.0,
    color_temp: float = 0.0,
    tint_shift: float = 0.0,
    vibrance: float = 0.0,
    matte_fade: float = 0.0,
    halation_glow: float = 0.0,
    lut_path: Optional[str] = None,
    chroma_split: float = 0.0,
    letterbox: str = "none",
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
    target_w: int = 1080,
    target_h: int = 1920,
) -> str:
    """
    Build deterministic FFmpeg video filter chain for Step 3 Cinema Color, .cube 3D LUTs,
    preset strength blending, Highlights/Shadows recovery, Halation, Motion/Lens FX, and Letterbox bars.
    """
    filters: List[str] = []

    p_strength = _norm_unit(preset_intensity if preset_intensity is not None else 1.0)
    norm_exp = _norm_signed(exposure)
    norm_bright = _norm_signed(brightness)
    norm_contrast = _norm_mult(contrast, default=1.0)
    norm_highlights = _norm_signed(highlights)
    norm_shadows = _norm_signed(shadows)
    norm_warmth = _norm_signed(warmth)
    norm_temp = _norm_signed(color_temp)
    norm_tint_shift = _norm_signed(tint_shift)
    norm_vibrance = max(-1.0, min(1.5, float(vibrance or 0.0) / 100.0)) if abs(float(vibrance or 0.0)) > 1.5 else max(-1.0, min(1.5, float(vibrance or 0.0)))
    matte_val = _norm_unit(matte_fade)
    halation_val = _norm_unit(halation_glow)
    chroma_val = _norm_unit(chroma_split)
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

    raw_lm = float(lift_mids or 0.0)
    use_legacy_curve = 0.01 < raw_lm <= 0.55
    if abs(raw_lm) < 0.01 or abs(raw_lm - 1.0) < 0.01 or use_legacy_curve:
        gamma_val = 1.0
    elif 0.55 < raw_lm <= 2.0:
        gamma_val = max(0.5, min(2.0, raw_lm))
    else:
        gamma_val = max(0.5, min(2.0, 1.0 + raw_lm / 100.0))

    # 1. Preset Look & .cube 3D LUT with linear `preset_intensity` (0.0 -> 0%, 0.5 -> 50%, 1.0 -> 100%)
    look_chain: List[str] = []
    p = COLOR_PRESETS.get((preset or "none").lower(), COLOR_PRESETS["none"])
    if p["filter"]:
        look_chain.append(p["filter"])
    if lut_path:
        clean_lut = _escape_ffmpeg_filter_path(lut_path)
        look_chain.append(f"lut3d=file='{clean_lut}':interp=trilinear")

    if look_chain and p_strength > 0.01:
        wet_filter = ",".join(look_chain)
        if p_strength >= 0.99:
            filters.append(wet_filter)
        else:
            filters.append(
                f"split[_p_dry][_p_wet];"
                f"[_p_wet]{wet_filter}[_p_graded];"
                f"[_p_dry][_p_graded]blend=all_mode=normal:all_opacity={p_strength:.3f}"
            )

    # 2. Light, Exposure, Contrast, Saturation & Gamma (calibrated for unmistakable visual impact at ±40)
    total_bright = max(-0.65, min(0.65, norm_bright + norm_exp * 0.42))
    exp_gamma = max(0.55, min(1.75, gamma_val * (1.0 + norm_exp * 0.22)))
    eq_c = max(0.30, min(2.20, norm_contrast))
    eq_s = max(0.00, min(2.50, norm_sat * (1.0 - bw_factor)))

    if (
        abs(total_bright) > 0.002
        or abs(eq_c - 1.0) > 0.002
        or abs(eq_s - 1.0) > 0.002
        or abs(exp_gamma - 1.0) > 0.002
    ):
        filters.append(f"eq=brightness={total_bright:.3f}:contrast={eq_c:.3f}:saturation={eq_s:.3f}:gamma={exp_gamma:.3f}")

    if bw_factor >= 0.99 or eq_s <= 0.01:
        filters.append("hue=s=0")

    if use_legacy_curve:
        mid_val = 0.5 + (raw_lm * 0.20)
        filters.append(f"curves=m='0/0 0.5/{mid_val:.2f} 1/1'")

    # 2b. Highlights (-100..+100) & Shadows (-100..+100) sculpted tonal curves
    if abs(norm_highlights) > 0.01 or abs(norm_shadows) > 0.01:
        y0 = max(0.0, min(0.12, norm_shadows * 0.07)) if norm_shadows > 0 else 0.0
        y25 = max(0.05, min(0.45, 0.25 + norm_shadows * 0.16))
        y75 = max(0.55, min(0.96, 0.75 + norm_highlights * 0.17))
        y1 = max(0.84, min(1.0, 1.0 + norm_highlights * 0.10)) if norm_highlights < 0 else 1.0
        filters.append(f"curves=m='0/{y0:.3f} 0.25/{y25:.3f} 0.5/0.500 0.75/{y75:.3f} 1/{y1:.3f}'")

    # 3. Unified Temperature (-100..+100), Green<->Magenta Tint (-100..+100), Vibrance, Matte Fade
    comb_warm = max(-1.0, min(1.0, norm_warmth + norm_temp))
    if abs(comb_warm) > 0.005:
        red_shift = max(-0.55, min(0.55, comb_warm * 0.42))
        blue_shift = max(-0.55, min(0.55, -comb_warm * 0.42))
        g_warm = max(-0.20, min(0.20, comb_warm * 0.06))
        filters.append(
            f"colorbalance=rs={red_shift * 0.85:.2f}:gs={g_warm * 0.5:.2f}:bs={blue_shift * 0.85:.2f}:"
            f"rm={red_shift:.2f}:gm={g_warm:.2f}:bm={blue_shift:.2f}:"
            f"rh={red_shift * 0.85:.2f}:bh={blue_shift * 0.85:.2f}"
        )
    if abs(norm_tint_shift) > 0.005:
        g_shift = max(-0.45, min(0.45, -norm_tint_shift * 0.34))
        rb_shift = max(-0.45, min(0.45, norm_tint_shift * 0.20))
        filters.append(
            f"colorbalance=gs={g_shift:.2f}:gm={g_shift:.2f}:gh={g_shift * 0.8:.2f}:"
            f"rs={rb_shift:.2f}:bs={rb_shift:.2f}:rm={rb_shift:.2f}:bm={rb_shift:.2f}"
        )
    if abs(norm_vibrance) > 0.01:
        filters.append(f"vibrance=intensity={norm_vibrance * 1.10:.3f}")
    if matte_val > 0.01:
        black_lift = min(0.18, matte_val * 0.14)
        mid_y = round(0.5 + black_lift * 0.12, 3)
        filters.append(f"curves=m='0/{black_lift:.3f} 0.5/{mid_y:.3f} 1/1'")

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

    # 12. RGB Chromatic Split (Lens fringe FX)
    if chroma_val > 0.01:
        shift_px = max(1, min(14, int(round(chroma_val * 10.0))))
        filters.append(f"rgbashift=rh={shift_px}:bh={-shift_px}:rv=0:bv=0:edge=smear")

    # 13. Film Halation Glow (warm red/amber highlight bloom, or pure silver bloom in B&W modes)
    is_mono_grade = (
        ((preset or "none").lower() in ("bw_noir", "bw_16mm", "bw_16mm_noir") and p_strength >= 0.99)
        or bw_factor >= 0.99
        or eq_s <= 0.01
    )
    if halation_val > 0.01:
        h_sig = max(4.0, 6.0 + halation_val * 14.0)
        h_op = min(0.55, 0.14 + halation_val * 0.38)
        tint_bloom = "" if is_mono_grade else ",colorbalance=rh=0.45:gh=0.06:bh=-0.20"
        filters.append(
            f"split[_hal_orig][_hal_hi];"
            f"[_hal_hi]lutyuv=y='if(gt(val,180),val,0)':u=128:v=128,gblur=sigma={h_sig:.1f}"
            f"{tint_bloom}[_hal_bloom];"
            f"[_hal_orig][_hal_bloom]blend=all_mode=screen:all_opacity={h_op:.2f}"
        )

    if is_mono_grade:
        filters.append("hue=s=0")

    # 14. Cinema Letterbox Matte Bars (1.85:1 / 2.39:1)
    lb_raw = str(letterbox or "none").strip().lower()
    if lb_raw in ("1.85:1", "1.85", "2.39:1", "2.39"):
        tw = max(16, int(target_w or 1080))
        th = max(16, int(target_h or 1920))
        lb_ar = 1.85 if "1.85" in lb_raw else 2.39
        if tw >= th:
            vis_h = int(round(tw / lb_ar))
            bar_h = max(8, (th - vis_h) // 2) if vis_h < th else int(round(th * (0.075 if lb_ar < 2.0 else 0.135)))
        else:
            bar_h = int(round(th * (0.075 if lb_ar < 2.0 else 0.135)))
        bar_h = max(6, min(th // 4, bar_h))
        filters.append(f"drawbox=x=0:y=0:w=iw:h={bar_h}:color=black@1.0:t=fill")
        filters.append(f"drawbox=x=0:y=ih-{bar_h}:w=iw:h={bar_h}:color=black@1.0:t=fill")

    # 15. Bottom Fade (Dark box gradient behind caption safe zone)
    if fade_val > 0.01:
        alpha = min(0.80, fade_val * 0.70)
        filters.append(f"drawbox=y=ih*0.62:h=ih*0.38:color=black@{alpha:.2f}:t=fill")

    return ",".join(filters)


def build_depth_pop_foreground_filter(depth_pop: float = 0.0) -> str:
    """
    Apply realistic foreground subject micro-contrast, vibrancy, and crisp optical edge pop
    for Subject Depth Pop (f/1.8) mode. Returns empty string when disabled.
    """
    d = _norm_unit(depth_pop)
    if d <= 0.01:
        return ""
    c = min(1.30, 1.0 + 0.14 * d)
    s = min(1.25, 1.0 + 0.08 * d)
    sharp = min(2.0, 0.55 + 1.25 * d)
    return f"eq=contrast={c:.3f}:saturation={s:.3f},unsharp=5:5:{sharp:.2f}:5:5:0.0"


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


def generate_clip_preset_previews(
    video_path: str,
    project_id: int,
    clip_id: int,
    ref_timestamp: float,
    lut_path: Optional[str] = None,
    force: bool = False,
) -> Dict[str, Any]:
    """
    Extract one 160x90 sharpest reference frame from `video_path` at `ref_timestamp` and render
    real FFmpeg-graded thumbnail previews for all 9 STUDIO_COLOR_PRESETS (plus any uploaded `.cube` LUT)
    into `STORAGE_DIR/previews/presets/{project_id}_{clip_id}/`.
    Uses an ASCII-safe staging temp directory to avoid Windows Unicode/space path issues in FFmpeg.
    """
    import hashlib
    import json
    import os
    import shutil
    import subprocess
    import tempfile
    import time
    from pathlib import Path
    from app.config import settings
    from app.services.proxy import get_ffmpeg_cmd
    from app.services.audio_mastering import resolve_local_media_asset, stage_ascii_asset

    out_dir = Path(settings.STORAGE_DIR) / "previews" / "presets" / f"{int(project_id)}_{int(clip_id)}"
    out_dir.mkdir(parents=True, exist_ok=True)
    meta_file = out_dir / "meta.json"

    lut_abs = resolve_local_media_asset(str(lut_path)) if lut_path else None
    has_lut = bool(lut_abs and os.path.exists(lut_abs))
    sig_payload = json.dumps(
        {
            "video": str(video_path),
            "ts": round(float(ref_timestamp), 2),
            "lut": str(lut_abs or ""),
            "lut_mtime": os.path.getmtime(lut_abs) if has_lut else 0.0,
            "preset_ids": [p["id"] for p in STUDIO_COLOR_PRESETS],
        },
        sort_keys=True,
    )
    sig_hash = hashlib.md5(sig_payload.encode("utf-8")).hexdigest()[:12]

    def _build_response(version_tag: str, include_lut: bool) -> Dict[str, Any]:
        base_url = f"/media/previews/presets/{int(project_id)}_{int(clip_id)}"
        items = []
        for p in STUDIO_COLOR_PRESETS:
            pid = p["id"]
            jpg_file = out_dir / f"{pid}.jpg"
            items.append(
                {
                    "id": pid,
                    "name": p["name"],
                    "description": p["description"],
                    "thumbnail_url": f"{base_url}/{pid}.jpg?v={version_tag}" if jpg_file.exists() else None,
                    "values": p["values"],
                }
            )
        lut_item = None
        if include_lut and (out_dir / "custom_lut.jpg").exists():
            lut_item = {
                "id": "custom_lut",
                "name": Path(str(lut_abs)).stem if lut_abs else "Custom .cube LUT",
                "description": "Uploaded 3D .cube LUT applied to clip frame",
                "thumbnail_url": f"{base_url}/custom_lut.jpg?v={version_tag}",
                "lut_path": lut_path,
            }
        return {
            "project_id": int(project_id),
            "clip_id": int(clip_id),
            "ref_timestamp": round(float(ref_timestamp), 3),
            "presets": items,
            "lut_preview": lut_item,
            "cache_key": sig_hash,
        }

    # Check disk cache hit
    if not force and meta_file.exists():
        try:
            meta = json.loads(meta_file.read_text(encoding="utf-8"))
            if meta.get("sig_hash") == sig_hash:
                all_present = all((out_dir / f"{p['id']}.jpg").exists() for p in STUDIO_COLOR_PRESETS)
                if all_present and (not has_lut or (out_dir / "custom_lut.jpg").exists()):
                    return _build_response(sig_hash, has_lut)
        except Exception:
            pass

    ffmpeg = get_ffmpeg_cmd()
    work_dir = tempfile.mkdtemp(prefix="lost_heaven_pre_")
    try:
        src_ext = Path(video_path).suffix or ".mp4"
        staged_vid = os.path.join(work_dir, f"src{src_ext}")
        # Prefer running FFmpeg directly with input path (only reading), writing ASCII base frame into work_dir
        base_jpg = os.path.join(work_dir, "base_160x90.jpg")
        extract_cmd = [
            ffmpeg,
            "-y",
            "-ss",
            f"{max(0.0, float(ref_timestamp)):.3f}",
            "-i",
            str(video_path),
            "-frames:v",
            "1",
            "-vf",
            "scale=160:90:force_original_aspect_ratio=increase,crop=160:90,setsar=1",
            "-q:v",
            "3",
            base_jpg,
        ]
        res_ext = subprocess.run(extract_cmd, capture_output=True, text=True)
        if res_ext.returncode != 0 or not os.path.exists(base_jpg):
            # Retry once with staged ASCII video copy if source path had troublesome chars
            shutil.copy2(video_path, staged_vid)
            extract_cmd[5] = staged_vid
            res_ext2 = subprocess.run(extract_cmd, capture_output=True, text=True)
            if res_ext2.returncode != 0 or not os.path.exists(base_jpg):
                raise RuntimeError(f"Failed to extract reference frame for preset previews: {res_ext2.stderr[-300:]}")

        for p in STUDIO_COLOR_PRESETS:
            pid = p["id"]
            out_tmp = os.path.join(work_dir, f"{pid}.jpg")
            dest_jpg = out_dir / f"{pid}.jpg"
            vals = p.get("values") or {}
            vf_str = build_advanced_color_filter(
                preset=pid,
                preset_intensity=1.0,
                exposure=float(vals.get("exposure", 0.0) or 0.0),
                contrast=float(vals.get("contrast", 1.0) or 1.0),
                highlights=float(vals.get("highlights", 0.0) or 0.0),
                shadows=float(vals.get("shadows", 0.0) or 0.0),
                sharpness=float(vals.get("sharpness", 0.0) or 0.0),
                vignette=float(vals.get("vignette", 0.0) or 0.0),
                color_temp=float(vals.get("color_temp", 0.0) or 0.0),
                tint_shift=float(vals.get("tint_shift", 0.0) or 0.0),
                vibrance=float(vals.get("vibrance", 0.0) or 0.0),
                saturation=float(vals.get("saturation", 1.0) if vals.get("saturation") is not None else 1.0),
                matte_fade=float(vals.get("matte_fade", 0.0) or 0.0),
                halation_glow=float(vals.get("halation_glow", 0.0) or 0.0),
                target_w=160,
                target_h=90,
            )
            if vf_str:
                cmd = [
                    ffmpeg,
                    "-y",
                    "-i",
                    base_jpg,
                    "-vf",
                    vf_str,
                    "-frames:v",
                    "1",
                    "-q:v",
                    "4",
                    out_tmp,
                ]
                proc = subprocess.run(cmd, cwd=work_dir, capture_output=True, text=True)
                if proc.returncode == 0 and os.path.exists(out_tmp):
                    shutil.copy2(out_tmp, dest_jpg)
                else:
                    logger.warning(f"Preset preview render fallback for {pid}: {proc.stderr[-200:]}")
                    shutil.copy2(base_jpg, dest_jpg)
            else:
                shutil.copy2(base_jpg, dest_jpg)

        if has_lut and lut_abs:
            staged_lut_rel = stage_ascii_asset(lut_abs, Path(work_dir), target_stem="preview_lut")
            lut_vf = build_advanced_color_filter(
                preset="none",
                preset_intensity=1.0,
                lut_path=staged_lut_rel,
                target_w=160,
                target_h=90,
            )
            out_lut_tmp = os.path.join(work_dir, "custom_lut.jpg")
            dest_lut = out_dir / "custom_lut.jpg"
            if lut_vf:
                proc_lut = subprocess.run(
                    [ffmpeg, "-y", "-i", base_jpg, "-vf", lut_vf, "-frames:v", "1", "-q:v", "4", out_lut_tmp],
                    cwd=work_dir,
                    capture_output=True,
                    text=True,
                )
                if proc_lut.returncode == 0 and os.path.exists(out_lut_tmp):
                    shutil.copy2(out_lut_tmp, dest_lut)
                else:
                    logger.warning(f"LUT preview render failed: {proc_lut.stderr[-200:]}")
                    shutil.copy2(base_jpg, dest_lut)

        ver_tag = f"{sig_hash}_{int(time.time())}"
        meta_file.write_text(json.dumps({"sig_hash": sig_hash, "version": ver_tag}), encoding="utf-8")
        return _build_response(ver_tag, has_lut)
    finally:
        shutil.rmtree(work_dir, ignore_errors=True)

