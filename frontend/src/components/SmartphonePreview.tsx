import React, { useRef, useState, useEffect } from 'react';
import {
  Play, Pause, RefreshCw, Eye, Smartphone, Heart, MessageCircle,
  Share2, Music, Volume2, VolumeX, Download, Zap, CheckCircle2, Loader2, X, Scissors,
  Sparkles, Package, Copy, Check, Activity, Trash2, Image as ImageIcon, Shield
} from 'lucide-react';
import {
  ViralClip, PlatformCopyMap, RetentionAuditReport, RetentionWarning,
  PrivacyBlurBox, PreflightReport
} from '../types';
import { api } from '../api/client';

const YouTubeIcon = ({ className = 'w-4 h-4' }: { className?: string }) => (
  <svg viewBox="0 0 24 24" fill="currentColor" className={className}>
    <path d="M23.498 6.186a3.016 3.016 0 0 0-2.122-2.136C19.505 3.545 12 3.545 12 3.545s-7.505 0-9.377.505A3.017 3.017 0 0 0 .502 6.186C0 8.07 0 12 0 12s0 3.93.502 5.814a3.016 3.016 0 0 0 2.122 2.136c1.871.505 9.376.505 9.376.505s7.505 0 9.377-.505a3.015 3.015 0 0 0 2.122-2.136C24 15.93 24 12 24 12s0-3.93-.502-5.814zM9.545 15.568V8.432L15.818 12l-6.273 3.568z" />
  </svg>
);

type SegPair = [number, number];

function resolveClipKeepSegments(clip: ViralClip | null): SegPair[] {
  if (!clip) return [];
  if (Array.isArray(clip.keep_segments) && clip.keep_segments.length > 0) {
    return clip.keep_segments
      .map((s) => [Number(s[0]), Number(s[1])] as SegPair)
      .filter(([s, e]) => Number.isFinite(s) && Number.isFinite(e) && e > s + 0.05);
  }
  return [[Number(clip.start_time || 0), Number(clip.end_time || 30)]];
}

function mapRelToMasterAndSeg(
  relT: number,
  segs: SegPair[],
  crossfadeSec = 0.03
): { masterT: number; segIdx: number } {
  if (!segs.length) return { masterT: Math.max(0, relT), segIdx: -1 };
  let outStart = 0;
  let prevDur = 0;
  for (let i = 0; i < segs.length; i++) {
    const [s, e] = segs[i];
    const segDur = Math.max(0.05, e - s);
    const effXfade =
      i > 0 ? Math.max(0, Math.min(crossfadeSec, prevDur * 0.35, segDur * 0.35)) : 0;
    const segOutStart = i === 0 ? 0 : Math.max(0, outStart - effXfade);
    const segOutEnd = segOutStart + segDur;
    if (relT <= segOutEnd || i === segs.length - 1) {
      const local = Math.max(0, Math.min(segDur, relT - segOutStart));
      return { masterT: s + local, segIdx: i };
    }
    outStart = segOutEnd;
    prevDur = segDur;
  }
  return { masterT: segs[segs.length - 1][1], segIdx: segs.length - 1 };
}

function mapMasterToRel(masterT: number, segs: SegPair[], crossfadeSec = 0.03): number {
  if (!segs.length) return Math.max(0, masterT);
  let outStart = 0;
  let prevDur = 0;
  let bestRel = 0;
  let bestDist = Number.POSITIVE_INFINITY;
  for (let i = 0; i < segs.length; i++) {
    const [s, e] = segs[i];
    const segDur = Math.max(0.05, e - s);
    const effXfade =
      i > 0 ? Math.max(0, Math.min(crossfadeSec, prevDur * 0.35, segDur * 0.35)) : 0;
    const segOutStart = i === 0 ? 0 : Math.max(0, outStart - effXfade);
    if (masterT >= s && masterT <= e) {
      return segOutStart + (masterT - s);
    }
    const clamped = Math.max(s, Math.min(e, masterT));
    const dist = Math.abs(masterT - clamped);
    if (dist < bestDist) {
      bestDist = dist;
      bestRel = segOutStart + (clamped - s);
    }
    outStart = segOutStart + segDur;
    prevDur = segDur;
  }
  return Math.max(0, bestRel);
}

interface SmartphonePreviewProps {
  clip: ViralClip | null;
  videoUrl?: string;
  safeZoneGuide: string;
  onSafeZoneChange: (guide: string) => void;
  seekTime?: number;
  onPlaybackTimeChange?: (relativeSeconds: number) => void;
  onGenerateClips: () => void;
  onAutoRenderClip?: (
    projectId: number,
    clipId: number,
    signal: AbortSignal
  ) => Promise<{
    status: string;
    video_url?: string;
    preview_url?: string;
    rendered_path?: string;
  }>;
  onAutoRenderComplete?: (clipId: number, nextVideoUrl: string, renderedPath?: string) => void;
  onTrimClip?: (clipId: number, startTime: number, endTime: number) => void | Promise<void>;
  onUpdateSegments?: (clipId: number, keepSegments: [number, number][]) => void | Promise<void>;
  onApplyFixClip?: (clipId: number, updates: Partial<ViralClip>) => void | Promise<void>;
  isRendering: boolean;
  renderProgress: number;
}

