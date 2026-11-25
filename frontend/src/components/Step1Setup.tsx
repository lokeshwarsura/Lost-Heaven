import React, { useState } from 'react';
import {
  Cpu, Zap, ChevronDown, ChevronUp, Sparkles, Scissors, ZoomIn, MessageSquareQuote, RotateCcw, Loader2, Check
} from 'lucide-react';
import { ViralClip, Project, HookVariant } from '../types';
import { api } from '../api/client';

interface Step1SetupProps {
  clip: ViralClip | null;
  project: Project | null;
  onUpdateClip: (updates: Partial<ViralClip>) => void;
  onUpdateProject?: (updates: Partial<Project>) => void;
}

export const Step1Setup: React.FC<Step1SetupProps> = ({
  clip,
  project,
  onUpdateClip,
  onUpdateProject
}) => {
  const [hookVariants, setHookVariants] = useState<HookVariant[]>([]);
  const [isGeneratingHooks, setIsGeneratingHooks] = useState(false);
  const [hookError, setHookError] = useState<string | null>(null);

  const handleGenerateHooks = async () => {
    if (!clip || !project) return;
    setIsGeneratingHooks(true);
    setHookError(null);
    try {
      const res = await api.generateClipHooks(project.id, clip.id);
      setHookVariants(Array.isArray(res?.variants) ? res.variants : []);
    } catch (err: any) {
      setHookError(err?.message || 'Failed to generate hooks');
    } finally {
      setIsGeneratingHooks(false);
    }
  };
  const [clipOptsOpen, setClipOptsOpen] = useState(true);

  if (!clip) {
    return (
      <div className="p-8 rounded-2xl bg-[var(--bg-card)] border border-[var(--border-subtle)] text-center text-xs text-[var(--text-secondary)]">
        Select a clip from the Viral Clips sidebar to configure setup and framing.
      </div>
    );
  }

  const aspectRatios: { label: ViralClip['aspect_ratio']; desc: string; boxClass: string }[] = [
    { label: '9:16', desc: 'Shorts / TikTok', boxClass: 'w-3.5 h-6' },
    { label: '4:5', desc: 'Portrait', boxClass: 'w-4 h-5' },
    { label: '1:1', desc: 'Square', boxClass: 'w-4.5 h-4.5 w-[18px] h-[18px]' },
    { label: '4:3', desc: 'Standard', boxClass: 'w-5 h-4' },
    { label: '16:9', desc: 'Landscape', boxClass: 'w-6 h-3.5' }
  ];

  const framingTemplates: {
    id: ViralClip['frame_layout'];
    label: string;
    desc: string;
    schematic: 'single' | 'blur' | 'split_v' | 'pip' | 'split_h';
  }[] = [
    { id: 'auto_track', label: 'Auto-Track Speaker', desc: 'Smart face centering', schematic: 'single' },
    { id: 'fit_blur', label: 'Fit + Blurred Backdrop', desc: 'Best for 16:9 & gaming', schematic: 'blur' },
    { id: 'webcam_gameplay_split', label: 'Webcam + Gameplay Split', desc: 'Top cam, bottom action', schematic: 'split_v' },
    { id: 'gameplay_webcam_pip', label: 'Gameplay + Webcam PiP', desc: 'Corner face overlay', schematic: 'pip' },
    { id: 'split_podcast', label: 'Split Podcast', desc: 'Dual speaker stack', schematic: 'split_h' }
  ];

  const clipLengths = ['Auto', '15s', '30s', '45s', '60s', '90s'];
  const clipCounts = [3, 5, 8, 10];
  const languages = ['As spoken', 'English', 'Hinglish', 'Hindi', 'Spanish', 'French', 'German', 'Japanese'];
  const currentLanguage = clip.caption_language || project?.caption_language || 'As spoken';

  const renderSchematic = (kind: string) => {
    switch (kind) {
      case 'blur':
        return (
          <div className="w-6 h-8 rounded border border-current/40 flex items-center justify-center p-0.5 opacity-80">
            <div className="w-full h-3 bg-current/35 rounded-[2px]" />
          </div>
        );
      case 'split_v':
        return (
          <div className="w-6 h-8 rounded border border-current/40 flex flex-col gap-0.5 p-0.5 opacity-80">
            <div className="w-full h-3 bg-current/40 rounded-[2px]" />
            <div className="w-full flex-1 bg-current/25 rounded-[2px]" />
          </div>
        );
      case 'pip':
        return (
          <div className="w-6 h-8 rounded border border-current/40 relative p-0.5 bg-current/15 opacity-80">
            <div className="w-2.5 h-2 bg-current/70 rounded-[1px] absolute top-1 left-1" />
          </div>
        );
      case 'split_h':
        return (
          <div className="w-6 h-8 rounded border border-current/40 flex flex-col gap-0.5 p-0.5 opacity-80">
            <div className="w-full flex-1 bg-current/35 rounded-[2px]" />
            <div className="w-full flex-1 bg-current/35 rounded-[2px]" />
          </div>
        );
      case 'single':
      default:
        return (
          <div className="w-6 h-8 rounded border border-current/40 flex items-center justify-center p-0.5 opacity-80">
            <div className="w-3 h-3 rounded-full bg-current/50" />
          </div>
        );
    }
  };

  return (
    <div className="space-y-4 text-xs animate-fadeIn w-full">
      {/* Selected Clip Quick Options Card */}
      <div className="bg-[var(--bg-card)] border border-[var(--border-subtle)] rounded-xl overflow-hidden">
        <button
          type="button"
          onClick={() => setClipOptsOpen((v) => !v)}
          className="w-full px-4 py-2.5 flex items-center justify-between hover:bg-[var(--bg-input)] transition text-left"
        >
          <div className="flex items-center gap-2">
            <Sparkles className="w-3.5 h-3.5 text-[var(--accent-primary)]" />
            <span className="font-bold text-xs text-[var(--text-primary)]">Selected Clip Options</span>
            <span className="text-[10px] text-[var(--text-secondary)]">Hook banner, cold-open & pacing FX</span>
          </div>
          {clipOptsOpen ? (
            <ChevronUp className="w-4 h-4 text-[var(--text-secondary)]" />
          ) : (
            <ChevronDown className="w-4 h-4 text-[var(--text-secondary)]" />
          )}
        </button>

        {clipOptsOpen && (
          <div className="px-4 pb-3.5 pt-2 border-t border-[var(--border-subtle)] space-y-3">
            {/* Inline Top Hook Banner Headline */}
            <div className="flex flex-col sm:flex-row sm:items-center gap-2.5">
              <label className="flex items-center gap-2 cursor-pointer shrink-0">
                <input
                  type="checkbox"
                  checked={clip.show_hook_banner}
                  onChange={(e) => onUpdateClip({ show_hook_banner: e.target.checked })}
                  className="w-3.5 h-3.5 accent-[var(--accent-primary)] cursor-pointer"
                />
                <span className="font-semibold text-[var(--text-primary)]">Top Hook Banner</span>
              </label>
              <input
                type="text"
                value={clip.hook_headline || ''}
                onChange={(e) => onUpdateClip({ hook_headline: e.target.value })}
                disabled={!clip.show_hook_banner}
                placeholder="Enter top hook headline..."
                className="flex-1 min-w-0 bg-[var(--bg-input)] border border-[var(--border-subtle)] rounded-lg px-3 py-1.5 text-xs text-[var(--text-primary)] focus:border-[var(--accent-primary)] outline-none disabled:opacity-50"
              />
              <button
                type="button"
                onClick={handleGenerateHooks}
                disabled={isGeneratingHooks || !project}
                className="px-2.5 py-1.5 rounded-lg bg-[var(--accent-subtle)] border border-[var(--accent-primary)]/40 text-[var(--accent-primary)] hover:bg-[var(--accent-primary)] hover:text-[var(--accent-foreground)] text-[11px] font-bold flex items-center gap-1.5 transition shrink-0 disabled:opacity-50"
                title="Generate 3 AI viral hook variants (Curiosity, Contrarian, High-Stakes)"
              >
                {isGeneratingHooks ? (
                  <Loader2 className="w-3.5 h-3.5 animate-spin" />
                ) : (
                  <Sparkles className="w-3.5 h-3.5" />
                )}
                <span>{isGeneratingHooks ? 'Generating...' : 'AI Hook Variants'}</span>
              </button>
            </div>

            {hookError && (
              <div className="text-[11px] text-red-400 bg-red-500/10 border border-red-500/25 rounded-lg px-2.5 py-1.5">
                {hookError}
              </div>
            )}

            {hookVariants.length > 0 && (
              <div className="grid grid-cols-1 gap-1.5 pt-1">
                {hookVariants.map((variant, idx) => {
                  const isSelected = (clip.hook_headline || '').trim() === (variant.hook_headline || '').trim();
                  return (
                    <button
                      key={`${variant.variant_type}-${idx}`}
                      type="button"
                      onClick={() =>
                        onUpdateClip({
                          show_hook_banner: true,
                          hook_headline: variant.hook_headline,
                          selected_title: variant.hook_headline,
                        })
                      }
                      className={`text-left p-2.5 rounded-xl border transition flex items-start justify-between gap-2 ${
                        isSelected
                          ? 'bg-[var(--accent-subtle)] border-[var(--accent-primary)] text-[var(--text-primary)]'
                          : 'bg-[var(--bg-input)] border-[var(--border-subtle)] hover:border-[var(--accent-primary)]/50 text-[var(--text-primary)]'
                      }`}
                    >
                      <div className="min-w-0 space-y-0.5">
                        <div className="flex items-center gap-1.5 flex-wrap">
                          <span className="px-1.5 py-0.5 rounded text-[9px] font-black uppercase tracking-wider bg-[var(--bg-card)] border border-[var(--border-subtle)] text-[var(--accent-primary)]">
                            {variant.variant_type}
                          </span>
                          {variant.predicted_retention_lift && (
                            <span className="px-1.5 py-0.5 rounded text-[9px] font-bold bg-emerald-500/15 text-emerald-400 border border-emerald-500/30">
                              {variant.predicted_retention_lift} Lift
                            </span>
                          )}
                          <span className="text-xs font-extrabold truncate">{variant.hook_headline}</span>
                        </div>
                        {variant.rationale && (
                          <p className="text-[11px] text-[var(--text-secondary)] line-clamp-1">
                            {variant.rationale}
                          </p>
                        )}
                      </div>
                      {isSelected && <Check className="w-3.5 h-3.5 text-[var(--accent-primary)] shrink-0 mt-0.5" />}
                    </button>
                  );
                })}
              </div>
            )}

            {/* Retention Quick Toggles Row */}
            <div className="grid grid-cols-1 sm:grid-cols-3 gap-2">
              <button
                type="button"
                onClick={() => onUpdateClip({ use_cold_open: !clip.use_cold_open })}
                className={`flex items-center justify-between px-3 py-2 rounded-lg border transition text-left ${
                  clip.use_cold_open
                    ? 'bg-[var(--accent-subtle)] border-[var(--accent-primary)] text-[var(--text-primary)]'
                    : 'bg-[var(--bg-input)] border-[var(--border-subtle)] text-[var(--text-secondary)] hover:text-[var(--text-primary)]'
                }`}
              >
                <span className="flex items-center gap-1.5 font-medium">
                  <MessageSquareQuote className="w-3.5 h-3.5 text-[var(--accent-primary)]" />
                  Cold-Open Climax
                </span>
                <span className="text-[10px] font-bold uppercase">
                  {clip.use_cold_open ? 'ON' : 'OFF'}
                </span>
              </button>

              <button
                type="button"
                onClick={() => onUpdateClip({ auto_trim_dead_air: !clip.auto_trim_dead_air })}
                className={`flex items-center justify-between px-3 py-2 rounded-lg border transition text-left ${
                  clip.auto_trim_dead_air
                    ? 'bg-[var(--accent-subtle)] border-[var(--accent-primary)] text-[var(--text-primary)]'
                    : 'bg-[var(--bg-input)] border-[var(--border-subtle)] text-[var(--text-secondary)] hover:text-[var(--text-primary)]'
                }`}
              >
                <span className="flex items-center gap-1.5 font-medium">
                  <Scissors className="w-3.5 h-3.5 text-[var(--accent-primary)]" />
                  Trim Dead Air
                </span>
                <span className="text-[10px] font-bold uppercase">
                  {clip.auto_trim_dead_air ? 'ON' : 'OFF'}
                </span>
              </button>

              <button
                type="button"
                onClick={() => onUpdateClip({ dynamic_punch_zoom: !clip.dynamic_punch_zoom })}
                className={`flex items-center justify-between px-3 py-2 rounded-lg border transition text-left ${
                  clip.dynamic_punch_zoom
                    ? 'bg-[var(--accent-subtle)] border-[var(--accent-primary)] text-[var(--text-primary)]'
                    : 'bg-[var(--bg-input)] border-[var(--border-subtle)] text-[var(--text-secondary)] hover:text-[var(--text-primary)]'
                }`}
              >
                <span className="flex items-center gap-1.5 font-medium">
                  <ZoomIn className="w-3.5 h-3.5 text-[var(--accent-primary)]" />
                  Punch Zoom FX
                </span>
                <span className="text-[10px] font-bold uppercase">
                  {clip.dynamic_punch_zoom ? 'ON' : 'OFF'}
                </span>
              </button>
            </div>

            {clip.dynamic_punch_zoom && (
              <div className="flex items-center justify-between gap-2 pt-1.5 border-t border-[var(--border-subtle)]/60">
                <span className="text-[11px] text-[var(--text-secondary)] font-medium">
                  Punch Intensity (Audio-Peak Triggered)
                </span>
                <div className="flex items-center gap-1">
                  {[
                    { label: 'Subtle 1.08x', val: 1.08 },
                    { label: 'Standard 1.14x', val: 1.14 },
                    { label: 'Aggressive 1.22x', val: 1.22 },
                  ].map((opt) => {
                    const active = Math.abs((clip.punch_zoom_intensity ?? 1.14) - opt.val) < 0.02;
                    return (
                      <button
                        key={opt.val}
                        type="button"
                        onClick={() => onUpdateClip({ punch_zoom_intensity: opt.val })}
                        className={`px-2 py-0.5 rounded-md border text-[10px] font-semibold transition ${
                          active
                            ? 'bg-[var(--accent-primary)] text-[var(--accent-foreground)] border-[var(--accent-primary)]'
                            : 'bg-[var(--bg-input)] border-[var(--border-subtle)] text-[var(--text-secondary)] hover:text-[var(--text-primary)]'
                        }`}
                      >
                        {opt.label}
                      </button>
                    );
                  })}
                </div>
              </div>
            )}
          </div>
        )}
      </div>

      {/* Main 2-Column Responsive Inspector Grid */}
      <div className="grid grid-cols-1 lg:grid-cols-2 gap-4">
        {/* Aspect Ratio Visual Cards */}
        <div className="bg-[var(--bg-card)] border border-[var(--border-subtle)] rounded-xl p-4 space-y-3">
          <div className="flex items-center justify-between">
            <span className="font-bold text-xs text-[var(--text-primary)] uppercase tracking-wide">
              Aspect Ratio
            </span>
            <span className="text-[10px] text-[var(--text-secondary)]">Auto-scales preview & export</span>
          </div>

          <div className="grid grid-cols-5 gap-1.5">
            {aspectRatios.map((item) => {
              const active = clip.aspect_ratio === item.label;
              return (
                <button
                  key={item.label}
                  type="button"
                  onClick={() => onUpdateClip({ aspect_ratio: item.label })}
                  className={`flex flex-col items-center justify-between py-2.5 px-1 rounded-xl border transition h-20 ${
                    active
                      ? 'bg-[var(--accent-primary)] text-[var(--accent-foreground)] border-[var(--accent-primary)] font-bold shadow-sm'
                      : 'bg-[var(--bg-input)] border-[var(--border-subtle)] text-[var(--text-secondary)] hover:text-[var(--text-primary)] hover:border-[var(--accent-primary)]/40'
                  }`}
                >
                  <div className="flex-1 flex items-center justify-center">
                    <div
                      className={`${item.boxClass} rounded-[3px] border-2 ${
                        active ? 'border-current bg-current/20' : 'border-current/60'
                      }`}
                    />
                  </div>
                  <div className="text-center leading-tight">
                    <span className="block text-[11px] font-bold tabular-nums">{item.label}</span>
                    <span className={`block text-[9px] truncate ${active ? 'opacity-85' : 'opacity-70'}`}>
                      {item.desc.split(' ')[0]}
                    </span>
                  </div>
                </button>
              );
            })}
          </div>

          {/* Processing Engine */}
          <div className="pt-2 border-t border-[var(--border-subtle)] space-y-2">
            <span className="font-bold text-[11px] text-[var(--text-secondary)] uppercase tracking-wide block">
              Processing Engine
            </span>
            <div className="grid grid-cols-2 gap-2">
              <button
                type="button"
                onClick={() => {
                  onUpdateClip({ hardware_mode: 'gpu' });
                  onUpdateProject?.({ hardware_mode: 'gpu' });
                }}
                className={`flex items-center justify-center gap-1.5 py-2 px-2.5 rounded-lg border font-medium transition ${
                  clip.hardware_mode === 'gpu'
                    ? 'bg-[var(--accent-subtle)] border-[var(--accent-primary)] text-[var(--accent-primary)]'
                    : 'bg-[var(--bg-input)] border-[var(--border-subtle)] text-[var(--text-secondary)] hover:text-[var(--text-primary)]'
                }`}
              >
                <Zap className="w-3.5 h-3.5" />
                <span>GPU (NVENC)</span>
              </button>
              <button
                type="button"
                onClick={() => {
                  onUpdateClip({ hardware_mode: 'cpu' });
                  onUpdateProject?.({ hardware_mode: 'cpu' });
                }}
                className={`flex items-center justify-center gap-1.5 py-2 px-2.5 rounded-lg border font-medium transition ${
                  clip.hardware_mode === 'cpu'
                    ? 'bg-[var(--accent-subtle)] border-[var(--accent-primary)] text-[var(--accent-primary)]'
                    : 'bg-[var(--bg-input)] border-[var(--border-subtle)] text-[var(--text-secondary)] hover:text-[var(--text-primary)]'
                }`}
              >
                <Cpu className="w-3.5 h-3.5" />
                <span>CPU (libx264)</span>
              </button>
            </div>
          </div>
        </div>

        {/* Framing Templates with Schematics + Zoom & Pan */}
        <div className="bg-[var(--bg-card)] border border-[var(--border-subtle)] rounded-xl p-4 space-y-3">
          <div className="flex items-center justify-between">
            <span className="font-bold text-xs text-[var(--text-primary)] uppercase tracking-wide">
              Framing Template
            </span>
            <button
              type="button"
              onClick={() => onUpdateClip({ zoom: 1.0, pan_x_offset: 0.0 })}
              className="text-[10px] text-[var(--text-secondary)] hover:text-[var(--accent-primary)] flex items-center gap-1 transition"
            >
              <RotateCcw className="w-3 h-3" /> Reset Zoom/Pan
            </button>
          </div>

          <div className="grid grid-cols-1 sm:grid-cols-2 gap-1.5">
            {framingTemplates.map((t) => {
              const active =
                clip.frame_layout === t.id ||
                (t.id === 'gameplay_webcam_pip' && clip.frame_layout === 'webcam_gameplay_pip');
              return (
                <button
                  key={t.id}
                  type="button"
                  onClick={() => onUpdateClip({ frame_layout: t.id })}
                  className={`flex items-center gap-2.5 p-2 rounded-xl border text-left transition ${
                    active
                      ? 'bg-[var(--accent-subtle)] border-[var(--accent-primary)] text-[var(--text-primary)]'
                      : 'bg-[var(--bg-input)] border-[var(--border-subtle)] text-[var(--text-secondary)] hover:text-[var(--text-primary)] hover:border-[var(--accent-primary)]/40'
                  }`}
                >
                  <div className={active ? 'text-[var(--accent-primary)]' : 'text-[var(--text-secondary)]'}>
                    {renderSchematic(t.schematic)}
                  </div>
                  <div className="min-w-0">
                    <span className="block text-[11px] font-bold truncate text-[var(--text-primary)]">
                      {t.label}
                    </span>
                    <span className="block text-[9px] truncate opacity-80">{t.desc}</span>
                  </div>
                </button>
              );
            })}
          </div>

          {/* Webcam Corner Selector for Split / PiP templates */}
          {(clip.frame_layout === 'webcam_gameplay_split' ||
            clip.frame_layout === 'gameplay_webcam_pip' ||
            clip.frame_layout === 'webcam_gameplay_pip') && (
            <div className="pt-2 border-t border-[var(--border-subtle)] space-y-1.5">
              <div className="flex items-center justify-between text-[11px] text-[var(--text-secondary)]">
                <span>Webcam Corner</span>
                <span className="font-mono text-[10px] text-[var(--accent-primary)]">
                  {clip.webcam_corner || 'top_left'}
                </span>
              </div>
              <div className="grid grid-cols-4 gap-1.5">
                {(
                  [
                    { id: 'top_left', label: 'Top Left' },
                    { id: 'top_right', label: 'Top Right' },
                    { id: 'bottom_left', label: 'Bottom Left' },
                    { id: 'bottom_right', label: 'Bottom Right' }
                  ] as const
                ).map((corner) => {
                  const selected = (clip.webcam_corner || 'top_left') === corner.id;
                  return (
                    <button
                      key={corner.id}
                      type="button"
                      onClick={() => onUpdateClip({ webcam_corner: corner.id })}
                      className={`py-1.5 px-2 rounded-lg text-[10px] font-semibold border transition ${
                        selected
                          ? 'bg-[var(--accent-subtle)] border-[var(--accent-primary)] text-[var(--accent-primary)]'
                          : 'bg-[var(--bg-input)] border-[var(--border-subtle)] text-[var(--text-secondary)] hover:text-[var(--text-primary)]'
                      }`}
                    >
                      {corner.label}
                    </button>
                  );
                })}
              </div>
            </div>
          )}

          {/* Zoom & Pan Sliders */}
          <div className="grid grid-cols-2 gap-3 pt-2 border-t border-[var(--border-subtle)] tabular-nums">
            <div>
              <div className="flex justify-between text-[11px] text-[var(--text-secondary)] mb-1">
                <span>Zoom</span>
                <span className="font-mono text-[var(--accent-primary)] font-bold">
                  {(clip.zoom || 1.0).toFixed(2)}x
                </span>
              </div>
              <input
                type="range"
                min={1.0}
                max={2.5}
                step={0.05}
                value={clip.zoom || 1.0}
                onChange={(e) => onUpdateClip({ zoom: parseFloat(e.target.value) })}
                className="w-full h-1.5 bg-[var(--bg-input)] rounded appearance-none cursor-pointer"
              />
            </div>
            <div>
              <div className="flex justify-between text-[11px] text-[var(--text-secondary)] mb-1">
                <span>Horizontal Pan</span>
                <span className="font-mono text-[var(--accent-primary)] font-bold">
                  {Math.round((clip.pan_x_offset || 0) * 100)}%
                </span>
              </div>
              <input
                type="range"
                min={-100}
                max={100}
                step={2}
                value={Math.round((clip.pan_x_offset || 0) * 100)}
                onChange={(e) => onUpdateClip({ pan_x_offset: parseInt(e.target.value, 10) / 100.0 })}
                className="w-full h-1.5 bg-[var(--bg-input)] rounded appearance-none cursor-pointer"
              />
            </div>
          </div>
        </div>

        {/* Clip Length & Clip Count */}
        <div className="bg-[var(--bg-card)] border border-[var(--border-subtle)] rounded-xl p-4 space-y-3">
          <div>
            <div className="flex items-center justify-between mb-1.5">
              <span className="font-bold text-xs text-[var(--text-primary)] uppercase tracking-wide">
                Clip Length Target
              </span>
              <span className="text-[10px] text-[var(--text-secondary)]">Duration bound</span>
            </div>
            <div className="grid grid-cols-6 gap-1.5 tabular-nums">
              {clipLengths.map((len) => {
                const isSelected =
                  project?.duration_preset === len || (len === 'Auto' && !project?.duration_preset);
                return (
                  <button
                    key={len}
                    type="button"
                    onClick={() => {
                      onUpdateProject?.({ duration_preset: len });
                      if (len !== 'Auto') {
                        const targetDur = parseInt(len, 10);
                        if (Number.isFinite(targetDur) && targetDur >= 2) {
                          const projMax =
                            project?.duration && project.duration > clip.start_time + 2
                              ? project.duration
                              : undefined;
                          const nextEnd = projMax
                            ? Math.min(projMax, clip.start_time + targetDur)
                            : clip.start_time + targetDur;
                          onUpdateClip({
                            end_time: Number(nextEnd.toFixed(2)),
                            duration: Number((nextEnd - clip.start_time).toFixed(2))
                          });
                        }
                      }
                    }}
                    className={`py-1.5 rounded-lg border text-center font-medium transition ${
                      isSelected
                        ? 'bg-[var(--accent-primary)] text-[var(--accent-foreground)] font-bold border-[var(--accent-primary)]'
                        : 'bg-[var(--bg-input)] border-[var(--border-subtle)] text-[var(--text-secondary)] hover:text-[var(--text-primary)]'
                    }`}
                  >
                    {len}
                  </button>
                );
              })}
            </div>
          </div>

          <div className="pt-2 border-t border-[var(--border-subtle)]">
            <div className="flex items-center justify-between mb-1.5">
              <span className="font-bold text-xs text-[var(--text-primary)] uppercase tracking-wide">
                Number of Clips
              </span>
              <span className="text-[10px] text-[var(--text-secondary)]">Generation ceiling</span>
            </div>
            <div className="grid grid-cols-4 gap-1.5 tabular-nums">
              {clipCounts.map((countNum) => {
                const isSelected =
                  project?.clip_count === countNum || (!project?.clip_count && countNum === 5);
                return (
                  <button
                    key={countNum}
                    type="button"
                    onClick={() => onUpdateProject?.({ clip_count: countNum })}
                    className={`py-1.5 rounded-lg border text-center font-medium transition ${
                      isSelected
                        ? 'bg-[var(--accent-primary)] text-[var(--accent-foreground)] font-bold border-[var(--accent-primary)]'
                        : 'bg-[var(--bg-input)] border-[var(--border-subtle)] text-[var(--text-secondary)] hover:text-[var(--text-primary)]'
                    }`}
                  >
                    {countNum} clips
                  </button>
                );
              })}
            </div>
          </div>
        </div>

        {/* Spoken / Caption Language */}
        <div className="bg-[var(--bg-card)] border border-[var(--border-subtle)] rounded-xl p-4 space-y-2.5">
          <div className="flex items-center justify-between">
            <span className="font-bold text-xs text-[var(--text-primary)] uppercase tracking-wide">
              Caption Language
            </span>
            <span className="text-[10px] text-[var(--text-secondary)]">Whisper recognition target</span>
          </div>
          <div className="grid grid-cols-4 gap-1.5">
            {languages.map((lang) => {
              const isSelected = currentLanguage === lang;
              return (
                <button
                  key={lang}
                  type="button"
                  onClick={() => {
                    onUpdateClip({ caption_language: lang });
                    onUpdateProject?.({ caption_language: lang });
                  }}
                  className={`py-2 px-2 rounded-lg border text-center font-medium transition truncate ${
                    isSelected
                      ? 'bg-[var(--accent-primary)] text-[var(--accent-foreground)] font-bold border-[var(--accent-primary)]'
                      : 'bg-[var(--bg-input)] border-[var(--border-subtle)] text-[var(--text-secondary)] hover:text-[var(--text-primary)]'
                  }`}
                >
                  {lang}
                </button>
              );
            })}
          </div>
        </div>
      </div>
    </div>
  );
};
