import React, { useEffect, useMemo, useRef, useState } from 'react';
import {
  Activity,
  AlertCircle,
  ArrowLeft,
  ArrowRight,
  Check,
  CheckCircle2,
  Clock,
  Copy,
  Cpu,
  Eye,
  FileVideo,
  Film,
  Filter,
  FolderOpen,
  History,
  KeyRound,
  Layers,
  Loader2,
  Play,
  RefreshCw,
  RotateCcw,
  Sparkles,
  Square,
  Terminal,
  WifiOff,
  XCircle,
} from 'lucide-react';
import { api } from '../api/client';
import { JobProgress, JobsListResponse, PipelineLog, PipelineStage } from '../types';

interface ProgressViewProps {
  projectId?: number | null;
  onOpenEditor: (projectId: number) => void;
  onBackToHome: () => void;
}

type StageFilterKey = 'ALL' | 'ingest' | 'audio' | 'transcribe' | 'gemini' | 'ready';

function formatClock(sec: number): string {
  const clean = Math.max(0, Math.floor(sec || 0));
  const h = Math.floor(clean / 3600);
  const m = Math.floor((clean % 3600) / 60);
  const s = clean % 60;
  if (h > 0) return `${h}:${m.toString().padStart(2, '0')}:${s.toString().padStart(2, '0')}`;
  return `${m.toString().padStart(2, '0')}:${s.toString().padStart(2, '0')}`;
}

function formatStepDuration(sec: number | null | undefined): string {
  if (sec === null || sec === undefined || Number.isNaN(sec)) return '';
  if (sec < 0.1) return '< 0.1s';
  if (sec < 60) return `${sec.toFixed(1)}s`;
  const m = Math.floor(sec / 60);
  const s = Math.round(sec % 60);
  return `${m}m ${s}s`;
}

function formatTimestampShort(iso: string): string {
  try {
    const d = new Date(iso);
    if (Number.isNaN(d.getTime())) return iso.slice(11, 19) || '';
    return d.toLocaleTimeString([], { hour12: false, hour: '2-digit', minute: '2-digit', second: '2-digit' });
  } catch {
    return '';
  }
}

function logMatchesStageFilter(log: PipelineLog, filterKey: StageFilterKey): boolean {
  if (filterKey === 'ALL') return true;
  const tag = (log.stage || '').trim().toUpperCase();
  if (filterKey === 'ingest') return ['INGEST', 'MEDIA', 'INIT', 'SYSTEM'].includes(tag);
  if (filterKey === 'audio') return tag === 'AUDIO';
  if (filterKey === 'transcribe') return tag === 'TRANSCRIBE';
  if (filterKey === 'gemini') return ['GEMINI', 'OFFLINE AI', 'OLLAMA', 'ANALYSIS'].includes(tag);
  if (filterKey === 'ready') return ['READY', 'RENDER'].includes(tag);
  return true;
}

