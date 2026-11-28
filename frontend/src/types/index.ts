export interface WordTimestamp {
  word: string;
  start: number;
  end: number;
  is_filler?: boolean;
  deleted?: boolean;
  cut_from_video?: boolean;
  is_profane?: boolean;
  speaker_id?: string;
  translated_text?: string;
  translated_phrase?: string;
  translated_chunk_id?: number;
}

export interface SpeakerProfile {
  id: string;
  name: string;
  color: string;
  word_count?: number;
}

export interface CalloutCard {
  id: string;
  start: number;
  end: number;
  badge: string;
  title: string;
  category?: string;
  accent_color?: string;
  enabled?: boolean;
}

export interface PrivacyBlurBox {
  id: string;
  x: number;
  y: number;
  w: number;
  h: number;
  mode: 'gaussian' | 'pixelate';
  track_motion: boolean;
  intensity?: number;
}

export interface BrandPreset {
  id?: number;
  name: string;
  caption_style_name?: string;
  watermark_url?: string;
  watermark_corner: 'top_left' | 'top_right' | 'bottom_left' | 'bottom_right';
  watermark_opacity: number;
  watermark_size: number;
  outro_url?: string;
  bgm_url?: string;
  bgm_volume: number;
  aspect_ratio: string;
  safe_zone_guide: 'No guide' | 'TikTok' | 'Instagram' | 'YT Shorts' | 'X' | 'All';
  created_at?: string;
}

export interface CaptionStylePreset {
  id?: number | string;
  name: string;
  font_family: string;
  font_size_percent: number;
  bold: boolean;
  italic: boolean;
  uppercase: boolean;
  text_align: 'Left' | 'Centre' | 'Right';
  base_color: string;
  active_color: string;
  outline_color?: string;
  hold_back_alpha: number;
  highlight_shape: string;
  animation_type: string;
  position_grid: string;
  position_x: number;
  position_y: number;
  outline_width: number;
  shadow_depth: number;
  glow_intensity: number;
  letter_spacing: number;
  timing_offset: number;
  emoji_enabled: boolean;
  highlight_power_words?: boolean;
  how_phrase_arrives: 'All at once' | 'Word by word';
  is_builtin?: boolean;
  border_style?: number;
}

export interface HookVariant {
  variant_type: string;
  hook_headline: string;
  predicted_retention_lift?: string;
  rationale?: string;
}

export interface PlatformCopyEntry {
  platform?: string;
  title?: string;
  description?: string;
  caption?: string;
  post_text?: string;
  pinned_comment?: string;
  hashtags?: string[];
  tags?: string[];
  category_id?: string;
}

export interface SocialCopyBundle {
  youtube?: {
    title: string;
    description: string;
    tags: string[];
    pinned_comment: string;
    category_id: string;
  };
  instagram?: {
    title?: string;
    caption: string;
    pinned_comment: string;
    hashtags?: string[];
  };
  tiktok?: {
    title?: string;
    caption: string;
    pinned_comment: string;
    hashtags?: string[];
  };
  x?: {
    title?: string;
    post_text: string;
    pinned_comment?: string;
    hashtags?: string[];
  };
  platforms?: Partial<Record<'youtube_shorts' | 'tiktok' | 'instagram_reels' | 'x_twitter', PlatformCopyEntry>>;
  source?: string;
  mode?: 'gemini' | 'offline' | string;
  categories?: Array<{ id: string; label: string }>;
}

export type PlatformCopyMap = Record<'youtube_shorts' | 'tiktok' | 'instagram_reels' | 'x_twitter', PlatformCopyEntry>;

export interface RetentionPoint {
  sec: number;
  score: number;
  wpm: number;
  rms: number;
  face_offset: number;
}

export interface RetentionWarning {
  id: string;
  timestamp: number;
  end_timestamp: number;
  severity: 'high' | 'medium' | 'low';
  category: string;
  issue: string;
  fix_label: string;
  fix_action: {
    type: 'set_segments' | 'patch_clip';
    keep_segments?: [number, number][];
    updates?: Partial<ViralClip>;
  };
}

