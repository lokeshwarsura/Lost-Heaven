import React, { useState, useEffect, useRef } from 'react';
import { 
  UploadCloud, Sparkles, Film, Clock, Play, 
  Trash2, ArrowRight, CheckCircle2, Layers, Globe, FileVideo,
  AlertCircle, RefreshCw, Cpu, Zap
} from 'lucide-react';
import { Project, AnalyticsFormulaReport } from '../types';
import { api } from '../api/client';

const YouTubeIcon: React.FC<{ className?: string }> = ({ className = "w-4 h-4" }) => (
  <svg className={className} viewBox="0 0 24 24" fill="currentColor">
    <path d="M23.498 6.186a3.016 3.016 0 0 0-2.122-2.136C19.505 3.545 12 3.545 12 3.545s-7.505 0-9.377.505A3.017 3.017 0 0 0 .502 6.186C0 8.07 0 12 0 12s0 3.93.502 5.814a3.016 3.016 0 0 0 2.122 2.136c1.871.505 9.376.505 9.376.505s7.505 0 9.377-.505a3.015 3.015 0 0 0 2.122-2.136C24 15.93 24 12 24 12s0-3.93-.502-5.814zM9.545 15.568V8.432L15.818 12l-6.273 3.568z"/>
  </svg>
);

interface HomeViewProps {
  onStartJob: (projectId: number) => void;
  onOpenProject: (projectId: number) => void;
  onViewProgress: (projectId: number) => void;
  onViewLibrary?: () => void;
}

