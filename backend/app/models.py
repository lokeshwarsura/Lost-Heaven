import json
from datetime import datetime
from typing import Optional, List, Dict, Any
from pydantic import BaseModel, Field
from sqlmodel import SQLModel, Field as SQLField

# -------------------------------------------------------------
# Database Models
# -------------------------------------------------------------

class Project(SQLModel, table=True):
    __tablename__ = "projects"

    id: Optional[int] = SQLField(default=None, primary_key=True)
    title: str = SQLField(index=True)
    source_type: str = SQLField(default="local") # "local" or "youtube"
    source_filename: Optional[str] = SQLField(default=None)
    source_path: str
    proxy_path: Optional[str] = SQLField(default=None)
    duration: float = SQLField(default=0.0)
    resolution_w: int = SQLField(default=0)
    resolution_h: int = SQLField(default=0)
    fps: float = SQLField(default=30.0)
    status: str = SQLField(default="created") # created, proxy_ready, analyzing, analyzed, error
    error_message: Optional[str] = SQLField(default=None)

    # Pre-analysis AI Controls
    default_aspect_ratio: str = SQLField(default="9:16") # 9:16, 4:5, 1:1, 4:3, 16:9, custom
    duration_preset: str = SQLField(default="Auto") # Auto, 15s, 30s, 45s, 60s, 90s, 30-58s
    clip_count: int = SQLField(default=5) # 3, 5, 8, 10 or exact custom
    clip_length_target: str = SQLField(default="Auto") # Auto, 15s, 30s, 45s, 60s, 90s
    caption_language: str = SQLField(default="As spoken") # As spoken, Hinglish, English, Hindi, Spanish, French, German, Arabic, Portuguese, Japanese
    hardware_mode: str = SQLField(default="gpu") # gpu (Graphics card) or cpu (Processor only)
    default_framing: str = SQLField(default="smart_auto") # smart_auto, auto_track, gameplay_webcam_pip, fit_blur
    ai_mode: Optional[str] = SQLField(default=None) # Explicit per-project "gemini" or "offline"
    topic_focus: Optional[str] = SQLField(default=None) # Optional creative guidance for Gemini
    multimodal_analysis_json: str = SQLField(default="") # Persistent audio+video multimodal timeline & summary
    tags_json: str = SQLField(default="[]") # Custom Vault project tags JSON list
    podcast_mode: bool = SQLField(default=False) # Audio-only / podcast waveform ingestion mode

    created_at: str = SQLField(default_factory=lambda: datetime.utcnow().isoformat())

    def get_tags(self) -> List[str]:
        try:
            raw = (self.tags_json or "[]").strip()
            data = json.loads(raw)
            if isinstance(data, list):
                return [str(t).strip() for t in data if str(t).strip()]
        except Exception:
            pass
        return []

    def set_tags(self, tags: Optional[List[str]]):
        if not tags:
            self.tags_json = "[]"
        else:
            cleaned = []
            seen = set()
            for t in tags:
                s = str(t).strip()
                if s and s.lower() not in seen:
                    seen.add(s.lower())
                    cleaned.append(s[:32])
            self.tags_json = json.dumps(cleaned)

    def get_multimodal_analysis(self) -> Optional[Dict[str, Any]]:
        try:
            raw = (self.multimodal_analysis_json or "").strip()
            if not raw:
                return None
            data = json.loads(raw)
            return data if isinstance(data, dict) else None
        except Exception:
            return None

    def set_multimodal_analysis(self, payload: Optional[Dict[str, Any]]):
        if payload is None:
            self.multimodal_analysis_json = ""
        elif isinstance(payload, dict):
            self.multimodal_analysis_json = json.dumps(payload)


class BrandPreset(SQLModel, table=True):
    __tablename__ = "brand_presets"

    id: Optional[int] = SQLField(default=None, primary_key=True)
    name: str = SQLField(index=True)
    caption_style_name: Optional[str] = SQLField(default="Cinematic")
    watermark_url: Optional[str] = SQLField(default=None)
    watermark_corner: str = SQLField(default="top_right") # top_left, top_right, bottom_left, bottom_right
    watermark_opacity: float = SQLField(default=0.85)
    watermark_size: float = SQLField(default=0.15)
    outro_url: Optional[str] = SQLField(default=None)
    bgm_url: Optional[str] = SQLField(default=None)
    bgm_volume: float = SQLField(default=0.20)
    aspect_ratio: str = SQLField(default="9:16")
    safe_zone_guide: str = SQLField(default="TikTok") # No guide, TikTok, Instagram, YT Shorts, X, All
    created_at: str = SQLField(default_factory=lambda: datetime.utcnow().isoformat())


class CaptionStylePreset(SQLModel, table=True):
    __tablename__ = "caption_style_presets"

    id: Optional[int] = SQLField(default=None, primary_key=True)
    name: str = SQLField(index=True)
    font_family: str = SQLField(default="Montserrat")
    font_size_percent: int = SQLField(default=100)
    bold: bool = SQLField(default=True)
    italic: bool = SQLField(default=False)
    uppercase: bool = SQLField(default=True)
    text_align: str = SQLField(default="Centre") # Left, Centre, Right
    base_color: str = SQLField(default="#FFFFFF")
    active_color: str = SQLField(default="#C6F32F") # ClipShlip neon lime
    hold_back_alpha: float = SQLField(default=1.0) # 0.0 to 1.0 (opacity of inactive words)
    highlight_shape: str = SQLField(default="Colour only") # Colour only, Filled box, Underline
    animation_type: str = SQLField(default="Karaoke pop")
    position_grid: str = SQLField(default="bottom_center") # 3x3 position
    position_x: float = SQLField(default=50.0) # % across
    position_y: float = SQLField(default=78.0) # % down
    outline_width: float = SQLField(default=4.0)
    shadow_depth: float = SQLField(default=3.0)
    glow_intensity: float = SQLField(default=0.0)
    letter_spacing: float = SQLField(default=0.0)
    timing_offset: float = SQLField(default=0.0)
    emoji_enabled: bool = SQLField(default=False)
    highlight_power_words: bool = SQLField(default=False)
    how_phrase_arrives: str = SQLField(default="All at once") # All at once, Word by word
    created_at: str = SQLField(default_factory=lambda: datetime.utcnow().isoformat())


