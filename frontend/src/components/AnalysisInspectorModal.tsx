import React, { useEffect, useMemo, useState } from 'react';
import {
  Activity,
  AlertTriangle,
  Camera,
  CheckCircle2,
  Crosshair,
  Eye,
  Film,
  Image as ImageIcon,
  Loader2,
  Music,
  Play,
  RefreshCw,
  Scissors,
  Sparkles,
  Users,
  Volume2,
  X,
} from 'lucide-react';
import { api } from '../api/client';
import { BestFrameItem, BestFramesResponse, ViralClip } from '../types';

interface AnalysisInspectorModalProps {
  isOpen: boolean;
  onClose: () => void;
  clip: ViralClip | null;
  projectId?: number;
  currentPlaybackTime?: number;
  onSeekToMasterTime: (masterTime: number) => void;
  onSetThumbnailCover: (frame: BestFrameItem) => void;
  onSnapClipStart: (frame: BestFrameItem) => Promise<void> | void;
  onCalibrateCamera: (frame: BestFrameItem) => Promise<void> | void;
  onClipUpdated?: (updatedClip: ViralClip) => void;
}

const EMOTION_COLORS: Record<string, string> = {
  joy: '#10b981',
  surprise: '#a855f7',
  anger: '#ef4444',
  fear: '#f97316',
  disgust: '#84cc16',
  sadness: '#38bdf8',
  neutral: '#94a3b8',
};

const formatTagLabel = (raw?: string | null) =>
  (raw || 'neutral').replace(/_/g, ' ').replace(/\b\w/g, (c) => c.toUpperCase());

const ROLE_META: Record<
  string,
  { label: string; badgeClass: string }
> = {
  peak_emotion: {
    label: 'Peak Emotion',
    badgeClass: 'bg-emerald-500/15 text-emerald-400 border-emerald-500/40',
  },
  peak_action: {
    label: 'Peak Action',
    badgeClass: 'bg-amber-500/15 text-amber-400 border-amber-500/40',
  },
  clean_anchor: {
    label: 'Clean Anchor',
    badgeClass: 'bg-sky-500/15 text-sky-400 border-sky-500/40',
  },
  clean_opener: {
    label: 'Clean Opener',
    badgeClass: 'bg-violet-500/15 text-violet-400 border-violet-500/40',
  },
  highlight_moment: {
    label: 'Highlight',
    badgeClass: 'bg-[var(--accent-subtle)] text-[var(--accent-primary)] border-[var(--accent-primary)]/40',
  },
};