export interface RetentionAuditReport {
  clip_id: number;
  effective_duration: number;
  overall_score: number;
  time_to_first_word: number;
  dead_air_count: number;
  retention_curve: RetentionPoint[];
  drop_off_warnings: RetentionWarning[];
}

export interface SystemInfo {
  cpu_model: string;
  cpu_cores: number;
  ram_gb: number;
  gpu_name: string;
  has_cuda: boolean;
  active_encoder: string;
  whisper_model: string;
  gemini_model?: string;
  ai_provider?: string;
  gemini_status: string;
  gemini_configured?: boolean;
  gemini_enabled?: boolean;
}

export interface PreviewFrameResponse {
  frame_url: string;
  render_time_ms: number;
  timestamp: number;
  timeline_fx_summary?: {
    cold_open_active?: boolean;
    cold_open_label?: string | null;
    dead_air_cuts_count?: number;
    dead_air_saved_sec?: number;
    dead_air_label?: string | null;
    punch_zoom_count?: number;
    punch_zoom_label?: string | null;
  } | null;
}

export interface PresetPreviewItem {
  id: string;
  name: string;
  description: string;
  thumbnail_url?: string | null;
  values: Record<string, number>;
}

export interface PresetPreviewsResponse {
  project_id: number;
  clip_id: number;
  ref_timestamp: number;
  presets: PresetPreviewItem[];
  lut_preview?: {
    id: string;
    name: string;
    description: string;
    thumbnail_url?: string | null;
    lut_path?: string | null;
  } | null;
  cache_key?: string;
}

export interface SourceWebcamRegion {
  detected: boolean;
  webcam_detected: boolean;
  source_zone: string;
  locked_zone?: string;
  x: number;
  y: number;
  w: number;
  h: number;
  aspect?: number;
  aspect_ratio?: number;
  face_cx?: number;
  face_cy?: number;
}

export interface FramingBox {
  x: number;
  y: number;
  w: number;
  h: number;
}

export interface FramingOverride {
  webcam_box?: FramingBox | null;
  focus_anchor?: { x: number; y: number } | null;
  frame_crop?: FramingBox | null;
  keyframe_start_crop?: FramingBox | null;
  keyframe_end_crop?: FramingBox | null;
  lock_camera?: boolean;
}

export interface FramingMapResponse {
  clip_id: number;
  project_id: number;
  visual_map: {
    version?: number;
    duration?: number;
    source_w?: number;
    source_h?: number;
    cut_timestamps?: number[];
    shots?: Array<{
      shot_id: number;
      start_t: number;
      end_t: number;
      speaker_mode: 'two_speaker' | 'single_speaker' | 'zero_face' | string;
      face_count_median: number;
      active_speaker_idx: number;
      cx: number;
      cy: number;
      action_cx: number;
      action_cy: number;
    }>;
    keyframes?: Array<{
      t: number;
      cx: number;
      cy: number;
      action_cx?: number;
      action_cy?: number;
    }>;
    webcam?: SourceWebcamRegion;
    summary?: {
      has_corner_webcam?: boolean;
      face_presence_ratio?: number;
      dual_speaker_ratio?: number;
      dominant_scene_type?: string;
    };
  };
  framing_override?: FramingOverride | null;
  source_webcam_region?: SourceWebcamRegion | null;
  recommended?: {
    frame_layout: string;
    webcam_corner: string;
    scene_type: string;
    reason: string;
    confidence: number;
  };
}

export interface PreflightCheck {
  id: string;
  title: string;
  status: 'pass' | 'warn' | 'fail';
  detail: string;
  fix_label?: string;
  fix_patch?: Partial<ViralClip>;
}

export interface PreflightReport {
  clip_id: number;
  readiness_score: number;
  status: 'pass' | 'warn' | 'risk';
  checks: PreflightCheck[];
}

export interface AnalyticsFormulaReport {
  has_data: boolean;
  sample_count: number;
  headline: string;
  preferred_aspect_ratio: string;
  preferred_caption_style: string;
  preferred_frame_layout: string;
  preferred_duration_band: string;
  preferred_duration_bounds: [number, number];
  hook_banner_recommended: boolean;
  beat_sync_recommended: boolean;
  avg_views: number;
  avg_retention_pct: number;
  prompt_guidance: string;
}

