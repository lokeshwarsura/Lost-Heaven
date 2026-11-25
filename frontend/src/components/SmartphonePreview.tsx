import React, { useRef, useState, useEffect } from 'react';
import {
  Play, Pause, RefreshCw, Eye, Smartphone, Heart, MessageCircle,
  Share2, Music, Volume2, VolumeX, Download, Zap, CheckCircle2, Loader2, X, Scissors,
  Sparkles, Package, Copy, Check
} from 'lucide-react';
import { ViralClip, PlatformCopyMap } from '../types';
import { api } from '../api/client';

const YouTubeIcon = ({ className = 'w-4 h-4' }: { className?: string }) => (
  <svg viewBox="0 0 24 24" fill="currentColor" className={className}>
    <path d="M23.498 6.186a3.016 3.016 0 0 0-2.122-2.136C19.505 3.545 12 3.545 12 3.545s-7.505 0-9.377.505A3.017 3.017 0 0 0 .502 6.186C0 8.07 0 12 0 12s0 3.93.502 5.814a3.016 3.016 0 0 0 2.122 2.136c1.871.505 9.376.505 9.376.505s7.505 0 9.377-.505a3.015 3.015 0 0 0 2.122-2.136C24 15.93 24 12 24 12s0-3.93-.502-5.814zM9.545 15.568V8.432L15.818 12l-6.273 3.568z" />
  </svg>
);

interface SmartphonePreviewProps {
  clip: ViralClip | null;
  videoUrl?: string;
  safeZoneGuide: string;
  onSafeZoneChange: (guide: string) => void;
  seekTime?: number;
  onPlaybackTimeChange?: (relativeSeconds: number) => void;
  onGenerateClips: () => void;
  onAutoRenderClip?: (
    projectId: number,
    clipId: number,
    signal: AbortSignal
  ) => Promise<{
    status: string;
    video_url?: string;
    preview_url?: string;
    rendered_path?: string;
  }>;
  onAutoRenderComplete?: (clipId: number, nextVideoUrl: string, renderedPath?: string) => void;
  onTrimClip?: (clipId: number, startTime: number, endTime: number) => void | Promise<void>;
  isRendering: boolean;
  renderProgress: number;
}

