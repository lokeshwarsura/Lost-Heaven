import React, { useEffect, useState } from 'react';
import {
  Cpu, Zap, ChevronDown, ChevronUp, Sparkles, Scissors, ZoomIn, MessageSquareQuote, RotateCcw, Loader2, Check, Layers, Camera, RefreshCw, AlertTriangle, CheckCircle2, Lock
} from 'lucide-react';
import { ViralClip, Project, HookVariant, SourceWebcamRegion, FramingMapResponse, BestFrameItem } from '../types';
import { api } from '../api/client';

interface Step1SetupProps {
  clip: ViralClip | null;
  project: Project | null;
  onUpdateClip: (updates: Partial<ViralClip>) => void;
  onUpdateProject?: (updates: Partial<Project>) => void;
  onOpenAnalysisWorkstation?: () => void;
  onSeekToMasterTime?: (t: number) => void;
  onCalibrateFrame?: (frame: BestFrameItem) => void;
  onOpenThumbnailFrame?: (frame: BestFrameItem) => void;
}

export const Step1Setup: React.FC<Step1SetupProps> = ({
  clip,
  project,
  onUpdateClip,
  onUpdateProject,
  onOpenAnalysisWorkstation,
  onSeekToMasterTime,
  onCalibrateFrame,
  onOpenThumbnailFrame
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
  const [targetsCardOpen, setTargetsCardOpen] = useState(true);
  const [langCardOpen, setLangCardOpen] = useState(true);
  const [webcamRegion, setWebcamRegion] = useState<SourceWebcamRegion | null>(
    clip?.source_webcam_region ?? null
  );
  const [isDetectingWebcam, setIsDetectingWebcam] = useState(false);
  const [framingMap, setFramingMap] = useState<FramingMapResponse | null>(null);
  const [bestFrames, setBestFrames] = useState<BestFrameItem[]>(clip?.best_frames || []);
  const [isLoadingBestFrames, setIsLoadingBestFrames] = useState(false);
  const [timelineFxSummary, setTimelineFxSummary] = useState<{
    cold_open_label?: string;
    dead_air_label?: string;
    punch_zoom_label?: string;
  } | null>(null);

  useEffect(() => {
    const onSummary = (ev: Event) => {
      const detail = (ev as CustomEvent)?.detail;
      if (detail && detail.clip_id === clip?.id && detail.summary) {
        setTimelineFxSummary(detail.summary);
      }
    };
    window.addEventListener('lh-timeline-fx-summary', onSummary);
    return () => window.removeEventListener('lh-timeline-fx-summary', onSummary);
  }, [clip?.id]);

  const isMultiRegionTemplate =
    clip?.frame_layout === 'webcam_gameplay_split' ||
    clip?.frame_layout === 'gameplay_webcam_pip' ||
    clip?.frame_layout === 'webcam_gameplay_pip' ||
    clip?.frame_layout === 'split_podcast' ||
    clip?.frame_layout === 'triple_gaming_hud' ||
    clip?.frame_layout === 'portrait_pip_badge' ||
    clip?.frame_layout === 'side_by_side_50_50';

  const isWebcamTemplate = isMultiRegionTemplate;

  useEffect(() => {
    setWebcamRegion(clip?.source_webcam_region ?? null);
  }, [clip?.id, clip?.source_webcam_region]);

  useEffect(() => {
    if (!clip || !project) {
      setBestFrames([]);
      return;
    }
    if (Array.isArray(clip.best_frames) && clip.best_frames.length > 0) {
      setBestFrames(clip.best_frames);
      return;
    }
    let cancelled = false;
    setIsLoadingBestFrames(true);
    api
      .getBestFrames(project.id, clip.id)
      .then((res) => {
        if (!cancelled && Array.isArray(res?.frames)) {
          setBestFrames(res.frames);
        }
      })
      .catch(() => {})
      .finally(() => {
        if (!cancelled) setIsLoadingBestFrames(false);
      });
    return () => {
      cancelled = true;
    };
  }, [clip?.id, project?.id]);

  useEffect(() => {
    if (!clip || !project) {
      setFramingMap(null);
      return;
    }
    let cancelled = false;
    api
      .getFramingMap(project.id, clip.id, false)
      .then((res) => {
        if (!cancelled) setFramingMap(res);
      })
      .catch(() => {});
    return () => {
      cancelled = true;
    };
  }, [clip?.id, project?.id]);

  useEffect(() => {
    if (!clip || !project || !isWebcamTemplate) return;
    if (clip.source_webcam_region) {
      setWebcamRegion(clip.source_webcam_region);
      return;
    }
    let cancelled = false;
    setIsDetectingWebcam(true);
    api
      .getWebcamRegion(project.id, clip.id, false)
      .then((region) => {
        if (cancelled) return;
        setWebcamRegion(region);
        onUpdateClip({ source_webcam_region: region });
      })
      .catch(() => {})
      .finally(() => {
        if (!cancelled) setIsDetectingWebcam(false);
      });
    return () => {
      cancelled = true;
    };
  }, [clip?.id, project?.id, isWebcamTemplate]);

  const handleSetSourceWebcamZone = async (zoneId: string, refresh = false) => {
    if (!clip || !project) return;
    setIsDetectingWebcam(true);
    try {
      const region = await api.updateWebcamRegion(project.id, clip.id, {
        source_zone: zoneId,
        refresh
      });
      setWebcamRegion(region);
      onUpdateClip({ source_webcam_region: region });
    } catch (err) {
      console.error('Failed to update source webcam zone:', err);
    } finally {
      setIsDetectingWebcam(false);
    }
  };

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
    schematic:
      | 'single'
      | 'blur'
      | 'split_v'
      | 'pip'
      | 'split_h'
      | 'cinema_letterbox'
      | 'triple_hud'
      | 'portrait_pip'
      | 'punch_crop'
      | 'side_50_50';
  }[] = [
    { id: 'auto_track', label: 'Auto-Track Speaker', desc: 'Smart face centering', schematic: 'single' },
    { id: 'fit_blur', label: 'Fit + Blurred Backdrop', desc: 'Full 16:9 over blur', schematic: 'blur' },
    { id: 'webcam_gameplay_split', label: 'Webcam + Gameplay Split', desc: 'Top cam, bottom action', schematic: 'split_v' },
    { id: 'gameplay_webcam_pip', label: 'Gameplay + Webcam PiP', desc: 'Corner face overlay', schematic: 'pip' },
    { id: 'split_podcast', label: 'Split Podcast', desc: 'Dual speaker stack', schematic: 'split_h' },
    { id: 'cinema_letterbox_blur', label: 'Cinema 2.39:1 + Blur', desc: 'Letterbox + ambient fill', schematic: 'cinema_letterbox' },
    { id: 'triple_gaming_hud', label: 'Triple Stack: Cam + HUD', desc: 'Cam + action + mini-map', schematic: 'triple_hud' },
    { id: 'portrait_pip_badge', label: 'Portrait Facecam Card', desc: 'Full video + 3:4 cam badge', schematic: 'portrait_pip' },
    { id: 'dynamic_punch_crop', label: 'Action Punch Crop (1.28x)', desc: 'Tight focus mobile fill', schematic: 'punch_crop' },
    { id: 'side_by_side_50_50', label: 'Side-by-Side 50/50 Split', desc: 'Dual vertical split screen', schematic: 'side_50_50' },
  ];

  const clipLengths = ['Auto', '15s', '30s', '45s', '60s', '90s'];
  const clipCounts = [3, 5, 8, 10];
  const languages = ['As spoken', 'English', 'Hinglish', 'Hindi', 'Spanish', 'French', 'German', 'Japanese'];
  const currentLanguage = clip.caption_language || project?.caption_language || 'As spoken';

  const renderSchematic = (kind: string) => {
    const frameShell = 'w-8 h-10 shrink-0';
    switch (kind) {
      case 'blur':
        return (
          <svg viewBox="0 0 32 40" className={frameShell} fill="none" aria-hidden="true">
            <rect x="1.5" y="1.5" width="29" height="37" rx="4" stroke="currentColor" strokeOpacity="0.45" strokeWidth="1.4" />
            <line x1="6" y1="7" x2="26" y2="7" stroke="currentColor" strokeOpacity="0.25" strokeWidth="1.2" strokeLinecap="round" />
            <line x1="8" y1="33" x2="24" y2="33" stroke="currentColor" strokeOpacity="0.25" strokeWidth="1.2" strokeLinecap="round" />
            <rect x="3" y="13" width="26" height="14" rx="2" fill="currentColor" fillOpacity="0.28" stroke="currentColor" strokeOpacity="0.7" strokeWidth="1.2" />
          </svg>
        );
      case 'split_v':
        return (
          <svg viewBox="0 0 32 40" className={frameShell} fill="none" aria-hidden="true">
            <rect x="1.5" y="1.5" width="29" height="37" rx="4" stroke="currentColor" strokeOpacity="0.45" strokeWidth="1.4" />
            <rect x="3" y="3" width="26" height="14" rx="2" fill="currentColor" fillOpacity="0.32" />
            <circle cx="16" cy="9.5" r="3" fill="currentColor" fillOpacity="0.85" />
            <line x1="2" y1="18.5" x2="30" y2="18.5" stroke="currentColor" strokeWidth="1.5" strokeOpacity="0.85" />
            <rect x="3" y="20.5" width="26" height="16.5" rx="2" fill="currentColor" fillOpacity="0.16" />
          </svg>
        );
      case 'pip':
        return (
          <svg viewBox="0 0 32 40" className={frameShell} fill="none" aria-hidden="true">
            <rect x="1.5" y="1.5" width="29" height="37" rx="4" fill="currentColor" fillOpacity="0.12" stroke="currentColor" strokeOpacity="0.45" strokeWidth="1.4" />
            <rect x="15" y="5" width="12.5" height="9.5" rx="2" fill="currentColor" fillOpacity="0.42" stroke="currentColor" strokeWidth="1.3" />
            <circle cx="21.25" cy="9.2" r="2" fill="currentColor" fillOpacity="0.9" />
          </svg>
        );
      case 'split_h':
        return (
          <svg viewBox="0 0 32 40" className={frameShell} fill="none" aria-hidden="true">
            <rect x="1.5" y="1.5" width="29" height="37" rx="4" stroke="currentColor" strokeOpacity="0.45" strokeWidth="1.4" />
            <rect x="3" y="3" width="26" height="16" rx="2" fill="currentColor" fillOpacity="0.28" />
            <circle cx="16" cy="10.5" r="3.2" fill="currentColor" fillOpacity="0.8" />
            <line x1="2" y1="20" x2="30" y2="20" stroke="currentColor" strokeWidth="1.4" strokeOpacity="0.85" />
            <rect x="3" y="21.5" width="26" height="15.5" rx="2" fill="currentColor" fillOpacity="0.22" />
            <circle cx="16" cy="29" r="3.2" fill="currentColor" fillOpacity="0.8" />
          </svg>
        );
      case 'cinema_letterbox':
        return (
          <svg viewBox="0 0 32 40" className={frameShell} fill="none" aria-hidden="true">
            <rect x="1.5" y="1.5" width="29" height="37" rx="4" stroke="currentColor" strokeOpacity="0.45" strokeWidth="1.4" />
            <line x1="7" y1="6.5" x2="25" y2="6.5" stroke="currentColor" strokeOpacity="0.25" strokeWidth="1.2" strokeLinecap="round" />
            <line x1="7" y1="33.5" x2="25" y2="33.5" stroke="currentColor" strokeOpacity="0.25" strokeWidth="1.2" strokeLinecap="round" />
            <rect x="2.5" y="12.5" width="27" height="2.2" fill="currentColor" fillOpacity="0.85" />
            <rect x="2.5" y="15" width="27" height="10" fill="currentColor" fillOpacity="0.30" />
            <rect x="2.5" y="25.3" width="27" height="2.2" fill="currentColor" fillOpacity="0.85" />
          </svg>
        );
      case 'triple_hud':
        return (
          <svg viewBox="0 0 32 40" className={frameShell} fill="none" aria-hidden="true">
            <rect x="1.5" y="1.5" width="29" height="37" rx="4" stroke="currentColor" strokeOpacity="0.45" strokeWidth="1.4" />
            <rect x="3" y="3" width="26" height="10.5" rx="1.5" fill="currentColor" fillOpacity="0.34" />
            <circle cx="16" cy="8.2" r="2.5" fill="currentColor" fillOpacity="0.85" />
            <rect x="3" y="15" width="26" height="14.5" rx="1.5" fill="currentColor" fillOpacity="0.18" />
            <rect x="3" y="31" width="26" height="6" rx="1.5" fill="currentColor" fillOpacity="0.42" />
            <circle cx="8" cy="34" r="1.4" fill="currentColor" fillOpacity="0.9" />
          </svg>
        );
      case 'portrait_pip':
        return (
          <svg viewBox="0 0 32 40" className={frameShell} fill="none" aria-hidden="true">
            <rect x="1.5" y="1.5" width="29" height="37" rx="4" fill="currentColor" fillOpacity="0.12" stroke="currentColor" strokeOpacity="0.45" strokeWidth="1.4" />
            <rect x="17" y="5" width="10.5" height="14" rx="2.5" fill="currentColor" fillOpacity="0.42" stroke="currentColor" strokeWidth="1.3" />
            <circle cx="22.25" cy="10.2" r="2.2" fill="currentColor" fillOpacity="0.92" />
            <path d="M18.8 16.5c0-1.9 1.5-3 3.45-3s3.45 1.1 3.45 3" stroke="currentColor" strokeWidth="1.2" strokeLinecap="round" />
          </svg>
        );
      case 'punch_crop':
        return (
          <svg viewBox="0 0 32 40" className={frameShell} fill="none" aria-hidden="true">
            <rect x="1.5" y="1.5" width="29" height="37" rx="4" fill="currentColor" fillOpacity="0.14" stroke="currentColor" strokeOpacity="0.45" strokeWidth="1.4" />
            <path d="M6 12V7h5M26 12V7h-5M6 28v5h5M26 28v5h-5" stroke="currentColor" strokeWidth="1.6" strokeLinecap="round" strokeLinejoin="round" />
            <circle cx="16" cy="20" r="4.5" fill="currentColor" fillOpacity="0.45" stroke="currentColor" strokeWidth="1.2" />
          </svg>
        );
      case 'side_50_50':
        return (
          <svg viewBox="0 0 32 40" className={frameShell} fill="none" aria-hidden="true">
            <rect x="1.5" y="1.5" width="29" height="37" rx="4" stroke="currentColor" strokeOpacity="0.45" strokeWidth="1.4" />
            <rect x="3" y="3" width="12" height="34" rx="2" fill="currentColor" fillOpacity="0.28" />
            <circle cx="9" cy="17" r="2.7" fill="currentColor" fillOpacity="0.85" />
            <line x1="16" y1="2.5" x2="16" y2="37.5" stroke="currentColor" strokeWidth="1.5" strokeOpacity="0.9" />
            <rect x="17" y="3" width="12" height="34" rx="2" fill="currentColor" fillOpacity="0.22" />
            <circle cx="23" cy="17" r="2.7" fill="currentColor" fillOpacity="0.85" />
          </svg>
        );
      case 'single':
      default:
        return (
          <svg viewBox="0 0 32 40" className={frameShell} fill="none" aria-hidden="true">
            <rect x="1.5" y="1.5" width="29" height="37" rx="4" fill="currentColor" fillOpacity="0.12" stroke="currentColor" strokeOpacity="0.45" strokeWidth="1.4" />
            <circle cx="16" cy="14" r="4.5" fill="currentColor" fillOpacity="0.65" />
            <path d="M9.5 29c0-3.8 2.9-6.2 6.5-6.2s6.5 2.4 6.5 6.2" fill="currentColor" fillOpacity="0.35" />
            <circle cx="16" cy="14" r="7.5" stroke="currentColor" strokeOpacity="0.35" strokeWidth="1" strokeDasharray="2 2" />
          </svg>
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
                onClick={() => {
                  const next = !(clip.cold_open ?? clip.use_cold_open);
                  onUpdateClip({ use_cold_open: next, cold_open: next } as any);
                }}
                className={`flex flex-col gap-0.5 px-3 py-2 rounded-lg border transition text-left ${
                  (clip.cold_open ?? clip.use_cold_open)
                    ? 'bg-[var(--accent-subtle)] border-[var(--accent-primary)] text-[var(--text-primary)]'
                    : 'bg-[var(--bg-input)] border-[var(--border-subtle)] text-[var(--text-secondary)] hover:text-[var(--text-primary)]'
                }`}
              >
                <div className="flex items-center justify-between gap-2 w-full">
                  <span className="flex items-center gap-1.5 font-medium">
                    <MessageSquareQuote className="w-3.5 h-3.5 text-[var(--accent-primary)]" />
                    Cold-Open Climax
                  </span>
                  <span className="text-[10px] font-bold uppercase">
                    {(clip.cold_open ?? clip.use_cold_open) ? 'ON' : 'OFF'}
                  </span>
                </div>
                {(clip.cold_open ?? clip.use_cold_open) && (
                  <span className="text-[9px] font-mono text-[var(--text-muted)] truncate">
                    {timelineFxSummary?.cold_open_label || '2.8s climax hook + 0.2s flash'}
                  </span>
                )}
              </button>

              <button
                type="button"
                onClick={() => {
                  const next = !(clip.trim_dead_air ?? clip.auto_trim_dead_air);
                  onUpdateClip({ auto_trim_dead_air: next, trim_dead_air: next } as any);
                }}
                className={`flex flex-col gap-0.5 px-3 py-2 rounded-lg border transition text-left ${
                  (clip.trim_dead_air ?? clip.auto_trim_dead_air)
                    ? 'bg-[var(--accent-subtle)] border-[var(--accent-primary)] text-[var(--text-primary)]'
                    : 'bg-[var(--bg-input)] border-[var(--border-subtle)] text-[var(--text-secondary)] hover:text-[var(--text-primary)]'
                }`}
              >
                <div className="flex items-center justify-between gap-2 w-full">
                  <span className="flex items-center gap-1.5 font-medium">
                    <Scissors className="w-3.5 h-3.5 text-[var(--accent-primary)]" />
                    Trim Dead Air
                  </span>
                  <span className="text-[10px] font-bold uppercase">
                    {(clip.trim_dead_air ?? clip.auto_trim_dead_air) ? 'ON' : 'OFF'}
                  </span>
                </div>
                {(clip.trim_dead_air ?? clip.auto_trim_dead_air) && (
                  <span className="text-[9px] font-mono text-[var(--text-muted)] truncate">
                    {timelineFxSummary?.dead_air_label || 'Cutting pauses >= 0.55s'}
                  </span>
                )}
              </button>

              <button
                type="button"
                onClick={() => {
                  const next = !(clip.punch_zoom ?? clip.dynamic_punch_zoom);
                  onUpdateClip({
                    dynamic_punch_zoom: next,
                    punch_zoom: next,
                    ...(next && !clip.punch_zoom_intensity ? { punch_zoom_intensity: 1.16 } : {}),
                  } as any);
                }}
                className={`flex flex-col gap-0.5 px-3 py-2 rounded-lg border transition text-left ${
                  (clip.punch_zoom ?? clip.dynamic_punch_zoom)
                    ? 'bg-[var(--accent-subtle)] border-[var(--accent-primary)] text-[var(--text-primary)]'
                    : 'bg-[var(--bg-input)] border-[var(--border-subtle)] text-[var(--text-secondary)] hover:text-[var(--text-primary)]'
                }`}
              >
                <div className="flex items-center justify-between gap-2 w-full">
                  <span className="flex items-center gap-1.5 font-medium">
                    <ZoomIn className="w-3.5 h-3.5 text-[var(--accent-primary)]" />
                    Punch Zoom FX
                  </span>
                  <span className="text-[10px] font-bold uppercase">
                    {(clip.punch_zoom ?? clip.dynamic_punch_zoom) ? 'ON' : 'OFF'}
                  </span>
                </div>
                {(clip.punch_zoom ?? clip.dynamic_punch_zoom) && (
                  <span className="text-[9px] font-mono text-[var(--text-muted)] truncate">
                    {timelineFxSummary?.punch_zoom_label || 'Peak zoom FX (1.16x)'}
                  </span>
                )}
              </button>
            </div>

            {(clip.punch_zoom ?? clip.dynamic_punch_zoom) && (
              <div className="flex items-center justify-between gap-2 pt-1.5 border-t border-[var(--border-subtle)]/60">
                <span className="text-[11px] text-[var(--text-secondary)] font-medium">
                  Punch Intensity (Audio-Peak Triggered)
                </span>
                <div className="flex items-center gap-1">
                  {[
                    { label: 'Subtle 1.08x', val: 1.08 },
                    { label: 'Standard 1.16x', val: 1.16 },
                    { label: 'Aggressive 1.24x', val: 1.24 },
                  ].map((opt) => {
                    const active = Math.abs((clip.punch_zoom_intensity ?? 1.16) - opt.val) < 0.025;
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
      <div className="grid grid-cols-1 lg:grid-cols-2 gap-4 items-start">
        {/* Left Stack: Aspect Ratio + Engine, Clip Targets, Caption Language */}
        <div className="space-y-4">
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

        {/* Clip Length & Clip Count */}
        <div className="bg-[var(--bg-card)] border border-[var(--border-subtle)] rounded-xl p-4 space-y-3">
          <button
            type="button"
            onClick={() => setTargetsCardOpen((v) => !v)}
            className="w-full flex items-center justify-between text-left"
          >
            <span className="font-bold text-xs text-[var(--text-primary)] uppercase tracking-wide">
              Clip Length &amp; Targets
            </span>
            <span className="flex items-center gap-1.5 text-[10px] text-[var(--text-secondary)]">
              <span>{project?.duration_preset || 'Auto'} • {project?.clip_count || 5} clips</span>
              {targetsCardOpen ? <ChevronUp className="w-3.5 h-3.5" /> : <ChevronDown className="w-3.5 h-3.5" />}
            </span>
          </button>

          {targetsCardOpen && (
          <div className="space-y-3">
          <div>
            <div className="flex items-center justify-between mb-1.5">
              <span className="text-[11px] font-semibold text-[var(--text-secondary)]">
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
          )}
        </div>

        {/* Spoken / Caption Language */}
        <div className="bg-[var(--bg-card)] border border-[var(--border-subtle)] rounded-xl p-4 space-y-2.5">
          <button
            type="button"
            onClick={() => setLangCardOpen((v) => !v)}
            className="w-full flex items-center justify-between text-left"
          >
            <span className="font-bold text-xs text-[var(--text-primary)] uppercase tracking-wide">
              Caption Language
            </span>
            <span className="flex items-center gap-1.5 text-[10px] text-[var(--text-secondary)]">
              <span>{currentLanguage}</span>
              {langCardOpen ? <ChevronUp className="w-3.5 h-3.5" /> : <ChevronDown className="w-3.5 h-3.5" />}
            </span>
          </button>
          {langCardOpen && (
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
          )}
        </div>
        </div>

        {/* Right Stack: Framing Templates with Schematics + Zoom & Pan + Virtual Backdrop */}
        <div className="space-y-4">
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
                  onClick={() =>
                    onUpdateClip({
                      frame_layout: t.id,
                      framing_template: t.id,
                      framing_user_locked: true,
                    } as any)
                  }
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

          {/* Active Framing Telemetry Pill (always reflects selected user layout, never contradicts with AI: fit_blur) */}
          {(() => {
            const ov = clip.framing_override;
            const zoneRaw = webcamRegion?.locked_zone && webcamRegion.locked_zone !== 'auto'
              ? webcamRegion.locked_zone
              : webcamRegion?.source_zone && webcamRegion.source_zone !== 'none'
                ? webcamRegion.source_zone
                : 'auto';
            const zoneNice = zoneRaw.replace(/_/g, ' ').replace(/\b\w/g, (c) => c.toUpperCase());
            const activeTplObj = framingTemplates.find(
              (ft) => ft.id === clip.frame_layout || (ft.id === 'gameplay_webcam_pip' && clip.frame_layout === 'webcam_gameplay_pip')
            );
            const activeTplName = activeTplObj?.label || clip.frame_layout || 'Auto-Track Speaker';

            if (ov?.lock_camera && ov.focus_anchor) {
              return (
                <div className="flex items-center justify-between gap-2 px-2.5 py-1.5 rounded-lg bg-amber-500/12 border border-amber-500/35 text-[10px] text-amber-300 font-semibold">
                  <span className="inline-flex items-center gap-1.5">
                    <Lock className="w-3 h-3" />
                    Active Layout: {activeTplName} · Focus ({Math.round(ov.focus_anchor.x * 100)}%, {Math.round(ov.focus_anchor.y * 100)}%)
                  </span>
                  {project && (
                    <button
                      type="button"
                      onClick={() => {
                        void api.updateFramingOverride(project.id, clip.id, { reset: true }).then((r) => {
                          onUpdateClip({ framing_override: r.framing_override, source_webcam_region: r.source_webcam_region });
                        });
                      }}
                      className="underline hover:text-white"
                    >
                      Unlock
                    </button>
                  )}
                </div>
              );
            }
            return (
              <div className="flex items-center justify-between gap-2 px-2.5 py-1.5 rounded-lg bg-[var(--bg-input)] border border-[var(--border-subtle)] text-[10px] text-[var(--text-secondary)] font-semibold">
                <span className="inline-flex items-center gap-1.5 truncate">
                  <CheckCircle2 className="w-3 h-3 text-[var(--accent-primary)] shrink-0" />
                  <span className="truncate">
                    Active Layout: <span className="text-[var(--text-primary)]">{activeTplName}</span>
                    {isMultiRegionTemplate ? ` · Source ROI: ${zoneNice}` : ''}
                  </span>
                </span>
                {webcamRegion?.webcam_detected ? (
                  <span className="font-mono text-emerald-400 shrink-0">Cam Locked</span>
                ) : (
                  <span className="font-mono text-sky-300 shrink-0">Action ROI</span>
                )}
              </div>
            );
          })()}

          {/* Compact Best Frames Strip */}
          {(bestFrames.length > 0 || isLoadingBestFrames) && (
            <div className="pt-2 border-t border-[var(--border-subtle)] space-y-1.5">
              <div className="flex items-center justify-between gap-2">
                <span className="text-[11px] font-semibold text-[var(--text-primary)] flex items-center gap-1.5">
                  <Sparkles className="w-3.5 h-3.5 text-[var(--accent-primary)]" />
                  <span>Best Frames Strip ({bestFrames.length})</span>
                </span>
                {onOpenAnalysisWorkstation && (
                  <button
                    type="button"
                    onClick={onOpenAnalysisWorkstation}
                    className="text-[10px] font-bold text-[var(--accent-primary)] hover:underline"
                  >
                    Open Workstation →
                  </button>
                )}
              </div>

              {isLoadingBestFrames && bestFrames.length === 0 ? (
                <div className="flex items-center gap-1.5 text-[10px] text-[var(--text-secondary)] py-1">
                  <Loader2 className="w-3 h-3 animate-spin" />
                  <span>Extracting sharp keyframes...</span>
                </div>
              ) : (
                <div className="grid grid-cols-3 gap-1.5">
                  {bestFrames.slice(0, 6).map((bf) => (
                    <div
                      key={bf.index}
                      className="rounded-lg bg-[var(--bg-input)] border border-[var(--border-subtle)] overflow-hidden flex flex-col"
                    >
                      <button
                        type="button"
                        onClick={() => onSeekToMasterTime?.(bf.t)}
                        className="relative aspect-video bg-black w-full text-left group"
                        title={`Jump to ${bf.role.replace(/_/g, ' ')} at ${bf.t.toFixed(2)}s`}
                      >
                        {bf.image_url && (
                          <img
                            src={bf.image_url}
                            alt={bf.role}
                            className="w-full h-full object-cover group-hover:scale-105 transition"
                          />
                        )}
                        <span className="absolute bottom-0.5 right-0.5 px-1 rounded bg-black/75 text-[9px] font-mono text-white">
                          {bf.t.toFixed(1)}s
                        </span>
                      </button>
                      <div className="p-1.5 space-y-1">
                        <div className="text-[9px] font-bold text-[var(--text-primary)] truncate">
                          {bf.role.replace(/_/g, ' ')} · {Math.round(bf.sharpness_score)}%
                        </div>
                        <div className="flex items-center gap-1">
                          {onOpenThumbnailFrame && (
                            <button
                              type="button"
                              onClick={() => onOpenThumbnailFrame(bf)}
                              className="flex-1 py-0.5 rounded bg-[var(--bg-card)] border border-[var(--border-subtle)] text-[9px] font-semibold text-[var(--text-primary)] hover:border-[var(--accent-primary)]/50"
                            >
                              Cover
                            </button>
                          )}
                          {onCalibrateFrame && (
                            <button
                              type="button"
                              onClick={() => onCalibrateFrame(bf)}
                              className="flex-1 py-0.5 rounded bg-[var(--bg-card)] border border-[var(--border-subtle)] text-[9px] font-semibold text-[var(--accent-primary)] hover:border-[var(--accent-primary)]/50"
                            >
                              Calibrate
                            </button>
                          )}
                        </div>
                      </div>
                    </div>
                  ))}
                </div>
              )}
            </div>
          )}

          {/* Always-Accessible Source Corner & Output Corner Controls for ALL Multi-Region Templates */}
          {isMultiRegionTemplate && (
            <div className="pt-2 border-t border-[var(--border-subtle)] space-y-2.5">
              {/* Source Region Detection Pill + Re-scan */}
              <div className="flex items-center justify-between gap-2 flex-wrap">
                {isDetectingWebcam ? (
                  <span className="inline-flex items-center gap-1.5 px-2.5 py-1 rounded-full text-[10px] font-semibold bg-[var(--accent-subtle)] text-[var(--accent-primary)] border border-[var(--accent-primary)]/40">
                    <Loader2 className="w-3 h-3 animate-spin" />
                    Scanning 16 frames for source region...
                  </span>
                ) : webcamRegion?.webcam_detected ? (
                  <span
                    data-testid="webcam-status-pill"
                    className="inline-flex items-center gap-1.5 px-2.5 py-1 rounded-full text-[10px] font-semibold bg-emerald-500/15 text-emerald-400 border border-emerald-500/40"
                  >
                    <CheckCircle2 className="w-3 h-3" />
                    Webcam region active (
                    {(webcamRegion.locked_zone && webcamRegion.locked_zone !== 'auto'
                      ? webcamRegion.locked_zone
                      : webcamRegion.source_zone || 'top_left'
                    )
                      .replace(/_/g, ' ')
                      .replace(/\b\w/g, (c) => c.toUpperCase())}
                    {webcamRegion.aspect_ratio
                      ? ` • ${
                          webcamRegion.aspect_ratio >= 1.55
                            ? '16:9'
                            : webcamRegion.aspect_ratio <= 1.15
                            ? '1:1'
                            : '4:3'
                        }`
                      : ''}
                    )
                  </span>
                ) : (
                  <span
                    data-testid="webcam-status-pill"
                    className="inline-flex items-center gap-1.5 px-2.5 py-1 rounded-full text-[10px] font-semibold bg-sky-500/15 text-sky-300 border border-sky-500/40"
                  >
                    <Camera className="w-3 h-3" />
                    Corner crop active ({(webcamRegion?.locked_zone || 'top_left').replace(/_/g, ' ')})
                  </span>
                )}
                <button
                  type="button"
                  disabled={isDetectingWebcam}
                  onClick={() => handleSetSourceWebcamZone('auto', true)}
                  className="inline-flex items-center gap-1 px-2 py-1 rounded-md text-[10px] font-semibold bg-[var(--bg-input)] border border-[var(--border-subtle)] text-[var(--text-secondary)] hover:text-[var(--text-primary)] disabled:opacity-50"
                  title="Re-scan 16 frames across the clip to locate the streamer webcam"
                >
                  <RefreshCw className="w-2.5 h-2.5" />
                  Re-scan
                </button>
              </div>

              {/* Source Region Corner (where secondary region sits in original 16:9 frame) */}
              <div className="space-y-1">
                <div className="flex items-center justify-between text-[11px] text-[var(--text-secondary)]">
                  <span className="inline-flex items-center gap-1">
                    <Camera className="w-3 h-3 text-[var(--accent-primary)]" />
                    Source Region Corner
                  </span>
                  <span className="font-mono text-[10px] text-[var(--accent-primary)]">
                    {webcamRegion?.locked_zone || 'auto'}
                  </span>
                </div>
                <div className="grid grid-cols-3 gap-1.5">
                  {(
                    [
                      { id: 'auto', label: 'Auto' },
                      { id: 'top_left', label: 'Top Left' },
                      { id: 'top_right', label: 'Top Right' },
                      { id: 'bottom_left', label: 'Bottom Left' },
                      { id: 'bottom_right', label: 'Bottom Right' },
                      { id: 'center', label: 'Center' }
                    ] as const
                  ).map((zone) => {
                    const currentZone = webcamRegion?.locked_zone || 'auto';
                    const isSelected = currentZone === zone.id;
                    return (
                      <button
                        key={zone.id}
                        type="button"
                        disabled={isDetectingWebcam}
                        onClick={() => handleSetSourceWebcamZone(zone.id, zone.id === 'auto')}
                        className={`py-1.5 px-2 rounded-lg text-[10px] font-semibold border transition ${
                          isSelected
                            ? 'bg-[var(--accent-subtle)] border-[var(--accent-primary)] text-[var(--accent-primary)]'
                            : 'bg-[var(--bg-input)] border-[var(--border-subtle)] text-[var(--text-secondary)] hover:text-[var(--text-primary)]'
                        }`}
                      >
                        {zone.label}
                      </button>
                    );
                  })}
                </div>
              </div>

              {/* Output Overlay Corner (where PiP / overlay badge sits in 9:16 output) */}
              <div className="space-y-1">
                <div className="flex items-center justify-between text-[11px] text-[var(--text-secondary)]">
                  <span>Output Overlay Corner</span>
                  <span className="font-mono text-[10px] text-[var(--accent-primary)]">
                    {clip.webcam_corner && clip.webcam_corner !== 'auto' ? clip.webcam_corner : 'top_right'}
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
                  const activeCorner = clip.webcam_corner && clip.webcam_corner !== 'auto' ? clip.webcam_corner : 'top_right';
                  const selected = activeCorner === corner.id;
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
            </div>
          )}

          {/* Virtual Camera Motion & Whip-Pan Cuts */}
          <div className="pt-2 border-t border-[var(--border-subtle)] space-y-2">
            <div className="flex items-center justify-between gap-2">
              <span className="text-[11px] font-semibold text-[var(--text-secondary)]">
                Camera Motion
              </span>
              <span className="font-mono text-[10px] text-[var(--accent-primary)]">
                {clip.camera_motion || 'steady'}
              </span>
            </div>
            <div className="grid grid-cols-2 sm:grid-cols-4 gap-1.5">
              {(
                [
                  { id: 'steady', label: 'Steady' },
                  { id: 'dolly_in', label: 'Slow Dolly In' },
                  { id: 'dolly_out', label: 'Dolly Out' },
                  { id: 'beat_punch', label: 'Beat Punch' }
                ] as const
              ).map((m) => {
                const selected = (clip.camera_motion || 'steady') === m.id;
                return (
                  <button
                    key={m.id}
                    type="button"
                    onClick={() => onUpdateClip({ camera_motion: m.id })}
                    className={`py-1.5 px-2 rounded-lg text-[10px] font-semibold border transition ${
                      selected
                        ? 'bg-[var(--accent-subtle)] border-[var(--accent-primary)] text-[var(--accent-primary)]'
                        : 'bg-[var(--bg-input)] border-[var(--border-subtle)] text-[var(--text-secondary)] hover:text-[var(--text-primary)]'
                    }`}
                  >
                    {m.label}
                  </button>
                );
              })}
            </div>
            <button
              type="button"
              onClick={() => onUpdateClip({ whip_pan_cuts: !clip.whip_pan_cuts })}
              className={`w-full flex items-center justify-between px-2.5 py-1.5 rounded-lg text-[11px] font-semibold border transition ${
                clip.whip_pan_cuts
                  ? 'bg-[var(--accent-subtle)] border-[var(--accent-primary)] text-[var(--text-primary)]'
                  : 'bg-[var(--bg-input)] border-[var(--border-subtle)] text-[var(--text-secondary)] hover:text-[var(--text-primary)]'
              }`}
            >
              <span>Whip-Pan Scene Cuts</span>
              <span className="font-mono text-[10px] text-[var(--accent-primary)]">
                {clip.whip_pan_cuts ? 'ON (0.20s blur)' : 'OFF'}
              </span>
            </button>
          </div>

          {/* Zoom, Pan & Horizon Tilt Sliders */}
          <div className="grid grid-cols-1 sm:grid-cols-3 gap-3 pt-2 border-t border-[var(--border-subtle)] tabular-nums">
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
            <div>
              <div className="flex justify-between text-[11px] text-[var(--text-secondary)] mb-1">
                <span>Horizon Tilt</span>
                <button
                  type="button"
                  onClick={() => onUpdateClip({ horizon_tilt: 0 })}
                  title="Reset horizon tilt to 0°"
                  className="font-mono text-[var(--accent-primary)] font-bold hover:underline"
                >
                  {(clip.horizon_tilt || 0) > 0 ? `+${(clip.horizon_tilt || 0).toFixed(1)}°` : `${(clip.horizon_tilt || 0).toFixed(1)}°`}
                </button>
              </div>
              <input
                type="range"
                min={-10}
                max={10}
                step={0.5}
                value={clip.horizon_tilt || 0}
                onChange={(e) => onUpdateClip({ horizon_tilt: parseFloat(e.target.value) })}
                onDoubleClick={() => onUpdateClip({ horizon_tilt: 0 })}
                aria-label="Horizon Tilt rotation degrees"
                className="w-full h-1.5 bg-[var(--bg-input)] rounded appearance-none cursor-pointer"
              />
            </div>
          </div>

          <div className="flex items-center justify-between pt-2">
            <span className="text-[10px] text-[var(--text-muted)]">
              Level horizon rotation auto-zooms to hide black corners on export.
            </span>
            <button
              type="button"
              onClick={() => window.dispatchEvent(new CustomEvent('lh-toggle-thirds'))}
              className="px-2.5 py-1 rounded-lg bg-[var(--bg-input)] border border-[var(--border-subtle)] hover:border-[var(--accent-primary)] text-[10px] font-bold text-[var(--text-primary)] transition"
            >
              Toggle Rule-of-Thirds Grid
            </button>
          </div>

          {/* Virtual Studio Backdrop (AI Person Segmentation) */}
          <div className="pt-3 border-t border-[var(--border-subtle)] space-y-2">
            <div className="flex items-center justify-between">
              <span className="font-bold text-xs text-[var(--text-primary)] flex items-center gap-1.5 uppercase tracking-wide">
                <Layers className="w-3.5 h-3.5 text-[var(--accent-primary)]" />
                Virtual Studio Backdrop
              </span>
              <span className="text-[10px] text-[var(--text-secondary)]">AI subject cutout</span>
            </div>
            <div className="grid grid-cols-2 sm:grid-cols-5 gap-1.5">
              {[
                { id: 'none', label: 'Original' },
                { id: 'studio_blur', label: 'Studio Bokeh' },
                { id: 'solid_charcoal', label: 'Charcoal' },
                { id: 'accent_gradient', label: 'Gradient' },
                { id: 'gameplay_blur', label: 'Game Blur' }
              ].map((preset) => {
                const active = (clip.backdrop_mode || 'none') === preset.id;
                return (
                  <button
                    key={preset.id}
                    type="button"
                    onClick={() => onUpdateClip({ backdrop_mode: preset.id })}
                    className={`py-1.5 px-2 rounded-lg border text-[10px] font-semibold transition truncate ${
                      active
                        ? 'bg-[var(--accent-subtle)] border-[var(--accent-primary)] text-[var(--accent-primary)]'
                        : 'bg-[var(--bg-input)] border-[var(--border-subtle)] text-[var(--text-secondary)] hover:text-[var(--text-primary)]'
                    }`}
                  >
                    {preset.label}
                  </button>
                );
              })}
            </div>

            {(clip.backdrop_mode === 'studio_blur' || clip.backdrop_mode === 'gameplay_blur') && (
              <div>
                <div className="flex justify-between text-[10px] text-[var(--text-secondary)] mb-1">
                  <span>Bokeh Blur Intensity</span>
                  <span className="font-mono text-[var(--accent-primary)] font-bold">
                    {Math.round(clip.backdrop_blur_intensity ?? 18)}
                  </span>
                </div>
                <input
                  type="range"
                  min={6}
                  max={45}
                  step={2}
                  value={clip.backdrop_blur_intensity ?? 18}
                  onChange={(e) => onUpdateClip({ backdrop_blur_intensity: parseFloat(e.target.value) })}
                  className="w-full h-1.5 bg-[var(--bg-input)] rounded appearance-none cursor-pointer"
                />
              </div>
            )}

            {(clip.backdrop_mode === 'solid_charcoal' || clip.backdrop_mode === 'accent_gradient') && (
              <div className="flex items-center justify-between gap-2 bg-[var(--bg-input)] border border-[var(--border-subtle)] rounded-lg px-2.5 py-1.5">
                <span className="text-[11px] text-[var(--text-secondary)]">Backdrop Studio Color</span>
                <div className="flex items-center gap-2">
                  {['#18181B', '#0F172A', '#6366F1', '#10B981'].map((col) => (
                    <button
                      key={col}
                      type="button"
                      onClick={() => onUpdateClip({ backdrop_color: col })}
                      className="w-4 h-4 rounded-full border border-white/25"
                      style={{ backgroundColor: col }}
                      title={col}
                    />
                  ))}
                  <input
                    type="color"
                    value={clip.backdrop_color || '#18181B'}
                    onChange={(e) => onUpdateClip({ backdrop_color: e.target.value })}
                    className="w-6 h-5 rounded cursor-pointer border-0 bg-transparent p-0"
                  />
                </div>
              </div>
            )}
          </div>
        </div>
        </div>
      </div>
    </div>
  );
};