export interface ViralClip {
  id: number;
  project_id: number;
  start_time: number;
  end_time: number;
  duration: number;
  keep_segments?: [number, number][];
  merge_crossfade_sec?: number;
  virality_score: number;
  hook_score: number;
  retention_score: number;
  emotional_score: number;
  standalone_score: number;
  viral_rationale: string;

  // Hooks & Retention FX
  hook_headline: string;
  hook_text?: string;
  show_hook_banner: boolean;
  show_hook?: boolean;
  cold_open_quote: string;
  use_cold_open: boolean;
  cold_open?: boolean;
  auto_trim_dead_air: boolean;
  trim_dead_air?: boolean;
  dynamic_punch_zoom: boolean;
  punch_zoom?: boolean;
  punch_zoom_intensity?: number;
  framing_user_locked?: boolean;

  // Step 1: Multi-format & layouts
  aspect_ratio: '9:16' | '16:9' | '1:1' | '4:5' | '4:3' | '2.39:1' | 'custom';
  custom_width: number;
  custom_height: number;
  frame_layout:
    | 'auto_track'
    | 'fit_blur'
    | 'webcam_gameplay_split'
    | 'webcam_gameplay_pip'
    | 'gameplay_webcam_pip'
    | 'split_podcast'
    | 'cinema_letterbox_blur'
    | 'triple_gaming_hud'
    | 'portrait_pip_badge'
    | 'dynamic_punch_crop'
    | 'side_by_side_50_50';
  framing_template?: ViralClip['frame_layout'];
  webcam_corner: 'auto' | 'top_left' | 'top_right' | 'bottom_left' | 'bottom_right';
  webcam_split_ratio: number;
  source_webcam_region?: SourceWebcamRegion | null;
  framing_override?: FramingOverride | null;
  frame_crop_override?: any;
  clear_framing_override?: boolean;
  visual_map_summary?: {
    has_corner_webcam?: boolean;
    face_presence_ratio?: number;
    dual_speaker_ratio?: number;
    dominant_scene_type?: string;
  } | null;
  pip_border_color: string;
  zoom: number;
  pan_x_offset: number;

  // Step 2: Captions & typography
  burn_captions: boolean;
  timing_offset: number;
  caption_style: string;
  caption_font: string;
  caption_animation: string;
  caption_language?: string;
  base_color: string;
  active_color: string;
  outline_color: string;
  pill_color?: string;
  pacing_density?: string;
  text_case?: string;
  font_size: number;
  font_size_percent: number;
  y_offset?: number;
  stroke_width?: number;
  bold: boolean;
  italic: boolean;
  all_caps: boolean;
  text_align: 'Left' | 'Centre' | 'Right';
  position_grid: string;
  position_x: number;
  position_y: number;
  caption_x?: number;
  caption_y?: number;
  caption_custom_position?: boolean;
  caption_rotation?: number;
  caption_max_width?: number;
  hold_back_alpha: number;
  highlight_shape: string;
  options?: Record<string, any>;
  emoji_enabled: boolean;
  highlight_power_words?: boolean;
  how_phrase_arrives: 'All at once' | 'Word by word';
  outline_width: number;
  shadow_depth: number;
  glow_intensity: number;
  letter_spacing: number;
  clean_filler_words: boolean;
  tighten_pacing?: boolean;
  silence_threshold?: number;
  censor_profanity?: boolean;

  // Step 3: Color grading & filters
  clip_size: 'Auto' | '4k' | '2160p' | '1080p' | '720p' | '540p' | '480p';
  export_fps?: 'source' | '60' | string;
  color_preset: string;
  preset_intensity?: number;
  exposure: number;
  brightness: number;
  contrast: number;
  lift_mids: number;
  highlights?: number;
  shadows?: number;
  warmth: number;
  saturation: number;
  black_and_white: number;
  vintage: number;
  tint_color: string;
  tint_strength: number;
  glow: number;
  smooth: number;
  sharpness: number;
  blur: number;
  film_grain: number;
  vignette: number | boolean;
  bottom_fade: number;
  color_temp?: number;
  tint_shift?: number;
  vibrance?: number;
  matte_fade?: number;
  halation_glow?: number;
  lut_path?: string;
  chroma_split?: number;
  letterbox?: string;
  speed_ramp?: number;
  horizon_tilt?: number;
  voice_eq_preset?: string;
  de_esser?: boolean;
  voice_enhancer?: boolean;
  loudness_target?: string;

