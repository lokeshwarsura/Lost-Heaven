import React, { useState, useEffect, useRef } from 'react';
import { 
  CheckCircle2, Clock, Terminal, ChevronDown, ChevronUp,
  AlertCircle, RefreshCw, XCircle, ArrowRight, Sparkles,
  Cpu, HardDrive, FileVideo, Layers, ShieldCheck, Key, Eye, EyeOff
} from 'lucide-react';
import { JobProgress, BatchQueueItem } from '../types';
import { api } from '../api/client';

interface ProgressViewProps {
  projectId: number;
  onOpenEditor: (projectId: number) => void;
  onBackToHome: () => void;
}

export const ProgressView: React.FC<ProgressViewProps> = ({
  projectId,
  onOpenEditor,
  onBackToHome
}) => {
  const [progress, setProgress] = useState<JobProgress | null>(null);
  const [batchItems, setBatchItems] = useState<BatchQueueItem[]>([]);
  const [isLoading, setIsLoading] = useState(true);
  const [isCancelling, setIsCancelling] = useState(false);
  const [isRetrying, setIsRetrying] = useState(false);
  const [elapsedSeconds, setElapsedSeconds] = useState(0);
  const [autoOpenEditor, setAutoOpenEditor] = useState(true);
  const [autoOpenCountdown, setAutoOpenCountdown] = useState<number | null>(null);
  const [isLogsExpanded, setIsLogsExpanded] = useState(true);

  // Gemini API Key Recovery Card State
  const [apiKeyInput, setApiKeyInput] = useState('');
  const [showApiKey, setShowApiKey] = useState(false);
  const [isProvidingKey, setIsProvidingKey] = useState(false);
  const [keyError, setKeyError] = useState<string | null>(null);
  const [isRetryingOffline, setIsRetryingOffline] = useState(false);

  const logsEndRef = useRef<HTMLDivElement>(null);

  // Poll progress every 1s
  useEffect(() => {
    let isMounted = true;

    const fetchProgress = async () => {
      try {
        const [data, batchRes] = await Promise.all([
          api.getProjectProgress(projectId),
          api.getBatchQueue().catch(() => ({ items: [] }))
        ]);
        if (isMounted) {
          setProgress(data);
          setBatchItems(batchRes?.items || []);
          setIsLoading(false);
        }
      } catch (err) {
        console.error('Failed to fetch job progress:', err);
      }
    };

    fetchProgress();
    const interval = setInterval(fetchProgress, 1000);
    return () => {
      isMounted = false;
      clearInterval(interval);
    };
  }, [projectId]);

  // Track elapsed timer accurately using server-computed elapsed_seconds
  useEffect(() => {
    if (typeof progress?.elapsed_seconds === 'number') {
      setElapsedSeconds(Math.max(0, progress.elapsed_seconds));
    } else if (progress?.started_at) {
      const startTime = new Date(progress.started_at).getTime();
      const isComplete = progress.status === 'finished' || progress.status === 'analyzed' || progress.status === 'error' || progress.status === 'cancelled' || progress.status === 'awaiting_api_key';
      const endTime = isComplete && progress.updated_at ? new Date(progress.updated_at).getTime() : Date.now();
      setElapsedSeconds(Math.max(0, Math.floor((endTime - startTime) / 1000)));
    } else {
      setElapsedSeconds(0);
    }

    let timer: any = null;
    if (progress?.status === 'processing') {
      timer = setInterval(() => {
        setElapsedSeconds((prev) => prev + 1);
      }, 1000);
    }
    return () => {
      if (timer) clearInterval(timer);
    };
  }, [progress?.elapsed_seconds, progress?.started_at, progress?.status, progress?.updated_at]);

  // Auto-scroll logs
  useEffect(() => {
    if (isLogsExpanded) {
      logsEndRef.current?.scrollIntoView({ behavior: 'smooth' });
    }
  }, [progress?.logs, isLogsExpanded]);

  // Reset auto-redirect countdown when project switches
  useEffect(() => {
    setAutoOpenCountdown(null);
  }, [projectId]);

  // Handle completion auto-redirect countdown
  useEffect(() => {
    const isFinished = progress?.status === 'finished' || progress?.status === 'analyzed';
    if (isFinished && autoOpenEditor && autoOpenCountdown === null) {
      setAutoOpenCountdown(3);
    }
  }, [progress?.status, autoOpenEditor, autoOpenCountdown]);

  useEffect(() => {
    if (autoOpenCountdown !== null && autoOpenCountdown > 0) {
      const t = setTimeout(() => {
        setAutoOpenCountdown(autoOpenCountdown - 1);
      }, 1000);
      return () => clearTimeout(t);
    } else if (autoOpenCountdown === 0) {
      onOpenEditor(projectId);
    }
  }, [autoOpenCountdown, onOpenEditor, projectId]);

  const handleCancelJob = async () => {
    if (!confirm('Are you sure you want to abort this analysis pipeline?')) return;
    try {
      setIsCancelling(true);
      await api.cancelProject(projectId);
      const data = await api.getProjectProgress(projectId);
      setProgress(data);
    } catch (err) {
      console.error('Cancel failed:', err);
    } finally {
      setIsCancelling(false);
    }
  };

  const handleRetryJob = async () => {
    try {
      setIsRetrying(true);
      await api.retryProject(projectId, false);
      const data = await api.getProjectProgress(projectId);
      setProgress(data);
    } catch (err: any) {
      alert(`Failed to retry project: ${err?.message || err}`);
    } finally {
      setIsRetrying(false);
    }
  };

  const handleProvideKey = async (e?: React.FormEvent) => {
    if (e) e.preventDefault();
    if (!apiKeyInput.trim()) {
      setKeyError('Please enter a Google Gemini API key.');
      return;
    }
    setKeyError(null);
    setIsProvidingKey(true);
    try {
      await api.provideApiKey(projectId, apiKeyInput.trim());
      const data = await api.getProjectProgress(projectId);
      setProgress(data);
      setApiKeyInput('');
    } catch (err: any) {
      setKeyError(err?.message || 'Verification failed. Please check your Gemini API key.');
    } finally {
      setIsProvidingKey(false);
    }
  };

  const handleContinueOffline = async () => {
    setIsRetryingOffline(true);
    try {
      await api.retryProject(projectId, true);
      const data = await api.getProjectProgress(projectId);
      setProgress(data);
    } catch (err: any) {
      alert(`Failed to switch to offline mode: ${err?.message || err}`);
    } finally {
      setIsRetryingOffline(false);
    }
  };

  const formatTimer = (sec: number) => {
    const m = Math.floor(sec / 60);
    const s = Math.floor(sec % 60);
    return `${m}:${s < 10 ? '0' : ''}${s}`;
  };

  const isFinished = progress?.status === 'finished' || progress?.status === 'analyzed';
  const isError = progress?.status === 'error';
  const isCancelled = progress?.status === 'cancelled';
  const isAwaitingApiKey = progress?.status === 'awaiting_api_key';

  return (
    <div className="flex-1 h-full overflow-y-auto bg-[var(--bg-canvas)] text-[var(--text-primary)] transition-colors p-6 md:p-10 select-none flex flex-col items-center justify-center">
      <div className="w-full max-w-3xl mx-auto space-y-6 animate-fadeIn py-6">
        
        {/* Main Central Studio Progress Card */}
        <div className="bg-[var(--bg-surface)] border border-[var(--border-subtle)] rounded-3xl p-6 md:p-8 shadow-2xl space-y-6 relative overflow-hidden">
          
          {/* Top Metadata Bar */}
          <div className="flex flex-col sm:flex-row items-start sm:items-center justify-between gap-4 pb-5 border-b border-[var(--border-subtle)]">
            <div className="flex items-center gap-3">
              <div className="w-12 h-12 rounded-2xl bg-[var(--accent-primary)]/15 border border-[var(--accent-primary)]/30 flex items-center justify-center text-[var(--accent-primary)]">
                <FileVideo className="w-6 h-6" />
              </div>
              <div>
                <h2 className="font-bold text-base text-[var(--text-primary)] truncate max-w-sm">
                  {progress?.title || 'Processing Video...'}
                </h2>
                <div className="flex items-center gap-2 text-xs text-[var(--text-secondary)] font-mono mt-0.5">
                  {progress?.source_filename && <span>{progress.source_filename}</span>}
                  {progress?.file_size_formatted && <span>• {progress.file_size_formatted}</span>}
                </div>
              </div>
            </div>

            {/* Hardware Telemetry Badge & Time */}
            <div className="flex items-center gap-3 self-end sm:self-auto font-mono text-xs">
              <div className="px-3 py-1.5 rounded-xl bg-[var(--bg-card)] border border-[var(--border-subtle)] flex items-center gap-2 text-[var(--text-secondary)]">
                <Cpu className="w-3.5 h-3.5 text-emerald-400" />
                <span className="text-[11px] font-bold text-[var(--text-primary)]">
                  {progress?.hardware_badge || 'GPU Hardware Acceleration'}
                </span>
              </div>
              <div className="px-3 py-1.5 rounded-xl bg-[var(--bg-card)] border border-[var(--border-subtle)] flex items-center gap-1.5 text-[var(--text-secondary)]">
                <Clock className="w-3.5 h-3.5 text-[var(--accent-primary)]" />
                <span className="font-bold text-[var(--text-primary)] tabular-nums">{formatTimer(elapsedSeconds)}</span>
              </div>
            </div>
          </div>

          {/* Animated Horizontal Progress Bar */}
          <div className="space-y-2">
            <div className="flex items-center justify-between text-xs">
              <span className="font-bold text-[var(--text-primary)] flex items-center gap-2">
                {isFinished ? (
                  <span className="text-emerald-400 flex items-center gap-1.5">
                    <CheckCircle2 className="w-4 h-4" /> Pipeline Complete
                  </span>
                ) : isAwaitingApiKey ? (
                  <span className="text-amber-400 flex items-center gap-1.5 font-bold animate-pulse">
                    <Key className="w-4 h-4" /> Action Required: Gemini API Key Needed
                  </span>
                ) : isError ? (
                  <span className="text-red-400 flex items-center gap-1.5">
                    <AlertCircle className="w-4 h-4" /> Pipeline Failed
                  </span>
                ) : isCancelled ? (
                  <span className="text-amber-400">Cancelled</span>
                ) : (
                  <span className="flex items-center gap-1.5">
                    <RefreshCw className="w-3.5 h-3.5 text-[var(--accent-primary)] animate-spin" />
                    {progress?.stage_label || 'Executing pipeline stages...'}
                  </span>
                )}
              </span>

              <span className="font-mono font-black text-sm text-[var(--accent-primary)] tabular-nums">
                {progress?.percent ?? 0}%
              </span>
            </div>

            {/* Track */}
            <div className="w-full h-3.5 bg-[var(--bg-input)] rounded-full overflow-hidden p-0.5 border border-[var(--border-subtle)] shadow-inner">
              <div
                className={`h-full rounded-full transition-all duration-500 ease-out ${
                  isError
                    ? 'bg-red-500'
                    : isFinished
                    ? 'bg-emerald-500 shadow-md shadow-emerald-500/30'
                    : isAwaitingApiKey
                    ? 'bg-amber-500 shadow-md shadow-amber-500/40'
                    : 'bg-gradient-to-r from-[#F97316] to-[#F59E0B] shadow-md shadow-[rgba(245,158,11,0.3)]'
                }`}
                style={{ width: `${Math.max(5, progress?.percent ?? 5)}%` }}
              />
            </div>

            {/* Estimated time remaining */}
            {!isFinished && !isError && !isAwaitingApiKey && progress?.eta_seconds !== undefined && progress.eta_seconds > 0 && (
              <div className="text-[11px] text-right text-[var(--text-secondary)] font-mono tabular-nums">
                Estimated time remaining: ~{progress.eta_seconds}s
              </div>
            )}
          </div>

          {/* 5-Step Pipeline Stepper */}
          <div className="grid grid-cols-5 gap-2 pt-2">
            {(progress?.stages || [
              { id: 'ingest', name: 'Media Prep', desc: 'Probing video stream', status: 'running', percent: 20 },
              { id: 'audio', name: 'Audio Prep', desc: '16kHz WAV & 480p proxy', status: 'pending', percent: 0 },
              { id: 'transcribe', name: 'Whisper VAD', desc: 'Voice recognition', status: 'pending', percent: 0 },
              { id: 'gemini', name: 'Gemini AI', desc: 'Viral moment detection', status: 'pending', percent: 0 },
              { id: 'ready', name: 'Sync & Ready', desc: 'Generating clips', status: 'pending', percent: 0 }
            ]).map((st, idx) => {
              const isDone = st.status === 'done';
              const isRunning = st.status === 'running';
              const isFailed = st.status === 'failed';
              const isActionRequired = st.status === 'action_required' || (isAwaitingApiKey && st.id === 'gemini');

              return (
                <div
                  key={st.id || idx}
                  className={`p-3 rounded-2xl border text-center transition flex flex-col items-center justify-between gap-1.5 ${
                    isDone
                      ? 'bg-emerald-500/10 border-emerald-500/30 text-emerald-400'
                      : isActionRequired
                      ? 'bg-amber-500/15 border-amber-500/50 text-amber-400 ring-2 ring-amber-500/30 animate-pulse'
                      : isRunning
                      ? 'bg-[var(--accent-primary)]/10 border-[var(--accent-primary)] text-[var(--text-primary)] ring-1 ring-[var(--accent-primary)]/30'
                      : isFailed
                      ? 'bg-red-500/10 border-red-500/30 text-red-400'
                      : 'bg-[var(--bg-input)] border-[var(--border-subtle)] text-[var(--text-secondary)] opacity-60'
                  }`}
                >
                  <div className="w-7 h-7 rounded-full flex items-center justify-center text-xs font-black">
                    {isDone ? (
                      <CheckCircle2 className="w-5 h-5 text-emerald-400" />
                    ) : isActionRequired ? (
                      <Key className="w-4 h-4 text-amber-400" />
                    ) : isRunning ? (
                      <RefreshCw className="w-4 h-4 text-[var(--accent-primary)] animate-spin" />
                    ) : isFailed ? (
                      <AlertCircle className="w-5 h-5 text-red-400" />
                    ) : (
                      <span className="text-xs font-mono">{idx + 1}</span>
                    )}
                  </div>

                  <div className="w-full min-w-0 overflow-hidden text-center">
                    <span className="block text-[11px] font-bold text-[var(--text-primary)] truncate">
                      {st.name}
                    </span>
                    <span className="block text-[9px] text-[var(--text-secondary)] leading-tight line-clamp-2">
                      {isActionRequired ? 'Key Required' : st.desc}
                    </span>
                  </div>
                </div>
              );
            })}
          </div>

          {/* Interactive Gemini API Key Recovery Card */}
          {isAwaitingApiKey && (
            <div className="p-6 rounded-2xl bg-amber-500/10 border border-amber-500/40 space-y-4 animate-fadeIn">
              <div className="flex items-start gap-3">
                <div className="w-10 h-10 rounded-xl bg-amber-500/20 text-amber-400 flex items-center justify-center flex-shrink-0 mt-0.5">
                  <Key className="w-5 h-5" />
                </div>
                <div className="flex-1 min-w-0">
                  <div className="flex items-center gap-2">
                    <h3 className="font-bold text-sm text-[var(--text-primary)]">
                      {progress?.api_key_reason === 'invalid'
                        ? 'Gemini API Key Not Recognized'
                        : 'Gemini API Key Needed for AI Highlights'}
                    </h3>
                      <span className="text-[10px] font-bold uppercase tracking-wider px-2 py-0.5 rounded-full bg-amber-500/20 text-amber-500 border border-amber-500/30">
                        Action Required
                      </span>
                    </div>
                    <p className="text-xs text-[var(--text-secondary)] mt-1 leading-relaxed">
                      Stages 1–3 (Media Prep, Audio Extraction, Whisper VAD) completed successfully and are safely cached. 
                      Provide a valid Google Gemini API key to run AI moment detection, or complete immediately in 100% Offline Mode.
                    </p>
                    {progress?.api_key_message && (
                      <p className="text-xs font-mono text-amber-500 mt-1.5 bg-amber-500/10 p-2 rounded-lg border border-amber-500/25">
                        {progress.api_key_message}
                      </p>
                    )}
                  </div>
                </div>

                {/* Key Input Form */}
                <form onSubmit={handleProvideKey} className="space-y-3 pt-1">
                  <div className="relative">
                    <input
                      type={showApiKey ? 'text' : 'password'}
                      value={apiKeyInput}
                      onChange={(e) => {
                        setApiKeyInput(e.target.value);
                        if (keyError) setKeyError(null);
                      }}
                      placeholder="Paste AI Studio API key (AIzaSy...)"
                      className="w-full bg-[var(--bg-input)] border border-[var(--border-subtle)] focus:border-amber-500 rounded-xl px-4 py-2.5 text-xs text-[var(--text-primary)] placeholder-[var(--text-secondary)] pr-10 outline-none transition font-mono"
                    />
                    <button
                      type="button"
                      onClick={() => setShowApiKey(!showApiKey)}
                      className="absolute right-3 top-1/2 -translate-y-1/2 text-[var(--text-secondary)] hover:text-[var(--text-primary)]"
                    >
                      {showApiKey ? <EyeOff className="w-4 h-4" /> : <Eye className="w-4 h-4" />}
                    </button>
                  </div>

                {keyError && (
                  <div className="flex items-center gap-2 text-xs text-red-400 bg-red-500/10 p-2.5 rounded-xl border border-red-500/20">
                    <AlertCircle className="w-4 h-4 flex-shrink-0" />
                    <span>{keyError}</span>
                  </div>
                )}

                <div className="flex flex-col sm:flex-row items-stretch sm:items-center justify-between gap-3 pt-1">
                  <a
                    href="https://aistudio.google.com/apikey"
                    target="_blank"
                    rel="noopener noreferrer"
                    className="text-[11px] text-amber-400 hover:text-amber-300 underline underline-offset-2 flex items-center gap-1"
                  >
                    <span>Get free API Key from Google AI Studio</span>
                    <ArrowRight className="w-3 h-3" />
                  </a>

                  <div className="flex items-center gap-2">
                    <button
                      type="button"
                      onClick={handleContinueOffline}
                      disabled={isRetryingOffline || isProvidingKey}
                      className="px-4 py-2 rounded-xl bg-[var(--bg-card)] border border-[var(--border-subtle)] hover:bg-[var(--bg-input)] text-xs font-semibold text-[var(--text-secondary)] hover:text-[var(--text-primary)] transition flex items-center gap-1.5 disabled:opacity-50"
                      title="Extract clips using local acoustic energy + Whisper without Gemini"
                    >
                      <Cpu className="w-3.5 h-3.5 text-amber-400" />
                      <span>{isRetryingOffline ? 'Switching...' : 'Continue in 100% Offline Mode'}</span>
                    </button>

                    <button
                      type="submit"
                      disabled={isProvidingKey || !apiKeyInput.trim()}
                      className="px-5 py-2 rounded-xl bg-gradient-to-r from-amber-500 to-amber-600 hover:from-amber-400 hover:to-amber-500 text-black font-black text-xs shadow-md shadow-amber-500/20 transition flex items-center gap-1.5 disabled:opacity-50"
                    >
                      <Sparkles className={`w-3.5 h-3.5 ${isProvidingKey ? 'animate-spin' : ''}`} />
                      <span>{isProvidingKey ? 'Verifying...' : 'Verify Key & Resume Gemini AI'}</span>
                    </button>
                  </div>
                </div>
              </form>
            </div>
          )}

          {/* Completion CTA Actions */}
          {isFinished && (
            <div className="p-5 rounded-2xl bg-emerald-500/10 border border-emerald-500/30 flex flex-col sm:flex-row items-center justify-between gap-4 animate-fadeIn">
              <div className="flex items-center gap-3">
                <div className="w-10 h-10 rounded-xl bg-emerald-500/20 text-emerald-400 flex items-center justify-center flex-shrink-0">
                  <Sparkles className="w-5 h-5" />
                </div>
                <div>
                  <h3 className="font-bold text-sm text-[var(--text-primary)]">
                    Viral Moments Successfully Extracted!
                  </h3>
                  <p className="text-xs text-[var(--text-secondary)]">
                    {progress?.clips_count || 'Multiple'} clips ready with word-level karaoke captions.
                  </p>
                </div>
              </div>

              <div className="flex items-center gap-3">
                <label className="flex items-center gap-2 text-xs text-[var(--text-secondary)] cursor-pointer select-none">
                  <input
                    type="checkbox"
                    checked={autoOpenEditor}
                    onChange={(e) => setAutoOpenEditor(e.target.checked)}
                    className="rounded bg-[var(--bg-input)] border-[var(--border-subtle)] text-[var(--accent-primary)] focus:ring-0"
                  />
                  <span>Auto-open {autoOpenCountdown !== null ? `(${autoOpenCountdown}s)` : ''}</span>
                </label>

                <button
                  type="button"
                  onClick={() => onOpenEditor(projectId)}
                  className="px-6 py-2.5 rounded-xl bg-gradient-to-r from-[#F97316] to-[#F59E0B] text-black font-black text-xs shadow-lg shadow-[rgba(245,158,11,0.25)] hover:scale-105 active:scale-95 transition-all flex items-center gap-2"
                >
                  <span>Open Clips in Studio Editor</span>
                  <ArrowRight className="w-4 h-4" />
                </button>
              </div>
            </div>
          )}

          {/* Error Details & Retry */}
          {isError && (
            <div className="p-4 rounded-2xl bg-red-500/10 border border-red-500/30 space-y-3 animate-fadeIn">
              <div className="flex items-center gap-2 text-red-400 text-xs font-bold">
                <AlertCircle className="w-4 h-4" />
                <span>Processing Encountered an Error</span>
              </div>
              <div className="p-3 bg-[var(--bg-input)] rounded-xl font-mono text-[11px] text-red-500 overflow-x-auto max-h-32">
                {progress?.error_detail || 'Unknown pipeline exception.'}
              </div>
              <div className="flex flex-wrap justify-end gap-2 pt-1">
                <button
                  type="button"
                  onClick={onBackToHome}
                  className="px-4 py-2 rounded-xl bg-[var(--bg-card)] border border-[var(--border-subtle)] text-xs font-semibold text-[var(--text-secondary)] hover:text-[var(--text-primary)] transition"
                >
                  Back to Home
                </button>
                <button
                  type="button"
                  onClick={handleContinueOffline}
                  disabled={isRetryingOffline}
                  className="px-4 py-2 rounded-xl bg-[var(--bg-card)] border border-[var(--border-subtle)] text-xs font-semibold text-amber-500 hover:text-amber-400 hover:bg-[var(--bg-input)] transition flex items-center gap-1.5 disabled:opacity-50"
                  title="Complete remaining stages offline"
                >
                  <Cpu className="w-3.5 h-3.5 text-amber-400" />
                  <span>{isRetryingOffline ? 'Resuming...' : 'Complete in Offline Mode'}</span>
                </button>
                <button
                  type="button"
                  onClick={handleRetryJob}
                  disabled={isRetrying}
                  className="px-4 py-2 rounded-xl bg-red-600 hover:bg-red-500 text-white text-xs font-bold transition flex items-center gap-1.5 disabled:opacity-50"
                >
                  <RefreshCw className={`w-3.5 h-3.5 ${isRetrying ? 'animate-spin' : ''}`} />
                  {isRetrying ? 'Resuming...' : 'Retry Job'}
                </button>
              </div>
            </div>
          )}

          {/* Multi-Video Batch Queue */}
          {batchItems.length > 0 && (
            <div className="border border-[var(--border-subtle)] rounded-2xl p-4 bg-[var(--bg-card)] space-y-2.5">
              <div className="flex items-center justify-between text-xs font-bold text-[var(--text-primary)]">
                <span className="flex items-center gap-2">
                  <Layers className="w-4 h-4 text-[var(--accent-primary)]" />
                  Sequential Multi-Video Batch Queue ({batchItems.length})
                </span>
                <span className="text-[10px] font-mono text-[var(--text-secondary)]">
                  Auto-executes sequentially
                </span>
              </div>
              <div className="grid grid-cols-1 sm:grid-cols-2 gap-2 max-h-36 overflow-y-auto">
                {batchItems.map((b) => (
                  <div
                    key={b.project_id}
                    className="flex items-center justify-between gap-2 px-3 py-2 rounded-xl bg-[var(--bg-input)] border border-[var(--border-subtle)] text-xs"
                  >
                    <span className="font-semibold text-[var(--text-primary)] truncate">{b.title}</span>
                    <span
                      className={`text-[10px] font-bold uppercase px-2 py-0.5 rounded-full shrink-0 ${
                        b.status === 'completed'
                          ? 'bg-emerald-500/15 text-emerald-400'
                          : b.status === 'processing'
                          ? 'bg-[var(--accent-subtle)] text-[var(--accent-primary)]'
                          : b.status === 'error'
                          ? 'bg-red-500/15 text-red-400'
                          : 'bg-[var(--bg-card)] text-[var(--text-secondary)]'
                      }`}
                    >
                      {b.status}
                    </span>
                  </div>
                ))}
              </div>
            </div>
          )}

          {/* Live Console Logs Accordion */}
          <div className="border border-[var(--border-subtle)] rounded-2xl overflow-hidden bg-[var(--bg-card)]">
            <button
              type="button"
              onClick={() => setIsLogsExpanded(!isLogsExpanded)}
              className="w-full px-4 py-3 bg-[var(--bg-input)] flex items-center justify-between text-xs font-bold text-[var(--text-secondary)] hover:text-[var(--text-primary)] transition"
            >
              <div className="flex items-center gap-2">
                <Terminal className="w-4 h-4 text-[var(--accent-primary)]" />
                <span>Live Studio Terminal Console</span>
                <span className="text-[10px] font-mono px-2 py-0.5 rounded bg-[var(--bg-card)] text-[var(--text-secondary)] tabular-nums">
                  {progress?.logs?.length || 0} events
                </span>
              </div>
              {isLogsExpanded ? <ChevronUp className="w-4 h-4" /> : <ChevronDown className="w-4 h-4" />}
            </button>

            {isLogsExpanded && (
              <div className="p-4 font-mono text-[11px] max-h-48 overflow-y-auto space-y-1.5 bg-[var(--bg-input)] text-[var(--text-primary)]">
                {(progress?.logs || []).map((l, i) => (
                  <div key={i} className="flex items-start gap-2 leading-relaxed">
                    <span className="text-[var(--text-secondary)] flex-shrink-0 tabular-nums">
                      {new Date(l.timestamp).toLocaleTimeString()}
                    </span>
                    <span className={`text-[9px] font-black uppercase px-1 rounded flex-shrink-0 ${
                      l.level === 'SUCCESS'
                        ? 'bg-emerald-500/20 text-emerald-400'
                        : l.level === 'ERROR'
                        ? 'bg-red-500/20 text-red-400'
                        : l.level === 'WARN'
                        ? 'bg-amber-500/20 text-amber-400'
                        : 'bg-[var(--bg-card)] text-[var(--text-secondary)] border border-[var(--border-subtle)]'
                    }`}>
                      {l.stage}
                    </span>
                    <span className="text-[var(--text-primary)] break-all">{l.message}</span>
                  </div>
                ))}
                <div ref={logsEndRef} />
              </div>
            )}
          </div>

          {/* Bottom Actions */}
          <div className="flex items-center justify-between pt-2">
            <button
              type="button"
              onClick={onBackToHome}
              className="text-xs text-[var(--text-secondary)] hover:text-[var(--text-primary)] font-semibold transition flex items-center gap-1.5"
            >
              ← Back to Ingestion Home
            </button>

            {!isFinished && !isError && !isCancelled && (
              <button
                type="button"
                onClick={handleCancelJob}
                disabled={isCancelling}
                className="px-4 py-2 rounded-xl bg-red-500/10 hover:bg-red-500/20 border border-red-500/30 text-red-400 text-xs font-semibold transition disabled:opacity-50"
              >
                {isCancelling ? 'Aborting...' : 'Cancel Job'}
              </button>
            )}
          </div>

        </div>
      </div>
    </div>
  );
};