class ViralClip(SQLModel, table=True):
    __tablename__ = "viral_clips"

    id: Optional[int] = SQLField(default=None, primary_key=True)
    project_id: int = SQLField(index=True, foreign_key="projects.id")

    # Time boundaries in seconds
    start_time: float
    end_time: float
    duration: float

    # Virality metrics
    virality_score: int = SQLField(default=0)
    hook_score: int = SQLField(default=0)
    retention_score: int = SQLField(default=0)
    emotional_score: int = SQLField(default=0)
    standalone_score: int = SQLField(default=0)
    viral_rationale: str = SQLField(default="")

    # Hook engine & Retention FX
    hook_headline: str = SQLField(default="")
    show_hook_banner: bool = SQLField(default=True)
    cold_open_quote: str = SQLField(default="")
    use_cold_open: bool = SQLField(default=False)
    auto_trim_dead_air: bool = SQLField(default=False)
    dynamic_punch_zoom: bool = SQLField(default=False)
    punch_zoom_intensity: float = SQLField(default=1.14) # 1.08 subtle, 1.14 standard, 1.22 aggressive

    # Multi-Aspect Ratio & Framing Controls
    aspect_ratio: str = SQLField(default="9:16") # 9:16, 4:5, 1:1, 4:3, 16:9, custom
    custom_width: int = SQLField(default=1080)
    custom_height: int = SQLField(default=1920)
    frame_layout: str = SQLField(default="auto_track") # auto_track, webcam_gameplay_split, webcam_gameplay_pip, split_podcast, fit_blur
    framing_template: Optional[str] = SQLField(default=None)
    webcam_corner: str = SQLField(default="auto") # auto, top_left, top_right, bottom_left, bottom_right
    webcam_split_ratio: float = SQLField(default=0.40) # 0.25 to 0.50
    source_webcam_region_json: str = SQLField(default="")
    visual_map_json: str = SQLField(default="")
    framing_override_json: str = SQLField(default="")
    best_frames_json: str = SQLField(default="[]")
    camera_motion: str = SQLField(default="steady") # steady, dolly_in, dolly_out, beat_punch
    whip_pan_cuts: bool = SQLField(default=False)
    pip_border_color: str = SQLField(default="#F59E0B") # ClipShlip amber
    zoom: float = SQLField(default=1.0) # 1.0 to 1.8
    pan_x_offset: float = SQLField(default=0.0) # -0.40 to +0.40

    # Step 2: Pro Captions & Typography
    burn_captions: bool = SQLField(default=True)
    timing_offset: float = SQLField(default=0.0) # -1.00s to +1.00s
    caption_style: str = SQLField(default="Karaoke pop") # 12 styles
    caption_font: str = SQLField(default="Montserrat")
    caption_animation: str = SQLField(default="Karaoke") # Plays once vs whole line
    base_color: str = SQLField(default="#FFFFFF")
    active_color: str = SQLField(default="#C6F32F")
    outline_color: str = SQLField(default="#000000")
    font_size: int = SQLField(default=64)
    font_size_percent: int = SQLField(default=100) # 50% to 160%
    bold: bool = SQLField(default=True)
    italic: bool = SQLField(default=False)
    all_caps: bool = SQLField(default=True)
    text_align: str = SQLField(default="Centre") # Left, Centre, Right
    position_grid: str = SQLField(default="bottom_center") # 3x3 grid
    position_x: float = SQLField(default=50.0)
    position_y: float = SQLField(default=78.0)
    hold_back_alpha: float = SQLField(default=1.0) # 0.0 to 1.0
    highlight_shape: str = SQLField(default="Colour only") # Colour only, Filled box, Underline
    emoji_enabled: bool = SQLField(default=False)
    highlight_power_words: bool = SQLField(default=False)
    how_phrase_arrives: str = SQLField(default="All at once") # All at once, Word by word
    outline_width: float = SQLField(default=4.0) # 0-12
    shadow_depth: float = SQLField(default=3.0) # 0-10
    glow_intensity: float = SQLField(default=0.0) # 0-10
    letter_spacing: float = SQLField(default=0.0) # 0-8
    clean_filler_words: bool = SQLField(default=False)
    tighten_pacing: bool = SQLField(default=False)
    silence_threshold: float = SQLField(default=0.45)
    censor_profanity: bool = SQLField(default=False)

    # Step 3: Color Grading & Visual FX
    clip_size: str = SQLField(default="1080p") # Auto, 1080p, 720p, 540p, 480p
    color_preset: str = SQLField(default="none")
    exposure: float = SQLField(default=0.0) # -0.50 to +0.50
    brightness: float = SQLField(default=0.0) # -0.50 to +0.50
    contrast: float = SQLField(default=1.0) # 0.50 to 1.50
    lift_mids: float = SQLField(default=0.0) # 0.0 to 1.0
    warmth: float = SQLField(default=0.0) # -0.50 to +0.50
    saturation: float = SQLField(default=1.0) # 0.00 to 2.00
    black_and_white: float = SQLField(default=0.0) # 0.0 to 1.0
    vintage: float = SQLField(default=0.0) # 0.0 to 1.0
    tint_color: str = SQLField(default="none") # none, red, blue, green, yellow, or hex
    tint_strength: float = SQLField(default=0.0) # 0.0 to 1.0
    glow: float = SQLField(default=0.0) # 0.0 to 1.0
    smooth: float = SQLField(default=0.0) # 0.0 to 1.0
    sharpness: float = SQLField(default=0.0) # 0.0 to 1.0
    blur: float = SQLField(default=0.0) # 0.0 to 1.0
    film_grain: float = SQLField(default=0.0) # 0.0 to 1.0
    vignette: float = SQLField(default=0.0) # 0.0 to 1.0
    bottom_fade: float = SQLField(default=0.0) # 0.0 to 1.0

    # Step 4: Branding, Outro & Audio
    watermark_path: Optional[str] = SQLField(default=None)
    watermark_corner: str = SQLField(default="top_right")
    watermark_opacity: float = SQLField(default=0.85)
    watermark_size: float = SQLField(default=0.15)
    outro_path: Optional[str] = SQLField(default=None)
    bgm_path: Optional[str] = SQLField(default=None)
    bgm_volume: float = SQLField(default=0.20)
    audio_volume: float = SQLField(default=1.0)
    normalize_audio: bool = SQLField(default=True)
    vocal_clarity: bool = SQLField(default=False)
    voice_isolation: bool = SQLField(default=False)
    auto_sfx: bool = SQLField(default=False)
    retention_bar_enabled: bool = SQLField(default=False)
    retention_bar_color: str = SQLField(default="#C6F32F")
    outro_cta_enabled: bool = SQLField(default=False)
    outro_cta_text: str = SQLField(default="Subscribe for More")
    broll_json: str = SQLField(default="[]")
    broll_layout: str = SQLField(default="fullscreen") # fullscreen, reaction_pip
    speakers_json: str = SQLField(default="{}")
    speaker_lower_thirds: bool = SQLField(default=False)
    face_safe_captions: bool = SQLField(default=True)
    callout_cards_json: str = SQLField(default="[]")
    auto_callouts: bool = SQLField(default=False)
    bilingual_enabled: bool = SQLField(default=False)
    secondary_language: str = SQLField(default="")
    dubbing_enabled: bool = SQLField(default=False)
    dubbing_language: str = SQLField(default="Spanish")
    dubbing_voice: str = SQLField(default="default")
    dubbing_bed_volume: float = SQLField(default=0.15)
    dubbed_audio_path: Optional[str] = SQLField(default=None)
    backdrop_mode: str = SQLField(default="none")
    backdrop_blur_intensity: float = SQLField(default=18.0)
    backdrop_color: str = SQLField(default="#18181B")
    depth_pop: float = SQLField(default=0.0)
    privacy_blur_json: str = SQLField(default="[]")
    beat_sync_enabled: bool = SQLField(default=False)
    spectrum_enabled: bool = SQLField(default=False)
    spectrum_position: str = SQLField(default="bottom")
    spectrum_color: str = SQLField(default="#C6F32F")
    chapters_json: str = SQLField(default="[]")
    chapters_enabled: bool = SQLField(default=False)
    handle_watermark_enabled: bool = SQLField(default=False)
    handle_text: str = SQLField(default="")
    handle_platform_icon: str = SQLField(default="none")
    handle_corner: str = SQLField(default="bottom_right")
    handle_opacity: float = SQLField(default=0.65)
    handle_display_mode: str = SQLField(default="always")
    hardware_mode: str = SQLField(default="gpu") # gpu or cpu

    # Word-level transcript JSON & Multi-Segment Blade Cuts
    transcript_json: Optional[str] = SQLField(default="[]")
    keep_segments_json: str = SQLField(default="[]")
    merge_crossfade_sec: float = SQLField(default=0.03)
    options_json: str = SQLField(default="{}")

    # Publishing metadata
    youtube_titles: str = SQLField(default="[]") # JSON list of 3 titles
    selected_title: str = SQLField(default="")
    youtube_description: str = SQLField(default="")
    hashtags: str = SQLField(default="")
    pinned_comment: str = SQLField(default="")
    analysis_source: Optional[str] = SQLField(default="gemini") # "gemini", "offline", "offline_fallback", "ollama"
    privacy_status: str = SQLField(default="private") # private, unlisted, public

    # Render pipeline states
    status: str = SQLField(default="draft") # draft, rendering, ready, failed
    render_status: str = SQLField(default="idle") # idle, rendering, completed, failed
    render_progress: int = SQLField(default=0)
    render_message: Optional[str] = SQLField(default=None)
    rendered_path: Optional[str] = SQLField(default=None)
    rendered_video_path: Optional[str] = SQLField(default=None)

    # YouTube publish result
    youtube_video_id: Optional[str] = SQLField(default=None)
    youtube_url: Optional[str] = SQLField(default=None)

    created_at: str = SQLField(default_factory=lambda: datetime.utcnow().isoformat())

    def get_titles(self) -> List[str]:
        try:
            return json.loads(self.youtube_titles)
        except Exception:
            return []

    def set_titles(self, titles: List[str]):
        self.youtube_titles = json.dumps(titles)

    def get_transcript(self) -> List[Dict[str, Any]]:
        try:
            return json.loads(self.transcript_json or "[]")
        except Exception:
            return []

    def set_transcript(self, words: List[Dict[str, Any]]):
        self.transcript_json = json.dumps(words)

    def get_keep_segments(self) -> List[List[float]]:
        """Return [[start, end], ...] kept intervals in master-video seconds."""
        try:
            segs = json.loads(self.keep_segments_json or "[]")
            cleaned: List[List[float]] = []
            if isinstance(segs, list):
                for item in segs:
                    if isinstance(item, (list, tuple)) and len(item) >= 2:
                        s, e = float(item[0]), float(item[1])
                        if e > s + 0.05:
                            cleaned.append([round(max(0.0, s), 3), round(e, 3)])
            if cleaned:
                return cleaned
        except Exception:
            pass
        return [[round(max(0.0, float(self.start_time)), 3), round(max(float(self.start_time) + 0.2, float(self.end_time)), 3)]]

    def set_keep_segments(self, segs: List[List[float]]):
        """Persist non-empty kept segments and keep start_time, end_time, and duration in sync."""
        cleaned: List[List[float]] = []
        for item in segs or []:
            if isinstance(item, (list, tuple)) and len(item) >= 2:
                s, e = max(0.0, float(item[0])), max(0.0, float(item[1]))
                if e > s + 0.1:
                    cleaned.append([round(s, 3), round(e, 3)])
        if not cleaned:
            cleaned = [[round(max(0.0, float(self.start_time)), 3), round(max(float(self.start_time) + 0.5, float(self.end_time)), 3)]]
        self.keep_segments_json = json.dumps(cleaned)
        self.start_time = min(pair[0] for pair in cleaned)
        self.end_time = max(pair[1] for pair in cleaned)
        self.duration = round(sum(pair[1] - pair[0] for pair in cleaned), 2)

    def get_speakers(self) -> Dict[str, Dict[str, Any]]:
        try:
            data = json.loads(self.speakers_json or "{}")
            return data if isinstance(data, dict) else {}
        except Exception:
            return {}

    def set_speakers(self, speakers: Dict[str, Any]):
        self.speakers_json = json.dumps(speakers if isinstance(speakers, dict) else {})

    def get_callout_cards(self) -> List[Dict[str, Any]]:
        try:
            data = json.loads(self.callout_cards_json or "[]")
            return data if isinstance(data, list) else []
        except Exception:
            return []

    def set_callout_cards(self, cards: List[Dict[str, Any]]):
        self.callout_cards_json = json.dumps(cards if isinstance(cards, list) else [])

    def get_privacy_blur_boxes(self) -> List[Dict[str, Any]]:
        try:
            data = json.loads(self.privacy_blur_json or "[]")
            return data if isinstance(data, list) else []
        except Exception:
            return []

    def set_privacy_blur_boxes(self, boxes: List[Dict[str, Any]]):
        self.privacy_blur_json = json.dumps(boxes[:3] if isinstance(boxes, list) else [])

    def get_chapters(self) -> List[Dict[str, Any]]:
        try:
            data = json.loads(self.chapters_json or "[]")
            return data if isinstance(data, list) else []
        except Exception:
            return []

    def set_chapters(self, chapters: List[Dict[str, Any]]):
        self.chapters_json = json.dumps(chapters[:8] if isinstance(chapters, list) else [])

    def get_source_webcam_region(self) -> Optional[Dict[str, Any]]:
        try:
            raw = (self.source_webcam_region_json or "").strip()
            if not raw:
                return None
            data = json.loads(raw)
            return data if isinstance(data, dict) else None
        except Exception:
            return None

    def set_source_webcam_region(self, region: Optional[Dict[str, Any]]):
        if region is None:
            self.source_webcam_region_json = ""
        elif isinstance(region, dict):
            self.source_webcam_region_json = json.dumps(region)

    def get_visual_map(self) -> Optional[Dict[str, Any]]:
        try:
            raw = (self.visual_map_json or "").strip()
            if not raw:
                return None
            data = json.loads(raw)
            return data if isinstance(data, dict) else None
        except Exception:
            return None

    def set_visual_map(self, visual_map: Optional[Dict[str, Any]]):
        if visual_map is None:
            self.visual_map_json = ""
        elif isinstance(visual_map, dict):
            self.visual_map_json = json.dumps(visual_map)

    def get_framing_override(self) -> Optional[Dict[str, Any]]:
        try:
            raw = (self.framing_override_json or "").strip()
            if not raw:
                return None
            data = json.loads(raw)
            return data if isinstance(data, dict) else None
        except Exception:
            return None

    def set_framing_override(self, override: Optional[Dict[str, Any]]):
        if override is None:
            self.framing_override_json = ""
        elif isinstance(override, dict):
            self.framing_override_json = json.dumps(override)

    def get_best_frames(self) -> List[Dict[str, Any]]:
        try:
            raw = (self.best_frames_json or "[]").strip()
            if not raw:
                return []
            data = json.loads(raw)
            return data if isinstance(data, list) else []
        except Exception:
            return []

    def set_best_frames(self, frames: Optional[List[Dict[str, Any]]]):
        if not frames:
            self.best_frames_json = "[]"
        elif isinstance(frames, list):
            self.best_frames_json = json.dumps(frames)

    def get_options(self) -> Dict[str, Any]:
        try:
            raw = getattr(self, "options_json", "{}") or "{}"
            data = json.loads(raw)
            return data if isinstance(data, dict) else {}
        except Exception:
            return {}

    def set_options(self, patch: Optional[Dict[str, Any]], replace: bool = False):
        cur = {} if replace else self.get_options()
        if isinstance(patch, dict):
            cur.update(patch)
        self.options_json = json.dumps(cur)