  // Step 4: Branding, Outro & Audio
  watermark_path?: string;
  watermark_corner: 'top_left' | 'top_right' | 'bottom_left' | 'bottom_right';
  watermark_opacity: number;
  watermark_size: number;
  outro_path?: string;
  bgm_path?: string;
  bgm_volume: number;
  audio_volume?: number;
  normalize_audio?: boolean;
  vocal_clarity?: boolean;
  voice_isolation?: boolean;
  auto_sfx?: boolean;
  retention_bar_enabled?: boolean;
  retention_bar_color?: string;
  outro_cta_enabled?: boolean;
  outro_cta_text?: string;
  outro_cta_headline?: string;
  outro_cta_handle?: string;
  outro_logo_path?: string;
  broll_json?: string;
  broll_segments?: BrollSegment[];
  speakers?: Record<string, SpeakerProfile>;
  speaker_lower_thirds?: boolean;
  face_safe_captions?: boolean;
  callout_cards?: CalloutCard[];
  auto_callouts?: boolean;
  bilingual_enabled?: boolean;
  secondary_language?: string;
  dubbing_enabled?: boolean;
  dubbing_language?: string;
  dubbing_voice?: string;
  dubbing_bed_volume?: number;
  dubbed_audio_path?: string;
  backdrop_mode?: string;
  backdrop_blur_intensity?: number;
  backdrop_color?: string;
  camera_motion?: 'steady' | 'dolly_in' | 'dolly_out' | 'beat_punch' | string;
  whip_pan_cuts?: boolean;
  broll_layout?: 'fullscreen' | 'reaction_pip' | string;
  depth_pop?: number;
  privacy_blur_boxes?: PrivacyBlurBox[];
  beat_sync_enabled?: boolean;
  spectrum_enabled?: boolean;
  spectrum_position?: 'bottom' | 'top' | 'center' | string;
  spectrum_color?: string;
  chapters_enabled?: boolean;
  chapters?: Array<{ id?: string; start: number; end: number; title: string }>;
  handle_watermark_enabled?: boolean;
  handle_text?: string;
  handle_platform_icon?: 'none' | 'youtube' | 'tiktok' | 'instagram' | 'x' | string;
  handle_corner?: 'top_left' | 'top_right' | 'bottom_left' | 'bottom_right' | string;
  handle_opacity?: number;
  handle_display_mode?: 'always' | 'pulse_12s' | string;
  best_frames?: BestFrameItem[];
  multimodal_summary?: MultimodalSummary;
  has_existing_music?: boolean;
  narrative_arc?: string;
  arc_label?: string;
  dominant_emotion?: string;
  vocal_emotion?: string;
  emotion_arousal?: number;
  emotion_valence?: number;
  acoustic_events?: string[];
  analysis_source?: string;
  hardware_mode: 'gpu' | 'cpu';

  // Transcript & publishing
  transcript_words: WordTimestamp[];
  youtube_titles: string[];
  selected_title: string;
  youtube_description: string;
  hashtags?: string;
  pinned_comment: string;
  privacy_status: 'private' | 'unlisted' | 'public';

  // Render pipeline states
  status?: 'draft' | 'rendering' | 'ready' | 'failed';
  render_status: 'idle' | 'rendering' | 'completed' | 'failed';
  render_progress: number;
  render_message?: string;
  rendered_path?: string;
  preview_url?: string;
  video_url?: string;
  rendered_video_path?: string;
  video_size_bytes?: number | null;
  social_copy?: SocialCopyBundle | null;
  youtube_video_id?: string;
  youtube_url?: string;
  created_at: string;
}

