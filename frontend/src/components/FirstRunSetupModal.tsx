import React, { useEffect, useState } from 'react';
import {
  AlertCircle,
  ArrowLeft,
  ArrowRight,
  CheckCircle2,
  Cpu,
  Download,
  Film,
  HardDrive,
  KeyRound,
  Loader2,
  MonitorCheck,
  RefreshCw,
  ShieldCheck,
  Sparkles,
  WifiOff,
  X,
  Zap,
} from 'lucide-react';
import { api } from '../api/client';
import {
  PrerequisiteInstallStatus,
  StudioSettings,
  SystemInfo,
  SystemOnboardingReport,
} from '../types';

export interface FirstRunSetupModalProps {
  isOpen: boolean;
  onClose: () => void;
  onCompleted?: (settings: StudioSettings) => void;
  systemInfo?: SystemInfo | null;
}

const WHISPER_CHOICES = [
  { id: 'tiny', label: 'tiny (~75 MB · Fastest CPU)' },
  { id: 'base', label: 'base (~145 MB · Balanced CPU)' },
  { id: 'small', label: 'small (~465 MB · Mid GPU / Podcast)' },
  { id: 'medium', label: 'medium (~1.5 GB · High Precision)' },
  { id: 'large-v3-turbo', label: 'large-v3-turbo (~1.5 GB · Ultra GPU Studio)' },
];

