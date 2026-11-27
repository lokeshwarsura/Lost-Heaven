import { 
  Project, 
  ViralClip, 
  StudioSettings, 
  SystemInfo, 
  BrandPreset, 
  CaptionStylePreset, 
  PreviewFrameResponse,
  ProjectsListResponse,
  ProjectLogsResponse,
  SystemCheckReport,
  MachineSpecs,
  ModelsStatusReport,
  TestGeminiResponse,
  GeminiModelInfo,
  SourceWebcamRegion,
  FramingOverride,
  FramingMapResponse,
  JobProgress,
  JobsListResponse,
  YouTubeMetadataInfo,
  SystemOnboardingReport,
  PrerequisiteInstallStatus
} from '../types';

declare global {
  interface Window {
    lostHeaven?: {
      apiBaseUrl?: string;
    };
    clipship?: {
      apiBaseUrl?: string;
    };
  }
}

const _bridgeApiBase =
  typeof window !== 'undefined'
    ? window.lostHeaven?.apiBaseUrl || window.clipship?.apiBaseUrl
    : undefined;

const ORIGIN_BASE = _bridgeApiBase
  ? _bridgeApiBase.replace(/\/$/, '')
  : typeof window !== 'undefined' &&
      (window.location.protocol === 'http:' || window.location.protocol === 'https:')
    ? ''
    : 'http://127.0.0.1:8000';

const API_BASE = `${ORIGIN_BASE}/api`;