export const SmartphonePreview: React.FC<SmartphonePreviewProps> = ({
  clip,
  videoUrl,
  safeZoneGuide,
  onSafeZoneChange,
  seekTime,
  onPlaybackTimeChange,
  onGenerateClips,
  onAutoRenderClip,
  onAutoRenderComplete,
  onTrimClip,
  onUpdateSegments,
  onApplyFixClip,
  isRendering,
  renderProgress
}) => {
  const videoRef = useRef<HTMLVideoElement>(null);
  const stageRef = useRef<HTMLDivElement>(null);
  const prevClipIdRef = useRef<number | null>(null);
  const hasSwitchedClipRef = useRef<boolean>(false);
  const shouldAutoPlayRef = useRef<boolean>(false);
  const preparingClipIdRef = useRef<number | null>(null);
  const lastRenderedHashByClipRef = useRef<Record<number, string>>({});
  const debounceTimerRef = useRef<number | null>(null);
  const autoRenderAbortRef = useRef<AbortController | null>(null);
  const pendingRestoreRef = useRef<{ relTime: number; shouldPlay: boolean } | null>(null);
  const relCurrentRef = useRef<number>(0);

  const [autoRender, setAutoRender] = useState<boolean>(() => {
    try {
      const saved =
        localStorage.getItem('lost_heaven_auto_render') ??
        localStorage.getItem('clipship_auto_render');
      return saved === null ? true : saved === 'true';
    } catch {
      return true;
    }
  });
  const [isAutoRendering, setIsAutoRendering] = useState(false);
  const [isPreparingClip, setIsPreparingClip] = useState(false);
  const [videoVersion, setVideoVersion] = useState(0);

  const toggleAutoRender = () => {
    setAutoRender((prev) => {
      const next = !prev;
      try {
        localStorage.setItem('lost_heaven_auto_render', String(next));
      } catch {
        // ignore storage restrictions
      }
      return next;
    });
  };

  const [stageSize, setStageSize] = useState<{ w: number; h: number }>({ w: 280, h: 460 });
  const [isPlaying, setIsPlaying] = useState(false);
  const [isMuted, setIsMuted] = useState(false);
  const [currentTime, setCurrentTime] = useState(0);
  const [duration, setDuration] = useState(30);
  const [showExactFrame, setShowExactFrame] = useState(false);
  const [exactFrameUrl, setExactFrameUrl] = useState<string | null>(null);
  const [frameLatencyMs, setFrameLatencyMs] = useState<number | null>(null);
  const [isRenderingFrame, setIsRenderingFrame] = useState(false);

  // YouTube Publish Modal state
  const [isPublishModalOpen, setIsPublishModalOpen] = useState(false);

  // Platform Safe-Zone & Demonetization Pre-Flight Scanner state
  const [preflightReport, setPreflightReport] = useState<PreflightReport | null>(null);
  const [isPreflightOpen, setIsPreflightOpen] = useState(false);
  const [isPreflightLoading, setIsPreflightLoading] = useState(false);
  const [fixingPreflightId, setFixingPreflightId] = useState<string | null>(null);

  // Depth-Layered AI Thumbnail Studio state
  const [isThumbModalOpen, setIsThumbModalOpen] = useState(false);
  const [thumbFormat, setThumbFormat] = useState<'9:16' | '16:9'>('9:16');
  const [thumbHeadline, setThumbHeadline] = useState('');
  const [thumbBadge, setThumbBadge] = useState('');
  const [thumbDepthLayer, setThumbDepthLayer] = useState(true);
  const [thumbRimGlow, setThumbRimGlow] = useState(true);
  const [thumbAccentColor, setThumbAccentColor] = useState('#C6F32F');
  const [thumbUsePlayhead, setThumbUsePlayhead] = useState(false);
  const [isGeneratingThumb, setIsGeneratingThumb] = useState(false);
  const [thumbError, setThumbError] = useState<string | null>(null);
  const [thumbResult, setThumbResult] = useState<{
    thumbnail_url: string;
    jpg_url: string;
    width: number;
    height: number;
    timestamp: number;
  } | null>(null);

  // Interactive Privacy / HUD Blur Box Drag & Resize state
  const previewStageBoxRef = useRef<HTMLDivElement>(null);
  const [blurDraftBoxes, setBlurDraftBoxes] = useState<PrivacyBlurBox[] | null>(null);
  const blurDragRef = useRef<{
    idx: number;
    kind: 'move' | 'resize';
    startX: number;
    startY: number;
    origBox: PrivacyBlurBox;
    latest: PrivacyBlurBox[];
  } | null>(null);
  const [draggingBlurIdx, setDraggingBlurIdx] = useState<number | null>(null);

  useEffect(() => {
    setBlurDraftBoxes(null);
  }, [clip?.id, JSON.stringify(clip?.privacy_blur_boxes || [])]);

  useEffect(() => {
    if (!clip) {
      setPreflightReport(null);
      return;
    }
    let cancelled = false;
    setIsPreflightLoading(true);
    api
      .getClipPreflight(clip.project_id, clip.id)
      .then((rep) => {
        if (!cancelled) setPreflightReport(rep as PreflightReport);
      })
      .catch(() => null)
      .finally(() => {
        if (!cancelled) setIsPreflightLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, [
    clip?.id,
    clip?.burn_captions,
    clip?.censor_profanity,
    clip?.normalize_audio,
    clip?.audio_volume,
    clip?.position_x,
    clip?.position_y,
    clip?.aspect_ratio,
    clip?.spectrum_enabled,
    clip?.spectrum_position,
  ]);

  useEffect(() => {
    if (draggingBlurIdx === null || !clip) return;
    const onMove = (ev: PointerEvent) => {
      const dr = blurDragRef.current;
      const boxEl = previewStageBoxRef.current;
      if (!dr || !boxEl) return;
      const rect = boxEl.getBoundingClientRect();
      const dx = (ev.clientX - dr.startX) / Math.max(1, rect.width);
      const dy = (ev.clientY - dr.startY) / Math.max(1, rect.height);
      const next = dr.latest.map((b, i) => {
        if (i !== dr.idx) return b;
        if (dr.kind === 'move') {
          const nx = Math.max(0, Math.min(1 - b.w, dr.origBox.x + dx));
          const ny = Math.max(0, Math.min(1 - b.h, dr.origBox.y + dy));
          return { ...b, x: Number(nx.toFixed(3)), y: Number(ny.toFixed(3)) };
        }
        const nw = Math.max(0.08, Math.min(1 - dr.origBox.x, dr.origBox.w + dx));
        const nh = Math.max(0.06, Math.min(1 - dr.origBox.y, dr.origBox.h + dy));
        return { ...b, w: Number(nw.toFixed(3)), h: Number(nh.toFixed(3)) };
      });
      dr.latest = next;
      setBlurDraftBoxes(next);
    };
    const onUp = () => {
      const dr = blurDragRef.current;
      setDraggingBlurIdx(null);
      blurDragRef.current = null;
      if (dr && onApplyFixClip) {
        void onApplyFixClip(clip.id, { privacy_blur_boxes: dr.latest });
      }
    };
    window.addEventListener('pointermove', onMove);
    window.addEventListener('pointerup', onUp);
    return () => {
      window.removeEventListener('pointermove', onMove);
      window.removeEventListener('pointerup', onUp);
    };
  }, [draggingBlurIdx, clip, onApplyFixClip]);
  const [publishTitle, setPublishTitle] = useState('Viral Video #Shorts');
  const [publishDesc, setPublishDesc] = useState('Created with Lost Heaven Studio! #Shorts');
  const [publishPrivacy, setPublishPrivacy] = useState<'private' | 'unlisted' | 'public'>('private');
  const [isPublishing, setIsPublishing] = useState(false);
  const [publishResult, setPublishResult] = useState<string | null>(null);
  const [isExportingPack, setIsExportingPack] = useState(false);
  const [packError, setPackError] = useState<string | null>(null);
  const [socialCopy, setSocialCopy] = useState<Partial<PlatformCopyMap> | null>(null);
  const [isGeneratingCopy, setIsGeneratingCopy] = useState(false);
  const [activeCopyPlatform, setActiveCopyPlatform] = useState<'youtube_shorts' | 'tiktok' | 'instagram_reels' | 'x_twitter'>('youtube_shorts');
  const [copiedField, setCopiedField] = useState<string | null>(null);

  // Interactive 150-bin Waveform & ±5s In/Out Trim State
  const waveTrackRef = useRef<HTMLDivElement>(null);
  const [waveformData, setWaveformData] = useState<{
    bins: number[];
    rms?: number[];
    speech_density?: number[];
    is_silence?: boolean[];
    window_start: number;
    window_end: number;
    clip_start: number;
    clip_end: number;
    video_duration: number;
  } | null>(null);
  const [trimStart, setTrimStart] = useState<number>(clip?.start_time ?? 0);
  const [trimEnd, setTrimEnd] = useState<number>(clip?.end_time ?? 30);
  const [keepSegments, setKeepSegments] = useState<SegPair[]>(() => resolveClipKeepSegments(clip));
  const [selectedSegIdx, setSelectedSegIdx] = useState<number | null>(null);
  const [isAuditOpen, setIsAuditOpen] = useState(false);
  const [isAuditing, setIsAuditing] = useState(false);
  const [auditError, setAuditError] = useState<string | null>(null);
  const [retentionAudit, setRetentionAudit] = useState<RetentionAuditReport | null>(null);
  const [draggingHandle, setDraggingHandle] = useState<'in' | 'out' | null>(null);
  const trimDraftRef = useRef<{ start: number; end: number }>({
    start: clip?.start_time ?? 0,
    end: clip?.end_time ?? 30
  });

  useEffect(() => {
    setKeepSegments(resolveClipKeepSegments(clip));
    setSelectedSegIdx(null);
  }, [clip?.id, clip?.start_time, clip?.end_time, JSON.stringify(clip?.keep_segments || [])]);

  useEffect(() => {
    setRetentionAudit(null);
    setAuditError(null);
    setIsAuditOpen(false);
  }, [clip?.id]);

  useEffect(() => {
    if (!clip?.id || !clip?.project_id) {
      setWaveformData(null);
      return;
    }
    const segs = resolveClipKeepSegments(clip);
    const firstStart = segs.length ? segs[0][0] : clip.start_time;
    const lastEnd = segs.length ? segs[segs.length - 1][1] : clip.end_time;
    setTrimStart(firstStart);
    setTrimEnd(lastEnd);
    trimDraftRef.current = { start: firstStart, end: lastEnd };
    let cancelled = false;
    api
      .getClipWaveform(clip.project_id, clip.id, 150)
      .then((data) => {
        if (!cancelled) {
          setWaveformData(data);
        }
      })
      .catch(() => {
        if (!cancelled) {
          setWaveformData(null);
        }
      });
    return () => {
      cancelled = true;
    };
  }, [clip?.id, clip?.project_id, clip?.start_time, clip?.end_time]);

  useEffect(() => {
    if (!draggingHandle || !clip || !waveformData) return;
    const handleMove = (e: PointerEvent) => {
      const el = waveTrackRef.current;
      if (!el) return;
      const rect = el.getBoundingClientRect();
      const ratio = Math.max(0, Math.min(1, (e.clientX - rect.left) / Math.max(1, rect.width)));
      const span = Math.max(1, waveformData.window_end - waveformData.window_start);
      const t = waveformData.window_start + ratio * span;
      if (draggingHandle === 'in') {
        const clamped = Math.max(waveformData.window_start, Math.min(trimDraftRef.current.end - 1.0, t));
        trimDraftRef.current.start = Number(clamped.toFixed(2));
        setTrimStart(trimDraftRef.current.start);
      } else {
        const clamped = Math.min(waveformData.window_end, Math.max(trimDraftRef.current.start + 1.0, t));
        trimDraftRef.current.end = Number(clamped.toFixed(2));
        setTrimEnd(trimDraftRef.current.end);
      }
    };
    const handleUp = () => {
      setDraggingHandle(null);
      const { start, end } = trimDraftRef.current;
      if (keepSegments.length > 1 && onUpdateSegments) {
        const nextSegs = keepSegments.map(([s, e]) => [s, e] as SegPair);
        nextSegs[0][0] = Math.min(nextSegs[0][1] - 0.4, start);
        nextSegs[nextSegs.length - 1][1] = Math.max(nextSegs[nextSegs.length - 1][0] + 0.4, end);
        onUpdateSegments(clip.id, nextSegs);
      } else if (
        onTrimClip &&
        (Math.abs(start - clip.start_time) > 0.05 || Math.abs(end - clip.end_time) > 0.05)
      ) {
        onTrimClip(clip.id, start, end);
      }
    };
    window.addEventListener('pointermove', handleMove);
    window.addEventListener('pointerup', handleUp);
    return () => {
      window.removeEventListener('pointermove', handleMove);
      window.removeEventListener('pointerup', handleUp);
    };
  }, [draggingHandle, clip, waveformData, keepSegments, onTrimClip, onUpdateSegments]);

  // Measure available preview stage so the chassis NEVER overflows width or height
  useEffect(() => {
    const el = stageRef.current;
    if (!el) return;
    const updateSize = () => {
      const rect = el.getBoundingClientRect();
      if (rect.width > 0 && rect.height > 0) {
        setStageSize({ w: Math.max(120, rect.width - 24), h: Math.max(120, rect.height - 24) });
      }
    };
    updateSize();
    const ro = new ResizeObserver(updateSize);
    ro.observe(el);
    return () => ro.disconnect();
  }, []);

  const resolvedUrl = clip?.video_url || videoUrl;
  const clipIsRendered = Boolean(
    clip &&
      (clip.status === 'ready' || clip.render_status === 'completed') &&
      (clip.rendered_path ||
        clip.rendered_video_path ||
        (resolvedUrl && !resolvedUrl.includes('/media/masters/')))
  );
  const activeVideoSrc = clipIsRendered ? resolvedUrl : undefined;

  useEffect(() => {
    if (seekTime !== undefined && videoRef.current && clip && clipIsRendered) {
      videoRef.current.currentTime = seekTime;
      setCurrentTime(seekTime);
      onPlaybackTimeChange?.(seekTime);
    }
  }, [seekTime]);

  // Force clean <video> reload & reset playback position whenever active clip or rendered URL changes
  useEffect(() => {
    if (!clip) {
      setIsPlaying(false);
      setCurrentTime(0);
      return;
    }

    setVideoVersion((v) => v + 1);
    setShowExactFrame(false);
    setExactFrameUrl(null);
    setDuration(clip.duration || 30);

    if (!pendingRestoreRef.current) {
      setCurrentTime(0);
      setIsPlaying(false);
    }
  }, [clip?.id, activeVideoSrc]);

  useEffect(() => {
    const vid = videoRef.current;
    if (vid && activeVideoSrc) {
      try {
        if (!pendingRestoreRef.current) {
          vid.currentTime = 0;
        }
        vid.load();
      } catch {
        // ignore pre-metadata load issues
      }
    }
  }, [clip?.id, activeVideoSrc, videoVersion]);

  const togglePlay = () => {
    if (!videoRef.current) return;
    if (isPlaying) {
      videoRef.current.pause();
      setIsPlaying(false);
    } else {
      setShowExactFrame(false);
      videoRef.current.play().catch(() => {});
      setIsPlaying(true);
    }
  };

  const toggleMute = () => {
    if (!videoRef.current) return;
    videoRef.current.muted = !isMuted;
    setIsMuted(!isMuted);
  };

  const isRenderedSource = clipIsRendered;
  const clipStartOffset = 0;
  const relCurrent = Math.max(0, Math.min(duration, currentTime - clipStartOffset));
  relCurrentRef.current = relCurrent;

  const emitToast = (message: string, type: 'success' | 'error' | 'info' = 'info') => {
    window.dispatchEvent(new CustomEvent('lh-toast', { detail: { message, type } }));
  };

  const handleBladeSplit = (customRelT?: number) => {
    if (!clip || !onUpdateSegments) return;
    const xfade = clip.merge_crossfade_sec ?? 0.03;
    const targetRel = typeof customRelT === 'number' ? customRelT : relCurrentRef.current;
    const { masterT, segIdx } = mapRelToMasterAndSeg(targetRel, keepSegments, xfade);
    if (segIdx < 0 || segIdx >= keepSegments.length) return;
    const [s, e] = keepSegments[segIdx];
    const splitPt = Number(masterT.toFixed(2));
    if (splitPt - s < 0.4 || e - splitPt < 0.4) {
      emitToast('Move playhead at least 0.4s inside a segment before splitting', 'info');
      return;
    }
    const nextSegs: SegPair[] = [
      ...keepSegments.slice(0, segIdx),
      [s, splitPt],
      [splitPt, e],
      ...keepSegments.slice(segIdx + 1)
    ];
    setKeepSegments(nextSegs);
    setSelectedSegIdx(segIdx + 1);
    onUpdateSegments(clip.id, nextSegs);
  };

  const handleDeleteSegment = (targetIdx?: number) => {
    if (!clip || !onUpdateSegments) return;
    if (keepSegments.length <= 1) {
      emitToast('Cannot delete the only remaining segment', 'info');
      return;
    }
    const xfade = clip.merge_crossfade_sec ?? 0.03;
    const idx =
      typeof targetIdx === 'number'
        ? targetIdx
        : selectedSegIdx !== null
        ? selectedSegIdx
        : mapRelToMasterAndSeg(relCurrentRef.current, keepSegments, xfade).segIdx;
    if (idx < 0 || idx >= keepSegments.length) return;
    const nextSegs = keepSegments.filter((_, i) => i !== idx);
    setKeepSegments(nextSegs);
    setSelectedSegIdx(nextSegs.length > 1 ? Math.min(idx, nextSegs.length - 1) : null);
    onUpdateSegments(clip.id, nextSegs);
  };

  const handleRunRetentionAudit = async (forceKeepOpen = false) => {
    if (!clip) return;
    if (!forceKeepOpen && isAuditOpen && retentionAudit) {
      setIsAuditOpen(false);
      return;
    }
    setIsAuditOpen(true);
    setIsAuditing(true);
    setAuditError(null);
    try {
      const report = await api.getRetentionAudit(clip.project_id, clip.id);
      setRetentionAudit(report as RetentionAuditReport);
    } catch (err: any) {
      setAuditError(err?.message || 'Retention audit failed');
    } finally {
      setIsAuditing(false);
    }
  };

  const handleApplyAuditFix = async (warning: RetentionWarning) => {
    if (!clip) return;
    const action = warning.fix_action;
    if (action.type === 'set_segments' && action.keep_segments && onUpdateSegments) {
      setKeepSegments(action.keep_segments);
      await onUpdateSegments(clip.id, action.keep_segments);
      await handleRunRetentionAudit(true);
    } else if (action.type === 'patch_clip' && action.updates && onApplyFixClip) {
      await onApplyFixClip(clip.id, action.updates);
      await handleRunRetentionAudit(true);
    }
  };

  const seekToRelTime = (nextRel: number) => {
    const clamped = Math.max(0, Math.min(duration || 30, nextRel));
    setCurrentTime(clamped);
    if (videoRef.current) videoRef.current.currentTime = clamped;
    onPlaybackTimeChange?.(clamped);
  };

  const cmdStateRef = useRef({
    clip,
    relCurrent,
    duration,
    keepSegments,
    onTrimClip,
    onUpdateSegments,
    onPlaybackTimeChange,
    handleBladeSplit,
  });
  cmdStateRef.current = {
    clip,
    relCurrent,
    duration,
    keepSegments,
    onTrimClip,
    onUpdateSegments,
    onPlaybackTimeChange,
    handleBladeSplit,
  };

  // Keyboard shortcut event bridge from App.tsx (Space, Arrows, I/O, B, M)
  useEffect(() => {
    const onPlayerCmd = (ev: Event) => {
      const detail = (ev as CustomEvent<{ type: string; delta?: number }>).detail;
      if (!detail) return;
      const vid = videoRef.current;
      const st = cmdStateRef.current;
      if (detail.type === 'togglePlay' && vid) {
        if (vid.paused) {
          setShowExactFrame(false);
          vid.play().catch(() => {});
          setIsPlaying(true);
        } else {
          vid.pause();
          setIsPlaying(false);
        }
      } else if (detail.type === 'toggleMute' && vid) {
        const nextMuted = !vid.muted;
        vid.muted = nextMuted;
        setIsMuted(nextMuted);
      } else if (detail.type === 'seekDelta') {
        const cur = vid ? vid.currentTime : st.relCurrent;
        const maxDur =
          vid && Number.isFinite(vid.duration) && vid.duration > 0
            ? vid.duration
            : Math.max(1, st.duration);
        const nextT = Math.max(0, Math.min(maxDur, cur + (detail.delta || 0)));
        if (vid) vid.currentTime = nextT;
        setCurrentTime(nextT);
        st.onPlaybackTimeChange?.(nextT);
      } else if (detail.type === 'bladeSplit') {
        st.handleBladeSplit(vid ? vid.currentTime : st.relCurrent);
      } else if (detail.type === 'trimIn' && st.clip && st.onTrimClip) {
        const targetAbs = Math.max(
          0,
          Math.min(st.clip.end_time - 0.5, (st.clip.start_time || 0) + st.relCurrent)
        );
        const rounded = Number(targetAbs.toFixed(2));
        setTrimStart(rounded);
        st.onTrimClip(st.clip.id, rounded, st.clip.end_time);
      } else if (detail.type === 'trimOut' && st.clip && st.onTrimClip) {
        const targetAbs = Math.max(
          st.clip.start_time + 0.5,
          (st.clip.start_time || 0) + st.relCurrent
        );
        const rounded = Number(targetAbs.toFixed(2));
        setTrimEnd(rounded);
        st.onTrimClip(st.clip.id, st.clip.start_time, rounded);
      }
    };
    window.addEventListener('lh-player-cmd', onPlayerCmd);
    return () => window.removeEventListener('lh-player-cmd', onPlayerCmd);
  }, []);

  const handleTimeUpdate = () => {
    if (videoRef.current) {
      const t = videoRef.current.currentTime;
      setCurrentTime(t);
      onPlaybackTimeChange?.(Math.max(0, t - clipStartOffset));
    }
  };

  const handleScrubberChange = (e: React.ChangeEvent<HTMLInputElement>) => {
    const newRel = parseFloat(e.target.value);
    const absolute = clipStartOffset + newRel;
    setCurrentTime(absolute);
    if (videoRef.current) {
      videoRef.current.currentTime = absolute;
    }
    onPlaybackTimeChange?.(newRel);
  };

  const formatMMSS = (secs: number) => {
    const safe = Math.max(0, Math.floor(secs || 0));
    const m = Math.floor(safe / 60);
    const s = safe % 60;
    return `${String(m).padStart(2, '0')}:${String(s).padStart(2, '0')}`;
  };

  const fetchExactFrame = async () => {
    if (!clip) return;
    setIsRenderingFrame(true);
    try {
      const { masterT: absTimestamp } = mapRelToMasterAndSeg(
        relCurrent,
        keepSegments,
        clip.merge_crossfade_sec ?? 0.03
      );
      const res = await api.getPreviewFrame({
        clip_id: clip.id,
        project_id: clip.project_id,
        timestamp: absTimestamp,
        clip_rel_ts: relCurrent,
        aspect_ratio: clip.aspect_ratio,
        custom_width: clip.custom_width,
        custom_height: clip.custom_height,
        frame_layout: clip.frame_layout,
        framing_template: clip.frame_layout,
        webcam_corner: clip.webcam_corner,
        webcam_split_ratio: clip.webcam_split_ratio,
        pip_border_color: clip.pip_border_color,
        clip_size: clip.clip_size,
        export_resolution: clip.clip_size,
        watermark_path: clip.watermark_path,
        watermark_corner: clip.watermark_corner,
        watermark_opacity: clip.watermark_opacity,
        watermark_size: clip.watermark_size,
        zoom: clip.zoom,
        pan_x_offset: clip.pan_x_offset,
        pan_x: Math.round((clip.pan_x_offset || 0) * 100),
        color_preset: clip.color_preset,
        exposure: clip.exposure,
        brightness: clip.brightness,
        contrast: clip.contrast,
        lift_mids: clip.lift_mids,
        warmth: clip.warmth,
        saturation: clip.saturation,
        black_and_white: clip.black_and_white,
        vintage: clip.vintage,
        tint_color: clip.tint_color,
        color_tint: clip.tint_color,
        tint_strength: clip.tint_strength,
        glow: clip.glow,
        smooth: clip.smooth,
        sharpness: clip.sharpness,
        blur: clip.blur,
        film_grain: clip.film_grain,
        vignette: typeof clip.vignette === 'number' ? clip.vignette : clip.vignette ? 1.0 : 0.0,
        bottom_fade: clip.bottom_fade,
        burn_captions: clip.burn_captions,
        timing_offset: clip.timing_offset,
        caption_style: clip.caption_style,
        style_preset: clip.caption_style,
        caption_font: clip.caption_font,
        font_family: clip.caption_font,
        caption_animation: clip.caption_animation,
        font_size_percent: clip.font_size_percent,
        font_size_pct: clip.font_size_percent,
        base_color: clip.base_color,
        active_color: clip.active_color,
        highlight_color: clip.active_color,
        outline_color: clip.outline_color,
        hold_back_alpha: clip.hold_back_alpha,
        hold_back_opacity: clip.hold_back_alpha,
        highlight_shape: clip.highlight_shape,
        position_grid: clip.position_grid,
        position_x: clip.position_x,
        pos_x: clip.position_x,
        position_y: clip.position_y,
        pos_y: clip.position_y,
        outline_width: clip.outline_width,
        outline: clip.outline_width,
        shadow_depth: clip.shadow_depth,
        shadow: clip.shadow_depth,
        glow_intensity: clip.glow_intensity,
        letter_spacing: clip.letter_spacing,
        bold: clip.bold,
        italic: clip.italic,
        all_caps: clip.all_caps,
        uppercase: clip.all_caps,
        text_align: clip.text_align,
        alignment: clip.text_align,
        emoji_enabled: clip.emoji_enabled,
        auto_emoji: clip.emoji_enabled,
        highlight_power_words: Boolean(clip.highlight_power_words),
        how_phrase_arrives: clip.how_phrase_arrives,
        word_by_word: (clip.how_phrase_arrives || '').toLowerCase().replace('-', ' ') === 'word by word',
        clean_filler_words: clip.clean_filler_words,
        censor_profanity: clip.censor_profanity,
        show_hook_banner: clip.show_hook_banner,
        hook_banner_enabled: clip.show_hook_banner,
        hook_headline: clip.hook_headline,
        retention_bar_enabled: clip.retention_bar_enabled,
        retention_bar_color: clip.retention_bar_color,
        outro_cta_enabled: clip.outro_cta_enabled,
        outro_cta_text: clip.outro_cta_text,
        words: clip.transcript_words,
        keep_segments: keepSegments,
        merge_crossfade_sec: clip.merge_crossfade_sec,
        speakers: clip.speakers,
        speaker_lower_thirds: clip.speaker_lower_thirds,
        face_safe_captions: clip.face_safe_captions,
        callout_cards: clip.callout_cards,
        auto_callouts: clip.auto_callouts,
        bilingual_enabled: clip.bilingual_enabled,
        secondary_language: clip.secondary_language,
        backdrop_mode: clip.backdrop_mode,
        backdrop_blur_intensity: clip.backdrop_blur_intensity,
        backdrop_color: clip.backdrop_color,
        privacy_blur_boxes: clip.privacy_blur_boxes,
        beat_sync_enabled: clip.beat_sync_enabled,
        spectrum_enabled: clip.spectrum_enabled,
        spectrum_position: clip.spectrum_position,
        spectrum_color: clip.spectrum_color,
        chapters_enabled: clip.chapters_enabled,
        chapters: clip.chapters,
        handle_watermark_enabled: clip.handle_watermark_enabled,
        handle_text: clip.handle_text,
        handle_platform_icon: clip.handle_platform_icon,
        handle_corner: clip.handle_corner,
        handle_opacity: clip.handle_opacity,
        handle_display_mode: clip.handle_display_mode
      });
      setExactFrameUrl(res.frame_url);
      setFrameLatencyMs(res.render_time_ms);
      setShowExactFrame(true);
    } catch (err) {
      console.error('Failed to render exact frame:', err);
    } finally {
      setIsRenderingFrame(false);
    }
  };

  const previewConfigSig = clip
    ? JSON.stringify({
        aspect_ratio: clip.aspect_ratio,
        custom_width: clip.custom_width,
        custom_height: clip.custom_height,
        frame_layout: clip.frame_layout,
        webcam_corner: clip.webcam_corner,
        zoom: clip.zoom,
        pan_x_offset: clip.pan_x_offset,
        color_preset: clip.color_preset,
        exposure: clip.exposure,
        brightness: clip.brightness,
        contrast: clip.contrast,
        lift_mids: clip.lift_mids,
        warmth: clip.warmth,
        saturation: clip.saturation,
        black_and_white: clip.black_and_white,
        vintage: clip.vintage,
        tint_color: clip.tint_color,
        color_tint: clip.tint_color,
        tint_strength: clip.tint_strength,
        glow: clip.glow,
        smooth: clip.smooth,
        sharpness: clip.sharpness,
        blur: clip.blur,
        film_grain: clip.film_grain,
        vignette: clip.vignette,
        bottom_fade: clip.bottom_fade,
        burn_captions: clip.burn_captions,
        timing_offset: clip.timing_offset,
        caption_style: clip.caption_style,
        caption_font: clip.caption_font,
        caption_animation: clip.caption_animation,
        font_size_percent: clip.font_size_percent,
        base_color: clip.base_color,
        active_color: clip.active_color,
        outline_color: clip.outline_color,
        hold_back_alpha: clip.hold_back_alpha,
        highlight_shape: clip.highlight_shape,
        position_grid: clip.position_grid,
        position_x: clip.position_x,
        position_y: clip.position_y,
        outline_width: clip.outline_width,
        shadow_depth: clip.shadow_depth,
        glow_intensity: clip.glow_intensity,
        letter_spacing: clip.letter_spacing,
        bold: clip.bold,
        italic: clip.italic,
        all_caps: clip.all_caps,
        text_align: clip.text_align,
        emoji_enabled: clip.emoji_enabled,
        highlight_power_words: clip.highlight_power_words,
        how_phrase_arrives: clip.how_phrase_arrives,
        clean_filler_words: clip.clean_filler_words,
        censor_profanity: clip.censor_profanity,
        tighten_pacing: clip.tighten_pacing,
        silence_threshold: clip.silence_threshold,
        start_time: clip.start_time,
        end_time: clip.end_time,
        show_hook_banner: clip.show_hook_banner,
        hook_headline: clip.hook_headline,
        transcript_words: clip.transcript_words,
        clip_size: clip.clip_size,
        watermark_path: clip.watermark_path,
        watermark_corner: clip.watermark_corner,
        watermark_opacity: clip.watermark_opacity,
        bgm_path: clip.bgm_path,
        bgm_volume: clip.bgm_volume,
        audio_volume: clip.audio_volume,
        normalize_audio: clip.normalize_audio,
        vocal_clarity: clip.vocal_clarity,
        voice_isolation: clip.voice_isolation,
        auto_sfx: clip.auto_sfx,
        dynamic_punch_zoom: clip.dynamic_punch_zoom,
        punch_zoom_intensity: clip.punch_zoom_intensity,
        retention_bar_enabled: clip.retention_bar_enabled,
        retention_bar_color: clip.retention_bar_color,
        outro_cta_enabled: clip.outro_cta_enabled,
        outro_cta_text: clip.outro_cta_text,
        broll_segments: clip.broll_segments,
        keep_segments: clip.keep_segments,
        merge_crossfade_sec: clip.merge_crossfade_sec,
        speakers: clip.speakers,
        speaker_lower_thirds: clip.speaker_lower_thirds,
        face_safe_captions: clip.face_safe_captions,
        callout_cards: clip.callout_cards,
        auto_callouts: clip.auto_callouts,
        bilingual_enabled: clip.bilingual_enabled,
        secondary_language: clip.secondary_language,
        dubbing_enabled: clip.dubbing_enabled,
        dubbing_language: clip.dubbing_language,
        dubbing_voice: clip.dubbing_voice,
        dubbing_bed_volume: clip.dubbing_bed_volume,
        dubbed_audio_path: clip.dubbed_audio_path,
        backdrop_mode: clip.backdrop_mode,
        backdrop_blur_intensity: clip.backdrop_blur_intensity,
        backdrop_color: clip.backdrop_color,
        privacy_blur_boxes: clip.privacy_blur_boxes,
        beat_sync_enabled: clip.beat_sync_enabled,
        spectrum_enabled: clip.spectrum_enabled,
        spectrum_position: clip.spectrum_position,
        spectrum_color: clip.spectrum_color,
        chapters_enabled: clip.chapters_enabled,
        chapters: clip.chapters,
        handle_watermark_enabled: clip.handle_watermark_enabled,
        handle_text: clip.handle_text,
        handle_platform_icon: clip.handle_platform_icon,
        handle_corner: clip.handle_corner,
        handle_opacity: clip.handle_opacity,
        handle_display_mode: clip.handle_display_mode
      })
    : '';

  useEffect(() => {
    if (!clip) return;

    const triggerFastPreviewRender = (
      targetClipId: number,
      targetProjectId: number,
      dispatchedSig: string,
      isInitialPrepare: boolean
    ) => {
      if (!onAutoRenderClip) return;
      if (autoRenderAbortRef.current) {
        autoRenderAbortRef.current.abort();
        autoRenderAbortRef.current = null;
      }

      const controller = new AbortController();
      autoRenderAbortRef.current = controller;
      if (isInitialPrepare) {
        preparingClipIdRef.current = targetClipId;
        setIsPreparingClip(true);
      }
      setIsAutoRendering(true);

      onAutoRenderClip(targetProjectId, targetClipId, controller.signal)
        .then((res) => {
          if (controller.signal.aborted || res.status === 'superseded') return;
          if (res.status === 'completed') {
            lastRenderedHashByClipRef.current[targetClipId] = dispatchedSig;
            const nextUrl = res.video_url || res.preview_url;
            if (nextUrl) {
              if (isInitialPrepare) {
                pendingRestoreRef.current = null;
                shouldAutoPlayRef.current = true;
              } else {
                const shouldResume = Boolean(videoRef.current && !videoRef.current.paused);
                pendingRestoreRef.current = {
                  relTime: relCurrentRef.current,
                  shouldPlay: shouldResume
                };
              }
              setShowExactFrame(false);
              onAutoRenderComplete?.(targetClipId, nextUrl, res.rendered_path);
            }
          }
        })
        .catch((err: any) => {
          if (err?.name !== 'AbortError') {
            console.error('Auto-render failed:', err);
          }
        })
        .finally(() => {
          if (preparingClipIdRef.current === targetClipId) {
            preparingClipIdRef.current = null;
            setIsPreparingClip(false);
          }
          if (autoRenderAbortRef.current === controller) {
            setIsAutoRendering(false);
            autoRenderAbortRef.current = null;
          }
        });
    };

    if (prevClipIdRef.current !== clip.id) {
      const switchedFromAnother = prevClipIdRef.current !== null || hasSwitchedClipRef.current;
      prevClipIdRef.current = clip.id;
      hasSwitchedClipRef.current = true;
      pendingRestoreRef.current = null;

      if (debounceTimerRef.current !== null) {
        window.clearTimeout(debounceTimerRef.current);
        debounceTimerRef.current = null;
      }
      if (autoRenderAbortRef.current) {
        autoRenderAbortRef.current.abort();
        autoRenderAbortRef.current = null;
      }
      preparingClipIdRef.current = null;
      setShowExactFrame(false);
      setExactFrameUrl(null);
      setIsAutoRendering(false);

      if (clipIsRendered) {
        lastRenderedHashByClipRef.current[clip.id] = previewConfigSig;
        setIsPreparingClip(false);
        shouldAutoPlayRef.current = switchedFromAnother;
        return;
      }

      // Clip is not rendered yet: immediately fire fast_preview so clicking any clip plays cleanly
      if (onAutoRenderClip) {
        shouldAutoPlayRef.current = true;
        triggerFastPreviewRender(clip.id, clip.project_id, previewConfigSig, true);
      } else {
        setIsPreparingClip(false);
      }
      return;
    }

    // Do not queue edit debounce while the initial unrendered clip preview is still preparing
    if (preparingClipIdRef.current === clip.id) return;

    if (lastRenderedHashByClipRef.current[clip.id] === previewConfigSig) return;

    if (debounceTimerRef.current !== null) {
      window.clearTimeout(debounceTimerRef.current);
    }

    if (autoRender && onAutoRenderClip) {
      const dispatchedSig = previewConfigSig;
      const targetClipId = clip.id;
      const targetProjectId = clip.project_id;

      debounceTimerRef.current = window.setTimeout(() => {
        triggerFastPreviewRender(targetClipId, targetProjectId, dispatchedSig, false);
      }, 900);

      return () => {
        if (debounceTimerRef.current !== null) {
          window.clearTimeout(debounceTimerRef.current);
        }
      };
    }

    if (isPlaying) return;
    debounceTimerRef.current = window.setTimeout(() => {
      void fetchExactFrame();
    }, 350);

    return () => {
      if (debounceTimerRef.current !== null) {
        window.clearTimeout(debounceTimerRef.current);
      }
    };
  }, [clip?.id, previewConfigSig, autoRender, isPlaying, clipIsRendered]);

  // Safe-Zone options mapped cleanly to existing backend values
  const safeZones = [
    { value: 'No guide', label: 'None' },
    { value: 'TikTok', label: 'TikTok' },
    { value: 'Instagram', label: 'Reels' },
    { value: 'YT Shorts', label: 'Shorts' },
    { value: 'All', label: 'All' }
  ];

  // Exact aspect ratio math for guaranteed zero-overflow sizing
  const getAspectNumeric = (ar?: string) => {
    switch (ar) {
      case '16:9':
        return 16 / 9;
      case '1:1':
        return 1;
      case '4:5':
        return 4 / 5;
      case '4:3':
        return 4 / 3;
      case '9:16':
      default:
        return 9 / 16;
    }
  };

  const ratio = getAspectNumeric(clip?.aspect_ratio);
  let frameWidth = stageSize.w;
  let frameHeight = frameWidth / ratio;
  if (frameHeight > stageSize.h) {
    frameHeight = stageSize.h;
    frameWidth = frameHeight * ratio;
  }

  const isCompleted = Boolean(
    clip && (clip.status === 'ready' || clip.render_status === 'completed')
  );
  const downloadUrl = clip ? api.getDownloadUrl(clip.project_id, clip.id) : '#';

  const videoFilterAlreadyBaked = Boolean(
    clip && isRenderedSource && lastRenderedHashByClipRef.current[clip.id] === previewConfigSig
  );

  const videoCssFilter =
    clip && !videoFilterAlreadyBaked
      ? (() => {
          const b = Math.max(0.25, 1 + (clip.brightness || 0) + (clip.exposure || 0) * 0.65);
          const c = Math.max(0.3, clip.contrast || 1.0);
          const bw = Math.max(0, Math.min(1, clip.black_and_white || 0));
          const s = Math.max(0, (clip.saturation ?? 1.0) * (1 - bw));
          const sep = Math.max(0, Math.min(1, (clip.vintage || 0) * 0.65));
          const bl = Math.max(0, (clip.blur || 0) * 6);
          const wHue = (clip.warmth || 0) * -22;
          const parts = [
            `brightness(${b.toFixed(2)})`,
            `contrast(${c.toFixed(2)})`,
            `saturate(${s.toFixed(2)})`,
          ];
          if (bw > 0.01) parts.push(`grayscale(${bw.toFixed(2)})`);
          if (sep > 0.01) parts.push(`sepia(${sep.toFixed(2)})`);
          if (Math.abs(wHue) > 0.5) parts.push(`hue-rotate(${wHue.toFixed(1)}deg)`);
          if (bl > 0.05) parts.push(`blur(${bl.toFixed(1)}px)`);
          return parts.join(' ');
        })()
      : 'none';

  const handleOpenPublish = () => {
    if (clip) {
      setPublishTitle(clip.selected_title || clip.youtube_titles?.[0] || 'Viral Video #Shorts');
      const baseDesc = (clip.youtube_description || 'Created with Lost Heaven Studio! #Shorts').trim();
      const extraTags = (clip.hashtags || '').trim();
      setPublishDesc(extraTags && !baseDesc.includes(extraTags) ? `${baseDesc}\n\n${extraTags}` : baseDesc);
      setPublishPrivacy(clip.privacy_status || 'private');
      setPublishResult(null);
      setSocialCopy(null);
    }
    setIsPublishModalOpen(true);
  };

  const handleExportPublishPack = async () => {
    if (!clip) return;
    setIsExportingPack(true);
    setPackError(null);
    try {
      await api.exportPublishPack(clip.project_id, clip.id, 'fast_preview');
    } catch (err: any) {
      setPackError(err?.message || 'Failed to build Publish Pack .zip');
    } finally {
      setIsExportingPack(false);
    }
  };

  const handleGenerateSocialCopy = async () => {
    if (!clip) return;
    setIsGeneratingCopy(true);
    try {
      const res = await api.generateSocialCopy(clip.project_id, clip.id);
      const plats = res?.platforms as Partial<PlatformCopyMap> | undefined;
      if (plats) {
        setSocialCopy(plats);
        const cur = plats[activeCopyPlatform] || plats.youtube_shorts;
        if (cur && activeCopyPlatform === 'youtube_shorts') {
          if (cur.title) setPublishTitle(cur.title);
          const tags = Array.isArray(cur.hashtags) ? cur.hashtags.join(' ') : '';
          setPublishDesc([cur.description || '', tags].filter(Boolean).join('\n\n'));
        }
      }
    } catch (err: any) {
      setPublishResult(`Error: ${err?.message || 'Failed to generate copy'}`);
    } finally {
      setIsGeneratingCopy(false);
    }
  };

  const handleCopyText = (key: string, text: string) => {
    navigator.clipboard?.writeText(text).catch(() => {});
    setCopiedField(key);
    window.dispatchEvent(
      new CustomEvent('lh-toast', {
        detail: { message: 'Copied social copy & hashtags to clipboard', type: 'success' },
      })
    );
    window.setTimeout(() => setCopiedField(null), 1500);
  };

  const handlePublishSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!clip) return;
    setIsPublishing(true);
    setPublishResult(null);
    try {
      const res = await api.publishToYouTube(clip.id, {
        title: publishTitle,
        description: publishDesc,
        privacy_status: publishPrivacy
      });
      setPublishResult(res.url || 'Published successfully!');
    } catch (err: any) {
      setPublishResult(`Error: ${err.message || err}`);
    } finally {
      setIsPublishing(false);
    }
  };

  return (
    <aside className="w-[340px] xl:w-[390px] shrink-0 h-full min-h-0 box-border overflow-hidden border-l border-[var(--border-subtle)] bg-[var(--bg-surface)] flex flex-col select-none">
      {/* Top Control Strip: Safe-Zone Pills + Auto-render + Exact Frame */}
      <div className="px-3 py-2 border-b border-[var(--border-subtle)] flex items-center justify-between gap-1.5 shrink-0">
        <div className="flex items-center gap-0.5 bg-[var(--bg-input)] p-0.5 rounded-lg border border-[var(--border-subtle)] min-w-0">
          {safeZones.map((g) => (
            <button
              key={g.value}
              type="button"
              onClick={() => onSafeZoneChange(g.value)}
              className={`px-1.5 py-1 rounded-md text-[10px] font-semibold transition whitespace-nowrap ${
                safeZoneGuide === g.value
                  ? 'bg-[var(--accent-primary)] text-[var(--accent-foreground)]'
                  : 'text-[var(--text-secondary)] hover:text-[var(--text-primary)]'
              }`}
            >
              {g.label}
            </button>
          ))}
        </div>

        <div className="flex items-center gap-1 shrink-0">
          <button
            type="button"
            onClick={toggleAutoRender}
            className={`flex items-center gap-1 px-2 py-1 rounded-lg text-[10px] font-semibold border transition whitespace-nowrap ${
              autoRender
                ? 'bg-[var(--accent-subtle)] border-[var(--accent-primary)] text-[var(--accent-primary)]'
                : 'bg-[var(--bg-card)] border-[var(--border-subtle)] text-[var(--text-secondary)] hover:text-[var(--text-primary)]'
            }`}
            title="Automatically render fast preview MP4 when edits change (900ms debounce)"
          >
            {isAutoRendering ? (
              <Loader2 className="w-3 h-3 animate-spin" />
            ) : (
              <Zap className="w-3 h-3" />
            )}
            <span>{isAutoRendering ? 'Syncing' : 'Auto-render'}</span>
          </button>

          <button
            type="button"
            onClick={() => {
              if (!showExactFrame) fetchExactFrame();
              else setShowExactFrame(false);
            }}
            disabled={isRenderingFrame || !clip}
            className={`flex items-center gap-1 px-2 py-1 rounded-lg text-[10px] font-semibold border transition shrink-0 tabular-nums whitespace-nowrap ${
              showExactFrame
                ? 'bg-[var(--accent-subtle)] border-[var(--accent-primary)] text-[var(--accent-primary)]'
                : 'bg-[var(--bg-card)] border-[var(--border-subtle)] text-[var(--text-secondary)] hover:text-[var(--text-primary)]'
            }`}
            title="Render single exact graded FFmpeg frame"
          >
            {isRenderingFrame ? (
              <RefreshCw className="w-3 h-3 animate-spin text-[var(--accent-primary)]" />
            ) : (
              <Eye className="w-3 h-3" />
            )}
            <span>
              {isRenderingFrame
                ? 'Frame'
                : showExactFrame
                ? `Live${frameLatencyMs !== null ? ` ${frameLatencyMs}ms` : ''}`
                : 'Frame'}
            </span>
          </button>

          <button
            type="button"
            onClick={() => void handleRunRetentionAudit()}
            disabled={isAuditing || !clip}
            className={`flex items-center gap-1 px-2 py-1 rounded-lg text-[10px] font-semibold border transition shrink-0 tabular-nums whitespace-nowrap ${
              isAuditOpen
                ? 'bg-[var(--accent-subtle)] border-[var(--accent-primary)] text-[var(--accent-primary)]'
                : 'bg-[var(--bg-card)] border-[var(--border-subtle)] text-[var(--text-secondary)] hover:text-[var(--text-primary)]'
            }`}
            title="Second-by-second retention curve & drop-off auditor"
          >
            {isAuditing ? (
              <Loader2 className="w-3 h-3 animate-spin text-[var(--accent-primary)]" />
            ) : (
              <Activity className="w-3 h-3" />
            )}
            <span>
              {retentionAudit ? `${retentionAudit.overall_score}` : 'Audit'}
            </span>
          </button>
        </div>
      </div>

      {/* Expandable Retention Drop-Off Auditor Drawer */}
      {isAuditOpen && clip && (
        <div className="border-b border-[var(--border-subtle)] bg-[var(--bg-surface)] px-3 py-2.5 space-y-2 shrink-0">
          <div className="flex items-center justify-between gap-2">
            <div className="flex items-center gap-2 min-w-0">
              <Activity className="w-3.5 h-3.5 text-[var(--accent-primary)] shrink-0" />
              <span className="text-[11px] font-bold text-[var(--text-primary)] truncate">
                Retention Audit
              </span>
              {retentionAudit && (
                <span
                  className={`text-[10px] font-bold px-1.5 py-0.5 rounded-full tabular-nums ${
                    retentionAudit.overall_score >= 78
                      ? 'bg-emerald-500/15 text-emerald-400'
                      : retentionAudit.overall_score >= 58
                      ? 'bg-amber-500/15 text-amber-400'
                      : 'bg-red-500/15 text-red-400'
                  }`}
                >
                  {retentionAudit.overall_score}/100
                </span>
              )}
            </div>
            <div className="flex items-center gap-1.5 shrink-0">
              <button
                type="button"
                onClick={() => void handleRunRetentionAudit(true)}
                disabled={isAuditing}
                className="p-1 rounded hover:bg-[var(--bg-hover)] text-[var(--text-secondary)] hover:text-[var(--text-primary)]"
                title="Re-run retention audit"
              >
                <RefreshCw className={`w-3 h-3 ${isAuditing ? 'animate-spin' : ''}`} />
              </button>
              <button
                type="button"
                onClick={() => setIsAuditOpen(false)}
                className="p-1 rounded hover:bg-[var(--bg-hover)] text-[var(--text-secondary)] hover:text-[var(--text-primary)]"
                title="Close retention audit"
              >
                <X className="w-3 h-3" />
              </button>
            </div>
          </div>

          {isAuditing && !retentionAudit && (
            <div className="py-4 flex items-center justify-center gap-2 text-[11px] text-[var(--text-secondary)]">
              <Loader2 className="w-3.5 h-3.5 animate-spin text-[var(--accent-primary)]" />
              <span>Auditing hook velocity, dead-air gaps &amp; framing...</span>
            </div>
          )}

          {auditError && (
            <div className="text-[11px] text-red-400 bg-red-500/10 border border-red-500/20 rounded-lg px-2.5 py-1.5">
              {auditError}
            </div>
          )}

          {retentionAudit && (
            <>
              <div className="flex items-center justify-between text-[10px] text-[var(--text-secondary)] font-mono tabular-nums">
                <span>Hook: {retentionAudit.time_to_first_word.toFixed(2)}s</span>
                <span>Dead air: {retentionAudit.dead_air_count}</span>
                <span>Span: {retentionAudit.effective_duration.toFixed(1)}s</span>
              </div>

              {/* Interactive Retention Curve SVG */}
              {retentionAudit.retention_curve.length > 0 && (() => {
                const pts = retentionAudit.retention_curve;
                const totalDur = Math.max(1, retentionAudit.effective_duration || pts.length);
                const polyPts = pts
                  .map((pt, idx) => {
                    const x = pts.length > 1 ? (idx / (pts.length - 1)) * 300 : 150;
                    const y = 60 - (Math.max(0, Math.min(100, pt.score)) / 100) * 52;
                    return `${x.toFixed(1)},${y.toFixed(1)}`;
                  })
                  .join(' ');
                const areaPts = `0,60 ${polyPts} 300,60`;
                const playX = Math.max(0, Math.min(300, (relCurrent / totalDur) * 300));
                return (
                  <div className="rounded-lg bg-[var(--bg-input)] border border-[var(--border-subtle)] p-1.5">
                    <svg
                      viewBox="0 0 300 64"
                      className="w-full h-14 cursor-crosshair block"
                      onClick={(e) => {
                        const rect = e.currentTarget.getBoundingClientRect();
                        const ratio = Math.max(0, Math.min(1, (e.clientX - rect.left) / Math.max(1, rect.width)));
                        seekToRelTime(ratio * totalDur);
                      }}
                    >
                      <defs>
                        <linearGradient id="lhRetentionGrad" x1="0" y1="0" x2="0" y2="1">
                          <stop offset="0%" stopColor="var(--accent-primary)" stopOpacity="0.38" />
                          <stop offset="100%" stopColor="var(--accent-primary)" stopOpacity="0.03" />
                        </linearGradient>
                      </defs>
                      <line x1="0" y1="21" x2="300" y2="21" stroke="var(--border-subtle)" strokeDasharray="2 2" strokeWidth="0.6" />
                      <line x1="0" y1="42" x2="300" y2="42" stroke="var(--border-subtle)" strokeDasharray="2 2" strokeWidth="0.6" />
                      <polygon points={areaPts} fill="url(#lhRetentionGrad)" />
                      <polyline
                        fill="none"
                        stroke="var(--accent-primary)"
                        strokeWidth="1.8"
                        strokeLinejoin="round"
                        points={polyPts}
                      />
                      <line x1={playX} y1="2" x2={playX} y2="62" stroke="var(--text-primary)" strokeWidth="1" opacity="0.85" />
                      {retentionAudit.drop_off_warnings.map((w) => {
                        const wx = Math.max(5, Math.min(295, (w.timestamp / totalDur) * 300));
                        const color = w.severity === 'high' ? '#EF4444' : w.severity === 'medium' ? '#F59E0B' : '#38BDF8';
                        return (
                          <g
                            key={w.id}
                            className="cursor-pointer"
                            onClick={(ev) => {
                              ev.stopPropagation();
                              seekToRelTime(w.timestamp);
                            }}
                          >
                            <line x1={wx} y1="8" x2={wx} y2="60" stroke={color} strokeWidth="0.9" strokeDasharray="2 2" opacity="0.7" />
                            <circle cx={wx} cy="9" r="3.5" fill={color} />
                          </g>
                        );
                      })}
                    </svg>
                  </div>
                );
              })()}

              {/* Timestamped Warning Pins & 1-Click Fix List */}
              {retentionAudit.drop_off_warnings.length === 0 ? (
                <div className="text-[11px] text-emerald-400 bg-emerald-500/10 border border-emerald-500/20 rounded-lg px-2.5 py-1.5 flex items-center gap-1.5">
                  <CheckCircle2 className="w-3.5 h-3.5 shrink-0" />
                  <span>Strong pacing — no major drop-off risks detected.</span>
                </div>
              ) : (
                <div className="space-y-1.5 max-h-36 overflow-y-auto pr-0.5">
                  {retentionAudit.drop_off_warnings.map((w) => {
                    const pillColor =
                      w.severity === 'high'
                        ? 'bg-red-500/15 text-red-400 border-red-500/30'
                        : w.severity === 'medium'
                        ? 'bg-amber-500/15 text-amber-400 border-amber-500/30'
                        : 'bg-sky-500/15 text-sky-400 border-sky-500/30';
                    return (
                      <div
                        key={w.id}
                        className="flex items-center justify-between gap-2 p-1.5 rounded-lg bg-[var(--bg-card)] border border-[var(--border-subtle)] text-[10px]"
                      >
                        <button
                          type="button"
                          onClick={() => seekToRelTime(w.timestamp)}
                          className="flex items-start gap-1.5 text-left min-w-0 flex-1 hover:opacity-90"
                          title={`Jump to ${w.timestamp.toFixed(1)}s`}
                        >
                          <span className={`px-1.5 py-0.5 rounded border font-mono shrink-0 tabular-nums ${pillColor}`}>
                            {formatMMSS(w.timestamp)}
                          </span>
                          <span className="text-[var(--text-primary)] leading-snug line-clamp-2">
                            {w.issue}
                          </span>
                        </button>
                        <button
                          type="button"
                          onClick={() => void handleApplyAuditFix(w)}
                          className="px-2 py-1 rounded-md bg-[var(--accent-primary)] text-[var(--accent-foreground)] font-bold shrink-0 hover:brightness-110 transition"
                        >
                          {w.fix_label}
                        </button>
                      </div>
                    );
                  })}
                </div>
              )}
            </>
          )}
        </div>
      )}

      {/* Auto-Scaled Preview Stage */}
      <div
        ref={stageRef}
        className="flex-1 min-h-0 flex items-center justify-center p-3 overflow-hidden bg-[var(--bg-canvas)]"
      >
        <div
          style={{ width: `${Math.floor(frameWidth)}px`, height: `${Math.floor(frameHeight)}px` }}
          className="relative bg-black rounded-[24px] p-1.5 border-2 border-[var(--border-subtle)] shadow-xl flex flex-col overflow-hidden transition-all duration-200"
        >
          <div
            ref={previewStageBoxRef}
            className="relative w-full h-full rounded-[18px] overflow-hidden bg-[#0A0A0A] flex items-center justify-center"
          >
            {showExactFrame && exactFrameUrl ? (
              <img
                src={exactFrameUrl}
                alt="Exact FFmpeg Graded Frame"
                className="w-full h-full object-cover"
              />
            ) : activeVideoSrc ? (
              <video
                key={`${clip?.project_id ?? 0}-${clip?.id ?? 0}-${videoVersion}`}
                ref={videoRef}
                src={activeVideoSrc}
                muted={isMuted}
                preload="auto"
                onPlay={() => setIsPlaying(true)}
                onPause={() => setIsPlaying(false)}
                onTimeUpdate={handleTimeUpdate}
                onLoadedMetadata={() => {
                  const vid = videoRef.current;
                  if (!vid) return;
                  if (Number.isFinite(vid.duration) && vid.duration > 0) {
                    setDuration(vid.duration);
                  }
                  const restore = pendingRestoreRef.current;
                  if (restore) {
                    vid.currentTime = restore.relTime;
                    setCurrentTime(restore.relTime);
                    if (restore.shouldPlay) {
                      vid.play().catch(() => {});
                    }
                    pendingRestoreRef.current = null;
                  } else {
                    vid.currentTime = 0;
                    setCurrentTime(0);
                    if (shouldAutoPlayRef.current) {
                      shouldAutoPlayRef.current = false;
                      vid.play().catch(() => {});
                    }
                  }
                }}
                onClick={togglePlay}
                style={{ filter: videoCssFilter }}
                className="w-full h-full object-cover cursor-pointer transition-[filter] duration-75"
                playsInline
                loop
              />
            ) : (
              <div className="flex flex-col items-center justify-center text-zinc-500 p-4 text-center">
                {isPreparingClip ? (
                  <>
                    <Loader2 className="w-8 h-8 mb-2 animate-spin text-[var(--accent-primary)]" />
                    <span className="text-xs font-semibold text-white">Preparing preview...</span>
                    <span className="text-[10px] text-zinc-400 mt-1">
                      Rendering fast MP4 preview
                    </span>
                  </>
                ) : (
                  <>
                    <Smartphone className="w-10 h-10 mb-2 stroke-1 opacity-70" />
                    <span className="text-xs font-medium">No video preview</span>
                    <span className="text-[10px] opacity-70 mt-1">Select a clip or render MP4</span>
                  </>
                )}
              </div>
            )}

            {isPreparingClip && activeVideoSrc && (
              <div className="absolute inset-0 z-30 bg-black/65 backdrop-blur-[2px] flex flex-col items-center justify-center p-4 text-center pointer-events-none">
                <Loader2 className="w-7 h-7 mb-2 animate-spin text-[var(--accent-primary)]" />
                <span className="text-xs font-semibold text-white">Preparing preview...</span>
              </div>
            )}

            {/* Top Hook Banner Overlay */}
            {clip?.show_hook_banner && clip?.hook_headline && (
              <div className="absolute top-6 left-3 right-3 z-20 pointer-events-none flex justify-center">
                <div className="px-2.5 py-1 rounded-lg bg-black/80 border border-yellow-400 text-yellow-300 font-black text-[10px] uppercase tracking-wide shadow text-center leading-tight">
                  ★ {clip.hook_headline} ★
                </div>
              </div>
            )}

            {/* Interactive Tracked Privacy / HUD Blur Box Handles */}
            {(blurDraftBoxes || clip?.privacy_blur_boxes || []).map((box, bIdx, allBoxes) => (
              <div
                key={box.id || bIdx}
                onPointerDown={(ev) => {
                  ev.preventDefault();
                  ev.stopPropagation();
                  blurDragRef.current = {
                    idx: bIdx,
                    kind: 'move',
                    startX: ev.clientX,
                    startY: ev.clientY,
                    origBox: { ...box },
                    latest: allBoxes.map((x) => ({ ...x })),
                  };
                  setDraggingBlurIdx(bIdx);
                }}
                style={{
                  left: `${box.x * 100}%`,
                  top: `${box.y * 100}%`,
                  width: `${box.w * 100}%`,
                  height: `${box.h * 100}%`,
                }}
                className="absolute z-25 border-2 border-dashed border-amber-400 bg-black/35 backdrop-blur-xs rounded cursor-move select-none group"
                title={`Drag to reposition Blur #${bIdx + 1} (${box.mode})`}
              >
                <span className="absolute -top-4 left-0 px-1 py-0.2 rounded bg-amber-400 text-black font-mono font-bold text-[8px] whitespace-nowrap pointer-events-none">
                  #{bIdx + 1} {box.mode === 'pixelate' ? 'PIX' : 'BLUR'}
                </span>
                <div
                  onPointerDown={(ev) => {
                    ev.preventDefault();
                    ev.stopPropagation();
                    blurDragRef.current = {
                      idx: bIdx,
                      kind: 'resize',
                      startX: ev.clientX,
                      startY: ev.clientY,
                      origBox: { ...box },
                      latest: allBoxes.map((x) => ({ ...x })),
                    };
                    setDraggingBlurIdx(bIdx);
                  }}
                  className="absolute -bottom-1.5 -right-1.5 w-3.5 h-3.5 rounded-sm bg-amber-400 border border-black cursor-nwse-resize"
                  title="Drag corner to resize blur region"
                />
              </div>
            ))}

            {/* Platform Safe-Zone Overlay */}
            {safeZoneGuide !== 'No guide' && (
              <div className="absolute inset-0 pointer-events-none z-10 flex flex-col justify-between p-3">
                <div className="w-full border-t border-dashed border-[var(--accent-primary)]/60 pt-0.5 text-[9px] text-[var(--accent-primary)] font-mono">
                  {safeZoneGuide} Safe Top
                </div>

                {(safeZoneGuide === 'TikTok' || safeZoneGuide === 'YT Shorts' || safeZoneGuide === 'All') && (
                  <div className="absolute right-2 bottom-16 flex flex-col items-center gap-2.5 text-white/80">
                    <div className="flex flex-col items-center">
                      <div className="w-7 h-7 rounded-full bg-black/45 flex items-center justify-center border border-white/20">
                        <Heart className="w-3.5 h-3.5 text-white" />
                      </div>
                      <span className="text-[8px] font-mono mt-0.5">84K</span>
                    </div>
                    <div className="flex flex-col items-center">
                      <div className="w-7 h-7 rounded-full bg-black/45 flex items-center justify-center border border-white/20">
                        <MessageCircle className="w-3.5 h-3.5 text-white" />
                      </div>
                      <span className="text-[8px] font-mono mt-0.5">1.2K</span>
                    </div>
                    <div className="flex flex-col items-center">
                      <div className="w-7 h-7 rounded-full bg-black/45 flex items-center justify-center border border-white/20">
                        <Share2 className="w-3.5 h-3.5 text-white" />
                      </div>
                      <span className="text-[8px] font-mono mt-0.5">Share</span>
                    </div>
                    <div className="w-6 h-6 rounded-full bg-black/45 flex items-center justify-center border border-white/20">
                      <Music className="w-3 h-3 text-white" />
                    </div>
                  </div>
                )}

                <div className="w-full border-b border-dashed border-[var(--accent-primary)]/60 pb-1 text-[9px] text-[var(--accent-primary)] font-mono">
                  Caption Safe Zone
                </div>
              </div>
            )}
          </div>
        </div>
      </div>

      {/* Integrated Transport Bar */}
      <div className="px-3.5 py-2 border-t border-[var(--border-subtle)] bg-[var(--bg-surface)] flex items-center gap-2.5 shrink-0">
        <button
          type="button"
          onClick={togglePlay}
          disabled={!activeVideoSrc}
          className="w-7 h-7 rounded-lg bg-[var(--bg-card)] hover:border-[var(--accent-primary)]/50 text-[var(--accent-primary)] border border-[var(--border-subtle)] flex items-center justify-center transition shrink-0 disabled:opacity-40"
          title={isPlaying ? 'Pause' : 'Play'}
        >
          {isPlaying ? <Pause className="w-3.5 h-3.5 fill-current" /> : <Play className="w-3.5 h-3.5 fill-current" />}
        </button>

        <input
          type="range"
          min={0}
          max={Math.max(1, duration)}
          step={0.1}
          value={relCurrent}
          onChange={handleScrubberChange}
          disabled={!activeVideoSrc}
          className="flex-1 min-w-0 h-1 bg-[var(--bg-input)] rounded appearance-none cursor-pointer"
        />

        <span className="text-[11px] font-mono text-[var(--text-secondary)] shrink-0 tabular-nums">
          {formatMMSS(relCurrent)} / {formatMMSS(duration)}
        </span>

        <button
          type="button"
          onClick={toggleMute}
          disabled={!activeVideoSrc}
          className="w-7 h-7 rounded-lg bg-[var(--bg-card)] hover:text-[var(--text-primary)] text-[var(--text-secondary)] border border-[var(--border-subtle)] flex items-center justify-center transition shrink-0 disabled:opacity-40"
          title={isMuted ? 'Unmute' : 'Mute'}
        >
          {isMuted ? <VolumeX className="w-3.5 h-3.5" /> : <Volume2 className="w-3.5 h-3.5" />}
        </button>
      </div>

      {/* Interactive 150-Bin RMS Audio Waveform & Multi-Segment Blade Timeline */}
      {clip && waveformData && waveformData.bins.length > 0 && (() => {
        const xfade = clip.merge_crossfade_sec ?? 0.03;
        const span = Math.max(0.5, waveformData.window_end - waveformData.window_start);
        const inPct = Math.max(0, Math.min(98, ((trimStart - waveformData.window_start) / span) * 100));
        const outPct = Math.max(inPct + 2, Math.min(100, ((trimEnd - waveformData.window_start) / span) * 100));
        const { masterT: playAbs, segIdx: activeSegIdx } = mapRelToMasterAndSeg(relCurrent, keepSegments, xfade);
        const playPct = Math.max(0, Math.min(100, ((playAbs - waveformData.window_start) / span) * 100));
        const highlightedSegIdx = selectedSegIdx !== null ? selectedSegIdx : activeSegIdx;
        return (
          <div className="px-3.5 py-2 border-t border-[var(--border-subtle)] bg-[var(--bg-surface)]/80 space-y-1.5 shrink-0">
            <div className="flex items-center justify-between gap-1 text-[10px] font-mono text-[var(--text-secondary)] tabular-nums">
              <span className="flex items-center gap-1 font-semibold text-[var(--text-primary)]">
                <Scissors className="w-2.5 h-2.5 text-[var(--accent-primary)]" />
                <span>In {trimStart.toFixed(1)}s</span>
              </span>
              <div className="flex items-center gap-1.5">
                <span className="text-[var(--accent-primary)] font-bold">
                  {(duration || trimEnd - trimStart).toFixed(1)}s
                </span>
                {onUpdateSegments && (
                  <>
                    <button
                      type="button"
                      onClick={() => handleBladeSplit()}
                      className="px-1.5 py-0.5 rounded bg-[var(--bg-card)] border border-[var(--border-subtle)] hover:border-[var(--accent-primary)] text-[9px] font-sans font-semibold text-[var(--text-primary)] transition"
                      title="Split segment at playhead (B)"
                    >
                      Split (B)
                    </button>
                    {keepSegments.length > 1 && (
                      <button
                        type="button"
                        onClick={() => handleDeleteSegment()}
                        className="p-0.5 rounded bg-red-500/10 border border-red-500/30 text-red-400 hover:bg-red-500/20 transition"
                        title={`Delete selected segment S${highlightedSegIdx + 1}`}
                      >
                        <Trash2 className="w-2.5 h-2.5" />
                      </button>
                    )}
                  </>
                )}
              </div>
              <span>Out {trimEnd.toFixed(1)}s</span>
            </div>

            {keepSegments.length > 1 && (
              <div className="flex items-center gap-1 overflow-x-auto pb-0.5">
                {keepSegments.map(([segS, segE], idx) => {
                  const isSel = idx === highlightedSegIdx;
                  return (
                    <button
                      key={`${idx}-${segS}-${segE}`}
                      type="button"
                      onClick={() => {
                        setSelectedSegIdx(idx);
                        seekToRelTime(mapMasterToRel(segS + 0.05, keepSegments, xfade));
                      }}
                      className={`px-1.5 py-0.5 rounded text-[9px] font-mono border shrink-0 transition tabular-nums ${
                        isSel
                          ? 'bg-[var(--accent-subtle)] border-[var(--accent-primary)] text-[var(--accent-primary)] font-bold'
                          : 'bg-[var(--bg-card)] border-[var(--border-subtle)] text-[var(--text-secondary)] hover:text-[var(--text-primary)]'
                      }`}
                      title={`Select & seek to Segment ${idx + 1} (${segS.toFixed(1)}s - ${segE.toFixed(1)}s)`}
                    >
                      S{idx + 1} {segS.toFixed(1)}-{segE.toFixed(1)}s
                    </button>
                  );
                })}
              </div>
            )}

            <div
              ref={waveTrackRef}
              onClick={(e) => {
                if (draggingHandle) return;
                const el = waveTrackRef.current;
                if (!el) return;
                const rect = el.getBoundingClientRect();
                const ratio = Math.max(0, Math.min(1, (e.clientX - rect.left) / Math.max(1, rect.width)));
                const clickedAbs = waveformData.window_start + ratio * span;
                const nextRel = mapMasterToRel(clickedAbs, keepSegments, xfade);
                seekToRelTime(nextRel);
              }}
              className="relative h-8 w-full rounded-lg bg-[var(--bg-input)] border border-[var(--border-subtle)] overflow-hidden select-none touch-none cursor-pointer"
              title="Click waveform to seek • Press B to split segment • Drag handles to trim"
            >
              {/* 150-bin Heatmap SVG Waveform */}
              <svg
                viewBox="0 0 150 32"
                preserveAspectRatio="none"
                className="w-full h-full block pointer-events-none"
              >
                {waveformData.bins.map((amp, i) => {
                  const barAbs = waveformData.window_start + ((i + 0.5) / waveformData.bins.length) * span;
                  const insideSeg = keepSegments.some(([s, e]) => barAbs >= s && barAbs <= e);
                  const rms = waveformData.rms?.[i] ?? amp;
                  const density = waveformData.speech_density?.[i] ?? 0;
                  const silent = Boolean(waveformData.is_silence?.[i]);

                  let fillColor = 'var(--text-secondary)';
                  let fillOpacity = 0.22;
                  if (insideSeg) {
                    if (silent) {
                      fillColor = '#EF4444';
                      fillOpacity = 0.52;
                    } else if (rms >= 0.72 || (rms >= 0.5 && density >= 0.5)) {
                      fillColor = '#F59E0B';
                      fillOpacity = 0.92;
                    } else if (density >= 0.35 || rms >= 0.4) {
                      fillColor = '#10B981';
                      fillOpacity = 0.85;
                    } else {
                      fillColor = 'var(--accent-primary)';
                      fillOpacity = 0.78;
                    }
                  }

                  const h = Math.max(2, Math.round(amp * 28));
                  const y = Math.round((32 - h) / 2);
                  return (
                    <rect
                      key={i}
                      x={i + 0.12}
                      y={y}
                      width={0.76}
                      height={h}
                      rx={0.25}
                      fill={fillColor}
                      fillOpacity={fillOpacity}
                    />
                  );
                })}
              </svg>

              {/* Multi-segment cut overlays inside waveform window */}
              {keepSegments.length > 1 &&
                keepSegments.map(([segS, segE], idx) => {
                  if (segE <= waveformData.window_start || segS >= waveformData.window_end) return null;
                  const sPct = Math.max(0, Math.min(100, ((segS - waveformData.window_start) / span) * 100));
                  const ePct = Math.max(sPct, Math.min(100, ((segE - waveformData.window_start) / span) * 100));
                  if (ePct - sPct < 1) return null;
                  const isSel = idx === selectedSegIdx;
                  return (
                    <div
                      key={`ov-${idx}`}
                      className={`absolute inset-y-0 border-x pointer-events-none ${
                        isSel
                          ? 'border-[var(--accent-primary)] bg-[var(--accent-primary)]/12'
                          : 'border-white/25'
                      }`}
                      style={{ left: `${sPct}%`, width: `${ePct - sPct}%` }}
                    />
                  );
                })}

              {/* Retention Warning Pins on Waveform */}
              {retentionAudit?.drop_off_warnings.map((w) => {
                const { masterT } = mapRelToMasterAndSeg(w.timestamp, keepSegments, xfade);
                if (masterT < waveformData.window_start || masterT > waveformData.window_end) return null;
                const wPct = Math.max(1, Math.min(99, ((masterT - waveformData.window_start) / span) * 100));
                const dotColor = w.severity === 'high' ? 'bg-red-500' : w.severity === 'medium' ? 'bg-amber-400' : 'bg-sky-400';
                return (
                  <div
                    key={`pin-${w.id}`}
                    className={`absolute top-0.5 w-1.5 h-1.5 rounded-full -translate-x-1/2 pointer-events-none z-10 ${dotColor}`}
                    style={{ left: `${wPct}%` }}
                  />
                );
              })}

              {/* Live Playhead Needle */}
              <div
                className="absolute inset-y-0 w-0.5 bg-[var(--text-primary)] ring-1 ring-[var(--bg-canvas)] shadow-sm pointer-events-none z-20"
                style={{ left: `${playPct}%` }}
              />

              {/* Dimmed Outer Regions */}
              <div
                className="absolute inset-y-0 left-0 bg-black/45 pointer-events-none"
                style={{ width: `${inPct}%` }}
              />
              <div
                className="absolute inset-y-0 right-0 bg-black/45 pointer-events-none"
                style={{ width: `${Math.max(0, 100 - outPct)}%` }}
              />

              {/* Active Selection Border */}
              <div
                className="absolute inset-y-0 border-y-2 border-[var(--accent-primary)] pointer-events-none"
                style={{ left: `${inPct}%`, width: `${Math.max(2, outPct - inPct)}%` }}
              />

              {/* Draggable In Handle */}
              <div
                role="slider"
                aria-label="Trim In point"
                aria-valuenow={trimStart}
                onPointerDown={(e) => {
                  e.preventDefault();
                  setDraggingHandle('in');
                }}
                style={{ left: `calc(${inPct}% - 5px)` }}
                className="absolute inset-y-0 w-2.5 bg-[var(--accent-primary)] hover:brightness-110 cursor-ew-resize flex items-center justify-center rounded-l shadow z-10"
                title={`Drag In handle (${trimStart.toFixed(2)}s)`}
              >
                <span className="w-0.5 h-3.5 bg-[var(--accent-foreground)] rounded-full" />
              </div>

              {/* Draggable Out Handle */}
              <div
                role="slider"
                aria-label="Trim Out point"
                aria-valuenow={trimEnd}
                onPointerDown={(e) => {
                  e.preventDefault();
                  setDraggingHandle('out');
                }}
                style={{ left: `calc(${outPct}% - 5px)` }}
                className="absolute inset-y-0 w-2.5 bg-[var(--accent-primary)] hover:brightness-110 cursor-ew-resize flex items-center justify-center rounded-r shadow z-10"
                title={`Drag Out handle (${trimEnd.toFixed(2)}s)`}
              >
                <span className="w-0.5 h-3.5 bg-[var(--accent-foreground)] rounded-full" />
              </div>
            </div>
          </div>
        );
      })()}

      {/* Dedicated Export & Action Dock */}
      <div className="p-3 border-t border-[var(--border-subtle)] bg-[var(--bg-card)] space-y-2 shrink-0 relative">
        {clip && (
          <div className="flex items-center justify-between gap-2">
            <span className="text-[10px] font-bold uppercase tracking-wider text-[var(--text-muted)]">
              Export Dock
            </span>
            <button
              type="button"
              onClick={async () => {
                if (!clip) return;
                const nextOpen = !isPreflightOpen;
                setIsPreflightOpen(nextOpen);
                if (nextOpen) {
                  try {
                    setIsPreflightLoading(true);
                    const rep = await api.getClipPreflight(clip.project_id, clip.id);
                    setPreflightReport(rep as PreflightReport);
                  } catch {
                    // ignore transient offline scan error
                  } finally {
                    setIsPreflightLoading(false);
                  }
                }
              }}
              className={`px-2 py-0.5 rounded-full border text-[10px] font-bold flex items-center gap-1 transition ${
                !preflightReport || preflightReport.status === 'pass'
                  ? 'bg-emerald-500/10 border-emerald-500/35 text-emerald-400 hover:bg-emerald-500/20'
                  : preflightReport.status === 'warn'
                  ? 'bg-amber-500/10 border-amber-500/40 text-amber-400 hover:bg-amber-500/20'
                  : 'bg-red-500/10 border-red-500/40 text-red-400 hover:bg-red-500/20'
              }`}
              title="Run Demonetization, Audio Clipping & Safe-Zone Pre-Flight Check"
            >
              <Shield className="w-3 h-3" />
              <span>
                {isPreflightLoading
                  ? 'Scanning...'
                  : preflightReport
                  ? `Readiness ${preflightReport.readiness_score}/100`
                  : 'Pre-Flight Check'}
              </span>
            </button>
          </div>
        )}

        {clip && isPreflightOpen && (
          <div className="rounded-xl bg-[var(--bg-surface)] border border-[var(--border-subtle)] p-2.5 space-y-2 shadow-xl text-left">
            <div className="flex items-center justify-between">
              <span className="text-[11px] font-bold text-[var(--text-primary)] flex items-center gap-1.5">
                <Shield className="w-3.5 h-3.5 text-[var(--accent-primary)]" />
                Pre-Flight Readiness ({preflightReport?.readiness_score ?? '--'}/100)
              </span>
              <button
                type="button"
                onClick={() => setIsPreflightOpen(false)}
                className="text-[var(--text-muted)] hover:text-[var(--text-primary)]"
              >
                <X className="w-3.5 h-3.5" />
              </button>
            </div>
            {isPreflightLoading && !preflightReport ? (
              <div className="text-[10px] text-[var(--text-secondary)] py-1">Running safety audit...</div>
            ) : (
              <div className="space-y-1.5 max-h-[160px] overflow-y-auto pr-0.5">
                {(preflightReport?.checks || []).map((chk) => (
                  <div
                    key={chk.id}
                    className="p-2 rounded-lg bg-[var(--bg-input)] border border-[var(--border-subtle)] flex items-start justify-between gap-2"
                  >
                    <div className="min-w-0">
                      <div className="flex items-center gap-1.5 text-[10px] font-bold">
                        <span
                          className={`inline-block w-1.5 h-1.5 rounded-full ${
                            chk.status === 'pass'
                              ? 'bg-emerald-400'
                              : chk.status === 'warn'
                              ? 'bg-amber-400'
                              : 'bg-red-400'
                          }`}
                        />
                        <span className="text-[var(--text-primary)]">{chk.title}</span>
                      </div>
                      <p className="text-[10px] text-[var(--text-secondary)] mt-0.5 leading-snug">
                        {chk.detail}
                      </p>
                    </div>
                    {chk.fix_patch && onApplyFixClip && (
                      <button
                        type="button"
                        disabled={fixingPreflightId === chk.id}
                        onClick={async () => {
                          if (!clip || !chk.fix_patch) return;
                          try {
                            setFixingPreflightId(chk.id);
                            await onApplyFixClip(clip.id, chk.fix_patch);
                            const refreshed = await api.getClipPreflight(clip.project_id, clip.id);
                            setPreflightReport(refreshed as PreflightReport);
                          } finally {
                            setFixingPreflightId(null);
                          }
                        }}
                        className="shrink-0 px-2 py-1 rounded bg-[var(--accent-primary)] text-[var(--accent-foreground)] text-[9px] font-bold hover:opacity-90 transition disabled:opacity-50"
                      >
                        {fixingPreflightId === chk.id ? 'Fixing...' : chk.fix_label || '1-Click Fix'}
                      </button>
                    )}
                  </div>
                ))}
              </div>
            )}
          </div>
        )}
        {isRendering && (
          <div className="space-y-1 tabular-nums">
            <div className="flex justify-between text-[10px] font-mono text-[var(--text-secondary)]">
              <span>Encoding final MP4...</span>
              <span className="text-[var(--accent-primary)] font-bold">{renderProgress}%</span>
            </div>
            <div className="w-full h-1.5 rounded-full bg-[var(--bg-input)] overflow-hidden">
              <div
                className="h-full bg-[var(--accent-primary)] transition-all duration-300 rounded-full"
                style={{ width: `${renderProgress}%` }}
              />
            </div>
          </div>
        )}

        <div className="grid grid-cols-2 gap-2">
          <button
            type="button"
            onClick={onGenerateClips}
            disabled={isRendering || !clip}
            className="h-9 px-3 rounded-xl bg-[var(--accent-primary)] hover:opacity-90 disabled:opacity-50 text-[var(--accent-foreground)] font-bold text-xs flex items-center justify-center gap-1.5 transition shadow-sm"
          >
            {isRendering ? (
              <>
                <Loader2 className="w-3.5 h-3.5 animate-spin" />
                <span>Rendering...</span>
              </>
            ) : isCompleted ? (
              <>
                <CheckCircle2 className="w-3.5 h-3.5" />
                <span>Re-render Clip</span>
              </>
            ) : (
              <>
                <Zap className="w-3.5 h-3.5 fill-current" />
                <span>Render Clip</span>
              </>
            )}
          </button>

          {isCompleted ? (
            <a
              href={downloadUrl}
              download
              className="h-9 px-3 rounded-xl bg-[var(--bg-surface)] hover:border-[var(--accent-primary)]/50 text-[var(--text-primary)] border border-[var(--border-subtle)] font-semibold text-xs flex items-center justify-center gap-1.5 transition"
            >
              <Download className="w-3.5 h-3.5" />
              <span>Download MP4</span>
            </a>
          ) : (
            <button
              type="button"
              disabled
              className="h-9 px-3 rounded-xl bg-[var(--bg-input)] text-[var(--text-secondary)] border border-[var(--border-subtle)] font-semibold text-xs flex items-center justify-center gap-1.5 opacity-50 cursor-not-allowed"
              title="Render clip first to download MP4"
            >
              <Download className="w-3.5 h-3.5" />
              <span>Download MP4</span>
            </button>
          )}
        </div>

        <div className="grid grid-cols-2 gap-2">
          <button
            type="button"
            onClick={handleOpenPublish}
            disabled={!isCompleted}
            className="h-8 px-2.5 rounded-xl bg-[var(--bg-surface)] hover:border-red-500/40 text-[var(--text-primary)] border border-[var(--border-subtle)] font-semibold text-xs flex items-center justify-center gap-1.5 transition disabled:opacity-45 disabled:cursor-not-allowed"
            title={isCompleted ? 'Publish rendered clip directly to YouTube Shorts' : 'Render clip first to publish'}
          >
            <YouTubeIcon className="w-3.5 h-3.5 text-red-500 shrink-0" />
            <span className="truncate">Post YouTube</span>
          </button>

          {clip ? (
            <select
              aria-label="Export Pro NLE Format"
              defaultValue=""
              onChange={(e) => {
                const fmt = e.target.value as 'fcpxml' | 'premiere' | 'srt' | 'zip';
                if (!fmt) return;
                const url = api.getNleExportUrl(clip.project_id, clip.id, fmt);
                window.open(url, '_blank');
                e.target.value = '';
              }}
              className="h-8 px-2 rounded-xl bg-[var(--bg-surface)] hover:border-[var(--accent-primary)]/50 text-[var(--text-primary)] border border-[var(--border-subtle)] font-semibold text-[11px] outline-none cursor-pointer"
            >
              <option value="" disabled>
                Pro NLE Export ▾
              </option>
              <option value="fcpxml">DaVinci / FCPXML</option>
              <option value="premiere">Adobe Premiere .XML</option>
              <option value="srt">Captions .SRT</option>
              <option value="zip">Full Studio .ZIP</option>
            </select>
          ) : (
            <div />
          )}
        </div>

        <div className="grid grid-cols-3 gap-1.5">
          <button
            type="button"
            onClick={() => {
              if (!clip) return;
              setThumbHeadline(clip.hook_headline || clip.selected_title || 'WATCH THIS');
              setThumbFormat(clip.aspect_ratio === '16:9' ? '16:9' : '9:16');
              setThumbAccentColor(clip.active_color || '#C6F32F');
              setThumbError(null);
              setIsThumbModalOpen(true);
            }}
            disabled={!clip}
            className="h-8 px-2 rounded-xl bg-[var(--bg-surface)] hover:border-[var(--accent-primary)]/50 text-[var(--text-primary)] border border-[var(--border-subtle)] font-semibold text-[11px] flex items-center justify-center gap-1 transition disabled:opacity-45"
            title="Depth-Layer AI Thumbnail & Cover Designer"
          >
            <ImageIcon className="w-3.5 h-3.5 text-[var(--accent-primary)] shrink-0" />
            <span className="truncate">Cover</span>
          </button>
          <button
            type="button"
            onClick={handleExportPublishPack}
            disabled={!clip || isExportingPack}
            className="col-span-2 h-8 px-2.5 rounded-xl bg-[var(--accent-subtle)] hover:bg-[var(--accent-primary)] hover:text-[var(--accent-foreground)] text-[var(--accent-primary)] border border-[var(--accent-primary)]/40 font-bold text-[11px] flex items-center justify-center gap-1.5 transition disabled:opacity-45 disabled:cursor-not-allowed"
            title="Export 9:16 + 4:5 + 16:9 MP4s, AI Cover Thumbnail, Captions .SRT, and Social Copy .TXT in one .zip"
          >
            {isExportingPack ? (
              <Loader2 className="w-3.5 h-3.5 animate-spin shrink-0" />
            ) : (
              <Package className="w-3.5 h-3.5 shrink-0" />
            )}
            <span className="truncate">{isExportingPack ? 'Building Pack...' : 'Publish Pack (.zip)'}</span>
          </button>
        </div>

        {packError && (
          <div className="text-[10px] text-red-400 bg-red-500/10 border border-red-500/25 rounded-lg px-2 py-1 truncate">
            {packError}
          </div>
        )}
      </div>

      {/* YouTube & Multi-Platform Social Copy Publishing Modal */}
      {isPublishModalOpen && (
        <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/75 backdrop-blur-sm p-4 animate-fadeIn">
          <div className="bg-[var(--bg-surface)] border border-[var(--border-subtle)] rounded-2xl w-full max-w-xl max-h-[90vh] overflow-y-auto p-6 space-y-4 shadow-2xl text-[var(--text-primary)]">
            <div className="flex items-center justify-between border-b border-[var(--border-subtle)] pb-3">
              <div className="flex items-center gap-2 font-bold text-sm">
                <YouTubeIcon className="w-5 h-5 text-red-500" />
                <span>Publish &amp; Multi-Platform Social Studio</span>
              </div>
              <div className="flex items-center gap-2">
                <button
                  type="button"
                  onClick={handleGenerateSocialCopy}
                  disabled={isGeneratingCopy || !clip}
                  className="px-2.5 py-1.5 rounded-lg bg-[var(--accent-subtle)] border border-[var(--accent-primary)]/40 text-[var(--accent-primary)] hover:bg-[var(--accent-primary)] hover:text-[var(--accent-foreground)] text-[11px] font-bold flex items-center gap-1.5 transition disabled:opacity-50"
                >
                  {isGeneratingCopy ? (
                    <Loader2 className="w-3.5 h-3.5 animate-spin" />
                  ) : (
                    <Sparkles className="w-3.5 h-3.5" />
                  )}
                  <span>{isGeneratingCopy ? 'Writing Copy...' : 'Generate Platform Copy'}</span>
                </button>
                <button
                  type="button"
                  onClick={() => setIsPublishModalOpen(false)}
                  className="text-[var(--text-secondary)] hover:text-[var(--text-primary)]"
                >
                  <X className="w-5 h-5" />
                </button>
              </div>
            </div>

            {socialCopy && (
              <div className="rounded-xl border border-[var(--border-subtle)] bg-[var(--bg-input)] p-3 space-y-2.5 text-xs">
                <div className="grid grid-cols-4 gap-1 bg-[var(--bg-surface)] p-1 rounded-lg border border-[var(--border-subtle)]">
                  {([
                    { id: 'youtube_shorts', label: 'YouTube' },
                    { id: 'tiktok', label: 'TikTok' },
                    { id: 'instagram_reels', label: 'Reels' },
                    { id: 'x_twitter', label: 'X' },
                  ] as const).map((tab) => (
                    <button
                      key={tab.id}
                      type="button"
                      onClick={() => setActiveCopyPlatform(tab.id)}
                      className={`py-1 rounded-md text-[11px] font-bold transition ${
                        activeCopyPlatform === tab.id
                          ? 'bg-[var(--accent-primary)] text-[var(--accent-foreground)]'
                          : 'text-[var(--text-secondary)] hover:text-[var(--text-primary)]'
                      }`}
                    >
                      {tab.label}
                    </button>
                  ))}
                </div>

                {(() => {
                  const entry = socialCopy[activeCopyPlatform];
                  if (!entry) return null;
                  const tagsStr = Array.isArray(entry.hashtags) ? entry.hashtags.join(' ') : '';
                  const fullCopy = `${entry.title}\n\n${entry.description}${tagsStr ? `\n\n${tagsStr}` : ''}`;
                  return (
                    <div className="space-y-2">
                      <div className="flex items-center justify-between gap-2">
                        <span className="font-bold text-[var(--text-primary)] truncate">
                          {entry.title}
                        </span>
                        <div className="flex items-center gap-1.5 shrink-0">
                          <button
                            type="button"
                            onClick={() => handleCopyText(activeCopyPlatform, fullCopy)}
                            className="px-2 py-1 rounded-md bg-[var(--bg-surface)] border border-[var(--border-subtle)] text-[10px] font-semibold flex items-center gap-1 hover:border-[var(--accent-primary)]/50"
                          >
                            {copiedField === activeCopyPlatform ? (
                              <Check className="w-3 h-3 text-emerald-400" />
                            ) : (
                              <Copy className="w-3 h-3" />
                            )}
                            <span>{copiedField === activeCopyPlatform ? 'Copied' : 'Copy All'}</span>
                          </button>
                          <button
                            type="button"
                            onClick={() => {
                              setPublishTitle(entry.title || publishTitle);
                              setPublishDesc([entry.description || '', tagsStr].filter(Boolean).join('\n\n'));
                            }}
                            className="px-2 py-1 rounded-md bg-[var(--accent-subtle)] text-[var(--accent-primary)] text-[10px] font-bold hover:opacity-90"
                          >
                            Apply Below
                          </button>
                        </div>
                      </div>
                      <p className="text-[11px] text-[var(--text-secondary)] whitespace-pre-line">
                        {entry.description}
                      </p>
                      {entry.pinned_comment && (
                        <div className="text-[10px] text-[var(--text-secondary)] bg-[var(--bg-surface)] px-2 py-1 rounded border border-[var(--border-subtle)]">
                          <span className="font-semibold text-[var(--text-primary)]">Pinned: </span>
                          {entry.pinned_comment}
                        </div>
                      )}
                      {tagsStr && (
                        <div className="text-[10px] font-mono text-[var(--accent-primary)] break-words">
                          {tagsStr}
                        </div>
                      )}
                    </div>
                  );
                })()}
              </div>
            )}

            <form onSubmit={handlePublishSubmit} className="space-y-3.5 text-xs">
              <div>
                <label className="text-[var(--text-secondary)] font-semibold block mb-1">
                  Title (with #Shorts)
                </label>
                <input
                  type="text"
                  value={publishTitle}
                  onChange={(e) => setPublishTitle(e.target.value)}
                  className="w-full bg-[var(--bg-input)] border border-[var(--border-subtle)] rounded-xl px-3 py-2 text-[var(--text-primary)] outline-none focus:border-[var(--accent-primary)]"
                  required
                />
              </div>

              <div>
                <label className="text-[var(--text-secondary)] font-semibold block mb-1">
                  Description & Hashtags
                </label>
                <textarea
                  rows={3}
                  value={publishDesc}
                  onChange={(e) => setPublishDesc(e.target.value)}
                  className="w-full bg-[var(--bg-input)] border border-[var(--border-subtle)] rounded-xl px-3 py-2 text-[var(--text-primary)] outline-none focus:border-[var(--accent-primary)]"
                />
              </div>

              <div className="flex items-center justify-between">
                <div>
                  <label className="text-[var(--text-secondary)] font-semibold block mb-1">Visibility</label>
                  <select
                    value={publishPrivacy}
                    onChange={(e) => setPublishPrivacy(e.target.value as 'private' | 'unlisted' | 'public')}
                    className="bg-[var(--bg-input)] border border-[var(--border-subtle)] rounded-lg px-2.5 py-1.5 text-[var(--text-primary)] outline-none"
                  >
                    <option value="private">Private</option>
                    <option value="unlisted">Unlisted</option>
                    <option value="public">Public</option>
                  </select>
                </div>

                <button
                  type="submit"
                  disabled={isPublishing}
                  className="px-5 py-2 bg-red-600 hover:bg-red-700 text-white font-bold text-xs rounded-xl transition shadow-md flex items-center gap-1.5 disabled:opacity-50"
                >
                  {isPublishing ? <Loader2 className="w-4 h-4 animate-spin" /> : <YouTubeIcon className="w-4 h-4" />}
                  <span>{isPublishing ? 'Publishing...' : 'Upload Video'}</span>
                </button>
              </div>

              {publishResult && (
                <div
                  className={`p-3 rounded-xl text-xs flex items-center justify-between ${
                    publishResult.startsWith('Error')
                      ? 'bg-red-500/10 border border-red-500/30 text-red-400'
                      : 'bg-emerald-500/10 border border-emerald-500/30 text-emerald-400'
                  }`}
                >
                  <span className="break-all">{publishResult}</span>
                  {publishResult.startsWith('http') && (
                    <a href={publishResult} target="_blank" rel="noreferrer" className="underline font-bold ml-2 shrink-0">
                      View
                    </a>
                  )}
                </div>
              )}
            </form>
          </div>
        </div>
      )}

      {/* Depth-Layer AI Thumbnail & Cover Designer Modal */}
      {isThumbModalOpen && clip && (
        <div className="fixed inset-0 z-50 bg-black/75 backdrop-blur-sm flex items-center justify-center p-4 animate-fadeIn">
          <div className="w-full max-w-2xl bg-[var(--bg-card)] border border-[var(--border-subtle)] rounded-2xl shadow-2xl overflow-hidden flex flex-col max-h-[90vh]">
            <div className="flex items-center justify-between px-5 py-3.5 border-b border-[var(--border-subtle)]">
              <div className="flex items-center gap-2">
                <ImageIcon className="w-4 h-4 text-[var(--accent-primary)]" />
                <h3 className="font-bold text-sm text-[var(--text-primary)]">
                  Depth-Layer AI Thumbnail Studio
                </h3>
              </div>
              <button
                type="button"
                onClick={() => setIsThumbModalOpen(false)}
                className="text-[var(--text-secondary)] hover:text-[var(--text-primary)]"
              >
                <X className="w-4 h-4" />
              </button>
            </div>

            <div className="p-5 overflow-y-auto grid grid-cols-1 md:grid-cols-2 gap-4 text-xs">
              <div className="space-y-3">
                <div>
                  <label className="text-[var(--text-secondary)] font-semibold block mb-1">
                    Cover Headline
                  </label>
                  <input
                    type="text"
                    value={thumbHeadline}
                    onChange={(e) => setThumbHeadline(e.target.value)}
                    placeholder="UNBELIEVABLE REVEAL"
                    className="w-full bg-[var(--bg-input)] border border-[var(--border-subtle)] rounded-lg px-3 py-2 text-[var(--text-primary)] outline-none focus:border-[var(--accent-primary)]"
                  />
                </div>

                <div className="grid grid-cols-2 gap-2">
                  <div>
                    <label className="text-[var(--text-secondary)] font-semibold block mb-1">
                      Aspect Format
                    </label>
                    <select
                      value={thumbFormat}
                      onChange={(e) => setThumbFormat(e.target.value as '9:16' | '16:9')}
                      className="w-full bg-[var(--bg-input)] border border-[var(--border-subtle)] rounded-lg px-2.5 py-1.5 text-[var(--text-primary)] outline-none"
                    >
                      <option value="9:16">1080×1920 Shorts/Reels</option>
                      <option value="16:9">1280×720 YouTube</option>
                    </select>
                  </div>
                  <div>
                    <label className="text-[var(--text-secondary)] font-semibold block mb-1">
                      Badge Sticker
                    </label>
                    <input
                      type="text"
                      value={thumbBadge}
                      onChange={(e) => setThumbBadge(e.target.value)}
                      placeholder="e.g. VIRAL"
                      className="w-full bg-[var(--bg-input)] border border-[var(--border-subtle)] rounded-lg px-2.5 py-1.5 text-[var(--text-primary)] outline-none"
                    />
                  </div>
                </div>

                <div className="space-y-2 pt-1">
                  <label className="flex items-center justify-between px-3 py-2 rounded-lg bg-[var(--bg-input)] border border-[var(--border-subtle)] cursor-pointer">
                    <span className="font-semibold text-[var(--text-primary)]">Text Behind Subject (Depth Layer)</span>
                    <input
                      type="checkbox"
                      checked={thumbDepthLayer}
                      onChange={(e) => setThumbDepthLayer(e.target.checked)}
                      className="accent-[var(--accent-primary)] w-3.5 h-3.5"
                    />
                  </label>
                  <label className="flex items-center justify-between px-3 py-2 rounded-lg bg-[var(--bg-input)] border border-[var(--border-subtle)] cursor-pointer">
                    <span className="font-semibold text-[var(--text-primary)]">Subject Accent Rim Glow</span>
                    <input
                      type="checkbox"
                      checked={thumbRimGlow}
                      onChange={(e) => setThumbRimGlow(e.target.checked)}
                      className="accent-[var(--accent-primary)] w-3.5 h-3.5"
                    />
                  </label>
                  <label className="flex items-center justify-between px-3 py-2 rounded-lg bg-[var(--bg-input)] border border-[var(--border-subtle)] cursor-pointer">
                    <span className="font-semibold text-[var(--text-primary)]">
                      Use Current Playhead ({relCurrent.toFixed(1)}s)
                    </span>
                    <input
                      type="checkbox"
                      checked={thumbUsePlayhead}
                      onChange={(e) => setThumbUsePlayhead(e.target.checked)}
                      className="accent-[var(--accent-primary)] w-3.5 h-3.5"
                    />
                  </label>
                </div>

                <div className="flex items-center justify-between px-3 py-1.5 rounded-lg bg-[var(--bg-input)] border border-[var(--border-subtle)]">
                  <span className="text-[var(--text-secondary)] font-semibold">Accent Color</span>
                  <input
                    type="color"
                    value={thumbAccentColor}
                    onChange={(e) => setThumbAccentColor(e.target.value)}
                    className="w-7 h-5 rounded cursor-pointer border-0 bg-transparent p-0"
                  />
                </div>

                <button
                  type="button"
                  disabled={isGeneratingThumb}
                  onClick={async () => {
                    try {
                      setIsGeneratingThumb(true);
                      setThumbError(null);
                      const { masterT } = mapRelToMasterAndSeg(
                        relCurrent,
                        keepSegments,
                        clip.merge_crossfade_sec ?? 0.03
                      );
                      const res = await api.generateThumbnail(clip.project_id, clip.id, {
                        timestamp: thumbUsePlayhead ? masterT : undefined,
                        headline: thumbHeadline,
                        format: thumbFormat,
                        depth_layer: thumbDepthLayer,
                        accent_color: thumbAccentColor,
                        rim_glow: thumbRimGlow,
                        badge_text: thumbBadge,
                      });
                      setThumbResult(res);
                    } catch (err: any) {
                      setThumbError(err?.message || 'Thumbnail generation failed');
                    } finally {
                      setIsGeneratingThumb(false);
                    }
                  }}
                  className="w-full py-2.5 rounded-xl bg-[var(--accent-primary)] text-[var(--accent-foreground)] font-bold flex items-center justify-center gap-1.5 hover:opacity-90 transition disabled:opacity-50"
                >
                  {isGeneratingThumb ? <Loader2 className="w-4 h-4 animate-spin" /> : <Sparkles className="w-4 h-4" />}
                  <span>{isGeneratingThumb ? 'Generating Cover...' : 'Generate AI Cover'}</span>
                </button>
                {thumbError && (
                  <div className="p-2 rounded-lg bg-red-500/10 border border-red-500/30 text-red-400 text-[11px]">
                    {thumbError}
                  </div>
                )}
              </div>

              <div className="flex flex-col items-center justify-center rounded-xl bg-[var(--bg-surface)] border border-[var(--border-subtle)] p-3 min-h-[260px] space-y-3">
                {thumbResult ? (
                  <>
                    <img
                      src={thumbResult.thumbnail_url}
                      alt="Generated Depth Cover"
                      className="max-h-72 w-auto rounded-lg border border-[var(--border-subtle)] shadow-lg object-contain"
                    />
                    <div className="flex items-center gap-2 w-full">
                      <a
                        href={thumbResult.thumbnail_url}
                        download
                        className="flex-1 py-1.5 px-2.5 rounded-lg bg-[var(--bg-card)] border border-[var(--border-subtle)] text-center font-bold text-[11px] text-[var(--text-primary)] hover:border-[var(--accent-primary)]"
                      >
                        Download PNG ({thumbResult.width}×{thumbResult.height})
                      </a>
                      <a
                        href={thumbResult.jpg_url}
                        download
                        className="flex-1 py-1.5 px-2.5 rounded-lg bg-[var(--bg-card)] border border-[var(--border-subtle)] text-center font-bold text-[11px] text-[var(--text-primary)] hover:border-[var(--accent-primary)]"
                      >
                        Download JPG
                      </a>
                    </div>
                  </>
                ) : (
                  <div className="text-center text-[var(--text-secondary)] space-y-1.5 p-4">
                    <ImageIcon className="w-8 h-8 mx-auto opacity-50" />
                    <p className="font-semibold text-[var(--text-primary)]">Depth-Layer Cover Designer</p>
                    <p className="text-[11px]">
                      Automatically separates speaker foreground matte and weaves headline typography behind subject.
                    </p>
                  </div>
                )}
              </div>
            </div>
          </div>
        </div>
      )}
    </aside>
  );
};
