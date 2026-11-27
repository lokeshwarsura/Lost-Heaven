import React, { useEffect, useRef, useState } from 'react';
import {
  AlertCircle,
  ArrowRight,
  Check,
  CheckCircle2,
  Clock,
  Cpu,
  FileVideo,
  Film,
  FolderOpen,
  KeyRound,
  Layers,
  Link2,
  Loader2,
  Plus,
  RefreshCw,
  ShieldCheck,
  Sparkles,
  Trash2,
  Upload,
  Video,
  WifiOff,
} from 'lucide-react';
import { api } from '../api/client';
import { Project, StudioSettings, YouTubeMetadataInfo } from '../types';

interface HomeViewProps {
  onStartJob: (projectId: number, jobId?: number) => void;
  onJobStarted?: (projectId: number, jobId?: number) => void;
  onOpenProject: (projectId: number) => void;
  onViewProgress: (projectId: number) => void;
  onViewLibrary: () => void;
  isOfflineMultimodal?: boolean;
  studioSettings?: Partial<StudioSettings> | null;
}

interface LocalVideoPreviewMeta {
  file: File;
  thumbnailDataUrl: string | null;
  durationSec: number;
  width: number;
  height: number;
  sizeBytes: number;
}

const DIRECTOR_CHIPS = [
  'Storytime Arc',
  'Hot Takes & Debate',
  'Funny / High Energy',
  'Key Takeaways',
  'Gameplay Climax',
];

const ASPECT_OPTIONS: Array<{ value: string; label: string; sub: string }> = [
  { value: '9:16', label: '9:16', sub: 'Shorts / Reels' },
  { value: '16:9', label: '16:9', sub: 'Widescreen' },
  { value: '1:1', label: '1:1', sub: 'Square' },
  { value: '4:5', label: '4:5', sub: 'Social Feed' },
];

const LENGTH_OPTIONS = ['Auto', '15s', '30s', '45s', '60s'];

const CLIP_COUNTS = [3, 5, 8, 10];

const FRAMING_OPTIONS: Array<{ value: string; label: string; hint: string }> = [
  { value: 'smart_auto', label: 'Smart Auto-Frame', hint: 'AI chooses best layout' },
  { value: 'auto_track', label: 'Auto-Track Speaker', hint: 'Dynamic face follow' },
  { value: 'gameplay_webcam_pip', label: 'Gameplay + Webcam PiP', hint: 'Streamer overlay' },
  { value: 'fit_blur', label: 'Fit + Blurred Backdrop', hint: 'Full frame widescreen' },
];

const LANGUAGES = [
  'As spoken',
  'Hinglish',
  'English',
  'Hindi',
  'Spanish',
  'French',
  'German',
  'Arabic',
  'Portuguese',
  'Japanese',
];

function formatDurationClock(sec: number): string {
  const clean = Math.max(0, Math.floor(sec || 0));
  const h = Math.floor(clean / 3600);
  const m = Math.floor((clean % 3600) / 60);
  const s = clean % 60;
  if (h > 0) return `${h}:${m.toString().padStart(2, '0')}:${s.toString().padStart(2, '0')}`;
  return `${m}:${s.toString().padStart(2, '0')}`;
}

function formatBytesCompact(bytes: number): string {
  if (!bytes || bytes <= 0) return '—';
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(0)} KB`;
  if (bytes < 1024 ** 3) return `${(bytes / (1024 * 1024)).toFixed(1)} MB`;
  return `${(bytes / 1024 ** 3).toFixed(2)} GB`;
}

function estimateProcessingWindow(durationSec: number, hardwareMode: string): string {
  const dur = Math.max(15, durationSec || 60);
  // Batched whisper + sequential ffmpeg scan typically takes ~0.12x to 0.35x realtime
  const low = Math.max(8, Math.round(dur * (hardwareMode === 'gpu' ? 0.12 : 0.25)));
  const high = Math.max(low + 10, Math.round(dur * (hardwareMode === 'gpu' ? 0.28 : 0.55)));
  if (high < 90) return `~${low}-${high}s`;
  return `~${Math.max(1, Math.round(low / 60))}-${Math.max(2, Math.round(high / 60))} min`;
}

function normalizeYouTubeUrl(raw: string): string {
  const trimmed = raw.trim();
  if (!trimmed) return '';
  const withProto = /^https?:\/\//i.test(trimmed)
    ? trimmed
    : /^(www\.)?(youtube\.com|youtu\.be)\//i.test(trimmed)
      ? `https://${trimmed}`
      : trimmed;

  try {
    const parsed = new URL(withProto);
    const host = parsed.hostname.toLowerCase();
    let vidId = '';
    if (host === 'youtu.be' || host.endsWith('.youtu.be')) {
      const parts = parsed.pathname.split('/').filter(Boolean);
      if (parts.length > 0) vidId = parts[0];
    } else if (host.includes('youtube.com') || host.includes('youtube-nocookie.com')) {
      const vParam = parsed.searchParams.get('v');
      if (vParam) {
        vidId = vParam;
      } else {
        const parts = parsed.pathname.split('/').filter(Boolean);
        for (const pfx of ['shorts', 'live', 'embed', 'v']) {
          const idx = parts.indexOf(pfx);
          if (idx !== -1 && idx + 1 < parts.length) {
            vidId = parts[idx + 1];
            break;
          }
        }
      }
    }
    if (vidId) {
      return `https://www.youtube.com/watch?v=${vidId}`;
    }
    return parsed.toString();
  } catch {
    return withProto;
  }
}

function isValidYouTubeUrl(raw: string): boolean {
  const norm = normalizeYouTubeUrl(raw).toLowerCase();
  if (!norm) return false;
  return (
    (norm.startsWith('http://') || norm.startsWith('https://')) &&
    (norm.includes('youtube.com/') || norm.includes('youtu.be/'))
  );
}

async function extractLocalVideoPreview(file: File): Promise<LocalVideoPreviewMeta> {
  const objectUrl = URL.createObjectURL(file);
  try {
    return await new Promise<LocalVideoPreviewMeta>((resolve) => {
      const video = document.createElement('video');
      video.preload = 'metadata';
      video.muted = true;
      video.playsInline = true;

      let settled = false;
      const finish = (thumb: string | null, durationSec: number, width: number, height: number) => {
        if (settled) return;
        settled = true;
        video.removeAttribute('src');
        video.load();
        resolve({
          file,
          thumbnailDataUrl: thumb,
          durationSec: Number.isFinite(durationSec) ? durationSec : 0,
          width: width || 1920,
          height: height || 1080,
          sizeBytes: file.size,
        });
      };

      const timeoutId = window.setTimeout(() => {
        finish(null, video.duration || 0, video.videoWidth || 1920, video.videoHeight || 1080);
      }, 3500);

      video.onloadedmetadata = () => {
        const dur = Number.isFinite(video.duration) ? video.duration : 0;
        const seekTarget = dur > 1.5 ? Math.min(dur * 0.15, 6.0) : 0.1;
        try {
          video.currentTime = seekTarget;
        } catch {
          window.clearTimeout(timeoutId);
          finish(null, dur, video.videoWidth, video.videoHeight);
        }
      };

      video.onseeked = () => {
        window.clearTimeout(timeoutId);
        let dataUrl: string | null = null;
        try {
          const vw = video.videoWidth || 640;
          const vh = video.videoHeight || 360;
          const targetW = 320;
          const targetH = Math.max(90, Math.round((vh / vw) * targetW));
          const canvas = document.createElement('canvas');
          canvas.width = targetW;
          canvas.height = targetH;
          const ctx = canvas.getContext('2d');
          if (ctx) {
            ctx.drawImage(video, 0, 0, targetW, targetH);
            dataUrl = canvas.toDataURL('image/jpeg', 0.8);
          }
        } catch {
          dataUrl = null;
        }
        finish(dataUrl, video.duration || 0, video.videoWidth, video.videoHeight);
      };

      video.onerror = () => {
        window.clearTimeout(timeoutId);
        finish(null, 0, 1920, 1080);
      };

      video.src = objectUrl;
    });
  } finally {
    URL.revokeObjectURL(objectUrl);
  }
}