# -------------------------------------------------------------
# Gemini Structured Output Schemas
# -------------------------------------------------------------

class GeminiClipItem(BaseModel):
    start_time: str = Field(description="Clip start time formatted as HH:MM:SS or MM:SS")
    end_time: str = Field(description="Clip end time formatted as HH:MM:SS or MM:SS")
    start_seconds: Optional[float] = Field(default=None, description="Start boundary in seconds")
    end_seconds: Optional[float] = Field(default=None, description="End boundary in seconds")
    virality_score: int = Field(ge=0, le=100, description="Overall virality score 0-100")
    hook_score: int = Field(ge=0, le=100, description="Strength of opening 3 seconds")
    retention_score: int = Field(ge=0, le=100, description="Narrative pacing and payoff")
    emotional_score: int = Field(ge=0, le=100, description="Humor, surprise, urgency or insight")
    standalone_score: int = Field(ge=0, le=100, description="Clarity without watching full video")
    viral_rationale: str = Field(description="1-2 sentences explaining why this moment is viral")
    hook_headline: str = Field(description="Punchy 4-6 word top banner text summarizing the hook")
    cold_open_quote: str = Field(description="Exact 1-sentence climax quote inside the clip for cold open")
    youtube_titles: List[str] = Field(description="3 A/B-testable YouTube titles under 60 chars")
    youtube_description: str = Field(description="SEO description with 3-5 relevant hashtags")
    pinned_comment: str = Field(description="1 engagement question for the YouTube comment section")