export interface Project {
  id: number;
  title: string;
  source_type: 'local' | 'youtube';
  source_filename?: string;
  duration: number;
  resolution_w: number;
  resolution_h: number;
  fps: number;
  status: 'created' | 'proxy_ready' | 'analyzing' | 'analyzed' | 'processing' | 'finished' | 'ready' | 'awaiting_api_key' | 'queued' | 'error' | 'failed' | 'cancelled' | string;
  error_message?: string | null;
  default_aspect_ratio: string;
  duration_preset: string;
  clip_count: number;
  clip_length_target?: string;
  caption_language?: string;
  hardware_mode?: string;
  default_framing?: string;
  ai_mode?: 'gemini' | 'offline' | string;
  topic_focus?: string;
  thumbnail_url?: string | null;
  created_at: string;
  disk_bytes?: number;
  disk_size_bytes?: number;
  disk_size_formatted?: string;
  source_bytes?: number;
  clips_bytes?: number;
  cache_bytes?: number;
  source_missing?: boolean;
  clips_count?: number;
  tags?: string[];
  podcast_mode?: boolean;
}

export interface StorageBreakdown {
  source_bytes: number;
  source_formatted: string;
  clips_bytes: number;
  clips_formatted: string;
  cache_bytes: number;
  cache_formatted: string;
  reclaimable_test_count: number;
  reclaimable_test_bytes: number;
  reclaimable_test_formatted: string;
  reclaimable_failed_count: number;
  reclaimable_failed_bytes: number;
  reclaimable_failed_formatted: string;
}

export interface ProjectsListResponse {
  projects: Project[];
  total_disk_bytes: number;
  total_disk_formatted: string;
  projects_count: number;
  total_clips?: number;
  storage_breakdown?: StorageBreakdown;
}

export interface ProjectLogItem {
  timestamp: string;
  message: string;
  stage?: string;
}

export interface ProjectLogsResponse {
  project_id: number;
  logs: ProjectLogItem[];
}

export interface SystemCheckItem {
  id: string;
  label: string;
  ok: boolean;
  detail: string;
}

export interface SystemCheckReport {
  checks: SystemCheckItem[];
  all_passed: boolean;
  timestamp: string;
}

export interface BinaryDetail {
  path: string;
  version: string;
  size_mb?: number;
}

export interface MachineSpecs {
  cpu: {
    model: string;
    cores_physical: number;
    cores_logical: number;
    avx2: boolean;
  };
  ram: {
    total_gb: number;
    available_gb: number;
    percent_used: number;
  };
  gpu: {
    name: string;
    has_cuda: boolean;
    vram_mb: number;
    vram_gb: number;
    driver_version?: string;
  };
  storage: {
    total_gb: number;
    free_gb: number;
  };
  binaries: {
    ffmpeg: BinaryDetail;
    ffprobe: BinaryDetail;
    ytdlp: { version: string };
    faster_whisper: { version: string; models_dir: string };
  };
}

export interface ModelCatalogItem {
  name: string;
  size_mb: number;
  ram_required_gb: number;
  downloaded: boolean;
  is_recommended: boolean;
  path?: string;
}

export interface ModelsStatusReport {
  models: ModelCatalogItem[];
  active_model?: string;
  storage_used_mb?: number;
  storage_used_formatted?: string;
  storage?: {
    models_dir: string;
    used_mb: number;
    used_gb: number;
    free_gb: number;
  };
}

export interface BrollSegment {
  keyword: string;
  start: number;
  end: number;
  asset_path: string;
  source: 'local' | 'pexels' | 'unassigned';
  enabled: boolean;
  layout?: 'fullscreen' | 'reaction_pip';
}

export interface BestFrameItem {
  index: number;
  role: 'peak_emotion' | 'peak_action' | 'clean_anchor' | 'clean_opener' | 'highlight_moment' | string;
  t: number;
  rel_t: number;
  composite_score: number;
  sharpness_score: number;
  exposure_score: number;
  action_score: number;
  composition_score: number;
  emotion_intensity: number;
  eyes_open_confidence: number;
  gaze_score?: number;
  audio_excitement: number;
  face_count: number;
  image_path?: string;
  image_url: string;
}

export interface EmotionShiftItem {
  t: number;
  from_emotion: string;
  to_emotion: string;
  delta: number;
  arousal: number;
  vocal_emotion?: string;
  source?: string;
}

