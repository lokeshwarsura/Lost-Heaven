import { useCallback, useRef, useState } from 'react';
import { ViralClip } from '../types';

export type EditableClipSnapshot = Pick<
  ViralClip,
  | 'start_time'
  | 'end_time'
  | 'duration'
  | 'transcript_words'
  | 'selected_title'
  | 'hook_headline'
  | 'show_hook_banner'
  | 'cold_open_quote'
  | 'use_cold_open'
  | 'auto_trim_dead_air'
  | 'dynamic_punch_zoom'
  | 'punch_zoom_intensity'
  | 'aspect_ratio'
  | 'custom_width'
  | 'custom_height'
  | 'frame_layout'
  | 'webcam_corner'
  | 'webcam_split_ratio'
  | 'pip_border_color'
  | 'zoom'
  | 'pan_x_offset'
  | 'burn_captions'
  | 'timing_offset'
  | 'caption_style'
  | 'caption_font'
  | 'caption_animation'
  | 'base_color'
  | 'active_color'
  | 'outline_color'
  | 'font_size'
  | 'font_size_percent'
  | 'bold'
  | 'italic'
  | 'all_caps'
  | 'text_align'
  | 'position_grid'
  | 'position_x'
  | 'position_y'
  | 'hold_back_alpha'
  | 'highlight_shape'
  | 'emoji_enabled'
  | 'highlight_power_words'
  | 'how_phrase_arrives'
  | 'outline_width'
  | 'shadow_depth'
  | 'glow_intensity'
  | 'letter_spacing'
  | 'clean_filler_words'
  | 'tighten_pacing'
  | 'silence_threshold'
  | 'censor_profanity'
  | 'clip_size'
  | 'color_preset'
  | 'exposure'
  | 'brightness'
  | 'contrast'
  | 'lift_mids'
  | 'warmth'
  | 'saturation'
  | 'black_and_white'
  | 'vintage'
  | 'tint_color'
  | 'tint_strength'
  | 'glow'
  | 'smooth'
  | 'sharpness'
  | 'blur'
  | 'film_grain'
  | 'vignette'
  | 'bottom_fade'
  | 'watermark_corner'
  | 'watermark_opacity'
  | 'watermark_size'
  | 'watermark_path'
  | 'bgm_path'
  | 'bgm_volume'
  | 'audio_volume'
  | 'normalize_audio'
  | 'vocal_clarity'
  | 'voice_isolation'
  | 'auto_sfx'
  | 'retention_bar_enabled'
  | 'retention_bar_color'
  | 'outro_cta_enabled'
  | 'outro_cta_text'
>;

interface HistoryStack {
  past: EditableClipSnapshot[];
  future: EditableClipSnapshot[];
}

const MAX_HISTORY = 30;
const COALESCE_MS = 450;

export function extractEditableSnapshot(clip: ViralClip): EditableClipSnapshot {
  return {
    start_time: clip.start_time,
    end_time: clip.end_time,
    duration: clip.duration,
    transcript_words: Array.isArray(clip.transcript_words)
      ? clip.transcript_words.map((w) => ({ ...w }))
      : [],
    selected_title: clip.selected_title,
    hook_headline: clip.hook_headline,
    show_hook_banner: clip.show_hook_banner,
    cold_open_quote: clip.cold_open_quote,
    use_cold_open: clip.use_cold_open,
    auto_trim_dead_air: clip.auto_trim_dead_air,
    dynamic_punch_zoom: clip.dynamic_punch_zoom,
    punch_zoom_intensity: clip.punch_zoom_intensity,
    aspect_ratio: clip.aspect_ratio,
    custom_width: clip.custom_width,
    custom_height: clip.custom_height,
    frame_layout: clip.frame_layout,
    webcam_corner: clip.webcam_corner,
    webcam_split_ratio: clip.webcam_split_ratio,
    pip_border_color: clip.pip_border_color,
    zoom: clip.zoom,
    pan_x_offset: clip.pan_x_offset,
    burn_captions: clip.burn_captions,
    timing_offset: clip.timing_offset,
    caption_style: clip.caption_style,
    caption_font: clip.caption_font,
    caption_animation: clip.caption_animation,
    base_color: clip.base_color,
    active_color: clip.active_color,
    outline_color: clip.outline_color,
    font_size: clip.font_size,
    font_size_percent: clip.font_size_percent,
    bold: clip.bold,
    italic: clip.italic,
    all_caps: clip.all_caps,
    text_align: clip.text_align,
    position_grid: clip.position_grid,
    position_x: clip.position_x,
    position_y: clip.position_y,
    hold_back_alpha: clip.hold_back_alpha,
    highlight_shape: clip.highlight_shape,
    emoji_enabled: clip.emoji_enabled,
    highlight_power_words: clip.highlight_power_words,
    how_phrase_arrives: clip.how_phrase_arrives,
    outline_width: clip.outline_width,
    shadow_depth: clip.shadow_depth,
    glow_intensity: clip.glow_intensity,
    letter_spacing: clip.letter_spacing,
    clean_filler_words: clip.clean_filler_words,
    tighten_pacing: clip.tighten_pacing,
    silence_threshold: clip.silence_threshold,
    censor_profanity: clip.censor_profanity,
    clip_size: clip.clip_size,
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
    tint_strength: clip.tint_strength,
    glow: clip.glow,
    smooth: clip.smooth,
    sharpness: clip.sharpness,
    blur: clip.blur,
    film_grain: clip.film_grain,
    vignette: clip.vignette,
    bottom_fade: clip.bottom_fade,
    watermark_corner: clip.watermark_corner,
    watermark_opacity: clip.watermark_opacity,
    watermark_size: clip.watermark_size,
    watermark_path: clip.watermark_path,
    bgm_path: clip.bgm_path,
    bgm_volume: clip.bgm_volume,
    audio_volume: clip.audio_volume,
    normalize_audio: clip.normalize_audio,
    vocal_clarity: clip.vocal_clarity,
    voice_isolation: clip.voice_isolation,
    auto_sfx: clip.auto_sfx,
    retention_bar_enabled: clip.retention_bar_enabled,
    retention_bar_color: clip.retention_bar_color,
    outro_cta_enabled: clip.outro_cta_enabled,
    outro_cta_text: clip.outro_cta_text,
  };
}

