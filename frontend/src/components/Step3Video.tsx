import React, { useState } from 'react';
import {
  Sun, Droplets, Sparkles, Shield, Upload, Music, Volume2, Copy, Check, RotateCcw, Save, Film, Flag
} from 'lucide-react';
import { ViralClip, BrollSegment } from '../types';
import { api } from '../api/client';

interface Step3VideoProps {
  clip: ViralClip | null;
  onUpdateClip: (updates: Partial<ViralClip>) => void;
  onUploadAsset?: (file: File) => Promise<{ url: string; filename: string; path: string }>;
}

export const Step3Video: React.FC<Step3VideoProps> = ({
  clip,
  onUpdateClip,
  onUploadAsset
}) => {
  const [subTab, setSubTab] = useState<'grade' | 'fx' | 'metadata'>('grade');
  const [copiedTitleIndex, setCopiedTitleIndex] = useState<number | null>(null);
  const [metadataSaved, setMetadataSaved] = useState(false);
  const [loadingBroll, setLoadingBroll] = useState(false);
  const [brollNotice, setBrollNotice] = useState<string | null>(null);
  const [loadingCallouts, setLoadingCallouts] = useState(false);
  const [calloutNotice, setCalloutNotice] = useState<string | null>(null);
  const [dubLang, setDubLang] = useState<string>('Spanish');
  const [dubVoice, setDubVoice] = useState<string>('default');
  const [isDubbing, setIsDubbing] = useState(false);
  const [dubNotice, setDubNotice] = useState<string | null>(null);

  const handleExtractCallouts = async () => {
    if (!clip) return;
    try {
      setLoadingCallouts(true);
      setCalloutNotice(null);
      const res = await api.extractCalloutCards(clip.project_id, clip.id, 3);
      const cards = res?.callout_cards || [];
      onUpdateClip({ callout_cards: cards, auto_callouts: true });
      setCalloutNotice(
        cards.length > 0
          ? `Extracted ${cards.length} stat callout card${cards.length === 1 ? '' : 's'}.`
          : 'No numbers, percentages, or currency stats found.'
      );
    } catch {
      setCalloutNotice('Failed to extract stat callout cards.');
    } finally {
      setLoadingCallouts(false);
    }
  };

  const handleSuggestBroll = async () => {
    if (!clip) return;
    try {
      setLoadingBroll(true);
      setBrollNotice(null);
      const res = await api.getBrollSuggestions(clip.project_id, clip.id);
      const suggs: BrollSegment[] = res?.suggestions || [];
      onUpdateClip({ broll_segments: suggs });
      setBrollNotice(
        suggs.length > 0
          ? `Found ${suggs.length} keyword cutaway slot${suggs.length === 1 ? '' : 's'}.`
          : 'No transcript keywords detected yet.'
      );
    } catch {
      setBrollNotice('Failed to inspect B-Roll suggestions.');
    } finally {
      setLoadingBroll(false);
    }
  };

  const handleAssignBrollAsset = async (index: number, file: File) => {
    if (!onUploadAsset || !clip) return;
    try {
      const res = await onUploadAsset(file);
      const next = [...(clip.broll_segments || [])];
      if (next[index]) {
        next[index] = {
          ...next[index],
          asset_path: res.path || res.url,
          source: 'local',
          enabled: true,
        };
        onUpdateClip({ broll_segments: next });
      }
    } catch (err) {
      console.error('B-Roll asset upload failed:', err);
    }
  };

  if (!clip) {
    return (
      <div className="p-8 rounded-2xl bg-[var(--bg-card)] border border-[var(--border-subtle)] text-center text-xs text-[var(--text-secondary)]">
        Select a clip from the Viral Clips sidebar to grade footage and edit YouTube metadata.
      </div>
    );
  }

  const clipSizes: ViralClip['clip_size'][] = ['Auto', '1080p', '720p', '540p', '480p'];

  const colorPresets: {
    id: string;
    label: string;
    values: Partial<ViralClip>;
  }[] = [
    {
      id: 'none',
      label: 'Original',
      values: {
        exposure: 0, brightness: 0, contrast: 1.0, lift_mids: 1.0,
        warmth: 0, saturation: 1.0, black_and_white: 0, vintage: 0,
        tint_color: 'none', tint_strength: 0, sharpness: 0, vignette: 0
      }
    },
    {
      id: 'cinematic',
      label: 'Cinematic',
      values: {
        exposure: 0.02, brightness: -0.02, contrast: 1.18, lift_mids: 0.95,
        warmth: 0.14, saturation: 1.12, black_and_white: 0, vintage: 0.10,
        tint_color: 'none', tint_strength: 0, sharpness: 0.2, vignette: 0.35
      }
    },
    {
      id: 'vibrant_gaming',
      label: 'Vibrant Gaming',
      values: {
        exposure: 0.06, brightness: 0.02, contrast: 1.22, lift_mids: 1.05,
        warmth: 0.04, saturation: 1.45, black_and_white: 0, vintage: 0,
        tint_color: 'none', tint_strength: 0, sharpness: 0.45, vignette: 0
      }
    },
    {
      id: 'warm_vintage',
      label: 'Warm Vintage',
      values: {
        exposure: 0.04, brightness: 0.02, contrast: 1.06, lift_mids: 1.05,
        warmth: 0.34, saturation: 1.10, black_and_white: 0, vintage: 0.45,
        tint_color: 'Yellow', tint_strength: 0.20, sharpness: 0, vignette: 0.25
      }
    },
    {
      id: 'cool_tech',
      label: 'Cool Tech',
      values: {
        exposure: 0.02, brightness: 0, contrast: 1.14, lift_mids: 1.0,
        warmth: -0.32, saturation: 1.16, black_and_white: 0, vintage: 0,
        tint_color: 'Blue', tint_strength: 0.25, sharpness: 0.30, vignette: 0.15
      }
    },
    {
      id: 'bw_noir',
      label: 'B&W Noir',
      values: {
        exposure: -0.02, brightness: -0.04, contrast: 1.38, lift_mids: 0.92,
        warmth: 0, saturation: 0.0, black_and_white: 1.0, vintage: 0,
        tint_color: 'none', tint_strength: 0, sharpness: 0.25, vignette: 0.45
      }
    },
    {
      id: 'hdr_pop',
      label: 'HDR Pop',
      values: {
        exposure: 0.08, brightness: 0.02, contrast: 1.25, lift_mids: 1.10,
        warmth: 0.06, saturation: 1.50, black_and_white: 0, vintage: 0,
        tint_color: 'none', tint_strength: 0, sharpness: 0.55, vignette: 0.10
      }
    }
  ];

  const tintOptions = ['None', 'Red', 'Blue', 'Green', 'Yellow'];

  const corners: { id: ViralClip['watermark_corner']; label: string }[] = [
    { id: 'top_left', label: 'Top Left' },
    { id: 'top_right', label: 'Top Right' },
    { id: 'bottom_left', label: 'Bottom Left' },
    { id: 'bottom_right', label: 'Bottom Right' }
  ];

  const handleCopyTitle = (title: string, index: number) => {
    navigator.clipboard.writeText(title);
    setCopiedTitleIndex(index);
    setTimeout(() => setCopiedTitleIndex(null), 2000);
  };

  const handleWatermarkUpload = async (e: React.ChangeEvent<HTMLInputElement>) => {
    if (e.target.files?.[0] && onUploadAsset) {
      const res = await onUploadAsset(e.target.files[0]);
      onUpdateClip({ watermark_path: res.path });
    }
  };

  const handleBgmUpload = async (e: React.ChangeEvent<HTMLInputElement>) => {
    if (e.target.files?.[0] && onUploadAsset) {
      const res = await onUploadAsset(e.target.files[0]);
      onUpdateClip({ bgm_path: res.path });
    }
  };

  const resetLightAndColor = () => {
    onUpdateClip({
      color_preset: 'none',
      exposure: 0,
      brightness: 0,
      contrast: 1.0,
      lift_mids: 1.0,
      warmth: 0,
      saturation: 1.0,
      black_and_white: 0,
      vintage: 0,
      tint_color: 'none',
      tint_strength: 0
    });
  };

  const handleSaveMetadata = async () => {
    try {
      await api.updateClipMetadata(clip.project_id, clip.id, {
        selected_title: clip.selected_title,
        youtube_description: clip.youtube_description,
        hashtags: clip.hashtags || '',
        pinned_comment: clip.pinned_comment || '',
        privacy_status: clip.privacy_status
      });
      setMetadataSaved(true);
      setTimeout(() => setMetadataSaved(false), 2000);
    } catch (err) {
      console.error('Failed to save metadata:', err);
    }
  };

  const vignetteVal =
    typeof clip.vignette === 'number' ? clip.vignette : clip.vignette ? 1.0 : 0;

  return (
    <div className="space-y-4 text-xs animate-fadeIn w-full text-[var(--text-primary)]">
      {/* Top Row: Clip Resolution + Preset Quick Looks */}
      <div className="grid grid-cols-1 lg:grid-cols-2 gap-4">
        <div className="bg-[var(--bg-card)] border border-[var(--border-subtle)] rounded-xl p-4 space-y-2">
          <div className="flex items-center justify-between">
            <span className="font-bold text-xs uppercase tracking-wide">Export Resolution</span>
            <span className="text-[10px] text-[var(--text-secondary)]">Target MP4 height</span>
          </div>
          <div className="grid grid-cols-5 gap-1.5 tabular-nums">
            {clipSizes.map((sz) => (
              <button
                key={sz}
                type="button"
                onClick={() => onUpdateClip({ clip_size: sz })}
                className={`py-1.5 rounded-lg border text-center font-medium transition ${
                  clip.clip_size === sz
                    ? 'bg-[var(--accent-primary)] text-[var(--accent-foreground)] font-bold border-[var(--accent-primary)]'
                    : 'bg-[var(--bg-input)] border-[var(--border-subtle)] text-[var(--text-secondary)] hover:text-[var(--text-primary)]'
                }`}
              >
                {sz}
              </button>
            ))}
          </div>
        </div>

        <div className="bg-[var(--bg-card)] border border-[var(--border-subtle)] rounded-xl p-4 space-y-2">
          <div className="flex items-center justify-between">
            <span className="font-bold text-xs uppercase tracking-wide">Preset Quick Looks</span>
            <button
              type="button"
              onClick={resetLightAndColor}
              className="text-[10px] text-[var(--text-secondary)] hover:text-[var(--accent-primary)] flex items-center gap-1 transition"
            >
              <RotateCcw className="w-3 h-3" /> Reset Grade
            </button>
          </div>
          <div className="flex flex-wrap gap-1.5">
            {colorPresets.map((p) => (
              <button
                key={p.id}
                type="button"
                onClick={() => onUpdateClip({ color_preset: p.id, ...p.values })}
                className={`py-1 px-2.5 rounded-lg border text-[11px] transition ${
                  clip.color_preset === p.id
                    ? 'bg-[var(--accent-primary)] text-[var(--accent-foreground)] font-bold border-[var(--accent-primary)]'
                    : 'bg-[var(--bg-input)] border-[var(--border-subtle)] text-[var(--text-secondary)] hover:text-[var(--text-primary)]'
                }`}
              >
                {p.label}
              </button>
            ))}
          </div>
        </div>
      </div>

      {/* Sub-Tabs */}
      <div className="flex items-center gap-1 p-1 rounded-xl bg-[var(--bg-card)] border border-[var(--border-subtle)]">
        {[
          { id: 'grade', label: 'Light & Color', icon: Sun },
          { id: 'fx', label: 'Effects & Audio', icon: Sparkles },
          { id: 'metadata', label: 'YouTube Metadata', icon: Shield }
        ].map((tab) => {
          const Icon = tab.icon;
          const active = subTab === tab.id;
          return (
            <button
              key={tab.id}
              type="button"
              onClick={() => setSubTab(tab.id as any)}
              className={`flex-1 flex items-center justify-center gap-1.5 py-1.5 px-2 rounded-lg text-xs font-semibold transition whitespace-nowrap ${
                active
                  ? 'bg-[var(--accent-primary)] text-[var(--accent-foreground)] shadow-sm'
                  : 'text-[var(--text-secondary)] hover:text-[var(--text-primary)]'
              }`}
            >
              <Icon className="w-3.5 h-3.5 shrink-0" />
              <span>{tab.label}</span>
            </button>
          );
        })}
      </div>

      {/* TAB 1: LIGHT & COLOR */}
      {subTab === 'grade' && (
        <div className="grid grid-cols-1 lg:grid-cols-2 gap-4 animate-fadeIn tabular-nums">
          {/* Light Card */}
          <div className="bg-[var(--bg-card)] border border-[var(--border-subtle)] rounded-xl p-4 space-y-3">
            <div className="flex items-center gap-2 font-bold text-xs text-[var(--text-primary)]">
              <Sun className="w-4 h-4 text-[var(--accent-primary)]" />
              <span>Light & Exposure</span>
            </div>

            {[
              { label: 'Exposure', key: 'exposure', min: -0.5, max: 0.5, step: 0.02, val: clip.exposure || 0, display: Math.round((clip.exposure || 0) * 100) },
              { label: 'Brightness', key: 'brightness', min: -0.5, max: 0.5, step: 0.02, val: clip.brightness || 0, display: Math.round((clip.brightness || 0) * 100) },
              { label: 'Contrast', key: 'contrast', min: 0.5, max: 1.8, step: 0.05, val: clip.contrast || 1.0, display: Math.round(((clip.contrast || 1.0) - 1.0) * 100) },
              { label: 'Lift Mids (Gamma)', key: 'lift_mids', min: 0.6, max: 1.6, step: 0.05, val: clip.lift_mids || 1.0, display: Math.round(((clip.lift_mids || 1.0) - 1.0) * 100) }
            ].map((s) => (
              <div key={s.key}>
                <div className="flex justify-between text-[11px] text-[var(--text-secondary)] mb-1">
                  <span>{s.label}</span>
                  <span className="font-mono text-[var(--accent-primary)] font-bold">{s.display}%</span>
                </div>
                <input
                  type="range"
                  min={s.min}
                  max={s.max}
                  step={s.step}
                  value={s.val}
                  onChange={(e) => onUpdateClip({ [s.key]: parseFloat(e.target.value) } as any)}
                  className="w-full h-1.5 bg-[var(--bg-input)] rounded appearance-none cursor-pointer"
                />
              </div>
            ))}
          </div>

          {/* Color Card */}
          <div className="bg-[var(--bg-card)] border border-[var(--border-subtle)] rounded-xl p-4 space-y-3">
            <div className="flex items-center gap-2 font-bold text-xs text-[var(--text-primary)]">
              <Droplets className="w-4 h-4 text-[var(--accent-primary)]" />
              <span>Color & Tint</span>
            </div>

            {[
              { label: 'Warmth (Cool ↔ Warm)', key: 'warmth', min: -0.5, max: 0.5, step: 0.02, val: clip.warmth || 0, display: Math.round((clip.warmth || 0) * 100) },
              { label: 'Saturation', key: 'saturation', min: 0.0, max: 2.5, step: 0.05, val: clip.saturation || 1.0, display: Math.round(((clip.saturation || 1.0) - 1.0) * 100) },
              { label: 'Black & White', key: 'black_and_white', min: 0, max: 1.0, step: 0.05, val: clip.black_and_white || 0, display: Math.round((clip.black_and_white || 0) * 100) },
              { label: 'Vintage Look', key: 'vintage', min: 0, max: 1.0, step: 0.05, val: clip.vintage || 0, display: Math.round((clip.vintage || 0) * 100) }
            ].map((s) => (
              <div key={s.key}>
                <div className="flex justify-between text-[11px] text-[var(--text-secondary)] mb-1">
                  <span>{s.label}</span>
                  <span className="font-mono text-[var(--accent-primary)] font-bold">{s.display}%</span>
                </div>
                <input
                  type="range"
                  min={s.min}
                  max={s.max}
                  step={s.step}
                  value={s.val}
                  onChange={(e) => onUpdateClip({ [s.key]: parseFloat(e.target.value) } as any)}
                  className="w-full h-1.5 bg-[var(--bg-input)] rounded appearance-none cursor-pointer"
                />
              </div>
            ))}

            <div className="pt-1 flex items-center justify-between gap-2">
              <span className="text-[11px] text-[var(--text-secondary)]">Color Tint</span>
              <div className="flex gap-1 flex-wrap">
                {tintOptions.map((t) => {
                  const tintHexMap: Record<string, string> = {
                    Red: '#EF4444',
                    Blue: '#3B82F6',
                    Green: '#10B981',
                    Yellow: '#FACC15'
                  };
                  const activeTint =
                    t === 'None'
                      ? (clip.tint_strength || 0) <= 0.01 || !clip.tint_color || clip.tint_color === 'none'
                      : (clip.tint_strength || 0) > 0.01 &&
                        (clip.tint_color === t || clip.tint_color === tintHexMap[t]);
                  return (
                    <button
                      key={t}
                      type="button"
                      onClick={() => {
                        if (t === 'None') onUpdateClip({ tint_color: 'none', tint_strength: 0 });
                        else onUpdateClip({ tint_color: t, tint_strength: 0.25 });
                      }}
                      className={`px-2 py-0.5 rounded border text-[10px] transition ${
                        activeTint
                          ? 'bg-[var(--accent-subtle)] border-[var(--accent-primary)] text-[var(--accent-primary)] font-bold'
                          : 'bg-[var(--bg-input)] border-[var(--border-subtle)] text-[var(--text-secondary)] hover:text-[var(--text-primary)]'
                      }`}
                    >
                      {t}
                    </button>
                  );
                })}
              </div>
            </div>
          </div>
        </div>
      )}

      {/* TAB 2: EFFECTS & AUDIO */}
      {subTab === 'fx' && (
        <div className="grid grid-cols-1 lg:grid-cols-2 gap-4 animate-fadeIn tabular-nums">
          {/* Lens & Legibility Effects */}
          <div className="bg-[var(--bg-card)] border border-[var(--border-subtle)] rounded-xl p-4 space-y-3">
            <div className="flex items-center justify-between">
              <span className="font-bold text-xs text-[var(--text-primary)] flex items-center gap-1.5">
                <Sparkles className="w-4 h-4 text-[var(--accent-primary)]" />
                Lens & Legibility FX
              </span>
              <button
                type="button"
                onClick={() =>
                  onUpdateClip({
                    glow: 0,
                    smooth: 0,
                    sharpness: 0,
                    blur: 0,
                    film_grain: 0,
                    vignette: 0,
                    bottom_fade: 0
                  })
                }
                className="text-[10px] text-[var(--text-secondary)] hover:text-[var(--accent-primary)] flex items-center gap-1"
              >
                <RotateCcw className="w-3 h-3" /> Reset FX
              </button>
            </div>

            <div className="grid grid-cols-2 gap-3">
              {[
                { label: 'Dreamy Glow', key: 'glow', val: clip.glow || 0 },
                { label: 'Skin Smooth', key: 'smooth', val: clip.smooth || 0 },
                { label: 'Sharpness', key: 'sharpness', val: clip.sharpness || 0 },
                { label: 'Soft Blur', key: 'blur', val: clip.blur || 0 },
                { label: 'Film Grain', key: 'film_grain', val: clip.film_grain || 0 },
                { label: 'Vignette', key: 'vignette', val: vignetteVal },
                { label: 'Bottom Fade', key: 'bottom_fade', val: clip.bottom_fade || 0 }
              ].map((s) => (
                <div key={s.key}>
                  <div className="flex justify-between text-[11px] text-[var(--text-secondary)] mb-1">
                    <span>{s.label}</span>
                    <span className="font-mono text-[var(--accent-primary)] font-bold">
                      {Math.round(s.val * 100)}%
                    </span>
                  </div>
                  <input
                    type="range"
                    min={0}
                    max={1.0}
                    step={0.05}
                    value={s.val}
                    onChange={(e) => onUpdateClip({ [s.key]: parseFloat(e.target.value) } as any)}
                    className="w-full h-1.5 bg-[var(--bg-input)] rounded appearance-none cursor-pointer"
                  />
                </div>
              ))}
            </div>
          </div>

          {/* Watermark & BGM Ducking */}
          <div className="bg-[var(--bg-card)] border border-[var(--border-subtle)] rounded-xl p-4 space-y-4">
            <div className="space-y-2">
              <div className="flex items-center justify-between">
                <span className="font-bold text-xs text-[var(--text-primary)] flex items-center gap-1.5">
                  <Shield className="w-3.5 h-3.5 text-[var(--accent-primary)]" />
                  Watermark / Channel Logo
                </span>
                {clip.watermark_path && (
                  <button
                    type="button"
                    onClick={() => onUpdateClip({ watermark_path: undefined })}
                    className="text-[10px] text-red-400 hover:underline"
                  >
                    Remove
                  </button>
                )}
              </div>

              <div className="flex items-center gap-2.5">
                <label className="flex items-center gap-1.5 px-3 py-1.5 bg-[var(--bg-input)] hover:border-[var(--accent-primary)]/40 text-[var(--text-primary)] rounded-lg border border-[var(--border-subtle)] cursor-pointer transition">
                  <Upload className="w-3.5 h-3.5 text-[var(--accent-primary)]" />
                  <span>{clip.watermark_path ? 'Change PNG' : 'Upload PNG Logo'}</span>
                  <input
                    type="file"
                    accept="image/png,image/jpeg,image/webp"
                    onChange={handleWatermarkUpload}
                    className="hidden"
                  />
                </label>
                {clip.watermark_path && (
                  <span className="text-[11px] text-emerald-400 font-mono">✓ Loaded</span>
                )}
              </div>

              {clip.watermark_path && (
                <div className="grid grid-cols-2 gap-3 pt-1">
                  <select
                    value={clip.watermark_corner || 'top_right'}
                    onChange={(e) => onUpdateClip({ watermark_corner: e.target.value as any })}
                    className="bg-[var(--bg-input)] border border-[var(--border-subtle)] rounded-lg px-2 py-1 text-xs text-[var(--text-primary)] outline-none"
                  >
                    {corners.map((c) => (
                      <option key={c.id} value={c.id}>
                        {c.label}
                      </option>
                    ))}
                  </select>
                  <div>
                    <div className="flex justify-between text-[10px] text-[var(--text-secondary)]">
                      <span>Opacity</span>
                      <span className="font-mono text-[var(--accent-primary)]">
                        {Math.round((clip.watermark_opacity ?? 0.85) * 100)}%
                      </span>
                    </div>
                    <input
                      type="range"
                      min={0.1}
                      max={1.0}
                      step={0.05}
                      value={clip.watermark_opacity ?? 0.85}
                      onChange={(e) =>
                        onUpdateClip({ watermark_opacity: parseFloat(e.target.value) })
                      }
                      className="w-full h-1.5 bg-[var(--bg-input)] rounded appearance-none cursor-pointer"
                    />
                  </div>
                </div>
              )}
            </div>

            <div className="pt-3 border-t border-[var(--border-subtle)] space-y-2">
              <div className="flex items-center justify-between">
                <span className="font-bold text-xs text-[var(--text-primary)] flex items-center gap-1.5">
                  <Music className="w-3.5 h-3.5 text-[var(--accent-primary)]" />
                  Background Music & Auto-Ducking
                </span>
                {clip.bgm_path && (
                  <button
                    type="button"
                    onClick={() => onUpdateClip({ bgm_path: undefined })}
                    className="text-[10px] text-red-400 hover:underline"
                  >
                    Remove
                  </button>
                )}
              </div>

              <div className="flex items-center gap-2.5">
                <label className="flex items-center gap-1.5 px-3 py-1.5 bg-[var(--bg-input)] hover:border-[var(--accent-primary)]/40 text-[var(--text-primary)] rounded-lg border border-[var(--border-subtle)] cursor-pointer transition">
                  <Upload className="w-3.5 h-3.5 text-[var(--accent-primary)]" />
                  <span>{clip.bgm_path ? 'Change Audio' : 'Add MP3/WAV Track'}</span>
                  <input
                    type="file"
                    accept="audio/*"
                    onChange={handleBgmUpload}
                    className="hidden"
                  />
                </label>
                {clip.bgm_path && (
                  <span className="text-[11px] text-emerald-400 font-mono">✓ Active</span>
                )}
              </div>

              {clip.bgm_path && (
                <div>
                  <div className="flex justify-between text-[11px] text-[var(--text-secondary)] mb-1">
                    <span>BGM Volume (Ducked)</span>
                    <span className="font-mono text-[var(--accent-primary)]">
                      {Math.round((clip.bgm_volume ?? 0.25) * 100)}%
                    </span>
                  </div>
                  <input
                    type="range"
                    min={0.05}
                    max={0.8}
                    step={0.05}
                    value={clip.bgm_volume ?? 0.25}
                    onChange={(e) => onUpdateClip({ bgm_volume: parseFloat(e.target.value) })}
                    className="w-full h-1.5 bg-[var(--bg-input)] rounded appearance-none cursor-pointer"
                  />
                </div>
              )}
            </div>

            {/* Voice Mastering Controls */}
            <div className="pt-3 border-t border-[var(--border-subtle)] space-y-2.5">
              <span className="font-bold text-xs text-[var(--text-primary)] flex items-center gap-1.5">
                <Volume2 className="w-3.5 h-3.5 text-[var(--accent-primary)]" />
                Voice Gain & Mastering
              </span>
              <div>
                <div className="flex justify-between text-[11px] text-[var(--text-secondary)] mb-1">
                  <span>Voice Volume / Gain</span>
                  <span className="font-mono text-[var(--accent-primary)] font-bold">
                    {Math.round((clip.audio_volume ?? 1.0) * 100)}%
                  </span>
                </div>
                <input
                  type="range"
                  min={0.0}
                  max={2.0}
                  step={0.05}
                  value={clip.audio_volume ?? 1.0}
                  onChange={(e) => onUpdateClip({ audio_volume: parseFloat(e.target.value) })}
                  className="w-full h-1.5 bg-[var(--bg-input)] rounded appearance-none cursor-pointer"
                />
              </div>
              <div className="grid grid-cols-2 gap-2">
                <button
                  type="button"
                  onClick={() => onUpdateClip({ normalize_audio: !(clip.normalize_audio ?? true) })}
                  className={`py-1.5 px-2.5 rounded-lg border text-[11px] font-medium text-left transition ${
                    (clip.normalize_audio ?? true)
                      ? 'bg-[var(--accent-subtle)] border-[var(--accent-primary)] text-[var(--accent-primary)]'
                      : 'bg-[var(--bg-input)] border-[var(--border-subtle)] text-[var(--text-secondary)]'
                  }`}
                >
                  {(clip.normalize_audio ?? true) ? '✓ ' : ''}Loudness -14 LUFS
                </button>
                <button
                  type="button"
                  onClick={() => onUpdateClip({ vocal_clarity: !(clip.vocal_clarity ?? false) })}
                  className={`py-1.5 px-2.5 rounded-lg border text-[11px] font-medium text-left transition ${
                    clip.vocal_clarity
                      ? 'bg-[var(--accent-subtle)] border-[var(--accent-primary)] text-[var(--accent-primary)]'
                      : 'bg-[var(--bg-input)] border-[var(--border-subtle)] text-[var(--text-secondary)]'
                  }`}
                >
                  {clip.vocal_clarity ? '✓ ' : ''}Vocal Denoise
                </button>
                <button
                  type="button"
                  onClick={() => onUpdateClip({ voice_isolation: !(clip.voice_isolation ?? false) })}
                  className={`py-1.5 px-2.5 rounded-lg border text-[11px] font-medium text-left transition ${
                    clip.voice_isolation
                      ? 'bg-[var(--accent-subtle)] border-[var(--accent-primary)] text-[var(--accent-primary)]'
                      : 'bg-[var(--bg-input)] border-[var(--border-subtle)] text-[var(--text-secondary)]'
                  }`}
                >
                  {clip.voice_isolation ? '✓ ' : ''}Studio Voice Isolation
                </button>
                <button
                  type="button"
                  onClick={() => onUpdateClip({ auto_sfx: !(clip.auto_sfx ?? false) })}
                  className={`py-1.5 px-2.5 rounded-lg border text-[11px] font-medium text-left transition ${
                    clip.auto_sfx
                      ? 'bg-[var(--accent-subtle)] border-[var(--accent-primary)] text-[var(--accent-primary)]'
                      : 'bg-[var(--bg-input)] border-[var(--border-subtle)] text-[var(--text-secondary)]'
                  }`}
                >
                  {clip.auto_sfx ? '✓ ' : ''}Auto Micro-SFX
                </button>
                <button
                  type="button"
                  onClick={() => onUpdateClip({ beat_sync_enabled: !(clip.beat_sync_enabled ?? false) })}
                  className={`py-1.5 px-2.5 rounded-lg border text-[11px] font-medium text-left transition ${
                    clip.beat_sync_enabled
                      ? 'bg-[var(--accent-subtle)] border-[var(--accent-primary)] text-[var(--accent-primary)]'
                      : 'bg-[var(--bg-input)] border-[var(--border-subtle)] text-[var(--text-secondary)]'
                  }`}
                >
                  {clip.beat_sync_enabled ? '✓ ' : ''}Snap Zooms &amp; Cuts to Beat
                </button>
              </div>

              {/* Live Audio Spectrum Visualizer */}
              <div className="pt-2.5 border-t border-[var(--border-subtle)]/70 space-y-2">
                <div className="flex items-center justify-between gap-2">
                  <span className="font-semibold text-[11px] text-[var(--text-primary)]">
                    Live Audio Spectrum Bar
                  </span>
                  <div className="flex items-center gap-2">
                    <input
                      type="color"
                      value={clip.spectrum_color || '#C6F32F'}
                      onChange={(e) => onUpdateClip({ spectrum_color: e.target.value })}
                      className="w-6 h-5 rounded cursor-pointer border border-[var(--border-subtle)] bg-transparent p-0"
                      title="Spectrum bar color"
                    />
                    <button
                      type="button"
                      onClick={() => onUpdateClip({ spectrum_enabled: !(clip.spectrum_enabled ?? false) })}
                      className={`px-2 py-0.5 rounded border text-[10px] font-bold transition ${
                        clip.spectrum_enabled
                          ? 'bg-[var(--accent-subtle)] border-[var(--accent-primary)] text-[var(--accent-primary)]'
                          : 'bg-[var(--bg-input)] border-[var(--border-subtle)] text-[var(--text-secondary)]'
                      }`}
                    >
                      {clip.spectrum_enabled ? '✓ Spectrum ON' : 'Spectrum OFF'}
                    </button>
                  </div>
                </div>
                <div className="grid grid-cols-3 gap-1.5">
                  {(['bottom', 'top', 'center'] as const).map((pos) => (
                    <button
                      key={pos}
                      type="button"
                      onClick={() =>
                        onUpdateClip({ spectrum_position: pos, spectrum_enabled: true })
                      }
                      className={`py-1 rounded border text-[10px] font-semibold capitalize transition ${
                        (clip.spectrum_position || 'bottom') === pos
                          ? 'bg-[var(--accent-subtle)] border-[var(--accent-primary)] text-[var(--accent-primary)]'
                          : 'bg-[var(--bg-input)] border-[var(--border-subtle)] text-[var(--text-secondary)]'
                      }`}
                    >
                      {pos}
                    </button>
                  ))}
                </div>
              </div>

              {/* 1-Click Local AI Voice Dubbing */}
              <div className="pt-2.5 border-t border-[var(--border-subtle)]/70 space-y-2">
                <div className="flex items-center justify-between gap-2">
                  <span className="font-semibold text-[11px] text-[var(--text-primary)]">
                    1-Click AI Voice Dubbing
                  </span>
                  <button
                    type="button"
                    onClick={() => onUpdateClip({ dubbing_enabled: !(clip.dubbing_enabled ?? false) })}
                    className={`px-2 py-0.5 rounded border text-[10px] font-bold transition ${
                      clip.dubbing_enabled
                        ? 'bg-[var(--accent-subtle)] border-[var(--accent-primary)] text-[var(--accent-primary)]'
                        : 'bg-[var(--bg-input)] border-[var(--border-subtle)] text-[var(--text-secondary)]'
                    }`}
                  >
                    {clip.dubbing_enabled ? '✓ Dub ON' : 'Dub OFF'}
                  </button>
                </div>
                <div className="grid grid-cols-3 gap-1.5">
                  <select
                    value={clip.dubbing_language || dubLang}
                    onChange={(e) => setDubLang(e.target.value)}
                    aria-label="Dubbing target language"
                    className="bg-[var(--bg-input)] border border-[var(--border-subtle)] rounded-lg px-2 py-1 text-[11px] text-[var(--text-primary)] outline-none"
                  >
                    {['Spanish', 'Hindi', 'Telugu', 'French', 'German', 'Portuguese', 'Japanese', 'Korean', 'Arabic', 'English'].map((lg) => (
                      <option key={lg} value={lg}>{lg}</option>
                    ))}
                  </select>
                  <select
                    value={clip.dubbing_voice || dubVoice}
                    onChange={(e) => setDubVoice(e.target.value)}
                    aria-label="Dubbing voice style"
                    className="bg-[var(--bg-input)] border border-[var(--border-subtle)] rounded-lg px-2 py-1 text-[11px] text-[var(--text-primary)] outline-none"
                  >
                    <option value="default">Neutral Voice</option>
                    <option value="female">Female Voice</option>
                    <option value="male">Male Voice</option>
                  </select>
                  <button
                    type="button"
                    disabled={isDubbing}
                    onClick={async () => {
                      if (!clip) return;
                      try {
                        setIsDubbing(true);
                        setDubNotice(null);
                        const chosenLang = clip.dubbing_language && dubLang === 'Spanish' ? clip.dubbing_language : dubLang;
                        const chosenVoice = clip.dubbing_voice && dubVoice === 'default' ? clip.dubbing_voice : dubVoice;
                        const res = await api.dubClip(clip.project_id, clip.id, {
                          target_language: chosenLang,
                          voice: chosenVoice,
                          original_bed_volume: clip.dubbing_bed_volume ?? 0.15,
                        });
                        onUpdateClip({
                          dubbing_enabled: true,
                          dubbing_language: chosenLang,
                          dubbing_voice: chosenVoice,
                          dubbed_audio_path: res.dubbed_audio_path,
                          transcript_words: res.clip.transcript_words,
                        });
                        setDubNotice(`Dubbed via ${res.tts_backend} (${res.segment_count} lines)`);
                      } catch (err: any) {
                        setDubNotice(err?.message || 'Dubbing failed');
                      } finally {
                        setIsDubbing(false);
                      }
                    }}
                    className="px-2 py-1 rounded-lg bg-[var(--accent-primary)] text-[var(--accent-foreground)] font-bold text-[10px] hover:opacity-90 transition disabled:opacity-50"
                  >
                    {isDubbing ? 'Dubbing...' : 'Generate Dub'}
                  </button>
                </div>
                <div>
                  <div className="flex justify-between text-[10px] text-[var(--text-secondary)] mb-0.5">
                    <span>Original Audio Bed</span>
                    <span className="font-mono text-[var(--accent-primary)]">
                      {Math.round((clip.dubbing_bed_volume ?? 0.15) * 100)}%
                    </span>
                  </div>
                  <input
                    type="range"
                    min={0.0}
                    max={0.6}
                    step={0.05}
                    value={clip.dubbing_bed_volume ?? 0.15}
                    onChange={(e) => onUpdateClip({ dubbing_bed_volume: parseFloat(e.target.value) })}
                    className="w-full h-1.5 bg-[var(--bg-input)] rounded appearance-none cursor-pointer"
                  />
                </div>
                {dubNotice && (
                  <div className="text-[10px] font-mono text-[var(--accent-primary)]">{dubNotice}</div>
                )}
              </div>
            </div>

            {/* Retention Progress Bar & 2s Outro CTA */}
            <div className="pt-3 border-t border-[var(--border-subtle)] space-y-2.5">
              <span className="font-bold text-xs text-[var(--text-primary)] flex items-center gap-1.5">
                <Flag className="w-3.5 h-3.5 text-[var(--accent-primary)]" />
                Retention Bar &amp; 2s Outro CTA
              </span>
              <div className="grid grid-cols-1 sm:grid-cols-2 gap-2">
                <div className="flex items-center justify-between gap-2 px-2.5 py-1.5 rounded-lg bg-[var(--bg-input)] border border-[var(--border-subtle)]">
                  <button
                    type="button"
                    onClick={() =>
                      onUpdateClip({ retention_bar_enabled: !(clip.retention_bar_enabled ?? false) })
                    }
                    className={`text-[11px] font-semibold flex items-center gap-1.5 ${
                      clip.retention_bar_enabled ? 'text-[var(--accent-primary)]' : 'text-[var(--text-secondary)]'
                    }`}
                  >
                    <span>{clip.retention_bar_enabled ? '✓ ' : ''}Progress Bar</span>
                  </button>
                  <input
                    type="color"
                    value={clip.retention_bar_color || '#C6F32F'}
                    onChange={(e) => onUpdateClip({ retention_bar_color: e.target.value })}
                    className="w-6 h-5 rounded cursor-pointer border border-[var(--border-subtle)] bg-transparent p-0"
                    title="Retention progress bar color"
                  />
                </div>

                <button
                  type="button"
                  onClick={() => onUpdateClip({ outro_cta_enabled: !(clip.outro_cta_enabled ?? false) })}
                  className={`py-1.5 px-2.5 rounded-lg border text-[11px] font-semibold text-left transition ${
                    clip.outro_cta_enabled
                      ? 'bg-[var(--accent-subtle)] border-[var(--accent-primary)] text-[var(--accent-primary)]'
                      : 'bg-[var(--bg-input)] border-[var(--border-subtle)] text-[var(--text-secondary)]'
                  }`}
                >
                  {clip.outro_cta_enabled ? '✓ ' : ''}2s Outro End-Card CTA
                </button>
              </div>

              {clip.outro_cta_enabled && (
                <input
                  type="text"
                  value={clip.outro_cta_text ?? 'Subscribe for More'}
                  onChange={(e) => onUpdateClip({ outro_cta_text: e.target.value })}
                  placeholder="Subscribe for More"
                  className="w-full bg-[var(--bg-input)] border border-[var(--border-subtle)] rounded-lg px-2.5 py-1.5 text-xs text-[var(--text-primary)] outline-none focus:border-[var(--accent-primary)]"
                />
              )}
            </div>

            {/* Anti-Reupload Animated Creator Handle Watermark */}
            <div className="pt-3 border-t border-[var(--border-subtle)] space-y-2.5">
              <div className="flex items-center justify-between gap-2">
                <span className="font-bold text-xs text-[var(--text-primary)] flex items-center gap-1.5">
                  <Shield className="w-3.5 h-3.5 text-[var(--accent-primary)]" />
                  Creator Handle Watermark
                </span>
                <button
                  type="button"
                  onClick={() => {
                    const nextOn = !(clip.handle_watermark_enabled ?? false);
                    onUpdateClip({
                      handle_watermark_enabled: nextOn,
                      ...(nextOn && !(clip.handle_text || '').trim() ? { handle_text: '@LostHeaven' } : {}),
                    });
                  }}
                  className={`px-2 py-0.5 rounded border text-[10px] font-bold transition ${
                    clip.handle_watermark_enabled
                      ? 'bg-[var(--accent-subtle)] border-[var(--accent-primary)] text-[var(--accent-primary)]'
                      : 'bg-[var(--bg-input)] border-[var(--border-subtle)] text-[var(--text-secondary)]'
                  }`}
                >
                  {clip.handle_watermark_enabled ? '✓ Watermark ON' : 'Watermark OFF'}
                </button>
              </div>

              <div className="grid grid-cols-1 sm:grid-cols-2 gap-2">
                <input
                  type="text"
                  value={clip.handle_text || ''}
                  onChange={(e) =>
                    onUpdateClip({
                      handle_text: e.target.value,
                      ...(e.target.value.trim() && !clip.handle_watermark_enabled
                        ? { handle_watermark_enabled: true }
                        : {}),
                    })
                  }
                  placeholder="@yourhandle"
                  className="px-2.5 py-1.5 rounded-lg bg-[var(--bg-input)] border border-[var(--border-subtle)] text-[11px] text-[var(--text-primary)] outline-none focus:border-[var(--accent-primary)]"
                />
                <select
                  aria-label="Creator Handle Corner"
                  value={clip.handle_corner || 'bottom_right'}
                  onChange={(e) => onUpdateClip({ handle_corner: e.target.value })}
                  className="px-2 py-1.5 rounded-lg bg-[var(--bg-input)] border border-[var(--border-subtle)] text-[11px] text-[var(--text-primary)] outline-none"
                >
                  <option value="bottom_right">Bottom Right</option>
                  <option value="bottom_left">Bottom Left</option>
                  <option value="top_right">Top Right</option>
                  <option value="top_left">Top Left</option>
                </select>
              </div>

              <div className="grid grid-cols-5 gap-1">
                {(['none', 'youtube', 'tiktok', 'instagram', 'x'] as const).map((icon) => (
                  <button
                    key={icon}
                    type="button"
                    onClick={() => onUpdateClip({ handle_platform_icon: icon })}
                    className={`py-1 rounded border text-[10px] font-semibold capitalize transition ${
                      (clip.handle_platform_icon || 'none') === icon
                        ? 'bg-[var(--accent-subtle)] border-[var(--accent-primary)] text-[var(--accent-primary)]'
                        : 'bg-[var(--bg-input)] border-[var(--border-subtle)] text-[var(--text-secondary)]'
                    }`}
                  >
                    {icon === 'none' ? 'Plain' : icon === 'x' ? 'X' : icon}
                  </button>
                ))}
              </div>

              <div className="flex flex-wrap items-center justify-between gap-2">
                <div className="flex-1 min-w-[130px]">
                  <div className="flex justify-between text-[10px] text-[var(--text-secondary)] mb-0.5">
                    <span>Watermark Opacity</span>
                    <span className="font-mono text-[var(--accent-primary)]">
                      {Math.round((clip.handle_opacity ?? 0.65) * 100)}%
                    </span>
                  </div>
                  <input
                    type="range"
                    min={0.20}
                    max={0.90}
                    step={0.05}
                    value={clip.handle_opacity ?? 0.65}
                    onChange={(e) => onUpdateClip({ handle_opacity: parseFloat(e.target.value) })}
                    className="w-full h-1.5 bg-[var(--bg-input)] rounded appearance-none cursor-pointer"
                  />
                </div>
                <div className="flex items-center gap-1">
                  {([
                    { id: 'always', label: 'Always On' },
                    { id: 'pulse_12s', label: 'Pulse 12s' },
                  ] as const).map((mode) => (
                    <button
                      key={mode.id}
                      type="button"
                      onClick={() => onUpdateClip({ handle_display_mode: mode.id })}
                      className={`px-2 py-1 rounded border text-[10px] font-semibold transition ${
                        (clip.handle_display_mode || 'always') === mode.id
                          ? 'bg-[var(--accent-subtle)] border-[var(--accent-primary)] text-[var(--accent-primary)]'
                          : 'bg-[var(--bg-input)] border-[var(--border-subtle)] text-[var(--text-secondary)]'
                      }`}
                    >
                      {mode.label}
                    </button>
                  ))}
                </div>
              </div>
            </div>

            {/* Auto B-Roll Cutaways */}
            <div className="pt-3 border-t border-[var(--border-subtle)] space-y-2.5">
              <div className="flex items-center justify-between gap-2">
                <span className="font-bold text-xs text-[var(--text-primary)] flex items-center gap-1.5">
                  <Film className="w-3.5 h-3.5 text-[var(--accent-primary)]" />
                  B-Roll Cutaways (Local / Pexels)
                </span>
                <button
                  type="button"
                  onClick={handleSuggestBroll}
                  disabled={loadingBroll}
                  className="px-2.5 py-1 rounded-lg bg-[var(--bg-input)] border border-[var(--border-subtle)] hover:border-[var(--accent-primary)]/50 text-[11px] font-semibold text-[var(--text-primary)] transition disabled:opacity-50"
                >
                  {loadingBroll ? 'Scanning...' : 'Suggest Cutaways'}
                </button>
              </div>
              {brollNotice && (
                <div className="text-[10px] font-mono text-[var(--accent-primary)]">{brollNotice}</div>
              )}
              {(clip.broll_segments || []).length > 0 ? (
                <div className="space-y-1.5 max-h-40 overflow-y-auto">
                  {(clip.broll_segments || []).map((seg, idx) => (
                    <div
                      key={`${seg.keyword}-${idx}`}
                      className="flex items-center justify-between gap-2 px-2.5 py-1.5 rounded-lg bg-[var(--bg-input)] border border-[var(--border-subtle)] text-[11px]"
                    >
                      <div className="min-w-0 flex-1">
                        <span className="font-bold text-[var(--text-primary)]">#{seg.keyword}</span>
                        <span className="ml-2 font-mono text-[10px] text-[var(--text-secondary)]">
                          {seg.start.toFixed(1)}s–{seg.end.toFixed(1)}s
                        </span>
                        <span className="ml-2 text-[10px] text-emerald-400">
                          {seg.asset_path ? seg.source : 'No asset'}
                        </span>
                      </div>
                      <div className="flex items-center gap-1.5 shrink-0">
                        <label className="px-2 py-0.5 rounded border border-[var(--border-subtle)] bg-[var(--bg-card)] cursor-pointer text-[10px] hover:border-[var(--accent-primary)]">
                          Upload
                          <input
                            type="file"
                            accept="video/*,image/*"
                            className="hidden"
                            onChange={(e) => {
                              const f = e.target.files?.[0];
                              if (f) handleAssignBrollAsset(idx, f);
                            }}
                          />
                        </label>
                        {seg.asset_path && (
                          <button
                            type="button"
                            onClick={() => {
                              const next = [...(clip.broll_segments || [])];
                              next[idx] = { ...next[idx], enabled: !next[idx].enabled };
                              onUpdateClip({ broll_segments: next });
                            }}
                            className={`px-2 py-0.5 rounded border text-[10px] font-bold ${
                              seg.enabled
                                ? 'bg-[var(--accent-subtle)] border-[var(--accent-primary)] text-[var(--accent-primary)]'
                                : 'bg-[var(--bg-card)] border-[var(--border-subtle)] text-[var(--text-secondary)]'
                            }`}
                          >
                            {seg.enabled ? 'ON' : 'OFF'}
                          </button>
                        )}
                      </div>
                    </div>
                  ))}
                </div>
              ) : (
                <p className="text-[10px] text-[var(--text-secondary)]">
                  Matches files in storage/broll/ or optional Pexels API key in Settings.
                </p>
              )}
            </div>

            {/* Smart Stat & Keyword Callout Pop-Up Cards */}
            <div className="pt-3 border-t border-[var(--border-subtle)] space-y-2.5">
              <div className="flex items-center justify-between gap-2">
                <span className="font-bold text-xs text-[var(--text-primary)] flex items-center gap-1.5">
                  <Sparkles className="w-3.5 h-3.5 text-[var(--accent-primary)]" />
                  Smart Callout Cards
                </span>
                <div className="flex items-center gap-1.5">
                  <button
                    type="button"
                    onClick={() => onUpdateClip({ auto_callouts: !(clip.auto_callouts ?? false) })}
                    className={`px-2 py-1 rounded-lg border text-[10px] font-bold transition ${
                      clip.auto_callouts
                        ? 'bg-[var(--accent-subtle)] border-[var(--accent-primary)] text-[var(--accent-primary)]'
                        : 'bg-[var(--bg-input)] border-[var(--border-subtle)] text-[var(--text-secondary)]'
                    }`}
                  >
                    {clip.auto_callouts ? '✓ Active' : 'Enable'}
                  </button>
                  <button
                    type="button"
                    onClick={handleExtractCallouts}
                    disabled={loadingCallouts}
                    className="px-2.5 py-1 rounded-lg bg-[var(--bg-input)] border border-[var(--border-subtle)] hover:border-[var(--accent-primary)]/50 text-[11px] font-semibold text-[var(--text-primary)] transition disabled:opacity-50"
                  >
                    {loadingCallouts ? 'Extracting...' : 'Auto Callouts'}
                  </button>
                </div>
              </div>
              {calloutNotice && (
                <div className="text-[10px] font-mono text-[var(--accent-primary)]">{calloutNotice}</div>
              )}
              {(clip.callout_cards || []).length > 0 && (
                <div className="space-y-1.5 max-h-40 overflow-y-auto">
                  {(clip.callout_cards || []).map((card, idx) => (
                    <div
                      key={card.id || idx}
                      className="flex items-center justify-between gap-2 px-2.5 py-1.5 rounded-lg bg-[var(--bg-input)] border border-[var(--border-subtle)] text-[11px]"
                    >
                      <input
                        type="text"
                        value={card.badge}
                        onChange={(e) => {
                          const next = [...(clip.callout_cards || [])];
                          next[idx] = { ...next[idx], badge: e.target.value };
                          onUpdateClip({ callout_cards: next });
                        }}
                        className="w-20 bg-[var(--bg-card)] border border-[var(--border-subtle)] rounded px-1.5 py-0.5 text-[10px] font-bold text-[var(--accent-primary)] outline-none"
                      />
                      <input
                        type="text"
                        value={card.title}
                        onChange={(e) => {
                          const next = [...(clip.callout_cards || [])];
                          next[idx] = { ...next[idx], title: e.target.value };
                          onUpdateClip({ callout_cards: next });
                        }}
                        className="flex-1 min-w-0 bg-[var(--bg-card)] border border-[var(--border-subtle)] rounded px-1.5 py-0.5 text-[10px] text-[var(--text-primary)] outline-none"
                      />
                      <span className="font-mono text-[9px] text-[var(--text-secondary)] shrink-0 tabular-nums">
                        {card.start.toFixed(1)}s
                      </span>
                      <button
                        type="button"
                        onClick={() => {
                          const next = [...(clip.callout_cards || [])];
                          next[idx] = { ...next[idx], enabled: !(next[idx].enabled ?? true) };
                          onUpdateClip({ callout_cards: next });
                        }}
                        className={`px-1.5 py-0.5 rounded border text-[9px] font-bold shrink-0 ${
                          (card.enabled ?? true)
                            ? 'bg-[var(--accent-subtle)] border-[var(--accent-primary)] text-[var(--accent-primary)]'
                            : 'bg-[var(--bg-card)] border-[var(--border-subtle)] text-[var(--text-secondary)]'
                        }`}
                      >
                        {(card.enabled ?? true) ? 'ON' : 'OFF'}
                      </button>
                    </div>
                  ))}
                </div>
              )}
            </div>

            {/* Tracked Privacy / HUD / Watermark Blur Boxes (up to 3) */}
            <div className="pt-3 border-t border-[var(--border-subtle)] space-y-2.5">
              <div className="flex items-center justify-between gap-2">
                <span className="font-bold text-xs text-[var(--text-primary)] flex items-center gap-1.5">
                  <Shield className="w-3.5 h-3.5 text-[var(--accent-primary)]" />
                  Privacy / HUD Blur Boxes
                </span>
                <button
                  type="button"
                  disabled={(clip.privacy_blur_boxes || []).length >= 3}
                  onClick={() => {
                    const existing = clip.privacy_blur_boxes || [];
                    if (existing.length >= 3) return;
                    const offset = existing.length * 0.08;
                    const nextBox = {
                      id: `blur_${Date.now().toString(36)}`,
                      x: Number((0.14 + offset).toFixed(2)),
                      y: Number((0.18 + offset).toFixed(2)),
                      w: 0.34,
                      h: 0.14,
                      mode: 'gaussian' as const,
                      track_motion: true,
                      intensity: 18,
                    };
                    onUpdateClip({ privacy_blur_boxes: [...existing, nextBox] });
                  }}
                  className="px-2.5 py-1 rounded-lg bg-[var(--bg-input)] border border-[var(--border-subtle)] hover:border-[var(--accent-primary)]/50 text-[11px] font-semibold text-[var(--text-primary)] transition disabled:opacity-45"
                >
                  + Add Blur Box ({(clip.privacy_blur_boxes || []).length}/3)
                </button>
              </div>
              {(clip.privacy_blur_boxes || []).length === 0 ? (
                <p className="text-[10px] text-[var(--text-secondary)]">
                  Hide usernames, stream HUDs, or license plates. Drag boxes directly on the preview frame.
                </p>
              ) : (
                <div className="space-y-2">
                  {(clip.privacy_blur_boxes || []).map((box, bIdx) => (
                    <div
                      key={box.id || bIdx}
                      className="p-2 rounded-lg bg-[var(--bg-input)] border border-[var(--border-subtle)] space-y-1.5 text-[10px]"
                    >
                      <div className="flex items-center justify-between gap-1.5 flex-wrap">
                        <span className="font-bold text-[var(--text-primary)] font-mono">
                          Box #{bIdx + 1} ({Math.round(box.x * 100)}%, {Math.round(box.y * 100)}%)
                        </span>
                        <div className="flex items-center gap-1">
                          <button
                            type="button"
                            onClick={() => {
                              const next = [...(clip.privacy_blur_boxes || [])];
                              next[bIdx] = {
                                ...next[bIdx],
                                mode: box.mode === 'pixelate' ? 'gaussian' : 'pixelate',
                              };
                              onUpdateClip({ privacy_blur_boxes: next });
                            }}
                            className="px-2 py-0.5 rounded bg-[var(--bg-card)] border border-[var(--border-subtle)] font-semibold text-[var(--text-primary)]"
                          >
                            {box.mode === 'pixelate' ? 'Pixelate' : 'Gaussian'}
                          </button>
                          <button
                            type="button"
                            onClick={() => {
                              const next = [...(clip.privacy_blur_boxes || [])];
                              next[bIdx] = { ...next[bIdx], track_motion: !box.track_motion };
                              onUpdateClip({ privacy_blur_boxes: next });
                            }}
                            className={`px-2 py-0.5 rounded border font-semibold ${
                              box.track_motion
                                ? 'bg-[var(--accent-subtle)] border-[var(--accent-primary)] text-[var(--accent-primary)]'
                                : 'bg-[var(--bg-card)] border-[var(--border-subtle)] text-[var(--text-secondary)]'
                            }`}
                          >
                            {box.track_motion ? '✓ Track Motion' : 'Static'}
                          </button>
                          <button
                            type="button"
                            onClick={() => {
                              const next = (clip.privacy_blur_boxes || []).filter((_, i) => i !== bIdx);
                              onUpdateClip({ privacy_blur_boxes: next });
                            }}
                            className="px-1.5 py-0.5 rounded border border-red-500/30 bg-red-500/10 text-red-400 hover:bg-red-500/20 font-bold"
                            title="Remove blur box"
                          >
                            ✕
                          </button>
                        </div>
                      </div>
                      <div className="flex items-center gap-2">
                        <span className="text-[var(--text-secondary)] shrink-0">Strength: {box.intensity ?? 18}</span>
                        <input
                          type="range"
                          min={6}
                          max={32}
                          step={2}
                          value={box.intensity ?? 18}
                          onChange={(e) => {
                            const next = [...(clip.privacy_blur_boxes || [])];
                            next[bIdx] = { ...next[bIdx], intensity: parseInt(e.target.value, 10) };
                            onUpdateClip({ privacy_blur_boxes: next });
                          }}
                          className="w-full h-1 bg-[var(--bg-card)] rounded appearance-none cursor-pointer"
                        />
                      </div>
                    </div>
                  ))}
                </div>
              )}
            </div>
          </div>
        </div>
      )}

      {/* TAB 3: YOUTUBE METADATA */}
      {subTab === 'metadata' && (
        <div className="bg-[var(--bg-card)] border border-[var(--border-subtle)] rounded-xl p-4 grid grid-cols-1 lg:grid-cols-2 gap-4 animate-fadeIn">
          {/* A/B Titles */}
          <div className="space-y-2">
            <span className="font-bold text-xs text-[var(--text-primary)] block">
              A/B Viral Hook Titles
            </span>
            <div className="space-y-1.5">
              {(clip.youtube_titles?.length
                ? clip.youtube_titles
                : [clip.selected_title || 'Viral Short #Shorts']
              ).map((title, idx) => {
                const isSelected = (clip.selected_title || clip.youtube_titles?.[0]) === title;
                return (
                  <div
                    key={idx}
                    onClick={() => onUpdateClip({ selected_title: title })}
                    className={`p-2 rounded-lg border flex items-center justify-between cursor-pointer transition ${
                      isSelected
                        ? 'border-[var(--accent-primary)] bg-[var(--accent-subtle)]'
                        : 'border-[var(--border-subtle)] bg-[var(--bg-input)] hover:border-[var(--accent-primary)]/40'
                    }`}
                  >
                    <div className="flex items-center gap-2 min-w-0 flex-1">
                      <span className="text-[10px] font-bold px-1.5 py-0.5 rounded bg-[var(--bg-card)] text-[var(--accent-primary)] tabular-nums">
                        #{idx + 1}
                      </span>
                      <span className="text-xs text-[var(--text-primary)] truncate font-medium">
                        {title}
                      </span>
                    </div>
                    <button
                      type="button"
                      onClick={(e) => {
                        e.stopPropagation();
                        handleCopyTitle(title, idx);
                      }}
                      className="p-1 text-[var(--text-secondary)] hover:text-[var(--text-primary)] shrink-0"
                      title="Copy title"
                    >
                      {copiedTitleIndex === idx ? (
                        <Check className="w-3.5 h-3.5 text-emerald-400" />
                      ) : (
                        <Copy className="w-3.5 h-3.5" />
                      )}
                    </button>
                  </div>
                );
              })}
            </div>
            <div className="pt-1">
              <label className="font-bold text-[11px] text-[var(--text-secondary)] block mb-1">
                Custom Selected Title
              </label>
              <input
                type="text"
                value={clip.selected_title || ''}
                onChange={(e) => onUpdateClip({ selected_title: e.target.value })}
                placeholder="Final YouTube Shorts title..."
                className="w-full bg-[var(--bg-input)] border border-[var(--border-subtle)] rounded-lg px-2.5 py-1.5 text-xs text-[var(--text-primary)] focus:outline-none focus:border-[var(--accent-primary)]"
              />
            </div>
          </div>

          {/* Description, Hashtags, Pinned Comment & Privacy */}
          <div className="space-y-2.5">
            <div>
              <label className="font-bold text-xs text-[var(--text-primary)] block mb-1">
                Video Description
              </label>
              <textarea
                rows={3}
                value={clip.youtube_description || ''}
                onChange={(e) => onUpdateClip({ youtube_description: e.target.value })}
                placeholder="Describe this Short..."
                className="w-full bg-[var(--bg-input)] border border-[var(--border-subtle)] rounded-lg p-2 text-xs text-[var(--text-primary)] focus:outline-none focus:border-[var(--accent-primary)]"
              />
            </div>

            <div className="grid grid-cols-1 sm:grid-cols-2 gap-2">
              <div>
                <label className="font-bold text-[11px] text-[var(--text-secondary)] block mb-1">
                  Hashtags
                </label>
                <input
                  type="text"
                  value={clip.hashtags || ''}
                  onChange={(e) => onUpdateClip({ hashtags: e.target.value })}
                  placeholder="#Shorts #Viral #AI"
                  className="w-full bg-[var(--bg-input)] border border-[var(--border-subtle)] rounded-lg px-2.5 py-1.5 text-xs text-[var(--text-primary)] focus:outline-none focus:border-[var(--accent-primary)]"
                />
              </div>
              <div>
                <label className="font-bold text-[11px] text-[var(--text-secondary)] block mb-1">
                  Pinned Comment
                </label>
                <input
                  type="text"
                  value={clip.pinned_comment || ''}
                  onChange={(e) => onUpdateClip({ pinned_comment: e.target.value })}
                  placeholder="Question for viewers..."
                  className="w-full bg-[var(--bg-input)] border border-[var(--border-subtle)] rounded-lg px-2.5 py-1.5 text-xs text-[var(--text-primary)] focus:outline-none focus:border-[var(--accent-primary)]"
                />
              </div>
            </div>

            <div className="flex items-center justify-between pt-1">
              <div className="flex items-center gap-2">
                <span className="text-xs text-[var(--text-secondary)]">Visibility:</span>
                <select
                  value={clip.privacy_status || 'private'}
                  onChange={(e) => onUpdateClip({ privacy_status: e.target.value as any })}
                  className="bg-[var(--bg-input)] border border-[var(--border-subtle)] rounded-lg px-2.5 py-1 text-xs text-[var(--text-primary)] outline-none focus:border-[var(--accent-primary)]"
                >
                  <option value="private">Private</option>
                  <option value="unlisted">Unlisted</option>
                  <option value="public">Public</option>
                </select>
              </div>
              <button
                type="button"
                onClick={handleSaveMetadata}
                className="flex items-center gap-1.5 px-3 py-1.5 rounded-lg bg-[var(--accent-primary)] text-[var(--accent-foreground)] font-bold text-xs hover:opacity-90 transition"
              >
                {metadataSaved ? <Check className="w-3.5 h-3.5" /> : <Save className="w-3.5 h-3.5" />}
                <span>{metadataSaved ? 'Saved!' : 'Save Metadata'}</span>
              </button>
            </div>
          </div>
        </div>
      )}
    </div>
  );
};