export const AnalysisInspectorModal: React.FC<AnalysisInspectorModalProps> = ({
  isOpen,
  onClose,
  clip,
  projectId,
  currentPlaybackTime,
  onSeekToMasterTime,
  onSetThumbnailCover,
  onSnapClipStart,
  onCalibrateCamera,
  onClipUpdated,
}) => {
  const [data, setData] = useState<BestFramesResponse | null>(null);
  const [isLoading, setIsLoading] = useState(false);
  const [isReanalyzing, setIsReanalyzing] = useState(false);
  const [isApplyingFixes, setIsApplyingFixes] = useState(false);
  const [busyAction, setBusyAction] = useState<string | null>(null);
  const [statusNote, setStatusNote] = useState<string | null>(null);
  const [errorMsg, setErrorMsg] = useState<string | null>(null);
  const [hoveredT, setHoveredT] = useState<number | null>(null);

  const effProjectId = projectId || clip?.project_id;

  const loadAnalysis = async (force = false) => {
    if (!clip || !effProjectId) return;
    setErrorMsg(null);
    setStatusNote(null);
    if (force) setIsReanalyzing(true);
    else setIsLoading(true);
    try {
      const res: BestFramesResponse = force
        ? await api.reanalyzeClip(effProjectId, clip.id)
        : await api.getBestFrames(effProjectId, clip.id);
      setData(res);
      if (force) {
        setStatusNote(
          `Re-analyzed ${res.multimodal_timeline?.length || 0} windows & extracted ${res.frames?.length || 0} Best Frames.`
        );
      }
    } catch (err: any) {
      setErrorMsg(err?.message || 'Failed to inspect video & audio telemetry.');
    } finally {
      setIsLoading(false);
      setIsReanalyzing(false);
    }
  };

  useEffect(() => {
    if (!isOpen || !clip || !effProjectId) return;
    void loadAnalysis(false);
  }, [isOpen, clip?.id, effProjectId]);

  const timeline = data?.multimodal_timeline || [];
  const frames = data?.frames || [];
  const cuts = data?.scene_cuts || [];
  const summary = data?.multimodal_summary;
  const emotionShifts = data?.emotion_shifts || [];
  const acousticEvents = data?.acoustic_events || [];
  const emotionBreakdown = data?.clip_emotion_breakdown;
  const retentionDiag = data?.retention_diagnostics;

  const winStart = useMemo(() => {
    if (timeline.length > 0) return timeline[0].t;
    return Number(clip?.start_time || 0);
  }, [timeline, clip?.start_time]);

  const winEnd = useMemo(() => {
    if (timeline.length > 0) return Math.max(winStart + 0.5, timeline[timeline.length - 1].t);
    return Math.max(winStart + 0.5, Number(clip?.end_time || winStart + 5));
  }, [timeline, winStart, clip?.end_time]);

  const winSpan = Math.max(0.5, winEnd - winStart);

  const svgGeometry = useMemo(() => {
    const W = 760;
    const H = 160;
    const padX = 14;
    const padY = 18;
    const innerW = W - padX * 2;
    const innerH = H - padY * 2;

    const toX = (t: number) =>
      padX + Math.max(0, Math.min(1, (t - winStart) / winSpan)) * innerW;
    const toY = (val: number) =>
      padY + (1 - Math.max(0, Math.min(100, val)) / 100) * innerH;

    const buildPath = (extractor: (pt: (typeof timeline)[number]) => number) => {
      if (!timeline.length) return '';
      return timeline
        .map((pt, i) => `${i === 0 ? 'M' : 'L'} ${toX(pt.t).toFixed(1)} ${toY(extractor(pt)).toFixed(1)}`)
        .join(' ');
    };

    const audioPath = buildPath((p) => p.audio_prosody_score);
    const visualPath = buildPath((p) => p.visual_emotion_action_score);
    const combinedPath = buildPath((p) => p.combined_engagement_score);
    const eyeContactPath = buildPath((p) => p.eye_contact_score ?? p.gaze_engagement_score ?? 0);
    const noveltyPath = buildPath((p) => p.visual_novelty_score ?? 0);
    const arousalPath = buildPath((p) => p.fused_arousal ?? p.emotion_intensity ?? 35);
    const areaPath = timeline.length
      ? `${combinedPath} L ${toX(timeline[timeline.length - 1].t).toFixed(1)} ${(padY + innerH).toFixed(1)} L ${toX(timeline[0].t).toFixed(1)} ${(padY + innerH).toFixed(1)} Z`
      : '';
    const arousalAreaPath = timeline.length
      ? `${arousalPath} L ${toX(timeline[timeline.length - 1].t).toFixed(1)} ${(padY + innerH).toFixed(1)} L ${toX(timeline[0].t).toFixed(1)} ${(padY + innerH).toFixed(1)} Z`
      : '';

    return {
      W,
      H,
      padX,
      padY,
      innerW,
      innerH,
      toX,
      toY,
      audioPath,
      visualPath,
      combinedPath,
      eyeContactPath,
      noveltyPath,
      areaPath,
      arousalPath,
      arousalAreaPath,
    };
  }, [timeline, winStart, winSpan]);

  const hoveredPoint = useMemo(() => {
    if (hoveredT === null || !timeline.length) return null;
    return timeline.reduce((best, cur) =>
      Math.abs(cur.t - hoveredT) < Math.abs(best.t - hoveredT) ? cur : best
    );
  }, [hoveredT, timeline]);

  if (!isOpen || !clip) return null;

  return (
    <div className="fixed inset-0 z-50 bg-black/75 backdrop-blur-sm flex items-center justify-center p-3 lg:p-5 animate-fadeIn">
      <div className="w-full max-w-5xl max-h-[92vh] bg-[var(--bg-surface)] border border-[var(--border-subtle)] rounded-2xl shadow-2xl flex flex-col overflow-hidden text-[var(--text-primary)]">
        {/* Modal Header */}
        <div className="px-5 py-3.5 border-b border-[var(--border-subtle)] bg-[var(--bg-card)] flex flex-wrap items-center justify-between gap-3 shrink-0">
          <div className="flex items-center gap-2.5 min-w-0">
            <div className="w-8 h-8 rounded-xl bg-[var(--accent-subtle)] border border-[var(--accent-primary)]/40 flex items-center justify-center shrink-0">
              <Sparkles className="w-4 h-4 text-[var(--accent-primary)]" />
            </div>
            <div className="min-w-0">
              <h2 className="font-bold text-sm text-[var(--text-primary)] truncate">
                AI Video &amp; Audio Intelligence + Best-Frame Workstation
              </h2>
              <p className="text-[11px] text-[var(--text-secondary)] truncate">
                EBU R128 Loudness, Spectral Prosody, Optical Flow, Face Blendshapes &amp; Sharpness Keyframes
              </p>
            </div>
          </div>

          <div className="flex items-center gap-2 shrink-0">
            {summary && (
              <div className="px-2.5 py-1 rounded-lg bg-emerald-500/15 border border-emerald-500/35 text-emerald-400 text-[11px] font-bold tabular-nums">
                Score {Math.round(summary.overall_engagement_score)}/100
              </div>
            )}
            <button
              type="button"
              disabled={isReanalyzing || isLoading}
              onClick={() => void loadAnalysis(true)}
              className="flex items-center gap-1.5 px-3 py-1.5 rounded-lg bg-[var(--accent-primary)] text-[var(--accent-foreground)] text-xs font-bold hover:opacity-90 transition disabled:opacity-50"
              title="Run fresh audio loudness, vision quality & Best Frame extraction"
            >
              {isReanalyzing ? (
                <Loader2 className="w-3.5 h-3.5 animate-spin" />
              ) : (
                <RefreshCw className="w-3.5 h-3.5" />
              )}
              <span>{isReanalyzing ? 'Analyzing...' : 'Re-Analyze Audio & Video'}</span>
            </button>
            <button
              type="button"
              onClick={onClose}
              className="p-1.5 rounded-lg text-[var(--text-secondary)] hover:text-[var(--text-primary)] hover:bg-[var(--bg-input)] transition"
              aria-label="Close Analysis Workstation"
            >
              <X className="w-4 h-4" />
            </button>
          </div>
        </div>

        {/* Scrollable Workspace */}
        <div className="flex-1 overflow-y-auto p-5 space-y-5">
          {statusNote && (
            <div className="px-3 py-2 rounded-xl bg-emerald-500/10 border border-emerald-500/30 text-emerald-400 text-xs font-medium flex items-center gap-2">
              <CheckCircle2 className="w-4 h-4 shrink-0" />
              <span>{statusNote}</span>
            </div>
          )}
          {errorMsg && (
            <div className="px-3 py-2 rounded-xl bg-red-500/10 border border-red-500/30 text-red-400 text-xs font-medium">
              {errorMsg}
            </div>
          )}

          {isLoading && !data ? (
            <div className="py-16 flex flex-col items-center justify-center gap-3 text-[var(--text-secondary)] text-xs">
              <Loader2 className="w-7 h-7 animate-spin text-[var(--accent-primary)]" />
              <span>Running multimodal prosody &amp; frame-quality scan...</span>
            </div>
          ) : (
            <>
              {/* Telemetry Summary Cards */}
              {summary && (
                <div className="grid grid-cols-2 md:grid-cols-5 gap-2.5 tabular-nums">
                  <div className="p-3 rounded-xl bg-[var(--bg-card)] border border-[var(--border-subtle)] space-y-1">
                    <div className="flex items-center gap-1.5 text-[10px] font-semibold uppercase tracking-wider text-[var(--text-secondary)]">
                      <Volume2 className="w-3.5 h-3.5 text-emerald-400" />
                      <span>Integrated LUFS</span>
                    </div>
                    <div className="text-base font-black text-[var(--text-primary)]">
                      {summary.integrated_lufs.toFixed(1)} <span className="text-[11px] font-semibold text-[var(--text-secondary)]">LUFS</span>
                    </div>
                    <div className="text-[10px] font-mono text-[var(--text-secondary)]">
                      LRA {summary.lra.toFixed(1)} LU · Peak {summary.true_peak_db.toFixed(1)} dB
                    </div>
                  </div>

                  <div className="p-3 rounded-xl bg-[var(--bg-card)] border border-[var(--border-subtle)] space-y-1">
                    <div className="flex items-center gap-1.5 text-[10px] font-semibold uppercase tracking-wider text-[var(--text-secondary)]">
                      <Activity className="w-3.5 h-3.5 text-[var(--accent-primary)]" />
                      <span>Speech Cadence</span>
                    </div>
                    <div className="text-base font-black text-[var(--text-primary)]">
                      {Math.round(summary.avg_wpm)} <span className="text-[11px] font-semibold text-[var(--text-secondary)]">WPM</span>
                    </div>
                    <div className="text-[10px] font-mono text-[var(--text-secondary)]">
                      Composite {Math.round(summary.overall_engagement_score)}/100
                    </div>
                  </div>

                  <div className="p-3 rounded-xl bg-[var(--bg-card)] border border-[var(--border-subtle)] space-y-1">
                    <div className="flex items-center gap-1.5 text-[10px] font-semibold uppercase tracking-wider text-[var(--text-secondary)]">
                      <Camera className="w-3.5 h-3.5 text-sky-400" />
                      <span>Sharpness Avg</span>
                    </div>
                    <div className="text-base font-black text-[var(--text-primary)]">
                      {Math.round(summary.avg_sharpness)}%
                    </div>
                    <div className="text-[10px] font-mono text-[var(--text-secondary)]">
                      Exposure {Math.round(summary.avg_exposure)}%
                    </div>
                  </div>

                  <div className="p-3 rounded-xl bg-[var(--bg-card)] border border-[var(--border-subtle)] space-y-1">
                    <div className="flex items-center gap-1.5 text-[10px] font-semibold uppercase tracking-wider text-[var(--text-secondary)]">
                      <Sparkles className="w-3.5 h-3.5 text-amber-400" />
                      <span>Face &amp; Action</span>
                    </div>
                    <div className="text-base font-black text-[var(--text-primary)]">
                      {Math.round(summary.face_presence_ratio * 100)}% <span className="text-[11px] font-semibold text-[var(--text-secondary)]">Face</span>
                    </div>
                    <div className="text-[10px] font-mono text-[var(--text-secondary)]">
                      Motion {Math.round(summary.avg_action)}/100
                    </div>
                  </div>

                  <div className="p-3 rounded-xl bg-[var(--bg-card)] border border-[var(--border-subtle)] space-y-1">
                    <div className="flex items-center gap-1.5 text-[10px] font-semibold uppercase tracking-wider text-[var(--text-secondary)]">
                      <Film className="w-3.5 h-3.5 text-rose-400" />
                      <span>Shot Structure</span>
                    </div>
                    <div className="text-base font-black text-[var(--text-primary)]">
                      {summary.scene_cut_count} <span className="text-[11px] font-semibold text-[var(--text-secondary)]">Cuts</span>
                    </div>
                    <div className="text-[10px] font-mono text-[var(--text-secondary)]">
                      {frames.length} Best Keyframes
                    </div>
                  </div>
                </div>
              )}

              {/* Retention Grade & Smart Diagnostics Panel */}
              {retentionDiag && (
                <div className="p-4 rounded-xl bg-[var(--bg-card)] border border-[var(--border-subtle)] space-y-3">
                  <div className="flex flex-wrap items-center justify-between gap-2.5">
                    <div className="flex items-center gap-2.5">
                      <span
                        className={`w-9 h-9 rounded-xl flex items-center justify-center font-black text-base border tabular-nums ${
                          retentionDiag.retention_grade === 'S'
                            ? 'bg-emerald-500/20 text-emerald-400 border-emerald-500/40'
                            : retentionDiag.retention_grade === 'A'
                              ? 'bg-sky-500/20 text-sky-400 border-sky-500/40'
                              : retentionDiag.retention_grade === 'B'
                                ? 'bg-amber-500/20 text-amber-400 border-amber-500/40'
                                : 'bg-rose-500/20 text-rose-400 border-rose-500/40'
                        }`}
                        title={`Retention Score ${Math.round(retentionDiag.retention_score)}/100`}
                      >
                        {retentionDiag.retention_grade}
                      </span>
                      <div>
                        <div className="text-xs font-bold text-[var(--text-primary)] flex items-center gap-2">
                          <span>Retention Grade &amp; Smart Diagnostics</span>
                          <span className="text-[10px] font-mono text-[var(--text-secondary)]">
                            ({Math.round(retentionDiag.retention_score)}/100)
                          </span>
                        </div>
                        <div className="flex flex-wrap items-center gap-1.5 mt-1 text-[10px] font-semibold tabular-nums">
                          <span className="px-2 py-0.5 rounded-full bg-[var(--bg-input)] border border-[var(--border-subtle)] text-[var(--text-primary)]">
                            Fluency: {Math.round(retentionDiag.speech_fluency.avg_fluency_score)}%
                          </span>
                          <span className="px-2 py-0.5 rounded-full bg-[var(--bg-input)] border border-[var(--border-subtle)] text-sky-400 flex items-center gap-1">
                            <Eye className="w-3 h-3" />
                            Eye-Contact: {Math.round(retentionDiag.avg_eye_contact_score)}%
                          </span>
                          <span className="px-2 py-0.5 rounded-full bg-[var(--bg-input)] border border-[var(--border-subtle)] text-fuchsia-400">
                            Visual Novelty: {Math.round(retentionDiag.avg_visual_novelty)}%
                          </span>
                          {retentionDiag.music_bed_summary.has_existing_music && (
                            <span className="px-2 py-0.5 rounded-full bg-emerald-500/15 border border-emerald-500/35 text-emerald-300 flex items-center gap-1">
                              <Music className="w-3 h-3" />
                              Source Music Detected
                              {retentionDiag.music_bed_summary.estimated_bpm > 0
                                ? ` · ${Math.round(retentionDiag.music_bed_summary.estimated_bpm)} BPM`
                                : ''}
                            </span>
                          )}
                        </div>
                      </div>
                    </div>

                    {retentionDiag.smart_fixes.length > 0 && effProjectId && (
                      <button
                        type="button"
                        disabled={isApplyingFixes || busyAction !== null}
                        onClick={async () => {
                          setIsApplyingFixes(true);
                          setErrorMsg(null);
                          try {
                            const res = await api.applyRetentionFixes(effProjectId, clip.id);
                            if (res?.retention_diagnostics) {
                              setData((prev) =>
                                prev ? { ...prev, retention_diagnostics: res.retention_diagnostics } : prev
                              );
                            }
                            if (res?.clip && onClipUpdated) {
                              onClipUpdated(res.clip);
                            }
                            const count = res?.applied_fixes?.length || 0;
                            setStatusNote(
                              count > 0
                                ? `Applied ${count} smart retention optimization${count > 1 ? 's' : ''}.`
                                : 'All smart retention checks are already optimized.'
                            );
                          } catch (err: any) {
                            setErrorMsg(err?.message || 'Failed to apply smart retention fixes.');
                          } finally {
                            setIsApplyingFixes(false);
                          }
                        }}
                        className="px-3 py-1.5 rounded-lg bg-[var(--accent-primary)] text-[var(--accent-foreground)] text-xs font-bold flex items-center gap-1.5 hover:opacity-90 transition disabled:opacity-50"
                      >
                        {isApplyingFixes ? (
                          <Loader2 className="w-3.5 h-3.5 animate-spin" />
                        ) : (
                          <Sparkles className="w-3.5 h-3.5" />
                        )}
                        <span>✨ Apply Smart Retention Fixes ({retentionDiag.smart_fixes.length})</span>
                      </button>
                    )}
                  </div>

                  {/* Speaker Breakdown Bar + Cross-Talk Overlap Pins */}
                  {Object.keys(retentionDiag.speaker_breakdown.dominance_ratios || {}).length > 0 && (
                    <div className="p-2.5 rounded-lg bg-[var(--bg-input)] border border-[var(--border-subtle)] space-y-1.5">
                      <div className="flex flex-wrap items-center justify-between gap-2 text-[10px]">
                        <span className="font-bold text-[var(--text-primary)] flex items-center gap-1.5">
                          <Users className="w-3.5 h-3.5 text-[var(--accent-primary)]" />
                          <span>Speaker Dominance Breakdown</span>
                        </span>
                        <span className="font-mono text-[var(--text-secondary)]">
                          {Object.entries(retentionDiag.speaker_breakdown.dominance_ratios)
                            .map(([sp, ratio]) => `${sp} ${Math.round(Number(ratio) * 100)}%`)
                            .join(' · ')}
                        </span>
                      </div>
                      <div className="h-2 w-full rounded-full overflow-hidden flex bg-[var(--bg-card)] border border-[var(--border-subtle)]">
                        {Object.entries(retentionDiag.speaker_breakdown.dominance_ratios).map(([sp, ratio], idx) => {
                          const colors = ['var(--accent-primary)', '#38BDF8', '#F472B6', '#FBBF24'];
                          return (
                            <div
                              key={sp}
                              style={{
                                width: `${Math.max(4, Math.round(Number(ratio) * 100))}%`,
                                backgroundColor: colors[idx % colors.length],
                              }}
                              title={`${sp}: ${Math.round(Number(ratio) * 100)}%`}
                            />
                          );
                        })}
                      </div>
                      {retentionDiag.speaker_breakdown.crosstalk_overlaps.length > 0 && (
                        <div className="flex flex-wrap items-center gap-1.5 pt-1">
                          <span className="text-[10px] font-semibold text-amber-400">Cross-Talk Overlaps:</span>
                          {retentionDiag.speaker_breakdown.crosstalk_overlaps.slice(0, 6).map((ov, i) => (
                            <button
                              key={`ct-ov-${i}`}
                              type="button"
                              onClick={() => onSeekToMasterTime(ov.t)}
                              className="px-2 py-0.5 rounded-full bg-amber-500/15 border border-amber-500/35 text-amber-300 text-[10px] font-bold hover:brightness-110 transition tabular-nums"
                            >
                              Clash {ov.t.toFixed(1)}s–{ov.end.toFixed(1)}s
                            </button>
                          ))}
                        </div>
                      )}
                    </div>
                  )}

                  {/* HUD Collision Alert & Retention Risk Zones */}
                  {(retentionDiag.hud_obstruction.hud_collision ||
                    retentionDiag.hud_obstruction.bottom_hud_occupied ||
                    retentionDiag.risk_zones.length > 0 ||
                    retentionDiag.smart_fixes.length > 0) && (
                    <div className="flex flex-wrap items-center gap-2 text-[10px]">
                      {retentionDiag.hud_obstruction.hud_collision ? (
                        <div className="px-2.5 py-1.5 rounded-lg bg-amber-500/15 border border-amber-500/40 text-amber-300 font-semibold flex items-center gap-1.5">
                          <AlertTriangle className="w-3.5 h-3.5 shrink-0" />
                          <span>
                            HUD / Burned-in Subtitle Collision Detected — recommended Caption Y ={' '}
                            {retentionDiag.hud_obstruction.recommended_caption_y.toFixed(2)}
                          </span>
                        </div>
                      ) : retentionDiag.hud_obstruction.bottom_hud_occupied ? (
                        <div className="px-2.5 py-1.5 rounded-lg bg-emerald-500/10 border border-emerald-500/30 text-emerald-300 font-semibold flex items-center gap-1.5">
                          <CheckCircle2 className="w-3.5 h-3.5 shrink-0" />
                          <span>Bottom HUD detected — captions safely cleared to Top Zone</span>
                        </div>
                      ) : null}

                      {retentionDiag.risk_zones.map((rz, idx) => (
                        <button
                          key={`rz-${idx}`}
                          type="button"
                          onClick={() => onSeekToMasterTime(rz.start)}
                          className="px-2.5 py-1 rounded-lg bg-rose-500/15 border border-rose-500/35 text-rose-300 font-semibold hover:brightness-110 transition tabular-nums"
                          title={rz.reason}
                        >
                          Drop-Off Risk {rz.start.toFixed(1)}s–{rz.end.toFixed(1)}s ({rz.duration.toFixed(1)}s)
                        </button>
                      ))}

                      {retentionDiag.smart_fixes.map((fx) => (
                        <span
                          key={fx.id}
                          className="px-2 py-0.5 rounded-full bg-[var(--bg-input)] border border-[var(--border-subtle)] text-[var(--text-secondary)]"
                          title={fx.reason}
                        >
                          Recommended: {fx.label}
                        </span>
                      ))}
                    </div>
                  )}
                </div>
              )}

              {/* Synchronized Multi-Track Telemetry Graph */}
              <div className="p-4 rounded-xl bg-[var(--bg-card)] border border-[var(--border-subtle)] space-y-2.5">
                <div className="flex flex-wrap items-center justify-between gap-2">
                  <div className="flex items-center gap-2">
                    <Activity className="w-4 h-4 text-[var(--accent-primary)]" />
                    <span className="font-bold text-xs text-[var(--text-primary)]">
                      Synchronized Multi-Track Telemetry (click graph to seek preview)
                    </span>
                  </div>
                  <div className="flex flex-wrap items-center gap-3 text-[10px] font-semibold">
                    <span className="flex items-center gap-1 text-[var(--accent-primary)]">
                      <span className="w-2.5 h-0.5 bg-[var(--accent-primary)] inline-block" /> Composite
                    </span>
                    <span className="flex items-center gap-1 text-emerald-400">
                      <span className="w-2.5 h-0.5 bg-emerald-400 inline-block" /> Audio Excitement
                    </span>
                    <span className="flex items-center gap-1 text-amber-400">
                      <span className="w-2.5 h-0.5 bg-amber-400 inline-block" /> Motion &amp; Emotion
                    </span>
                    <span className="flex items-center gap-1 text-sky-400">
                      <span className="w-2.5 h-0.5 bg-sky-400 inline-block" /> Eye-Contact
                    </span>
                    <span className="flex items-center gap-1 text-fuchsia-400">
                      <span className="w-2.5 h-0.5 bg-fuchsia-400 inline-block" /> Visual Novelty
                    </span>
                    <span className="flex items-center gap-1 text-rose-400">
                      <span className="w-0.5 h-2.5 bg-rose-400 inline-block" /> Scene Cut
                    </span>
                  </div>
                </div>

                <div className="relative rounded-xl bg-[var(--bg-input)] border border-[var(--border-subtle)] p-2">
                  <svg
                    viewBox={`0 0 ${svgGeometry.W} ${svgGeometry.H}`}
                    className="w-full h-40 cursor-crosshair select-none overflow-visible"
                    onMouseMove={(e) => {
                      const rect = e.currentTarget.getBoundingClientRect();
                      const ratio = Math.max(0, Math.min(1, (e.clientX - rect.left) / Math.max(1, rect.width)));
                      setHoveredT(winStart + ratio * winSpan);
                    }}
                    onMouseLeave={() => setHoveredT(null)}
                    onClick={(e) => {
                      const rect = e.currentTarget.getBoundingClientRect();
                      const ratio = Math.max(0, Math.min(1, (e.clientX - rect.left) / Math.max(1, rect.width)));
                      onSeekToMasterTime(winStart + ratio * winSpan);
                    }}
                  >
                    {/* Horizontal grid */}
                    {[25, 50, 75].map((pct) => (
                      <line
                        key={pct}
                        x1={svgGeometry.padX}
                        y1={svgGeometry.toY(pct)}
                        x2={svgGeometry.W - svgGeometry.padX}
                        y2={svgGeometry.toY(pct)}
                        stroke="currentColor"
                        strokeOpacity={0.12}
                        strokeDasharray="3 4"
                      />
                    ))}

                    {/* Composite area fill */}
                    {svgGeometry.areaPath && (
                      <path
                        d={svgGeometry.areaPath}
                        fill="var(--accent-primary)"
                        fillOpacity={0.10}
                      />
                    )}

                    {/* Audio Excitement */}
                    {svgGeometry.audioPath && (
                      <path
                        d={svgGeometry.audioPath}
                        fill="none"
                        stroke="#34d399"
                        strokeWidth={2}
                        strokeLinejoin="round"
                      />
                    )}

                    {/* Visual Motion & Emotion */}
                    {svgGeometry.visualPath && (
                      <path
                        d={svgGeometry.visualPath}
                        fill="none"
                        stroke="#fbbf24"
                        strokeWidth={2}
                        strokeLinejoin="round"
                      />
                    )}

                    {/* Eye-Contact Engagement */}
                    {svgGeometry.eyeContactPath && (
                      <path
                        d={svgGeometry.eyeContactPath}
                        fill="none"
                        stroke="#38bdf8"
                        strokeWidth={1.7}
                        strokeDasharray="4 2"
                        strokeLinejoin="round"
                      />
                    )}

                    {/* Visual Novelty */}
                    {svgGeometry.noveltyPath && (
                      <path
                        d={svgGeometry.noveltyPath}
                        fill="none"
                        stroke="#e879f9"
                        strokeWidth={1.7}
                        strokeLinejoin="round"
                      />
                    )}

                    {/* Combined Retention */}
                    {svgGeometry.combinedPath && (
                      <path
                        d={svgGeometry.combinedPath}
                        fill="none"
                        stroke="var(--accent-primary)"
                        strokeWidth={2.5}
                        strokeLinejoin="round"
                      />
                    )}

                    {/* Scene Cuts */}
                    {cuts.map((c, idx) => {
                      const x = svgGeometry.toX(c.t);
                      return (
                        <g key={`cut-${idx}`}>
                          <line
                            x1={x}
                            y1={svgGeometry.padY}
                            x2={x}
                            y2={svgGeometry.H - svgGeometry.padY}
                            stroke="#fb7185"
                            strokeWidth={1.5}
                            strokeDasharray="3 3"
                          />
                          <text
                            x={x + 3}
                            y={svgGeometry.padY + 9}
                            fontSize={9}
                            fill="#fb7185"
                            fontFamily="monospace"
                          >
                            CUT {c.t.toFixed(1)}s
                          </text>
                        </g>
                      );
                    })}

                    {/* Current playhead indicator */}
                    {typeof currentPlaybackTime === 'number' && Number.isFinite(currentPlaybackTime) && (
                      <line
                        x1={svgGeometry.toX(winStart + currentPlaybackTime)}
                        y1={svgGeometry.padY - 4}
                        x2={svgGeometry.toX(winStart + currentPlaybackTime)}
                        y2={svgGeometry.H - svgGeometry.padY + 4}
                        stroke="var(--text-primary)"
                        strokeWidth={1.5}
                        strokeOpacity={0.7}
                      />
                    )}

                    {/* Hover crosshair */}
                    {hoveredPoint && (
                      <line
                        x1={svgGeometry.toX(hoveredPoint.t)}
                        y1={svgGeometry.padY}
                        x2={svgGeometry.toX(hoveredPoint.t)}
                        y2={svgGeometry.H - svgGeometry.padY}
                        stroke="var(--accent-primary)"
                        strokeWidth={1}
                        strokeDasharray="2 2"
                      />
                    )}

                    {/* Best-Frame Pins */}
                    {frames.map((bf) => {
                      const cx = svgGeometry.toX(bf.t);
                      const cy = svgGeometry.toY(bf.composite_score);
                      return (
                        <g
                          key={`pin-${bf.index}`}
                          className="cursor-pointer"
                          onClick={(e) => {
                            e.stopPropagation();
                            onSeekToMasterTime(bf.t);
                          }}
                        >
                          <circle
                            cx={cx}
                            cy={cy}
                            r={5}
                            fill="var(--accent-primary)"
                            stroke="var(--bg-surface)"
                            strokeWidth={2}
                          />
                          <text
                            x={cx}
                            y={Math.max(10, cy - 8)}
                            textAnchor="middle"
                            fontSize={9}
                            fontWeight="bold"
                            fill="var(--text-primary)"
                          >
                            #{bf.index + 1}
                          </text>
                        </g>
                      );
                    })}
                  </svg>

                  {/* Hover readout bar */}
                  <div className="flex flex-wrap items-center justify-between gap-2 px-2 pt-1 text-[10px] font-mono text-[var(--text-secondary)] border-t border-[var(--border-subtle)]/60">
                    <span>Start {winStart.toFixed(2)}s</span>
                    {hoveredPoint ? (
                      <span className="text-[var(--text-primary)] font-semibold">
                        t={hoveredPoint.t.toFixed(2)}s · Comb {hoveredPoint.combined_engagement_score} · Aud{' '}
                        {hoveredPoint.audio_prosody_score} · Vis {hoveredPoint.visual_emotion_action_score} · Eye{' '}
                        {Math.round(hoveredPoint.eye_contact_score ?? 0)}% · Nov{' '}
                        {Math.round(hoveredPoint.visual_novelty_score ?? 0)}% · Flu{' '}
                        {Math.round(hoveredPoint.fluency_score ?? 78)}% ·{' '}
                        {hoveredPoint.speaker_id || formatTagLabel(hoveredPoint.dominant_emotion)}
                      </span>
                    ) : (
                      <span>Hover or click timeline to inspect exact 0.25s prosody &amp; quality</span>
                    )}
                    <span>End {winEnd.toFixed(2)}s</span>
                  </div>
                </div>
              </div>

              {/* Interactive Emotion & Arousal Arc Track + Emotion Breakdown Card */}
              <div className="grid grid-cols-1 lg:grid-cols-3 gap-3.5">
                {/* Left 2 cols: Interactive Emotion & Arousal Arc Track */}
                <div className="lg:col-span-2 p-4 rounded-xl bg-[var(--bg-card)] border border-[var(--border-subtle)] space-y-2.5">
                  <div className="flex flex-wrap items-center justify-between gap-2">
                    <div className="flex items-center gap-2">
                      <Sparkles className="w-4 h-4 text-purple-400" />
                      <span className="font-bold text-xs text-[var(--text-primary)]">
                        Emotion &amp; Arousal Arc Track
                      </span>
                    </div>
                    <div className="flex flex-wrap items-center gap-2.5 text-[10px] font-semibold">
                      <span className="flex items-center gap-1 text-purple-400">
                        <span className="w-2 h-2 rounded-full bg-purple-400 inline-block" /> Arousal
                      </span>
                      <span className="flex items-center gap-1 text-amber-400">
                        <span className="w-2 h-2 rotate-45 bg-amber-400 inline-block" /> Micro-Shift
                      </span>
                      <span className="flex items-center gap-1 text-emerald-400">
                        <span className="w-2 h-2 rounded-sm bg-emerald-400 inline-block" /> Acoustic Event
                      </span>
                    </div>
                  </div>

                  {/* Color-coded Emotion Timeline Ribbon */}
                  {timeline.length > 0 && (
                    <div
                      className="h-2.5 w-full rounded-full overflow-hidden flex border border-[var(--border-subtle)] bg-[var(--bg-input)]"
                      title="Dominant emotion arc ribbon (click below track to seek)"
                    >
                      {timeline.map((pt, idx) => (
                        <button
                          key={`emo-seg-${idx}`}
                          type="button"
                          onClick={() => onSeekToMasterTime(pt.t)}
                          style={{
                            width: `${100 / Math.max(1, timeline.length)}%`,
                            backgroundColor: EMOTION_COLORS[pt.dominant_emotion || 'neutral'] || '#94a3b8',
                            opacity: 0.45 + 0.55 * Math.min(1, (pt.fused_arousal ?? 40) / 100),
                          }}
                          className="h-full transition-opacity hover:opacity-100"
                          title={`${pt.t.toFixed(2)}s · Facial: ${formatTagLabel(pt.dominant_emotion)} · Vocal: ${formatTagLabel(pt.vocal_emotion)} · Arousal ${Math.round(pt.fused_arousal ?? 0)}`}
                        />
                      ))}
                    </div>
                  )}

                  {/* Arousal SVG Track + Shift & Acoustic Event Pins */}
                  <div className="relative rounded-xl bg-[var(--bg-input)] border border-[var(--border-subtle)] p-2">
                    <svg
                      viewBox={`0 0 ${svgGeometry.W} 110`}
                      className="w-full h-24 cursor-crosshair select-none overflow-visible"
                      onClick={(e) => {
                        const rect = e.currentTarget.getBoundingClientRect();
                        const ratio = Math.max(0, Math.min(1, (e.clientX - rect.left) / Math.max(1, rect.width)));
                        onSeekToMasterTime(winStart + ratio * winSpan);
                      }}
                    >
                      {[25, 50, 75].map((pct) => {
                        const yVal = 12 + (1 - pct / 100) * 86;
                        return (
                          <line
                            key={`ar-grid-${pct}`}
                            x1={svgGeometry.padX}
                            y1={yVal}
                            x2={svgGeometry.W - svgGeometry.padX}
                            y2={yVal}
                            stroke="currentColor"
                            strokeOpacity={0.1}
                            strokeDasharray="3 4"
                          />
                        );
                      })}

                      {timeline.length > 0 && (
                        <>
                          <path
                            d={timeline
                              .map((pt, i) => {
                                const x = svgGeometry.toX(pt.t);
                                const y = 12 + (1 - Math.max(0, Math.min(100, pt.fused_arousal ?? pt.emotion_intensity ?? 35)) / 100) * 86;
                                return `${i === 0 ? 'M' : 'L'} ${x.toFixed(1)} ${y.toFixed(1)}`;
                              })
                              .join(' ')}
                            fill="none"
                            stroke="#a855f7"
                            strokeWidth={2.2}
                            strokeLinejoin="round"
                          />
                        </>
                      )}

                      {/* Micro-Expression Shift Pins */}
                      {emotionShifts.map((sh, sIdx) => {
                        const x = svgGeometry.toX(sh.t);
                        const y = 12 + (1 - Math.max(0, Math.min(100, sh.arousal || 60)) / 100) * 86;
                        return (
                          <g
                            key={`sh-pin-${sIdx}`}
                            className="cursor-pointer"
                            onClick={(e) => {
                              e.stopPropagation();
                              onSeekToMasterTime(sh.t);
                            }}
                          >
                            <circle cx={x} cy={y} r={4.5} fill="#fbbf24" stroke="var(--bg-surface)" strokeWidth={1.5} />
                            <text
                              x={x}
                              y={Math.max(9, y - 7)}
                              textAnchor="middle"
                              fontSize={8.5}
                              fontWeight="bold"
                              fill="#fbbf24"
                            >
                              {formatTagLabel(sh.to_emotion)}
                            </text>
                          </g>
                        );
                      })}

                      {/* Clickable Acoustic Event Markers */}
                      {acousticEvents.map((ev, eIdx) => {
                        const x = svgGeometry.toX(ev.t);
                        return (
                          <g
                            key={`ac-pin-${eIdx}`}
                            className="cursor-pointer"
                            onClick={(e) => {
                              e.stopPropagation();
                              onSeekToMasterTime(ev.t);
                            }}
                          >
                            <line x1={x} y1={10} x2={x} y2={98} stroke="#34d399" strokeWidth={1.5} strokeDasharray="2 2" />
                            <rect x={x - 4} y={88} width={8} height={8} rx={2} fill="#34d399" />
                          </g>
                        );
                      })}
                    </svg>

                    {/* Clickable Acoustic Event & Micro-Shift Chips */}
                    <div className="flex flex-wrap items-center gap-1.5 pt-1.5 border-t border-[var(--border-subtle)]/50">
                      {acousticEvents.length === 0 && emotionShifts.length === 0 ? (
                        <span className="text-[10px] text-[var(--text-secondary)]">
                          No discrete acoustic bursts or sharp micro-expression shifts flagged inside this clip.
                        </span>
                      ) : (
                        <>
                          {acousticEvents.map((ev, idx) => (
                            <button
                              key={`ev-chip-${idx}`}
                              type="button"
                              onClick={() => onSeekToMasterTime(ev.t)}
                              className="px-2 py-0.5 rounded-full bg-emerald-500/15 border border-emerald-500/35 text-emerald-300 text-[10px] font-bold hover:brightness-110 transition tabular-nums"
                              title={`Seek to ${ev.label} at ${ev.t.toFixed(2)}s (${Math.round(ev.confidence * 100)}% conf)`}
                            >
                              {ev.label} · {ev.t.toFixed(1)}s
                            </button>
                          ))}
                          {emotionShifts.slice(0, 6).map((sh, idx) => (
                            <button
                              key={`sh-chip-${idx}`}
                              type="button"
                              onClick={() => onSeekToMasterTime(sh.t)}
                              className="px-2 py-0.5 rounded-full bg-amber-500/15 border border-amber-500/35 text-amber-300 text-[10px] font-bold hover:brightness-110 transition tabular-nums"
                              title={`Jump to micro-expression shift: ${sh.from_emotion} -> ${sh.to_emotion}`}
                            >
                              Δ {formatTagLabel(sh.to_emotion)} · {sh.t.toFixed(1)}s
                            </button>
                          ))}
                        </>
                      )}
                    </div>
                  </div>
                </div>

                {/* Right Col: Emotion Breakdown Card */}
                <div className="p-4 rounded-xl bg-[var(--bg-card)] border border-[var(--border-subtle)] space-y-3">
                  <div className="flex items-center justify-between gap-2">
                    <span className="font-bold text-xs text-[var(--text-primary)]">Emotion Breakdown</span>
                    {(emotionBreakdown?.arc_label || clip.arc_label) && (
                      <span className="px-2 py-0.5 rounded-full bg-[var(--accent-subtle)] border border-[var(--accent-primary)]/40 text-[var(--accent-primary)] text-[10px] font-bold">
                        {emotionBreakdown?.arc_label || clip.arc_label}
                      </span>
                    )}
                  </div>

                  <div className="grid grid-cols-2 gap-2 text-[11px] tabular-nums">
                    <div className="p-2 rounded-lg bg-[var(--bg-input)] border border-[var(--border-subtle)]">
                      <div className="text-[10px] text-[var(--text-secondary)]">Facial Emotion</div>
                      <div className="font-extrabold text-[var(--text-primary)]">
                        {formatTagLabel(emotionBreakdown?.dominant_facial_emotion || clip.dominant_emotion)}
                      </div>
                    </div>
                    <div className="p-2 rounded-lg bg-[var(--bg-input)] border border-[var(--border-subtle)]">
                      <div className="text-[10px] text-[var(--text-secondary)]">Vocal Tone</div>
                      <div className="font-extrabold text-[var(--text-primary)]">
                        {formatTagLabel(emotionBreakdown?.dominant_vocal_emotion || clip.vocal_emotion)}
                      </div>
                    </div>
                    <div className="p-2 rounded-lg bg-[var(--bg-input)] border border-[var(--border-subtle)]">
                      <div className="text-[10px] text-[var(--text-secondary)]">Peak Arousal</div>
                      <div className="font-extrabold text-purple-400">
                        {Math.round(emotionBreakdown?.peak_arousal ?? clip.emotion_arousal ?? 45)}/100
                      </div>
                    </div>
                    <div className="p-2 rounded-lg bg-[var(--bg-input)] border border-[var(--border-subtle)]">
                      <div className="text-[10px] text-[var(--text-secondary)]">Emotional Valence</div>
                      <div
                        className={`font-extrabold ${(emotionBreakdown?.avg_valence ?? clip.emotion_valence ?? 0) >= 0
                            ? 'text-emerald-400'
                            : 'text-rose-400'
                          }`}
                      >
                        {(emotionBreakdown?.avg_valence ?? clip.emotion_valence ?? 0) >= 0 ? '+' : ''}
                        {(emotionBreakdown?.avg_valence ?? clip.emotion_valence ?? 0).toFixed(2)}
                      </div>
                    </div>
                  </div>

                  {/* Facial Distribution Bars */}
                  {emotionBreakdown?.facial_distribution && (
                    <div className="space-y-1.5 pt-1">
                      <div className="text-[10px] font-bold uppercase tracking-wider text-[var(--text-secondary)]">
                        Expression Mix
                      </div>
                      {Object.entries(emotionBreakdown.facial_distribution)
                        .slice(0, 4)
                        .map(([emoName, share]) => (
                          <div key={emoName} className="space-y-0.5 text-[10px] tabular-nums">
                            <div className="flex items-center justify-between text-[var(--text-secondary)]">
                              <span className="font-semibold text-[var(--text-primary)]">
                                {formatTagLabel(emoName)}
                              </span>
                              <span>{Math.round(Number(share) * 100)}%</span>
                            </div>
                            <div className="h-1.5 rounded-full bg-[var(--bg-input)] overflow-hidden">
                              <div
                                className="h-full rounded-full"
                                style={{
                                  width: `${Math.max(4, Math.min(100, Math.round(Number(share) * 100)))}%`,
                                  backgroundColor: EMOTION_COLORS[emoName] || 'var(--accent-primary)',
                                }}
                              />
                            </div>
                          </div>
                        ))}
                    </div>
                  )}
                </div>
              </div>

              {/* Best Frames Interactive Gallery (Top 6 Keyframes) */}
              <div className="space-y-3">
                <div className="flex items-center justify-between gap-2">
                  <h3 className="font-bold text-xs uppercase tracking-wider text-[var(--text-primary)] flex items-center gap-1.5">
                    <ImageIcon className="w-3.5 h-3.5 text-[var(--accent-primary)]" />
                    <span>Best Frames Interactive Gallery (Top {frames.length})</span>
                  </h3>
                  <span className="text-[11px] text-[var(--text-secondary)]">
                    Blurry, crushed-dark &amp; mid-blink frames automatically excluded
                  </span>
                </div>

                {frames.length === 0 ? (
                  <div className="p-6 rounded-xl bg-[var(--bg-card)] border border-[var(--border-subtle)] text-center text-xs text-[var(--text-secondary)]">
                    No clean frames passed quality filters yet. Try clicking <strong>Re-Analyze Audio &amp; Video</strong>.
                  </div>
                ) : (
                  <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-3.5">
                    {frames.map((frame) => {
                      const meta = ROLE_META[frame.role] || ROLE_META.highlight_moment;
                      const snapKey = `snap-${frame.index}`;
                      const calKey = `cal-${frame.index}`;
                      return (
                        <div
                          key={frame.index}
                          className="rounded-xl bg-[var(--bg-card)] border border-[var(--border-subtle)] overflow-hidden flex flex-col justify-between shadow-sm hover:border-[var(--accent-primary)]/45 transition"
                        >
                          <div>
                            <div className="relative aspect-video bg-black overflow-hidden">
                              {frame.image_url ? (
                                <img
                                  src={frame.image_url}
                                  alt={`${meta.label} frame at ${frame.t.toFixed(2)}s`}
                                  className="w-full h-full object-cover"
                                />
                              ) : (
                                <div className="w-full h-full flex items-center justify-center text-xs text-[var(--text-secondary)]">
                                  Unavailable
                                </div>
                              )}
                              <div className="absolute top-2 left-2 flex items-center gap-1.5">
                                <span
                                  className={`px-2 py-0.5 rounded-full text-[10px] font-bold border backdrop-blur-sm ${meta.badgeClass}`}
                                >
                                  {meta.label}
                                </span>
                              </div>
                              <div className="absolute top-2 right-2 px-1.5 py-0.5 rounded bg-black/75 text-white text-[10px] font-mono tabular-nums">
                                {frame.t.toFixed(2)}s
                              </div>
                              <div className="absolute bottom-2 right-2 px-1.5 py-0.5 rounded bg-black/75 text-emerald-300 text-[10px] font-bold tabular-nums">
                                Score {frame.composite_score.toFixed(0)}
                              </div>
                            </div>

                            <div className="p-3 pb-2 flex items-center justify-between gap-2 text-[11px] font-mono text-[var(--text-secondary)] tabular-nums">
                              <span>Sharp {Math.round(frame.sharpness_score)}%</span>
                              <span>Emo {Math.round(frame.emotion_intensity)}%</span>
                              <span>Aud {Math.round(frame.audio_excitement)}%</span>
                            </div>
                          </div>

                          <div className="p-3 pt-1.5 grid grid-cols-2 gap-1.5 text-[11px]">
                            <button
                              type="button"
                              onClick={() => onSeekToMasterTime(frame.t)}
                              className="px-2 py-1.5 rounded-lg bg-[var(--bg-input)] border border-[var(--border-subtle)] hover:border-[var(--accent-primary)]/50 text-[var(--text-primary)] font-semibold flex items-center justify-center gap-1 transition"
                            >
                              <Play className="w-3 h-3 text-[var(--accent-primary)]" />
                              <span>Jump to Frame</span>
                            </button>

                            <button
                              type="button"
                              onClick={() => onSetThumbnailCover(frame)}
                              className="px-2 py-1.5 rounded-lg bg-[var(--bg-input)] border border-[var(--border-subtle)] hover:border-[var(--accent-primary)]/50 text-[var(--text-primary)] font-semibold flex items-center justify-center gap-1 transition"
                            >
                              <ImageIcon className="w-3 h-3 text-emerald-400" />
                              <span>Set as Thumbnail Cover</span>
                            </button>

                            <button
                              type="button"
                              disabled={busyAction !== null}
                              onClick={async () => {
                                setBusyAction(snapKey);
                                try {
                                  await onSnapClipStart(frame);
                                  setStatusNote(`Snapped clip start to clean frame at ${frame.t.toFixed(2)}s.`);
                                } finally {
                                  setBusyAction(null);
                                }
                              }}
                              className="px-2 py-1.5 rounded-lg bg-[var(--bg-input)] border border-[var(--border-subtle)] hover:border-[var(--accent-primary)]/50 text-[var(--text-primary)] font-semibold flex items-center justify-center gap-1 transition disabled:opacity-50"
                            >
                              {busyAction === snapKey ? (
                                <Loader2 className="w-3 h-3 animate-spin" />
                              ) : (
                                <Scissors className="w-3 h-3 text-amber-400" />
                              )}
                              <span>Snap Clip Start Here</span>
                            </button>

                            <button
                              type="button"
                              disabled={busyAction !== null}
                              onClick={async () => {
                                setBusyAction(calKey);
                                try {
                                  await onCalibrateCamera(frame);
                                  setStatusNote(`Calibrated camera framing on ${meta.label} (${frame.t.toFixed(2)}s).`);
                                } finally {
                                  setBusyAction(null);
                                }
                              }}
                              className="px-2 py-1.5 rounded-lg bg-[var(--bg-input)] border border-[var(--border-subtle)] hover:border-[var(--accent-primary)]/50 text-[var(--text-primary)] font-semibold flex items-center justify-center gap-1 transition disabled:opacity-50"
                            >
                              {busyAction === calKey ? (
                                <Loader2 className="w-3 h-3 animate-spin" />
                              ) : (
                                <Crosshair className="w-3 h-3 text-sky-400" />
                              )}
                              <span>Calibrate Camera Here</span>
                            </button>
                          </div>
                        </div>
                      );
                    })}
                  </div>
                )}
              </div>
            </>
          )}
        </div>
      </div>
    </div>
  );
};