export const FirstRunSetupModal: React.FC<FirstRunSetupModalProps> = ({
  isOpen,
  onClose,
  onCompleted,
}) => {
  const [step, setStep] = useState<1 | 2 | 3>(1);
  const [report, setReport] = useState<SystemOnboardingReport | null>(null);
  const [loadingSpecs, setLoadingSpecs] = useState(false);
  const [specError, setSpecError] = useState<string | null>(null);

  // Step 2: Prerequisites Downloader state
  const [wantFfmpeg, setWantFfmpeg] = useState(true);
  const [wantWhisper, setWantWhisper] = useState(true);
  const [installState, setInstallState] = useState<PrerequisiteInstallStatus | null>(null);
  const [isInstalling, setIsInstalling] = useState(false);

  // Step 3: Recommended Settings & AI Engine state
  const [whisperModel, setWhisperModel] = useState('base');
  const [whisperDevice, setWhisperDevice] = useState('cpu');
  const [whisperComputeType, setWhisperComputeType] = useState('int8');
  const [whisperBatchSize, setWhisperBatchSize] = useState(4);
  const [videoEncoder, setVideoEncoder] = useState('libx264');
  const [defaultResolution, setDefaultResolution] = useState('1080p');
  const [visionScanFps, setVisionScanFps] = useState(1.2);
  const [hardwareAcceleration, setHardwareAcceleration] = useState('auto');
  const [aiMode, setAiMode] = useState<'offline' | 'gemini'>('offline');
  const [geminiApiKey, setGeminiApiKey] = useState('');

  const [isSaving, setIsSaving] = useState(false);
  const [saveError, setSaveError] = useState<string | null>(null);

  const loadOnboarding = async () => {
    setLoadingSpecs(true);
    setSpecError(null);
    try {
      const data = await api.getSystemOnboarding();
      setReport(data);

      const rec = data.recommended_settings;
      setWhisperModel(rec.whisper_model);
      setWhisperDevice(rec.whisper_device);
      setWhisperComputeType(rec.whisper_compute_type);
      setWhisperBatchSize(rec.whisper_batch_size);
      setVideoEncoder(rec.video_encoder);
      setDefaultResolution(rec.default_resolution);
      setVisionScanFps(rec.vision_scan_fps);
      setHardwareAcceleration(rec.hardware_acceleration);
      setAiMode(rec.ai_mode === 'gemini' ? 'gemini' : 'offline');

      const ffItem = data.downloads_needed.find((d) => d.id === 'ffmpeg');
      const whItem = data.downloads_needed.find((d) => d.id === 'whisper_model');
      setWantFfmpeg(Boolean(ffItem && !ffItem.installed));
      setWantWhisper(Boolean(whItem && !whItem.installed));
    } catch (err: any) {
      setSpecError(err?.message || 'Could not detect hardware specs.');
    } finally {
      setLoadingSpecs(false);
    }
  };

  useEffect(() => {
    if (isOpen) {
      setStep(1);
      setInstallState(null);
      setIsInstalling(false);
      setSaveError(null);
      loadOnboarding();
    }
  }, [isOpen]);

  // Poll prerequisite installer status while active
  useEffect(() => {
    if (!isOpen || !isInstalling) return;
    let cancelled = false;
    const poll = async () => {
      try {
        const st = await api.getInstallStatus();
        if (cancelled) return;
        setInstallState(st);
        if (st.status === 'completed' || st.status === 'error') {
          setIsInstalling(false);
          if (st.status === 'completed') {
            loadOnboarding();
          }
        }
      } catch {
        // Ignore transient poll error
      }
    };
    poll();
    const timer = window.setInterval(poll, 1000);
    return () => {
      cancelled = true;
      window.clearInterval(timer);
    };
  }, [isOpen, isInstalling]);

  if (!isOpen) return null;

  const handleStartInstall = async () => {
    if (!report || isInstalling) return;
    const ffNeeded = report.downloads_needed.some((d) => d.id === 'ffmpeg' && !d.installed) && wantFfmpeg;
    const targetWhisper = wantWhisper ? whisperModel : undefined;
    if (!ffNeeded && !targetWhisper) {
      setStep(3);
      return;
    }
    setIsInstalling(true);
    try {
      const initSt = await api.installPrerequisites({
        install_ffmpeg: ffNeeded,
        whisper_model: targetWhisper,
      });
      setInstallState(initSt);
    } catch (err: any) {
      setIsInstalling(false);
      setInstallState({
        status: 'error',
        progress_pct: 0,
        current_step: 'error',
        message: err?.message || 'Failed to launch prerequisite downloader',
        error: err?.message,
        logs: [],
        ffmpeg_installed: false,
      });
    }
  };

  const handleCompleteOnboarding = async () => {
    setIsSaving(true);
    setSaveError(null);
    try {
      const res = await api.completeOnboarding({
        whisper_model: whisperModel,
        whisper_device: whisperDevice,
        whisper_compute_type: whisperComputeType,
        whisper_batch_size: whisperBatchSize,
        video_encoder: videoEncoder,
        default_resolution: defaultResolution,
        vision_scan_fps: visionScanFps,
        ai_mode: aiMode,
        gemini_api_key: geminiApiKey.trim() || undefined,
        hardware_acceleration: hardwareAcceleration,
      });
      localStorage.setItem('lost_heaven_onboarded', '1');
      window.dispatchEvent(new CustomEvent('lh-settings-updated', { detail: res.settings }));
      onCompleted?.(res.settings);
      onClose();
    } catch (err: any) {
      setSaveError(err?.message || 'Failed to save recommended studio settings.');
    } finally {
      setIsSaving(false);
    }
  };

  const specs = report?.specs;
  const downloads = report?.downloads_needed || [];
  const pendingDownloads = downloads.filter((d) => !d.installed && d.required);
  const tierBadge =
    report?.tier === 'ultra_gpu'
      ? 'bg-emerald-500/15 text-emerald-300 border-emerald-500/40'
      : report?.tier === 'mid_gpu'
        ? 'bg-indigo-500/15 text-indigo-300 border-indigo-500/40'
        : report?.tier === 'standard_cpu'
          ? 'bg-amber-500/15 text-amber-300 border-amber-500/40'
          : 'bg-slate-500/15 text-slate-300 border-slate-500/40';

  const gpuSubtitle = specs?.gpu?.has_cuda
    ? `${specs.gpu.vram_gb > 0 ? `${specs.gpu.vram_gb} GB VRAM · ` : ''}CUDA Enabled`
    : specs?.gpu?.is_apple_silicon
      ? 'Apple Silicon Unified Memory'
      : 'Integrated / CPU Fallback';

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/75 backdrop-blur-sm p-4 animate-fadeIn">
      <div className="w-full max-w-2xl bg-[var(--bg-surface)] border border-[var(--border-subtle)] rounded-3xl shadow-2xl overflow-hidden text-[var(--text-primary)] flex flex-col max-h-[92vh]">
        {/* Header + 3-Step Indicator */}
        <div className="px-6 py-4 border-b border-[var(--border-subtle)] bg-[var(--bg-card)] flex items-center justify-between gap-4">
          <div className="flex items-center gap-3">
            <div className="w-9 h-9 rounded-xl bg-[var(--accent-primary)] text-[var(--accent-foreground)] flex items-center justify-center font-black text-xs tracking-tighter shadow">
              LH
            </div>
            <div>
              <h2 className="text-sm font-extrabold text-[var(--text-primary)]">
                Lost Heaven Hardware &amp; First-Run Studio Setup
              </h2>
              <div className="flex items-center gap-2 text-[11px] text-[var(--text-muted)] mt-0.5">
                {[
                  { num: 1, title: 'Hardware Profile' },
                  { num: 2, title: 'Downloads' },
                  { num: 3, title: 'Settings & AI' },
                ].map((s, idx) => (
                  <React.Fragment key={s.num}>
                    <button
                      type="button"
                      onClick={() => setStep(s.num as 1 | 2 | 3)}
                      className={`font-bold transition-colors ${
                        step === s.num
                          ? 'text-[var(--accent-primary)]'
                          : step > s.num
                            ? 'text-emerald-400'
                            : 'text-[var(--text-muted)]'
                      }`}
                    >
                      {s.num}. {s.title}
                    </button>
                    {idx < 2 && <span className="opacity-40">›</span>}
                  </React.Fragment>
                ))}
              </div>
            </div>
          </div>

          <button
            type="button"
            onClick={onClose}
            className="p-1.5 rounded-lg text-[var(--text-secondary)] hover:text-[var(--text-primary)] hover:bg-[var(--bg-hover)] transition-colors"
            title="Close wizard"
          >
            <X className="w-4 h-4" />
          </button>
        </div>

        {/* Body */}
        <div className="p-6 overflow-y-auto space-y-5 flex-1">
          {loadingSpecs && !report ? (
            <div className="py-12 flex flex-col items-center justify-center gap-3 text-xs text-[var(--text-muted)]">
              <Loader2 className="w-7 h-7 animate-spin text-[var(--accent-primary)]" />
              <span>Scanning CPU, RAM, GPU VRAM, Video Encoders, and Whisper cache...</span>
            </div>
          ) : specError && !report ? (
            <div className="rounded-xl bg-rose-500/10 border border-rose-500/30 p-4 text-xs text-rose-300 flex items-center justify-between">
              <span>{specError}</span>
              <button
                type="button"
                onClick={loadOnboarding}
                className="px-3 py-1.5 rounded-lg bg-[var(--bg-card)] text-[var(--text-primary)] font-bold"
              >
                Retry Scan
              </button>
            </div>
          ) : null}

          {/* STEP 1: HARDWARE PROFILE */}
          {step === 1 && report && specs && (
            <div className="space-y-4">
              {/* Highlighted Detected Tier Banner */}
              <div className="rounded-2xl bg-[var(--bg-card)] border border-[var(--border-subtle)] p-4 space-y-2">
                <div className="flex flex-wrap items-center justify-between gap-2">
                  <span className={`inline-flex items-center gap-1.5 px-3 py-1 rounded-full text-xs font-extrabold border ${tierBadge}`}>
                    <Zap className="w-3.5 h-3.5" />
                    {report.tier_label}
                    {specs.gpu?.has_cuda && specs.gpu.name !== 'None'
                      ? ` · ${specs.gpu.name}${specs.gpu.vram_gb > 0 ? ` (${specs.gpu.vram_gb} GB VRAM)` : ''}`
                      : ` · ${specs.ram.total_gb} GB RAM`}
                  </span>

                  <button
                    type="button"
                    onClick={loadOnboarding}
                    disabled={loadingSpecs}
                    className="inline-flex items-center gap-1 px-2.5 py-1 rounded-lg bg-[var(--bg-input)] hover:bg-[var(--bg-hover)] border border-[var(--border-subtle)] text-[11px] font-bold text-[var(--text-secondary)]"
                  >
                    <RefreshCw className={`w-3 h-3 ${loadingSpecs ? 'animate-spin' : ''}`} />
                    Re-Scan
                  </button>
                </div>
                <p className="text-xs text-[var(--text-secondary)] leading-relaxed">
                  {report.summary_reason}
                </p>
              </div>

              {/* 4-Card Spec Grid */}
              <div className="grid grid-cols-1 sm:grid-cols-2 gap-3 text-xs">
                <div className="rounded-xl bg-[var(--bg-card)] border border-[var(--border-subtle)] p-3.5 space-y-1">
                  <div className="flex items-center gap-1.5 text-[10px] font-bold uppercase tracking-wider text-[var(--text-muted)]">
                    <Cpu className="w-3.5 h-3.5 text-[var(--accent-primary)]" />
                    CPU
                  </div>
                  <div className="font-bold text-[var(--text-primary)] truncate" title={specs.cpu.model}>
                    {specs.cpu.model}
                  </div>
                  <div className="text-[11px] text-[var(--text-muted)]">
                    {specs.cpu.physical_cores} physical cores · {specs.cpu.logical_threads} threads ({specs.os.name} {specs.os.arch})
                  </div>
                </div>

                <div className="rounded-xl bg-[var(--bg-card)] border border-[var(--border-subtle)] p-3.5 space-y-1">
                  <div className="flex items-center gap-1.5 text-[10px] font-bold uppercase tracking-wider text-[var(--text-muted)]">
                    <HardDrive className="w-3.5 h-3.5 text-[var(--accent-primary)]" />
                    Memory (RAM) &amp; Disk
                  </div>
                  <div className="font-bold text-[var(--text-primary)]">
                    {specs.ram.total_gb} GB Total RAM
                  </div>
                  <div className="text-[11px] text-[var(--text-muted)]">
                    {specs.ram.available_gb} GB free RAM · {specs.storage.free_gb} GB free disk
                  </div>
                </div>

                <div className="rounded-xl bg-[var(--bg-card)] border border-[var(--border-subtle)] p-3.5 space-y-1">
                  <div className="flex items-center gap-1.5 text-[10px] font-bold uppercase tracking-wider text-[var(--text-muted)]">
                    <MonitorCheck className="w-3.5 h-3.5 text-[var(--accent-primary)]" />
                    GPU &amp; VRAM
                  </div>
                  <div className="font-bold text-[var(--text-primary)] truncate">
                    {specs.gpu.name !== 'None' ? specs.gpu.name : 'CPU-Only Mode'}
                  </div>
                  <div className="text-[11px] text-[var(--text-muted)]">{gpuSubtitle}</div>
                </div>

                <div className="rounded-xl bg-[var(--bg-card)] border border-[var(--border-subtle)] p-3.5 space-y-1">
                  <div className="flex items-center gap-1.5 text-[10px] font-bold uppercase tracking-wider text-[var(--text-muted)]">
                    <Film className="w-3.5 h-3.5 text-[var(--accent-primary)]" />
                    Video Encoder
                  </div>
                  <div className="font-bold font-mono text-[var(--accent-primary)]">
                    {specs.encoders.preferred}
                  </div>
                  <div className="text-[11px] text-[var(--text-muted)] truncate">
                    Available: {specs.encoders.available.join(', ')}
                  </div>
                </div>
              </div>
            </div>
          )}

          {/* STEP 2: FIRST-TIME DOWNLOADS */}
          {step === 2 && report && (
            <div className="space-y-4">
              <div>
                <h3 className="text-sm font-extrabold text-[var(--text-primary)] flex items-center gap-2">
                  <Download className="w-4 h-4 text-[var(--accent-primary)]" />
                  First-Time Prerequisites &amp; Speech Weights
                </h3>
                <p className="text-xs text-[var(--text-muted)] mt-0.5">
                  Verify core media tools and pre-cache your tailored Whisper model for fast offline transcription.
                </p>
              </div>

              <div className="space-y-2.5">
                {downloads.map((item) => {
                  const isFfmpeg = item.id === 'ffmpeg';
                  const isWhisper = item.id === 'whisper_model';
                  const checked = isFfmpeg ? wantFfmpeg : isWhisper ? wantWhisper : item.installed;

                  return (
                    <div
                      key={item.id}
                      className={`rounded-xl border p-3.5 flex items-start justify-between gap-3 ${
                        item.installed
                          ? 'bg-emerald-500/5 border-emerald-500/30'
                          : 'bg-[var(--bg-card)] border-[var(--border-subtle)]'
                      }`}
                    >
                      <div className="flex items-start gap-3">
                        {(isFfmpeg || isWhisper) && !item.installed ? (
                          <input
                            type="checkbox"
                            checked={checked}
                            onChange={(e) => {
                              if (isFfmpeg) setWantFfmpeg(e.target.checked);
                              if (isWhisper) setWantWhisper(e.target.checked);
                            }}
                            className="mt-1 accent-[var(--accent-primary)] w-4 h-4"
                          />
                        ) : (
                          <div className="mt-0.5">
                            {item.installed ? (
                              <CheckCircle2 className="w-4 h-4 text-emerald-400" />
                            ) : (
                              <AlertCircle className="w-4 h-4 text-amber-400" />
                            )}
                          </div>
                        )}

                        <div className="space-y-0.5">
                          <div className="flex flex-wrap items-center gap-2">
                            <span className="text-xs font-bold text-[var(--text-primary)]">{item.name}</span>
                            {item.required && (
                              <span className="px-1.5 py-0.5 rounded text-[9px] font-bold uppercase bg-[var(--bg-input)] border border-[var(--border-subtle)] text-[var(--text-secondary)]">
                                Required
                              </span>
                            )}
                          </div>
                          <p className="text-[11px] text-[var(--text-muted)]">{item.description}</p>
                        </div>
                      </div>

                      <div className="shrink-0 text-right">
                        {item.installed ? (
                          <span className="inline-flex items-center gap-1 px-2.5 py-1 rounded-full text-[10px] font-bold bg-emerald-500/15 text-emerald-400 border border-emerald-500/30">
                            ✓ Installed
                          </span>
                        ) : (
                          <span className="inline-flex items-center gap-1 px-2.5 py-1 rounded-full text-[10px] font-bold bg-amber-500/15 text-amber-300 border border-amber-500/30">
                            ⬇ ~{item.size_mb} MB
                          </span>
                        )}
                      </div>
                    </div>
                  );
                })}
              </div>

              {/* Live Installer Progress Panel */}
              {installState && (
                <div className="rounded-xl bg-[var(--bg-card)] border border-[var(--border-subtle)] p-4 space-y-2.5">
                  <div className="flex items-center justify-between text-xs">
                    <span className="font-bold text-[var(--text-primary)] flex items-center gap-2">
                      {installState.status === 'running' && (
                        <Loader2 className="w-3.5 h-3.5 animate-spin text-[var(--accent-primary)]" />
                      )}
                      {installState.status === 'completed' && (
                        <CheckCircle2 className="w-3.5 h-3.5 text-emerald-400" />
                      )}
                      {installState.status === 'error' && (
                        <AlertCircle className="w-3.5 h-3.5 text-rose-400" />
                      )}
                      {installState.message}
                    </span>
                    <span className="font-mono font-bold text-[var(--accent-primary)]">
                      {installState.progress_pct}%
                    </span>
                  </div>

                  <div className="h-2 w-full rounded-full bg-[var(--bg-input)] overflow-hidden">
                    <div
                      className={`h-full transition-all duration-300 ${
                        installState.status === 'error'
                          ? 'bg-rose-500'
                          : installState.status === 'completed'
                            ? 'bg-emerald-500'
                            : 'bg-[var(--accent-primary)]'
                      }`}
                      style={{ width: `${Math.max(4, installState.progress_pct)}%` }}
                    />
                  </div>

                  {installState.logs?.length > 0 && (
                    <div className="max-h-24 overflow-y-auto text-[10px] font-mono text-[var(--text-muted)] space-y-0.5 pt-1">
                      {installState.logs.map((entry, idx) => (
                        <div key={idx}>{entry.message}</div>
                      ))}
                    </div>
                  )}
                </div>
              )}

              <div className="flex flex-wrap items-center justify-between gap-2 pt-1">
                <button
                  type="button"
                  onClick={handleStartInstall}
                  disabled={isInstalling || (!wantFfmpeg && !wantWhisper)}
                  className="px-4 py-2.5 rounded-xl bg-[var(--accent-primary)] text-[var(--accent-foreground)] text-xs font-extrabold flex items-center gap-2 hover:brightness-110 disabled:opacity-45 shadow"
                >
                  {isInstalling ? (
                    <>
                      <Loader2 className="w-3.5 h-3.5 animate-spin" />
                      Installing Prerequisites...
                    </>
                  ) : pendingDownloads.length === 0 ? (
                    <>
                      <CheckCircle2 className="w-3.5 h-3.5" />
                      Verify Selected Weights
                    </>
                  ) : (
                    <>
                      <Download className="w-3.5 h-3.5" />
                      Download &amp; Install Recommended
                    </>
                  )}
                </button>

                <button
                  type="button"
                  onClick={() => setStep(3)}
                  className="px-3.5 py-2 rounded-xl bg-[var(--bg-card)] hover:bg-[var(--bg-hover)] border border-[var(--border-subtle)] text-xs font-bold text-[var(--text-secondary)]"
                >
                  Skip for Now
                </button>
              </div>
            </div>
          )}

          {/* STEP 3: RECOMMENDED SETTINGS & AI MODE */}
          {step === 3 && report && (
            <div className="space-y-4">
              <div>
                <h3 className="text-sm font-extrabold text-[var(--text-primary)] flex items-center gap-2">
                  <Sparkles className="w-4 h-4 text-[var(--accent-primary)]" />
                  Tailored Studio Configuration &amp; AI Mode
                </h3>
                <p className="text-xs text-[var(--text-muted)] mt-0.5">
                  Matched to your <span className="font-bold text-[var(--text-primary)]">{report.tier_label}</span> tier. You can customize any setting anytime.
                </p>
              </div>

              {/* Tailored Configuration Table */}
              <div className="rounded-2xl bg-[var(--bg-card)] border border-[var(--border-subtle)] divide-y divide-[var(--border-subtle)] text-xs">
                <div className="p-3 flex items-center justify-between gap-4">
                  <div>
                    <div className="font-bold text-[var(--text-primary)]">Whisper Model</div>
                    <div className="text-[11px] text-[var(--text-muted)]">Speech recognition precision &amp; VRAM footprint</div>
                  </div>
                  <select
                    value={whisperModel}
                    onChange={(e) => setWhisperModel(e.target.value)}
                    className="px-2.5 py-1.5 rounded-lg bg-[var(--bg-input)] border border-[var(--border-subtle)] text-xs font-bold text-[var(--text-primary)]"
                  >
                    {WHISPER_CHOICES.map((w) => (
                      <option key={w.id} value={w.id}>
                        {w.label}
                        {w.id === report.recommended_settings.whisper_model ? ' ★' : ''}
                      </option>
                    ))}
                  </select>
                </div>

                <div className="p-3 flex items-center justify-between gap-4">
                  <div>
                    <div className="font-bold text-[var(--text-primary)]">Compute Mode</div>
                    <div className="text-[11px] text-[var(--text-muted)]">Inference device, precision &amp; batch concurrency</div>
                  </div>
                  <span className="px-2.5 py-1 rounded-lg bg-[var(--bg-input)] border border-[var(--border-subtle)] font-mono font-bold text-[var(--accent-primary)]">
                    {whisperDevice} · {whisperComputeType} (batch={whisperBatchSize})
                  </span>
                </div>

                <div className="p-3 flex items-center justify-between gap-4">
                  <div>
                    <div className="font-bold text-[var(--text-primary)]">Hardware Encoder</div>
                    <div className="text-[11px] text-[var(--text-muted)]">MP4 proxy &amp; final render video encoder</div>
                  </div>
                  <select
                    value={videoEncoder}
                    onChange={(e) => setVideoEncoder(e.target.value)}
                    className="px-2.5 py-1.5 rounded-lg bg-[var(--bg-input)] border border-[var(--border-subtle)] font-mono text-xs font-bold text-[var(--text-primary)]"
                  >
                    {(report.specs.encoders.available.includes(videoEncoder)
                      ? report.specs.encoders.available
                      : [videoEncoder, ...report.specs.encoders.available]
                    ).map((enc) => (
                      <option key={enc} value={enc}>
                        {enc}
                      </option>
                    ))}
                  </select>
                </div>

                <div className="p-3 flex items-center justify-between gap-4">
                  <div>
                    <div className="font-bold text-[var(--text-primary)]">Default Resolution</div>
                    <div className="text-[11px] text-[var(--text-muted)]">Recommended export target &amp; preview speed</div>
                  </div>
                  <select
                    value={defaultResolution}
                    onChange={(e) => setDefaultResolution(e.target.value)}
                    className="px-2.5 py-1.5 rounded-lg bg-[var(--bg-input)] border border-[var(--border-subtle)] text-xs font-bold text-[var(--text-primary)]"
                  >
                    <option value="1080p">1080p (Full HD)</option>
                    <option value="720p">720p (Fast CPU Balance)</option>
                    <option value="540p">540p (Low-RAM Turbo)</option>
                  </select>
                </div>
              </div>

              {/* AI Mode Selection */}
              <div className="space-y-2.5">
                <div className="text-[11px] font-bold uppercase tracking-wider text-[var(--text-muted)]">
                  Default Highlight Intelligence Mode
                </div>
                <div className="grid grid-cols-1 sm:grid-cols-2 gap-2.5 text-xs">
                  <button
                    type="button"
                    onClick={() => setAiMode('offline')}
                    className={`p-3.5 rounded-xl border text-left transition-all space-y-1 ${
                      aiMode === 'offline'
                        ? 'bg-[var(--accent-soft)] border-[var(--accent-primary)] ring-1 ring-[var(--accent-primary)]/40'
                        : 'bg-[var(--bg-card)] border-[var(--border-subtle)]'
                    }`}
                  >
                    <div className="font-bold text-[var(--text-primary)] flex items-center gap-1.5">
                      <WifiOff className="w-3.5 h-3.5 text-emerald-400" />
                      Offline Multimodal AI
                    </div>
                    <div className="text-[11px] text-[var(--text-muted)] flex items-center gap-1">
                      <ShieldCheck className="w-3 h-3 text-emerald-400 shrink-0" />
                      No API Key Required · 100% Local
                    </div>
                  </button>

                  <button
                    type="button"
                    onClick={() => setAiMode('gemini')}
                    className={`p-3.5 rounded-xl border text-left transition-all space-y-1 ${
                      aiMode === 'gemini'
                        ? 'bg-[var(--accent-soft)] border-[var(--accent-primary)] ring-1 ring-[var(--accent-primary)]/40'
                        : 'bg-[var(--bg-card)] border-[var(--border-subtle)]'
                    }`}
                  >
                    <div className="font-bold text-[var(--text-primary)] flex items-center gap-1.5">
                      <Sparkles className="w-3.5 h-3.5 text-[var(--accent-primary)]" />
                      Gemini Cloud AI
                    </div>
                    <div className="text-[11px] text-[var(--text-muted)]">
                      Uses Google Gemini key + local prosody
                    </div>
                  </button>
                </div>

                {aiMode === 'gemini' && (
                  <div className="rounded-xl bg-[var(--bg-card)] border border-[var(--border-subtle)] p-3 space-y-1.5">
                    <label className="text-[11px] font-bold text-[var(--text-secondary)] flex items-center gap-1.5">
                      <KeyRound className="w-3.5 h-3.5 text-[var(--accent-primary)]" />
                      Google Gemini API Key (Optional — leave blank if already saved)
                    </label>
                    <input
                      type="password"
                      value={geminiApiKey}
                      onChange={(e) => setGeminiApiKey(e.target.value)}
                      placeholder="AIza..."
                      className="w-full px-3 py-2 rounded-lg bg-[var(--bg-input)] border border-[var(--border-subtle)] text-xs text-[var(--text-primary)] outline-none focus:border-[var(--accent-primary)]"
                    />
                  </div>
                )}
              </div>

              {saveError && (
                <div className="px-3 py-2 rounded-xl bg-rose-500/10 border border-rose-500/35 text-xs text-rose-300">
                  {saveError}
                </div>
              )}
            </div>
          )}
        </div>

        {/* Footer */}
        <div className="px-6 py-4 border-t border-[var(--border-subtle)] bg-[var(--bg-card)] flex items-center justify-between gap-3">
          {step > 1 ? (
            <button
              type="button"
              onClick={() => setStep((s) => (s === 3 ? 2 : 1))}
              className="inline-flex items-center gap-1.5 px-3.5 py-2 rounded-xl bg-[var(--bg-surface)] hover:bg-[var(--bg-hover)] border border-[var(--border-subtle)] text-xs font-bold text-[var(--text-secondary)]"
            >
              <ArrowLeft className="w-3.5 h-3.5" />
              Back
            </button>
          ) : (
            <button
              type="button"
              onClick={onClose}
              className="px-3.5 py-2 rounded-xl text-xs font-semibold text-[var(--text-muted)] hover:text-[var(--text-primary)]"
            >
              Dismiss
            </button>
          )}

          {step < 3 ? (
            <button
              type="button"
              disabled={!report}
              onClick={() => setStep((s) => (s === 1 ? 2 : 3))}
              className="inline-flex items-center gap-2 px-5 py-2.5 rounded-xl bg-[var(--accent-primary)] text-[var(--accent-foreground)] text-xs font-extrabold hover:brightness-110 disabled:opacity-50 shadow"
            >
              <span>{step === 1 ? 'Continue to Downloads' : 'Continue to Recommended Settings'}</span>
              <ArrowRight className="w-3.5 h-3.5" />
            </button>
          ) : (
            <button
              type="button"
              disabled={isSaving || !report}
              onClick={handleCompleteOnboarding}
              className="inline-flex items-center gap-2 px-5 py-2.5 rounded-xl bg-[var(--accent-primary)] text-[var(--accent-foreground)] text-xs font-extrabold hover:brightness-110 disabled:opacity-50 shadow-md"
            >
              {isSaving ? (
                <>
                  <Loader2 className="w-3.5 h-3.5 animate-spin" />
                  Saving Settings...
                </>
              ) : (
                <>
                  <CheckCircle2 className="w-4 h-4" />
                  Save Recommended Settings &amp; Enter Studio
                </>
              )}
            </button>
          )}
        </div>
      </div>
    </div>
  );
};