export interface AcousticEventItem {
  t: number;
  end: number;
  event_type: 'laughter_burst' | 'crowd_cheering' | 'bass_music_drop' | 'dramatic_pause' | string;
  confidence: number;
  label: string;
}

export interface ClipEmotionBreakdown {
  dominant_facial_emotion: string;
  dominant_vocal_emotion: string;
  avg_arousal: number;
  peak_arousal: number;
  avg_valence: number;
  emotion_delta_peak: number;
  narrative_arc?: string | null;
  arc_label?: string | null;
  facial_distribution: Record<string, number>;
  vocal_distribution: Record<string, number>;
}

export interface MultimodalTimelinePoint {
  t: number;
  combined_engagement_score: number;
  semantic_score: number;
  audio_prosody_score: number;
  visual_emotion_action_score: number;
  rms: number;
  lufs_m: number;
  wpm: number;
  sharpness_score: number;
  exposure_score: number;
  emotion_intensity: number;
  action_score: number;
  face_count: number;
  eyes_open_confidence: number;
  dominant_emotion?: string;
  vocal_emotion?: string;
  facial_arousal?: number;
  vocal_arousal?: number;
  fused_arousal?: number;
  fused_valence?: number;
  emotion_delta?: number;
  micro_expression_spike?: boolean;
  shot_scale?: string;
  acoustic_event?: string | null;
  speaker_id?: string | null;
  is_crosstalk?: boolean;
  fluency_score?: number;
  music_bed_intensity?: number;
  eye_contact_score?: number;
  gaze_engagement_score?: number;
  head_emphasis_spike?: boolean;
  visual_novelty_score?: number;
  top_hud_occupied?: boolean;
  bottom_hud_occupied?: boolean;
  is_cut?: boolean;
}

export interface RetentionRiskZone {
  start: number;
  end: number;
  duration: number;
  avg_novelty: number;
  avg_arousal: number;
  avg_fluency: number;
  reason: string;
}

export interface SmartRetentionFix {
  id: 'shift_captions_clear_hud' | 'enable_punch_zoom_on_peaks' | 'trim_hesitation_dead_air' | string;
  label: string;
  reason: string;
  target_caption_y?: number;
  max_gap_sec?: number;
  applied?: boolean;
}

export interface RetentionDiagnostics {
  retention_grade: 'S' | 'A' | 'B' | 'C' | string;
  retention_score: number;
  risk_zones: RetentionRiskZone[];
  risk_zone_count: number;
  smart_fixes: SmartRetentionFix[];
  speaker_breakdown: {
    speaker_count: number;
    dominance_ratios: Record<string, number>;
    crosstalk_overlaps: Array<{ t: number; end: number; confidence?: number; spectral_entropy?: number }>;
    crosstalk_count: number;
  };
  speech_fluency: {
    avg_fluency_score: number;
    filler_count: number;
    clean_hook_candidates: Array<{ t: number; end: number; text: string; fluency_score: number; speaker_id?: string }>;
    hesitation_intervals: Array<{ t: number; end: number; duration: number; reason?: string }>;
    max_hesitation_sec: number;
  };
  music_bed_summary: {
    has_existing_music: boolean;
    avg_music_intensity: number;
    estimated_bpm: number;
  };
  hud_obstruction: {
    top_hud_occupied: boolean;
    bottom_hud_occupied: boolean;
    recommended_caption_y: number;
    current_caption_y: number;
    hud_collision: boolean;
  };
  avg_eye_contact_score: number;
  avg_visual_novelty: number;
}

export interface MultimodalSummary {
  duration: number;
  step_sec?: number;
  overall_engagement_score: number;
  integrated_lufs: number;
  lra: number;
  true_peak_db: number;
  avg_wpm: number;
  avg_sharpness: number;
  avg_exposure: number;
  avg_action: number;
  face_presence_ratio: number;
  scene_cut_count: number;
  black_interval_count: number;
  dominant_facial_emotion?: string;
  dominant_vocal_emotion?: string;
  avg_arousal?: number;
  peak_arousal?: number;
  avg_valence?: number;
  speaker_switch_rate?: number;
  speaker_count?: number;
  dominance_ratios?: Record<string, number>;
  crosstalk_count?: number;
  avg_fluency_score?: number;
  has_existing_music?: boolean;
  avg_eye_contact_score?: number;
  avg_visual_novelty?: number;
  recommended_caption_y?: number;
  acoustic_event_counts?: Record<string, number>;
}