class GeminiAnalysisResponse(BaseModel):
    clips: List[GeminiClipItem] = Field(description="Ranked extracted viral clips")


# -------------------------------------------------------------
# API Request / Response DTOs
# -------------------------------------------------------------

class SystemInfoResponse(BaseModel):
    cpu_model: str
    cpu_cores: int
    ram_gb: float
    gpu_name: Optional[str] = None
    has_cuda: bool = False
    active_encoder: str = "libx264"
    whisper_model: str = "large-v3-turbo"
    gemini_status: str = "active"


class PreviewFrameRequest(BaseModel):
    clip_id: Optional[int] = None
    project_id: Optional[int] = None
    timestamp: float = 0.0

    # Layout
    aspect_ratio: str = "9:16"
    custom_width: int = 1080
    custom_height: int = 1920
    frame_layout: str = "auto_track"
    framing_template: Optional[str] = None
    framing_user_locked: Optional[bool] = None
    webcam_corner: str = "top_left"
    source_corner: Optional[str] = "auto"
    webcam_split_ratio: float = 0.40
    source_webcam_region: Optional[Dict[str, Any]] = None
    framing_override: Optional[Dict[str, Any]] = None
    frame_crop_override: Optional[Any] = None
    clear_framing_override: Optional[bool] = None
    camera_motion: str = "steady"
    whip_pan_cuts: bool = False
    pip_border_color: str = "#F59E0B"
    zoom: float = 1.0
    pan_x_offset: float = 0.0
    pan_x: Optional[float] = None
    clip_rel_ts: Optional[float] = None
    clip_size: str = "1080p"
    export_resolution: Optional[str] = None
    watermark_path: Optional[str] = None
    watermark_corner: str = "top_right"
    watermark_opacity: float = 0.85
    watermark_size: float = 0.15

    # Grading & Lens/Motion/Audio FX
    color_preset: str = "none"
    preset_intensity: Optional[float] = None
    bypass_color: bool = False
    exposure: float = 0.0
    brightness: float = 0.0
    contrast: float = 1.0
    lift_mids: float = 0.0
    highlights: Optional[float] = None
    shadows: Optional[float] = None
    warmth: float = 0.0
    color_temp: Optional[float] = None
    tint_shift: Optional[float] = None
    vibrance: Optional[float] = None
    matte_fade: Optional[float] = None
    halation_glow: Optional[float] = None
    lut_path: Optional[str] = None
    chroma_split: Optional[float] = None
    letterbox: Optional[str] = None
    export_fps: Optional[str] = None
    speed_ramp: Optional[float] = None
    voice_eq_preset: Optional[str] = None
    de_esser: Optional[bool] = None
    loudness_target: Optional[str] = None
    voice_enhancer: Optional[bool] = None
    horizon_tilt: Optional[float] = None
    saturation: float = 1.0
    black_and_white: float = 0.0
    vintage: float = 0.0
    tint_color: str = "none"
    color_tint: Optional[str] = None
    tint_strength: float = 0.0
    glow: float = 0.0
    smooth: float = 0.0
    sharpness: float = 0.0
    blur: float = 0.0
    film_grain: float = 0.0
    vignette: float = 0.0
    bottom_fade: float = 0.0

    # Subtitles
    burn_captions: bool = True
    timing_offset: float = 0.0
    caption_style: str = "Karaoke pop"
    style_preset: Optional[str] = None
    caption_font: str = "Montserrat"
    font_family: Optional[str] = None
    caption_animation: str = "Karaoke"
    animation: Optional[str] = None
    font_size_percent: int = 100
    font_size_pct: Optional[int] = None
    bold: bool = True
    italic: bool = False
    all_caps: bool = True
    uppercase: Optional[bool] = None
    text_align: str = "Centre"
    alignment: Optional[str] = None
    position_grid: str = "bottom_center"
    base_color: str = "#FFFFFF"
    active_color: str = "#C6F32F"
    highlight_color: Optional[str] = None
    outline_color: str = "#000000"
    pill_color: Optional[str] = None
    pacing_density: Optional[str] = None
    text_case: Optional[str] = None
    hold_back_alpha: float = 1.0
    hold_back_opacity: Optional[float] = None
    highlight_shape: str = "Colour only"
    position_x: float = 50.0
    pos_x: Optional[float] = None
    position_y: float = 78.0
    pos_y: Optional[float] = None
    caption_x: Optional[float] = None
    caption_y: Optional[float] = None
    caption_custom_position: Optional[bool] = None
    caption_rotation: Optional[float] = None
    caption_max_width: Optional[float] = None
    outline_width: float = 4.0
    outline: Optional[float] = None
    shadow_depth: float = 3.0
    shadow: Optional[float] = None
    glow_intensity: float = 0.0
    letter_spacing: float = 0.0
    emoji_enabled: bool = False
    auto_emoji: Optional[bool] = None
    highlight_power_words: bool = False
    how_phrase_arrives: str = "All at once"
    line_mode: Optional[str] = None
    word_by_word: Optional[bool] = None
    clean_filler_words: bool = False
    censor_profanity: bool = False
    show_hook_banner: Optional[bool] = None
    show_hook: Optional[bool] = None
    hook_banner_enabled: Optional[bool] = None
    enable_hook_banner: Optional[bool] = None
    top_hook_banner: Optional[bool] = None
    hook_headline: str = ""
    hook_text: Optional[str] = None
    safe_zone: Optional[str] = None
    safe_zone_guide: Optional[str] = None
    retention_bar_enabled: bool = False
    retention_bar_color: str = "#C6F32F"
    outro_cta_enabled: bool = False
    outro_cta_text: str = "Subscribe for More"
    outro_cta_headline: Optional[str] = None
    outro_cta_handle: Optional[str] = None
    outro_logo_path: Optional[str] = None
    words: Optional[List[Dict[str, Any]]] = None
    keep_segments: Optional[List[List[float]]] = None
    merge_crossfade_sec: Optional[float] = None
    speakers: Optional[Dict[str, Any]] = None
    speaker_lower_thirds: bool = False
    face_safe_captions: bool = True
    callout_cards: Optional[List[Dict[str, Any]]] = None
    auto_callouts: bool = False
    bilingual_enabled: bool = False
    secondary_language: str = ""
    backdrop_mode: str = "none"
    backdrop_blur_intensity: float = 18.0
    backdrop_color: str = "#18181B"
    depth_pop: float = 0.0
    privacy_blur_boxes: Optional[List[Dict[str, Any]]] = None
    beat_sync_enabled: bool = False
    spectrum_enabled: bool = False
    spectrum_position: str = "bottom"
    spectrum_color: str = "#C6F32F"
    chapters_enabled: bool = False
    chapters: Optional[List[Dict[str, Any]]] = None
    handle_watermark_enabled: bool = False
    handle_text: str = ""
    handle_platform_icon: str = "none"
    handle_corner: str = "bottom_right"
    handle_opacity: float = 0.65
    handle_display_mode: str = "always"
    # Retention FX canonical + alias flags
    use_cold_open: Optional[bool] = None
    cold_open: Optional[bool] = None
    cold_open_climax: Optional[bool] = None
    cold_open_quote: Optional[str] = None
    auto_trim_dead_air: Optional[bool] = None
    trim_dead_air: Optional[bool] = None
    silence_threshold: Optional[float] = None
    dynamic_punch_zoom: Optional[bool] = None
    punch_zoom: Optional[bool] = None
    punch_zoom_fx: Optional[bool] = None
    punch_zoom_intensity: Optional[float] = None
    options: Optional[Dict[str, Any]] = None

    def resolved_retention_flags(self, clip: Optional[Any] = None) -> Dict[str, Any]:
        co = (
            bool(self.cold_open_climax)
            if self.cold_open_climax is not None
            else (
                bool(self.cold_open)
                if self.cold_open is not None
                else (
                    bool(self.use_cold_open)
                    if self.use_cold_open is not None
                    else bool(getattr(clip, "use_cold_open", False))
                )
            )
        )
        tda = (
            bool(self.trim_dead_air)
            if self.trim_dead_air is not None
            else (
                bool(self.auto_trim_dead_air)
                if self.auto_trim_dead_air is not None
                else bool(getattr(clip, "auto_trim_dead_air", False))
            )
        )
        pz = (
            bool(self.punch_zoom_fx)
            if self.punch_zoom_fx is not None
            else (
                bool(self.punch_zoom)
                if self.punch_zoom is not None
                else (
                    bool(self.dynamic_punch_zoom)
                    if self.dynamic_punch_zoom is not None
                    else bool(getattr(clip, "dynamic_punch_zoom", False))
                )
            )
        )
        pzi_raw = (
            self.punch_zoom_intensity
            if self.punch_zoom_intensity is not None
            else getattr(clip, "punch_zoom_intensity", 1.14)
        )
        co_quote = (
            self.cold_open_quote
            if self.cold_open_quote is not None
            else str(getattr(clip, "cold_open_quote", "") or "")
        )
        sil_t = (
            self.silence_threshold
            if self.silence_threshold is not None
            else float(getattr(clip, "silence_threshold", 0.45) or 0.45)
        )
        return {
            "use_cold_open": co,
            "cold_open_quote": co_quote,
            "auto_trim_dead_air": tda,
            "silence_threshold": float(sil_t),
            "dynamic_punch_zoom": pz,
            "punch_zoom_intensity": max(1.04, min(1.35, float(pzi_raw or 1.14))),
        }