export const SmartphonePreview: React.FC<SmartphonePreviewProps> = ({
  clip,
  videoUrl,
  safeZoneGuide,
  onSafeZoneChange,
  seekTime,
  onPlaybackTimeChange,
  onGenerateClips,
  onAutoRenderClip,
  onAutoRenderComplete,
  onTrimClip,
  isRendering,
  renderProgress
}) => {
  const videoRef = useRef<HTMLVideoElement>(null);
  const stageRef = useRef<HTMLDivElement>(null);
  const prevClipIdRef = useRef<number | null>(null);
  const hasSwitchedClipRef = useRef<boolean>(false);
  const shouldAutoPlayRef = useRef<boolean>(false);
  const preparingClipIdRef = useRef<number | null>(null);
  const lastRenderedHashByClipRef = useRef<Record<number, string>>({});
  const debounceTimerRef = useRef<number | null>(null);
  const autoRenderAbortRef = useRef<AbortController | null>(null);
  const pendingRestoreRef = useRef<{ relTime: number; shouldPlay: boolean } | null>(null);
  const relCurrentRef = useRef<number>(0);

  const [autoRender, setAutoRender] = useState<boolean>(() => {
    try {
      const saved =
        localStorage.getItem('lost_heaven_auto_render') ??
        localStorage.getItem('clipship_auto_render');
      return saved === null ? true : saved === 'true';
    } catch {
      return true;
    }
  });
  const [isAutoRendering, setIsAutoRendering] = useState(false);
  const [isPreparingClip, setIsPreparingClip] = useState(false);
  const [videoVersion, setVideoVersion] = useState(0);

  const toggleAutoRender = () => {
    setAutoRender((prev) => {
      const next = !prev;
      try {
        localStorage.setItem('lost_heaven_auto_render', String(next));
      } catch {
        // ignore storage restrictions
      }
      return next;
    });
  };

  const [stageSize, setStageSize] = useState<{ w: number; h: number }>({ w: 280, h: 460 });
  const [isPlaying, setIsPlaying] = useState(false);
  const [isMuted, setIsMuted] = useState(false);
  const [currentTime, setCurrentTime] = useState(0);
  const [duration, setDuration] = useState(30);
  const [showExactFrame, setShowExactFrame] = useState(false);
  const [exactFrameUrl, setExactFrameUrl] = useState<string | null>(null);
  const [frameLatencyMs, setFrameLatencyMs] = useState<number | null>(null);
  const [isRenderingFrame, setIsRenderingFrame] = useState(false);

  // YouTube Publish Modal state
  const [isPublishModalOpen, setIsPublishModalOpen] = useState(false);
  const [publishTitle, setPublishTitle] = useState('Viral Video #Shorts');
  const [publishDesc, setPublishDesc] = useState('Created with Lost Heaven Studio! #Shorts');
  const [publishPrivacy, setPublishPrivacy] = useState<'private' | 'unlisted' | 'public'>('private');
  const [isPublishing, setIsPublishing] = useState(false);
  const [publishResult, setPublishResult] = useState<string | null>(null);
  const [isExportingPack, setIsExportingPack] = useState(false);
  const [packError, setPackError] = useState<string | null>(null);
  const [socialCopy, setSocialCopy] = useState<Partial<PlatformCopyMap> | null>(null);
  const [isGeneratingCopy, setIsGeneratingCopy] = useState(false);
  const [activeCopyPlatform, setActiveCopyPlatform] = useState<'youtube_shorts' | 'tiktok' | 'instagram_reels' | 'x_twitter'>('youtube_shorts');
  const [copiedField, setCopiedField] = useState<string | null>(null);

  // Interactive 150-bin Waveform & ±5s In/Out Trim State
  const waveTrackRef = useRef<HTMLDivElement>(null);
  const [waveformData, setWaveformData] = useState<{
    bins: number[];
    rms?: number[];
    speech_density?: number[];
    is_silence?: boolean[];
    window_start: number;
    window_end: number;
    clip_start: number;
    clip_end: number;
    video_duration: number;
  } | null>(null);
  const [trimStart, setTrimStart] = useState<number>(clip?.start_time ?? 0);
  const [trimEnd, setTrimEnd] = useState<number>(clip?.end_time ?? 30);
  const [draggingHandle, setDraggingHandle] = useState<'in' | 'out' | null>(null);
  const trimDraftRef = useRef<{ start: number; end: number }>({
    start: clip?.start_time ?? 0,
    end: clip?.end_time ?? 30
  });

  useEffect(() => {
    if (!clip?.id || !clip?.project_id) {
      setWaveformData(null);
      return;
    }
    setTrimStart(clip.start_time);
    setTrimEnd(clip.end_time);
    trimDraftRef.current = { start: clip.start_time, end: clip.end_time };
    let cancelled = false;
    api
      .getClipWaveform(clip.project_id, clip.id, 150)
      .then((data) => {
        if (!cancelled) {
          setWaveformData(data);
        }
      })
      .catch(() => {
        if (!cancelled) {
          setWaveformData(null);
        }
      });
    return () => {
      cancelled = true;
    };
  }, [clip?.id, clip?.project_id, clip?.start_time, clip?.end_time]);

  useEffect(() => {
    if (!draggingHandle || !clip || !waveformData) return;
    const handleMove = (e: PointerEvent) => {
      const el = waveTrackRef.current;
      if (!el) return;
      const rect = el.getBoundingClientRect();
      const ratio = Math.max(0, Math.min(1, (e.clientX - rect.left) / Math.max(1, rect.width)));
      const span = Math.max(1, waveformData.window_end - waveformData.window_start);
      const t = waveformData.window_start + ratio * span;
      if (draggingHandle === 'in') {
        const clamped = Math.max(waveformData.window_start, Math.min(trimDraftRef.current.end - 1.0, t));
        trimDraftRef.current.start = Number(clamped.toFixed(2));
        setTrimStart(trimDraftRef.current.start);
      } else {
        const clamped = Math.min(waveformData.window_end, Math.max(trimDraftRef.current.start + 1.0, t));
        trimDraftRef.current.end = Number(clamped.toFixed(2));
        setTrimEnd(trimDraftRef.current.end);
      }
    };
    const handleUp = () => {
      setDraggingHandle(null);
      const { start, end } = trimDraftRef.current;
      if (
        onTrimClip &&
        (Math.abs(start - clip.start_time) > 0.05 || Math.abs(end - clip.end_time) > 0.05)
      ) {
        onTrimClip(clip.id, start, end);
      }
    };
    window.addEventListener('pointermove', handleMove);
    window.addEventListener('pointerup', handleUp);
    return () => {
      window.removeEventListener('pointermove', handleMove);
      window.removeEventListener('pointerup', handleUp);
    };
  }, [draggingHandle, clip, waveformData, onTrimClip]);

  // Measure available preview stage so the chassis NEVER overflows width or height
  useEffect(() => {
    const el = stageRef.current;
    if (!el) return;
    const updateSize = () => {
      const rect = el.getBoundingClientRect();
      if (rect.width > 0 && rect.height > 0) {
        setStageSize({ w: Math.max(120, rect.width - 24), h: Math.max(120, rect.height - 24) });
      }
    };
    updateSize();
    const ro = new ResizeObserver(updateSize);
    ro.observe(el);
    return () => ro.disconnect();
  }, []);

  const resolvedUrl = clip?.video_url || videoUrl;
  const clipIsRendered = Boolean(
    clip &&
      (clip.status === 'ready' || clip.render_status === 'completed') &&
      (clip.rendered_path ||
        clip.rendered_video_path ||
        (resolvedUrl && !resolvedUrl.includes('/media/masters/')))
  );
  const activeVideoSrc = clipIsRendered ? resolvedUrl : undefined;

  useEffect(() => {
    if (seekTime !== undefined && videoRef.current && clip && clipIsRendered) {
      videoRef.current.currentTime = seekTime;
      setCurrentTime(seekTime);
      onPlaybackTimeChange?.(seekTime);
    }
  }, [seekTime]);

  // Force clean <video> reload & reset playback position whenever active clip or rendered URL changes
  useEffect(() => {
    if (!clip) {
      setIsPlaying(false);
      setCurrentTime(0);
      return;
    }

    setVideoVersion((v) => v + 1);
    setShowExactFrame(false);
    setExactFrameUrl(null);
    setDuration(clip.duration || 30);

    if (!pendingRestoreRef.current) {
      setCurrentTime(0);
      setIsPlaying(false);
    }
  }, [clip?.id, activeVideoSrc]);

  useEffect(() => {
    const vid = videoRef.current;
    if (vid && activeVideoSrc) {
      try {
        if (!pendingRestoreRef.current) {
          vid.currentTime = 0;
        }
        vid.load();
      } catch {
        // ignore pre-metadata load issues
      }
    }
  }, [clip?.id, activeVideoSrc, videoVersion]);

  const togglePlay = () => {
    if (!videoRef.current) return;
    if (isPlaying) {
      videoRef.current.pause();
      setIsPlaying(false);
    } else {
      setShowExactFrame(false);
      videoRef.current.play().catch(() => {});
      setIsPlaying(true);
    }
  };

  const toggleMute = () => {
    if (!videoRef.current) return;
    videoRef.current.muted = !isMuted;
    setIsMuted(!isMuted);
  };

  const isRenderedSource = clipIsRendered;
  const clipStartOffset = 0;
  const relCurrent = Math.max(0, Math.min(duration, currentTime - clipStartOffset));
  relCurrentRef.current = relCurrent;

  const cmdStateRef = useRef({
    clip,
    relCurrent,
    duration,
    onTrimClip,
    onPlaybackTimeChange,
  });
  cmdStateRef.current = { clip, relCurrent, duration, onTrimClip, onPlaybackTimeChange };

  // Keyboard shortcut event bridge from App.tsx (Space, Arrows, I/O, M)
  useEffect(() => {
    const onPlayerCmd = (ev: Event) => {
      const detail = (ev as CustomEvent<{ type: string; delta?: number }>).detail;
      if (!detail) return;
      const vid = videoRef.current;
      const st = cmdStateRef.current;
      if (detail.type === 'togglePlay' && vid) {
        if (vid.paused) {
          setShowExactFrame(false);
          vid.play().catch(() => {});
          setIsPlaying(true);
        } else {
          vid.pause();
          setIsPlaying(false);
        }
      } else if (detail.type === 'toggleMute' && vid) {
        const nextMuted = !vid.muted;
        vid.muted = nextMuted;
        setIsMuted(nextMuted);
      } else if (detail.type === 'seekDelta') {
        const cur = vid ? vid.currentTime : st.relCurrent;
        const maxDur =
          vid && Number.isFinite(vid.duration) && vid.duration > 0
            ? vid.duration
            : Math.max(1, st.duration);
        const nextT = Math.max(0, Math.min(maxDur, cur + (detail.delta || 0)));
        if (vid) vid.currentTime = nextT;
        setCurrentTime(nextT);
        st.onPlaybackTimeChange?.(nextT);
      } else if (detail.type === 'trimIn' && st.clip && st.onTrimClip) {
        const targetAbs = Math.max(
          0,
          Math.min(st.clip.end_time - 0.5, (st.clip.start_time || 0) + st.relCurrent)
        );
        const rounded = Number(targetAbs.toFixed(2));
        setTrimStart(rounded);
        st.onTrimClip(st.clip.id, rounded, st.clip.end_time);
      } else if (detail.type === 'trimOut' && st.clip && st.onTrimClip) {
        const targetAbs = Math.max(
          st.clip.start_time + 0.5,
          (st.clip.start_time || 0) + st.relCurrent
        );
        const rounded = Number(targetAbs.toFixed(2));
        setTrimEnd(rounded);
        st.onTrimClip(st.clip.id, st.clip.start_time, rounded);
      }
    };
    window.addEventListener('lh-player-cmd', onPlayerCmd);
    return () => window.removeEventListener('lh-player-cmd', onPlayerCmd);
  }, []);

  const handleTimeUpdate = () => {
    if (videoRef.current) {
      const t = videoRef.current.currentTime;
      setCurrentTime(t);
      onPlaybackTimeChange?.(Math.max(0, t - clipStartOffset));
    }
  };

  const handleScrubberChange = (e: React.ChangeEvent<HTMLInputElement>) => {
    const newRel = parseFloat(e.target.value);
    const absolute = clipStartOffset + newRel;
    setCurrentTime(absolute);
    if (videoRef.current) {
      videoRef.current.currentTime = absolute;
    }
    onPlaybackTimeChange?.(newRel);
  };

  const formatMMSS = (secs: number) => {
    const safe = Math.max(0, Math.floor(secs || 0));
    const m = Math.floor(safe / 60);
    const s = safe % 60;
    return `${String(m).padStart(2, '0')}:${String(s).padStart(2, '0')}`;
  };

  const fetchExactFrame = async () => {
    if (!clip) return;
    setIsRenderingFrame(true);
    try {
      const absTimestamp = (clip.start_time || 0) + relCurrent;
      const res = await api.getPreviewFrame({
        clip_id: clip.id,
        project_id: clip.project_id,
        timestamp: absTimestamp,
        clip_rel_ts: relCurrent,
        aspect_ratio: clip.aspect_ratio,
        custom_width: clip.custom_width,
        custom_height: clip.custom_height,
        frame_layout: clip.frame_layout,
        framing_template: clip.frame_layout,
        webcam_corner: clip.webcam_corner,
        webcam_split_ratio: clip.webcam_split_ratio,
        pip_border_color: clip.pip_border_color,
        clip_size: clip.clip_size,
        export_resolution: clip.clip_size,
        watermark_path: clip.watermark_path,
        watermark_corner: clip.watermark_corner,
        watermark_opacity: clip.watermark_opacity,
        watermark_size: clip.watermark_size,
        zoom: clip.zoom,
        pan_x_offset: clip.pan_x_offset,
        pan_x: Math.round((clip.pan_x_offset || 0) * 100),
        color_preset: clip.color_preset,
        exposure: clip.exposure,
        brightness: clip.brightness,
        contrast: clip.contrast,
        lift_mids: clip.lift_mids,
        warmth: clip.warmth,
        saturation: clip.saturation,
        black_and_white: clip.black_and_white,
        vintage: clip.vintage,
        tint_color: clip.tint_color,
        color_tint: clip.tint_color,
        tint_strength: clip.tint_strength,
        glow: clip.glow,
        smooth: clip.smooth,
        sharpness: clip.sharpness,
        blur: clip.blur,
        film_grain: clip.film_grain,
        vignette: typeof clip.vignette === 'number' ? clip.vignette : clip.vignette ? 1.0 : 0.0,
        bottom_fade: clip.bottom_fade,
        burn_captions: clip.burn_captions,
        timing_offset: clip.timing_offset,
        caption_style: clip.caption_style,
        style_preset: clip.caption_style,
        caption_font: clip.caption_font,
        font_family: clip.caption_font,
        caption_animation: clip.caption_animation,
        font_size_percent: clip.font_size_percent,
        font_size_pct: clip.font_size_percent,
        base_color: clip.base_color,
        active_color: clip.active_color,
        highlight_color: clip.active_color,
        outline_color: clip.outline_color,
        hold_back_alpha: clip.hold_back_alpha,
        hold_back_opacity: clip.hold_back_alpha,
        highlight_shape: clip.highlight_shape,
        position_grid: clip.position_grid,
        position_x: clip.position_x,
        pos_x: clip.position_x,
        position_y: clip.position_y,
        pos_y: clip.position_y,
        outline_width: clip.outline_width,
        outline: clip.outline_width,
        shadow_depth: clip.shadow_depth,
        shadow: clip.shadow_depth,
        glow_intensity: clip.glow_intensity,
        letter_spacing: clip.letter_spacing,
        bold: clip.bold,
        italic: clip.italic,
        all_caps: clip.all_caps,
        uppercase: clip.all_caps,
        text_align: clip.text_align,
        alignment: clip.text_align,
        emoji_enabled: clip.emoji_enabled,
        auto_emoji: clip.emoji_enabled,
        highlight_power_words: Boolean(clip.highlight_power_words),
        how_phrase_arrives: clip.how_phrase_arrives,
        word_by_word: (clip.how_phrase_arrives || '').toLowerCase().replace('-', ' ') === 'word by word',
        clean_filler_words: clip.clean_filler_words,
        censor_profanity: clip.censor_profanity,
        show_hook_banner: clip.show_hook_banner,
        hook_banner_enabled: clip.show_hook_banner,
        hook_headline: clip.hook_headline,
        retention_bar_enabled: clip.retention_bar_enabled,
        retention_bar_color: clip.retention_bar_color,
        outro_cta_enabled: clip.outro_cta_enabled,
        outro_cta_text: clip.outro_cta_text,
        words: clip.transcript_words
      });
      setExactFrameUrl(res.frame_url);
      setFrameLatencyMs(res.render_time_ms);
      setShowExactFrame(true);
    } catch (err) {
      console.error('Failed to render exact frame:', err);
    } finally {
      setIsRenderingFrame(false);
    }
  };

  const previewConfigSig = clip
    ? JSON.stringify({
        aspect_ratio: clip.aspect_ratio,
        custom_width: clip.custom_width,
        custom_height: clip.custom_height,
        frame_layout: clip.frame_layout,
        webcam_corner: clip.webcam_corner,
        zoom: clip.zoom,
        pan_x_offset: clip.pan_x_offset,
        color_preset: clip.color_preset,
        exposure: clip.exposure,
        brightness: clip.brightness,
        contrast: clip.contrast,
        lift_mids: clip.lift_mids,
        warmth: clip.warmth,
        saturation: clip.saturation,
        black_and_white: clip.black_and_white,
        vintage: clip.vintage,
        tint_color: clip.tint_color,
        color_tint: clip.tint_color,
        tint_strength: clip.tint_strength,
        glow: clip.glow,
        smooth: clip.smooth,
        sharpness: clip.sharpness,
        blur: clip.blur,
        film_grain: clip.film_grain,
        vignette: clip.vignette,
        bottom_fade: clip.bottom_fade,
        burn_captions: clip.burn_captions,
        timing_offset: clip.timing_offset,
        caption_style: clip.caption_style,
        caption_font: clip.caption_font,
        caption_animation: clip.caption_animation,
        font_size_percent: clip.font_size_percent,
        base_color: clip.base_color,
        active_color: clip.active_color,
        outline_color: clip.outline_color,
        hold_back_alpha: clip.hold_back_alpha,
        highlight_shape: clip.highlight_shape,
        position_grid: clip.position_grid,
        position_x: clip.position_x,
        position_y: clip.position_y,
        outline_width: clip.outline_width,
        shadow_depth: clip.shadow_depth,
        glow_intensity: clip.glow_intensity,
        letter_spacing: clip.letter_spacing,
        bold: clip.bold,
        italic: clip.italic,
        all_caps: clip.all_caps,
        text_align: clip.text_align,
        emoji_enabled: clip.emoji_enabled,
        highlight_power_words: clip.highlight_power_words,
        how_phrase_arrives: clip.how_phrase_arrives,
        clean_filler_words: clip.clean_filler_words,
        censor_profanity: clip.censor_profanity,
        tighten_pacing: clip.tighten_pacing,
        silence_threshold: clip.silence_threshold,
        start_time: clip.start_time,
        end_time: clip.end_time,
        show_hook_banner: clip.show_hook_banner,
        hook_headline: clip.hook_headline,
        transcript_words: clip.transcript_words,
        clip_size: clip.clip_size,
        watermark_path: clip.watermark_path,
        watermark_corner: clip.watermark_corner,
        watermark_opacity: clip.watermark_opacity,
        bgm_path: clip.bgm_path,
        bgm_volume: clip.bgm_volume,
        audio_volume: clip.audio_volume,
        normalize_audio: clip.normalize_audio,
        vocal_clarity: clip.vocal_clarity,
        voice_isolation: clip.voice_isolation,
        auto_sfx: clip.auto_sfx,
        dynamic_punch_zoom: clip.dynamic_punch_zoom,
        punch_zoom_intensity: clip.punch_zoom_intensity,
        retention_bar_enabled: clip.retention_bar_enabled,
        retention_bar_color: clip.retention_bar_color,
        outro_cta_enabled: clip.outro_cta_enabled,
        outro_cta_text: clip.outro_cta_text,
        broll_segments: clip.broll_segments
      })
    : '';

  useEffect(() => {
    if (!clip) return;

    const triggerFastPreviewRender = (
      targetClipId: number,
      targetProjectId: number,
      dispatchedSig: string,
      isInitialPrepare: boolean
    ) => {
      if (!onAutoRenderClip) return;
      if (autoRenderAbortRef.current) {
        autoRenderAbortRef.current.abort();
        autoRenderAbortRef.current = null;
      }

      const controller = new AbortController();
      autoRenderAbortRef.current = controller;
      if (isInitialPrepare) {
        preparingClipIdRef.current = targetClipId;
        setIsPreparingClip(true);
      }
      setIsAutoRendering(true);

      onAutoRenderClip(targetProjectId, targetClipId, controller.signal)
        .then((res) => {
          if (controller.signal.aborted || res.status === 'superseded') return;
          if (res.status === 'completed') {
            lastRenderedHashByClipRef.current[targetClipId] = dispatchedSig;
            const nextUrl = res.video_url || res.preview_url;
            if (nextUrl) {
              if (isInitialPrepare) {
                pendingRestoreRef.current = null;
                shouldAutoPlayRef.current = true;
              } else {
                const shouldResume = Boolean(videoRef.current && !videoRef.current.paused);
                pendingRestoreRef.current = {
                  relTime: relCurrentRef.current,
                  shouldPlay: shouldResume
                };
              }
              setShowExactFrame(false);
              onAutoRenderComplete?.(targetClipId, nextUrl, res.rendered_path);
            }
          }
        })
        .catch((err: any) => {
          if (err?.name !== 'AbortError') {
            console.error('Auto-render failed:', err);
          }
        })
        .finally(() => {
          if (preparingClipIdRef.current === targetClipId) {
            preparingClipIdRef.current = null;
            setIsPreparingClip(false);
          }
          if (autoRenderAbortRef.current === controller) {
            setIsAutoRendering(false);
            autoRenderAbortRef.current = null;
          }
        });
    };

    if (prevClipIdRef.current !== clip.id) {
      const switchedFromAnother = prevClipIdRef.current !== null || hasSwitchedClipRef.current;
      prevClipIdRef.current = clip.id;
      hasSwitchedClipRef.current = true;
      pendingRestoreRef.current = null;

      if (debounceTimerRef.current !== null) {
        window.clearTimeout(debounceTimerRef.current);
        debounceTimerRef.current = null;
      }
      if (autoRenderAbortRef.current) {
        autoRenderAbortRef.current.abort();
        autoRenderAbortRef.current = null;
      }
      preparingClipIdRef.current = null;
      setShowExactFrame(false);
      setExactFrameUrl(null);
      setIsAutoRendering(false);

      if (clipIsRendered) {
        lastRenderedHashByClipRef.current[clip.id] = previewConfigSig;
        setIsPreparingClip(false);
        shouldAutoPlayRef.current = switchedFromAnother;
        return;
      }

      // Clip is not rendered yet: immediately fire fast_preview so clicking any clip plays cleanly
      if (onAutoRenderClip) {
        shouldAutoPlayRef.current = true;
        triggerFastPreviewRender(clip.id, clip.project_id, previewConfigSig, true);
      } else {
        setIsPreparingClip(false);
      }
      return;
    }

    // Do not queue edit debounce while the initial unrendered clip preview is still preparing
    if (preparingClipIdRef.current === clip.id) return;

    if (lastRenderedHashByClipRef.current[clip.id] === previewConfigSig) return;

    if (debounceTimerRef.current !== null) {
      window.clearTimeout(debounceTimerRef.current);
    }

    if (autoRender && onAutoRenderClip) {
      const dispatchedSig = previewConfigSig;
      const targetClipId = clip.id;
      const targetProjectId = clip.project_id;

      debounceTimerRef.current = window.setTimeout(() => {
        triggerFastPreviewRender(targetClipId, targetProjectId, dispatchedSig, false);
      }, 900);

      return () => {
        if (debounceTimerRef.current !== null) {
          window.clearTimeout(debounceTimerRef.current);
        }
      };
    }

    if (isPlaying) return;
    debounceTimerRef.current = window.setTimeout(() => {
      void fetchExactFrame();
    }, 350);

    return () => {
      if (debounceTimerRef.current !== null) {
        window.clearTimeout(debounceTimerRef.current);
      }
    };
  }, [clip?.id, previewConfigSig, autoRender, isPlaying, clipIsRendered]);

  // Safe-Zone options mapped cleanly to existing backend values
  const safeZones = [
    { value: 'No guide', label: 'None' },
    { value: 'TikTok', label: 'TikTok' },
    { value: 'Instagram', label: 'Reels' },
    { value: 'YT Shorts', label: 'Shorts' },
    { value: 'All', label: 'All' }
  ];

  // Exact aspect ratio math for guaranteed zero-overflow sizing
  const getAspectNumeric = (ar?: string) => {
    switch (ar) {
      case '16:9':
        return 16 / 9;
      case '1:1':
        return 1;
      case '4:5':
        return 4 / 5;
      case '4:3':
        return 4 / 3;
      case '9:16':
      default:
        return 9 / 16;
    }
  };

  const ratio = getAspectNumeric(clip?.aspect_ratio);
  let frameWidth = stageSize.w;
  let frameHeight = frameWidth / ratio;
  if (frameHeight > stageSize.h) {
    frameHeight = stageSize.h;
    frameWidth = frameHeight * ratio;
  }

  const isCompleted = Boolean(
    clip && (clip.status === 'ready' || clip.render_status === 'completed')
  );
  const downloadUrl = clip ? api.getDownloadUrl(clip.project_id, clip.id) : '#';

  const videoFilterAlreadyBaked = Boolean(
    clip && isRenderedSource && lastRenderedHashByClipRef.current[clip.id] === previewConfigSig
  );

  const videoCssFilter =
    clip && !videoFilterAlreadyBaked
      ? (() => {
          const b = Math.max(0.25, 1 + (clip.brightness || 0) + (clip.exposure || 0) * 0.65);
          const c = Math.max(0.3, clip.contrast || 1.0);
          const bw = Math.max(0, Math.min(1, clip.black_and_white || 0));
          const s = Math.max(0, (clip.saturation ?? 1.0) * (1 - bw));
          const sep = Math.max(0, Math.min(1, (clip.vintage || 0) * 0.65));
          const bl = Math.max(0, (clip.blur || 0) * 6);
          const wHue = (clip.warmth || 0) * -22;
          const parts = [
            `brightness(${b.toFixed(2)})`,
            `contrast(${c.toFixed(2)})`,
            `saturate(${s.toFixed(2)})`,
          ];
          if (bw > 0.01) parts.push(`grayscale(${bw.toFixed(2)})`);
          if (sep > 0.01) parts.push(`sepia(${sep.toFixed(2)})`);
          if (Math.abs(wHue) > 0.5) parts.push(`hue-rotate(${wHue.toFixed(1)}deg)`);
          if (bl > 0.05) parts.push(`blur(${bl.toFixed(1)}px)`);
          return parts.join(' ');
        })()
      : 'none';

  const handleOpenPublish = () => {
    if (clip) {
      setPublishTitle(clip.selected_title || clip.youtube_titles?.[0] || 'Viral Video #Shorts');
      const baseDesc = (clip.youtube_description || 'Created with Lost Heaven Studio! #Shorts').trim();
      const extraTags = (clip.hashtags || '').trim();
      setPublishDesc(extraTags && !baseDesc.includes(extraTags) ? `${baseDesc}\n\n${extraTags}` : baseDesc);
      setPublishPrivacy(clip.privacy_status || 'private');
      setPublishResult(null);
      setSocialCopy(null);
    }
    setIsPublishModalOpen(true);
  };

  const handleExportPublishPack = async () => {
    if (!clip) return;
    setIsExportingPack(true);
    setPackError(null);
    try {
      await api.exportPublishPack(clip.project_id, clip.id, 'fast_preview');
    } catch (err: any) {
      setPackError(err?.message || 'Failed to build Publish Pack .zip');
    } finally {
      setIsExportingPack(false);
    }
  };

  const handleGenerateSocialCopy = async () => {
    if (!clip) return;
    setIsGeneratingCopy(true);
    try {
      const res = await api.generateSocialCopy(clip.project_id, clip.id);
      const plats = res?.platforms as Partial<PlatformCopyMap> | undefined;
      if (plats) {
        setSocialCopy(plats);
        const cur = plats[activeCopyPlatform] || plats.youtube_shorts;
        if (cur && activeCopyPlatform === 'youtube_shorts') {
          if (cur.title) setPublishTitle(cur.title);
          const tags = Array.isArray(cur.hashtags) ? cur.hashtags.join(' ') : '';
          setPublishDesc([cur.description || '', tags].filter(Boolean).join('\n\n'));
        }
      }
    } catch (err: any) {
      setPublishResult(`Error: ${err?.message || 'Failed to generate copy'}`);
    } finally {
      setIsGeneratingCopy(false);
    }
  };

  const handleCopyText = (key: string, text: string) => {
    navigator.clipboard?.writeText(text).catch(() => {});
    setCopiedField(key);
    window.dispatchEvent(
      new CustomEvent('lh-toast', {
        detail: { message: 'Copied social copy & hashtags to clipboard', type: 'success' },
      })
    );
    window.setTimeout(() => setCopiedField(null), 1500);
  };

  const handlePublishSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!clip) return;
    setIsPublishing(true);
    setPublishResult(null);
    try {
      const res = await api.publishToYouTube(clip.id, {
        title: publishTitle,
        description: publishDesc,
        privacy_status: publishPrivacy
      });
      setPublishResult(res.url || 'Published successfully!');
    } catch (err: any) {
      setPublishResult(`Error: ${err.message || err}`);
    } finally {
      setIsPublishing(false);
    }
  };

  return (
    <aside className="w-[340px] xl:w-[390px] shrink-0 h-full min-h-0 box-border overflow-hidden border-l border-[var(--border-subtle)] bg-[var(--bg-surface)] flex flex-col select-none">
      {/* Top Control Strip: Safe-Zone Pills + Auto-render + Exact Frame */}
      <div className="px-3 py-2 border-b border-[var(--border-subtle)] flex items-center justify-between gap-1.5 shrink-0">
        <div className="flex items-center gap-0.5 bg-[var(--bg-input)] p-0.5 rounded-lg border border-[var(--border-subtle)] min-w-0">
          {safeZones.map((g) => (
            <button
              key={g.value}
              type="button"
              onClick={() => onSafeZoneChange(g.value)}
              className={`px-1.5 py-1 rounded-md text-[10px] font-semibold transition whitespace-nowrap ${
                safeZoneGuide === g.value
                  ? 'bg-[var(--accent-primary)] text-[var(--accent-foreground)]'
                  : 'text-[var(--text-secondary)] hover:text-[var(--text-primary)]'
              }`}
            >
              {g.label}
            </button>
          ))}
        </div>

        <div className="flex items-center gap-1 shrink-0">
          <button
            type="button"
            onClick={toggleAutoRender}
            className={`flex items-center gap-1 px-2 py-1 rounded-lg text-[10px] font-semibold border transition whitespace-nowrap ${
              autoRender
                ? 'bg-[var(--accent-subtle)] border-[var(--accent-primary)] text-[var(--accent-primary)]'
                : 'bg-[var(--bg-card)] border-[var(--border-subtle)] text-[var(--text-secondary)] hover:text-[var(--text-primary)]'
            }`}
            title="Automatically render fast preview MP4 when edits change (900ms debounce)"
          >
            {isAutoRendering ? (
              <Loader2 className="w-3 h-3 animate-spin" />
            ) : (
              <Zap className="w-3 h-3" />
            )}
            <span>{isAutoRendering ? 'Syncing' : 'Auto-render'}</span>
          </button>

          <button
            type="button"
            onClick={() => {
              if (!showExactFrame) fetchExactFrame();
              else setShowExactFrame(false);
            }}
            disabled={isRenderingFrame || !clip}
            className={`flex items-center gap-1 px-2 py-1 rounded-lg text-[10px] font-semibold border transition shrink-0 tabular-nums whitespace-nowrap ${
              showExactFrame
                ? 'bg-[var(--accent-subtle)] border-[var(--accent-primary)] text-[var(--accent-primary)]'
                : 'bg-[var(--bg-card)] border-[var(--border-subtle)] text-[var(--text-secondary)] hover:text-[var(--text-primary)]'
            }`}
            title="Render single exact graded FFmpeg frame"
          >
            {isRenderingFrame ? (
              <RefreshCw className="w-3 h-3 animate-spin text-[var(--accent-primary)]" />
            ) : (
              <Eye className="w-3 h-3" />
            )}
            <span>
              {isRenderingFrame
                ? 'Frame'
                : showExactFrame
                ? `Live${frameLatencyMs !== null ? ` ${frameLatencyMs}ms` : ''}`
                : 'Frame'}
            </span>
          </button>
        </div>
      </div>

      {/* Auto-Scaled Preview Stage */}
      <div
        ref={stageRef}
        className="flex-1 min-h-0 flex items-center justify-center p-3 overflow-hidden bg-[var(--bg-canvas)]"
      >
        <div
          style={{ width: `${Math.floor(frameWidth)}px`, height: `${Math.floor(frameHeight)}px` }}
          className="relative bg-black rounded-[24px] p-1.5 border-2 border-[var(--border-subtle)] shadow-xl flex flex-col overflow-hidden transition-all duration-200"
        >
          <div className="relative w-full h-full rounded-[18px] overflow-hidden bg-[#0A0A0A] flex items-center justify-center">
            {showExactFrame && exactFrameUrl ? (
              <img
                src={exactFrameUrl}
                alt="Exact FFmpeg Graded Frame"
                className="w-full h-full object-cover"
              />
            ) : activeVideoSrc ? (
              <video
                key={`${clip?.project_id ?? 0}-${clip?.id ?? 0}-${videoVersion}`}
                ref={videoRef}
                src={activeVideoSrc}
                muted={isMuted}
                preload="auto"
                onPlay={() => setIsPlaying(true)}
                onPause={() => setIsPlaying(false)}
                onTimeUpdate={handleTimeUpdate}
                onLoadedMetadata={() => {
                  const vid = videoRef.current;
                  if (!vid) return;
                  if (Number.isFinite(vid.duration) && vid.duration > 0) {
                    setDuration(vid.duration);
                  }
                  const restore = pendingRestoreRef.current;
                  if (restore) {
                    vid.currentTime = restore.relTime;
                    setCurrentTime(restore.relTime);
                    if (restore.shouldPlay) {
                      vid.play().catch(() => {});
                    }
                    pendingRestoreRef.current = null;
                  } else {
                    vid.currentTime = 0;
                    setCurrentTime(0);
                    if (shouldAutoPlayRef.current) {
                      shouldAutoPlayRef.current = false;
                      vid.play().catch(() => {});
                    }
                  }
                }}
                onClick={togglePlay}
                style={{ filter: videoCssFilter }}
                className="w-full h-full object-cover cursor-pointer transition-[filter] duration-75"
                playsInline
                loop
              />
            ) : (
              <div className="flex flex-col items-center justify-center text-zinc-500 p-4 text-center">
                {isPreparingClip ? (
                  <>
                    <Loader2 className="w-8 h-8 mb-2 animate-spin text-[var(--accent-primary)]" />
                    <span className="text-xs font-semibold text-white">Preparing preview...</span>
                    <span className="text-[10px] text-zinc-400 mt-1">
                      Rendering fast MP4 preview
                    </span>
                  </>
                ) : (
                  <>
                    <Smartphone className="w-10 h-10 mb-2 stroke-1 opacity-70" />
                    <span className="text-xs font-medium">No video preview</span>
                    <span className="text-[10px] opacity-70 mt-1">Select a clip or render MP4</span>
                  </>
                )}
              </div>
            )}

            {isPreparingClip && activeVideoSrc && (
              <div className="absolute inset-0 z-30 bg-black/65 backdrop-blur-[2px] flex flex-col items-center justify-center p-4 text-center pointer-events-none">
                <Loader2 className="w-7 h-7 mb-2 animate-spin text-[var(--accent-primary)]" />
                <span className="text-xs font-semibold text-white">Preparing preview...</span>
              </div>
            )}

            {/* Top Hook Banner Overlay */}
            {clip?.show_hook_banner && clip?.hook_headline && (
              <div className="absolute top-6 left-3 right-3 z-20 pointer-events-none flex justify-center">
                <div className="px-2.5 py-1 rounded-lg bg-black/80 border border-yellow-400 text-yellow-300 font-black text-[10px] uppercase tracking-wide shadow text-center leading-tight">
                  ★ {clip.hook_headline} ★
                </div>
              </div>
            )}

            {/* Platform Safe-Zone Overlay */}
            {safeZoneGuide !== 'No guide' && (
              <div className="absolute inset-0 pointer-events-none z-10 flex flex-col justify-between p-3">
                <div className="w-full border-t border-dashed border-[var(--accent-primary)]/60 pt-0.5 text-[9px] text-[var(--accent-primary)] font-mono">
                  {safeZoneGuide} Safe Top
                </div>

                {(safeZoneGuide === 'TikTok' || safeZoneGuide === 'YT Shorts' || safeZoneGuide === 'All') && (
                  <div className="absolute right-2 bottom-16 flex flex-col items-center gap-2.5 text-white/80">
                    <div className="flex flex-col items-center">
                      <div className="w-7 h-7 rounded-full bg-black/45 flex items-center justify-center border border-white/20">
                        <Heart className="w-3.5 h-3.5 text-white" />
                      </div>
                      <span className="text-[8px] font-mono mt-0.5">84K</span>
                    </div>
                    <div className="flex flex-col items-center">
                      <div className="w-7 h-7 rounded-full bg-black/45 flex items-center justify-center border border-white/20">
                        <MessageCircle className="w-3.5 h-3.5 text-white" />
                      </div>
                      <span className="text-[8px] font-mono mt-0.5">1.2K</span>
                    </div>
                    <div className="flex flex-col items-center">
                      <div className="w-7 h-7 rounded-full bg-black/45 flex items-center justify-center border border-white/20">
                        <Share2 className="w-3.5 h-3.5 text-white" />
                      </div>
                      <span className="text-[8px] font-mono mt-0.5">Share</span>
                    </div>
                    <div className="w-6 h-6 rounded-full bg-black/45 flex items-center justify-center border border-white/20">
                      <Music className="w-3 h-3 text-white" />
                    </div>
                  </div>
                )}

                <div className="w-full border-b border-dashed border-[var(--accent-primary)]/60 pb-1 text-[9px] text-[var(--accent-primary)] font-mono">
                  Caption Safe Zone
                </div>
              </div>
            )}
          </div>
        </div>
      </div>

      {/* Integrated Transport Bar */}
      <div className="px-3.5 py-2 border-t border-[var(--border-subtle)] bg-[var(--bg-surface)] flex items-center gap-2.5 shrink-0">
        <button
          type="button"
          onClick={togglePlay}
          disabled={!activeVideoSrc}
          className="w-7 h-7 rounded-lg bg-[var(--bg-card)] hover:border-[var(--accent-primary)]/50 text-[var(--accent-primary)] border border-[var(--border-subtle)] flex items-center justify-center transition shrink-0 disabled:opacity-40"
          title={isPlaying ? 'Pause' : 'Play'}
        >
          {isPlaying ? <Pause className="w-3.5 h-3.5 fill-current" /> : <Play className="w-3.5 h-3.5 fill-current" />}
        </button>

        <input
          type="range"
          min={0}
          max={Math.max(1, duration)}
          step={0.1}
          value={relCurrent}
          onChange={handleScrubberChange}
          disabled={!activeVideoSrc}
          className="flex-1 min-w-0 h-1 bg-[var(--bg-input)] rounded appearance-none cursor-pointer"
        />

        <span className="text-[11px] font-mono text-[var(--text-secondary)] shrink-0 tabular-nums">
          {formatMMSS(relCurrent)} / {formatMMSS(duration)}
        </span>

        <button
          type="button"
          onClick={toggleMute}
          disabled={!activeVideoSrc}
          className="w-7 h-7 rounded-lg bg-[var(--bg-card)] hover:text-[var(--text-primary)] text-[var(--text-secondary)] border border-[var(--border-subtle)] flex items-center justify-center transition shrink-0 disabled:opacity-40"
          title={isMuted ? 'Unmute' : 'Mute'}
        >
          {isMuted ? <VolumeX className="w-3.5 h-3.5" /> : <Volume2 className="w-3.5 h-3.5" />}
        </button>
      </div>

      {/* Interactive 150-Bin RMS Audio Waveform & ±5s In/Out Trim Bar */}
      {clip && waveformData && waveformData.bins.length > 0 && (() => {
        const span = Math.max(0.5, waveformData.window_end - waveformData.window_start);
        const inPct = Math.max(0, Math.min(98, ((trimStart - waveformData.window_start) / span) * 100));
        const outPct = Math.max(inPct + 2, Math.min(100, ((trimEnd - waveformData.window_start) / span) * 100));
        const playAbs = (clip.start_time || 0) + relCurrent;
        const playPct = Math.max(0, Math.min(100, ((playAbs - waveformData.window_start) / span) * 100));
        return (
          <div className="px-3.5 py-2 border-t border-[var(--border-subtle)] bg-[var(--bg-surface)]/80 space-y-1 shrink-0">
            <div className="flex items-center justify-between text-[10px] font-mono text-[var(--text-secondary)] tabular-nums">
              <span className="flex items-center gap-1 font-semibold text-[var(--text-primary)]">
                <Scissors className="w-2.5 h-2.5 text-[var(--accent-primary)]" />
                <span>In {trimStart.toFixed(1)}s</span>
              </span>
              <span className="flex items-center gap-2">
                <span className="text-[var(--accent-primary)] font-bold">
                  {(trimEnd - trimStart).toFixed(1)}s
                </span>
                <span className="hidden xl:inline-flex items-center gap-1 text-[9px] text-[var(--text-secondary)]">
                  <span className="inline-block w-1.5 h-1.5 rounded-full bg-amber-400" />Peak
                  <span className="inline-block w-1.5 h-1.5 rounded-full bg-red-400 ml-1" />Silence
                </span>
              </span>
              <span>Out {trimEnd.toFixed(1)}s</span>
            </div>

            <div
              ref={waveTrackRef}
              onClick={(e) => {
                if (draggingHandle) return;
                const el = waveTrackRef.current;
                if (!el) return;
                const rect = el.getBoundingClientRect();
                const ratio = Math.max(0, Math.min(1, (e.clientX - rect.left) / Math.max(1, rect.width)));
                const clickedAbs = waveformData.window_start + ratio * span;
                const nextRel = Math.max(0, Math.min(duration, clickedAbs - (clip.start_time || 0)));
                setCurrentTime(nextRel);
                if (videoRef.current) videoRef.current.currentTime = nextRel;
                onPlaybackTimeChange?.(nextRel);
              }}
              className="relative h-8 w-full rounded-lg bg-[var(--bg-input)] border border-[var(--border-subtle)] overflow-hidden select-none touch-none cursor-pointer"
              title="Click waveform to seek • Drag handles to trim ±5s"
            >
              {/* 150-bin Heatmap SVG Waveform */}
              <svg
                viewBox="0 0 150 32"
                preserveAspectRatio="none"
                className="w-full h-full block pointer-events-none"
              >
                {waveformData.bins.map((amp, i) => {
                  const barPct = ((i + 0.5) / waveformData.bins.length) * 100;
                  const inside = barPct >= inPct && barPct <= outPct;
                  const rms = waveformData.rms?.[i] ?? amp;
                  const density = waveformData.speech_density?.[i] ?? 0;
                  const silent = Boolean(waveformData.is_silence?.[i]);

                  let fillColor = 'var(--text-secondary)';
                  let fillOpacity = 0.22;
                  if (inside) {
                    if (silent) {
                      fillColor = '#EF4444';
                      fillOpacity = 0.52;
                    } else if (rms >= 0.72 || (rms >= 0.5 && density >= 0.5)) {
                      fillColor = '#F59E0B';
                      fillOpacity = 0.92;
                    } else if (density >= 0.35 || rms >= 0.4) {
                      fillColor = '#10B981';
                      fillOpacity = 0.85;
                    } else {
                      fillColor = 'var(--accent-primary)';
                      fillOpacity = 0.78;
                    }
                  }

                  const h = Math.max(2, Math.round(amp * 28));
                  const y = Math.round((32 - h) / 2);
                  return (
                    <rect
                      key={i}
                      x={i + 0.12}
                      y={y}
                      width={0.76}
                      height={h}
                      rx={0.25}
                      fill={fillColor}
                      fillOpacity={fillOpacity}
                    />
                  );
                })}
              </svg>

              {/* Live Playhead Needle */}
              <div
                className="absolute inset-y-0 w-0.5 bg-[var(--text-primary)] ring-1 ring-[var(--bg-canvas)] shadow-sm pointer-events-none z-20"
                style={{ left: `${playPct}%` }}
              />

              {/* Dimmed Outer Regions */}
              <div
                className="absolute inset-y-0 left-0 bg-black/45 pointer-events-none"
                style={{ width: `${inPct}%` }}
              />
              <div
                className="absolute inset-y-0 right-0 bg-black/45 pointer-events-none"
                style={{ width: `${Math.max(0, 100 - outPct)}%` }}
              />

              {/* Active Selection Border */}
              <div
                className="absolute inset-y-0 border-y-2 border-[var(--accent-primary)] pointer-events-none"
                style={{ left: `${inPct}%`, width: `${Math.max(2, outPct - inPct)}%` }}
              />

              {/* Draggable In Handle */}
              <div
                role="slider"
                aria-label="Trim In point"
                aria-valuenow={trimStart}
                onPointerDown={(e) => {
                  e.preventDefault();
                  setDraggingHandle('in');
                }}
                style={{ left: `calc(${inPct}% - 5px)` }}
                className="absolute inset-y-0 w-2.5 bg-[var(--accent-primary)] hover:brightness-110 cursor-ew-resize flex items-center justify-center rounded-l shadow z-10"
                title={`Drag In handle (${trimStart.toFixed(2)}s)`}
              >
                <span className="w-0.5 h-3.5 bg-[var(--accent-foreground)] rounded-full" />
              </div>

              {/* Draggable Out Handle */}
              <div
                role="slider"
                aria-label="Trim Out point"
                aria-valuenow={trimEnd}
                onPointerDown={(e) => {
                  e.preventDefault();
                  setDraggingHandle('out');
                }}
                style={{ left: `calc(${outPct}% - 5px)` }}
                className="absolute inset-y-0 w-2.5 bg-[var(--accent-primary)] hover:brightness-110 cursor-ew-resize flex items-center justify-center rounded-r shadow z-10"
                title={`Drag Out handle (${trimEnd.toFixed(2)}s)`}
              >
                <span className="w-0.5 h-3.5 bg-[var(--accent-foreground)] rounded-full" />
              </div>
            </div>
          </div>
        );
      })()}

      {/* Dedicated Export & Action Dock */}
      <div className="p-3 border-t border-[var(--border-subtle)] bg-[var(--bg-card)] space-y-2 shrink-0">
        {isRendering && (
          <div className="space-y-1 tabular-nums">
            <div className="flex justify-between text-[10px] font-mono text-[var(--text-secondary)]">
              <span>Encoding final MP4...</span>
              <span className="text-[var(--accent-primary)] font-bold">{renderProgress}%</span>
            </div>
            <div className="w-full h-1.5 rounded-full bg-[var(--bg-input)] overflow-hidden">
              <div
                className="h-full bg-[var(--accent-primary)] transition-all duration-300 rounded-full"
                style={{ width: `${renderProgress}%` }}
              />
            </div>
          </div>
        )}

        <div className="grid grid-cols-2 gap-2">
          <button
            type="button"
            onClick={onGenerateClips}
            disabled={isRendering || !clip}
            className="h-9 px-3 rounded-xl bg-[var(--accent-primary)] hover:opacity-90 disabled:opacity-50 text-[var(--accent-foreground)] font-bold text-xs flex items-center justify-center gap-1.5 transition shadow-sm"
          >
            {isRendering ? (
              <>
                <Loader2 className="w-3.5 h-3.5 animate-spin" />
                <span>Rendering...</span>
              </>
            ) : isCompleted ? (
              <>
                <CheckCircle2 className="w-3.5 h-3.5" />
                <span>Re-render Clip</span>
              </>
            ) : (
              <>
                <Zap className="w-3.5 h-3.5 fill-current" />
                <span>Render Clip</span>
              </>
            )}
          </button>

          {isCompleted ? (
            <a
              href={downloadUrl}
              download
              className="h-9 px-3 rounded-xl bg-[var(--bg-surface)] hover:border-[var(--accent-primary)]/50 text-[var(--text-primary)] border border-[var(--border-subtle)] font-semibold text-xs flex items-center justify-center gap-1.5 transition"
            >
              <Download className="w-3.5 h-3.5" />
              <span>Download MP4</span>
            </a>
          ) : (
            <button
              type="button"
              disabled
              className="h-9 px-3 rounded-xl bg-[var(--bg-input)] text-[var(--text-secondary)] border border-[var(--border-subtle)] font-semibold text-xs flex items-center justify-center gap-1.5 opacity-50 cursor-not-allowed"
              title="Render clip first to download MP4"
            >
              <Download className="w-3.5 h-3.5" />
              <span>Download MP4</span>
            </button>
          )}
        </div>

        <div className="grid grid-cols-2 gap-2">
          <button
            type="button"
            onClick={handleOpenPublish}
            disabled={!isCompleted}
            className="h-8 px-2.5 rounded-xl bg-[var(--bg-surface)] hover:border-red-500/40 text-[var(--text-primary)] border border-[var(--border-subtle)] font-semibold text-xs flex items-center justify-center gap-1.5 transition disabled:opacity-45 disabled:cursor-not-allowed"
            title={isCompleted ? 'Publish rendered clip directly to YouTube Shorts' : 'Render clip first to publish'}
          >
            <YouTubeIcon className="w-3.5 h-3.5 text-red-500 shrink-0" />
            <span className="truncate">Post YouTube</span>
          </button>

          {clip ? (
            <select
              aria-label="Export Pro NLE Format"
              defaultValue=""
              onChange={(e) => {
                const fmt = e.target.value as 'fcpxml' | 'premiere' | 'srt' | 'zip';
                if (!fmt) return;
                const url = api.getNleExportUrl(clip.project_id, clip.id, fmt);
                window.open(url, '_blank');
                e.target.value = '';
              }}
              className="h-8 px-2 rounded-xl bg-[var(--bg-surface)] hover:border-[var(--accent-primary)]/50 text-[var(--text-primary)] border border-[var(--border-subtle)] font-semibold text-[11px] outline-none cursor-pointer"
            >
              <option value="" disabled>
                Pro NLE Export ▾
              </option>
              <option value="fcpxml">DaVinci / FCPXML</option>
              <option value="premiere">Adobe Premiere .XML</option>
              <option value="srt">Captions .SRT</option>
              <option value="zip">Full Studio .ZIP</option>
            </select>
          ) : (
            <div />
          )}
        </div>

        <button
          type="button"
          onClick={handleExportPublishPack}
          disabled={!clip || isExportingPack}
          className="w-full h-8 px-3 rounded-xl bg-[var(--accent-subtle)] hover:bg-[var(--accent-primary)] hover:text-[var(--accent-foreground)] text-[var(--accent-primary)] border border-[var(--accent-primary)]/40 font-bold text-xs flex items-center justify-center gap-1.5 transition disabled:opacity-45 disabled:cursor-not-allowed"
          title="Export 9:16 + 4:5 + 16:9 MP4s, AI Cover Thumbnail, Captions .SRT, and Social Copy .TXT in one .zip"
        >
          {isExportingPack ? (
            <Loader2 className="w-3.5 h-3.5 animate-spin" />
          ) : (
            <Package className="w-3.5 h-3.5" />
          )}
          <span>{isExportingPack ? 'Building Multi-Platform Publish Pack...' : 'Publish Pack (.zip — 9:16 + 4:5 + 16:9)'}</span>
        </button>

        {packError && (
          <div className="text-[10px] text-red-400 bg-red-500/10 border border-red-500/25 rounded-lg px-2 py-1 truncate">
            {packError}
          </div>
        )}
      </div>

      {/* YouTube & Multi-Platform Social Copy Publishing Modal */}
      {isPublishModalOpen && (
        <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/75 backdrop-blur-sm p-4 animate-fadeIn">
          <div className="bg-[var(--bg-surface)] border border-[var(--border-subtle)] rounded-2xl w-full max-w-xl max-h-[90vh] overflow-y-auto p-6 space-y-4 shadow-2xl text-[var(--text-primary)]">
            <div className="flex items-center justify-between border-b border-[var(--border-subtle)] pb-3">
              <div className="flex items-center gap-2 font-bold text-sm">
                <YouTubeIcon className="w-5 h-5 text-red-500" />
                <span>Publish &amp; Multi-Platform Social Studio</span>
              </div>
              <div className="flex items-center gap-2">
                <button
                  type="button"
                  onClick={handleGenerateSocialCopy}
                  disabled={isGeneratingCopy || !clip}
                  className="px-2.5 py-1.5 rounded-lg bg-[var(--accent-subtle)] border border-[var(--accent-primary)]/40 text-[var(--accent-primary)] hover:bg-[var(--accent-primary)] hover:text-[var(--accent-foreground)] text-[11px] font-bold flex items-center gap-1.5 transition disabled:opacity-50"
                >
                  {isGeneratingCopy ? (
                    <Loader2 className="w-3.5 h-3.5 animate-spin" />
                  ) : (
                    <Sparkles className="w-3.5 h-3.5" />
                  )}
                  <span>{isGeneratingCopy ? 'Writing Copy...' : 'Generate Platform Copy'}</span>
                </button>
                <button
                  type="button"
                  onClick={() => setIsPublishModalOpen(false)}
                  className="text-[var(--text-secondary)] hover:text-[var(--text-primary)]"
                >
                  <X className="w-5 h-5" />
                </button>
              </div>
            </div>

            {socialCopy && (
              <div className="rounded-xl border border-[var(--border-subtle)] bg-[var(--bg-input)] p-3 space-y-2.5 text-xs">
                <div className="grid grid-cols-4 gap-1 bg-[var(--bg-surface)] p-1 rounded-lg border border-[var(--border-subtle)]">
                  {([
                    { id: 'youtube_shorts', label: 'YouTube' },
                    { id: 'tiktok', label: 'TikTok' },
                    { id: 'instagram_reels', label: 'Reels' },
                    { id: 'x_twitter', label: 'X' },
                  ] as const).map((tab) => (
                    <button
                      key={tab.id}
                      type="button"
                      onClick={() => setActiveCopyPlatform(tab.id)}
                      className={`py-1 rounded-md text-[11px] font-bold transition ${
                        activeCopyPlatform === tab.id
                          ? 'bg-[var(--accent-primary)] text-[var(--accent-foreground)]'
                          : 'text-[var(--text-secondary)] hover:text-[var(--text-primary)]'
                      }`}
                    >
                      {tab.label}
                    </button>
                  ))}
                </div>

                {(() => {
                  const entry = socialCopy[activeCopyPlatform];
                  if (!entry) return null;
                  const tagsStr = Array.isArray(entry.hashtags) ? entry.hashtags.join(' ') : '';
                  const fullCopy = `${entry.title}\n\n${entry.description}${tagsStr ? `\n\n${tagsStr}` : ''}`;
                  return (
                    <div className="space-y-2">
                      <div className="flex items-center justify-between gap-2">
                        <span className="font-bold text-[var(--text-primary)] truncate">
                          {entry.title}
                        </span>
                        <div className="flex items-center gap-1.5 shrink-0">
                          <button
                            type="button"
                            onClick={() => handleCopyText(activeCopyPlatform, fullCopy)}
                            className="px-2 py-1 rounded-md bg-[var(--bg-surface)] border border-[var(--border-subtle)] text-[10px] font-semibold flex items-center gap-1 hover:border-[var(--accent-primary)]/50"
                          >
                            {copiedField === activeCopyPlatform ? (
                              <Check className="w-3 h-3 text-emerald-400" />
                            ) : (
                              <Copy className="w-3 h-3" />
                            )}
                            <span>{copiedField === activeCopyPlatform ? 'Copied' : 'Copy All'}</span>
                          </button>
                          <button
                            type="button"
                            onClick={() => {
                              setPublishTitle(entry.title || publishTitle);
                              setPublishDesc([entry.description || '', tagsStr].filter(Boolean).join('\n\n'));
                            }}
                            className="px-2 py-1 rounded-md bg-[var(--accent-subtle)] text-[var(--accent-primary)] text-[10px] font-bold hover:opacity-90"
                          >
                            Apply Below
                          </button>
                        </div>
                      </div>
                      <p className="text-[11px] text-[var(--text-secondary)] whitespace-pre-line">
                        {entry.description}
                      </p>
                      {entry.pinned_comment && (
                        <div className="text-[10px] text-[var(--text-secondary)] bg-[var(--bg-surface)] px-2 py-1 rounded border border-[var(--border-subtle)]">
                          <span className="font-semibold text-[var(--text-primary)]">Pinned: </span>
                          {entry.pinned_comment}
                        </div>
                      )}
                      {tagsStr && (
                        <div className="text-[10px] font-mono text-[var(--accent-primary)] break-words">
                          {tagsStr}
                        </div>
                      )}
                    </div>
                  );
                })()}
              </div>
            )}

            <form onSubmit={handlePublishSubmit} className="space-y-3.5 text-xs">
              <div>
                <label className="text-[var(--text-secondary)] font-semibold block mb-1">
                  Title (with #Shorts)
                </label>
                <input
                  type="text"
                  value={publishTitle}
                  onChange={(e) => setPublishTitle(e.target.value)}
                  className="w-full bg-[var(--bg-input)] border border-[var(--border-subtle)] rounded-xl px-3 py-2 text-[var(--text-primary)] outline-none focus:border-[var(--accent-primary)]"
                  required
                />
              </div>

              <div>
                <label className="text-[var(--text-secondary)] font-semibold block mb-1">
                  Description & Hashtags
                </label>
                <textarea
                  rows={3}
                  value={publishDesc}
                  onChange={(e) => setPublishDesc(e.target.value)}
                  className="w-full bg-[var(--bg-input)] border border-[var(--border-subtle)] rounded-xl px-3 py-2 text-[var(--text-primary)] outline-none focus:border-[var(--accent-primary)]"
                />
              </div>

              <div className="flex items-center justify-between">
                <div>
                  <label className="text-[var(--text-secondary)] font-semibold block mb-1">Visibility</label>
                  <select
                    value={publishPrivacy}
                    onChange={(e) => setPublishPrivacy(e.target.value as 'private' | 'unlisted' | 'public')}
                    className="bg-[var(--bg-input)] border border-[var(--border-subtle)] rounded-lg px-2.5 py-1.5 text-[var(--text-primary)] outline-none"
                  >
                    <option value="private">Private</option>
                    <option value="unlisted">Unlisted</option>
                    <option value="public">Public</option>
                  </select>
                </div>

                <button
                  type="submit"
                  disabled={isPublishing}
                  className="px-5 py-2 bg-red-600 hover:bg-red-700 text-white font-bold text-xs rounded-xl transition shadow-md flex items-center gap-1.5 disabled:opacity-50"
                >
                  {isPublishing ? <Loader2 className="w-4 h-4 animate-spin" /> : <YouTubeIcon className="w-4 h-4" />}
                  <span>{isPublishing ? 'Publishing...' : 'Upload Video'}</span>
                </button>
              </div>

              {publishResult && (
                <div
                  className={`p-3 rounded-xl text-xs flex items-center justify-between ${
                    publishResult.startsWith('Error')
                      ? 'bg-red-500/10 border border-red-500/30 text-red-400'
                      : 'bg-emerald-500/10 border border-emerald-500/30 text-emerald-400'
                  }`}
                >
                  <span className="break-all">{publishResult}</span>
                  {publishResult.startsWith('http') && (
                    <a href={publishResult} target="_blank" rel="noreferrer" className="underline font-bold ml-2 shrink-0">
                      View
                    </a>
                  )}
                </div>
              )}
            </form>
          </div>
        </div>
      )}
    </aside>
  );
};