function snapshotsEqual(a: EditableClipSnapshot, b: EditableClipSnapshot): boolean {
  return JSON.stringify(a) === JSON.stringify(b);
}

/** Per-clip Undo/Redo stack (max 30 entries) with slider gesture coalescing. */
export function useEditorHistory(activeClipId: number | null) {
  const stacksRef = useRef<Record<number, HistoryStack>>({});
  const lastPushAtRef = useRef<Record<number, number>>({});
  const [, setRevision] = useState(0);

  const ensureStack = (clipId: number): HistoryStack => {
    if (!stacksRef.current[clipId]) {
      stacksRef.current[clipId] = { past: [], future: [] };
    }
    return stacksRef.current[clipId];
  };

  const pushSnapshot = useCallback((clipId: number, prevClip: ViralClip) => {
    const stack = ensureStack(clipId);
    const snap = extractEditableSnapshot(prevClip);
    const now = Date.now();
    const lastPush = lastPushAtRef.current[clipId] || 0;

    // Coalesce rapid slider ticks so dragging a slider creates one clean Undo step
    if (now - lastPush < COALESCE_MS && stack.past.length > 0) {
      stack.future = [];
      lastPushAtRef.current[clipId] = now;
      return;
    }

    const top = stack.past[stack.past.length - 1];
    if (top && snapshotsEqual(top, snap)) {
      return;
    }

    stack.past = [...stack.past.slice(-(MAX_HISTORY - 1)), snap];
    stack.future = [];
    lastPushAtRef.current[clipId] = now;
    setRevision((v) => v + 1);
  }, []);

  const undo = useCallback((clipId: number, currentClip: ViralClip): EditableClipSnapshot | null => {
    const stack = ensureStack(clipId);
    if (stack.past.length === 0) return null;

    const currentSnap = extractEditableSnapshot(currentClip);
    const previous = stack.past[stack.past.length - 1];
    stack.past = stack.past.slice(0, -1);
    stack.future = [currentSnap, ...stack.future].slice(0, MAX_HISTORY);
    lastPushAtRef.current[clipId] = 0;
    setRevision((v) => v + 1);
    return previous;
  }, []);

  const redo = useCallback((clipId: number, currentClip: ViralClip): EditableClipSnapshot | null => {
    const stack = ensureStack(clipId);
    if (stack.future.length === 0) return null;

    const currentSnap = extractEditableSnapshot(currentClip);
    const nextSnap = stack.future[0];
    stack.future = stack.future.slice(1);
    stack.past = [...stack.past.slice(-(MAX_HISTORY - 1)), currentSnap];
    lastPushAtRef.current[clipId] = 0;
    setRevision((v) => v + 1);
    return nextSnap;
  }, []);

  const currentStack = activeClipId ? stacksRef.current[activeClipId] : undefined;
  const canUndo = Boolean(currentStack && currentStack.past.length > 0);
  const canRedo = Boolean(currentStack && currentStack.future.length > 0);

  return {
    canUndo,
    canRedo,
    pushSnapshot,
    undo,
    redo,
  };
}