class PreviewFrameResponse(BaseModel):
    frame_url: str
    render_time_ms: int
    timestamp: float
    timeline_fx_summary: Optional[Dict[str, Any]] = None


class YouTubeImportRequest(BaseModel):
    url: str
    aspect_ratio: Optional[str] = "9:16"
    duration_preset: Optional[str] = "Auto"
    clip_duration: Optional[str] = None
    clip_count: Optional[int] = 5
    num_clips: Optional[int] = None
    topic_focus: Optional[str] = None
    guidance: Optional[str] = None
    caption_language: Optional[str] = "As spoken"
    language: Optional[str] = None
    hardware_mode: Optional[str] = "gpu"
    default_framing: Optional[str] = "smart_auto"
    framing_template: Optional[str] = None
    ai_mode: Optional[str] = None
    batch_queue: Optional[bool] = False
    start_time: Optional[str] = None
    end_time: Optional[str] = None
    start_seconds: Optional[float] = None
    end_seconds: Optional[float] = None
    podcast_mode: Optional[bool] = False


class UpdateClipRequest(BaseModel):
    start_time: Optional[float] = None
    end_time: Optional[float] = None
    hook_headline: Optional[str] = None
    hook_text: Optional[str] = None
    show_hook_banner: Optional[bool] = None
    show_hook: Optional[bool] = None
    hook_banner_enabled: Optional[bool] = None
    enable_hook_banner: Optional[bool] = None
    top_hook_banner: Optional[bool] = None
    safe_zone: Optional[str] = None
    safe_zone_guide: Optional[str] = None
    cold_open_quote: Optional[str] = None
    use_cold_open: Optional[bool] = None
    cold_open: Optional[bool] = None
    cold_open_climax: Optional[bool] = None
    auto_trim_dead_air: Optional[bool] = None
    trim_dead_air: Optional[bool] = None
    dynamic_punch_zoom: Optional[bool] = None
    punch_zoom: Optional[bool] = None
    punch_zoom_fx: Optional[bool] = None
    punch_zoom_intensity: Optional[float] = None
    options: Optional[Dict[str, Any]] = None

    # Step 1: Layout & Framing
    aspect_ratio: Optional[str] = None
    custom_width: Optional[int] = None
    custom_height: Optional[int] = None
    frame_layout: Optional[str] = None
    framing_template: Optional[str] = None
    framing_user_locked: Optional[bool] = None
    webcam_corner: Optional[str] = None
    webcam_split_ratio: Optional[float] = None
    source_webcam_region: Optional[Dict[str, Any]] = None
    framing_override: Optional[Dict[str, Any]] = None
    frame_crop_override: Optional[Any] = None
    clear_framing_override: Optional[bool] = None
    camera_motion: Optional[str] = None
    whip_pan_cuts: Optional[bool] = None
    pip_border_color: Optional[str] = None
    zoom: Optional[float] = None
    pan_x_offset: Optional[float] = None
    pan_x: Optional[float] = None

    # Step 2: Captions
    burn_captions: Optional[bool] = None
    timing_offset: Optional[float] = None
    caption_style: Optional[str] = None
    caption_font: Optional[str] = None
    caption_animation: Optional[str] = None
    base_color: Optional[str] = None
    active_color: Optional[str] = None
    outline_color: Optional[str] = None
    pill_color: Optional[str] = None
    pacing_density: Optional[str] = None
    text_case: Optional[str] = None
    font_size: Optional[int] = None
    font_size_percent: Optional[int] = None
    bold: Optional[bool] = None
    italic: Optional[bool] = None
    all_caps: Optional[bool] = None
    text_align: Optional[str] = None
    position_grid: Optional[str] = None
    position_x: Optional[float] = None
    position_y: Optional[float] = None
    caption_x: Optional[float] = None
    caption_y: Optional[float] = None
    caption_custom_position: Optional[bool] = None
    caption_rotation: Optional[float] = None
    caption_max_width: Optional[float] = None
    hold_back_alpha: Optional[float] = None
    highlight_shape: Optional[str] = None
    emoji_enabled: Optional[bool] = None
    highlight_power_words: Optional[bool] = None
    how_phrase_arrives: Optional[str] = None
    outline_width: Optional[float] = None
    shadow_depth: Optional[float] = None
    glow_intensity: Optional[float] = None
    letter_spacing: Optional[float] = None
    clean_filler_words: Optional[bool] = None
    tighten_pacing: Optional[bool] = None
    silence_threshold: Optional[float] = None
    censor_profanity: Optional[bool] = None

    # Step 3: Color Grading & Effects
    clip_size: Optional[str] = None
    export_resolution: Optional[str] = None
    export_fps: Optional[str] = None
    color_preset: Optional[str] = None
    preset_intensity: Optional[float] = None
    exposure: Optional[float] = None
    brightness: Optional[float] = None
    contrast: Optional[float] = None
    lift_mids: Optional[float] = None
    highlights: Optional[float] = None
    shadows: Optional[float] = None
    warmth: Optional[float] = None
    saturation: Optional[float] = None
    black_and_white: Optional[float] = None
    vintage: Optional[float] = None
    tint_color: Optional[str] = None
    color_tint: Optional[str] = None
    tint_strength: Optional[float] = None
    glow: Optional[float] = None
    smooth: Optional[float] = None
    sharpness: Optional[float] = None
    blur: Optional[float] = None
    film_grain: Optional[float] = None
    vignette: Optional[float] = None
    bottom_fade: Optional[float] = None
    color_temp: Optional[float] = None
    tint_shift: Optional[float] = None
    vibrance: Optional[float] = None
    matte_fade: Optional[float] = None
    halation_glow: Optional[float] = None
    lut_path: Optional[str] = None
    chroma_split: Optional[float] = None
    letterbox: Optional[str] = None
    speed_ramp: Optional[float] = None
    voice_eq_preset: Optional[str] = None
    de_esser: Optional[bool] = None
    loudness_target: Optional[str] = None
    voice_enhancer: Optional[bool] = None
    horizon_tilt: Optional[float] = None

    # Step 4: Branding & BGM / Audio
    watermark_path: Optional[str] = None
    watermark_corner: Optional[str] = None
    watermark_opacity: Optional[float] = None
    watermark_size: Optional[float] = None
    outro_path: Optional[str] = None
    bgm_path: Optional[str] = None
    bgm_volume: Optional[float] = None
    audio_volume: Optional[float] = None
    normalize_audio: Optional[bool] = None
    vocal_clarity: Optional[bool] = None
    voice_isolation: Optional[bool] = None
    auto_sfx: Optional[bool] = None
    retention_bar_enabled: Optional[bool] = None
    retention_bar_color: Optional[str] = None
    outro_cta_enabled: Optional[bool] = None
    outro_cta_text: Optional[str] = None
    outro_cta_headline: Optional[str] = None
    outro_cta_handle: Optional[str] = None
    outro_logo_path: Optional[str] = None
    broll_segments: Optional[List[Dict[str, Any]]] = None
    broll_layout: Optional[str] = None
    speakers: Optional[Dict[str, Any]] = None
    speaker_lower_thirds: Optional[bool] = None
    face_safe_captions: Optional[bool] = None
    callout_cards: Optional[List[Dict[str, Any]]] = None
    auto_callouts: Optional[bool] = None
    bilingual_enabled: Optional[bool] = None
    secondary_language: Optional[str] = None
    dubbing_enabled: Optional[bool] = None
    dubbing_language: Optional[str] = None
    dubbing_voice: Optional[str] = None
    dubbing_bed_volume: Optional[float] = None
    dubbed_audio_path: Optional[str] = None
    backdrop_mode: Optional[str] = None
    backdrop_blur_intensity: Optional[float] = None
    backdrop_color: Optional[str] = None
    depth_pop: Optional[float] = None
    privacy_blur_boxes: Optional[List[Dict[str, Any]]] = None
    beat_sync_enabled: Optional[bool] = None
    spectrum_enabled: Optional[bool] = None
    spectrum_position: Optional[str] = None
    spectrum_color: Optional[str] = None
    chapters_enabled: Optional[bool] = None
    chapters: Optional[List[Dict[str, Any]]] = None
    handle_watermark_enabled: Optional[bool] = None
    handle_text: Optional[str] = None
    handle_platform_icon: Optional[str] = None
    handle_corner: Optional[str] = None
    handle_opacity: Optional[float] = None
    handle_display_mode: Optional[str] = None
    hardware_mode: Optional[str] = None

    # Publishing metadata & transcript
    selected_title: Optional[str] = None
    youtube_description: Optional[str] = None
    hashtags: Optional[str] = None
    pinned_comment: Optional[str] = None
    privacy_status: Optional[str] = None
    transcript_words: Optional[List[Dict[str, Any]]] = None
    keep_segments: Optional[List[List[float]]] = None
    merge_crossfade_sec: Optional[float] = None