export const HomeView: React.FC<HomeViewProps> = ({
  onStartJob,
  onJobStarted,
  onOpenProject,
  onViewProgress,
  onViewLibrary,
  isOfflineMultimodal = false,
  studioSettings = null,
}) => {
  const notifyJobStarted = (projectId: number, jobId?: number) => {
    onJobStarted?.(projectId, jobId);
    onStartJob(projectId, jobId);
  };
  // Media source states
  const [selectedFile, setSelectedFile] = useState<File | null>(null);
  const [localMeta, setLocalMeta] = useState<LocalVideoPreviewMeta | null>(null);
  const [inspectingLocal, setInspectingLocal] = useState(false);

  const [ytUrl, setYtUrl] = useState('');
  const [ytInfo, setYtInfo] = useState<YouTubeMetadataInfo | null>(null);
  const [fetchingYtInfo, setFetchingYtInfo] = useState(false);
  const [ytError, setYtError] = useState<string | null>(null);

  const [batchFiles, setBatchFiles] = useState<File[]>([]);
  const [isDragging, setIsDragging] = useState(false);
  const fileInputRef = useRef<HTMLInputElement>(null);
  const submitLockRef = useRef<boolean>(false);

  // Pipeline Configuration states
  const [aspectRatio, setAspectRatio] = useState('9:16');
  const [durationPreset, setDurationPreset] = useState('Auto');
  const [clipCount, setClipCount] = useState(5);
  const [defaultFraming, setDefaultFraming] = useState('smart_auto');
  const [captionLanguage, setCaptionLanguage] = useState('As spoken');
  const [hardwareMode, setHardwareMode] = useState<'gpu' | 'cpu'>('gpu');
  const [topicFocus, setTopicFocus] = useState('');

  // AI Engine Switcher state
  const deriveInitialAiMode = (): 'gemini' | 'offline' => {
    if (studioSettings?.ai_mode === 'gemini' || studioSettings?.ai_mode === 'offline') {
      return studioSettings.ai_mode;
    }
    if (studioSettings?.effective_ai_mode === 'offline' || isOfflineMultimodal) {
      return 'offline';
    }
    return studioSettings?.gemini_configured ? 'gemini' : 'offline';
  };
  const [aiModeChoice, setAiModeChoice] = useState<'gemini' | 'offline'>(deriveInitialAiMode);
  const [geminiConfigured, setGeminiConfigured] = useState<boolean>(Boolean(studioSettings?.gemini_configured));
  const [inlineKeyInput, setInlineKeyInput] = useState('');
  const [inlineKeyStatus, setInlineKeyStatus] = useState<{ type: 'ok' | 'err'; text: string } | null>(null);
  const [savingKey, setSavingKey] = useState(false);
  const [switchingMode, setSwitchingMode] = useState(false);

  // Submission & Projects state
  const [recentProjects, setRecentProjects] = useState<Project[]>([]);
  const [totalProjectsCount, setTotalProjectsCount] = useState(0);
  const [loadingProjects, setLoadingProjects] = useState(true);
  const [projectsError, setProjectsError] = useState<string | null>(null);
  const [isSubmitting, setIsSubmitting] = useState(false);
  const [submitError, setSubmitError] = useState<string | null>(null);
  const [showEngineChoiceDialog, setShowEngineChoiceDialog] = useState(false);
  const [dialogApiKey, setDialogApiKey] = useState('');
  const [dialogKeyError, setDialogKeyError] = useState<string | null>(null);
  const [dialogTestingKey, setDialogTestingKey] = useState(false);

  // Sync studio settings props & initial projects fetch
  useEffect(() => {
    if (studioSettings) {
      setGeminiConfigured(Boolean(studioSettings.gemini_configured));
      if (studioSettings.ai_mode === 'gemini' || studioSettings.ai_mode === 'offline') {
        setAiModeChoice(studioSettings.ai_mode);
      } else if (studioSettings.effective_ai_mode === 'offline') {
        setAiModeChoice('offline');
      }
      if (studioSettings.hardware_acceleration === 'cpu') {
        setHardwareMode('cpu');
      }
    }
  }, [studioSettings]);

  const loadRecentProjects = async () => {
    setLoadingProjects(true);
    setProjectsError(null);
    try {
      const res = await api.getProjectsList();
      setRecentProjects((res.projects || []).slice(0, 4));
      setTotalProjectsCount(res.projects_count ?? res.projects?.length ?? 0);
    } catch (err: any) {
      console.error('Failed to load recent projects:', err);
      setRecentProjects([]);
      setTotalProjectsCount(0);
      setProjectsError(err?.message || 'Unable to load recent projects.');
    } finally {
      setLoadingProjects(false);
    }
  };

  useEffect(() => {
    loadRecentProjects();
    api
      .getSettings()
      .then((s) => {
        setGeminiConfigured(Boolean(s.gemini_configured));
        if (s.ai_mode === 'gemini' || s.ai_mode === 'offline') {
          setAiModeChoice(s.ai_mode);
        } else if (s.effective_ai_mode === 'offline') {
          setAiModeChoice('offline');
        }
      })
      .catch(() => {});
  }, []);

  const handleSelectAiEngine = async (mode: 'gemini' | 'offline') => {
    setAiModeChoice(mode);
    setInlineKeyStatus(null);
    setSwitchingMode(true);
    try {
      await api.updateSystemSettings({ ai_mode: mode });
      window.dispatchEvent(new CustomEvent('lh-settings-updated'));
    } catch (err) {
      console.warn('Could not persist AI engine switch:', err);
    } finally {
      setSwitchingMode(false);
    }
  };

  const handleSaveInlineKey = async () => {
    const trimmed = inlineKeyInput.trim();
    if (!trimmed) {
      setInlineKeyStatus({ type: 'err', text: 'Paste your Google Gemini API key (AIza...).' });
      return;
    }
    setSavingKey(true);
    setInlineKeyStatus(null);
    try {
      const testRes = await api.testGeminiModels(trimmed);
      const anyOk = (testRes.models || []).some((m) => m.status === 'OK' || m.status === '503 High Demand');
      const anyInvalid = (testRes.models || []).some((m) => m.status === 'Invalid Key');
      if (anyInvalid && !anyOk) {
        setInlineKeyStatus({ type: 'err', text: 'Key rejected by Gemini API. Check that Generative Language API is enabled.' });
        return;
      }
      await api.updateSystemSettings({
        gemini_api_key: trimmed,
        ai_mode: 'gemini',
        ai_provider: 'gemini',
        gemini_enabled: true,
        gemini_boost_enabled: true,
      });
      setGeminiConfigured(true);
      setAiModeChoice('gemini');
      setInlineKeyInput('');
      setInlineKeyStatus({ type: 'ok', text: 'Key verified & saved!' });
      window.dispatchEvent(new CustomEvent('lh-settings-updated'));
    } catch (err: any) {
      setInlineKeyStatus({ type: 'err', text: err?.message || 'Failed to validate Gemini API key' });
    } finally {
      setSavingKey(false);
    }
  };

  const handleLocalFilePicked = async (files: FileList | File[]) => {
    const arr = Array.from(files).filter((f) =>
      /\.(mp4|mov|mkv|webm|avi)$/i.test(f.name) || f.type.startsWith('video/')
    );
    if (arr.length === 0) {
      setSubmitError('Please select a supported video file (.mp4, .mov, .mkv, .webm).');
      return;
    }
    setSubmitError(null);
    setYtError(null);

    if (arr.length === 1 && !selectedFile) {
      const f = arr[0];
      setSelectedFile(f);
      setYtUrl('');
      setYtInfo(null);
      setInspectingLocal(true);
      try {
        const meta = await extractLocalVideoPreview(f);
        setLocalMeta(meta);
      } finally {
        setInspectingLocal(false);
      }
    } else if (!selectedFile) {
      const [first, ...rest] = arr;
      setSelectedFile(first);
      setBatchFiles((prev) => [...prev, ...rest]);
      setYtUrl('');
      setYtInfo(null);
      setInspectingLocal(true);
      try {
        const meta = await extractLocalVideoPreview(first);
        setLocalMeta(meta);
      } finally {
        setInspectingLocal(false);
      }
    } else {
      setBatchFiles((prev) => [...prev, ...arr]);
    }
  };

  const handleClearMedia = () => {
    setSelectedFile(null);
    setLocalMeta(null);
    setYtUrl('');
    setYtInfo(null);
    setYtError(null);
    setBatchFiles([]);
    setSubmitError(null);
    if (fileInputRef.current) fileInputRef.current.value = '';
  };

  const handleQueueCurrentToBatch = () => {
    if (!selectedFile) return;
    setBatchFiles((prev) => {
      if (prev.some((x) => x.name === selectedFile.name && x.size === selectedFile.size)) return prev;
      return [...prev, selectedFile];
    });
    setSelectedFile(null);
    setLocalMeta(null);
    if (fileInputRef.current) fileInputRef.current.value = '';
  };

  const handleFetchYouTubeInfo = async (explicitUrl?: string) => {
    const raw = (explicitUrl ?? ytUrl).trim();
    if (!raw) return;
    const normalized = normalizeYouTubeUrl(raw);
    setFetchingYtInfo(true);
    setYtError(null);
    setSubmitError(null);
    try {
      const info = await api.getYouTubeInfo(normalized);
      setYtInfo(info);
      setYtUrl(info.webpage_url || normalized);
      setSelectedFile(null);
      setLocalMeta(null);
    } catch (err: any) {
      const msg = err?.message || 'Unable to fetch video metadata for that URL.';
      setYtError(msg);
      setSubmitError(msg);
      setYtInfo(null);
    } finally {
      setFetchingYtInfo(false);
    }
  };

  const hasValidMediaSource = Boolean(
    selectedFile || ytInfo || isValidYouTubeUrl(ytUrl) || batchFiles.length > 0
  );

  const executeIngestion = async (chosenAiMode: 'gemini' | 'offline') => {
    if (submitLockRef.current) return;
    submitLockRef.current = true;
    setIsSubmitting(true);
    setSubmitError(null);
    setYtError(null);
    try {
      const allBatchFiles = selectedFile && batchFiles.length > 0 ? [selectedFile, ...batchFiles] : batchFiles;
      if (allBatchFiles.length > 0 && !ytInfo && !isValidYouTubeUrl(ytUrl)) {
        let firstPid = 0;
        let firstJobId: number | undefined;
        for (let i = 0; i < allBatchFiles.length; i++) {
          const res = await api.uploadVideo(
            allBatchFiles[i],
            aspectRatio,
            durationPreset,
            clipCount,
            topicFocus.trim() || undefined,
            captionLanguage,
            hardwareMode,
            chosenAiMode,
            defaultFraming,
            allBatchFiles.length > 1
          );
          if (i === 0) {
            firstPid = res.project_id;
            firstJobId = res.job_id ?? res.project_id;
          }
        }
        if (firstPid) {
          notifyJobStarted(firstPid, firstJobId);
          return;
        }
      }

      if (selectedFile) {
        const res = await api.uploadVideo(
          selectedFile,
          aspectRatio,
          durationPreset,
          clipCount,
          topicFocus.trim() || undefined,
          captionLanguage,
          hardwareMode,
          chosenAiMode,
          defaultFraming,
          false
        );
        notifyJobStarted(res.project_id, res.job_id ?? res.project_id);
        return;
      }

      const targetYt = normalizeYouTubeUrl(ytInfo?.webpage_url || ytUrl);
      if (targetYt && isValidYouTubeUrl(targetYt)) {
        const res = await api.importYouTube(
          targetYt,
          aspectRatio,
          durationPreset,
          clipCount,
          topicFocus.trim() || undefined,
          captionLanguage,
          hardwareMode,
          chosenAiMode,
          defaultFraming,
          false
        );
        notifyJobStarted(res.project_id, res.job_id ?? res.project_id);
        return;
      }

      setSubmitError('Please select a local video file or paste a valid YouTube link.');
    } catch (err: any) {
      const msg = err?.message || 'Failed to launch extraction job.';
      setSubmitError(msg);
      console.error('Analyze & Generate Clips failed:', err);
    } finally {
      submitLockRef.current = false;
      setIsSubmitting(false);
    }
  };

  const handlePrimaryAnalyzeClick = async () => {
    if (!hasValidMediaSource || isSubmitting || submitLockRef.current) return;
    setSubmitError(null);
    // If Gemini mode is chosen but no API key is saved yet, prompt cleanly instead of silently failing
    if (aiModeChoice === 'gemini' && !geminiConfigured) {
      setDialogApiKey('');
      setDialogKeyError(null);
      setShowEngineChoiceDialog(true);
      return;
    }
    try {
      await api.updateSystemSettings({ ai_mode: aiModeChoice }).catch(() => {});
      await executeIngestion(aiModeChoice);
    } catch (err: any) {
      setSubmitError(err?.message || 'Failed to start ingestion job.');
      setIsSubmitting(false);
    }
  };

  const handleDialogStartGemini = async () => {
    const clean = dialogApiKey.trim();
    if (!clean) {
      setDialogKeyError('Paste your Google Gemini API key (starts with AIza...).');
      return;
    }
    setDialogTestingKey(true);
    setDialogKeyError(null);
    try {
      const testRes = await api.testGeminiModels(clean);
      const anyOk = (testRes.models || []).some((m) => m.status === 'OK' || m.status === '503 High Demand');
      const anyInvalid = (testRes.models || []).some((m) => m.status === 'Invalid Key');
      if (anyInvalid && !anyOk) {
        setDialogKeyError('Key rejected by Gemini API. Verify your key in Google AI Studio.');
        return;
      }
      await api.updateSystemSettings({
        gemini_api_key: clean,
        ai_mode: 'gemini',
        ai_provider: 'gemini',
        gemini_enabled: true,
        gemini_boost_enabled: true,
      });
      setGeminiConfigured(true);
      setAiModeChoice('gemini');
      setShowEngineChoiceDialog(false);
      window.dispatchEvent(new CustomEvent('lh-settings-updated'));
      await executeIngestion('gemini');
    } catch (err: any) {
      setDialogKeyError(err?.message || 'Could not validate Gemini key.');
    } finally {
      setDialogTestingKey(false);
    }
  };

  const handleDialogStartOffline = async () => {
    setShowEngineChoiceDialog(false);
    setAiModeChoice('offline');
    await api.updateSystemSettings({ ai_mode: 'offline' }).catch(() => {});
    window.dispatchEvent(new CustomEvent('lh-settings-updated'));
    await executeIngestion('offline');
  };

  const activeDurationSec = localMeta?.durationSec || ytInfo?.duration || 0;
  const activeResolution = localMeta
    ? `${localMeta.width}x${localMeta.height}`
    : ytInfo?.resolution || '1080p';
  const activeSizeText = localMeta
    ? formatBytesCompact(localMeta.sizeBytes)
    : ytInfo?.filesize_approx
      ? `~${formatBytesCompact(ytInfo.filesize_approx)}`
      : 'Remote Stream';
  const estProcessingText = activeDurationSec > 0 ? estimateProcessingWindow(activeDurationSec, hardwareMode) : '~15-45s';

  return (
    <div className="h-full overflow-y-auto px-5 py-5 lg:px-8 lg:py-6">
      <div className="max-w-7xl mx-auto space-y-6">
        {/* Compact Workstation Header */}
        <header className="flex flex-wrap items-center justify-between gap-4 border-b border-[var(--border-subtle)] pb-4">
          <div className="space-y-1">
            <div className="flex items-center gap-2">
              <span className="inline-flex items-center gap-1.5 px-2.5 py-0.5 rounded-full text-[11px] font-bold uppercase tracking-wider bg-[var(--accent-soft)] text-[var(--accent-primary)] border border-[var(--border-subtle)]">
                <Sparkles className="w-3 h-3" />
                Studio Ingestion Deck
              </span>
              <span className="text-[11px] font-medium text-[var(--text-muted)]">
                Local-first faster-whisper VAD · Frame-accurate FFmpeg
              </span>
            </div>
            <h1 className="text-2xl lg:text-3xl font-extrabold tracking-tight text-[var(--text-primary)]">
              Create Viral Shorts From Any Long-Form Video
            </h1>
          </div>

          <div className="flex items-center gap-2.5 shrink-0">
            <div className="px-3.5 py-1.5 rounded-xl bg-[var(--bg-surface)] border border-[var(--border-subtle)] flex items-center gap-2.5 text-xs whitespace-nowrap">
              <Cpu className="w-3.5 h-3.5 text-[var(--accent-primary)] shrink-0" />
              <span className="font-semibold text-[var(--text-secondary)] whitespace-nowrap">
                {hardwareMode === 'gpu' ? 'GPU Hardware Acceleration' : 'CPU Safe Mode'}
              </span>
              <button
                type="button"
                onClick={() => setHardwareMode((m) => (m === 'gpu' ? 'cpu' : 'gpu'))}
                className="px-2 py-0.5 rounded bg-[var(--bg-elevated)] hover:bg-[var(--bg-hover)] text-[10px] font-bold text-[var(--text-primary)] uppercase transition-colors shrink-0"
                title="Toggle GPU / CPU acceleration"
              >
                Switch
              </button>
            </div>
          </div>
        </header>

        {/* ================================================================ */}
        {/* 2-COLUMN STUDIO INGESTION DECK                                   */}
        {/* ================================================================ */}
        <div className="grid grid-cols-1 lg:grid-cols-12 gap-5 items-stretch">
          {/* LEFT COLUMN: Source Media Drop & URL Import (6 cols) */}
          <section className="lg:col-span-6 bg-[var(--bg-surface)] border border-[var(--border-subtle)] rounded-2xl p-5 flex flex-col justify-between gap-4 shadow-sm">
            <div className="space-y-4">
              <div className="flex items-center justify-between">
                <div>
                  <h2 className="text-sm font-bold uppercase tracking-wider text-[var(--text-primary)] flex items-center gap-2">
                    <FileVideo className="w-4 h-4 text-[var(--accent-primary)]" />
                    Source Media Drop &amp; URL Import
                  </h2>
                  <p className="text-xs text-[var(--text-muted)] mt-0.5">
                    Drop a video file or paste a YouTube link for instant pre-flight inspection
                  </p>
                </div>
                {(selectedFile || ytInfo || batchFiles.length > 0) && (
                  <button
                    type="button"
                    onClick={handleClearMedia}
                    className="inline-flex items-center gap-1 text-xs font-semibold text-[var(--text-muted)] hover:text-rose-400 transition-colors"
                  >
                    <Trash2 className="w-3.5 h-3.5" />
                    Clear
                  </button>
                )}
              </div>

              <input
                ref={fileInputRef}
                type="file"
                multiple
                accept=".mp4,.mov,.mkv,.webm,.avi,video/*"
                className="hidden"
                onChange={(e) => e.target.files && handleLocalFilePicked(e.target.files)}
              />

              {/* Either Pre-Flight Media Preview Card OR Compact Unified Dropzone */}
              {selectedFile || ytInfo ? (
                <div className="rounded-xl bg-[var(--bg-card)] border border-[var(--border-subtle)] overflow-hidden">
                  <div className="flex flex-col sm:flex-row gap-4 p-3.5 items-stretch">
                    {/* Crisp Visual Thumbnail */}
                    <div className="relative w-full sm:w-44 aspect-video shrink-0 rounded-lg overflow-hidden bg-black/50 border border-[var(--border-subtle)] flex items-center justify-center">
                      {inspectingLocal ? (
                        <div className="flex flex-col items-center gap-1.5 text-xs text-[var(--text-muted)]">
                          <Loader2 className="w-5 h-5 animate-spin text-[var(--accent-primary)]" />
                          <span>Reading metadata...</span>
                        </div>
                      ) : localMeta?.thumbnailDataUrl ? (
                        <img
                          src={localMeta.thumbnailDataUrl}
                          alt={selectedFile?.name || 'Video frame'}
                          className="w-full h-full object-cover"
                        />
                      ) : ytInfo?.thumbnail ? (
                        <img
                          src={ytInfo.thumbnail}
                          alt={ytInfo.title}
                          className="w-full h-full object-cover"
                        />
                      ) : (
                        <Film className="w-8 h-8 text-[var(--text-muted)]" />
                      )}

                      {activeDurationSec > 0 && (
                        <span className="absolute bottom-1.5 right-1.5 px-1.5 py-0.5 rounded bg-black/80 text-white font-mono text-[10px] font-bold">
                          {formatDurationClock(activeDurationSec)}
                        </span>
                      )}
                      <span className="absolute top-1.5 left-1.5 px-1.5 py-0.5 rounded bg-black/75 text-[10px] font-bold text-[var(--accent-primary)] uppercase">
                        {selectedFile ? 'Local' : 'YouTube'}
                      </span>
                    </div>

                    {/* Metadata Telemetry */}
                    <div className="flex-1 min-w-0 flex flex-col justify-between gap-2">
                      <div>
                        <h3 className="text-sm font-bold text-[var(--text-primary)] truncate" title={selectedFile?.name || ytInfo?.title}>
                          {selectedFile ? selectedFile.name : ytInfo?.title}
                        </h3>
                        {ytInfo?.uploader && (
                          <p className="text-xs text-[var(--text-muted)] truncate mt-0.5">
                            Channel: {ytInfo.uploader}
                          </p>
                        )}
                      </div>

                      <div className="grid grid-cols-3 gap-2 text-[11px]">
                        <div className="px-2 py-1.5 rounded-lg bg-[var(--bg-input)] border border-[var(--border-subtle)]">
                          <div className="text-[10px] uppercase text-[var(--text-muted)] font-semibold">Resolution</div>
                          <div className="font-bold text-[var(--text-primary)] truncate">{activeResolution}</div>
                        </div>
                        <div className="px-2 py-1.5 rounded-lg bg-[var(--bg-input)] border border-[var(--border-subtle)]">
                          <div className="text-[10px] uppercase text-[var(--text-muted)] font-semibold">File Size</div>
                          <div className="font-bold text-[var(--text-primary)] truncate">{activeSizeText}</div>
                        </div>
                        <div className="px-2 py-1.5 rounded-lg bg-[var(--bg-input)] border border-[var(--border-subtle)]">
                          <div className="text-[10px] uppercase text-[var(--text-muted)] font-semibold">Est. Time</div>
                          <div className="font-bold text-[var(--accent-primary)] truncate">{estProcessingText}</div>
                        </div>
                      </div>

                      <div className="flex flex-wrap items-center gap-2 pt-0.5">
                        <button
                          type="button"
                          onClick={handleClearMedia}
                          className="px-2.5 py-1 rounded-lg bg-[var(--bg-elevated)] hover:bg-[var(--bg-hover)] border border-[var(--border-subtle)] text-[11px] font-semibold text-[var(--text-secondary)] transition-colors"
                        >
                          Change / Clear
                        </button>
                        {selectedFile && (
                          <button
                            type="button"
                            onClick={handleQueueCurrentToBatch}
                            className="inline-flex items-center gap-1 px-2.5 py-1 rounded-lg bg-[var(--bg-elevated)] hover:bg-[var(--bg-hover)] border border-[var(--border-subtle)] text-[11px] font-semibold text-[var(--text-primary)] transition-colors"
                          >
                            <Plus className="w-3 h-3 text-[var(--accent-primary)]" />
                            Queue Multi-Video Batch
                          </button>
                        )}
                        <button
                          type="button"
                          onClick={() => fileInputRef.current?.click()}
                          className="inline-flex items-center gap-1 px-2.5 py-1 rounded-lg bg-[var(--bg-elevated)] hover:bg-[var(--bg-hover)] border border-[var(--border-subtle)] text-[11px] font-semibold text-[var(--text-muted)] hover:text-[var(--text-primary)] transition-colors"
                        >
                          <Layers className="w-3 h-3" />
                          + Add More
                        </button>
                      </div>
                    </div>
                  </div>
                </div>
              ) : (
                <div
                  role="button"
                  tabIndex={0}
                  onClick={() => fileInputRef.current?.click()}
                  onKeyDown={(e) => {
                    if (e.key === 'Enter' || e.key === ' ') {
                      e.preventDefault();
                      fileInputRef.current?.click();
                    }
                  }}
                  onDragOver={(e) => {
                    e.preventDefault();
                    setIsDragging(true);
                  }}
                  onDragLeave={() => setIsDragging(false)}
                  onDrop={(e) => {
                    e.preventDefault();
                    setIsDragging(false);
                    if (e.dataTransfer.files?.length) {
                      handleLocalFilePicked(e.dataTransfer.files);
                    }
                  }}
                  className={`rounded-xl border-2 border-dashed px-5 py-7 flex flex-col items-center justify-center text-center cursor-pointer transition-all ${
                    isDragging
                      ? 'border-[var(--accent-primary)] bg-[var(--accent-soft)]'
                      : 'border-[var(--border-subtle)] bg-[var(--bg-card)]/70 hover:border-[var(--accent-primary)]/70 hover:bg-[var(--bg-card)]'
                  }`}
                >
                  <div className="w-11 h-11 rounded-xl bg-[var(--bg-elevated)] border border-[var(--border-subtle)] flex items-center justify-center mb-2.5">
                    <Upload className="w-5 h-5 text-[var(--accent-primary)]" />
                  </div>
                  <p className="text-sm font-bold text-[var(--text-primary)]">
                    Drop video file(s) here or click to browse
                  </p>
                  <p className="text-xs text-[var(--text-muted)] mt-1">
                    Supports MP4, MOV, MKV, WEBM · Multi-file batch drop supported
                  </p>
                </div>
              )}

              {/* Staged Batch Queue List */}
              {batchFiles.length > 0 && (
                <div className="rounded-xl bg-[var(--bg-card)] border border-[var(--border-subtle)] p-3 space-y-2">
                  <div className="flex items-center justify-between text-xs font-bold text-[var(--text-primary)]">
                    <span className="flex items-center gap-1.5">
                      <Layers className="w-3.5 h-3.5 text-[var(--accent-primary)]" />
                      Multi-Video Batch Queue ({batchFiles.length + (selectedFile ? 1 : 0)} videos)
                    </span>
                    <button
                      type="button"
                      onClick={() => fileInputRef.current?.click()}
                      className="text-[11px] font-semibold text-[var(--accent-primary)] hover:underline"
                    >
                      + Add File
                    </button>
                  </div>
                  <div className="flex flex-wrap gap-1.5 max-h-20 overflow-y-auto">
                    {batchFiles.map((bf, idx) => (
                      <span
                        key={`${bf.name}-${idx}`}
                        className="inline-flex items-center gap-1.5 px-2 py-1 rounded-lg bg-[var(--bg-input)] border border-[var(--border-subtle)] text-[11px] font-medium text-[var(--text-secondary)] max-w-[220px]"
                      >
                        <span className="truncate">{bf.name}</span>
                        <button
                          type="button"
                          onClick={() => setBatchFiles((prev) => prev.filter((_, i) => i !== idx))}
                          className="text-[var(--text-muted)] hover:text-rose-400"
                        >
                          ×
                        </button>
                      </span>
                    ))}
                  </div>
                </div>
              )}

              {/* Integrated YouTube URL Bar */}
              <div className="space-y-1.5">
                <label className="text-[11px] font-bold uppercase tracking-wider text-[var(--text-muted)] flex items-center gap-1.5">
                  <Video className="w-3.5 h-3.5 text-rose-500" />
                  Or Import Directly From YouTube URL
                </label>
                <div className="flex items-center gap-2">
                  <div className="relative flex-1">
                    <Link2 className="w-4 h-4 text-[var(--text-muted)] absolute left-3 top-1/2 -translate-y-1/2" />
                    <input
                      type="url"
                      value={ytUrl}
                      onChange={(e) => {
                        setYtUrl(e.target.value);
                        setYtError(null);
                        if (ytInfo && e.target.value.trim() !== ytInfo.webpage_url) {
                          setYtInfo(null);
                        }
                      }}
                      onKeyDown={(e) => {
                        if (e.key === 'Enter' && ytUrl.trim()) {
                          e.preventDefault();
                          handleFetchYouTubeInfo();
                        }
                      }}
                      placeholder="https://www.youtube.com/watch?v=..."
                      className="w-full pl-9 pr-3 py-2.5 rounded-xl bg-[var(--bg-input)] border border-[var(--border-subtle)] text-xs text-[var(--text-primary)] placeholder:text-[var(--text-muted)] focus:outline-none focus:border-[var(--accent-primary)]"
                    />
                  </div>
                  <button
                    type="button"
                    onClick={() => handleFetchYouTubeInfo()}
                    disabled={!ytUrl.trim() || fetchingYtInfo}
                    className="px-3.5 py-2.5 rounded-xl bg-[var(--bg-elevated)] hover:bg-[var(--bg-hover)] border border-[var(--border-subtle)] text-xs font-bold text-[var(--text-primary)] disabled:opacity-45 transition-colors flex items-center gap-1.5 shrink-0"
                  >
                    {fetchingYtInfo ? (
                      <>
                        <Loader2 className="w-3.5 h-3.5 animate-spin" />
                        Fetching...
                      </>
                    ) : (
                      <>
                        <RefreshCw className="w-3.5 h-3.5 text-[var(--accent-primary)]" />
                        Fetch Info
                      </>
                    )}
                  </button>
                </div>
                {ytError && (
                  <p className="text-[11px] text-rose-400 flex items-center gap-1 pt-0.5">
                    <AlertCircle className="w-3.5 h-3.5 shrink-0" />
                    {ytError}
                  </p>
                )}
              </div>
            </div>

            {/* Bottom Gated Primary CTA */}
            <div className="pt-2 border-t border-[var(--border-subtle)] space-y-2">
              {submitError && (
                <div
                  role="alert"
                  className="px-3.5 py-2.5 rounded-xl bg-red-950/85 border border-red-500 text-xs font-semibold text-red-100 flex items-center gap-2 shadow-sm"
                >
                  <AlertCircle className="w-4 h-4 shrink-0 text-red-400" />
                  <span className="break-words">{submitError}</span>
                </div>
              )}

              <button
                type="button"
                disabled={!hasValidMediaSource || isSubmitting}
                onClick={handlePrimaryAnalyzeClick}
                className="w-full py-3.5 px-5 rounded-xl font-extrabold text-sm tracking-wide flex items-center justify-center gap-2 shadow-md transition-all bg-[var(--accent-primary)] text-[var(--accent-foreground)] hover:brightness-110 disabled:opacity-40 disabled:cursor-not-allowed disabled:hover:brightness-100"
              >
                {isSubmitting ? (
                  <>
                    <Loader2 className="w-4 h-4 animate-spin" />
                    Uploading &amp; Initializing Pipeline...
                  </>
                ) : (
                  <>
                    <Sparkles className="w-4 h-4" />
                    {batchFiles.length > 0
                      ? `Analyze & Queue ${batchFiles.length + (selectedFile ? 1 : 0)} Videos`
                      : 'Analyze & Generate Clips'}
                    <ArrowRight className="w-4 h-4" />
                  </>
                )}
              </button>

              <p className="text-center text-[11px] text-[var(--text-muted)]">
                {!hasValidMediaSource
                  ? 'Select a video file or paste a YouTube link to begin'
                  : aiModeChoice === 'gemini'
                    ? `Ready · Will analyze with Gemini Cloud AI · Target ${clipCount} clips (${aspectRatio})`
                    : `Ready · Will analyze 100% locally (Offline Multimodal AI) · Target ${clipCount} clips (${aspectRatio})`}
              </p>
            </div>
          </section>

          {/* RIGHT COLUMN: AI Engine & Pipeline Configuration (6 cols) */}
          <section className="lg:col-span-6 bg-[var(--bg-surface)] border border-[var(--border-subtle)] rounded-2xl p-5 flex flex-col justify-between gap-4 shadow-sm">
            <div className="space-y-4">
              {/* Interactive AI Engine Switcher */}
              <div className="space-y-2">
                <div className="flex items-center justify-between">
                  <h2 className="text-sm font-bold uppercase tracking-wider text-[var(--text-primary)] flex items-center gap-2">
                    <Sparkles className="w-4 h-4 text-[var(--accent-primary)]" />
                    AI Analysis Engine
                  </h2>
                  {switchingMode && <span className="text-[11px] text-[var(--text-muted)]">Saving mode...</span>}
                </div>

                <div className="grid grid-cols-1 sm:grid-cols-2 gap-2.5">
                  {/* Gemini Cloud Card */}
                  <button
                    type="button"
                    onClick={() => handleSelectAiEngine('gemini')}
                    className={`p-3 rounded-xl border text-left transition-all flex flex-col justify-between gap-1.5 ${
                      aiModeChoice === 'gemini'
                        ? 'bg-[var(--accent-soft)] border-[var(--accent-primary)] ring-1 ring-[var(--accent-primary)]/40'
                        : 'bg-[var(--bg-card)] border-[var(--border-subtle)] hover:bg-[var(--bg-hover)]'
                    }`}
                  >
                    <div className="flex items-center justify-between gap-2">
                      <span className="text-xs font-bold text-[var(--text-primary)] flex items-center gap-1.5">
                        <Sparkles className="w-3.5 h-3.5 text-[var(--accent-primary)]" />
                        Gemini Cloud + Multimodal AI
                      </span>
                      {aiModeChoice === 'gemini' && <Check className="w-3.5 h-3.5 text-[var(--accent-primary)] shrink-0" />}
                    </div>
                    <div className="flex items-center gap-1.5">
                      {geminiConfigured ? (
                        <span className="inline-flex items-center gap-1 px-2 py-0.5 rounded-full text-[10px] font-bold bg-emerald-500/15 text-emerald-400 border border-emerald-500/30">
                          <CheckCircle2 className="w-3 h-3" />
                          Key Connected
                        </span>
                      ) : (
                        <span className="inline-flex items-center gap-1 px-2 py-0.5 rounded-full text-[10px] font-bold bg-amber-500/15 text-amber-400 border border-amber-500/30">
                          <KeyRound className="w-3 h-3" />
                          Key Needed
                        </span>
                      )}
                      <span className="text-[10px] text-[var(--text-muted)] truncate">
                        Cloud hook &amp; transcript AI
                      </span>
                    </div>
                  </button>

                  {/* Offline Local Card */}
                  <button
                    type="button"
                    onClick={() => handleSelectAiEngine('offline')}
                    className={`p-3 rounded-xl border text-left transition-all flex flex-col justify-between gap-1.5 ${
                      aiModeChoice === 'offline'
                        ? 'bg-[var(--accent-soft)] border-[var(--accent-primary)] ring-1 ring-[var(--accent-primary)]/40'
                        : 'bg-[var(--bg-card)] border-[var(--border-subtle)] hover:bg-[var(--bg-hover)]'
                    }`}
                  >
                    <div className="flex items-center justify-between gap-2">
                      <span className="text-xs font-bold text-[var(--text-primary)] flex items-center gap-1.5">
                        <WifiOff className="w-3.5 h-3.5 text-emerald-400" />
                        Offline Multimodal AI (100% Local)
                      </span>
                      {aiModeChoice === 'offline' && <Check className="w-3.5 h-3.5 text-[var(--accent-primary)] shrink-0" />}
                    </div>
                    <div className="flex items-center gap-1.5">
                      <span className="inline-flex items-center gap-1 px-2 py-0.5 rounded-full text-[10px] font-bold bg-emerald-500/15 text-emerald-400 border border-emerald-500/30">
                        <ShieldCheck className="w-3 h-3" />
                        No Key Required
                      </span>
                      <span className="text-[10px] text-[var(--text-muted)] truncate">
                        Facial Emotion + Prosody + Whisper
                      </span>
                    </div>
                  </button>
                </div>

                {/* Inline API Key Input when Gemini is active & unconfigured */}
                {aiModeChoice === 'gemini' && !geminiConfigured && (
                  <div className="rounded-xl bg-[var(--bg-card)] border border-amber-500/35 p-3 space-y-2">
                    <div className="flex items-center justify-between text-[11px]">
                      <span className="font-bold text-amber-400 flex items-center gap-1.5">
                        <KeyRound className="w-3.5 h-3.5" />
                        Connect Google Gemini API Key
                      </span>
                      <button
                        type="button"
                        onClick={() => handleSelectAiEngine('offline')}
                        className="text-[11px] font-semibold text-[var(--text-muted)] hover:text-[var(--text-primary)] underline"
                      >
                        Or use 100% Offline AI
                      </button>
                    </div>
                    <div className="flex items-center gap-2">
                      <input
                        type="password"
                        value={inlineKeyInput}
                        onChange={(e) => setInlineKeyInput(e.target.value)}
                        placeholder="Paste Gemini API key (AIza...)"
                        className="flex-1 px-3 py-1.5 rounded-lg bg-[var(--bg-input)] border border-[var(--border-subtle)] text-xs text-[var(--text-primary)] focus:outline-none focus:border-[var(--accent-primary)]"
                      />
                      <button
                        type="button"
                        onClick={handleSaveInlineKey}
                        disabled={savingKey}
                        className="px-3 py-1.5 rounded-lg bg-[var(--accent-primary)] text-[var(--accent-foreground)] text-xs font-bold hover:brightness-110 disabled:opacity-50 shrink-0"
                      >
                        {savingKey ? 'Testing...' : 'Verify & Save'}
                      </button>
                    </div>
                    {inlineKeyStatus && (
                      <p className={`text-[11px] ${inlineKeyStatus.type === 'ok' ? 'text-emerald-400' : 'text-rose-400'}`}>
                        {inlineKeyStatus.text}
                      </p>
                    )}
                  </div>
                )}
              </div>

              {/* Clean 2x2 Configuration Grid */}
              <div className="grid grid-cols-1 sm:grid-cols-2 gap-3">
                {/* 1. Aspect Ratio */}
                <div className="rounded-xl bg-[var(--bg-card)] border border-[var(--border-subtle)] p-3 space-y-2">
                  <div className="flex items-center justify-between">
                    <span className="text-[11px] font-bold uppercase tracking-wider text-[var(--text-muted)]">
                      Aspect Ratio
                    </span>
                    <span className="text-[11px] font-bold text-[var(--accent-primary)]">{aspectRatio}</span>
                  </div>
                  <div className="grid grid-cols-4 gap-1.5">
                    {ASPECT_OPTIONS.map((opt) => (
                      <button
                        key={opt.value}
                        type="button"
                        onClick={() => setAspectRatio(opt.value)}
                        title={opt.sub}
                        className={`py-1.5 rounded-lg text-xs font-bold border transition-colors ${
                          aspectRatio === opt.value
                            ? 'bg-[var(--accent-primary)] text-[var(--accent-foreground)] border-transparent'
                            : 'bg-[var(--bg-input)] text-[var(--text-secondary)] border-[var(--border-subtle)] hover:bg-[var(--bg-hover)]'
                        }`}
                      >
                        {opt.label}
                      </button>
                    ))}
                  </div>
                </div>

                {/* 2. Target Clip Length */}
                <div className="rounded-xl bg-[var(--bg-card)] border border-[var(--border-subtle)] p-3 space-y-2">
                  <div className="flex items-center justify-between">
                    <span className="text-[11px] font-bold uppercase tracking-wider text-[var(--text-muted)]">
                      Target Clip Length
                    </span>
                    <span className="text-[11px] font-bold text-[var(--accent-primary)]">{durationPreset}</span>
                  </div>
                  <div className="grid grid-cols-5 gap-1">
                    {LENGTH_OPTIONS.map((len) => (
                      <button
                        key={len}
                        type="button"
                        onClick={() => setDurationPreset(len)}
                        className={`py-1.5 rounded-lg text-[11px] font-bold border transition-colors ${
                          durationPreset === len
                            ? 'bg-[var(--accent-primary)] text-[var(--accent-foreground)] border-transparent'
                            : 'bg-[var(--bg-input)] text-[var(--text-secondary)] border-[var(--border-subtle)] hover:bg-[var(--bg-hover)]'
                        }`}
                      >
                        {len}
                      </button>
                    ))}
                  </div>
                </div>

                {/* 3. Clips to Extract */}
                <div className="rounded-xl bg-[var(--bg-card)] border border-[var(--border-subtle)] p-3 space-y-2">
                  <div className="flex items-center justify-between">
                    <span className="text-[11px] font-bold uppercase tracking-wider text-[var(--text-muted)]">
                      Clips to Extract
                    </span>
                    <span className="text-[11px] font-bold text-[var(--accent-primary)]">{clipCount} clips</span>
                  </div>
                  <div className="grid grid-cols-4 gap-1.5">
                    {CLIP_COUNTS.map((cnt) => (
                      <button
                        key={cnt}
                        type="button"
                        onClick={() => setClipCount(cnt)}
                        className={`py-1.5 rounded-lg text-xs font-bold border transition-colors ${
                          clipCount === cnt
                            ? 'bg-[var(--accent-primary)] text-[var(--accent-foreground)] border-transparent'
                            : 'bg-[var(--bg-input)] text-[var(--text-secondary)] border-[var(--border-subtle)] hover:bg-[var(--bg-hover)]'
                        }`}
                      >
                        {cnt}
                      </button>
                    ))}
                  </div>
                </div>

                {/* 4. Default Framing */}
                <div className="rounded-xl bg-[var(--bg-card)] border border-[var(--border-subtle)] p-3 space-y-2">
                  <div className="flex items-center justify-between">
                    <span className="text-[11px] font-bold uppercase tracking-wider text-[var(--text-muted)]">
                      Default Framing
                    </span>
                  </div>
                  <select
                    value={defaultFraming}
                    onChange={(e) => setDefaultFraming(e.target.value)}
                    className="w-full px-2.5 py-1.5 rounded-lg bg-[var(--bg-input)] border border-[var(--border-subtle)] text-xs font-semibold text-[var(--text-primary)] focus:outline-none focus:border-[var(--accent-primary)]"
                  >
                    {FRAMING_OPTIONS.map((f) => (
                      <option key={f.value} value={f.value}>
                        {f.label} ({f.hint})
                      </option>
                    ))}
                  </select>
                </div>
              </div>

              {/* Compact Spoken Language + Director Guidance */}
              <div className="grid grid-cols-1 sm:grid-cols-12 gap-3 items-start">
                <div className="sm:col-span-4 space-y-1">
                  <label className="text-[11px] font-bold uppercase tracking-wider text-[var(--text-muted)]">
                    Spoken Language
                  </label>
                  <select
                    value={captionLanguage}
                    onChange={(e) => setCaptionLanguage(e.target.value)}
                    className="w-full px-2.5 py-2 rounded-xl bg-[var(--bg-input)] border border-[var(--border-subtle)] text-xs font-semibold text-[var(--text-primary)] focus:outline-none focus:border-[var(--accent-primary)]"
                  >
                    {LANGUAGES.map((lang) => (
                      <option key={lang} value={lang}>
                        {lang}
                      </option>
                    ))}
                  </select>
                </div>

                <div className="sm:col-span-8 space-y-1.5">
                  <label className="text-[11px] font-bold uppercase tracking-wider text-[var(--text-muted)]">
                    AI Director Guidance (Optional)
                  </label>
                  <input
                    type="text"
                    value={topicFocus}
                    onChange={(e) => setTopicFocus(e.target.value)}
                    placeholder="e.g. Prioritize funny reactions, clutch moments, and debates..."
                    className="w-full px-3 py-2 rounded-xl bg-[var(--bg-input)] border border-[var(--border-subtle)] text-xs text-[var(--text-primary)] placeholder:text-[var(--text-muted)] focus:outline-none focus:border-[var(--accent-primary)]"
                  />
                  <div className="flex flex-wrap gap-1.5">
                    {DIRECTOR_CHIPS.map((chip) => {
                      const active = topicFocus.toLowerCase().includes(chip.toLowerCase());
                      return (
                        <button
                          key={chip}
                          type="button"
                          onClick={() =>
                            setTopicFocus((prev) =>
                              active
                                ? prev
                                    .replace(new RegExp(chip.replace(/[.*+?^${}()|[\]\\]/g, '\\$&'), 'i'), '')
                                    .replace(/\s*[,·]\s*$/g, '')
                                    .trim()
                                : prev.trim()
                                  ? `${prev.trim()}, ${chip}`
                                  : chip
                            )
                          }
                          className={`px-2 py-0.5 rounded-full text-[10px] font-semibold border transition-colors ${
                            active
                              ? 'bg-[var(--accent-soft)] text-[var(--accent-primary)] border-[var(--accent-primary)]'
                              : 'bg-[var(--bg-card)] text-[var(--text-muted)] border-[var(--border-subtle)] hover:text-[var(--text-primary)]'
                          }`}
                        >
                          + {chip}
                        </button>
                      );
                    })}
                  </div>
                </div>
              </div>
            </div>
          </section>
        </div>

        {/* ================================================================ */}
        {/* RECENT PROJECTS QUICK-RESUME STRIP                               */}
        {/* ================================================================ */}
        <section className="space-y-3 pt-1">
          <div className="flex items-center justify-between">
            <div className="flex items-center gap-2">
              <FolderOpen className="w-4 h-4 text-[var(--accent-primary)]" />
              <h2 className="text-sm font-bold uppercase tracking-wider text-[var(--text-primary)]">
                Recent Studio Projects
              </h2>
              {totalProjectsCount > 0 && (
                <span className="px-2 py-0.5 rounded-full text-[10px] font-bold bg-[var(--bg-surface)] border border-[var(--border-subtle)] text-[var(--text-muted)]">
                  {totalProjectsCount} total
                </span>
              )}
            </div>

            <button
              type="button"
              onClick={onViewLibrary}
              className="inline-flex items-center gap-1 text-xs font-bold text-[var(--accent-primary)] hover:underline"
            >
              View All Projects
              <ArrowRight className="w-3.5 h-3.5" />
            </button>
          </div>

          {loadingProjects ? (
            <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-4 gap-3.5">
              {[1, 2, 3, 4].map((idx) => (
                <div
                  key={idx}
                  className="h-44 rounded-2xl bg-[var(--bg-surface)] border border-[var(--border-subtle)] animate-pulse"
                />
              ))}
            </div>
          ) : recentProjects.length === 0 ? (
            <div className="rounded-2xl bg-[var(--bg-surface)] border border-[var(--border-subtle)] p-6 text-center text-xs text-[var(--text-muted)] space-y-2">
              <p>
                {projectsError
                  ? `${projectsError} — upload a video above or refresh once the backend is ready.`
                  : 'No projects yet — upload a video above to get started'}
              </p>
              {projectsError && (
                <button
                  type="button"
                  onClick={loadRecentProjects}
                  className="inline-flex items-center gap-1.5 px-3 py-1.5 rounded-lg bg-[var(--bg-card)] border border-[var(--border-subtle)] text-xs font-bold text-[var(--accent-primary)] hover:bg-[var(--bg-hover)]"
                >
                  <RefreshCw className="w-3.5 h-3.5" />
                  Retry Loading Projects
                </button>
              )}
            </div>
          ) : (
            <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-4 gap-3.5">
              {recentProjects.map((proj) => {
                const isRunning = ['created', 'proxy_ready', 'analyzing', 'processing'].includes(proj.status);
                const isError = proj.status === 'error';
                const isOfflineProj = proj.ai_mode === 'offline';

                return (
                  <div
                    key={proj.id}
                    className="group rounded-2xl bg-[var(--bg-surface)] border border-[var(--border-subtle)] hover:border-[var(--accent-primary)]/60 overflow-hidden flex flex-col justify-between transition-all shadow-sm"
                  >
                    {/* Aspect Video Visual Thumbnail */}
                    <div className="relative aspect-video bg-[var(--bg-card)] border-b border-[var(--border-subtle)] overflow-hidden flex items-center justify-center">
                      {proj.thumbnail_url ? (
                        <img
                          src={proj.thumbnail_url}
                          alt={proj.title}
                          className="w-full h-full object-cover group-hover:scale-[1.03] transition-transform duration-300"
                        />
                      ) : (
                        <div className="flex flex-col items-center gap-1 text-[var(--text-muted)]">
                          <Film className="w-7 h-7 opacity-60" />
                          <span className="text-[10px] font-semibold uppercase tracking-wider opacity-75">
                            {proj.default_aspect_ratio || '9:16'} Project
                          </span>
                        </div>
                      )}

                      {/* Top Left AI Mode Badge */}
                      <span
                        className={`absolute top-2 left-2 inline-flex items-center gap-1 px-2 py-0.5 rounded-full text-[10px] font-bold backdrop-blur-md border ${
                          isOfflineProj
                            ? 'bg-emerald-950/75 text-emerald-300 border-emerald-500/40'
                            : 'bg-indigo-950/75 text-indigo-200 border-indigo-400/40'
                        }`}
                      >
                        {isOfflineProj ? <WifiOff className="w-2.5 h-2.5" /> : <Sparkles className="w-2.5 h-2.5" />}
                        {isOfflineProj ? 'Offline AI' : 'Gemini'}
                      </span>

                      {/* Bottom Right Duration / Clip Count */}
                      <div className="absolute bottom-2 right-2 flex items-center gap-1.5">
                        {proj.duration > 0 && (
                          <span className="px-1.5 py-0.5 rounded bg-black/75 text-white font-mono text-[10px] font-bold flex items-center gap-1">
                            <Clock className="w-2.5 h-2.5 opacity-80" />
                            {formatDurationClock(proj.duration)}
                          </span>
                        )}
                      </div>
                    </div>

                    {/* Card Body & Quick Resume CTA */}
                    <div className="p-3.5 space-y-2.5">
                      <div>
                        <h3 className="text-xs font-bold text-[var(--text-primary)] truncate" title={proj.title}>
                          {proj.title}
                        </h3>
                        <p className="text-[11px] text-[var(--text-muted)] mt-0.5 flex items-center justify-between">
                          <span>
                            {proj.clips_count ?? 0} {(proj.clips_count ?? 0) === 1 ? 'clip' : 'clips'}
                          </span>
                          <span
                            className={`font-semibold ${
                              isRunning
                                ? 'text-[var(--accent-primary)]'
                                : isError
                                  ? 'text-rose-400'
                                  : 'text-emerald-400'
                            }`}
                          >
                            {isRunning ? 'Running' : isError ? 'Failed' : 'Ready'}
                          </span>
                        </p>
                      </div>

                      <button
                        type="button"
                        onClick={() => (isRunning || isError ? onViewProgress(proj.id) : onOpenProject(proj.id))}
                        className="w-full py-2 px-3 rounded-xl bg-[var(--bg-elevated)] hover:bg-[var(--accent-primary)] text-[var(--text-primary)] hover:text-[var(--accent-foreground)] border border-[var(--border-subtle)] text-xs font-bold flex items-center justify-center gap-1.5 transition-colors"
                      >
                        {isRunning ? 'View Live Progress' : isError ? 'Inspect / Retry' : 'Open in Studio'}
                        <ArrowRight className="w-3.5 h-3.5" />
                      </button>
                    </div>
                  </div>
                );
              })}
            </div>
          )}
        </section>
      </div>

      {/* Choose AI Analysis Engine Dialog (only shown if Gemini chosen without saved key) */}
      {showEngineChoiceDialog && (
        <div className="fixed inset-0 z-50 bg-black/70 backdrop-blur-sm flex items-center justify-center p-4">
          <div className="w-full max-w-lg rounded-2xl bg-[var(--bg-surface)] border border-[var(--border-subtle)] p-6 space-y-4 shadow-2xl">
            <div className="flex items-start justify-between gap-3">
              <div>
                <h3 className="text-base font-extrabold text-[var(--text-primary)]">
                  Choose AI Analysis Engine
                </h3>
                <p className="text-xs text-[var(--text-muted)] mt-1">
                  Gemini Cloud mode needs a Google Gemini API key, or you can run 100% locally using Offline Multimodal AI.
                </p>
              </div>
              <button
                type="button"
                onClick={() => setShowEngineChoiceDialog(false)}
                className="text-xs font-bold text-[var(--text-muted)] hover:text-[var(--text-primary)]"
              >
                ✕
              </button>
            </div>

            <div className="rounded-xl bg-[var(--bg-card)] border border-[var(--border-subtle)] p-4 space-y-2.5">
              <div className="text-xs font-bold text-[var(--text-primary)] flex items-center gap-1.5">
                <KeyRound className="w-4 h-4 text-[var(--accent-primary)]" />
                Option 1: Connect Google Gemini API Key
              </div>
              <div className="flex gap-2">
                <input
                  type="password"
                  value={dialogApiKey}
                  onChange={(e) => setDialogApiKey(e.target.value)}
                  placeholder="Paste Gemini API key (AIza...)"
                  className="flex-1 px-3 py-2 rounded-xl bg-[var(--bg-input)] border border-[var(--border-subtle)] text-xs text-[var(--text-primary)] focus:outline-none focus:border-[var(--accent-primary)]"
                />
                <button
                  type="button"
                  disabled={dialogTestingKey}
                  onClick={handleDialogStartGemini}
                  className="px-4 py-2 rounded-xl bg-[var(--accent-primary)] text-[var(--accent-foreground)] text-xs font-bold hover:brightness-110 disabled:opacity-50"
                >
                  {dialogTestingKey ? 'Checking...' : 'Save & Start'}
                </button>
              </div>
              {dialogKeyError && <p className="text-[11px] text-rose-400">{dialogKeyError}</p>}
            </div>

            <button
              type="button"
              onClick={handleDialogStartOffline}
              className="w-full py-3 px-4 rounded-xl bg-[var(--bg-card)] hover:bg-[var(--bg-hover)] border border-[var(--border-subtle)] text-xs font-bold text-[var(--text-primary)] flex items-center justify-between"
            >
              <span className="flex items-center gap-2">
                <WifiOff className="w-4 h-4 text-emerald-400" />
                Option 2: Run in Offline Multimodal AI Mode (Zero Key Required)
              </span>
              <ArrowRight className="w-4 h-4 text-[var(--accent-primary)]" />
            </button>
          </div>
        </div>
      )}
    </div>
  );
};