export interface BestFramesResponse {
  status?: string;
  clip_id: number;
  project_id: number;
  start_time: number;
  end_time: number;
  suggested_start_time?: number | null;
  frames: BestFrameItem[];
  multimodal_summary: MultimodalSummary;
  emotion_summary?: Record<string, any>;
  emotion_shifts?: EmotionShiftItem[];
  acoustic_events?: AcousticEventItem[];
  clip_emotion_breakdown?: ClipEmotionBreakdown;
  retention_diagnostics?: RetentionDiagnostics;
  multimodal_timeline: MultimodalTimelinePoint[];
  scene_cuts: Array<{ t: number; score: number }>;
  black_intervals?: Array<{ start: number; end: number; duration: number }>;
  peak_moments?: Array<{ t: number; combined_score: number; reason: string }>;
}

export interface StudioProfile {
  id: string;
  name: string;
  description: string;
  is_builtin?: boolean;
  settings: Partial<ViralClip>;
}

export interface BatchQueueItem {
  project_id: number;
  title: string;
  source: string;
  source_path?: string;
  status: 'queued' | 'processing' | 'completed' | 'error';
  enqueued_at: string;
  error?: string;
}

export interface GeminiModelInfo {
  id: string;
  display_name: string;
  description?: string;
}

export interface StudioSettings {
  gemini_configured: boolean;
  gemini_enabled?: boolean;
  gemini_model: string;
  gemini_fallback_model?: string;
  gemini_analysis_mode?: 'transcript' | 'multimodal';
  auto_fallback_offline?: boolean;
  ai_provider?: 'gemini' | 'ollama' | 'offline';
  ai_mode?: 'gemini' | 'offline' | null;
  effective_ai_mode?: 'gemini' | 'offline' | 'needs_api_key_or_offline_choice';
  needs_api_key_or_offline_choice?: boolean;
  whisper_badge?: string;
  ollama_endpoint?: string;
  ollama_model?: string;
  model_cascade?: string[];
  whisper_model: string;
  whisper_device: string;
  whisper_compute_type?: string;
  whisper_batch_size?: number;
  video_encoder?: string;
  default_resolution?: string;
  default_fps?: string;
  loudness_target?: string;
  audio_normalization_enabled?: boolean;
  auto_cleanup_days?: number;
  vision_scan_fps?: number;
  first_run_completed?: boolean;
  youtube_authenticated: boolean;
  hardware_acceleration: string;
  ffmpeg_path: string;
  gemini_boost_enabled?: boolean;
  active_theme?: string;
  custom_themes?: Array<Record<string, any>>;
  pexels_api_key?: string;
  watch_folder_path?: string;
  watch_folder_enabled?: boolean;
}

export interface GeminiModelTestResult {
  model: string;
  status: 'OK' | '503 High Demand' | '404 Unavailable' | 'Invalid Key' | 'Error';
  latency_ms: number;
  detail: string;
}

export interface TestGeminiResponse {
  status: string;
  api_key_configured: boolean;
  models: GeminiModelTestResult[];
  default_cascade: string[];
}

export interface PipelineStage {
  id: string;
  name: string;
  desc: string;
  status: 'pending' | 'running' | 'done' | 'failed' | 'action_required' | 'skipped';
  percent: number;
  started_at?: string | null;
  completed_at?: string | null;
  duration_sec?: number | null;
}

export interface PipelineLog {
  timestamp: string;
  stage: string;
  level: 'INFO' | 'SUCCESS' | 'WARN' | 'ERROR';
  message: string;
}

export interface JobLiveMetrics {
  words_transcribed: number;
  keyframes_scanned: number;
  keyframes_total: number;
  highlights_detected: number;
}

export interface YouTubeMetadataInfo {
  title: string;
  thumbnail?: string | null;
  duration: number;
  width: number;
  height: number;
  resolution: string;
  uploader?: string;
  webpage_url: string;
  filesize_approx?: number;
}