export const HomeView: React.FC<HomeViewProps> = ({
  onStartJob,
  onOpenProject,
  onViewProgress,
  onViewLibrary
}) => {
  // Ingestion Source State
  const [activeTab, setActiveTab] = useState<'upload' | 'youtube'>('upload');
  const [selectedFile, setSelectedFile] = useState<File | null>(null);
  const [fileDuration, setFileDuration] = useState<number | null>(null);
  const [fileResolution, setFileResolution] = useState<{ w: number; h: number } | null>(null);
  const [youtubeUrl, setYoutubeUrl] = useState('');
  const [isDragging, setIsDragging] = useState(false);
  const [isSubmitting, setIsSubmitting] = useState(false);
  const [errorMessage, setErrorMessage] = useState<string | null>(null);

  // Pre-Submission Setup Bar State
  const [aspectRatio, setAspectRatio] = useState<string>('9:16');
  const [durationPreset, setDurationPreset] = useState<string>('Auto');
  const [clipCount, setClipCount] = useState<number>(5);
  const [captionLanguage, setCaptionLanguage] = useState<string>('As spoken');
  const [topicFocus, setTopicFocus] = useState<string>('');
  const [hardwareMode, setHardwareMode] = useState<string>('gpu');

  // Recent Projects
  const [recentProjects, setRecentProjects] = useState<Project[]>([]);
  const [isLoadingProjects, setIsLoadingProjects] = useState(true);
  const [formulaReport, setFormulaReport] = useState<AnalyticsFormulaReport | null>(null);

  const fileInputRef = useRef<HTMLInputElement>(null);

  useEffect(() => {
    loadRecentProjects();
    api.getAnalyticsFormula().then(setFormulaReport).catch(() => null);
  }, []);

  const loadRecentProjects = async () => {
    try {
      setIsLoadingProjects(true);
      const res = await api.getProjectsList();
      const projs = res?.projects || [];
      // Take top 4 most recent
      setRecentProjects(projs.slice(0, 4));
    } catch (err) {
      console.error('Failed to load recent projects:', err);
    } finally {
      setIsLoadingProjects(false);
    }
  };

  const handleFileDrop = (e: React.DragEvent) => {
    e.preventDefault();
    setIsDragging(false);
    if (e.dataTransfer.files && e.dataTransfer.files.length > 0) {
      processSelectedFile(e.dataTransfer.files[0]);
    }
  };

  const handleFileInputChange = (e: React.ChangeEvent<HTMLInputElement>) => {
    if (e.target.files && e.target.files.length > 0) {
      processSelectedFile(e.target.files[0]);
    }
  };

  const processSelectedFile = (file: File) => {
    setErrorMessage(null);
    const validExtensions = ['.mp4', '.mov', '.mkv', '.webm'];
    const ext = '.' + file.name.split('.').pop()?.toLowerCase();
    if (!validExtensions.includes(ext)) {
      setErrorMessage(`Please upload a valid video file (${validExtensions.join(', ')})`);
      return;
    }
    setSelectedFile(file);

    // Create temporary video element to probe duration & resolution
    const tempUrl = URL.createObjectURL(file);
    const v = document.createElement('video');
    v.preload = 'metadata';
    v.src = tempUrl;
    v.onloadedmetadata = () => {
      setFileDuration(v.duration);
      setFileResolution({ w: v.videoWidth, h: v.videoHeight });
      URL.revokeObjectURL(tempUrl);
    };
    v.onerror = () => {
      URL.revokeObjectURL(tempUrl);
    };
  };

  const formatFileSize = (bytes: number) => {
    if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`;
    if (bytes < 1024 * 1024 * 1024) return `${(bytes / (1024 * 1024)).toFixed(1)} MB`;
    return `${(bytes / (1024 * 1024 * 1024)).toFixed(2)} GB`;
  };

  const formatSeconds = (sec: number) => {
    const m = Math.floor(sec / 60);
    const s = Math.floor(sec % 60);
    return `${m}:${s < 10 ? '0' : ''}${s}`;
  };

  const handleStartAnalysis = async () => {
    setErrorMessage(null);
    if (activeTab === 'upload' && !selectedFile) {
      setErrorMessage('Please select or drop a video file before starting analysis.');
      return;
    }
    if (activeTab === 'youtube' && !youtubeUrl.trim()) {
      setErrorMessage('Please enter a YouTube video URL.');
      return;
    }

    try {
      setIsSubmitting(true);
      let res: { project_id: number; title: string };

      if (activeTab === 'upload' && selectedFile) {
        res = await api.uploadVideo(
          selectedFile,
          aspectRatio,
          durationPreset,
          clipCount,
          topicFocus.trim() || undefined,
          captionLanguage,
          hardwareMode
        );
      } else {
        res = await api.importYouTube(
          youtubeUrl.trim(),
          aspectRatio,
          durationPreset,
          clipCount,
          topicFocus.trim() || undefined,
          captionLanguage,
          hardwareMode
        );
      }

      if (res && res.project_id) {
        onStartJob(res.project_id);
      } else {
        throw new Error('No project ID returned by server');
      }
    } catch (err: any) {
      console.error('Submission failed:', err);
      setErrorMessage(err.message || 'Failed to submit video for analysis');
      setIsSubmitting(false);
    }
  };

  const handleDeleteProject = async (e: React.MouseEvent, id: number) => {
    e.stopPropagation();
    if (!confirm('Are you sure you want to delete this project and reclaim disk space?')) return;
    try {
      await api.deleteProject(id);
      loadRecentProjects();
    } catch (err) {
      console.error('Delete failed:', err);
    }
  };

  return (
    <div className="flex-1 h-full overflow-y-auto bg-[var(--bg-canvas)] text-[var(--text-primary)] transition-colors p-6 md:p-10 select-none">
      <div className="max-w-5xl mx-auto space-y-8 animate-fadeIn">
        
        {/* Header Hero */}
        <div className="text-center space-y-2 pt-2">
          <div className="inline-flex items-center gap-2 px-3 py-1 rounded-full bg-[var(--bg-card)] border border-[var(--border-subtle)] text-[var(--accent-primary)] text-xs font-semibold shadow-sm">
            <Sparkles className="w-3.5 h-3.5" />
            <span>Local-First AI Video Clipper & Multi-Format Studio</span>
          </div>
          <h1 className="text-3xl md:text-4xl font-black tracking-tight text-[var(--text-primary)]">
            Turn Long Videos into <span className="text-[var(--accent-primary)]">Viral Shorts</span>
          </h1>
          <p className="text-sm text-[var(--text-secondary)] max-w-xl mx-auto">
            Drop raw footage or import from YouTube. Multimodal Gemini AI detects viral hooks, while faster-whisper and FFmpeg generate animated karaoke captions.
          </p>
        </div>

        {/* Source Toggle Tabs */}
        <div className="flex justify-center">
          <div className="bg-[var(--bg-card)] p-1 rounded-2xl border border-[var(--border-subtle)] inline-flex gap-1 shadow-sm">
            <button
              onClick={() => { setActiveTab('upload'); setErrorMessage(null); }}
              className={`flex items-center gap-2 px-5 py-2 rounded-xl text-xs font-bold transition ${
                activeTab === 'upload'
                  ? 'bg-[var(--accent-primary)] text-[var(--accent-foreground)] shadow-md'
                  : 'text-[var(--text-secondary)] hover:text-[var(--text-primary)]'
              }`}
            >
              <UploadCloud className="w-4 h-4" />
              Upload Local Video
            </button>
            <button
              onClick={() => { setActiveTab('youtube'); setErrorMessage(null); }}
              className={`flex items-center gap-2 px-5 py-2 rounded-xl text-xs font-bold transition ${
                activeTab === 'youtube'
                  ? 'bg-[var(--accent-primary)] text-[var(--accent-foreground)] shadow-md'
                  : 'text-[var(--text-secondary)] hover:text-[var(--text-primary)]'
              }`}
            >
              <YouTubeIcon className="w-4 h-4 text-red-500" />
              Import YouTube URL
            </button>
          </div>
        </div>

        {/* Hero Dropzone / YouTube Input */}
        <div className="bg-[var(--bg-surface)] border border-[var(--border-subtle)] rounded-3xl p-6 md:p-8 shadow-xl relative overflow-hidden transition-all">
          {activeTab === 'upload' ? (
            <div
              onDragOver={(e) => { e.preventDefault(); setIsDragging(true); }}
              onDragLeave={() => setIsDragging(false)}
              onDrop={handleFileDrop}
              onClick={() => fileInputRef.current?.click()}
              className={`border-2 border-dashed rounded-2xl p-8 md:p-12 text-center cursor-pointer transition-all flex flex-col items-center justify-center gap-4 ${
                isDragging
                  ? 'border-[var(--accent-primary)] bg-[var(--accent-primary)]/10 scale-[1.01]'
                  : selectedFile
                  ? 'border-[var(--accent-primary)]/50 bg-[var(--bg-card)]'
                  : 'border-[var(--border-subtle)] hover:border-[var(--accent-primary)]/40 hover:bg-[var(--bg-card)]/50'
              }`}
            >
              <input
                ref={fileInputRef}
                type="file"
                accept=".mp4,.mov,.mkv,.webm"
                className="hidden"
                onChange={handleFileInputChange}
              />

              {selectedFile ? (
                <div className="space-y-3">
                  <div className="w-16 h-16 rounded-2xl bg-[var(--accent-primary)]/15 border border-[var(--accent-primary)]/30 flex items-center justify-center mx-auto text-[var(--accent-primary)] shadow-md">
                    <FileVideo className="w-8 h-8" />
                  </div>
                  <div>
                    <h3 className="font-bold text-base text-[var(--text-primary)]">{selectedFile.name}</h3>
                    <div className="flex items-center justify-center gap-3 text-xs text-[var(--text-secondary)] font-mono mt-1 tabular-nums">
                      <span>{formatFileSize(selectedFile.size)}</span>
                      {fileDuration && <span>• {formatSeconds(fileDuration)}</span>}
                      {fileResolution && <span>• {fileResolution.w}x{fileResolution.h}</span>}
                    </div>
                  </div>
                  <button
                    type="button"
                    onClick={(e) => { e.stopPropagation(); setSelectedFile(null); }}
                    className="text-xs text-[var(--text-secondary)] hover:text-red-400 underline pt-1"
                  >
                    Choose a different file
                  </button>
                </div>
              ) : (
                <>
                  <div className="w-16 h-16 rounded-2xl bg-[var(--bg-card)] border border-[var(--border-subtle)] flex items-center justify-center text-[var(--accent-primary)] shadow-md">
                    <UploadCloud className="w-8 h-8" />
                  </div>
                  <div className="space-y-1">
                    <p className="font-bold text-base text-[var(--text-primary)]">
                      Drag & Drop your raw video footage here
                    </p>
                    <p className="text-xs text-[var(--text-secondary)]">
                      or click to browse from your computer (.mp4, .mov, .mkv, .webm)
                    </p>
                  </div>
                  <div className="inline-flex items-center gap-2 px-3 py-1 rounded-lg bg-[var(--bg-input)] border border-[var(--border-subtle)] text-[11px] text-[var(--text-secondary)]">
                    <span>Zero file size limit</span> • <span>100% processed locally & privately</span>
                  </div>
                </>
              )}
            </div>
          ) : (
            <div className="py-6 px-4 md:px-8 space-y-4">
              <div className="flex items-center gap-3">
                <div className="w-10 h-10 rounded-xl bg-red-500/10 border border-red-500/20 flex items-center justify-center text-red-500 flex-shrink-0">
                  <YouTubeIcon className="w-5 h-5" />
                </div>
                <div>
                  <h3 className="text-sm font-bold text-[var(--text-primary)]">Download & Clip YouTube Video</h3>
                  <p className="text-xs text-[var(--text-secondary)]">Powered by yt-dlp with hardware-accelerated transcoding</p>
                </div>
              </div>

              <div className="flex gap-2">
                <input
                  type="text"
                  value={youtubeUrl}
                  onChange={(e) => setYoutubeUrl(e.target.value)}
                  placeholder="https://www.youtube.com/watch?v=..."
                  className="flex-1 bg-[var(--bg-input)] border border-[var(--border-subtle)] rounded-xl px-4 py-3 text-xs text-[var(--text-primary)] focus:outline-none focus:border-[var(--accent-primary)] transition"
                />
                <button
                  type="button"
                  onClick={async () => {
                    try {
                      const text = await navigator.clipboard.readText();
                      if (text) setYoutubeUrl(text);
                    } catch {}
                  }}
                  className="px-4 py-3 rounded-xl bg-[var(--bg-card)] border border-[var(--border-subtle)] text-xs font-semibold text-[var(--text-secondary)] hover:text-[var(--text-primary)] hover:border-[var(--accent-primary)]/40 transition"
                >
                  Paste
                </button>
              </div>
            </div>
          )}

          {/* Quick Pre-Submission Setup Controls Bar */}
          <div className="mt-6 pt-6 border-t border-[var(--border-subtle)] space-y-4">
            <div className="flex items-center justify-between">
              <span className="text-xs font-bold uppercase tracking-wider text-[var(--text-secondary)] flex items-center gap-1.5">
                <Layers className="w-3.5 h-3.5 text-[var(--accent-primary)]" />
                Quick Pipeline Setup
              </span>
              <span className="text-[11px] text-[var(--text-secondary)]">
                You can fine-tune captions and framing after ingestion
              </span>
            </div>

            <div className="grid grid-cols-1 md:grid-cols-4 gap-4">
              {/* Aspect Ratio */}
              <div className="space-y-1.5">
                <label className="text-[11px] font-bold text-[var(--text-secondary)]">Aspect Ratio</label>
                <div className="grid grid-cols-4 gap-1">
                  {['9:16', '16:9', '1:1', '4:5'].map((ratio) => (
                    <button
                      key={ratio}
                      type="button"
                      onClick={() => setAspectRatio(ratio)}
                      className={`py-1.5 rounded-lg text-xs font-bold border transition ${
                        aspectRatio === ratio
                          ? 'bg-[var(--accent-primary)] text-[var(--accent-foreground)] border-[var(--accent-primary)] shadow-sm'
                          : 'bg-[var(--bg-input)] border-[var(--border-subtle)] text-[var(--text-secondary)] hover:text-[var(--text-primary)]'
                      }`}
                    >
                      {ratio}
                    </button>
                  ))}
                </div>
              </div>

              {/* Clip Length Target */}
              <div className="space-y-1.5">
                <label className="text-[11px] font-bold text-[var(--text-secondary)]">Clip Duration</label>
                <div className="grid grid-cols-5 gap-1">
                  {['Auto', '15s', '30s', '45s', '60s'].map((d) => (
                    <button
                      key={d}
                      type="button"
                      onClick={() => setDurationPreset(d)}
                      className={`py-1.5 rounded-lg text-[11px] font-bold border transition ${
                        durationPreset === d
                          ? 'bg-[var(--accent-primary)] text-[var(--accent-foreground)] border-[var(--accent-primary)] shadow-sm'
                          : 'bg-[var(--bg-input)] border-[var(--border-subtle)] text-[var(--text-secondary)] hover:text-[var(--text-primary)]'
                      }`}
                    >
                      {d}
                    </button>
                  ))}
                </div>
              </div>

              {/* Number of Clips */}
              <div className="space-y-1.5">
                <label className="text-[11px] font-bold text-[var(--text-secondary)]">Clips to Generate</label>
                <div className="grid grid-cols-4 gap-1">
                  {[3, 5, 8, 10].map((num) => (
                    <button
                      key={num}
                      type="button"
                      onClick={() => setClipCount(num)}
                      className={`py-1.5 rounded-lg text-xs font-bold border transition ${
                        clipCount === num
                          ? 'bg-[var(--accent-primary)] text-[var(--accent-foreground)] border-[var(--accent-primary)] shadow-sm'
                          : 'bg-[var(--bg-input)] border-[var(--border-subtle)] text-[var(--text-secondary)] hover:text-[var(--text-primary)]'
                      }`}
                    >
                      {num}
                    </button>
                  ))}
                </div>
              </div>

              {/* Caption Language */}
              <div className="space-y-1.5">
                <label className="text-[11px] font-bold text-[var(--text-secondary)]">Spoken Language</label>
                <select
                  value={captionLanguage}
                  onChange={(e) => setCaptionLanguage(e.target.value)}
                  className="w-full bg-[var(--bg-input)] border border-[var(--border-subtle)] text-xs text-[var(--text-primary)] rounded-lg py-2 px-2.5 font-medium focus:outline-none focus:border-[var(--accent-primary)]"
                >
                  <option value="As spoken">Auto-detect (As spoken)</option>
                  <option value="English">English</option>
                  <option value="Hindi">Hindi</option>
                  <option value="Hinglish">Hinglish (Roman Script)</option>
                  <option value="Spanish">Spanish</option>
                  <option value="French">French</option>
                  <option value="German">German</option>
                  <option value="Japanese">Japanese</option>
                </select>
              </div>
            </div>

            {/* Optional Topic Focus & AI Director Instructions */}
            <div className="space-y-2">
              <label className="text-[11px] font-bold text-[var(--text-secondary)] flex items-center justify-between">
                <span>AI Director Instructions (Optional)</span>
                <span className="text-[10px] text-[var(--text-secondary)]">Tap quick chips or type custom guidance</span>
              </label>
              <div className="flex flex-wrap gap-1.5">
                {[
                  'Storytime Arc',
                  'Hot Takes & Debate',
                  'Technical Deep-Dive',
                  'Actionable Tips',
                  'Funny / High Energy',
                  'Pricing & Strategy'
                ].map((chip) => {
                  const isActive = topicFocus.toLowerCase().includes(chip.toLowerCase());
                  return (
                    <button
                      key={chip}
                      type="button"
                      onClick={() => {
                        if (isActive) {
                          const next = topicFocus
                            .split(',')
                            .map((s) => s.trim())
                            .filter((s) => s && s.toLowerCase() !== chip.toLowerCase())
                            .join(', ');
                          setTopicFocus(next);
                        } else {
                          setTopicFocus(topicFocus.trim() ? `${topicFocus.trim()}, ${chip}` : chip);
                        }
                      }}
                      className={`px-2.5 py-1 rounded-full border text-[10px] font-semibold transition ${
                        isActive
                          ? 'bg-[var(--accent-subtle)] border-[var(--accent-primary)] text-[var(--accent-primary)]'
                          : 'bg-[var(--bg-input)] border-[var(--border-subtle)] text-[var(--text-secondary)] hover:text-[var(--text-primary)]'
                      }`}
                    >
                      {chip}
                    </button>
                  );
                })}
              </div>
              <input
                type="text"
                value={topicFocus}
                onChange={(e) => setTopicFocus(e.target.value)}
                placeholder="Give AI specific guidance on what moments to extract..."
                className="w-full bg-[var(--bg-input)] border border-[var(--border-subtle)] rounded-xl px-4 py-2 text-xs text-[var(--text-primary)] focus:outline-none focus:border-[var(--accent-primary)] transition"
              />
            </div>

            {/* Error Banner */}
            {errorMessage && (
              <div className="p-3 bg-red-500/10 border border-red-500/30 rounded-xl flex items-center gap-2 text-xs text-red-400">
                <AlertCircle className="w-4 h-4 flex-shrink-0" />
                <span>{errorMessage}</span>
              </div>
            )}

            {/* Submit Action CTA */}
            <div className="pt-2 flex flex-col sm:flex-row items-center justify-between gap-4">
              <div className="flex items-center gap-3 flex-wrap">
                <button
                  type="button"
                  onClick={() => setHardwareMode((m) => (m === 'gpu' ? 'cpu' : 'gpu'))}
                  className="flex items-center gap-2 text-xs text-[var(--text-secondary)] hover:text-[var(--text-primary)] px-2.5 py-1 rounded-xl bg-[var(--bg-input)] border border-[var(--border-subtle)] transition"
                  title="Click to switch between GPU NVENC and CPU libx264 encoding"
                >
                  <Zap className={`w-4 h-4 ${hardwareMode === 'gpu' ? 'text-emerald-400' : 'text-amber-400'}`} />
                  <span>
                    {hardwareMode === 'gpu'
                      ? 'GPU Acceleration active (Whisper + NVENC)'
                      : 'CPU Mode (libx264 Fallback)'}
                  </span>
                </button>
                <label className="px-3 py-1.5 rounded-xl bg-[var(--bg-input)] border border-[var(--border-subtle)] hover:border-[var(--accent-primary)]/50 text-xs font-semibold text-[var(--text-primary)] cursor-pointer transition">
                  + Queue Multi-Video Batch
                  <input
                    type="file"
                    multiple
                    accept=".mp4,.mov,.mkv,.webm"
                    className="hidden"
                    onChange={async (e) => {
                      const files = Array.from(e.target.files || []);
                      if (!files.length) return;
                      try {
                        setIsSubmitting(true);
                        const res = await api.enqueueBatchUpload(files, {
                          aspect_ratio: aspectRatio,
                          duration_preset: durationPreset,
                          clip_count: clipCount,
                          topic_focus: topicFocus.trim() || undefined,
                          caption_language: captionLanguage,
                          hardware_mode: hardwareMode
                        });
                        if (res.enqueued?.[0]?.project_id) {
                          onStartJob(res.enqueued[0].project_id);
                        }
                      } catch (err: any) {
                        setErrorMessage(err?.message || 'Batch upload failed');
                      } finally {
                        setIsSubmitting(false);
                      }
                    }}
                  />
                </label>
              </div>

              <button
                type="button"
                onClick={handleStartAnalysis}
                disabled={isSubmitting}
                className="w-full sm:w-auto px-8 py-3.5 rounded-2xl bg-gradient-to-r from-[#F97316] to-[#F59E0B] text-black font-black text-sm shadow-xl shadow-[rgba(245,158,11,0.25)] hover:scale-105 active:scale-95 transition-all flex items-center justify-center gap-2 disabled:opacity-50 disabled:scale-100"
              >
                {isSubmitting ? (
                  <>
                    <RefreshCw className="w-4 h-4 animate-spin" />
                    <span>Launching Pipeline...</span>
                  </>
                ) : (
                  <>
                    <Sparkles className="w-4 h-4" />
                    <span>Analyze & Generate Clips</span>
                    <ArrowRight className="w-4 h-4" />
                  </>
                )}
              </button>
            </div>
          </div>
        </div>

        {/* Local Style Intelligence Card */}
        {formulaReport && (
          <div className="bg-[var(--bg-card)] border border-[var(--border-subtle)] rounded-2xl px-4 py-3 flex flex-wrap items-center justify-between gap-3 shadow-sm">
            <div className="flex items-center gap-2.5 min-w-0">
              <div className="w-8 h-8 rounded-xl bg-[var(--accent-subtle)] border border-[var(--accent-primary)]/40 flex items-center justify-center shrink-0">
                <Sparkles className="w-4 h-4 text-[var(--accent-primary)]" />
              </div>
              <div className="min-w-0">
                <div className="text-[11px] font-bold uppercase tracking-wider text-[var(--accent-primary)]">
                  Your Top Performing Formula
                  {formulaReport.sample_count > 0 ? ` · ${formulaReport.sample_count} tracked` : ''}
                </div>
                <div className="text-xs font-semibold text-[var(--text-primary)] truncate">
                  {formulaReport.headline}
                </div>
              </div>
            </div>
            {formulaReport.has_data && (
              <div className="flex items-center gap-3 text-[11px] font-mono text-[var(--text-secondary)] shrink-0">
                {formulaReport.avg_views > 0 && (
                  <span>{formulaReport.avg_views.toLocaleString()} avg views</span>
                )}
                {formulaReport.avg_retention_pct > 0 && (
                  <span className="text-emerald-400 font-bold">
                    {formulaReport.avg_retention_pct}% avg retention
                  </span>
                )}
              </div>
            )}
          </div>
        )}

        {/* Recent Projects Grid */}
        <div className="space-y-4">
          <div className="flex items-center justify-between">
            <h2 className="text-base font-bold text-[var(--text-primary)] flex items-center gap-2">
              <Film className="w-4 h-4 text-[var(--accent-primary)]" />
              Recent Studio Projects
            </h2>
            <button
              type="button"
              onClick={() => (onViewLibrary ? onViewLibrary() : onOpenProject(recentProjects[0]?.id || 0))}
              className="text-xs font-semibold text-[var(--text-secondary)] hover:text-[var(--accent-primary)] transition"
            >
              View all library →
            </button>
          </div>

          {isLoadingProjects ? (
            <div className="grid grid-cols-1 sm:grid-cols-2 md:grid-cols-4 gap-4">
              {[1, 2, 3, 4].map((n) => (
                <div key={n} className="h-44 bg-[var(--bg-card)] rounded-2xl border border-[var(--border-subtle)] animate-pulse" />
              ))}
            </div>
          ) : recentProjects.length === 0 ? (
            <div className="p-8 text-center bg-[var(--bg-card)] rounded-2xl border border-[var(--border-subtle)] text-xs text-[var(--text-secondary)]">
              No previous projects yet. Upload a video above to generate your first set of clips!
            </div>
          ) : (
            <div className="grid grid-cols-1 sm:grid-cols-2 md:grid-cols-4 gap-4">
              {recentProjects.map((p) => {
                const isFinished = p.status === 'analyzed' || p.status === 'finished';
                const isProcessing = p.status === 'created' || p.status === 'proxy_ready' || p.status === 'analyzing' || p.status === 'processing';

                return (
                  <div
                    key={p.id}
                    onClick={() => {
                      if (isFinished) onOpenProject(p.id);
                      else onViewProgress(p.id);
                    }}
                    className="bg-[var(--bg-card)] border border-[var(--border-subtle)] rounded-2xl p-4 hover:border-[var(--accent-primary)]/50 transition-all cursor-pointer group flex flex-col justify-between shadow-sm relative overflow-hidden"
                  >
                    <div className="space-y-2.5">
                      {/* Top badge */}
                      <div className="flex items-center justify-between">
                        <span className={`text-[10px] font-black uppercase px-2 py-0.5 rounded-full ${
                          isFinished
                            ? 'bg-emerald-500/15 text-emerald-400 border border-emerald-500/30'
                            : isProcessing
                            ? 'bg-amber-500/15 text-amber-400 border border-amber-500/30 animate-pulse'
                            : 'bg-red-500/15 text-red-400 border border-red-500/30'
                        }`}>
                          {p.status}
                        </span>
                        <button
                          type="button"
                          onClick={(e) => handleDeleteProject(e, p.id)}
                          className="text-[var(--text-secondary)] hover:text-red-400 p-1 opacity-0 group-hover:opacity-100 transition"
                          title="Delete Project"
                        >
                          <Trash2 className="w-3.5 h-3.5" />
                        </button>
                      </div>

                      {/* Title & Specs */}
                      <div>
                        <h4 className="font-bold text-xs text-[var(--text-primary)] line-clamp-2 group-hover:text-[var(--accent-primary)] transition">
                          {p.title}
                        </h4>
                        <div className="flex items-center gap-2 text-[10px] text-[var(--text-secondary)] mt-1 font-mono tabular-nums">
                          <span>{p.duration > 0 ? `${formatSeconds(p.duration)}` : 'Duration N/A'}</span>
                          <span>• {p.default_aspect_ratio || '9:16'}</span>
                        </div>
                      </div>
                    </div>

                    {/* Bottom Action */}
                    <div className="pt-3 mt-3 border-t border-[var(--border-subtle)] flex items-center justify-between text-xs font-bold">
                      <span className="text-[10px] text-[var(--text-secondary)] tabular-nums">
                        {p.clips_count !== undefined ? `${p.clips_count} clips` : ''}
                      </span>
                      <span className="text-[var(--accent-primary)] flex items-center gap-1 text-[11px] group-hover:translate-x-0.5 transition-transform">
                        {isFinished ? 'Open in Editor' : isProcessing ? 'View Progress' : 'Check Error'}
                        <ArrowRight className="w-3 h-3" />
                      </span>
                    </div>
                  </div>
                );
              })}
            </div>
          )}
        </div>

      </div>
    </div>
  );
};