class DiarizeClipRequest(BaseModel):
    num_speakers: int = 0


class UpdateSpeakersRequest(BaseModel):
    speakers: Dict[str, Any]
    transcript_words: Optional[List[Dict[str, Any]]] = None


class ExtractCalloutsRequest(BaseModel):
    max_cards: int = 3


class TranslateClipRequest(BaseModel):
    target_language: str = "Spanish"


class DubClipRequest(BaseModel):
    target_language: str = "Spanish"
    voice: str = "default"
    original_bed_volume: float = 0.15


class CompileTrailerRequest(BaseModel):
    title: Optional[str] = None
    max_moments: int = 4
    target_duration_sec: float = 32.0


class GenerateThumbnailRequest(BaseModel):
    timestamp: Optional[float] = None
    headline: str = ""
    format: str = "9:16"
    depth_layer: bool = True
    accent_color: str = "#C6F32F"
    rim_glow: bool = True
    badge_text: str = ""


class UpdateWebcamRegionRequest(BaseModel):
    source_zone: Optional[str] = None
    x: Optional[float] = None
    y: Optional[float] = None
    w: Optional[float] = None
    h: Optional[float] = None
    refresh: bool = False


class FramingOverrideRequest(BaseModel):
    webcam_box: Optional[Dict[str, float]] = None
    focus_anchor: Optional[Dict[str, float]] = None
    frame_crop: Optional[Dict[str, float]] = None
    frame_crop_x: Optional[float] = None
    frame_crop_y: Optional[float] = None
    frame_crop_w: Optional[float] = None
    frame_crop_h: Optional[float] = None
    keyframe_start_crop: Optional[Dict[str, float]] = None
    keyframe_end_crop: Optional[Dict[str, float]] = None
    lock_camera: Optional[bool] = None
    reset: bool = False


