export interface WordTimestamp {
  word: string;
  start: number;
  end: number;
  is_filler?: boolean;
  deleted?: boolean;
  cut_from_video?: boolean;
  is_profane?: boolean;
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
  highlight_shape: 'Colour only' | 'Filled box' | 'Underline';
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
  title: string;
  description: string;
  pinned_comment?: string;
  hashtags: string[];
}

export type PlatformCopyMap = Record<'youtube_shorts' | 'tiktok' | 'instagram_reels' | 'x_twitter', PlatformCopyEntry>;

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
}

export interface ViralClip {
  id: number;
  project_id: number;
  start_time: number;
  end_time: number;
  duration: number;
  virality_score: number;
  hook_score: number;
  retention_score: number;
  emotional_score: number;
  standalone_score: number;
  viral_rationale: string;

  // Hooks & Retention FX
  hook_headline: string;
  show_hook_banner: boolean;
  cold_open_quote: string;
  use_cold_open: boolean;
  auto_trim_dead_air: boolean;
  dynamic_punch_zoom: boolean;
  punch_zoom_intensity?: number;

  // Step 1: Multi-format & layouts
  aspect_ratio: '9:16' | '16:9' | '1:1' | '4:5' | '4:3' | 'custom';
  custom_width: number;
  custom_height: number;
  frame_layout: 'auto_track' | 'webcam_gameplay_split' | 'webcam_gameplay_pip' | 'gameplay_webcam_pip' | 'split_podcast' | 'fit_blur';
  webcam_corner: 'auto' | 'top_left' | 'top_right' | 'bottom_left' | 'bottom_right';
  webcam_split_ratio: number;
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
  hold_back_alpha: number;
  highlight_shape: 'Colour only' | 'Filled box' | 'Underline';
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
  clip_size: 'Auto' | '1080p' | '720p' | '540p' | '480p';
  color_preset: string;
  exposure: number;
  brightness: number;
  contrast: number;
  lift_mids: number;
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
  broll_json?: string;
  broll_segments?: BrollSegment[];
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
  status: 'created' | 'proxy_ready' | 'analyzing' | 'analyzed' | 'processing' | 'finished' | 'error';
  error_message?: string;
  default_aspect_ratio: string;
  duration_preset: string;
  clip_count: number;
  clip_length_target?: string;
  caption_language?: string;
  hardware_mode?: string;
  topic_focus?: string;
  created_at: string;
  disk_size_bytes?: number;
  disk_size_formatted?: string;
  clips_count?: number;
}

export interface ProjectsListResponse {
  projects: Project[];
  total_disk_bytes: number;
  total_disk_formatted: string;
  projects_count: number;
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
  ollama_endpoint?: string;
  ollama_model?: string;
  model_cascade?: string[];
  whisper_model: string;
  whisper_device: string;
  youtube_authenticated: boolean;
  hardware_acceleration: string;
  ffmpeg_path: string;
  gemini_boost_enabled?: boolean;
  active_theme?: string;
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
}

export interface PipelineLog {
  timestamp: string;
  stage: string;
  level: 'INFO' | 'SUCCESS' | 'WARN' | 'ERROR';
  message: string;
}

export interface JobProgress {
  project_id: number;
  title: string;
  status: 'created' | 'processing' | 'finished' | 'analyzed' | 'error' | 'cancelled' | 'not_found' | 'awaiting_api_key';
  api_key_reason?: 'missing' | 'invalid';
  api_key_message?: string;
  current_stage: string;
  stage_label: string;
  percent: number;
  started_at?: string;
  active_started_at?: string;
  accumulated_seconds?: number;
  elapsed_seconds?: number;
  updated_at?: string;
  eta_seconds?: number;
  source_path?: string;
  source_filename?: string;
  file_size_bytes?: number;
  file_size_formatted?: string;
  hardware_badge?: string;
  duration_preset?: string;
  aspect_ratio?: string;
  clip_count?: number;
  caption_language?: string;
  topic_focus?: string;
  hardware_mode?: string;
  stages: PipelineStage[];
  logs: PipelineLog[];
  error_detail?: string;
  clips_count?: number;
}