export const ProgressView: React.FC<ProgressViewProps> = ({
  projectId = null,
  onOpenEditor,
  onBackToHome,
}) => {
  const [selectedJobId, setSelectedJobId] = useState<number | null>(projectId ?? null);
  const [inspectingHistorical, setInspectingHistorical] = useState(false);

  const [jobsSummary, setJobsSummary] = useState<JobsListResponse | null>(null);
  const [loadingJobs, setLoadingJobs] = useState(true);

  const [job, setJob] = useState<JobProgress | null>(null);
  const [liveElapsedSec, setLiveElapsedSec] = useState<number>(0);

  // Terminal controls (open by default!)
  const [stageFilter, setStageFilter] = useState<StageFilterKey>('ALL');
  const [autoScroll, setAutoScroll] = useState(true);
  const [copiedLogs, setCopiedLogs] = useState(false);
  const logViewportRef = useRef<HTMLDivElement>(null);

  // Action states
  const [cancelling, setCancelling] = useState(false);
  const [retrying, setRetrying] = useState(false);
  const [inlineKey, setInlineKey] = useState('');
  const [keyError, setKeyError] = useState<string | null>(null);
  const [submittingKey, setSubmittingKey] = useState(false);

  // Track if current selectedJobId was observed running live so we only auto-transition on fresh completion
  const wasObservedRunningRef = useRef<boolean>(false);
  const autoTransitionTriggeredRef = useRef<boolean>(false);

  // Sync incoming prop changes cleanly
  useEffect(() => {
    setSelectedJobId(projectId ?? null);
    setInspectingHistorical(false);
    wasObservedRunningRef.current = false;
    autoTransitionTriggeredRef.current = false;
    setStageFilter('ALL');
  }, [projectId]);

  // Poll GET /api/jobs to keep Command Center & Auto-Binding fresh
  const fetchJobsSummary = async () => {
    try {
      const data = await api.getJobs(20);
      setJobsSummary(data);
      // Auto-bind to running job whenever user opens Pipeline Progress without an explicit projectId
      if (selectedJobId === null && !inspectingHistorical && data.active_jobs?.length > 0) {
        setSelectedJobId(data.active_jobs[0].project_id);
        wasObservedRunningRef.current = true;
      }
    } catch (err) {
      console.error('Failed to fetch jobs list:', err);
    } finally {
      setLoadingJobs(false);
    }
  };

  useEffect(() => {
    fetchJobsSummary();
    const timer = window.setInterval(fetchJobsSummary, 3000);
    return () => window.clearInterval(timer);
  }, [selectedJobId, inspectingHistorical]);

  // Stream & poll active job progress when a job ID is selected
  useEffect(() => {
    if (selectedJobId === null) {
      setJob(null);
      return;
    }

    let cancelled = false;
    let pollTimer: number | null = null;
    let redirectTimer: number | null = null;

    const handleJobPayload = (payload: JobProgress) => {
      if (cancelled || !payload || payload.project_id !== selectedJobId) return;
      setJob(payload);
      if (typeof payload.elapsed_seconds === 'number') {
        setLiveElapsedSec(payload.elapsed_seconds);
      } else if (typeof payload.elapsed_sec === 'number') {
        setLiveElapsedSec(payload.elapsed_sec);
      }

      const st = payload.status;
      if (st === 'processing' || st === 'created' || st === 'awaiting_api_key' || st === 'queued') {
        wasObservedRunningRef.current = true;
      }

      if (
        (st === 'finished' || st === 'analyzed') &&
        wasObservedRunningRef.current &&
        !autoTransitionTriggeredRef.current
      ) {
        autoTransitionTriggeredRef.current = true;
        redirectTimer = window.setTimeout(() => {
          if (!cancelled) {
            onOpenEditor(selectedJobId);
          }
        }, 1300);
      }
    };

    const pullProgress = async () => {
      try {
        const res = await api.getProjectProgress(selectedJobId);
        handleJobPayload(res);
      } catch (err) {
        console.error('Failed to poll job progress:', err);
      }
    };

    pullProgress();

    const unsubscribe = api.subscribeToEvents((ev) => {
      if (ev?.type === 'progress' && ev.data?.project_id === selectedJobId) {
        handleJobPayload(ev.data as JobProgress);
      }
    });

    pollTimer = window.setInterval(pullProgress, 2000);

    return () => {
      cancelled = true;
      unsubscribe();
      if (pollTimer) window.clearInterval(pollTimer);
      if (redirectTimer) window.clearTimeout(redirectTimer);
    };
  }, [selectedJobId]);

  const isRunningStatus = Boolean(
    job && ['processing', 'created', 'proxy_ready', 'analyzing'].includes(job.status)
  );
  const isFinishedStatus = Boolean(job && (job.status === 'finished' || job.status === 'analyzed'));
  const isAwaitingKey = Boolean(job && job.status === 'awaiting_api_key');
  const isFailedOrCancelled = Boolean(
    job && (job.status === 'error' || job.status === 'failed' || job.status === 'cancelled')
  );

  // Smooth monotonic 1s client ticker anchored to server elapsed_seconds only while running
  useEffect(() => {
    if (!isRunningStatus) return;
    const tick = window.setInterval(() => {
      setLiveElapsedSec((prev) => prev + 1);
    }, 1000);
    return () => window.clearInterval(tick);
  }, [isRunningStatus]);

  // Auto-scroll terminal console when enabled
  const filteredLogs = useMemo(() => {
    const allLogs = job?.logs || [];
    return allLogs.filter((l) => logMatchesStageFilter(l, stageFilter));
  }, [job?.logs, stageFilter]);

  useEffect(() => {
    if (autoScroll && logViewportRef.current) {
      logViewportRef.current.scrollTop = logViewportRef.current.scrollHeight;
    }
  }, [filteredLogs.length, autoScroll]);

  const handleCancelJob = async () => {
    if (!selectedJobId || cancelling) return;
    setCancelling(true);
    try {
      await api.cancelProject(selectedJobId);
      const updated = await api.getProjectProgress(selectedJobId);
      setJob(updated);
      fetchJobsSummary();
    } catch (err) {
      console.error('Failed to cancel job:', err);
    } finally {
      setCancelling(false);
    }
  };

  const handleRetryJob = async (targetId: number, forceOffline: boolean = false) => {
    if (retrying) return;
    setRetrying(true);
    setKeyError(null);
    wasObservedRunningRef.current = true;
    autoTransitionTriggeredRef.current = false;
    try {
      await api.retryProject(targetId, forceOffline);
      setSelectedJobId(targetId);
      setInspectingHistorical(false);
      const updated = await api.getProjectProgress(targetId);
      setJob(updated);
      fetchJobsSummary();
    } catch (err: any) {
      setKeyError(err?.message || 'Retry request failed');
    } finally {
      setRetrying(false);
    }
  };

  const handleSubmitResumeKey = async () => {
    if (!selectedJobId || !inlineKey.trim() || submittingKey) return;
    setSubmittingKey(true);
    setKeyError(null);
    wasObservedRunningRef.current = true;
    autoTransitionTriggeredRef.current = false;
    try {
      await api.provideApiKey(selectedJobId, inlineKey.trim());
      setInlineKey('');
      window.dispatchEvent(new CustomEvent('lh-settings-updated'));
      const updated = await api.getProjectProgress(selectedJobId);
      setJob(updated);
    } catch (err: any) {
      setKeyError(err?.message || 'Gemini API key verification failed');
    } finally {
      setSubmittingKey(false);
    }
  };

  const handleCopyLogs = async () => {
    const text = filteredLogs
      .map((l) => `[${formatTimestampShort(l.timestamp)}] [${l.stage}] [${l.level}] ${l.message}`)
      .join('\n');
    try {
      await navigator.clipboard.writeText(text || 'No logs available.');
      setCopiedLogs(true);
      window.setTimeout(() => setCopiedLogs(false), 1800);
    } catch {
      setCopiedLogs(false);
    }
  };

  // =========================================================================
  // IDLE / NO ACTIVE JOB -> PIPELINE COMMAND CENTER
  // =========================================================================
  if (selectedJobId === null || (!job && !loadingJobs && !jobsSummary?.active_jobs?.length)) {
    const queuedJobs = jobsSummary?.queued_jobs || [];
    const recentJobs = jobsSummary?.recent_jobs || [];

    return (
      <div className="h-full overflow-y-auto px-5 py-6 lg:px-8">
        <div className="max-w-6xl mx-auto space-y-6">
          {/* Command Center Hero */}
          <div className="rounded-2xl bg-[var(--bg-surface)] border border-[var(--border-subtle)] p-6 flex flex-wrap items-center justify-between gap-4 shadow-sm">
            <div className="space-y-1">
              <div className="flex items-center gap-2">
                <span className="inline-flex items-center gap-1.5 px-2.5 py-0.5 rounded-full text-[11px] font-bold uppercase tracking-wider bg-[var(--bg-card)] border border-[var(--border-subtle)] text-[var(--text-secondary)]">
                  <Activity className="w-3 h-3 text-[var(--accent-primary)]" />
                  Pipeline Command Center
                </span>
                <span className="text-xs font-semibold text-emerald-400 flex items-center gap-1">
                  <span className="w-2 h-2 rounded-full bg-emerald-400" />
                  No Active Jobs Running
                </span>
              </div>
              <h1 className="text-2xl font-extrabold text-[var(--text-primary)] tracking-tight">
                Pipeline Telemetry &amp; Execution History
              </h1>
              <p className="text-xs text-[var(--text-muted)]">
                Launch a new ingestion job or inspect logs, recover checkpoints, and open completed clips from recent runs.
              </p>
            </div>

            <button
              type="button"
              onClick={onBackToHome}
              className="px-5 py-3 rounded-xl bg-[var(--accent-primary)] text-[var(--accent-foreground)] font-extrabold text-xs tracking-wide flex items-center gap-2 hover:brightness-110 transition-all shadow-md"
            >
              <Play className="w-4 h-4 fill-current" />
              Start New Ingestion
            </button>
          </div>

          {/* Queued Multi-Video Batch Jobs */}
          {queuedJobs.length > 0 && (
            <section className="space-y-2.5">
              <h2 className="text-xs font-bold uppercase tracking-wider text-[var(--text-primary)] flex items-center gap-2">
                <Layers className="w-4 h-4 text-[var(--accent-primary)]" />
                Queued Batch Jobs ({queuedJobs.length})
              </h2>
              <div className="grid grid-cols-1 md:grid-cols-2 gap-3">
                {queuedJobs.map((qj) => (
                  <div
                    key={qj.project_id}
                    className="rounded-xl bg-[var(--bg-surface)] border border-[var(--border-subtle)] p-4 flex items-center justify-between gap-3"
                  >
                    <div className="min-w-0">
                      <h3 className="text-sm font-bold text-[var(--text-primary)] truncate">{qj.title}</h3>
                      <p className="text-xs text-[var(--text-muted)] truncate">
                        {qj.filename || 'Queued upload'} · Waiting for active worker slot
                      </p>
                    </div>
                    <button
                      type="button"
                      onClick={() => {
                        setInspectingHistorical(true);
                        setSelectedJobId(qj.project_id);
                      }}
                      className="px-3 py-1.5 rounded-lg bg-[var(--bg-card)] border border-[var(--border-subtle)] text-xs font-bold text-[var(--text-primary)] hover:bg-[var(--bg-hover)] shrink-0"
                    >
                      Inspect
                    </button>
                  </div>
                ))}
              </div>
            </section>
          )}

          {/* Recent Runs History */}
          <section className="space-y-3">
            <div className="flex items-center justify-between">
              <h2 className="text-xs font-bold uppercase tracking-wider text-[var(--text-primary)] flex items-center gap-2">
                <History className="w-4 h-4 text-[var(--accent-primary)]" />
                Recent Runs History ({recentJobs.length})
              </h2>
              <button
                type="button"
                onClick={fetchJobsSummary}
                className="inline-flex items-center gap-1 text-xs font-semibold text-[var(--text-muted)] hover:text-[var(--text-primary)]"
              >
                <RefreshCw className="w-3.5 h-3.5" />
                Refresh
              </button>
            </div>

            {loadingJobs ? (
              <div className="space-y-2.5">
                {[1, 2, 3].map((i) => (
                  <div key={i} className="h-20 rounded-xl bg-[var(--bg-surface)] border border-[var(--border-subtle)] animate-pulse" />
                ))}
              </div>
            ) : recentJobs.length === 0 ? (
              <div className="rounded-2xl bg-[var(--bg-surface)] border border-[var(--border-subtle)] p-10 text-center space-y-3">
                <FolderOpen className="w-9 h-9 mx-auto text-[var(--text-muted)] opacity-60" />
                <div className="space-y-1">
                  <p className="text-sm font-bold text-[var(--text-primary)]">No pipeline runs recorded yet</p>
                  <p className="text-xs text-[var(--text-muted)]">
                    Import a local video or YouTube URL from Home Ingestion to track live Whisper + Vision + AI extraction.
                  </p>
                </div>
                <button
                  type="button"
                  onClick={onBackToHome}
                  className="inline-flex items-center gap-2 px-4 py-2 rounded-xl bg-[var(--accent-primary)] text-[var(--accent-foreground)] text-xs font-bold"
                >
                  Start New Ingestion
                  <ArrowRight className="w-3.5 h-3.5" />
                </button>
              </div>
            ) : (
              <div className="space-y-2.5">
                {recentJobs.map((rj) => {
                  const isOk = rj.status === 'finished' || rj.status === 'analyzed';
                  const isErr = rj.status === 'error' || rj.status === 'failed' || rj.status === 'cancelled';
                  const isOff = rj.ai_mode === 'offline';

                  return (
                    <div
                      key={rj.project_id}
                      className="rounded-xl bg-[var(--bg-surface)] border border-[var(--border-subtle)] p-4 flex flex-wrap items-center justify-between gap-4 hover:border-[var(--accent-primary)]/40 transition-colors"
                    >
                      <div className="flex items-center gap-3.5 min-w-0 flex-1">
                        <div
                          className={`w-10 h-10 rounded-xl flex items-center justify-center shrink-0 border ${
                            isOk
                              ? 'bg-emerald-500/10 border-emerald-500/30 text-emerald-400'
                              : isErr
                                ? 'bg-rose-500/10 border-rose-500/30 text-rose-400'
                                : 'bg-[var(--bg-card)] border-[var(--border-subtle)] text-[var(--text-muted)]'
                          }`}
                        >
                          {isOk ? (
                            <CheckCircle2 className="w-5 h-5" />
                          ) : isErr ? (
                            <XCircle className="w-5 h-5" />
                          ) : (
                            <FileVideo className="w-5 h-5" />
                          )}
                        </div>

                        <div className="min-w-0 flex-1">
                          <div className="flex flex-wrap items-center gap-2">
                            <h3 className="text-sm font-bold text-[var(--text-primary)] truncate max-w-md">
                              {rj.title}
                            </h3>
                            <span
                              className={`inline-flex items-center gap-1 px-2 py-0.5 rounded-full text-[10px] font-bold border ${
                                isOff
                                  ? 'bg-emerald-500/10 text-emerald-400 border-emerald-500/30'
                                  : 'bg-indigo-500/10 text-indigo-300 border-indigo-500/30'
                              }`}
                            >
                              {isOff ? <WifiOff className="w-2.5 h-2.5" /> : <Sparkles className="w-2.5 h-2.5" />}
                              {isOff ? 'Offline AI' : 'Gemini'}
                            </span>
                            <span
                              className={`px-2 py-0.5 rounded-full text-[10px] font-bold uppercase ${
                                isOk
                                  ? 'bg-emerald-500/15 text-emerald-400'
                                  : isErr
                                    ? 'bg-rose-500/15 text-rose-400'
                                    : 'bg-[var(--bg-card)] text-[var(--text-muted)]'
                              }`}
                            >
                              {isOk ? 'Completed' : rj.status}
                            </span>
                          </div>
                          <div className="flex flex-wrap items-center gap-3 text-[11px] text-[var(--text-muted)] mt-1">
                            <span className="truncate max-w-[200px]">{rj.filename || rj.source_filename || 'Source video'}</span>
                            {rj.file_size_formatted && <span>· {rj.file_size_formatted}</span>}
                            {(rj.duration_sec ?? 0) > 0 && <span>· {formatClock(rj.duration_sec || 0)} video</span>}
                            <span>· {rj.clips_count ?? 0} clips</span>
                            {(rj.elapsed_sec ?? rj.elapsed_seconds ?? 0) > 0 && (
                              <span>· Ran {formatClock(rj.elapsed_sec ?? rj.elapsed_seconds ?? 0)}</span>
                            )}
                          </div>
                        </div>
                      </div>

                      <div className="flex items-center gap-2 shrink-0">
                        <button
                          type="button"
                          onClick={() => {
                            setInspectingHistorical(true);
                            setSelectedJobId(rj.project_id);
                          }}
                          className="inline-flex items-center gap-1.5 px-3 py-1.5 rounded-lg bg-[var(--bg-card)] hover:bg-[var(--bg-hover)] border border-[var(--border-subtle)] text-xs font-bold text-[var(--text-secondary)] hover:text-[var(--text-primary)] transition-colors"
                        >
                          <Eye className="w-3.5 h-3.5" />
                          View Logs
                        </button>

                        {isErr && (
                          <button
                            type="button"
                            disabled={retrying}
                            onClick={() => handleRetryJob(rj.project_id, false)}
                            className="inline-flex items-center gap-1.5 px-3 py-1.5 rounded-lg bg-amber-500/15 hover:bg-amber-500/25 border border-amber-500/35 text-xs font-bold text-amber-300 transition-colors"
                          >
                            <RotateCcw className="w-3.5 h-3.5" />
                            Retry Failed Job
                          </button>
                        )}

                        {(isOk || (rj.clips_count ?? 0) > 0) && (
                          <button
                            type="button"
                            onClick={() => onOpenEditor(rj.project_id)}
                            className="inline-flex items-center gap-1.5 px-3.5 py-1.5 rounded-lg bg-[var(--accent-primary)] text-[var(--accent-foreground)] text-xs font-bold hover:brightness-110 transition-all"
                          >
                            Open Clips
                            <ArrowRight className="w-3.5 h-3.5" />
                          </button>
                        )}
                      </div>
                    </div>
                  );
                })}
              </div>
            )}
          </section>
        </div>
      </div>
    );
  }

  // =========================================================================
  // ACTIVE JOB MISSION CONTROL (Single Cancel Button, 4 Telemetry Counters,
  // Interactive 5-Stage Stepper + Step Durations, 240px Console Open by Default)
  // =========================================================================
  const progressPct = job?.progress_pct ?? job?.percent ?? 0;
  const hwLabel = job?.hardware_label || job?.hardware_badge || 'CPU Multi-Core';
  const isOfflineEngine = job?.ai_mode === 'offline' || job?.ai_provider === 'offline';
  const engineLabel = isOfflineEngine ? 'Offline Multimodal AI' : 'Gemini Multimodal AI';
  const metrics = job?.live_metrics || {
    words_transcribed: 0,
    keyframes_scanned: 0,
    keyframes_total: 0,
    highlights_detected: job?.clips_count || 0,
  };
  const stages: PipelineStage[] = job?.stages || [];

  return (
    <div className="h-full overflow-y-auto px-5 py-5 lg:px-8">
      <div className="max-w-6xl mx-auto space-y-5">
        {/* Top Breadcrumb Navigation */}
        <div className="flex items-center justify-between gap-3">
          <button
            type="button"
            onClick={() => {
              setInspectingHistorical(true);
              setSelectedJobId(null);
            }}
            className="inline-flex items-center gap-1.5 text-xs font-bold text-[var(--text-secondary)] hover:text-[var(--accent-primary)] transition-colors"
          >
            <ArrowLeft className="w-4 h-4" />
            All Pipeline Runs
          </button>

          <button
            type="button"
            onClick={onBackToHome}
            className="inline-flex items-center gap-1.5 px-3 py-1.5 rounded-lg bg-[var(--bg-surface)] hover:bg-[var(--bg-hover)] border border-[var(--border-subtle)] text-xs font-bold text-[var(--text-secondary)] transition-colors"
          >
            + New Ingestion
          </button>
        </div>

        {/* 1. RICH JOB HEADER CARD (Single Cancel Button in top-right) */}
        <section className="rounded-2xl bg-[var(--bg-surface)] border border-[var(--border-subtle)] p-5 space-y-4 shadow-sm">
          <div className="flex flex-wrap items-start justify-between gap-4">
            <div className="flex items-start gap-4 min-w-0 flex-1">
              <div className="w-16 h-12 rounded-xl bg-[var(--bg-card)] border border-[var(--border-subtle)] flex items-center justify-center shrink-0">
                {isRunningStatus ? (
                  <Loader2 className="w-6 h-6 animate-spin text-[var(--accent-primary)]" />
                ) : isFinishedStatus ? (
                  <CheckCircle2 className="w-6 h-6 text-emerald-400" />
                ) : isFailedOrCancelled ? (
                  <AlertCircle className="w-6 h-6 text-rose-400" />
                ) : (
                  <Film className="w-6 h-6 text-[var(--accent-primary)]" />
                )}
              </div>

              <div className="min-w-0 flex-1 space-y-1.5">
                <div className="flex flex-wrap items-center gap-2">
                  <h1 className="text-lg lg:text-xl font-extrabold text-[var(--text-primary)] truncate">
                    {job?.title || `Processing Project #${selectedJobId}`}
                  </h1>
                </div>

                {/* File metadata + ONE Hardware badge + ONE Engine badge */}
                <div className="flex flex-wrap items-center gap-2 text-xs text-[var(--text-muted)]">
                  <span className="truncate max-w-xs font-medium text-[var(--text-secondary)]">
                    {job?.filename || job?.source_filename || 'Source video'}
                  </span>
                  {job?.file_size_formatted && <span>· {job.file_size_formatted}</span>}
                  {(job?.duration_sec ?? 0) > 0 && <span>· {formatClock(job?.duration_sec || 0)}</span>}

                  <span className="inline-flex items-center gap-1 px-2.5 py-0.5 rounded-full text-[11px] font-bold bg-[var(--bg-card)] border border-[var(--border-subtle)] text-[var(--text-primary)]">
                    <Cpu className="w-3 h-3 text-[var(--accent-primary)]" />
                    {hwLabel}
                  </span>

                  <span
                    className={`inline-flex items-center gap-1 px-2.5 py-0.5 rounded-full text-[11px] font-bold border ${
                      isOfflineEngine
                        ? 'bg-emerald-500/15 text-emerald-400 border-emerald-500/30'
                        : 'bg-indigo-500/15 text-indigo-300 border-indigo-500/30'
                    }`}
                  >
                    {isOfflineEngine ? <WifiOff className="w-3 h-3" /> : <Sparkles className="w-3 h-3" />}
                    {engineLabel}
                  </span>
                </div>
              </div>
            </div>

            {/* Right-aligned Server-Synced Elapsed Timer + ONLY Cancel Job Button */}
            <div className="flex items-center gap-3 shrink-0">
              <div className="px-3.5 py-2 rounded-xl bg-[var(--bg-card)] border border-[var(--border-subtle)] text-right">
                <div className="text-[10px] font-bold uppercase tracking-wider text-[var(--text-muted)] flex items-center justify-end gap-1">
                  <Clock className="w-3 h-3" />
                  Elapsed
                </div>
                <div className="text-sm font-mono font-extrabold text-[var(--text-primary)]">
                  {formatClock(liveElapsedSec)}
                </div>
              </div>

              {(isRunningStatus || isAwaitingKey) && (
                <button
                  type="button"
                  disabled={cancelling}
                  onClick={handleCancelJob}
                  className="inline-flex items-center gap-1.5 px-3.5 py-2.5 rounded-xl bg-rose-500/15 hover:bg-rose-500/25 border border-rose-500/40 text-xs font-bold text-rose-300 transition-colors disabled:opacity-50"
                >
                  <Square className="w-3.5 h-3.5 fill-current" />
                  {cancelling ? 'Stopping...' : 'Cancel Job'}
                </button>
              )}
            </div>
          </div>

          {/* Primary Progress Bar */}
          <div className="space-y-1.5">
            <div className="flex items-center justify-between text-xs">
              <span className="font-bold text-[var(--text-primary)]">
                {job?.stage_label || 'Initializing pipeline...'}
              </span>
              <span className="font-mono font-extrabold text-[var(--accent-primary)]">{progressPct}%</span>
            </div>
            <div className="h-2.5 w-full rounded-full bg-[var(--bg-input)] border border-[var(--border-subtle)] overflow-hidden">
              <div
                className={`h-full rounded-full transition-all duration-500 ${
                  isFailedOrCancelled
                    ? 'bg-rose-500'
                    : isFinishedStatus
                      ? 'bg-emerald-500'
                      : 'bg-[var(--accent-primary)]'
                }`}
                style={{ width: `${Math.max(2, Math.min(100, progressPct))}%` }}
              />
            </div>
          </div>
        </section>

        {/* Completion Auto-Transition Banner */}
        {isFinishedStatus && (
          <div className="rounded-2xl bg-emerald-500/15 border border-emerald-500/40 p-4 flex flex-wrap items-center justify-between gap-3">
            <div className="flex items-center gap-2.5 text-sm font-extrabold text-emerald-300">
              <CheckCircle2 className="w-5 h-5 text-emerald-400 shrink-0" />
              <span>
                {wasObservedRunningRef.current
                  ? '✓ Ready — Opening Studio Editor...'
                  : `✓ Pipeline Completed — ${job?.clips_count ?? metrics.highlights_detected} viral clips ready in Studio Editor`}
              </span>
            </div>
            <button
              type="button"
              onClick={() => onOpenEditor(selectedJobId)}
              className="px-4 py-2 rounded-xl bg-emerald-500 text-slate-950 font-extrabold text-xs hover:brightness-110 flex items-center gap-1.5"
            >
              Open Studio Editor
              <ArrowRight className="w-4 h-4" />
            </button>
          </div>
        )}

        {/* API Key Pause Recovery Card */}
        {isAwaitingKey && (
          <div className="rounded-2xl bg-amber-500/10 border border-amber-500/40 p-4 space-y-3">
            <div className="flex items-start gap-2.5">
              <KeyRound className="w-5 h-5 text-amber-400 shrink-0 mt-0.5" />
              <div className="space-y-1">
                <h2 className="text-sm font-extrabold text-amber-300">
                  Gemini API Key Required to Continue Stage 4
                </h2>
                <p className="text-xs text-[var(--text-secondary)]">
                  {job?.api_key_message ||
                    'Speech transcription & checkpoints are already saved. Paste a valid Gemini API key to continue with Cloud AI, or complete locally in Offline Multimodal AI Mode.'}
                </p>
              </div>
            </div>
            <div className="flex flex-wrap items-center gap-2">
              <input
                type="password"
                value={inlineKey}
                onChange={(e) => setInlineKey(e.target.value)}
                placeholder="Paste Google Gemini API key (AIza...)"
                className="flex-1 min-w-[240px] px-3 py-2 rounded-xl bg-[var(--bg-input)] border border-[var(--border-subtle)] text-xs text-[var(--text-primary)]"
              />
              <button
                type="button"
                disabled={submittingKey || !inlineKey.trim()}
                onClick={handleSubmitResumeKey}
                className="px-4 py-2 rounded-xl bg-[var(--accent-primary)] text-[var(--accent-foreground)] text-xs font-bold disabled:opacity-50"
              >
                {submittingKey ? 'Verifying...' : 'Save Key & Resume'}
              </button>
              <button
                type="button"
                disabled={retrying}
                onClick={() => handleRetryJob(selectedJobId, true)}
                className="px-4 py-2 rounded-xl bg-[var(--bg-card)] hover:bg-[var(--bg-hover)] border border-[var(--border-subtle)] text-xs font-bold text-emerald-400"
              >
                Complete in Offline Mode
              </button>
            </div>
            {keyError && <p className="text-xs text-rose-400">{keyError}</p>}
          </div>
        )}

        {/* Failed / Cancelled Recovery Banner (No duplicate Cancel button!) */}
        {isFailedOrCancelled && (
          <div className="rounded-2xl bg-rose-500/10 border border-rose-500/35 p-4 flex flex-wrap items-center justify-between gap-4">
            <div className="space-y-1 min-w-0 flex-1">
              <h2 className="text-sm font-extrabold text-rose-300 flex items-center gap-2">
                <AlertCircle className="w-4 h-4 shrink-0" />
                {job?.status === 'cancelled' ? 'Job Cancelled' : 'Pipeline Interrupted or Failed'}
              </h2>
              <p className="text-xs text-[var(--text-secondary)] break-words">
                {job?.error_detail || 'Checkpoints up to the last completed stage are preserved on disk.'}
              </p>
            </div>
            <div className="flex flex-wrap items-center gap-2 shrink-0">
              <button
                type="button"
                disabled={retrying}
                onClick={() => handleRetryJob(selectedJobId, false)}
                className="inline-flex items-center gap-1.5 px-3.5 py-2 rounded-xl bg-[var(--accent-primary)] text-[var(--accent-foreground)] text-xs font-bold hover:brightness-110 disabled:opacity-50"
              >
                <RotateCcw className="w-3.5 h-3.5" />
                Retry From Checkpoint
              </button>
              <button
                type="button"
                disabled={retrying}
                onClick={() => handleRetryJob(selectedJobId, true)}
                className="inline-flex items-center gap-1.5 px-3.5 py-2 rounded-xl bg-[var(--bg-card)] hover:bg-[var(--bg-hover)] border border-[var(--border-subtle)] text-xs font-bold text-emerald-400"
              >
                <WifiOff className="w-3.5 h-3.5" />
                Complete in Offline Mode
              </button>
            </div>
          </div>
        )}

        {/* 2. FOUR LIVE TELEMETRY COUNTERS */}
        <section className="grid grid-cols-2 lg:grid-cols-4 gap-3">
          <div className="rounded-xl bg-[var(--bg-surface)] border border-[var(--border-subtle)] p-4">
            <div className="text-[11px] font-bold uppercase tracking-wider text-[var(--text-muted)]">
              Words Transcribed
            </div>
            <div className="text-xl font-mono font-extrabold text-[var(--text-primary)] mt-1">
              {(metrics.words_transcribed || 0).toLocaleString()}
            </div>
            <div className="text-[11px] text-[var(--text-muted)] mt-0.5">faster-whisper VAD</div>
          </div>

          <div className="rounded-xl bg-[var(--bg-surface)] border border-[var(--border-subtle)] p-4">
            <div className="text-[11px] font-bold uppercase tracking-wider text-[var(--text-muted)]">
              Keyframes Scanned
            </div>
            <div className="text-xl font-mono font-extrabold text-[var(--text-primary)] mt-1">
              {metrics.keyframes_total > 0
                ? `${metrics.keyframes_scanned.toLocaleString()} / ${metrics.keyframes_total.toLocaleString()}`
                : (metrics.keyframes_scanned || 0).toLocaleString()}
            </div>
            <div className="text-[11px] text-[var(--text-muted)] mt-0.5">Sequential vision pass</div>
          </div>

          <div className="rounded-xl bg-[var(--bg-surface)] border border-[var(--border-subtle)] p-4">
            <div className="text-[11px] font-bold uppercase tracking-wider text-[var(--text-muted)]">
              Highlights Detected
            </div>
            <div className="text-xl font-mono font-extrabold text-[var(--accent-primary)] mt-1">
              {Math.max(metrics.highlights_detected || 0, job?.clips_count || 0)}
            </div>
            <div className="text-[11px] text-[var(--text-muted)] mt-0.5">Viral segments scored</div>
          </div>

          <div className="rounded-xl bg-[var(--bg-surface)] border border-[var(--border-subtle)] p-4">
            <div className="text-[11px] font-bold uppercase tracking-wider text-[var(--text-muted)]">
              Est. Time Remaining
            </div>
            <div className="text-xl font-mono font-extrabold text-[var(--text-primary)] mt-1">
              {isFinishedStatus
                ? 'Done'
                : isFailedOrCancelled
                  ? 'Stopped'
                  : (job?.eta_seconds ?? 0) > 0
                    ? formatClock(job?.eta_seconds || 0)
                    : '—'}
            </div>
            <div className="text-[11px] text-[var(--text-muted)] mt-0.5">
              {isRunningStatus ? 'Live stage ETA' : 'Execution complete'}
            </div>
          </div>
        </section>

        {/* 3. INTERACTIVE 5-STAGE STEPPER (Click stage to filter logs + step duration badges) */}
        <section className="rounded-2xl bg-[var(--bg-surface)] border border-[var(--border-subtle)] p-4 space-y-3">
          <div className="flex items-center justify-between text-xs">
            <span className="font-bold uppercase tracking-wider text-[var(--text-primary)]">
              5-Stage Pipeline Stepper
            </span>
            <span className="text-[11px] text-[var(--text-muted)]">
              Click any stage card to filter terminal logs below
            </span>
          </div>

          <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-5 gap-2.5">
            {stages.map((st, idx) => {
              const filterId = (st.id as StageFilterKey) || 'ALL';
              const isSelectedFilter = stageFilter === filterId;
              const stepDur = formatStepDuration(st.duration_sec);

              return (
                <button
                  key={st.id || idx}
                  type="button"
                  onClick={() => setStageFilter((prev) => (prev === filterId ? 'ALL' : filterId))}
                  className={`p-3 rounded-xl border text-left transition-all flex flex-col justify-between gap-2 ${
                    isSelectedFilter
                      ? 'bg-[var(--accent-soft)] border-[var(--accent-primary)] ring-1 ring-[var(--accent-primary)]/40'
                      : st.status === 'running'
                        ? 'bg-[var(--bg-card)] border-[var(--accent-primary)]/70'
                        : st.status === 'done'
                          ? 'bg-[var(--bg-card)] border-emerald-500/35 hover:border-emerald-400/70'
                          : st.status === 'failed'
                            ? 'bg-rose-500/10 border-rose-500/40'
                            : 'bg-[var(--bg-card)]/60 border-[var(--border-subtle)] opacity-80 hover:opacity-100'
                  }`}
                >
                  <div className="flex items-center justify-between gap-1.5">
                    <span className="text-[10px] font-mono font-bold text-[var(--text-muted)]">
                      0{idx + 1}
                    </span>

                    <div className="flex items-center gap-1.5">
                      {stepDur && st.status === 'done' && (
                        <span className="px-1.5 py-0.5 rounded bg-emerald-500/15 text-emerald-400 font-mono text-[10px] font-bold">
                          {stepDur}
                        </span>
                      )}
                      {st.status === 'done' ? (
                        <CheckCircle2 className="w-4 h-4 text-emerald-400 shrink-0" />
                      ) : st.status === 'running' ? (
                        <Loader2 className="w-4 h-4 animate-spin text-[var(--accent-primary)] shrink-0" />
                      ) : st.status === 'failed' ? (
                        <XCircle className="w-4 h-4 text-rose-400 shrink-0" />
                      ) : (
                        <span className="w-2.5 h-2.5 rounded-full bg-[var(--border-subtle)] shrink-0" />
                      )}
                    </div>
                  </div>

                  <div>
                    <div className="text-xs font-bold text-[var(--text-primary)] truncate">{st.name}</div>
                    <div className="text-[10px] text-[var(--text-muted)] line-clamp-2 mt-0.5">{st.desc}</div>
                  </div>

                  {st.status === 'running' && (
                    <div className="text-[10px] font-mono font-bold text-[var(--accent-primary)]">
                      Active · {st.percent}%
                    </div>
                  )}
                </button>
              );
            })}
          </div>
        </section>

        {/* 4. EXPANDED LIVE TERMINAL CONSOLE (h-[240px] OPEN BY DEFAULT) */}
        <section className="rounded-2xl bg-[var(--bg-surface)] border border-[var(--border-subtle)] overflow-hidden shadow-sm">
          {/* Console Toolbar (NO Cancel button here!) */}
          <div className="px-4 py-2.5 bg-[var(--bg-card)] border-b border-[var(--border-subtle)] flex flex-wrap items-center justify-between gap-2">
            <div className="flex flex-wrap items-center gap-2">
              <div className="flex items-center gap-1.5 text-xs font-bold text-[var(--text-primary)] mr-1">
                <Terminal className="w-4 h-4 text-[var(--accent-primary)]" />
                Live Telemetry Console
              </div>

              {/* Filter by Stage Pills */}
              <div className="flex flex-wrap items-center gap-1">
                <Filter className="w-3 h-3 text-[var(--text-muted)] mr-0.5" />
                {(
                  [
                    { key: 'ALL', label: 'All Stages' },
                    { key: 'ingest', label: 'Ingest' },
                    { key: 'audio', label: 'Audio' },
                    { key: 'transcribe', label: 'Transcribe' },
                    { key: 'gemini', label: 'AI / Vision' },
                    { key: 'ready', label: 'Ready' },
                  ] as Array<{ key: StageFilterKey; label: string }>
                ).map((item) => (
                  <button
                    key={item.key}
                    type="button"
                    onClick={() => setStageFilter(item.key)}
                    className={`px-2 py-0.5 rounded-md text-[10px] font-bold transition-colors ${
                      stageFilter === item.key
                        ? 'bg-[var(--accent-primary)] text-[var(--accent-foreground)]'
                        : 'bg-[var(--bg-input)] text-[var(--text-muted)] hover:text-[var(--text-primary)]'
                    }`}
                  >
                    {item.label}
                  </button>
                ))}
              </div>
            </div>

            <div className="flex items-center gap-2">
              <button
                type="button"
                onClick={() => setAutoScroll((v) => !v)}
                className={`px-2.5 py-1 rounded-lg border text-[11px] font-bold transition-colors ${
                  autoScroll
                    ? 'bg-[var(--accent-soft)] border-[var(--accent-primary)]/50 text-[var(--accent-primary)]'
                    : 'bg-[var(--bg-input)] border-[var(--border-subtle)] text-[var(--text-muted)]'
                }`}
              >
                Auto-Scroll: {autoScroll ? 'ON' : 'OFF'}
              </button>

              <button
                type="button"
                onClick={handleCopyLogs}
                className="inline-flex items-center gap-1 px-2.5 py-1 rounded-lg bg-[var(--bg-input)] hover:bg-[var(--bg-hover)] border border-[var(--border-subtle)] text-[11px] font-bold text-[var(--text-primary)] transition-colors"
              >
                {copiedLogs ? (
                  <>
                    <Check className="w-3 h-3 text-emerald-400" />
                    Copied
                  </>
                ) : (
                  <>
                    <Copy className="w-3 h-3 text-[var(--accent-primary)]" />
                    Copy Logs
                  </>
                )}
              </button>
            </div>
          </div>

          {/* Monospace Scrolling Log Stream (h-[240px]) */}
          <div
            ref={logViewportRef}
            className="h-[240px] overflow-y-auto p-3.5 font-mono text-[11px] leading-relaxed bg-[var(--bg-canvas)] space-y-1.5"
          >
            {filteredLogs.length === 0 ? (
              <div className="h-full flex items-center justify-center text-[var(--text-muted)]">
                {stageFilter === 'ALL'
                  ? 'Waiting for telemetry events...'
                  : `No log events recorded for stage filter '${stageFilter.toUpperCase()}'.`}
              </div>
            ) : (
              filteredLogs.map((log, idx) => {
                const lvlColor =
                  log.level === 'ERROR'
                    ? 'text-rose-400'
                    : log.level === 'WARN'
                      ? 'text-amber-300'
                      : log.level === 'SUCCESS'
                        ? 'text-emerald-400'
                        : 'text-[var(--text-secondary)]';
                const tag = (log.stage || 'INFO').toUpperCase();
                const tagBadge =
                  tag === 'OFFLINE AI'
                    ? 'bg-emerald-500/15 text-emerald-400 border-emerald-500/30'
                    : tag === 'GEMINI'
                      ? 'bg-indigo-500/15 text-indigo-300 border-indigo-500/30'
                      : tag === 'ERROR'
                        ? 'bg-rose-500/15 text-rose-400 border-rose-500/30'
                        : 'bg-[var(--bg-surface)] text-[var(--accent-primary)] border-[var(--border-subtle)]';

                return (
                  <div key={idx} className="flex items-start gap-2 break-words">
                    <span className="text-[var(--text-muted)] shrink-0 select-none">
                      {formatTimestampShort(log.timestamp)}
                    </span>
                    <span className={`px-1.5 py-0 rounded border text-[10px] font-bold shrink-0 ${tagBadge}`}>
                      [{tag}]
                    </span>
                    <span className={lvlColor}>{log.message}</span>
                  </div>
                );
              })
            )}
          </div>
        </section>
      </div>
    </div>
  );
};