class GenerateChaptersRequest(BaseModel):
    max_chapters: int = 4
    enable: bool = True


class UpdateClipMetadataRequest(BaseModel):
    selected_title: Optional[str] = None
    youtube_description: Optional[str] = None
    hashtags: Optional[str] = None
    pinned_comment: Optional[str] = None
    privacy_status: Optional[str] = None
    social_copy: Optional[Dict[str, Any]] = None


class RenderClipRequest(BaseModel):
    quality_mode: str = "final_hq"
    use_cold_open: Optional[bool] = None
    cold_open: Optional[bool] = None
    cold_open_climax: Optional[bool] = None
    auto_trim_dead_air: Optional[bool] = None
    trim_dead_air: Optional[bool] = None
    dynamic_punch_zoom: Optional[bool] = None
    punch_zoom: Optional[bool] = None
    punch_zoom_fx: Optional[bool] = None
    punch_zoom_intensity: Optional[float] = None
    silence_threshold: Optional[float] = None
    caption_animation: Optional[str] = None
    highlight_shape: Optional[str] = None
    pill_color: Optional[str] = None
    pacing_density: Optional[str] = None
    text_case: Optional[str] = None
    caption_x: Optional[float] = None
    caption_y: Optional[float] = None
    caption_custom_position: Optional[bool] = None
    face_safe_captions: Optional[bool] = None
    caption_rotation: Optional[float] = None
    caption_max_width: Optional[float] = None
    color_preset: Optional[str] = None
    preset_intensity: Optional[float] = None
    highlights: Optional[float] = None
    shadows: Optional[float] = None
    export_fps: Optional[str] = None
    lut_path: Optional[str] = None
    voice_eq_preset: Optional[str] = None
    de_esser: Optional[bool] = None
    loudness_target: Optional[str] = None
    voice_enhancer: Optional[bool] = None
    horizon_tilt: Optional[float] = None
    speed_ramp: Optional[float] = None
    chroma_split: Optional[float] = None
    letterbox: Optional[str] = None
    outro_cta_enabled: Optional[bool] = None
    frame_layout: Optional[str] = None
    framing_template: Optional[str] = None
    framing_user_locked: Optional[bool] = None
    webcam_corner: Optional[str] = None
    webcam_split_ratio: Optional[float] = None
    pip_border_color: Optional[str] = None
    source_corner: Optional[str] = None
    framing_override: Optional[Dict[str, Any]] = None
    frame_crop_override: Optional[Any] = None
    clear_framing_override: Optional[bool] = None
    show_hook: Optional[bool] = None
    show_hook_banner: Optional[bool] = None
    hook_banner_enabled: Optional[bool] = None
    enable_hook_banner: Optional[bool] = None
    top_hook_banner: Optional[bool] = None
    hook_headline: Optional[str] = None
    hook_text: Optional[str] = None
    safe_zone: Optional[str] = None
    safe_zone_guide: Optional[str] = None
    options: Optional[Dict[str, Any]] = None