export const api = {
  // --- Diagnostics & Machine Specs ---
  async getSystemInfo(): Promise<SystemInfo> {
    const res = await fetch(`${API_BASE}/system-info`);
    return res.json();
  },

  async getSystemCheck(): Promise<SystemCheckReport> {
    const res = await fetch(`${API_BASE}/system/check`);
    return res.json();
  },

  async getMachineSpecs(): Promise<MachineSpecs> {
    const res = await fetch(`${API_BASE}/system/info`);
    return res.json();
  },

  async updateYtDlp(): Promise<{ status: string; output: string }> {
    const res = await fetch(`${API_BASE}/system/update-ytdlp`, { method: 'POST' });
    return res.json();
  },

  async getSystemOnboarding(): Promise<SystemOnboardingReport> {
    const res = await fetch(`${API_BASE}/system/onboarding`);
    if (!res.ok) {
      throw new Error(`Failed to load hardware onboarding specs (${res.status})`);
    }
    return res.json();
  },

  async installPrerequisites(payload: {
    install_ffmpeg: boolean;
    whisper_model?: string;
  }): Promise<PrerequisiteInstallStatus> {
    const res = await fetch(`${API_BASE}/system/install-prerequisites`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(payload),
    });
    if (!res.ok) {
      const err = await res.json().catch(() => ({ detail: 'Failed to launch installer' }));
      throw new Error(err.detail || 'Failed to launch prerequisite installer');
    }
    return res.json();
  },

  async getInstallStatus(): Promise<PrerequisiteInstallStatus> {
    const res = await fetch(`${API_BASE}/system/install-status`);
    if (!res.ok) {
      throw new Error(`Failed to fetch installer status (${res.status})`);
    }
    return res.json();
  },

  async completeOnboarding(payload: {
    whisper_model: string;
    whisper_device: string;
    whisper_compute_type: string;
    whisper_batch_size?: number;
    video_encoder?: string;
    default_resolution?: string;
    vision_scan_fps?: number;
    ai_mode?: 'gemini' | 'offline';
    gemini_api_key?: string;
    hardware_acceleration?: string;
    active_theme?: string;
  }): Promise<{ status: string; first_run_completed: boolean; settings: StudioSettings }> {
    const res = await fetch(`${API_BASE}/system/complete-onboarding`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(payload),
    });
    if (!res.ok) {
      const err = await res.json().catch(() => ({ detail: 'Failed to save onboarding settings' }));
      throw new Error(err.detail || 'Failed to save onboarding settings');
    }
    return res.json();
  },

  // --- Settings & Runtime Settings ---
  async getSettings(): Promise<StudioSettings> {
    const res = await fetch(`${API_BASE}/settings`);
    return res.json();
  },

  async updateSettings(settings: Partial<StudioSettings & { gemini_api_key?: string }>): Promise<any> {
    const res = await fetch(`${API_BASE}/settings`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(settings),
    });
    return res.json();
  },

  async getSystemSettings(): Promise<any> {
    const res = await fetch(`${API_BASE}/system/settings`);
    return res.json();
  },

  async updateSystemSettings(data: Record<string, any>): Promise<any> {
    const res = await fetch(`${API_BASE}/system/settings`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(data),
    });
    return res.json();
  },

  async testGeminiModels(apiKey?: string): Promise<TestGeminiResponse> {
    const res = await fetch(`${API_BASE}/system/test-gemini`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ api_key: apiKey }),
    });
    if (!res.ok) {
      throw new Error(`Failed to test Gemini models (${res.status})`);
    }
    return res.json();
  },

  async getGeminiModels(apiKey?: string): Promise<{ models: GeminiModelInfo[]; discovered_count?: number; auth_error?: string | null }> {
    const url = apiKey
      ? `${API_BASE}/system/gemini-models?api_key=${encodeURIComponent(apiKey)}`
      : `${API_BASE}/system/gemini-models`;
    const res = await fetch(url);
    if (!res.ok) {
      return { models: [] };
    }
    return res.json();
  },

  // --- Faster-Whisper Models Manager ---
  async getModelsStatus(): Promise<ModelsStatusReport> {
    const res = await fetch(`${API_BASE}/models/status`);
    return res.json();
  },

  async downloadModel(modelName: string): Promise<any> {
    const res = await fetch(`${API_BASE}/models/download`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ model_name: modelName }),
    });
    return res.json();
  },

  async deleteModel(modelName: string): Promise<any> {
    const res = await fetch(`${API_BASE}/models/${modelName}`, {
      method: 'DELETE',
    });
    return res.json();
  },

  // --- Projects & Disk Space Management ---
  async getProjectsList(): Promise<ProjectsListResponse> {
    const res = await fetch(`${API_BASE}/projects`);
    const data = await res.json();
    if (Array.isArray(data)) {
      return {
        projects: data,
        total_disk_bytes: 0,
        total_disk_formatted: '0 MB',
        projects_count: data.length
      };
    }
    return {
      projects: data.projects || [],
      total_disk_bytes: data.total_disk_bytes || 0,
      total_disk_formatted: data.total_disk_formatted || '0 MB',
      projects_count: data.total_projects || (data.projects?.length ?? 0)
    };
  },

  async getProjects(): Promise<Project[]> {
    const listRes = await this.getProjectsList();
    return listRes.projects;
  },

  async getProject(id: number): Promise<{ project: Project; clips: ViralClip[] }> {
    const res = await fetch(`${API_BASE}/projects/${id}`);
    return res.json();
  },

  async updateProject(id: number, updates: Partial<Project>): Promise<any> {
    const res = await fetch(`${API_BASE}/projects/${id}`, {
      method: 'PATCH',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(updates),
    });
    return res.json();
  },

  async deleteProject(id: number): Promise<{ status: string; project_id: number; reclaimed_bytes: number; reclaimed_formatted: string }> {
    const res = await fetch(`${API_BASE}/projects/${id}`, {
      method: 'DELETE',
    });
    return res.json();
  },

  async getProjectLogs(id: number): Promise<ProjectLogsResponse> {
    const res = await fetch(`${API_BASE}/projects/${id}/logs`);
    return res.json();
  },

  async getJobs(limit: number = 20): Promise<JobsListResponse> {
    const res = await fetch(`${API_BASE}/jobs?limit=${limit}`);
    if (!res.ok) {
      throw new Error(`Failed to fetch jobs (${res.status})`);
    }
    return res.json();
  },

  async getProjectProgress(id: number): Promise<JobProgress> {
    const res = await fetch(`${API_BASE}/projects/${id}/progress`);
    if (!res.ok) {
      throw new Error(`Failed to fetch project progress (${res.status})`);
    }
    return res.json();
  },

  async getYouTubeInfo(url: string): Promise<YouTubeMetadataInfo> {
    const res = await fetch(`${API_BASE}/ingest/youtube-info?url=${encodeURIComponent(url)}`);
    if (!res.ok) {
      const err = await res.json().catch(() => ({ detail: 'Could not inspect YouTube video metadata' }));
      throw new Error(err.detail || 'Could not inspect YouTube video metadata');
    }
    return res.json();
  },

  async cancelProject(id: number): Promise<{ status: string; project_id: number }> {
    const res = await fetch(`${API_BASE}/projects/${id}/cancel`, {
      method: 'POST'
    });
    return res.json();
  },

  async retryProject(id: number, forceOffline: boolean = false): Promise<{ status: string; project_id: number; resumed_stage?: string; force_offline?: boolean }> {
    const res = await fetch(`${API_BASE}/projects/${id}/retry`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ force_offline: forceOffline })
    });
    if (!res.ok) {
      throw new Error(`Failed to retry project (${res.status})`);
    }
    return res.json();
  },

  async provideApiKey(projectId: number, apiKey: string, model?: string): Promise<{ status: string; project_id: number; message: string; resumed_stage?: string }> {
    const res = await fetch(`${API_BASE}/projects/${projectId}/provide-key`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ api_key: apiKey, model })
    });
    if (!res.ok) {
      const err = await res.json().catch(() => ({ detail: 'Failed to verify key' }));
      throw new Error(err.detail || 'Failed to verify Gemini API key');
    }
    return res.json();
  },

  async retranscribeClip(projectId: number, clipId: number): Promise<{ status: string; clip_id: number; transcript_words: any[]; words_count: number }> {
    const res = await fetch(`${API_BASE}/clips/${projectId}/${clipId}/transcribe`, {
      method: 'POST'
    });
    if (!res.ok) {
      const err = await res.json().catch(() => ({ detail: 'Re-transcription failed' }));
      throw new Error(err.detail || 'Voice re-transcription failed');
    }
    return res.json();
  },

  async uploadVideo(
    file: File,
    aspectRatio: string = '9:16',
    durationPreset: string = 'Auto',
    clipCount: number = 5,
    topicFocus?: string,
    captionLanguage: string = 'As spoken',
    hardwareMode: string = 'gpu',
    aiMode?: 'gemini' | 'offline',
    defaultFraming: string = 'smart_auto',
    batchQueue: boolean = false
  ): Promise<{ project_id: number; title: string; status?: string; ai_mode?: string; default_framing?: string }> {
    const formData = new FormData();
    formData.append('file', file);
    formData.append('aspect_ratio', aspectRatio);
    formData.append('duration_preset', durationPreset);
    formData.append('clip_count', clipCount.toString());
    formData.append('caption_language', captionLanguage);
    formData.append('hardware_mode', hardwareMode);
    formData.append('default_framing', defaultFraming || 'smart_auto');
    formData.append('batch_queue', batchQueue ? 'true' : 'false');
    if (aiMode) {
      formData.append('ai_mode', aiMode);
    }
    if (topicFocus) {
      formData.append('topic_focus', topicFocus);
    }

    const res = await fetch(`${API_BASE}/ingest/upload`, {
      method: 'POST',
      body: formData,
    });
    if (!res.ok) throw new Error('Video upload failed');
    return res.json();
  },

  async importYouTube(
    url: string,
    aspectRatio: string = '9:16',
    durationPreset: string = 'Auto',
    clipCount: number = 5,
    topicFocus?: string,
    captionLanguage: string = 'As spoken',
    hardwareMode: string = 'gpu',
    aiMode?: 'gemini' | 'offline',
    defaultFraming: string = 'smart_auto',
    batchQueue: boolean = false
  ): Promise<{ project_id: number; title: string; status?: string; ai_mode?: string; default_framing?: string }> {
    const res = await fetch(`${API_BASE}/ingest/youtube`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        url,
        aspect_ratio: aspectRatio,
        duration_preset: durationPreset,
        clip_count: clipCount,
        topic_focus: topicFocus,
        caption_language: captionLanguage,
        hardware_mode: hardwareMode,
        ai_mode: aiMode,
        default_framing: defaultFraming || 'smart_auto',
        batch_queue: batchQueue
      }),
    });
    if (!res.ok) {
      const err = await res.json();
      throw new Error(err.detail || 'YouTube import failed');
    }
    return res.json();
  },

  async updateClip(clipId: number, updates: Partial<ViralClip>): Promise<any> {
    const res = await fetch(`${API_BASE}/clips/${clipId}`, {
      method: 'PATCH',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(updates),
    });
    return res.json();
  },

  async updateClipMetadata(
    projectId: number,
    clipId: number,
    metadata: {
      selected_title?: string;
      youtube_description?: string;
      hashtags?: string;
      pinned_comment?: string;
      privacy_status?: 'private' | 'unlisted' | 'public';
    }
  ): Promise<any> {
    const res = await fetch(`${API_BASE}/clips/${projectId}/${clipId}/metadata`, {
      method: 'PATCH',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(metadata),
    });
    if (!res.ok) {
      const err = await res.json().catch(() => ({ detail: 'Failed to save metadata' }));
      throw new Error(err.detail || 'Failed to save metadata');
    }
    return res.json();
  },

  async triggerRender(
    clipId: number,
    qualityMode: 'fast_preview' | 'final_hq' = 'final_hq',
    signal?: AbortSignal,
    projectId?: number
  ): Promise<{
    status: string;
    clip_status?: string;
    clip_id: number;
    project_id?: number;
    quality_mode?: string;
    rendered_path?: string;
    video_url?: string;
    preview_url?: string;
  }> {
    const endpoint =
      projectId !== undefined
        ? `${API_BASE}/clips/${projectId}/${clipId}/render`
        : `${API_BASE}/clips/${clipId}/render`;
    const res = await fetch(endpoint, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ quality_mode: qualityMode }),
      signal,
    });
    if (!res.ok) {
      const err = await res.json().catch(() => ({ detail: 'Render request failed' }));
      throw new Error(err.detail || 'Render request failed');
    }
    return res.json();
  },

  async publishToYouTube(
    clipId: number,
    data: { title?: string; description?: string; privacy_status?: string }
  ): Promise<any> {
    const res = await fetch(`${API_BASE}/publish/youtube?clip_id=${clipId}`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(data),
    });
    return res.json();
  },

  async getBrandPresets(): Promise<BrandPreset[]> {
    const res = await fetch(`${API_BASE}/presets/brand`);
    return res.json();
  },

  async createBrandPreset(preset: Partial<BrandPreset>): Promise<any> {
    const res = await fetch(`${API_BASE}/presets/brand`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(preset),
    });
    return res.json();
  },

  async getCaptionStyles(): Promise<CaptionStylePreset[]> {
    const res = await fetch(`${API_BASE}/presets/caption-style`);
    return res.json();
  },

  async createCaptionStyle(style: Partial<CaptionStylePreset>): Promise<any> {
    const res = await fetch(`${API_BASE}/presets/caption-style`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(style),
    });
    return res.json();
  },

  async getFonts(): Promise<string[]> {
    const res = await fetch(`${API_BASE}/fonts`);
    return res.json();
  },

  async uploadFont(file: File): Promise<{ font_name: string }> {
    const formData = new FormData();
    formData.append('file', file);
    const res = await fetch(`${API_BASE}/system/fonts`, {
      method: 'POST',
      body: formData,
    });
    return res.json();
  },

  async uploadAsset(file: File): Promise<{ url: string; filename: string; path: string }> {
    const formData = new FormData();
    formData.append('file', file);
    const res = await fetch(`${API_BASE}/assets/upload`, {
      method: 'POST',
      body: formData,
    });
    return res.json();
  },

  async listLuts(): Promise<Array<{ name: string; filename: string; path: string }>> {
    const res = await fetch(`${API_BASE}/system/luts`);
    if (!res.ok) return [];
    return res.json();
  },

  async uploadLut(file: File): Promise<{ name: string; filename: string; path: string; url: string }> {
    const formData = new FormData();
    formData.append('file', file);
    const res = await fetch(`${API_BASE}/system/luts`, {
      method: 'POST',
      body: formData,
    });
    return res.json();
  },

  async uploadBrandLogo(file: File): Promise<{ filename: string; url: string; path: string }> {
    const formData = new FormData();
    formData.append('file', file);
    const res = await fetch(`${API_BASE}/system/brand-logo`, {
      method: 'POST',
      body: formData,
    });
    return res.json();
  },

  async getPreviewFrame(params: {
    clip_id?: number;
    project_id?: number;
    timestamp: number;
    clip_rel_ts?: number;
    aspect_ratio?: string;
    custom_width?: number;
    custom_height?: number;
    frame_layout?: string;
    framing_template?: string;
    webcam_corner?: string;
    webcam_split_ratio?: number;
    pip_border_color?: string;
    source_webcam_region?: any;
    framing_override?: any;
    clip_size?: string;
    export_resolution?: string;
    watermark_path?: string;
    watermark_corner?: string;
    watermark_opacity?: number;
    watermark_size?: number;
    zoom?: number;
    pan_x_offset?: number;
    pan_x?: number;
    color_preset?: string;
    preset_intensity?: number;
    exposure?: number;
    brightness?: number;
    contrast?: number;
    lift_mids?: number;
    highlights?: number;
    shadows?: number;
    warmth?: number;
    saturation?: number;
    black_and_white?: number;
    vintage?: number;
    tint_color?: string;
    color_tint?: string;
    tint_strength?: number;
    glow?: number;
    smooth?: number;
    sharpness?: number;
    blur?: number;
    film_grain?: number;
    vignette?: number;
    bottom_fade?: number;
    color_temp?: number;
    tint_shift?: number;
    vibrance?: number;
    matte_fade?: number;
    halation_glow?: number;
    lut_path?: string;
    chroma_split?: number;
    letterbox?: string;
    speed_ramp?: number;
    bypass_color?: boolean;
    outro_cta_headline?: string;
    outro_cta_handle?: string;
    outro_logo_path?: string;
    burn_captions?: boolean;
    timing_offset?: number;
    caption_style?: string;
    style_preset?: string;
    caption_font?: string;
    font_family?: string;
    caption_animation?: string;
    font_size_percent?: number;
    font_size_pct?: number;
    base_color?: string;
    active_color?: string;
    highlight_color?: string;
    outline_color?: string;
    pill_color?: string;
    pacing_density?: string;
    text_case?: string;
    hold_back_alpha?: number;
    hold_back_opacity?: number;
    highlight_shape?: string;
    position_grid?: string;
    position_x?: number;
    pos_x?: number;
    position_y?: number;
    pos_y?: number;
    caption_x?: number;
    caption_y?: number;
    caption_rotation?: number;
    caption_max_width?: number;
    outline_width?: number;
    outline?: number;
    shadow_depth?: number;
    shadow?: number;
    glow_intensity?: number;
    letter_spacing?: number;
    bold?: boolean;
    italic?: boolean;
    all_caps?: boolean;
    uppercase?: boolean;
    text_align?: string;
    alignment?: string;
    emoji_enabled?: boolean;
    auto_emoji?: boolean;
    highlight_power_words?: boolean;
    how_phrase_arrives?: string;
    word_by_word?: boolean;
    clean_filler_words?: boolean;
    censor_profanity?: boolean;
    show_hook_banner?: boolean;
    hook_banner_enabled?: boolean;
    hook_headline?: string;
    retention_bar_enabled?: boolean;
    retention_bar_color?: string;
    outro_cta_enabled?: boolean;
    outro_cta_text?: string;
    words?: any[];
    keep_segments?: [number, number][];
    merge_crossfade_sec?: number;
    speakers?: Record<string, any>;
    speaker_lower_thirds?: boolean;
    face_safe_captions?: boolean;
    callout_cards?: any[];
    auto_callouts?: boolean;
    bilingual_enabled?: boolean;
    secondary_language?: string;
    backdrop_mode?: string;
    backdrop_blur_intensity?: number;
    backdrop_color?: string;
    camera_motion?: string;
    whip_pan_cuts?: boolean;
    depth_pop?: number;
    privacy_blur_boxes?: any[];
    beat_sync_enabled?: boolean;
    spectrum_enabled?: boolean;
    spectrum_position?: string;
    spectrum_color?: string;
    chapters_enabled?: boolean;
    chapters?: any[];
    handle_watermark_enabled?: boolean;
    handle_text?: string;
    handle_platform_icon?: string;
    handle_corner?: string;
    handle_opacity?: number;
    handle_display_mode?: string;
    use_cold_open?: boolean;
    cold_open_quote?: string;
    auto_trim_dead_air?: boolean;
    silence_threshold?: number;
    dynamic_punch_zoom?: boolean;
    punch_zoom_intensity?: number;
    options?: Record<string, any>;
  }): Promise<PreviewFrameResponse> {
    const res = await fetch(`${API_BASE}/preview-frame`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(params),
    });
    if (!res.ok) {
      const err = await res.json();
      throw new Error(err.detail || 'Preview frame generation failed');
    }
    return res.json();
  },

  getDownloadUrl(projectId: number, clipId: number): string {
    return `${API_BASE}/download/${projectId}/${clipId}`;
  },

  getNleExportUrl(projectId: number, clipId: number, format: 'fcpxml' | 'premiere' | 'srt' | 'zip'): string {
    return `${API_BASE}/clips/${projectId}/${clipId}/export-nle?format=${format}`;
  },

  async getBrollSuggestions(projectId: number, clipId: number): Promise<{ suggestions: any[] }> {
    const res = await fetch(`${API_BASE}/clips/${projectId}/${clipId}/broll-suggestions`);
    if (!res.ok) throw new Error('Failed to fetch B-Roll suggestions');
    return res.json();
  },

  async searchProjectTranscript(
    projectId: number,
    query: string = ''
  ): Promise<{ project_id: number; matches: any[]; total_words: number }> {
    const res = await fetch(`${API_BASE}/projects/${projectId}/transcript?q=${encodeURIComponent(query)}`);
    if (!res.ok) throw new Error('Failed to search project transcript');
    return res.json();
  },

  async createManualClip(
    projectId: number,
    payload: { start_time: number; end_time: number; title?: string }
  ): Promise<{ status: string; clip: ViralClip }> {
    const res = await fetch(`${API_BASE}/projects/${projectId}/clips/manual`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(payload),
    });
    if (!res.ok) {
      const err = await res.json().catch(() => ({ detail: 'Failed to create manual clip' }));
      throw new Error(err.detail || 'Failed to create manual clip');
    }
    return res.json();
  },

  async getProfiles(): Promise<any[]> {
    const res = await fetch(`${API_BASE}/profiles`);
    if (!res.ok) return [];
    return res.json();
  },

  async createProfile(payload: { name: string; description?: string; settings: Record<string, any> }): Promise<any> {
    const res = await fetch(`${API_BASE}/profiles`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(payload),
    });
    return res.json();
  },

  async deleteProfile(profileId: string): Promise<any> {
    const res = await fetch(`${API_BASE}/profiles/${profileId}`, { method: 'DELETE' });
    return res.json();
  },

  async getBatchQueue(): Promise<{ items: any[] }> {
    const res = await fetch(`${API_BASE}/batch/queue`);
    if (!res.ok) return { items: [] };
    return res.json();
  },

  async enqueueBatchUpload(
    files: File[],
    params: {
      aspect_ratio?: string;
      duration_preset?: string;
      clip_count?: number;
      topic_focus?: string;
      caption_language?: string;
      hardware_mode?: string;
    }
  ): Promise<{ enqueued: any[] }> {
    const enqueued: any[] = [];
    for (const file of files) {
      const fd = new FormData();
      fd.append('file', file);
      fd.append('aspect_ratio', params.aspect_ratio || '9:16');
      fd.append('duration_preset', params.duration_preset || 'Auto');
      fd.append('clip_count', String(params.clip_count || 5));
      if (params.topic_focus) fd.append('topic_focus', params.topic_focus);
      fd.append('caption_language', params.caption_language || 'As spoken');
      fd.append('hardware_mode', params.hardware_mode || 'gpu');
      fd.append('batch_queue', 'true');
      const res = await fetch(`${API_BASE}/ingest/upload`, { method: 'POST', body: fd });
      if (res.ok) {
        enqueued.push(await res.json());
      }
    }
    return { enqueued };
  },

  async scanWatchFolder(folderPath?: string): Promise<{ scanned: number; enqueued: any[]; error?: string }> {
    const res = await fetch(`${API_BASE}/batch/scan-watch-folder`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ folder_path: folderPath || '' }),
    });
    return res.json();
  },

  async getClipWaveform(
    projectId: number,
    clipId: number,
    bins: number = 150
  ): Promise<{
    bins: number[];
    rms?: number[];
    speech_density?: number[];
    is_silence?: boolean[];
    keep_segments?: [number, number][];
    merge_crossfade_sec?: number;
    window_start: number;
    window_end: number;
    clip_start: number;
    clip_end: number;
    video_duration: number;
  }> {
    const res = await fetch(`${API_BASE}/clips/${projectId}/${clipId}/waveform?bins=${bins}`);
    if (!res.ok) {
      const err = await res.json().catch(() => ({ detail: 'Failed to load clip waveform' }));
      throw new Error(err.detail || 'Failed to load clip waveform');
    }
    return res.json();
  },

  async generateClipHooks(
    projectId: number,
    clipId: number
  ): Promise<{ clip_id: number; project_id: number; variants: any[] }> {
    const res = await fetch(`${API_BASE}/clips/${projectId}/${clipId}/generate-hooks`, {
      method: 'POST',
    });
    if (!res.ok) {
      const err = await res.json().catch(() => ({ detail: 'Failed to generate AI hook variants' }));
      throw new Error(err.detail || 'Failed to generate AI hook variants');
    }
    return res.json();
  },

  async generateSocialCopy(
    projectId: number,
    clipId: number
  ): Promise<{ clip_id: number; project_id: number; platforms: Record<string, any> }> {
    const res = await fetch(`${API_BASE}/clips/${projectId}/${clipId}/generate-social-copy`, {
      method: 'POST',
    });
    if (!res.ok) {
      const err = await res.json().catch(() => ({ detail: 'Failed to generate platform social copy' }));
      throw new Error(err.detail || 'Failed to generate platform social copy');
    }
    return res.json();
  },

  async exportPublishPack(
    projectId: number,
    clipId: number,
    qualityMode: string = 'fast_preview'
  ): Promise<string> {
    const res = await fetch(`${API_BASE}/clips/${projectId}/${clipId}/export-publish-pack`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ quality_mode: qualityMode }),
    });
    if (!res.ok) {
      const err = await res.json().catch(() => ({ detail: 'Failed to build Publish Pack' }));
      throw new Error(err.detail || 'Failed to build Publish Pack');
    }
    const blob = await res.blob();
    const disp = res.headers.get('Content-Disposition') || '';
    const match = disp.match(/filename="?([^";\n]+)"?/i);
    const filename = match ? match[1] : `LostHeaven_PublishPack_Clip${clipId}.zip`;
    const url = URL.createObjectURL(blob);
    const a = document.createElement('a');
    a.href = url;
    a.download = filename;
    document.body.appendChild(a);
    a.click();
    a.remove();
    window.setTimeout(() => URL.revokeObjectURL(url), 15000);
    return filename;
  },

  async trimClip(
    projectId: number,
    clipId: number,
    startTime: number,
    endTime: number
  ): Promise<{ status: string; clip: ViralClip }> {
    const res = await fetch(`${API_BASE}/clips/${projectId}/${clipId}/trim`, {
      method: 'PATCH',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ start_time: startTime, end_time: endTime }),
    });
    if (!res.ok) {
      const err = await res.json().catch(() => ({ detail: 'Failed to trim clip' }));
      throw new Error(err.detail || 'Failed to trim clip');
    }
    return res.json();
  },

  async updateClipSegments(
    projectId: number,
    clipId: number,
    keepSegments: [number, number][],
    crossfadeSec?: number
  ): Promise<{ status: string; clip: ViralClip }> {
    const res = await fetch(`${API_BASE}/clips/${projectId}/${clipId}/segments`, {
      method: 'PATCH',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        keep_segments: keepSegments,
        ...(crossfadeSec !== undefined ? { crossfade_sec: crossfadeSec } : {}),
      }),
    });
    if (!res.ok) {
      const err = await res.json().catch(() => ({ detail: 'Failed to update clip segments' }));
      throw new Error(err.detail || 'Failed to update clip segments');
    }
    return res.json();
  },

  async mergeClips(
    projectId: number,
    payload: {
      clip_ids: number[];
      smooth_transition?: boolean;
      transition_duration?: number;
      title?: string;
    }
  ): Promise<{ status: string; clip: ViralClip }> {
    const res = await fetch(`${API_BASE}/projects/${projectId}/clips/merge`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(payload),
    });
    if (!res.ok) {
      const err = await res.json().catch(() => ({ detail: 'Failed to merge clips' }));
      throw new Error(err.detail || 'Failed to merge clips');
    }
    return res.json();
  },

  async getRetentionAudit(projectId: number, clipId: number): Promise<any> {
    const res = await fetch(`${API_BASE}/clips/${projectId}/${clipId}/retention-audit`);
    if (!res.ok) {
      const err = await res.json().catch(() => ({ detail: 'Failed to audit retention' }));
      throw new Error(err.detail || 'Failed to audit retention');
    }
    return res.json();
  },

  async diarizeClip(
    projectId: number,
    clipId: number,
    numSpeakers = 0
  ): Promise<{ status: string; speakers: Record<string, any>; words: any[]; speaker_count: number; clip: ViralClip }> {
    const res = await fetch(`${API_BASE}/clips/${projectId}/${clipId}/diarize`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ num_speakers: numSpeakers }),
    });
    if (!res.ok) {
      const err = await res.json().catch(() => ({ detail: 'Speaker diarization failed' }));
      throw new Error(err.detail || 'Speaker diarization failed');
    }
    return res.json();
  },

  async updateClipSpeakers(
    projectId: number,
    clipId: number,
    speakers: Record<string, any>,
    transcriptWords?: any[]
  ): Promise<{ status: string; clip: ViralClip }> {
    const res = await fetch(`${API_BASE}/clips/${projectId}/${clipId}/speakers`, {
      method: 'PATCH',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        speakers,
        ...(transcriptWords !== undefined ? { transcript_words: transcriptWords } : {}),
      }),
    });
    if (!res.ok) {
      const err = await res.json().catch(() => ({ detail: 'Failed to save speakers' }));
      throw new Error(err.detail || 'Failed to save speakers');
    }
    return res.json();
  },

  async extractCalloutCards(
    projectId: number,
    clipId: number,
    maxCards = 3
  ): Promise<{ status: string; callout_cards: any[]; clip: ViralClip }> {
    const res = await fetch(`${API_BASE}/clips/${projectId}/${clipId}/callouts`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ max_cards: maxCards }),
    });
    if (!res.ok) {
      const err = await res.json().catch(() => ({ detail: 'Failed to extract callouts' }));
      throw new Error(err.detail || 'Failed to extract callouts');
    }
    return res.json();
  },

  async translateClip(
    projectId: number,
    clipId: number,
    targetLanguage: string
  ): Promise<{ status: string; target_language: string; source: string; translations: string[]; clip: ViralClip }> {
    const res = await fetch(`${API_BASE}/clips/${projectId}/${clipId}/translate`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ target_language: targetLanguage }),
    });
    if (!res.ok) {
      const err = await res.json().catch(() => ({ detail: 'Translation failed' }));
      throw new Error(err.detail || 'Translation failed');
    }
    return res.json();
  },

  async dubClip(
    projectId: number,
    clipId: number,
    payload: { target_language: string; voice?: string; original_bed_volume?: number }
  ): Promise<{ status: string; dubbed_audio_path: string; tts_backend: string; segment_count: number; clip: ViralClip }> {
    const res = await fetch(`${API_BASE}/clips/${projectId}/${clipId}/dub`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(payload),
    });
    if (!res.ok) {
      const err = await res.json().catch(() => ({ detail: 'Voice dubbing failed' }));
      throw new Error(err.detail || 'Voice dubbing failed');
    }
    return res.json();
  },

  async compileTrailer(
    projectId: number,
    payload?: { title?: string; max_moments?: number; target_duration_sec?: number }
  ): Promise<{ status: string; clip: ViralClip }> {
    const res = await fetch(`${API_BASE}/projects/${projectId}/compile-trailer`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(payload || {}),
    });
    if (!res.ok) {
      const err = await res.json().catch(() => ({ detail: 'Trailer compilation failed' }));
      throw new Error(err.detail || 'Trailer compilation failed');
    }
    return res.json();
  },

  async generateThumbnail(
    projectId: number,
    clipId: number,
    payload: {
      timestamp?: number;
      headline?: string;
      format?: '9:16' | '16:9';
      depth_layer?: boolean;
      accent_color?: string;
      rim_glow?: boolean;
      badge_text?: string;
    }
  ): Promise<{
    status: string;
    thumbnail_url: string;
    jpg_url: string;
    width: number;
    height: number;
    timestamp: number;
    depth_layer: boolean;
  }> {
    const res = await fetch(`${API_BASE}/clips/${projectId}/${clipId}/generate-thumbnail`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(payload),
    });
    if (!res.ok) {
      const err = await res.json().catch(() => ({ detail: 'Thumbnail generation failed' }));
      throw new Error(err.detail || 'Thumbnail generation failed');
    }
    return res.json();
  },

  async generateChapters(
    projectId: number,
    clipId: number,
    maxChapters = 4,
    enable = true
  ): Promise<{
    status: string;
    clip_id: number;
    chapters_enabled: boolean;
    chapters: Array<{ id: string; start: number; end: number; title: string }>;
  }> {
    const res = await fetch(`${API_BASE}/clips/${projectId}/${clipId}/generate-chapters`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ max_chapters: maxChapters, enable }),
    });
    if (!res.ok) {
      const err = await res.json().catch(() => ({ detail: 'Chapter generation failed' }));
      throw new Error(err.detail || 'Chapter generation failed');
    }
    return res.json();
  },

  async getClipPreflight(projectId: number, clipId: number): Promise<{
    clip_id: number;
    readiness_score: number;
    status: 'pass' | 'warn' | 'risk';
    checks: Array<{
      id: string;
      title: string;
      status: 'pass' | 'warn' | 'fail';
      detail: string;
      fix_label?: string;
      fix_patch?: Record<string, any>;
    }>;
  }> {
    const res = await fetch(`${API_BASE}/clips/${projectId}/${clipId}/preflight`);
    if (!res.ok) {
      throw new Error('Failed to run preflight safety scan');
    }
    return res.json();
  },

  async importAnalyticsCsv(csvText: string): Promise<{
    status: string;
    imported_rows: number;
    matched_clips: number;
    formula: any;
  }> {
    const res = await fetch(`${API_BASE}/analytics/import-csv`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ csv_text: csvText }),
    });
    if (!res.ok) {
      const err = await res.json().catch(() => ({ detail: 'CSV import failed' }));
      throw new Error(err.detail || 'CSV import failed');
    }
    return res.json();
  },

  async getAnalyticsFormula(): Promise<{
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
  }> {
    const res = await fetch(`${API_BASE}/analytics/formula`);
    if (!res.ok) {
      throw new Error('Failed to fetch analytics formula');
    }
    return res.json();
  },

  async getWebcamRegion(
    projectId: number,
    clipId: number,
    refresh = false
  ): Promise<SourceWebcamRegion> {
    const qs = refresh ? '?refresh=true' : '';
    const res = await fetch(`${API_BASE}/clips/${projectId}/${clipId}/webcam-region${qs}`);
    if (!res.ok) {
      throw new Error('Failed to detect source webcam region');
    }
    return res.json();
  },

  async updateWebcamRegion(
    projectId: number,
    clipId: number,
    payload: {
      source_zone?: string;
      x?: number;
      y?: number;
      w?: number;
      h?: number;
      refresh?: boolean;
    }
  ): Promise<SourceWebcamRegion> {
    const res = await fetch(`${API_BASE}/clips/${projectId}/${clipId}/webcam-region`, {
      method: 'PATCH',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(payload)
    });
    if (!res.ok) {
      throw new Error('Failed to update source webcam region');
    }
    return res.json();
  },

  async getFramingMap(
    projectId: number,
    clipId: number,
    refresh = false
  ): Promise<FramingMapResponse> {
    const qs = refresh ? '?refresh=true' : '';
    const res = await fetch(`${API_BASE}/clips/${projectId}/${clipId}/framing-map${qs}`);
    if (!res.ok) {
      throw new Error('Failed to load clip framing map');
    }
    return res.json();
  },

  async updateFramingOverride(
    projectId: number,
    clipId: number,
    payload: {
      webcam_box?: { x: number; y: number; w: number; h: number } | null;
      focus_anchor?: { x: number; y: number } | null;
      lock_camera?: boolean;
      reset?: boolean;
    }
  ): Promise<{
    clip_id: number;
    project_id: number;
    framing_override: FramingOverride | null;
    source_webcam_region: SourceWebcamRegion | null;
    visual_map?: any;
  }> {
    const res = await fetch(`${API_BASE}/clips/${projectId}/${clipId}/framing-override`, {
      method: 'PATCH',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(payload)
    });
    if (!res.ok) {
      throw new Error('Failed to save framing override');
    }
    return res.json();
  },

  async autoFrameAll(projectId: number): Promise<{
    status: string;
    project_id: number;
    updated_count: number;
    clips: ViralClip[];
  }> {
    const res = await fetch(`${API_BASE}/projects/${projectId}/auto-frame-all`, {
      method: 'POST'
    });
    if (!res.ok) {
      const err = await res.json().catch(() => ({ detail: 'Smart Auto-Frame failed' }));
      throw new Error(err.detail || 'Smart Auto-Frame failed');
    }
    return res.json();
  },

  async getBestFrames(projectId: number, clipId: number): Promise<any> {
    const res = await fetch(`${API_BASE}/clips/${projectId}/${clipId}/best-frames`);
    if (!res.ok) {
      const err = await res.json().catch(() => ({ detail: 'Failed to load best frames & analysis' }));
      throw new Error(err.detail || 'Failed to load best frames & analysis');
    }
    return res.json();
  },

  async getClipPresetPreviews(
    projectId: number,
    clipId: number,
    lutPath?: string | null,
    force = false
  ): Promise<import('../types').PresetPreviewsResponse> {
    const params = new URLSearchParams();
    if (lutPath) params.set('lut_path', lutPath);
    if (force) params.set('force', 'true');
    const qs = params.toString() ? `?${params.toString()}` : '';
    const res = await fetch(`${API_BASE}/clips/${projectId}/${clipId}/preset-previews${qs}`);
    if (!res.ok) {
      const err = await res.json().catch(() => ({ detail: 'Failed to generate color preset previews' }));
      throw new Error(err.detail || 'Failed to generate color preset previews');
    }
    return res.json();
  },

  async reanalyzeClip(projectId: number, clipId: number): Promise<any> {
    const res = await fetch(`${API_BASE}/clips/${projectId}/${clipId}/reanalyze`, {
      method: 'POST'
    });
    if (!res.ok) {
      const err = await res.json().catch(() => ({ detail: 'Clip reanalysis failed' }));
      throw new Error(err.detail || 'Clip reanalysis failed');
    }
    return res.json();
  },

  subscribeToEvents(onMessage: (event: any) => void): () => void {
    const eventSource = new EventSource(`${API_BASE}/sse/events`);
    eventSource.onmessage = (e) => {
      try {
        const data = JSON.parse(e.data);
        onMessage(data);
      } catch (err) {
        console.error('SSE JSON parse error:', err);
      }
    };
    eventSource.onerror = (e) => {
      console.warn('SSE connection error:', e);
    };
    return () => {
      eventSource.close();
    };
  },
};
