import React, { useState, useEffect } from 'react';
import {
  Sun, Droplets, Sparkles, Shield, Upload, Music, Volume2, Copy, Check, RotateCcw, Save, Film, Flag, Eye, Sliders
} from 'lucide-react';
import { ViralClip, BrollSegment, PresetPreviewsResponse } from '../types';
import { api } from '../api/client';
import { GradeSlider } from './GradeSlider';

interface Step3VideoProps {
  clip: ViralClip | null;
  onUpdateClip: (updates: Partial<ViralClip>) => void;
  onUploadAsset?: (file: File) => Promise<{ url: string; filename: string; path: string }>;
  compareOriginal?: boolean;
  onCompareOriginalChange?: (held: boolean) => void;
}

export const Step3Video: React.FC<Step3VideoProps> = ({
  clip,
  onUpdateClip,
  onUploadAsset,
  compareOriginal = false,
  onCompareOriginalChange,
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
  const [lutNotice, setLutNotice] = useState<string | null>(null);
  const [logoNotice, setLogoNotice] = useState<string | null>(null);
  const [presetPreviews, setPresetPreviews] = useState<PresetPreviewsResponse | null>(null);
  const [loadingPresetPreviews, setLoadingPresetPreviews] = useState(false);

  useEffect(() => {
    if (!clip) {
      setPresetPreviews(null);
      return;
    }
    let cancelled = false;
    setLoadingPresetPreviews(true);
    api
      .getClipPresetPreviews(clip.project_id, clip.id, clip.lut_path || undefined)
      .then((res) => {
        if (!cancelled) setPresetPreviews(res);
      })
      .catch(() => {
        if (!cancelled) setPresetPreviews(null);
      })
      .finally(() => {
        if (!cancelled) setLoadingPresetPreviews(false);
      });
    return () => {
      cancelled = true;
    };
  }, [clip?.project_id, clip?.id, clip?.lut_path]);

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
      const activeLayout = clip.broll_layout === 'reaction_pip' ? 'reaction_pip' : 'fullscreen';
      const suggs: BrollSegment[] = (res?.suggestions || []).map((seg: BrollSegment) => ({
        ...seg,
        layout: seg.layout || activeLayout
      }));
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

  const exportResolutions: { id: ViralClip['clip_size']; label: string }[] = [
    { id: 'Auto', label: 'Auto' },
    { id: '1080p', label: '1080p' },
    { id: '720p', label: '720p' },
    { id: '480p', label: '480p' },
  ];

  const colorPresets: {
    id: string;
    label: string;
    swatch: string;
    values: Partial<ViralClip>;
  }[] = [
    {
      id: 'none',
      label: 'Original',
      swatch: 'from-zinc-700/60 to-zinc-900/90',
      values: {
        exposure: 0, brightness: 0, contrast: 1.0, lift_mids: 1.0, highlights: 0, shadows: 0,
        sharpness: 0, vignette: 0, color_temp: 0, tint_shift: 0, vibrance: 0, saturation: 1.0,
        matte_fade: 0, halation_glow: 0, warmth: 0, black_and_white: 0, vintage: 0,
        tint_color: 'none', tint_strength: 0, lut_path: '',
      },
    },
    {
      id: 'kodak_portra_400',
      label: 'Kodak Portra 400',
      swatch: 'from-amber-700/50 via-orange-900/40 to-stone-900/90',
      values: {
        exposure: 0.03, brightness: 0, contrast: 1.04, lift_mids: 1.0, highlights: -8, shadows: 14,
        sharpness: 0.12, vignette: 0.16, color_temp: 14, tint_shift: 4, vibrance: 12, saturation: 1.04,
        matte_fade: 14, halation_glow: 20, warmth: 0, black_and_white: 0, vintage: 0,
        tint_color: 'none', tint_strength: 0,
      },
    },
    {
      id: 'teal_orange',
      label: 'Teal & Orange',
      swatch: 'from-cyan-700/60 via-teal-900/40 to-amber-700/70',
      values: {
        exposure: 0, brightness: 0, contrast: 1.08, lift_mids: 1.0, highlights: 8, shadows: -10,
        sharpness: 0.20, vignette: 0.26, color_temp: 8, tint_shift: -6, vibrance: 16, saturation: 1.08,
        matte_fade: 6, halation_glow: 12, warmth: 0, black_and_white: 0, vintage: 0,
        tint_color: 'none', tint_strength: 0,
      },
    },
    {
      id: 'cyberpunk_neon',
      label: 'Cyberpunk Neon',
      swatch: 'from-fuchsia-700/60 via-violet-900/50 to-cyan-700/70',
      values: {
        exposure: 0.02, brightness: 0, contrast: 1.10, lift_mids: 1.0, highlights: 14, shadows: -10,
        sharpness: 0.24, vignette: 0.24, color_temp: -18, tint_shift: 26, vibrance: 28, saturation: 1.14,
        matte_fade: 4, halation_glow: 32, warmth: 0, black_and_white: 0, vintage: 0,
        tint_color: 'none', tint_strength: 0,
      },
    },
    {
      id: 'moody_noir',
      label: 'Moody Noir',
      swatch: 'from-slate-800/80 via-zinc-900/70 to-black',
      values: {
        exposure: -0.06, brightness: 0, contrast: 1.12, lift_mids: 1.0, highlights: -10, shadows: -18,
        sharpness: 0.18, vignette: 0.38, color_temp: -12, tint_shift: -2, vibrance: -12, saturation: 0.88,
        matte_fade: 18, halation_glow: 8, warmth: 0, black_and_white: 0, vintage: 0,
        tint_color: 'none', tint_strength: 0,
      },
    },
    {
      id: 'golden_hour',
      label: 'Golden Hour',
      swatch: 'from-yellow-600/60 via-amber-800/50 to-orange-950/90',
      values: {
        exposure: 0.06, brightness: 0, contrast: 1.04, lift_mids: 1.0, highlights: 12, shadows: 14,
        sharpness: 0.10, vignette: 0.18, color_temp: 30, tint_shift: 6, vibrance: 18, saturation: 1.08,
        matte_fade: 10, halation_glow: 28, warmth: 0, black_and_white: 0, vintage: 0,
        tint_color: 'none', tint_strength: 0,
      },
    },
    {
      id: 'bw_16mm',
      label: '16mm B&W',
      swatch: 'from-neutral-500/70 via-neutral-800/80 to-black',
      values: {
        exposure: -0.02, brightness: 0, contrast: 1.16, lift_mids: 1.0, highlights: 10, shadows: -14,
        sharpness: 0.22, vignette: 0.36, color_temp: 0, tint_shift: 0, vibrance: 0, saturation: 0.88,
        matte_fade: 16, halation_glow: 16, warmth: 0, black_and_white: 0, vintage: 0,
        tint_color: 'none', tint_strength: 0,
      },
    },
    {
      id: 'gaming_pop',
      label: 'Gaming Pop',
      swatch: 'from-emerald-600/60 via-sky-800/50 to-indigo-950/90',
      values: {
        exposure: 0.04, brightness: 0, contrast: 1.12, lift_mids: 1.0, highlights: 12, shadows: -8,
        sharpness: 0.34, vignette: 0.0, color_temp: 4, tint_shift: 0, vibrance: 26, saturation: 1.18,
        matte_fade: 0, halation_glow: 0, warmth: 0, black_and_white: 0, vintage: 0,
        tint_color: 'none', tint_strength: 0,
      },
    },
    {
      id: 'clean_rec709',
      label: 'Clean Rec.709',
      swatch: 'from-sky-700/40 via-zinc-800/60 to-zinc-950/90',
      values: {
        exposure: 0.02, brightness: 0, contrast: 1.04, lift_mids: 1.0, highlights: 4, shadows: 4,
        sharpness: 0.14, vignette: 0.0, color_temp: 2, tint_shift: 0, vibrance: 10, saturation: 1.04,
        matte_fade: 0, halation_glow: 0, warmth: 0, black_and_white: 0, vintage: 0,
        tint_color: 'none', tint_strength: 0,
      },
    },
  ];

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
      preset_intensity: 100,
      exposure: 0,
      brightness: 0,
      contrast: 1.0,
      lift_mids: 1.0,
      highlights: 0,
      shadows: 0,
      sharpness: 0,
      vignette: 0,
      warmth: 0,
      saturation: 1.0,
      black_and_white: 0,
      vintage: 0,
      tint_color: 'none',
      tint_strength: 0,
      color_temp: 0,
      tint_shift: 0,
      vibrance: 0,
      matte_fade: 0,
      halation_glow: 0,
      lut_path: ''
    });
    setLutNotice(null);
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

  const activePresetStrength = Math.round(clip.preset_intensity ?? 100);
  const exposureUi = Math.round((clip.exposure || 0) * 100);
  const contrastUi = Math.round(((clip.contrast ?? 1.0) - 1.0) * 100);
  const highlightsUi = Math.round(clip.highlights ?? 0);
  const shadowsUi = Math.round(clip.shadows ?? 0);
  const clarityUi = Math.round((clip.sharpness || 0) * 100);
  const vignetteUi = Math.round(vignetteVal * 100);

  const colorTempUi = Math.round(clip.color_temp ?? 0);
  const tintShiftUi = Math.round(clip.tint_shift ?? 0);
  const vibranceUi = Math.round(clip.vibrance ?? 0);
  const saturationUi = Math.round(((clip.saturation ?? 1.0) - 1.0) * 100);
  const matteFadeUi = Math.round(clip.matte_fade ?? 0);
  const halationUi = Math.round(clip.halation_glow ?? 0);

  const thumbUrlByPreset: Record<string, string> = {};
  presetPreviews?.presets?.forEach((item) => {
    if (item.thumbnail_url) thumbUrlByPreset[item.id] = item.thumbnail_url;
  });

  const activeExportFps = String(clip.export_fps || 'source') === '60' ? '60' : 'source';

  return (
    <div className="space-y-4 text-xs animate-fadeIn w-full text-[var(--text-primary)]">
      {/* Top Row: Compact Export Output + 3x3 Live-Thumbnail Look Presets */}
      <div className="grid grid-cols-1 lg:grid-cols-[270px_minmax(0,1fr)] gap-4 items-stretch">
        {/* Left: Compact Export Output */}
        <div className="bg-[var(--bg-card)] border border-[var(--border-subtle)] rounded-xl p-4 flex flex-col justify-between gap-3">
          <div className="space-y-3">
            <div className="flex items-center justify-between gap-2">
              <span className="font-bold text-xs uppercase tracking-wide flex items-center gap-1.5">
                <Film className="w-3.5 h-3.5 text-[var(--accent-primary)]" />
                Export Output
              </span>
              <span className="text-[10px] font-mono text-[var(--text-muted)]">MP4 H.264</span>
            </div>

            <div>
              <div className="flex items-center justify-between text-[10px] text-[var(--text-secondary)] mb-1.5">
                <span>Resolution</span>
                <span className="font-mono text-[var(--accent-primary)] font-semibold">
                  {clip.clip_size || 'Auto'}
                </span>
              </div>
              <div className="grid grid-cols-4 gap-1.5 tabular-nums">
                {exportResolutions.map((sz) => (
                  <button
                    key={sz.id}
                    type="button"
                    onClick={() => onUpdateClip({ clip_size: sz.id })}
                    className={`py-1.5 rounded-lg border text-center text-[11px] font-medium transition ${
                      clip.clip_size === sz.id
                        ? 'bg-[var(--accent-primary)] text-[var(--accent-foreground)] font-bold border-[var(--accent-primary)]'
                        : 'bg-[var(--bg-input)] border-[var(--border-subtle)] text-[var(--text-secondary)] hover:text-[var(--text-primary)]'
                    }`}
                  >
                    {sz.label}
                  </button>
                ))}
              </div>
            </div>

            <div>
              <div className="flex items-center justify-between text-[10px] text-[var(--text-secondary)] mb-1.5">
                <span>Frame Rate</span>
                <span className="font-mono text-[var(--accent-primary)] font-semibold">
                  {activeExportFps === '60' ? '60 fps' : 'Source'}
                </span>
              </div>
              <div className="grid grid-cols-2 gap-1.5 tabular-nums">
                {[
                  { id: 'source', label: 'Source FPS' },
                  { id: '60', label: '60 fps' },
                ].map((opt) => (
                  <button
                    key={opt.id}
                    type="button"
                    onClick={() => onUpdateClip({ export_fps: opt.id })}
                    className={`py-1.5 rounded-lg border text-center text-[11px] font-medium transition ${
                      activeExportFps === opt.id
                        ? 'bg-[var(--accent-primary)] text-[var(--accent-foreground)] font-bold border-[var(--accent-primary)]'
                        : 'bg-[var(--bg-input)] border-[var(--border-subtle)] text-[var(--text-secondary)] hover:text-[var(--text-primary)]'
                    }`}
                  >
                    {opt.label}
                  </button>
                ))}
              </div>
            </div>
          </div>

          <div className="pt-2.5 border-t border-[var(--border-subtle)]/70 flex items-center justify-between text-[10px] text-[var(--text-muted)]">
            <span>Active Look</span>
            <span className="font-mono font-semibold text-[var(--text-primary)] truncate max-w-[140px]">
              {clip.lut_path
                ? '3D .cube LUT'
                : colorPresets.find((p) => p.id === (clip.color_preset || 'none'))?.label ||
                  (clip.color_preset || 'Original')}
            </span>
          </div>
        </div>

        {/* Right: 3x3 Live-Thumbnail Visual Look Presets & .cube 3D LUT Studio */}
        <div className="bg-[var(--bg-card)] border border-[var(--border-subtle)] rounded-xl p-4 space-y-3">
          <div className="flex flex-wrap items-center justify-between gap-2">
            <span className="font-bold text-xs uppercase tracking-wide flex items-center gap-1.5">
              <Sliders className="w-3.5 h-3.5 text-[var(--accent-primary)]" />
              Visual Look Presets
            </span>
            <div className="flex items-center gap-2 shrink-0">
              {onCompareOriginalChange && (
                <button
                  type="button"
                  onPointerDown={() => onCompareOriginalChange(true)}
                  onPointerUp={() => onCompareOriginalChange(false)}
                  onPointerLeave={() => onCompareOriginalChange(false)}
                  onPointerCancel={() => onCompareOriginalChange(false)}
                  className={`px-2 py-1 rounded-lg border text-[10px] font-semibold flex items-center gap-1 transition select-none ${
                    compareOriginal
                      ? 'bg-[var(--accent-primary)] text-[var(--accent-foreground)] border-[var(--accent-primary)]'
                      : 'bg-[var(--bg-input)] border-[var(--border-subtle)] text-[var(--text-secondary)] hover:text-[var(--text-primary)]'
                  }`}
                  title="Hold to view raw ungraded frame for comparison"
                >
                  <Eye className="w-3 h-3" />
                  <span>Compare Original (Hold)</span>
                </button>
              )}
              <button
                type="button"
                onClick={resetLightAndColor}
                className="text-[10px] text-[var(--text-secondary)] hover:text-[var(--accent-primary)] flex items-center gap-1 transition"
              >
                <RotateCcw className="w-3 h-3" /> Reset Grade
              </button>
            </div>
          </div>

          <div className="grid grid-cols-3 gap-2">
            {colorPresets.map((p) => {
              const active = (clip.color_preset || 'none') === p.id;
              const thumbUrl = thumbUrlByPreset[p.id];
              return (
                <button
                  key={p.id}
                  type="button"
                  onClick={() =>
                    onUpdateClip({
                      color_preset: p.id,
                      preset_intensity: (clip.preset_intensity ?? 100) <= 0 ? 100 : clip.preset_intensity ?? 100,
                      ...p.values,
                    })
                  }
                  className={`group relative aspect-video w-full rounded-lg overflow-hidden border text-left transition ${
                    active
                      ? 'border-[var(--accent-primary)] ring-2 ring-[var(--accent-primary)]/70'
                      : 'border-[var(--border-subtle)] hover:border-[var(--accent-primary)]/50'
                  }`}
                >
                  {thumbUrl ? (
                    <img
                      src={thumbUrl}
                      alt={p.label}
                      loading="lazy"
                      className="w-full h-full object-cover transition-transform duration-200 group-hover:scale-105"
                    />
                  ) : (
                    <div
                      className={`w-full h-full bg-gradient-to-br ${p.swatch} ${
                        loadingPresetPreviews ? 'animate-pulse' : ''
                      }`}
                    />
                  )}
                  <div className="absolute inset-x-0 bottom-0 bg-gradient-to-t from-black/90 via-black/55 to-transparent px-1.5 py-1 flex items-center justify-between gap-1">
                    <span
                      className={`text-[10px] leading-tight truncate ${
                        active ? 'text-[var(--accent-primary)] font-bold' : 'text-zinc-100 font-semibold'
                      }`}
                    >
                      {p.label}
                    </span>
                    {active && (
                      <span className="w-1.5 h-1.5 rounded-full bg-[var(--accent-primary)] shrink-0" />
                    )}
                  </div>
                </button>
              );
            })}
          </div>

          {/* Preset Strength + Upload .cube 3D LUT */}
          <div className="pt-2 border-t border-[var(--border-subtle)]/70 grid grid-cols-1 sm:grid-cols-[minmax(0,1fr)_auto] gap-3 items-center">
            <GradeSlider
              label="Preset Strength"
              value={activePresetStrength}
              min={0}
              max={100}
              step={1}
              defaultValue={100}
              unit="%"
              onChange={(val) => onUpdateClip({ preset_intensity: val })}
            />

            <div className="flex flex-wrap items-center gap-2 justify-end">
              {presetPreviews?.lut_preview?.thumbnail_url && clip.lut_path && (
                <img
                  src={presetPreviews.lut_preview.thumbnail_url}
                  alt="Custom LUT Preview"
                  className="w-12 h-7 object-cover rounded border border-[var(--accent-primary)]/70 shrink-0"
                />
              )}
              <label className="flex items-center gap-1.5 px-2.5 py-1.5 bg-[var(--bg-input)] hover:border-[var(--accent-primary)]/50 border border-[var(--border-subtle)] rounded-lg cursor-pointer text-[11px] font-semibold whitespace-nowrap">
                <Upload className="w-3 h-3 text-[var(--accent-primary)]" />
                <span>{clip.lut_path ? 'Replace .cube 3D LUT' : 'Upload .cube 3D LUT'}</span>
                <input
                  type="file"
                  accept=".cube"
                  className="hidden"
                  onChange={async (e) => {
                    const file = e.target.files?.[0];
                    if (!file) return;
                    try {
                      const res = await api.uploadLut(file);
                      onUpdateClip({ lut_path: res.path });
                      setLutNotice(`Loaded LUT: ${res.name}`);
                    } catch {
                      setLutNotice('Failed to upload .cube LUT');
                    }
                  }}
                />
              </label>
              {clip.lut_path && (
                <button
                  type="button"
                  onClick={() => {
                    onUpdateClip({ lut_path: '' });
                    setLutNotice(null);
                  }}
                  className="text-[10px] text-red-400 hover:underline whitespace-nowrap"
                >
                  Clear LUT
                </button>
              )}
              {lutNotice && (
                <span className="text-[10px] font-mono text-[var(--accent-primary)]">{lutNotice}</span>
              )}
            </div>
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

      {/* TAB 1: BALANCED 6 vs 6 LIGHT & COLOR STUDIO */}
      {subTab === 'grade' && (
        <div className="grid grid-cols-1 lg:grid-cols-2 gap-4 animate-fadeIn tabular-nums">
          {/* Tone & Optics (6 sliders) */}
          <div className="bg-[var(--bg-card)] border border-[var(--border-subtle)] rounded-xl p-4 space-y-3">
            <div className="flex items-center justify-between font-bold text-xs text-[var(--text-primary)]">
              <span className="flex items-center gap-2">
                <Sun className="w-4 h-4 text-[var(--accent-primary)]" />
                <span>Tone & Optics</span>
              </span>
              <span className="text-[10px] font-normal text-[var(--text-muted)]">Double-click to reset</span>
            </div>

            <GradeSlider
              label="Exposure"
              value={exposureUi}
              min={-100}
              max={100}
              step={1}
              defaultValue={0}
              bipolar
              onChange={(val) => onUpdateClip({ exposure: Number((val / 100).toFixed(2)), brightness: 0 })}
            />
            <GradeSlider
              label="Contrast"
              value={contrastUi}
              min={-100}
              max={100}
              step={1}
              defaultValue={0}
              bipolar
              onChange={(val) => onUpdateClip({ contrast: Number((1.0 + val / 100).toFixed(2)) })}
            />
            <GradeSlider
              label="Highlights"
              value={highlightsUi}
              min={-100}
              max={100}
              step={1}
              defaultValue={0}
              bipolar
              onChange={(val) => onUpdateClip({ highlights: val })}
            />
            <GradeSlider
              label="Shadows (Lift)"
              value={shadowsUi}
              min={-100}
              max={100}
              step={1}
              defaultValue={0}
              bipolar
              onChange={(val) => onUpdateClip({ shadows: val })}
            />
            <GradeSlider
              label="Clarity & Crispness"
              value={clarityUi}
              min={0}
              max={100}
              step={1}
              defaultValue={0}
              unit="%"
              onChange={(val) => onUpdateClip({ sharpness: Number((val / 100).toFixed(2)) })}
            />
            <GradeSlider
              label="Cinema Vignette"
              value={vignetteUi}
              min={0}
              max={100}
              step={1}
              defaultValue={0}
              unit="%"
              onChange={(val) => onUpdateClip({ vignette: Number((val / 100).toFixed(2)) })}
            />
          </div>

          {/* Color & Film Character (6 sliders) */}
          <div className="bg-[var(--bg-card)] border border-[var(--border-subtle)] rounded-xl p-4 space-y-3">
            <div className="flex items-center justify-between font-bold text-xs text-[var(--text-primary)]">
              <span className="flex items-center gap-2">
                <Droplets className="w-4 h-4 text-[var(--accent-primary)]" />
                <span>Color & Film Character</span>
              </span>
              <span className="text-[10px] font-normal text-[var(--text-muted)]">0 = neutral center</span>
            </div>

            <GradeSlider
              label="Temperature (Cool ↔ Warm)"
              value={colorTempUi}
              min={-100}
              max={100}
              step={1}
              defaultValue={0}
              bipolar
              onChange={(val) => onUpdateClip({ color_temp: val, warmth: 0 })}
            />
            <GradeSlider
              label="Tint (Green ↔ Magenta)"
              value={tintShiftUi}
              min={-100}
              max={100}
              step={1}
              defaultValue={0}
              bipolar
              onChange={(val) => onUpdateClip({ tint_shift: val })}
            />
            <GradeSlider
              label="Vibrance (Skin-Safe)"
              value={vibranceUi}
              min={-100}
              max={100}
              step={1}
              defaultValue={0}
              bipolar
              onChange={(val) => onUpdateClip({ vibrance: val })}
            />
            <GradeSlider
              label="Saturation"
              value={saturationUi}
              min={-100}
              max={100}
              step={1}
              defaultValue={0}
              bipolar
              onChange={(val) =>
                onUpdateClip({
                  saturation: Number((1.0 + val / 100).toFixed(2)),
                  black_and_white: val <= -99 ? 1.0 : 0,
                })
              }
            />
            <GradeSlider
              label="Matte Fade (Film Blacks)"
              value={matteFadeUi}
              min={0}
              max={100}
              step={1}
              defaultValue={0}
              unit="%"
              onChange={(val) => onUpdateClip({ matte_fade: val, vintage: 0 })}
            />
            <GradeSlider
              label="Film Halation Glow"
              value={halationUi}
              min={0}
              max={100}
              step={1}
              defaultValue={0}
              unit="%"
              onChange={(val) => onUpdateClip({ halation_glow: val })}
            />
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
                    bottom_fade: 0,
                    depth_pop: 0,
                    chroma_split: 0,
                    letterbox: 'none',
                    speed_ramp: 1.0
                  })
                }
                className="text-[10px] text-[var(--text-secondary)] hover:text-[var(--accent-primary)] flex items-center gap-1"
              >
                <RotateCcw className="w-3 h-3" /> Reset FX
              </button>
            </div>

            <div className="grid grid-cols-2 gap-3">
              {[
                { label: 'Dreamy Glow', key: 'glow', val: clip.glow || 0, max: 1.0, step: 0.05 },
                { label: 'Skin Smooth', key: 'smooth', val: clip.smooth || 0, max: 1.0, step: 0.05 },
                { label: 'Soft Blur', key: 'blur', val: clip.blur || 0, max: 1.0, step: 0.05 },
                { label: 'Film Grain', key: 'film_grain', val: clip.film_grain || 0, max: 1.0, step: 0.05 },
                { label: 'Bottom Fade', key: 'bottom_fade', val: clip.bottom_fade || 0, max: 1.0, step: 0.05 },
                { label: 'Subject Depth Pop (f/1.8)', key: 'depth_pop', val: clip.depth_pop || 0, max: 1.0, step: 0.05 },
                { label: 'RGB Chromatic Split', key: 'chroma_split', val: (clip.chroma_split ?? 0) > 1 ? (clip.chroma_split ?? 0) / 100 : (clip.chroma_split ?? 0), max: 1.0, step: 0.05 }
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
                    max={s.max}
                    step={s.step}
                    value={s.val}
                    onChange={(e) => onUpdateClip({ [s.key]: parseFloat(e.target.value) } as any)}
                    className="w-full h-1.5 bg-[var(--bg-input)] rounded appearance-none cursor-pointer"
                  />
                </div>
              ))}
            </div>

            <div className="pt-2.5 border-t border-[var(--border-subtle)]/70 grid grid-cols-1 sm:grid-cols-2 gap-3">
              <div>
                <span className="text-[11px] text-[var(--text-secondary)] block mb-1">Playback Speed Ramp</span>
                <div className="grid grid-cols-4 gap-1 bg-[var(--bg-input)] p-1 rounded-lg border border-[var(--border-subtle)]">
                  {[0.95, 1.0, 1.06, 1.12].map((spd) => {
                    const active = Math.abs((clip.speed_ramp ?? 1.0) - spd) < 0.015;
                    return (
                      <button
                        key={spd}
                        type="button"
                        onClick={() => onUpdateClip({ speed_ramp: spd })}
                        className={`py-1 rounded-md text-[10px] font-mono font-bold transition ${
                          active
                            ? 'bg-[var(--accent-primary)] text-[var(--accent-foreground)]'
                            : 'text-[var(--text-secondary)] hover:text-[var(--text-primary)]'
                        }`}
                      >
                        {spd.toFixed(2).replace(/\.00$/, '.0')}x
                      </button>
                    );
                  })}
                </div>
              </div>

              <div>
                <span className="text-[11px] text-[var(--text-secondary)] block mb-1">Cinema Letterbox</span>
                <div className="grid grid-cols-3 gap-1 bg-[var(--bg-input)] p-1 rounded-lg border border-[var(--border-subtle)]">
                  {[
                    { id: 'none', label: 'None' },
                    { id: '1.85:1', label: '1.85:1' },
                    { id: '2.39:1', label: '2.39:1' },
                  ].map((lb) => {
                    const active = (clip.letterbox || 'none').toLowerCase() === lb.id;
                    return (
                      <button
                        key={lb.id}
                        type="button"
                        onClick={() => onUpdateClip({ letterbox: lb.id })}
                        className={`py-1 rounded-md text-[10px] font-mono font-bold transition ${
                          active
                            ? 'bg-[var(--accent-primary)] text-[var(--accent-foreground)]'
                            : 'text-[var(--text-secondary)] hover:text-[var(--text-primary)]'
                        }`}
                      >
                        {lb.label}
                      </button>
                    );
                  })}
                </div>
              </div>
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
                  {(clip.normalize_audio ?? true) ? '✓ ' : ''}Loudness Normalize
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
                  onClick={() => onUpdateClip({ de_esser: !(clip.de_esser ?? false) })}
                  className={`py-1.5 px-2.5 rounded-lg border text-[11px] font-medium text-left transition ${
                    clip.de_esser
                      ? 'bg-[var(--accent-subtle)] border-[var(--accent-primary)] text-[var(--accent-primary)]'
                      : 'bg-[var(--bg-input)] border-[var(--border-subtle)] text-[var(--text-secondary)]'
                  }`}
                  title="Attenuates harsh 6.5 kHz sibilance on 'S' and 'T' sounds"
                >
                  {clip.de_esser ? '✓ ' : ''}De-Esser (6.5 kHz)
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

              {/* Voice EQ Presets & Broadcast LUFS Target */}
              <div className="pt-2 space-y-2 border-t border-[var(--border-subtle)]/70">
                <div>
                  <span className="text-[10px] font-bold uppercase text-[var(--text-secondary)] block mb-1">
                    Voice EQ Preset
                  </span>
                  <div className="grid grid-cols-2 sm:grid-cols-4 gap-1 bg-[var(--bg-input)] p-1 rounded-lg border border-[var(--border-subtle)]">
                    {['Raw', 'Podcast Warmth', 'Crisp Broadcast', 'Gaming Clarity'].map((eq) => {
                      const active = (clip.voice_eq_preset || 'Raw') === eq;
                      return (
                        <button
                          key={eq}
                          type="button"
                          onClick={() => onUpdateClip({ voice_eq_preset: eq })}
                          className={`py-1 px-1 rounded-md text-[10px] font-semibold transition truncate ${
                            active
                              ? 'bg-[var(--accent-primary)] text-[var(--accent-foreground)] font-bold'
                              : 'text-[var(--text-secondary)] hover:text-[var(--text-primary)]'
                          }`}
                        >
                          {eq}
                        </button>
                      );
                    })}
                  </div>
                </div>
                <div>
                  <span className="text-[10px] font-bold uppercase text-[var(--text-secondary)] block mb-1">
                    Broadcast Loudness Target
                  </span>
                  <div className="grid grid-cols-3 gap-1 bg-[var(--bg-input)] p-1 rounded-lg border border-[var(--border-subtle)]">
                    {['-16 LUFS', '-14 LUFS', '-11 LUFS'].map((lt) => {
                      const active = (clip.loudness_target || '-14 LUFS') === lt;
                      return (
                        <button
                          key={lt}
                          type="button"
                          onClick={() => onUpdateClip({ loudness_target: lt, normalize_audio: true })}
                          className={`py-1 rounded-md text-[10px] font-mono font-bold transition ${
                            active
                              ? 'bg-[var(--accent-primary)] text-[var(--accent-foreground)]'
                              : 'text-[var(--text-secondary)] hover:text-[var(--text-primary)]'
                          }`}
                        >
                          {lt}
                        </button>
                      );
                    })}
                  </div>
                </div>
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
                <div className="space-y-2 p-2.5 rounded-lg bg-[var(--bg-input)] border border-[var(--border-subtle)]">
                  <div className="grid grid-cols-1 sm:grid-cols-2 gap-2">
                    <input
                      type="text"
                      value={clip.outro_cta_headline ?? ''}
                      onChange={(e) => onUpdateClip({ outro_cta_headline: e.target.value })}
                      placeholder="End-Card Headline (e.g. WATCH NEXT)"
                      className="w-full bg-[var(--bg-card)] border border-[var(--border-subtle)] rounded-lg px-2.5 py-1.5 text-xs text-[var(--text-primary)] outline-none focus:border-[var(--accent-primary)]"
                    />
                    <input
                      type="text"
                      value={clip.outro_cta_handle ?? ''}
                      onChange={(e) => onUpdateClip({ outro_cta_handle: e.target.value })}
                      placeholder="@Handle (e.g. @LostHeaven)"
                      className="w-full bg-[var(--bg-card)] border border-[var(--border-subtle)] rounded-lg px-2.5 py-1.5 text-xs text-[var(--text-primary)] outline-none focus:border-[var(--accent-primary)]"
                    />
                  </div>
                  <div className="flex flex-wrap items-center gap-2">
                    <input
                      type="text"
                      value={clip.outro_cta_text ?? 'Subscribe for More'}
                      onChange={(e) => onUpdateClip({ outro_cta_text: e.target.value })}
                      placeholder="Button Badge Text (Subscribe for More)"
                      className="flex-1 min-w-[160px] bg-[var(--bg-card)] border border-[var(--border-subtle)] rounded-lg px-2.5 py-1.5 text-xs text-[var(--text-primary)] outline-none focus:border-[var(--accent-primary)]"
                    />
                    <label className="flex items-center gap-1.5 px-2.5 py-1.5 bg-[var(--bg-card)] hover:border-[var(--accent-primary)]/40 border border-[var(--border-subtle)] rounded-lg cursor-pointer text-[11px] font-semibold shrink-0">
                      <Upload className="w-3 h-3 text-[var(--accent-primary)]" />
                      <span>{clip.outro_logo_path ? 'Change Brand Logo' : 'Brand Logo PNG'}</span>
                      <input
                        type="file"
                        accept="image/png,image/jpeg,image/webp"
                        className="hidden"
                        onChange={async (e) => {
                          const file = e.target.files?.[0];
                          if (!file) return;
                          try {
                            const res = await api.uploadBrandLogo(file);
                            onUpdateClip({ outro_logo_path: res.path });
                            setLogoNotice('Logo attached');
                          } catch {
                            setLogoNotice('Upload failed');
                          }
                        }}
                      />
                    </label>
                    {clip.outro_logo_path && (
                      <button
                        type="button"
                        onClick={() => {
                          onUpdateClip({ outro_logo_path: '' });
                          setLogoNotice(null);
                        }}
                        className="text-[10px] text-red-400 hover:underline"
                      >
                        Remove Logo
                      </button>
                    )}
                    {logoNotice && <span className="text-[10px] font-mono text-emerald-400">{logoNotice}</span>}
                  </div>
                </div>
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
              <div className="grid grid-cols-2 gap-1.5">
                {(
                  [
                    { id: 'fullscreen', label: 'Full-Screen Cutaway' },
                    { id: 'reaction_pip', label: 'B-Roll + Speaker Reaction PiP' }
                  ] as const
                ).map((mode) => {
                  const selected = (clip.broll_layout || 'fullscreen') === mode.id;
                  return (
                    <button
                      key={mode.id}
                      type="button"
                      onClick={() => {
                        const updatedSegs = (clip.broll_segments || []).map((seg) => ({
                          ...seg,
                          layout: mode.id
                        }));
                        onUpdateClip({
                          broll_layout: mode.id,
                          ...(updatedSegs.length > 0 ? { broll_segments: updatedSegs } : {})
                        });
                      }}
                      className={`py-1.5 px-2 rounded-lg text-[10px] font-semibold border transition text-center ${
                        selected
                          ? 'bg-[var(--accent-subtle)] border-[var(--accent-primary)] text-[var(--accent-primary)]'
                          : 'bg-[var(--bg-input)] border-[var(--border-subtle)] text-[var(--text-secondary)] hover:text-[var(--text-primary)]'
                      }`}
                    >
                      {mode.label}
                    </button>
                  );
                })}
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