export interface JobProgress {
  project_id: number;
  title: string;
  status: 'created' | 'processing' | 'finished' | 'analyzed' | 'error' | 'failed' | 'cancelled' | 'not_found' | 'awaiting_api_key' | 'queued';
  api_key_reason?: 'missing' | 'invalid';
  api_key_message?: string;
  current_stage: string;
  stage_index?: number;
  stage_label: string;
  percent: number;
  progress_pct?: number;
  started_at?: string;
  active_started_at?: string;
  accumulated_seconds?: number;
  elapsed_seconds?: number;
  elapsed_sec?: number;
  updated_at?: string;
  eta_seconds?: number;
  source_path?: string;
  source_filename?: string;
  filename?: string;
  file_size_bytes?: number;
  file_size_formatted?: string;
  file_size_mb?: number;
  duration_sec?: number;
  hardware_badge?: string;
  hardware_label?: string;
  whisper_badge?: string;
  ai_mode?: 'gemini' | 'offline' | 'needs_api_key_or_offline_choice' | string;
  ai_provider?: string;
  needs_api_key_or_offline_choice?: boolean;
  needs_proxy?: boolean;
  duration_preset?: string;
  aspect_ratio?: string;
  clip_count?: number;
  caption_language?: string;
  default_framing?: string;
  topic_focus?: string;
  hardware_mode?: string;
  podcast_mode?: boolean;
  throughput_speed?: number | null;
  live_metrics?: JobLiveMetrics;
  stages: PipelineStage[];
  logs: PipelineLog[];
  error_detail?: string;
  clips_count?: number;
}

export interface PrerequisiteInstallStatus {
  status: 'idle' | 'running' | 'completed' | 'error';
  progress_pct: number;
  current_step: string;
  message: string;
  error?: string | null;
  logs: Array<{ timestamp: string; message: string }>;
  ffmpeg_installed: boolean;
  whisper_model_installed?: string | null;
}

export interface OnboardingDownloadItem {
  id: string;
  name: string;
  description: string;
  size_mb: number;
  required: boolean;
  installed: boolean;
  model_id?: string;
}

export interface OnboardingRecommendedSettings {
  whisper_model: string;
  whisper_device: string;
  whisper_compute_type: string;
  whisper_batch_size: number;
  video_encoder: string;
  encoder_preset?: string;
  default_resolution: string;
  vision_scan_fps: number;
  hardware_acceleration: string;
  ai_mode: 'gemini' | 'offline';
}

export interface SystemOnboardingReport {
  first_run_completed: boolean;
  specs: {
    os: { name: string; release: string; arch: string };
    cpu: { model: string; physical_cores: number; logical_threads: number };
    ram: { total_gb: number; available_gb: number };
    gpu: {
      name: string;
      has_cuda: boolean;
      has_nvidia?: boolean;
      cuda_device_count?: number;
      vram_mb: number;
      vram_gb: number;
      is_apple_silicon?: boolean;
    };
    encoders: { available: string[]; preferred: string };
    storage: { path: string; total_gb: number; free_gb: number };
    tools: { ffmpeg: boolean; ffprobe: boolean; yt_dlp: boolean };
    packages: { faster_whisper: boolean; mediapipe: boolean; cv2: boolean; scipy: boolean };
    cached_whisper_models: string[];
    os_name?: string;
    arch?: string;
    cpu_model?: string;
    cpu_cores_physical?: number;
    cpu_cores_logical?: number;
    ram_total_gb?: number;
    ram_available_gb?: number;
    gpu_name?: string;
    gpu_vram_gb?: number;
    preferred_encoder?: string;
    disk_free_gb?: number;
  };
  tier: 'ultra_gpu' | 'mid_gpu' | 'standard_cpu' | 'lightweight_cpu' | string;
  tier_label: string;
  downloads_needed: OnboardingDownloadItem[];
  recommended_settings: OnboardingRecommendedSettings;
  summary_reason: string;
}

export interface JobsListResponse {
  active_jobs: JobProgress[];
  queued_jobs: JobProgress[];
  recent_jobs: JobProgress[];
  jobs: JobProgress[];
}