class TrimClipRequest(BaseModel):
    start_time: float
    end_time: float


class UpdateSegmentsRequest(BaseModel):
    keep_segments: List[List[float]]
    crossfade_sec: Optional[float] = None


class MergeClipsRequest(BaseModel):
    clip_ids: List[int]
    smooth_transition: bool = True
    transition_duration: float = 0.35
    title: Optional[str] = None


class CreateBrandPresetRequest(BaseModel):
    name: str
    caption_style_name: Optional[str] = "Cinematic"
    watermark_url: Optional[str] = None
    watermark_corner: Optional[str] = "top_right"
    watermark_opacity: Optional[float] = 0.85
    watermark_size: Optional[float] = 0.15
    outro_url: Optional[str] = None
    bgm_url: Optional[str] = None
    bgm_volume: Optional[float] = 0.20
    aspect_ratio: Optional[str] = "9:16"
    safe_zone_guide: Optional[str] = "TikTok"


class CreateCaptionStyleRequest(BaseModel):
    name: str
    font_family: Optional[str] = "Montserrat"
    font_size_percent: Optional[int] = 100
    bold: Optional[bool] = True
    italic: Optional[bool] = False
    uppercase: Optional[bool] = True
    text_align: Optional[str] = "Centre"
    base_color: Optional[str] = "#FFFFFF"
    active_color: Optional[str] = "#C6F32F"
    hold_back_alpha: Optional[float] = 1.0
    highlight_shape: Optional[str] = "Colour only"
    animation_type: Optional[str] = "Karaoke pop"
    position_grid: Optional[str] = "bottom_center"
    position_x: Optional[float] = 50.0
    position_y: Optional[float] = 78.0
    outline_width: Optional[float] = 4.0
    shadow_depth: Optional[float] = 3.0
    glow_intensity: Optional[float] = 0.0
    letter_spacing: Optional[float] = 0.0
    timing_offset: Optional[float] = 0.0
    emoji_enabled: Optional[bool] = False
    how_phrase_arrives: Optional[str] = "All at once"


class PublishYouTubeRequest(BaseModel):
    clip_id: Optional[int] = None
    title: Optional[str] = None
    description: Optional[str] = None
    tags: Optional[List[str]] = None
    privacy_status: Optional[str] = "private"
    privacy: Optional[str] = None
    category_id: Optional[str] = "22"


class YouTubeCredentialsRequest(BaseModel):
    client_id: str
    client_secret: str


class SettingsUpdateRequest(BaseModel):
    gemini_api_key: Optional[str] = None
    gemini_model: Optional[str] = None
    gemini_fallback_model: Optional[str] = None
    gemini_analysis_mode: Optional[str] = None
    auto_fallback_offline: Optional[bool] = None
    ai_provider: Optional[str] = None
    ai_mode: Optional[str] = None
    ollama_endpoint: Optional[str] = None
    ollama_model: Optional[str] = None
    whisper_model: Optional[str] = None
    gemini_enabled: Optional[bool] = None
    gemini_boost_enabled: Optional[bool] = None
    active_theme: Optional[str] = None
    custom_themes: Optional[List[Dict[str, Any]]] = None
    hardware_acceleration: Optional[str] = None
    video_encoder: Optional[str] = None
    default_resolution: Optional[str] = None
    default_fps: Optional[str] = None
    loudness_target: Optional[str] = None
    audio_normalization_enabled: Optional[bool] = None
    auto_cleanup_days: Optional[int] = None
    pexels_api_key: Optional[str] = None
    watch_folder_path: Optional[str] = None
    watch_folder_enabled: Optional[bool] = None

class TestGeminiRequest(BaseModel):
    api_key: Optional[str] = None

class ProvideKeyRequest(BaseModel):
    api_key: str
    model: Optional[str] = None

class RetryProjectRequest(BaseModel):
    force_offline: Optional[bool] = False
    ai_mode: Optional[str] = None
    stage: Optional[str] = None


class BulkDeleteProjectsRequest(BaseModel):
    project_ids: List[int] = Field(default_factory=list)


class ProjectCleanupRequest(BaseModel):
    mode: str


class InstallPrerequisitesRequest(BaseModel):
    install_ffmpeg: bool = False
    whisper_model: Optional[str] = None


class CompleteOnboardingRequest(BaseModel):
    whisper_model: str = "base"
    whisper_device: str = "auto"
    whisper_compute_type: str = "auto"
    whisper_batch_size: Optional[int] = 4
    video_encoder: Optional[str] = "libx264"
    default_resolution: Optional[str] = "1080p"
    vision_scan_fps: Optional[float] = 1.2
    ai_mode: Optional[str] = "offline"
    gemini_api_key: Optional[str] = None
    hardware_acceleration: Optional[str] = "auto"
    active_theme: Optional[str] = None


class ImportAnalyticsCsvRequest(BaseModel):
    csv_text: str


class ProExportRequest(BaseModel):
    format: str = "mp4_h264"
    resolution: Optional[str] = None
    fps: Optional[str] = None
    quality_preset: str = "social_balanced"
    burn_captions: Optional[bool] = None
    aspects: Optional[List[str]] = None


class ExportEvent(SQLModel, table=True):
    __tablename__ = "export_events"

    id: Optional[int] = SQLField(default=None, primary_key=True)
    project_id: Optional[int] = SQLField(default=None, index=True)
    clip_id: Optional[int] = SQLField(default=None, index=True)
    event_type: str = SQLField(default="mp4_export")
    aspect_ratio: str = SQLField(default="9:16")
    caption_style: str = SQLField(default="Karaoke pop")
    frame_layout: str = SQLField(default="auto_track")
    duration_sec: float = SQLField(default=30.0)
    hook_score: int = SQLField(default=0)
    has_hook_banner: bool = SQLField(default=False)
    beat_sync_enabled: bool = SQLField(default=False)
    bilingual_enabled: bool = SQLField(default=False)
    title: str = SQLField(default="")
    views: int = SQLField(default=0)
    retention_pct: float = SQLField(default=0.0)
    ctr_pct: float = SQLField(default=0.0)
    created_at: datetime = SQLField(default_factory=lambda: datetime.now(timezone.utc))
