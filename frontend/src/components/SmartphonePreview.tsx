import React, { useRef, useState, useEffect } from 'react';
import {
  Play, Pause, RefreshCw, Eye, Smartphone, Heart, MessageCircle,
  Share2, Music, Volume2, VolumeX, Download, Zap, CheckCircle2, Loader2, X, Scissors,
  Sparkles, Package, Copy, Check, Activity, Trash2, Image as ImageIcon, Shield,
  Crop, Crosshair, Lock, Unlock, Move, RotateCcw, AlertTriangle, ExternalLink, Link2,
  Unlink, Upload, ChevronDown, ChevronUp, StepBack, StepForward, Grid
} from 'lucide-react';
import { ViralClip, PlatformCopyMap, RetentionAuditReport, RetentionWarning, PrivacyBlurBox, PreflightReport, PreflightCheck, FramingOverride, FramingBox, BestFrameItem } from '../types';
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
  activeStep?: 1 | 2 | 3;
  videoUrl?: string;
  safeZoneGuide: string;
  onSafeZoneChange: (guide: string) => void;
  seekTime?: number;
  onPlaybackTimeChange?: (relativeSeconds: number) => void;
  onGenerateClips: () => void;
  onAutoRenderClip?: (
    projectId: number,
    clipId: number,
    signal: AbortSignal,
    overrides?: Record<string, any>
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
  onUpdateClip?: (updates: Partial<ViralClip>) => void;
  onOpenAnalysisWorkstation?: () => void;
  externalThumbRequest?: { timestamp: number; seq: number } | null;
  compareOriginal?: boolean;
  onCompareOriginalChange?: (held: boolean) => void;
  isRendering: boolean;
  renderProgress: number;
}

export const SmartphonePreview: React.FC<SmartphonePreviewProps> = ({
  clip,
  activeStep = 1,
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
  onUpdateClip,
  onOpenAnalysisWorkstation,
  externalThumbRequest,
  compareOriginal = false,
  onCompareOriginalChange,
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
  const [canvasZoom, setCanvasZoom] = useState<'fit' | '75' | '100'>('fit');
  const [isPlaying, setIsPlaying] = useState(false);
  const [isMuted, setIsMuted] = useState(false);
  const [currentTime, setCurrentTime] = useState(0);
  const [duration, setDuration] = useState(30);
  const [showExactFrame, setShowExactFrame] = useState(false);
  const [exactFrameUrl, setExactFrameUrl] = useState<string | null>(null);
  const [rawFrameUrl, setRawFrameUrl] = useState<string | null>(null);
  const rawFrameCacheRef = useRef<Record<string, string>>({});
  const [frameLatencyMs, setFrameLatencyMs] = useState<number | null>(null);
  const [isRenderingFrame, setIsRenderingFrame] = useState(false);

  // YouTube Publish Modal state
  const [isPublishModalOpen, setIsPublishModalOpen] = useState(false);

  // Depth-Layered AI Thumbnail Studio state
  const [isThumbModalOpen, setIsThumbModalOpen] = useState(false);
  const [thumbFormat, setThumbFormat] = useState<'9:16' | '16:9'>('9:16');
  const [thumbHeadline, setThumbHeadline] = useState('');
  const [thumbBadge, setThumbBadge] = useState('');
  const [thumbDepthLayer, setThumbDepthLayer] = useState(true);
  const [thumbRimGlow, setThumbRimGlow] = useState(true);
  const [thumbAccentColor, setThumbAccentColor] = useState('#C6F32F');
  const [thumbUsePlayhead, setThumbUsePlayhead] = useState(false);
  const [thumbCustomTimestamp, setThumbCustomTimestamp] = useState<number | null>(null);
  const [thumbBestFrames, setThumbBestFrames] = useState<BestFrameItem[]>([]);
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
  const [isEditFrameOpen, setIsEditFrameOpen] = useState(false);
  const [sourceFrameUrl, setSourceFrameUrl] = useState<string | null>(null);
  const DEFAULT_FRAME_CROP: FramingBox = { x: 0.34, y: 0.04, w: 0.32, h: 0.92 };
  const [frameBoxDraft, setFrameBoxDraft] = useState<{
    webcam_box: FramingBox;
    focus_anchor: { x: number; y: number };
    frame_crop: FramingBox;
    keyframe_start_crop: FramingBox | null;
    keyframe_end_crop: FramingBox | null;
    lock_camera: boolean;
  }>({
    webcam_box: { x: 0.02, y: 0.02, w: 0.28, h: 0.30 },
    focus_anchor: { x: 0.5, y: 0.42 },
    frame_crop: DEFAULT_FRAME_CROP,
    keyframe_start_crop: null,
    keyframe_end_crop: null,
    lock_camera: false,
  });
  const [frameEditTarget, setFrameEditTarget] = useState<'frame_crop' | 'start_kf' | 'end_kf' | 'webcam'>('frame_crop');
  const [showRuleOfThirds, setShowRuleOfThirds] = useState<boolean>(true);
  const [showCanvasThirds, setShowCanvasThirds] = useState<boolean>(false);
  const [cropSnapGuides, setCropSnapGuides] = useState<{ x: boolean; y: boolean }>({ x: false, y: false });

  useEffect(() => {
    const onToggleThirds = () => setShowCanvasThirds((v) => !v);
    window.addEventListener('lh-toggle-thirds', onToggleThirds);
    return () => window.removeEventListener('lh-toggle-thirds', onToggleThirds);
  }, []);
  const frameBoxCanvasRef = useRef<HTMLDivElement>(null);
  const frameDragRef = useRef<{
    target:
      | 'box_move'
      | 'box_nw'
      | 'box_ne'
      | 'box_sw'
      | 'box_se'
      | 'crop_move'
      | 'crop_nw'
      | 'crop_ne'
      | 'crop_sw'
      | 'crop_se'
      | 'anchor';
    cropField: 'frame_crop' | 'keyframe_start_crop' | 'keyframe_end_crop';
    startX: number;
    startY: number;
    origBox: FramingBox;
    origCrop: FramingBox;
    origAnchor: { x: number; y: number };
    latest: {
      webcam_box: FramingBox;
      focus_anchor: { x: number; y: number };
      frame_crop: FramingBox;
      keyframe_start_crop: FramingBox | null;
      keyframe_end_crop: FramingBox | null;
      lock_camera: boolean;
    };
  } | null>(null);
  const [draggingFrameMode, setDraggingFrameMode] = useState<string | null>(null);
  const blurDragRef = useRef<{
    idx: number;
    kind: 'move' | 'resize';
    startX: number;
    startY: number;
    origBox: PrivacyBlurBox;
    latest: PrivacyBlurBox[];
  } | null>(null);
  const [draggingBlurIdx, setDraggingBlurIdx] = useState<number | null>(null);
  const [freeformCaptionMode, setFreeformCaptionMode] = useState<boolean>(activeStep === 2);
  const [captionDragDraft, setCaptionDragDraft] = useState<{ x: number; y: number } | null>(null);
  const [isDraggingCaption, setIsDraggingCaption] = useState<boolean>(false);
  const captionDragRef = useRef<{
    startX: number;
    startY: number;
    origX: number;
    origY: number;
    latestX: number;
    latestY: number;
  } | null>(null);

  useEffect(() => {
    setFreeformCaptionMode(activeStep === 2);
  }, [activeStep]);

  useEffect(() => {
    setCaptionDragDraft(null);
  }, [clip?.id, clip?.caption_x, clip?.caption_y, clip?.position_x, clip?.position_y]);

  useEffect(() => {
    if (!isDraggingCaption || !clip) return;
    const onMove = (ev: PointerEvent) => {
      const dr = captionDragRef.current;
      const box = previewStageBoxRef.current;
      if (!dr || !box) return;
      const rect = box.getBoundingClientRect();
      const dx = (ev.clientX - dr.startX) / Math.max(1, rect.width);
      const dy = (ev.clientY - dr.startY) / Math.max(1, rect.height);
      let nx = Math.max(0.05, Math.min(0.95, dr.origX + dx));
      let ny = Math.max(0.08, Math.min(0.92, dr.origY + dy));
      // Magnetic center snap within +/- 2.5%
      if (Math.abs(nx - 0.5) <= 0.025) nx = 0.5;
      if (Math.abs(ny - 0.5) <= 0.025) ny = 0.5;
      nx = Number(nx.toFixed(3));
      ny = Number(ny.toFixed(3));
      dr.latestX = nx;
      dr.latestY = ny;
      setCaptionDragDraft({ x: nx, y: ny });
    };
    const onUp = () => {
      const dr = captionDragRef.current;
      captionDragRef.current = null;
      setIsDraggingCaption(false);
      if (dr && clip) {
        const capPatch: Partial<ViralClip> = {
          caption_x: dr.latestX,
          caption_y: dr.latestY,
          caption_custom_position: true,
          face_safe_captions: false,
          position_x: Math.round(dr.latestX * 100),
          position_y: Math.round(dr.latestY * 100),
          position_grid: Math.abs(dr.latestX - 0.5) < 0.03 && Math.abs(dr.latestY - 0.5) < 0.03
            ? 'center'
            : clip.position_grid,
        };
        if (onUpdateClip) onUpdateClip(capPatch);
        else if (onApplyFixClip) void onApplyFixClip(clip.id, capPatch);
      }
    };
    window.addEventListener('pointermove', onMove);
    window.addEventListener('pointerup', onUp);
    return () => {
      window.removeEventListener('pointermove', onMove);
      window.removeEventListener('pointerup', onUp);
    };
  }, [isDraggingCaption, clip?.id, onUpdateClip, onApplyFixClip]);

  useEffect(() => {
    setBlurDraftBoxes(null);
  }, [clip?.id, JSON.stringify(clip?.privacy_blur_boxes || [])]);

  useEffect(() => {
    if (!clip) return;
    const ov = clip.framing_override;
    const cam = clip.source_webcam_region;
    setFrameBoxDraft({
      webcam_box: ov?.webcam_box
        ? { ...ov.webcam_box }
        : cam && cam.w > 0.05 && cam.h > 0.05
          ? { x: cam.x, y: cam.y, w: cam.w, h: cam.h }
          : { x: 0.02, y: 0.02, w: 0.28, h: 0.30 },
      focus_anchor: ov?.focus_anchor
        ? { ...ov.focus_anchor }
        : { x: cam?.face_cx ?? 0.5, y: cam?.face_cy ?? 0.42 },
      frame_crop: ov?.frame_crop
        ? { ...ov.frame_crop }
        : { ...DEFAULT_FRAME_CROP },
      keyframe_start_crop: ov?.keyframe_start_crop ? { ...ov.keyframe_start_crop } : null,
      keyframe_end_crop: ov?.keyframe_end_crop ? { ...ov.keyframe_end_crop } : null,
      lock_camera: Boolean(ov?.lock_camera),
    });
  }, [clip?.id, JSON.stringify(clip?.framing_override || null)]);

  const persistFramingOverride = async (next: {
    webcam_box?: FramingBox;
    focus_anchor?: { x: number; y: number };
    frame_crop?: FramingBox | null;
    keyframe_start_crop?: FramingBox | null;
    keyframe_end_crop?: FramingBox | null;
    lock_camera?: boolean;
    reset?: boolean;
  }) => {
    if (!clip) return;
    try {
      const res = await api.updateFramingOverride(clip.project_id, clip.id, next);
      if (onApplyFixClip) {
        await onApplyFixClip(clip.id, {
          framing_override: res.framing_override as FramingOverride | null,
          source_webcam_region: res.source_webcam_region ?? clip.source_webcam_region,
        });
      }
    } catch (err) {
      console.error('Failed to persist framing override:', err);
    }
  };

  useEffect(() => {
    if (!isEditFrameOpen || !clip) return;
    let cancelled = false;
    const segs: SegPair[] = resolveClipKeepSegments(clip);
    const { masterT } = mapRelToMasterAndSeg(relCurrentRef.current, segs, clip.merge_crossfade_sec ?? 0.03);
    Promise.all([
      api.getFramingMap(clip.project_id, clip.id, false).catch(() => null),
      api.getPreviewFrame({
        clip_id: clip.id,
        project_id: clip.project_id,
        timestamp: Math.max(0, masterT),
        aspect_ratio: '16:9',
        frame_layout: 'fit_blur',
        burn_captions: false,
        show_hook_banner: false,
        clip_size: '540p',
      }).catch(() => null),
    ]).then(([fmap, pframe]) => {
      if (cancelled) return;
      if (pframe?.frame_url) setSourceFrameUrl(pframe.frame_url);
      if (fmap) {
        const ov = fmap.framing_override;
        const cam = fmap.source_webcam_region || fmap.visual_map?.webcam;
        const sh = fmap.visual_map?.shots?.[0];
        setFrameBoxDraft({
          webcam_box: ov?.webcam_box
            ? { ...ov.webcam_box }
            : cam && cam.w > 0.05 && cam.h > 0.05
              ? { x: cam.x, y: cam.y, w: cam.w, h: cam.h }
              : { x: 0.02, y: 0.02, w: 0.28, h: 0.30 },
          focus_anchor: ov?.focus_anchor
            ? { ...ov.focus_anchor }
            : { x: sh?.cx ?? 0.5, y: sh?.cy ?? 0.42 },
          frame_crop: ov?.frame_crop
            ? { ...ov.frame_crop }
            : { ...DEFAULT_FRAME_CROP },
          keyframe_start_crop: ov?.keyframe_start_crop ? { ...ov.keyframe_start_crop } : null,
          keyframe_end_crop: ov?.keyframe_end_crop ? { ...ov.keyframe_end_crop } : null,
          lock_camera: Boolean(ov?.lock_camera),
        });
      }
    });
    return () => {
      cancelled = true;
    };
  }, [isEditFrameOpen, clip?.id]);

  useEffect(() => {
    if (!draggingFrameMode || !clip) return;
    const onMove = (ev: PointerEvent) => {
      const dr = frameDragRef.current;
      const el = frameBoxCanvasRef.current;
      if (!dr || !el) return;
      const rect = el.getBoundingClientRect();
      const dx = (ev.clientX - dr.startX) / Math.max(1, rect.width);
      const dy = (ev.clientY - dr.startY) / Math.max(1, rect.height);
      const b = { ...dr.origBox };
      const c = { ...dr.origCrop };
      let anchor = { ...dr.origAnchor };
      let snapX = false;
      let snapY = false;

      if (dr.target === 'anchor') {
        let ax = Math.max(0.05, Math.min(0.95, dr.origAnchor.x + dx));
        let ay = Math.max(0.05, Math.min(0.95, dr.origAnchor.y + dy));
        if (Math.abs(ax - 0.5) <= 0.02) { ax = 0.5; snapX = true; }
        if (Math.abs(ay - 0.5) <= 0.02) { ay = 0.5; snapY = true; }
        anchor = { x: Number(ax.toFixed(3)), y: Number(ay.toFixed(3)) };
      } else if (dr.target === 'box_move') {
        b.x = Number(Math.max(0, Math.min(1 - b.w, dr.origBox.x + dx)).toFixed(3));
        b.y = Number(Math.max(0, Math.min(1 - b.h, dr.origBox.y + dy)).toFixed(3));
      } else if (dr.target === 'box_se') {
        b.w = Number(Math.max(0.08, Math.min(1 - b.x, dr.origBox.w + dx)).toFixed(3));
        b.h = Number(Math.max(0.08, Math.min(1 - b.y, dr.origBox.h + dy)).toFixed(3));
      } else if (dr.target === 'box_nw') {
        const nx = Math.max(0, Math.min(dr.origBox.x + dr.origBox.w - 0.08, dr.origBox.x + dx));
        const ny = Math.max(0, Math.min(dr.origBox.y + dr.origBox.h - 0.08, dr.origBox.y + dy));
        b.w = Number((dr.origBox.x + dr.origBox.w - nx).toFixed(3));
        b.h = Number((dr.origBox.y + dr.origBox.h - ny).toFixed(3));
        b.x = Number(nx.toFixed(3));
        b.y = Number(ny.toFixed(3));
      } else if (dr.target === 'box_ne') {
        const ny = Math.max(0, Math.min(dr.origBox.y + dr.origBox.h - 0.08, dr.origBox.y + dy));
        b.w = Number(Math.max(0.08, Math.min(1 - b.x, dr.origBox.w + dx)).toFixed(3));
        b.h = Number((dr.origBox.y + dr.origBox.h - ny).toFixed(3));
        b.y = Number(ny.toFixed(3));
      } else if (dr.target === 'box_sw') {
        const nx = Math.max(0, Math.min(dr.origBox.x + dr.origBox.w - 0.08, dr.origBox.x + dx));
        b.w = Number((dr.origBox.x + dr.origBox.w - nx).toFixed(3));
        b.x = Number(nx.toFixed(3));
        b.h = Number(Math.max(0.08, Math.min(1 - b.y, dr.origBox.h + dy)).toFixed(3));
      } else if (dr.target === 'crop_move') {
        let nx = Math.max(0, Math.min(1 - c.w, dr.origCrop.x + dx));
        let ny = Math.max(0, Math.min(1 - c.h, dr.origCrop.y + dy));
        const cxCenter = nx + c.w * 0.5;
        const cyCenter = ny + c.h * 0.5;
        if (Math.abs(cxCenter - 0.5) <= 0.02) {
          nx = Math.max(0, Math.min(1 - c.w, 0.5 - c.w * 0.5));
          snapX = true;
        }
        if (Math.abs(cyCenter - 0.5) <= 0.02) {
          ny = Math.max(0, Math.min(1 - c.h, 0.5 - c.h * 0.5));
          snapY = true;
        }
        c.x = Number(nx.toFixed(3));
        c.y = Number(ny.toFixed(3));
      } else if (dr.target === 'crop_se') {
        c.w = Number(Math.max(0.10, Math.min(1 - c.x, dr.origCrop.w + dx)).toFixed(3));
        c.h = Number(Math.max(0.10, Math.min(1 - c.y, dr.origCrop.h + dy)).toFixed(3));
      } else if (dr.target === 'crop_nw') {
        const nx = Math.max(0, Math.min(dr.origCrop.x + dr.origCrop.w - 0.10, dr.origCrop.x + dx));
        const ny = Math.max(0, Math.min(dr.origCrop.y + dr.origCrop.h - 0.10, dr.origCrop.y + dy));
        c.w = Number((dr.origCrop.x + dr.origCrop.w - nx).toFixed(3));
        c.h = Number((dr.origCrop.y + dr.origCrop.h - ny).toFixed(3));
        c.x = Number(nx.toFixed(3));
        c.y = Number(ny.toFixed(3));
      } else if (dr.target === 'crop_ne') {
        const ny = Math.max(0, Math.min(dr.origCrop.y + dr.origCrop.h - 0.10, dr.origCrop.y + dy));
        c.w = Number(Math.max(0.10, Math.min(1 - c.x, dr.origCrop.w + dx)).toFixed(3));
        c.h = Number((dr.origCrop.y + dr.origCrop.h - ny).toFixed(3));
        c.y = Number(ny.toFixed(3));
      } else if (dr.target === 'crop_sw') {
        const nx = Math.max(0, Math.min(dr.origCrop.x + dr.origCrop.w - 0.10, dr.origCrop.x + dx));
        c.w = Number((dr.origCrop.x + dr.origCrop.w - nx).toFixed(3));
        c.x = Number(nx.toFixed(3));
        c.h = Number(Math.max(0.10, Math.min(1 - c.y, dr.origCrop.h + dy)).toFixed(3));
      }

      setCropSnapGuides({ x: snapX, y: snapY });
      const next = {
        ...dr.latest,
        webcam_box: b,
        focus_anchor: anchor,
        ...(dr.target.startsWith('crop_') ? { [dr.cropField]: c } : {}),
      };
      dr.latest = next;
      setFrameBoxDraft(next);
    };
    const onUp = () => {
      const dr = frameDragRef.current;
      setDraggingFrameMode(null);
      setCropSnapGuides({ x: false, y: false });
      frameDragRef.current = null;
      if (dr) {
        const payload: Parameters<typeof persistFramingOverride>[0] = {
          webcam_box: dr.latest.webcam_box,
          focus_anchor: dr.latest.focus_anchor,
          lock_camera: dr.latest.lock_camera,
        };
        if (dr.target.startsWith('crop_')) {
          payload[dr.cropField] = dr.latest[dr.cropField];
        }
        void persistFramingOverride(payload);
      }
    };
    window.addEventListener('pointermove', onMove);
    window.addEventListener('pointerup', onUp);
    return () => {
      window.removeEventListener('pointermove', onMove);
      window.removeEventListener('pointerup', onUp);
    };
  }, [draggingFrameMode, clip?.id]);

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
  const [publishSuccess, setPublishSuccess] = useState<{
    video_id: string;
    url: string;
    privacy_status: string;
    channel_title?: string | null;
  } | null>(null);
  const [publishError, setPublishError] = useState<string | null>(null);
  const [ytStatus, setYtStatus] = useState<{
    client_configured: boolean;
    connected: boolean;
    channel_title: string | null;
    channel_id: string | null;
  } | null>(null);
  const [isLoadingYtStatus, setIsLoadingYtStatus] = useState(false);
  const [isConnectingYt, setIsConnectingYt] = useState(false);
  const [isUploadingSecrets, setIsUploadingSecrets] = useState(false);
  const [showOAuthSetup, setShowOAuthSetup] = useState(false);
  const [ytAuthError, setYtAuthError] = useState<string | null>(null);
  const clientSecretsInputRef = useRef<HTMLInputElement>(null);
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
  const [isPreflightOpen, setIsPreflightOpen] = useState(false);
  const [isRunningPreflight, setIsRunningPreflight] = useState(false);
  const [preflightError, setPreflightError] = useState<string | null>(null);
  const [preflightReport, setPreflightReport] = useState<PreflightReport | null>(null);
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
    setPreflightReport(null);
    setPreflightError(null);
    setIsPreflightOpen(false);
  }, [clip?.id]);

  const handleRunPreflight = async (forceKeepOpen = true) => {
    if (!clip) return;
    if (forceKeepOpen) setIsPreflightOpen(true);
    setIsRunningPreflight(true);
    setPreflightError(null);
    try {
      const report = await api.getClipPreflight(clip.project_id, clip.id);
      setPreflightReport(report as PreflightReport);
    } catch (err: any) {
      setPreflightError(err?.message || 'Pre-flight safety scan failed');
    } finally {
      setIsRunningPreflight(false);
    }
  };

  useEffect(() => {
    if (!clip || !isPreflightOpen) return;
    void handleRunPreflight(false);
  }, [clip?.id, isPreflightOpen]);

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
  const isStage5Rendering = Boolean(
    clip &&
    !clipIsRendered &&
    (clip.status === 'rendering' || clip.render_status === 'rendering') &&
    !isAutoRendering &&
    !isPreparingClip
  );

  useEffect(() => {
    if (seekTime !== undefined && clip) {
      setCurrentTime(seekTime);
      onPlaybackTimeChange?.(seekTime);
      setShowExactFrame(false);
      if (videoRef.current && clipIsRendered) {
        videoRef.current.currentTime = seekTime;
      }
    }
  }, [seekTime]);

  useEffect(() => {
    if (!externalThumbRequest || !clip) return;
    setThumbHeadline(clip.hook_headline || clip.selected_title || 'WATCH THIS');
    setThumbFormat(clip.aspect_ratio === '16:9' ? '16:9' : '9:16');
    setThumbAccentColor(clip.active_color || '#C6F32F');
    setThumbUsePlayhead(false);
    setThumbCustomTimestamp(externalThumbRequest.timestamp);
    setThumbError(null);
    setIsThumbModalOpen(true);
  }, [externalThumbRequest?.seq]);

  useEffect(() => {
    if (!isThumbModalOpen || !clip) return;
    if (Array.isArray(clip.best_frames) && clip.best_frames.length > 0) {
      setThumbBestFrames(clip.best_frames);
      return;
    }
    let cancelled = false;
    api
      .getBestFrames(clip.project_id, clip.id)
      .then((res) => {
        if (!cancelled && Array.isArray(res?.frames)) {
          setThumbBestFrames(res.frames);
        }
      })
      .catch(() => { });
    return () => {
      cancelled = true;
    };
  }, [isThumbModalOpen, clip?.id]);

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
      videoRef.current.play().catch(() => { });
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
    clipStartOffset,
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
    clipStartOffset,
    duration,
    keepSegments,
    onTrimClip,
    onUpdateSegments,
    onPlaybackTimeChange,
    handleBladeSplit,
  };

  const previewFps = clip?.export_fps === '60' ? 60 : clip?.export_fps === '24' ? 24 : 30;

  const formatSMPTE = (secs: number, fpsVal = previewFps) => {
    const safe = Math.max(0, secs || 0);
    const totalFrames = Math.round(safe * fpsVal);
    const f = totalFrames % fpsVal;
    const totalSec = Math.floor(totalFrames / fpsVal);
    const s = totalSec % 60;
    const m = Math.floor(totalSec / 60) % 60;
    const h = Math.floor(totalSec / 3600);
    return `${String(h).padStart(2, '0')}:${String(m).padStart(2, '0')}:${String(s).padStart(2, '0')}:${String(f).padStart(2, '0')}`;
  };

  const stepSingleFrame = (framesDelta: number) => {
    const vid = videoRef.current;
    if (vid && !vid.paused) {
      vid.pause();
      setIsPlaying(false);
    }
    const maxDur =
      vid && Number.isFinite(vid.duration) && vid.duration > 0
        ? Math.max(0.2, vid.duration - clipStartOffset)
        : Math.max(1, duration || 30);
    const stepSec = framesDelta / Math.max(1, previewFps);
    const nextRel = Math.max(0, Math.min(maxDur, Number((relCurrent + stepSec).toFixed(4))));
    const absTarget = clipStartOffset + nextRel;
    if (vid) vid.currentTime = absTarget;
    setCurrentTime(absTarget);
    onPlaybackTimeChange?.(nextRel);
  };

  // Keyboard shortcut event bridge from App.tsx (Space, Arrows +/-1f, Shift+Arrows +/-1s, I/O, B, M)
  useEffect(() => {
    const onPlayerCmd = (ev: Event) => {
      const detail = (ev as CustomEvent<{ type: string; delta?: number; frames?: number }>).detail;
      if (!detail) return;
      const vid = videoRef.current;
      const st = cmdStateRef.current;
      if (detail.type === 'togglePlay' && vid) {
        if (vid.paused) {
          setShowExactFrame(false);
          vid.play().catch(() => { });
          setIsPlaying(true);
        } else {
          vid.pause();
          setIsPlaying(false);
        }
      } else if (detail.type === 'toggleMute' && vid) {
        const nextMuted = !vid.muted;
        vid.muted = nextMuted;
        setIsMuted(nextMuted);
      } else if (detail.type === 'stepFrame') {
        if (vid && !vid.paused) {
          vid.pause();
          setIsPlaying(false);
        }
        const fpsVal = st.clip?.export_fps === '60' ? 60 : st.clip?.export_fps === '24' ? 24 : 30;
        const maxDur =
          vid && Number.isFinite(vid.duration) && vid.duration > 0
            ? Math.max(0.2, vid.duration - (st.clipStartOffset || 0))
            : Math.max(1, st.duration);
        const stepSec = (detail.frames || 0) / fpsVal;
        const nextRel = Math.max(0, Math.min(maxDur, Number((st.relCurrent + stepSec).toFixed(4))));
        const absT = (st.clipStartOffset || 0) + nextRel;
        if (vid) vid.currentTime = absT;
        setCurrentTime(absT);
        st.onPlaybackTimeChange?.(nextRel);
      } else if (detail.type === 'seekDelta') {
        const maxDur =
          vid && Number.isFinite(vid.duration) && vid.duration > 0
            ? Math.max(0.2, vid.duration - (st.clipStartOffset || 0))
            : Math.max(1, st.duration);
        const nextRel = Math.max(0, Math.min(maxDur, st.relCurrent + (detail.delta || 0)));
        const absT = (st.clipStartOffset || 0) + nextRel;
        if (vid) vid.currentTime = absT;
        setCurrentTime(absT);
        st.onPlaybackTimeChange?.(nextRel);
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
        framing_user_locked: clip.framing_user_locked,
        webcam_corner: clip.webcam_corner,
        source_corner: clip.source_webcam_region?.locked_zone || 'auto',
        webcam_split_ratio: clip.webcam_split_ratio,
        pip_border_color: clip.pip_border_color,
        source_webcam_region: clip.source_webcam_region,
        framing_override: clip.framing_override,
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
        preset_intensity: clip.preset_intensity,
        exposure: clip.exposure,
        brightness: clip.brightness,
        contrast: clip.contrast,
        lift_mids: clip.lift_mids,
        highlights: clip.highlights,
        shadows: clip.shadows,
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
        color_temp: clip.color_temp,
        tint_shift: clip.tint_shift,
        vibrance: clip.vibrance,
        matte_fade: clip.matte_fade,
        halation_glow: clip.halation_glow,
        lut_path: clip.lut_path,
        chroma_split: clip.chroma_split,
        letterbox: clip.letterbox,
        speed_ramp: clip.speed_ramp,
        horizon_tilt: clip.horizon_tilt ?? 0,
        bypass_color: compareOriginal,
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
        pill_color: clip.pill_color,
        pacing_density: clip.pacing_density,
        text_case: clip.text_case,
        hold_back_alpha: clip.hold_back_alpha,
        hold_back_opacity: clip.hold_back_alpha,
        highlight_shape: clip.highlight_shape,
        position_grid: clip.position_grid,
        position_x: clip.position_x,
        pos_x: clip.position_x,
        position_y: clip.position_y,
        pos_y: clip.position_y,
        caption_x: clip.caption_x ?? (clip.position_x ?? 50) / 100,
        caption_y: clip.caption_y ?? (clip.position_y ?? 78) / 100,
        caption_custom_position: Boolean(clip.caption_custom_position),
        caption_rotation: clip.caption_rotation ?? 0,
        caption_max_width: clip.caption_max_width ?? 0.82,
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
        outro_cta_headline: clip.outro_cta_headline,
        outro_cta_handle: clip.outro_cta_handle,
        outro_logo_path: clip.outro_logo_path,
        words: clip.transcript_words,
        keep_segments: keepSegments,
        merge_crossfade_sec: clip.merge_crossfade_sec,
        speakers: clip.speakers,
        speaker_lower_thirds: clip.speaker_lower_thirds,
        face_safe_captions: clip.caption_custom_position ? false : (clip.face_safe_captions ?? true),
        callout_cards: clip.callout_cards,
        auto_callouts: clip.auto_callouts,
        bilingual_enabled: clip.bilingual_enabled,
        secondary_language: clip.secondary_language,
        backdrop_mode: clip.backdrop_mode,
        backdrop_blur_intensity: clip.backdrop_blur_intensity,
        backdrop_color: clip.backdrop_color,
        privacy_blur_boxes: clip.privacy_blur_boxes,
        use_cold_open: clip.cold_open ?? clip.use_cold_open,
        cold_open: clip.cold_open ?? clip.use_cold_open,
        cold_open_quote: clip.cold_open_quote,
        auto_trim_dead_air: clip.trim_dead_air ?? clip.auto_trim_dead_air,
        trim_dead_air: clip.trim_dead_air ?? clip.auto_trim_dead_air,
        silence_threshold: clip.silence_threshold,
        dynamic_punch_zoom: clip.punch_zoom ?? clip.dynamic_punch_zoom,
        punch_zoom: clip.punch_zoom ?? clip.dynamic_punch_zoom,
        punch_zoom_intensity: clip.punch_zoom_intensity
      });
      setExactFrameUrl(res.frame_url);
      setFrameLatencyMs(res.render_time_ms);
      setShowExactFrame(true);
      if (res.timeline_fx_summary) {
        window.dispatchEvent(
          new CustomEvent('lh-timeline-fx-summary', {
            detail: { clip_id: clip.id, summary: res.timeline_fx_summary },
          })
        );
      }
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
      framing_user_locked: Boolean(clip.framing_user_locked),
      webcam_corner: clip.webcam_corner,
      webcam_split_ratio: clip.webcam_split_ratio,
      pip_border_color: clip.pip_border_color,
      source_webcam_region: clip.source_webcam_region,
      framing_override: clip.framing_override,
      zoom: clip.zoom,
      pan_x_offset: clip.pan_x_offset,
      color_preset: clip.color_preset,
      preset_intensity: clip.preset_intensity,
      export_fps: clip.export_fps,
      exposure: clip.exposure,
      brightness: clip.brightness,
      contrast: clip.contrast,
      lift_mids: clip.lift_mids,
      highlights: clip.highlights,
      shadows: clip.shadows,
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
      color_temp: clip.color_temp,
      tint_shift: clip.tint_shift,
      vibrance: clip.vibrance,
      matte_fade: clip.matte_fade,
      halation_glow: clip.halation_glow,
      lut_path: clip.lut_path,
      chroma_split: clip.chroma_split,
      letterbox: clip.letterbox,
      speed_ramp: clip.speed_ramp,
      horizon_tilt: clip.horizon_tilt ?? 0,
      voice_eq_preset: clip.voice_eq_preset,
      de_esser: clip.de_esser,
      voice_enhancer: clip.voice_enhancer,
      loudness_target: clip.loudness_target,
      burn_captions: clip.burn_captions,
      timing_offset: clip.timing_offset,
      caption_style: clip.caption_style,
      caption_font: clip.caption_font,
      caption_animation: clip.caption_animation,
      font_size_percent: clip.font_size_percent,
      base_color: clip.base_color,
      active_color: clip.active_color,
      outline_color: clip.outline_color,
      pill_color: clip.pill_color,
      pacing_density: clip.pacing_density,
      text_case: clip.text_case,
      hold_back_alpha: clip.hold_back_alpha,
      highlight_shape: clip.highlight_shape,
      position_grid: clip.position_grid,
      position_x: clip.position_x,
      position_y: clip.position_y,
      caption_x: clip.caption_x,
      caption_y: clip.caption_y,
      caption_custom_position: Boolean(clip.caption_custom_position),
      face_safe_captions: Boolean(clip.face_safe_captions),
      caption_rotation: clip.caption_rotation,
      caption_max_width: clip.caption_max_width,
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
      use_cold_open: clip.cold_open ?? clip.use_cold_open,
      cold_open: clip.cold_open ?? clip.use_cold_open,
      cold_open_quote: clip.cold_open_quote,
      auto_trim_dead_air: clip.trim_dead_air ?? clip.auto_trim_dead_air,
      trim_dead_air: clip.trim_dead_air ?? clip.auto_trim_dead_air,
      dynamic_punch_zoom: clip.punch_zoom ?? clip.dynamic_punch_zoom,
      punch_zoom: clip.punch_zoom ?? clip.dynamic_punch_zoom,
      punch_zoom_intensity: clip.punch_zoom_intensity,
      retention_bar_enabled: clip.retention_bar_enabled,
      retention_bar_color: clip.retention_bar_color,
      outro_cta_enabled: clip.outro_cta_enabled,
      outro_cta_text: clip.outro_cta_text,
      outro_cta_headline: clip.outro_cta_headline,
      outro_cta_handle: clip.outro_cta_handle,
      outro_logo_path: clip.outro_logo_path,
      broll_segments: clip.broll_segments,
      keep_segments: clip.keep_segments,
      merge_crossfade_sec: clip.merge_crossfade_sec,
      speakers: clip.speakers,
      speaker_lower_thirds: clip.speaker_lower_thirds,
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
      privacy_blur_boxes: clip.privacy_blur_boxes
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

      onAutoRenderClip(targetProjectId, targetClipId, controller.signal, {
        caption_x: clip.caption_x ?? (clip.position_x ?? 50) / 100,
        caption_y: clip.caption_y ?? (clip.position_y ?? 78) / 100,
        caption_custom_position: Boolean(clip.caption_custom_position),
        face_safe_captions: clip.caption_custom_position ? false : (clip.face_safe_captions ?? true),
        use_cold_open: Boolean(clip.cold_open ?? clip.use_cold_open),
        cold_open: Boolean(clip.cold_open ?? clip.use_cold_open),
        auto_trim_dead_air: Boolean(clip.trim_dead_air ?? clip.auto_trim_dead_air),
        trim_dead_air: Boolean(clip.trim_dead_air ?? clip.auto_trim_dead_air),
        dynamic_punch_zoom: Boolean(clip.punch_zoom ?? clip.dynamic_punch_zoom),
        punch_zoom: Boolean(clip.punch_zoom ?? clip.dynamic_punch_zoom),
        punch_zoom_intensity: clip.punch_zoom_intensity ?? 1.16,
        silence_threshold: clip.silence_threshold ?? 0.55,
        frame_layout: clip.frame_layout,
        framing_template: clip.frame_layout,
        framing_user_locked: clip.framing_user_locked,
        webcam_corner: clip.webcam_corner,
        webcam_split_ratio: clip.webcam_split_ratio,
        pip_border_color: clip.pip_border_color,
        source_corner: clip.source_webcam_region?.locked_zone || 'auto',
      })
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

      // Stage 5 initial pre-render is already running on the backend: wait for polling instead of duplicating
      if (isStage5Rendering) {
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

    // Wait cleanly while backend Stage 5 initial preview render finishes for unedited options
    if (isStage5Rendering && lastRenderedHashByClipRef.current[clip.id] === previewConfigSig) {
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
  }, [clip?.id, previewConfigSig, autoRender, isPlaying, clipIsRendered, isStage5Rendering]);

  useEffect(() => {
    if (!clip || isPlaying) return;
    const fxOrLayoutActive = Boolean(
      clip.cold_open ??
        clip.use_cold_open ??
        clip.trim_dead_air ??
        clip.auto_trim_dead_air ??
        clip.punch_zoom ??
        clip.dynamic_punch_zoom ??
        (clip.frame_layout && clip.frame_layout !== 'auto_track')
    );
    if (!fxOrLayoutActive) return;
    const timer = window.setTimeout(() => {
      void fetchExactFrame();
    }, 180);
    return () => window.clearTimeout(timer);
  }, [
    clip?.id,
    clip?.use_cold_open,
    clip?.cold_open,
    clip?.auto_trim_dead_air,
    clip?.trim_dead_air,
    clip?.dynamic_punch_zoom,
    clip?.punch_zoom,
    clip?.punch_zoom_intensity,
    clip?.frame_layout,
    clip?.webcam_corner,
    clip?.source_webcam_region?.locked_zone,
    isPlaying,
  ]);

  // Safe-Zone options mapped cleanly to existing backend values
  const safeZones = [
    { value: 'No guide', label: 'None' },
    { value: 'TikTok', label: 'TikTok' },
    { value: 'Instagram', label: 'Reels' },
    { value: 'YT Shorts', label: 'Shorts' }
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
  const zoomScale = canvasZoom === '75' ? 0.75 : canvasZoom === '100' ? 1.25 : 1.0;
  const displayFrameWidth = Math.max(120, Math.floor(frameWidth * zoomScale));
  const displayFrameHeight = Math.max(120, Math.floor(frameHeight * zoomScale));

  const isCompleted = Boolean(
    clip && (clip.status === 'ready' || clip.render_status === 'completed')
  );
  const downloadUrl = clip ? api.getDownloadUrl(clip.project_id, clip.id) : '#';

  const videoFilterAlreadyBaked = Boolean(
    clip && isRenderedSource && lastRenderedHashByClipRef.current[clip.id] === previewConfigSig
  );

  // Fetch cached ungraded reference frame when Compare Original (Hold) is active over baked/exact renders
  useEffect(() => {
    if (!compareOriginal || !clip || (!showExactFrame && !videoFilterAlreadyBaked)) {
      if (!compareOriginal) setRawFrameUrl(null);
      return;
    }
    const cacheKey = `${clip.id}_${relCurrent.toFixed(2)}_${clip.frame_layout}_${clip.aspect_ratio}_${clip.webcam_corner}_${clip.zoom}_${clip.pan_x_offset}`;
    if (rawFrameCacheRef.current[cacheKey]) {
      setRawFrameUrl(rawFrameCacheRef.current[cacheKey]);
      return;
    }
    let cancelled = false;
    const { masterT: absTimestamp } = mapRelToMasterAndSeg(
      relCurrent,
      keepSegments,
      clip.merge_crossfade_sec ?? 0.03
    );
    api
      .getPreviewFrame({
        clip_id: clip.id,
        project_id: clip.project_id,
        timestamp: absTimestamp,
        clip_rel_ts: relCurrent,
        aspect_ratio: clip.aspect_ratio,
        custom_width: clip.custom_width,
        custom_height: clip.custom_height,
        frame_layout: clip.frame_layout,
        framing_template: clip.frame_layout,
        framing_user_locked: clip.framing_user_locked,
        webcam_corner: clip.webcam_corner,
        source_corner: clip.source_webcam_region?.locked_zone || 'auto',
        webcam_split_ratio: clip.webcam_split_ratio,
        pip_border_color: clip.pip_border_color,
        source_webcam_region: clip.source_webcam_region,
        framing_override: clip.framing_override,
        clip_size: clip.clip_size,
        export_resolution: clip.clip_size,
        zoom: clip.zoom,
        pan_x_offset: clip.pan_x_offset,
        pan_x: Math.round((clip.pan_x_offset || 0) * 100),
        horizon_tilt: clip.horizon_tilt ?? 0,
        bypass_color: true,
        burn_captions: clip.burn_captions,
      })
      .then((res) => {
        if (cancelled) return;
        const rawUrl = res.frame_url.startsWith('http')
          ? res.frame_url
          : `http://127.0.0.1:8000${res.frame_url}`;
        rawFrameCacheRef.current[cacheKey] = rawUrl;
        setRawFrameUrl(rawUrl);
      })
      .catch(() => {
        // Fallback smoothly to CSS filter bypass
      });
    return () => {
      cancelled = true;
    };
  }, [compareOriginal, clip?.id, relCurrent, showExactFrame, videoFilterAlreadyBaked]);

  const videoCssFilter =
    compareOriginal
      ? 'none'
      : clip && !videoFilterAlreadyBaked
        ? (() => {
          const expShift = (clip.exposure || 0) * 0.65 + (clip.shadows || 0) / 500 + (clip.highlights || 0) / 600;
          const b = Math.max(0.25, 1 + (clip.brightness || 0) + expShift);
          const c = Math.max(0.3, (clip.contrast ?? 1.0) - (clip.matte_fade || 0) / 500);
          const bw = Math.max(0, Math.min(1, clip.black_and_white || 0));
          const vib = 1 + (clip.vibrance || 0) / 220;
          const s = Math.max(0, (clip.saturation ?? 1.0) * (1 - bw) * vib);
          const sep = Math.max(0, Math.min(0.45, Math.max(0, (clip.color_temp || 0) / 160) + (clip.vintage || 0) * 0.65));
          const bl = Math.max(0, (clip.blur || 0) * 6);
          const tempDeg = ((clip.color_temp || 0) / 100) * -12 + (clip.warmth || 0) * -22 + ((clip.tint_shift || 0) / 100) * 14;
          const parts = [
            `brightness(${b.toFixed(2)})`,
            `contrast(${c.toFixed(2)})`,
            `saturate(${s.toFixed(2)})`,
          ];
          if (bw > 0.01) parts.push(`grayscale(${bw.toFixed(2)})`);
          if (sep > 0.01) parts.push(`sepia(${sep.toFixed(2)})`);
          if (Math.abs(tempDeg) > 0.5) parts.push(`hue-rotate(${tempDeg.toFixed(1)}deg)`);
          if (bl > 0.05) parts.push(`blur(${bl.toFixed(1)}px)`);
          return parts.join(' ');
        })()
        : 'none';

  const refreshYouTubeStatus = async () => {
    setIsLoadingYtStatus(true);
    try {
      const st = await api.getYouTubeStatus();
      setYtStatus(st);
      if (st.connected) {
        setShowOAuthSetup(false);
      }
    } catch (err: any) {
      setYtAuthError(err?.message || 'Could not inspect YouTube connection status.');
    } finally {
      setIsLoadingYtStatus(false);
    }
  };

  useEffect(() => {
    if (isPublishModalOpen) {
      void refreshYouTubeStatus();
    }
  }, [isPublishModalOpen]);

  const handleOpenPublish = () => {
    if (clip) {
      setPublishTitle(clip.selected_title || clip.youtube_titles?.[0] || 'Viral Video #Shorts');
      const baseDesc = (clip.youtube_description || 'Created with Lost Heaven Studio! #Shorts').trim();
      const extraTags = (clip.hashtags || '').trim();
      setPublishDesc(extraTags && !baseDesc.includes(extraTags) ? `${baseDesc}\n\n${extraTags}` : baseDesc);
      setPublishPrivacy(clip.privacy_status || 'private');
      setPublishSuccess(null);
      setPublishError(null);
      setYtAuthError(null);
      setSocialCopy(null);
    }
    setIsPublishModalOpen(true);
  };

  const handleUploadClientSecrets = async (e: React.ChangeEvent<HTMLInputElement>) => {
    const file = e.target.files?.[0];
    e.target.value = '';
    if (!file) return;
    setIsUploadingSecrets(true);
    setYtAuthError(null);
    try {
      const nextStatus = await api.uploadYouTubeClientSecrets(file);
      setYtStatus(nextStatus);
    } catch (err: any) {
      setYtAuthError(err?.message || 'Failed to save Google OAuth Client JSON.');
    } finally {
      setIsUploadingSecrets(false);
    }
  };

  const handleConnectYouTube = async () => {
    if (!ytStatus?.client_configured) {
      setShowOAuthSetup(true);
      setYtAuthError('Upload your Google OAuth Client JSON first to connect your YouTube channel.');
      return;
    }
    setIsConnectingYt(true);
    setYtAuthError(null);
    try {
      const nextStatus = await api.connectYouTube();
      setYtStatus(nextStatus);
      setShowOAuthSetup(false);
    } catch (err: any) {
      setYtAuthError(err?.message || 'YouTube OAuth connection failed.');
    } finally {
      setIsConnectingYt(false);
    }
  };

  const handleDisconnectYouTube = async () => {
    setYtAuthError(null);
    try {
      const nextStatus = await api.disconnectYouTube();
      setYtStatus(nextStatus);
      setPublishSuccess(null);
    } catch (err: any) {
      setYtAuthError(err?.message || 'Failed to disconnect YouTube channel.');
    }
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
      setPublishError(err?.message || 'Failed to generate copy');
    } finally {
      setIsGeneratingCopy(false);
    }
  };

  const handleCopyText = (key: string, text: string) => {
    navigator.clipboard?.writeText(text).catch(() => { });
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
    if (!ytStatus?.connected) {
      setPublishError('YouTube account not linked. Connect your channel before uploading.');
      return;
    }
    setIsPublishing(true);
    setPublishSuccess(null);
    setPublishError(null);
    try {
      const hashtagMatches = publishDesc.match(/#[A-Za-z0-9_]+/g) || [];
      const cleanTags = Array.from(new Set(hashtagMatches.map((h) => h.replace(/^#/, ''))));
      const res = await api.publishToYouTube(
        clip.id,
        {
          title: publishTitle,
          description: publishDesc,
          tags: cleanTags.length > 0 ? cleanTags : undefined,
          privacy_status: publishPrivacy,
        },
        clip.project_id
      );
      setPublishSuccess({
        video_id: res.video_id,
        url: res.url,
        privacy_status: res.privacy_status || publishPrivacy,
        channel_title: res.channel_title,
      });
      onUpdateClip?.({
        youtube_url: res.url,
        selected_title: publishTitle,
        youtube_description: publishDesc,
        privacy_status: publishPrivacy,
      });
    } catch (err: any) {
      setPublishError(err?.message || 'YouTube upload failed.');
    } finally {
      setIsPublishing(false);
    }
  };

  const tiltDeg =
    !videoFilterAlreadyBaked && Math.abs(clip?.horizon_tilt || 0) > 0.05
      ? (clip?.horizon_tilt || 0)
      : 0;
  const tiltRad = (Math.abs(tiltDeg) * Math.PI) / 180;
  const tiltScale = tiltDeg !== 0 ? Math.max(1.0, Math.cos(tiltRad) + Math.sin(tiltRad) * 1.15) : 1.0;

  return (
    <aside className="w-[420px] xl:w-[460px] shrink-0 h-full min-h-0 box-border overflow-hidden border-l border-[var(--border-subtle)] bg-[var(--bg-surface)] flex flex-col select-none">
      {/* Top Control Strip: 2-Row Responsive Toolbar (Studio Tools + Safe-Zone Guide Switcher) */}
      <div className="px-3 py-2 border-b border-[var(--border-subtle)] space-y-1.5 shrink-0">
        {/* Row 1: Status & Studio Tools */}
        <div className="flex flex-wrap items-center justify-between gap-1.5">
          <div className="flex flex-wrap items-center gap-1.5">
            <button
              type="button"
              onClick={toggleAutoRender}
              className={`flex items-center gap-1 px-2 py-1 rounded-lg text-[10px] font-semibold border transition shrink-0 whitespace-nowrap ${autoRender
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
              className={`flex items-center gap-1 px-2 py-1 rounded-lg text-[10px] font-semibold border transition shrink-0 tabular-nums whitespace-nowrap ${showExactFrame
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
              className={`flex items-center gap-1 px-2 py-1 rounded-lg text-[10px] font-semibold border transition shrink-0 tabular-nums whitespace-nowrap ${isAuditOpen
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

          <div className="flex flex-wrap items-center gap-1 shrink-0">
            {onOpenAnalysisWorkstation && (
              <button
                type="button"
                disabled={!clip}
                onClick={onOpenAnalysisWorkstation}
                className="flex items-center gap-1 px-2 py-1 rounded-lg text-[10px] font-semibold border bg-[var(--bg-card)] border-[var(--border-subtle)] text-[var(--text-primary)] hover:border-[var(--accent-primary)]/50 transition shrink-0 whitespace-nowrap disabled:opacity-45"
                title="Open AI Analysis & Best-Frame Workstation"
              >
                <Sparkles className="w-3 h-3 text-[var(--accent-primary)]" />
                <span>AI Analysis</span>
              </button>
            )}
            <button
              type="button"
              disabled={!clip}
              onClick={() => setIsEditFrameOpen((prev) => !prev)}
              className={`flex items-center gap-1 px-2 py-1 rounded-lg text-[10px] font-semibold border transition shrink-0 whitespace-nowrap disabled:opacity-45 ${isEditFrameOpen
                ? 'bg-[var(--accent-subtle)] border-[var(--accent-primary)] text-[var(--accent-primary)]'
                : 'bg-[var(--bg-card)] border-[var(--border-subtle)] text-[var(--text-secondary)] hover:text-[var(--text-primary)] hover:border-[var(--accent-primary)]/40'
                }`}
              title="Visually drag or resize source webcam box and subject focus anchor"
            >
              <Crop className="w-3 h-3" />
              <span>Edit Frame Box</span>
            </button>
            <button
              type="button"
              disabled={!clip}
              onClick={() => {
                if (!clip) return;
                setThumbHeadline(clip.hook_headline || clip.selected_title || 'WATCH THIS');
                setThumbFormat(clip.aspect_ratio === '16:9' ? '16:9' : '9:16');
                setThumbAccentColor(clip.active_color || '#C6F32F');
                setThumbCustomTimestamp(null);
                setThumbError(null);
                setIsThumbModalOpen(true);
              }}
              className="flex items-center gap-1 px-2 py-1 rounded-lg text-[10px] font-semibold border bg-[var(--bg-card)] border-[var(--border-subtle)] text-[var(--text-secondary)] hover:text-[var(--text-primary)] hover:border-[var(--accent-primary)]/40 transition shrink-0 whitespace-nowrap disabled:opacity-45"
              title="Open AI Thumbnail & Cover Designer"
            >
              <ImageIcon className="w-3 h-3 text-[var(--accent-primary)]" />
              <span>Cover</span>
            </button>
            <button
              type="button"
              disabled={!clip}
              onClick={() => setIsPreflightOpen((prev) => !prev)}
              className={`px-2 py-1 rounded-lg text-[10px] font-bold border transition shrink-0 tabular-nums whitespace-nowrap disabled:opacity-45 ${!preflightReport || preflightReport.status === 'pass'
                ? 'bg-emerald-500/10 border-emerald-500/35 text-emerald-400'
                : preflightReport.status === 'warn'
                  ? 'bg-amber-500/10 border-amber-500/40 text-amber-400'
                  : 'bg-red-500/10 border-red-500/40 text-red-400'
                }`}
              title="Toggle Pre-Flight Readiness Checklist"
            >
              {preflightReport ? `${preflightReport.readiness_score}/100` : 'Safe'}
            </button>
          </div>
        </div>

        {/* Row 2: Safe-Zone Guide Switcher + Compare Original (Hold) */}
        <div className="flex flex-wrap items-center justify-between gap-1.5">
          <div className="flex items-center gap-1.5 min-w-0">
            <span className="text-[10px] font-semibold text-[var(--text-muted)] uppercase tracking-wider shrink-0">
              Safe Zone:
            </span>
            <div className="flex items-center gap-0.5 bg-[var(--bg-input)] p-0.5 rounded-lg border border-[var(--border-subtle)] shrink-0">
              {safeZones.map((g) => (
                <button
                  key={g.value}
                  type="button"
                  onClick={() => onSafeZoneChange(g.value)}
                  className={`px-2 py-0.5 rounded-md text-[10px] font-semibold transition whitespace-nowrap shrink-0 ${safeZoneGuide === g.value
                    ? 'bg-[var(--accent-primary)] text-[var(--accent-foreground)]'
                    : 'text-[var(--text-secondary)] hover:text-[var(--text-primary)]'
                    }`}
                >
                  {g.label}
                </button>
              ))}
            </div>
          </div>

          <div className="flex items-center gap-1 shrink-0">
            <div
              className="flex items-center gap-0.5 bg-[var(--bg-input)] p-0.5 rounded-lg border border-[var(--border-subtle)] shrink-0"
              title="Preview canvas zoom"
            >
              {([
                { id: 'fit' as const, label: 'Fit' },
                { id: '75' as const, label: '75%' },
                { id: '100' as const, label: '100%' },
              ]).map((zm) => (
                <button
                  key={zm.id}
                  type="button"
                  onClick={() => setCanvasZoom(zm.id)}
                  className={`px-1.5 py-0.5 rounded text-[9px] font-bold transition ${canvasZoom === zm.id
                    ? 'bg-[var(--accent-primary)] text-[var(--accent-foreground)]'
                    : 'text-[var(--text-secondary)] hover:text-[var(--text-primary)]'
                    }`}
                >
                  {zm.label}
                </button>
              ))}
            </div>

            <button
              type="button"
              disabled={!clip}
              onClick={() => setFreeformCaptionMode((prev) => !prev)}
              className={`flex items-center gap-1 px-2 py-0.5 rounded-md text-[10px] font-semibold border transition shrink-0 whitespace-nowrap select-none disabled:opacity-45 ${freeformCaptionMode
                ? 'bg-[var(--accent-subtle)] text-[var(--accent-primary)] border-[var(--accent-primary)]'
                : 'bg-[var(--bg-input)] border-[var(--border-subtle)] text-[var(--text-secondary)] hover:text-[var(--text-primary)]'
                }`}
              title="Toggle free-form interactive caption drag box"
            >
              <Move className="w-3 h-3" />
              <span>Drag Captions</span>
            </button>

            {onCompareOriginalChange && (
              <button
                type="button"
                disabled={!clip}
                onPointerDown={() => onCompareOriginalChange(true)}
                onPointerUp={() => onCompareOriginalChange(false)}
                onPointerLeave={() => onCompareOriginalChange(false)}
                onPointerCancel={() => onCompareOriginalChange(false)}
                className={`flex items-center gap-1 px-2 py-0.5 rounded-md text-[10px] font-semibold border transition shrink-0 whitespace-nowrap select-none disabled:opacity-45 ${compareOriginal
                  ? 'bg-[var(--accent-primary)] text-[var(--accent-foreground)] border-[var(--accent-primary)]'
                  : 'bg-[var(--bg-input)] border-[var(--border-subtle)] text-[var(--text-secondary)] hover:text-[var(--text-primary)]'
                  }`}
                title="Hold to bypass color grade & preview raw source frame"
              >
                <Eye className="w-3 h-3" />
                <span>Original (Hold)</span>
              </button>
            )}
          </div>
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
                  className={`text-[10px] font-bold px-1.5 py-0.5 rounded-full tabular-nums ${retentionAudit.overall_score >= 78
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

      {/* Expandable Pre-Flight Readiness & Platform Safety Checklist */}
      {isPreflightOpen && clip && (
        <div className="border-b border-[var(--border-subtle)] bg-[var(--bg-surface)] px-3 py-2.5 space-y-2 shrink-0 max-h-56 overflow-y-auto">
          <div className="flex items-center justify-between gap-2">
            <div className="flex items-center gap-2 min-w-0">
              <Shield className="w-3.5 h-3.5 text-[var(--accent-primary)] shrink-0" />
              <span className="text-[11px] font-bold text-[var(--text-primary)] truncate">
                Pre-Flight Checklist
              </span>
              {preflightReport && (
                <span
                  className={`text-[10px] font-bold px-1.5 py-0.5 rounded-full tabular-nums ${preflightReport.status === 'pass'
                    ? 'bg-emerald-500/15 text-emerald-400'
                    : preflightReport.status === 'warn'
                      ? 'bg-amber-500/15 text-amber-400'
                      : 'bg-red-500/15 text-red-400'
                    }`}
                >
                  {preflightReport.readiness_score}/100
                </span>
              )}
            </div>
            <div className="flex items-center gap-1.5 shrink-0">
              <button
                type="button"
                onClick={() => void handleRunPreflight(true)}
                disabled={isRunningPreflight}
                className="p-1 rounded hover:bg-[var(--bg-hover)] text-[var(--text-secondary)] hover:text-[var(--text-primary)]"
                title="Re-scan pre-flight readiness"
              >
                <RefreshCw className={`w-3 h-3 ${isRunningPreflight ? 'animate-spin' : ''}`} />
              </button>
              <button
                type="button"
                onClick={() => setIsPreflightOpen(false)}
                className="p-1 rounded hover:bg-[var(--bg-hover)] text-[var(--text-secondary)] hover:text-[var(--text-primary)]"
                title="Close pre-flight checklist"
              >
                <X className="w-3 h-3" />
              </button>
            </div>
          </div>

          {isRunningPreflight && !preflightReport && (
            <div className="py-3 flex items-center justify-center gap-2 text-[11px] text-[var(--text-secondary)]">
              <Loader2 className="w-3.5 h-3.5 animate-spin text-[var(--accent-primary)]" />
              <span>Checking loudness, safe margins, frame rate &amp; resolution...</span>
            </div>
          )}

          {preflightError && (
            <div className="text-[11px] text-red-400 bg-red-500/10 border border-red-500/20 rounded-lg px-2.5 py-1.5">
              {preflightError}
            </div>
          )}

          {preflightReport && (
            <div className="space-y-1.5">
              {preflightReport.checks.map((chk: PreflightCheck) => {
                const badgeCls =
                  chk.status === 'pass'
                    ? 'bg-emerald-500/15 text-emerald-400 border-emerald-500/30'
                    : chk.status === 'warn'
                      ? 'bg-amber-500/15 text-amber-400 border-amber-500/30'
                      : 'bg-red-500/15 text-red-400 border-red-500/30';
                return (
                  <div
                    key={chk.id}
                    className="flex items-center justify-between gap-2 p-1.5 rounded-lg bg-[var(--bg-card)] border border-[var(--border-subtle)] text-[10px]"
                  >
                    <div className="min-w-0 flex-1">
                      <div className="flex items-center gap-1.5">
                        <span className={`px-1 py-0.5 rounded border uppercase font-bold text-[9px] shrink-0 ${badgeCls}`}>
                          {chk.status}
                        </span>
                        <span className="font-semibold text-[var(--text-primary)] truncate">
                          {chk.title}
                        </span>
                      </div>
                      <p className="text-[var(--text-secondary)] mt-0.5 leading-snug">
                        {chk.detail}
                      </p>
                    </div>
                    {chk.fix_patch && onApplyFixClip && (
                      <button
                        type="button"
                        onClick={async () => {
                          await onApplyFixClip(clip.id, chk.fix_patch as Partial<ViralClip>);
                          await handleRunPreflight(true);
                        }}
                        className="px-2 py-1 rounded-md bg-[var(--accent-primary)] text-[var(--accent-foreground)] font-bold shrink-0 hover:brightness-110 transition"
                      >
                        {chk.fix_label || 'Fix'}
                      </button>
                    )}
                  </div>
                );
              })}
            </div>
          )}
        </div>
      )}

      {/* Auto-Scaled & Zoomable Preview Stage */}
      <div
        ref={stageRef}
        className={`flex-1 min-h-0 flex items-center justify-center p-3 bg-[var(--bg-canvas)] ${canvasZoom === '100' ? 'overflow-auto' : 'overflow-hidden'}`}
      >
        <div
          style={{ width: `${displayFrameWidth}px`, height: `${displayFrameHeight}px` }}
          className="relative shrink-0 bg-black rounded-[24px] p-1.5 border-2 border-[var(--border-subtle)] shadow-xl flex flex-col overflow-hidden transition-all duration-200"
        >
          <div
            ref={previewStageBoxRef}
            className="relative w-full h-full rounded-[18px] overflow-hidden bg-[#0A0A0A] flex items-center justify-center"
          >
            {compareOriginal && rawFrameUrl ? (
              <img
                src={rawFrameUrl}
                alt="Original Ungraded Frame"
                className="w-full h-full object-cover"
              />
            ) : showExactFrame && exactFrameUrl ? (
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
                      vid.play().catch(() => { });
                    }
                    pendingRestoreRef.current = null;
                  } else {
                    vid.currentTime = 0;
                    setCurrentTime(0);
                    if (shouldAutoPlayRef.current) {
                      shouldAutoPlayRef.current = false;
                      vid.play().catch(() => { });
                    }
                  }
                }}
                onClick={togglePlay}
                style={{
                  filter: videoCssFilter,
                  transform: tiltDeg !== 0 ? `rotate(${tiltDeg}deg) scale(${tiltScale.toFixed(3)})` : undefined,
                }}
                className="w-full h-full object-cover cursor-pointer transition-[filter,transform] duration-75"
                playsInline
                loop
              />
            ) : (
              <div className="flex flex-col items-center justify-center text-zinc-500 p-4 text-center">
                {isStage5Rendering ? (
                  <>
                    <Loader2 className="w-8 h-8 mb-2 animate-spin text-[var(--accent-primary)]" />
                    <span className="text-xs font-semibold text-white">
                      Rendering initial preview...
                    </span>
                    <span className="text-[10px] text-zinc-400 mt-1">
                      Stage 5 queue
                      {typeof clip?.render_progress === 'number' && clip.render_progress > 0
                        ? ` • ${Math.round(clip.render_progress)}%`
                        : ''}
                    </span>
                  </>
                ) : isPreparingClip || (isRendering && !isAutoRendering) ? (
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

            {/* Rule-of-Thirds Overlay Grid on Studio Canvas */}
            {showCanvasThirds && !isEditFrameOpen && (
              <div className="absolute inset-0 z-20 pointer-events-none">
                <div className="absolute inset-y-0 left-1/3 border-l border-white/30" />
                <div className="absolute inset-y-0 left-2/3 border-l border-white/30" />
                <div className="absolute inset-x-0 top-1/3 border-t border-white/30" />
                <div className="absolute inset-x-0 top-2/3 border-t border-white/30" />
              </div>
            )}

            {/* Top Hook Banner Fallback ONLY when showing raw unrendered video (never duplicate backend PNG/MP4 banners) */}
            {!showExactFrame && !isRenderedSource && clip?.show_hook_banner && clip?.hook_headline && (
              <div className="absolute top-6 left-3 right-3 z-20 pointer-events-none flex justify-center">
                <div className="px-2.5 py-1 rounded-lg bg-black/80 border border-yellow-400 text-yellow-300 font-black text-[10px] uppercase tracking-wide shadow text-center leading-tight">
                  ★ {clip.hook_headline} ★
                </div>
              </div>
            )}

            {/* Free-Form Interactive Caption Drag & Snap Alignment Overlay */}
            {freeformCaptionMode && !isEditFrameOpen && clip && clip.burn_captions && (() => {
              const effCapX = captionDragDraft?.x ?? clip.caption_x ?? ((clip.position_x ?? 50) / 100);
              const effCapY = captionDragDraft?.y ?? clip.caption_y ?? ((clip.position_y ?? 78) / 100);
              const capRot = clip.caption_rotation ?? 0;
              const capW = Math.max(0.45, Math.min(0.90, clip.caption_max_width ?? 0.82));
              const snapCenterX = Math.abs(effCapX - 0.5) <= 0.015;
              const snapCenterY = Math.abs(effCapY - 0.5) <= 0.015;
              const commitQuickCap = (vals: Partial<ViralClip>) => {
                if (onUpdateClip) onUpdateClip(vals);
                else if (onApplyFixClip) void onApplyFixClip(clip.id, vals);
              };
              return (
                <>
                  {/* Magnetic Center-X and Center-Y Snap Guides */}
                  {(isDraggingCaption || snapCenterX) && snapCenterX && (
                    <div className="pointer-events-none absolute inset-y-0 left-1/2 -translate-x-1/2 border-l border-dashed border-cyan-400/90 z-25" />
                  )}
                  {(isDraggingCaption || snapCenterY) && snapCenterY && (
                    <div className="pointer-events-none absolute inset-x-0 top-1/2 -translate-y-1/2 border-t border-dashed border-lime-400/90 z-25" />
                  )}

                  {/* Interactive Draggable Caption Bounding Box */}
                  <div
                    onPointerDown={(ev) => {
                      ev.preventDefault();
                      ev.stopPropagation();
                      captionDragRef.current = {
                        startX: ev.clientX,
                        startY: ev.clientY,
                        origX: effCapX,
                        origY: effCapY,
                        latestX: effCapX,
                        latestY: effCapY,
                      };
                      setCaptionDragDraft({ x: effCapX, y: effCapY });
                      setIsDraggingCaption(true);
                    }}
                    style={{
                      left: `${effCapX * 100}%`,
                      top: `${effCapY * 100}%`,
                      width: `${capW * 100}%`,
                      transform: `translate(-50%, -50%) rotate(${capRot}deg)`,
                    }}
                    className={`absolute z-30 min-h-[38px] px-2 py-1.5 rounded-lg border-2 border-dashed cursor-grab active:cursor-grabbing select-none flex flex-col items-center justify-center transition-colors ${isDraggingCaption
                      ? 'border-cyan-300 bg-cyan-500/20 shadow-[0_0_0_2px_rgba(34,211,238,0.25)]'
                      : 'border-[var(--accent-primary)]/85 bg-black/25 hover:bg-black/40'
                      }`}
                    title="Drag anywhere on canvas to reposition captions (snaps to center)"
                  >
                    <div className="flex items-center gap-1 text-[9px] font-mono font-bold text-white drop-shadow">
                      <Move className="w-2.5 h-2.5 text-[var(--accent-primary)]" />
                      <span>
                        CAPTION ({Math.round(effCapX * 100)}%, {Math.round(effCapY * 100)}%)
                        {Math.abs(capRot) > 0.2 ? ` · ${capRot}°` : ''}
                      </span>
                    </div>
                  </div>

                  {/* Compact Quick Position Reset Pills */}
                  <div
                    onClick={(ev) => ev.stopPropagation()}
                    className="absolute bottom-1.5 left-1/2 -translate-x-1/2 z-30 flex items-center gap-1 px-1.5 py-0.5 rounded-full bg-black/80 border border-white/15 backdrop-blur-sm"
                  >
                    {[
                      { label: 'Center Bottom', x: 0.5, y: 0.78, rot: 0, grid: 'bottom_center' },
                      { label: 'Dead Center', x: 0.5, y: 0.5, rot: 0, grid: 'center' },
                      { label: 'Top Safe', x: 0.5, y: 0.18, rot: 0, grid: 'top_center' },
                    ].map((qp) => (
                      <button
                        key={qp.label}
                        type="button"
                        onClick={() =>
                          commitQuickCap({
                            caption_x: qp.x,
                            caption_y: qp.y,
                            caption_custom_position: true,
                            face_safe_captions: false,
                            caption_rotation: qp.rot,
                            position_x: Math.round(qp.x * 100),
                            position_y: Math.round(qp.y * 100),
                            position_grid: qp.grid,
                          })
                        }
                        className="px-1.5 py-0.5 rounded text-[8px] font-semibold text-zinc-200 hover:text-black hover:bg-[var(--accent-primary)] transition whitespace-nowrap"
                      >
                        {qp.label}
                      </button>
                    ))}
                    <button
                      type="button"
                      onClick={() =>
                        commitQuickCap({
                          caption_x: 0.5,
                          caption_y: 0.78,
                          caption_custom_position: false,
                          face_safe_captions: true,
                          caption_rotation: 0,
                          caption_max_width: 0.82,
                          position_x: 50,
                          position_y: 78,
                          position_grid: 'bottom_center',
                        })
                      }
                      className="px-1 py-0.5 rounded text-[8px] font-semibold text-amber-300 hover:text-white flex items-center gap-0.5 whitespace-nowrap"
                      title="Reset caption coordinates and tilt"
                    >
                      <RotateCcw className="w-2.5 h-2.5" />
                      <span>Reset</span>
                    </button>
                  </div>
                </>
              );
            })()}

            {/* Interactive Tracked Privacy / HUD Blur Box Handles */}
            {!isEditFrameOpen && (blurDraftBoxes || clip?.privacy_blur_boxes || []).map((box, bIdx, allBoxes) => (
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

            {/* Interactive On-Canvas Source Frame Crop Box, Keyframe Animator & Webcam Editor */}
            {isEditFrameOpen && clip && (() => {
              const activeCropField: 'frame_crop' | 'keyframe_start_crop' | 'keyframe_end_crop' =
                frameEditTarget === 'start_kf'
                  ? 'keyframe_start_crop'
                  : frameEditTarget === 'end_kf'
                    ? 'keyframe_end_crop'
                    : 'frame_crop';
              const activeCropBox: FramingBox =
                activeCropField === 'keyframe_start_crop'
                  ? (frameBoxDraft.keyframe_start_crop || frameBoxDraft.frame_crop)
                  : activeCropField === 'keyframe_end_crop'
                    ? (frameBoxDraft.keyframe_end_crop || frameBoxDraft.keyframe_start_crop || frameBoxDraft.frame_crop)
                    : frameBoxDraft.frame_crop;
              const isWebcamLayout = ['webcam_gameplay_split', 'gameplay_webcam_pip', 'portrait_pip_badge', 'triple_gaming_hud'].includes(
                String(clip.frame_layout || '')
              );
              const applyAspectFitCrop = () => {
                const ar = getAspectNumeric(clip.aspect_ratio);
                const srcRatio = 16 / 9;
                let w = Math.min(0.92, (ar / srcRatio) * 0.90);
                let h = 0.90;
                if (w > 0.92) {
                  w = 0.92;
                  h = Math.min(0.92, (w * srcRatio) / ar);
                }
                const fitted: FramingBox = {
                  x: Number((0.5 - w * 0.5).toFixed(3)),
                  y: Number((0.5 - h * 0.5).toFixed(3)),
                  w: Number(w.toFixed(3)),
                  h: Number(h.toFixed(3)),
                };
                const next = { ...frameBoxDraft, [activeCropField]: fitted };
                setFrameBoxDraft(next);
                void persistFramingOverride({ [activeCropField]: fitted });
              };
              return (
              <div className="absolute inset-0 z-40 bg-black/92 flex flex-col justify-between p-2 select-none">
                <div className="flex flex-wrap items-center justify-between gap-1 bg-black/80 border border-white/15 rounded-lg px-2 py-1 text-[9px] text-white">
                  <div className="flex items-center gap-1 flex-wrap">
                    {([
                      { id: 'frame_crop' as const, label: 'Static' },
                      { id: 'start_kf' as const, label: 'Start KF' },
                      { id: 'end_kf' as const, label: 'End KF' },
                    ]).map((pill) => (
                      <button
                        key={pill.id}
                        type="button"
                        onClick={() => {
                          setFrameEditTarget(pill.id);
                          if (pill.id === 'start_kf') {
                            seekToRelTime(0);
                            if (!frameBoxDraft.keyframe_start_crop) {
                              const next = { ...frameBoxDraft, keyframe_start_crop: { ...frameBoxDraft.frame_crop } };
                              setFrameBoxDraft(next);
                              void persistFramingOverride({ keyframe_start_crop: next.keyframe_start_crop });
                            }
                          } else if (pill.id === 'end_kf') {
                            seekToRelTime(Math.max(0.1, duration - 0.1));
                            if (!frameBoxDraft.keyframe_end_crop) {
                              const seed = frameBoxDraft.keyframe_start_crop || frameBoxDraft.frame_crop;
                              const next = { ...frameBoxDraft, keyframe_end_crop: { ...seed } };
                              setFrameBoxDraft(next);
                              void persistFramingOverride({ keyframe_end_crop: next.keyframe_end_crop });
                            }
                          }
                        }}
                        className={`px-1.5 py-0.5 rounded border font-bold transition ${frameEditTarget === pill.id
                          ? 'bg-cyan-500/25 border-cyan-400 text-cyan-200'
                          : 'border-white/20 text-white/75 hover:text-white'
                          }`}
                        title={pill.id === 'start_kf' ? 'Edit crop at start of clip' : pill.id === 'end_kf' ? 'Edit crop at end of clip' : 'Edit main crop box'}
                      >
                        {pill.label}
                      </button>
                    ))}
                    <button
                      type="button"
                      onClick={() => setShowRuleOfThirds((v) => !v)}
                      className={`inline-flex items-center gap-0.5 px-1.5 py-0.5 rounded border font-semibold ${showRuleOfThirds
                        ? 'bg-white/15 border-white/40 text-white'
                        : 'border-white/20 text-white/70 hover:text-white'
                        }`}
                      title="Toggle 3x3 Rule-of-Thirds overlay"
                    >
                      <Grid className="w-2.5 h-2.5" />
                      <span>3×3</span>
                    </button>
                    <button
                      type="button"
                      onClick={applyAspectFitCrop}
                      className="px-1.5 py-0.5 rounded border border-white/20 text-white/80 hover:text-white font-semibold"
                      title={`Snap crop box to ${clip.aspect_ratio || '9:16'} aspect`}
                    >
                      Fit {clip.aspect_ratio || '9:16'}
                    </button>
                  </div>

                  <div className="flex items-center gap-1">
                    <button
                      type="button"
                      onClick={() => {
                        const nextLock = !frameBoxDraft.lock_camera;
                        const next = { ...frameBoxDraft, lock_camera: nextLock };
                        setFrameBoxDraft(next);
                        void persistFramingOverride(next);
                      }}
                      className={`inline-flex items-center gap-1 px-1.5 py-0.5 rounded border font-semibold ${frameBoxDraft.lock_camera
                        ? 'bg-amber-500/25 border-amber-400 text-amber-300'
                        : 'border-white/20 text-white/80 hover:text-white'
                        }`}
                      title="Lock camera smoothly onto Subject Focus crosshair"
                    >
                      {frameBoxDraft.lock_camera ? <Lock className="w-2.5 h-2.5" /> : <Unlock className="w-2.5 h-2.5" />}
                      <span>{frameBoxDraft.lock_camera ? 'Locked' : 'Lock'}</span>
                    </button>
                    <button
                      type="button"
                      onClick={() => void persistFramingOverride({ reset: true })}
                      className="px-1.5 py-0.5 rounded border border-white/20 text-white/80 hover:text-white font-semibold"
                    >
                      Reset
                    </button>
                    <button
                      type="button"
                      onClick={() => setIsEditFrameOpen(false)}
                      className="px-1.5 py-0.5 rounded bg-[var(--accent-primary)] text-[var(--accent-foreground)] font-bold"
                    >
                      Done
                    </button>
                  </div>
                </div>

                <div className="flex-1 flex items-center justify-center min-h-0 my-1">
                  <div
                    ref={frameBoxCanvasRef}
                    style={{ aspectRatio: '16 / 9' }}
                    className="relative w-full max-h-full bg-zinc-900 rounded border border-white/20 overflow-hidden"
                  >
                    {sourceFrameUrl && (
                      <img
                        src={sourceFrameUrl}
                        alt="16:9 Source Frame"
                        className="w-full h-full object-contain pointer-events-none select-none"
                      />
                    )}

                    {/* 3x3 Rule-of-Thirds Overlay Grid */}
                    {showRuleOfThirds && (
                      <div className="absolute inset-0 pointer-events-none">
                        <div className="absolute inset-y-0 left-1/3 border-l border-white/20" />
                        <div className="absolute inset-y-0 left-2/3 border-l border-white/20" />
                        <div className="absolute inset-x-0 top-1/3 border-t border-white/20" />
                        <div className="absolute inset-x-0 top-2/3 border-t border-white/20" />
                      </div>
                    )}

                    {/* Magnetic Center Snap Guides */}
                    {cropSnapGuides.x && (
                      <div className="absolute inset-y-0 left-1/2 -translate-x-1/2 border-l-2 border-dashed border-cyan-300/90 pointer-events-none z-20" />
                    )}
                    {cropSnapGuides.y && (
                      <div className="absolute inset-x-0 top-1/2 -translate-y-1/2 border-t-2 border-dashed border-cyan-300/90 pointer-events-none z-20" />
                    )}

                    {/* Draggable & 4-Corner Resizable Normalized Output Crop Box */}
                    <div
                      onPointerDown={(ev) => {
                        ev.preventDefault();
                        ev.stopPropagation();
                        frameDragRef.current = {
                          target: 'crop_move',
                          cropField: activeCropField,
                          startX: ev.clientX,
                          startY: ev.clientY,
                          origBox: { ...frameBoxDraft.webcam_box },
                          origCrop: { ...activeCropBox },
                          origAnchor: { ...frameBoxDraft.focus_anchor },
                          latest: { ...frameBoxDraft },
                        };
                        setDraggingFrameMode('crop_move');
                      }}
                      style={{
                        left: `${activeCropBox.x * 100}%`,
                        top: `${activeCropBox.y * 100}%`,
                        width: `${activeCropBox.w * 100}%`,
                        height: `${activeCropBox.h * 100}%`,
                      }}
                      className="absolute border-2 border-cyan-400 bg-cyan-500/12 cursor-move z-10"
                      title="Drag crop frame (snaps to center); drag corners to resize"
                    >
                      <span className="absolute -top-3.5 left-0 px-1 rounded bg-cyan-400 text-black font-mono font-bold text-[8px] whitespace-nowrap pointer-events-none">
                        {activeCropField === 'keyframe_start_crop'
                          ? 'START KF'
                          : activeCropField === 'keyframe_end_crop'
                            ? 'END KF'
                            : 'FRAME CROP'}
                      </span>
                      {(['crop_nw', 'crop_ne', 'crop_sw', 'crop_se'] as const).map((hCorner) => {
                        const posCls =
                          hCorner === 'crop_nw'
                            ? '-top-1.5 -left-1.5 cursor-nwse-resize'
                            : hCorner === 'crop_ne'
                              ? '-top-1.5 -right-1.5 cursor-nesw-resize'
                              : hCorner === 'crop_sw'
                                ? '-bottom-1.5 -left-1.5 cursor-nesw-resize'
                                : '-bottom-1.5 -right-1.5 cursor-nwse-resize';
                        return (
                          <div
                            key={hCorner}
                            onPointerDown={(ev) => {
                              ev.preventDefault();
                              ev.stopPropagation();
                              frameDragRef.current = {
                                target: hCorner,
                                cropField: activeCropField,
                                startX: ev.clientX,
                                startY: ev.clientY,
                                origBox: { ...frameBoxDraft.webcam_box },
                                origCrop: { ...activeCropBox },
                                origAnchor: { ...frameBoxDraft.focus_anchor },
                                latest: { ...frameBoxDraft },
                              };
                              setDraggingFrameMode(hCorner);
                            }}
                            className={`absolute w-3 h-3 rounded-sm bg-cyan-400 border border-black ${posCls}`}
                          />
                        );
                      })}
                    </div>

                    {/* Draggable & 4-Corner Resizable Webcam Crop Box */}
                    {(isWebcamLayout || frameEditTarget === 'webcam') && (
                    <div
                      onPointerDown={(ev) => {
                        ev.preventDefault();
                        ev.stopPropagation();
                        frameDragRef.current = {
                          target: 'box_move',
                          cropField: activeCropField,
                          startX: ev.clientX,
                          startY: ev.clientY,
                          origBox: { ...frameBoxDraft.webcam_box },
                          origCrop: { ...activeCropBox },
                          origAnchor: { ...frameBoxDraft.focus_anchor },
                          latest: { ...frameBoxDraft },
                        };
                        setDraggingFrameMode('box_move');
                      }}
                      style={{
                        left: `${frameBoxDraft.webcam_box.x * 100}%`,
                        top: `${frameBoxDraft.webcam_box.y * 100}%`,
                        width: `${frameBoxDraft.webcam_box.w * 100}%`,
                        height: `${frameBoxDraft.webcam_box.h * 100}%`,
                      }}
                      className="absolute border-2 border-dashed border-emerald-400 bg-emerald-500/15 cursor-move z-15"
                      title="Drag to position Webcam Box; drag corners to resize"
                    >
                      <span className="absolute -top-3.5 left-0 px-1 rounded bg-emerald-500 text-black font-mono font-bold text-[8px] whitespace-nowrap pointer-events-none">
                        WEBCAM
                      </span>
                      {(['box_nw', 'box_ne', 'box_sw', 'box_se'] as const).map((hCorner) => {
                        const posCls =
                          hCorner === 'box_nw'
                            ? '-top-1.5 -left-1.5 cursor-nwse-resize'
                            : hCorner === 'box_ne'
                              ? '-top-1.5 -right-1.5 cursor-nesw-resize'
                              : hCorner === 'box_sw'
                                ? '-bottom-1.5 -left-1.5 cursor-nesw-resize'
                                : '-bottom-1.5 -right-1.5 cursor-nwse-resize';
                        return (
                          <div
                            key={hCorner}
                            onPointerDown={(ev) => {
                              ev.preventDefault();
                              ev.stopPropagation();
                              frameDragRef.current = {
                                target: hCorner,
                                cropField: activeCropField,
                                startX: ev.clientX,
                                startY: ev.clientY,
                                origBox: { ...frameBoxDraft.webcam_box },
                                origCrop: { ...activeCropBox },
                                origAnchor: { ...frameBoxDraft.focus_anchor },
                                latest: { ...frameBoxDraft },
                              };
                              setDraggingFrameMode(hCorner);
                            }}
                            className={`absolute w-3 h-3 rounded-sm bg-emerald-400 border border-black ${posCls}`}
                          />
                        );
                      })}
                    </div>
                    )}

                    {/* Draggable Subject Focus Crosshair */}
                    <div
                      onPointerDown={(ev) => {
                        ev.preventDefault();
                        ev.stopPropagation();
                        frameDragRef.current = {
                          target: 'anchor',
                          cropField: activeCropField,
                          startX: ev.clientX,
                          startY: ev.clientY,
                          origBox: { ...frameBoxDraft.webcam_box },
                          origCrop: { ...activeCropBox },
                          origAnchor: { ...frameBoxDraft.focus_anchor },
                          latest: { ...frameBoxDraft },
                        };
                        setDraggingFrameMode('anchor');
                      }}
                      style={{
                        left: `${frameBoxDraft.focus_anchor.x * 100}%`,
                        top: `${frameBoxDraft.focus_anchor.y * 100}%`,
                        transform: 'translate(-50%, -50%)',
                      }}
                      className="absolute w-6 h-6 rounded-full border-2 border-amber-400 bg-amber-500/25 flex items-center justify-center cursor-grab active:cursor-grabbing shadow z-20"
                      title="Drag Subject Focus Anchor crosshair"
                    >
                      <Crosshair className="w-3.5 h-3.5 text-amber-300" />
                    </div>
                  </div>
                </div>

                <div className="text-center text-[9px] text-white/80 font-mono truncate">
                  Crop: ({Math.round(activeCropBox.x * 100)}%,{Math.round(activeCropBox.y * 100)}%) {Math.round(activeCropBox.w * 100)}%×{Math.round(activeCropBox.h * 100)}% • Anchor: {Math.round(frameBoxDraft.focus_anchor.x * 100)}%,{Math.round(frameBoxDraft.focus_anchor.y * 100)}%
                </div>
              </div>
              );
            })()}

            {/* Platform Safe-Zone Overlay */}
            {safeZoneGuide !== 'No guide' && (
              <div className="absolute inset-0 pointer-events-none z-10">
                <div
                  style={{ left: '9%', right: '9%', top: '4%' }}
                  className="absolute border-t border-dashed border-[var(--accent-primary)]/60 pt-0.5 text-[9px] text-[var(--accent-primary)] font-mono text-center"
                >
                  {safeZoneGuide} Safe Top
                </div>

                {(safeZoneGuide === 'TikTok' || safeZoneGuide === 'YT Shorts' || safeZoneGuide === 'Instagram' || safeZoneGuide === 'All') && (
                  <div className="absolute right-2 bottom-14 flex flex-col items-center gap-2 text-white/75">
                    <div className="flex flex-col items-center">
                      <div className="w-6 h-6 rounded-full bg-black/45 flex items-center justify-center border border-white/20">
                        <Heart className="w-3 h-3 text-white" />
                      </div>
                      <span className="text-[7px] font-mono mt-0.5">84K</span>
                    </div>
                    <div className="flex flex-col items-center">
                      <div className="w-6 h-6 rounded-full bg-black/45 flex items-center justify-center border border-white/20">
                        <MessageCircle className="w-3 h-3 text-white" />
                      </div>
                      <span className="text-[7px] font-mono mt-0.5">1.2K</span>
                    </div>
                    <div className="flex flex-col items-center">
                      <div className="w-6 h-6 rounded-full bg-black/45 flex items-center justify-center border border-white/20">
                        <Share2 className="w-3 h-3 text-white" />
                      </div>
                      <span className="text-[7px] font-mono mt-0.5">Share</span>
                    </div>
                    <div className="w-5 h-5 rounded-full bg-black/45 flex items-center justify-center border border-white/20">
                      <Music className="w-2.5 h-2.5 text-white" />
                    </div>
                  </div>
                )}

                <div
                  style={{ left: '9%', right: '9%', bottom: '16%', height: '18%' }}
                  className="absolute rounded-lg border border-dashed border-[var(--accent-primary)]/65 bg-[var(--accent-primary)]/5 flex items-end justify-center pb-1"
                >
                  <span className="text-[9px] text-[var(--accent-primary)] font-mono tracking-tight">
                    Caption Safe Zone
                  </span>
                </div>
              </div>
            )}
          </div>
        </div>
      </div>

      {/* Integrated Frame-Accurate Transport Bar */}
      <div className="px-3 py-2 border-t border-[var(--border-subtle)] bg-[var(--bg-surface)] flex items-center gap-1.5 shrink-0">
        <button
          type="button"
          onClick={togglePlay}
          disabled={!activeVideoSrc}
          className="w-7 h-7 rounded-lg bg-[var(--bg-card)] hover:border-[var(--accent-primary)]/50 text-[var(--accent-primary)] border border-[var(--border-subtle)] flex items-center justify-center transition shrink-0 disabled:opacity-40"
          title={isPlaying ? 'Pause (Space)' : 'Play (Space)'}
        >
          {isPlaying ? <Pause className="w-3.5 h-3.5 fill-current" /> : <Play className="w-3.5 h-3.5 fill-current" />}
        </button>

        <button
          type="button"
          onClick={() => stepSingleFrame(-1)}
          disabled={!activeVideoSrc}
          className="px-1.5 h-7 rounded-lg bg-[var(--bg-card)] hover:border-[var(--accent-primary)]/50 text-[10px] font-mono font-bold text-[var(--text-secondary)] hover:text-[var(--text-primary)] border border-[var(--border-subtle)] flex items-center gap-0.5 transition shrink-0 disabled:opacity-40"
          title="Step backward 1 frame (Left Arrow)"
        >
          <StepBack className="w-2.5 h-2.5" />
          <span>-1f</span>
        </button>

        <button
          type="button"
          onClick={() => stepSingleFrame(1)}
          disabled={!activeVideoSrc}
          className="px-1.5 h-7 rounded-lg bg-[var(--bg-card)] hover:border-[var(--accent-primary)]/50 text-[10px] font-mono font-bold text-[var(--text-secondary)] hover:text-[var(--text-primary)] border border-[var(--border-subtle)] flex items-center gap-0.5 transition shrink-0 disabled:opacity-40"
          title="Step forward 1 frame (Right Arrow)"
        >
          <span>+1f</span>
          <StepForward className="w-2.5 h-2.5" />
        </button>

        <input
          type="range"
          min={0}
          max={Math.max(1, duration)}
          step={1 / previewFps}
          value={relCurrent}
          onChange={handleScrubberChange}
          disabled={!activeVideoSrc}
          className="flex-1 min-w-0 h-1 bg-[var(--bg-input)] rounded appearance-none cursor-pointer"
        />

        <span
          className="text-[10px] font-mono text-[var(--text-secondary)] shrink-0 tabular-nums"
          title={`Elapsed ${formatMMSS(relCurrent)} / ${formatMMSS(duration)} @ ${previewFps}fps`}
        >
          {formatSMPTE(relCurrent, previewFps)} <span className="opacity-60">/ {formatSMPTE(duration, previewFps)}</span>
        </span>

        <button
          type="button"
          onClick={toggleMute}
          disabled={!activeVideoSrc}
          className="w-7 h-7 rounded-lg bg-[var(--bg-card)] hover:text-[var(--text-primary)] text-[var(--text-secondary)] border border-[var(--border-subtle)] flex items-center justify-center transition shrink-0 disabled:opacity-40"
          title={isMuted ? 'Unmute (M)' : 'Mute (M)'}
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
                      className={`px-1.5 py-0.5 rounded text-[9px] font-mono border shrink-0 transition tabular-nums ${isSel
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
                      className={`absolute inset-y-0 border-x pointer-events-none ${isSel
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
      <div className="p-3 border-t border-[var(--border-subtle)] bg-[var(--bg-card)] space-y-2 shrink-0">
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
            title={isCompleted ? 'Publish rendered clip directly to YouTube & Social Studio' : 'Render clip first to publish'}
          >
            <YouTubeIcon className="w-3.5 h-3.5 text-red-500 shrink-0" />
            <span className="truncate">Export &amp; Publish</span>
          </button>

          {clip ? (
            <select
              aria-label="Export Pro NLE Format"
              defaultValue=""
              onChange={(e) => {
                const fmt = e.target.value as 'fcpxml' | 'premiere' | 'edl' | 'srt' | 'vtt' | 'ass' | 'zip';
                if (!fmt) return;
                const url = api.getNleExportUrl(clip.project_id, clip.id, fmt);
                window.open(url, '_blank');
                e.target.value = '';
              }}
              className="h-8 px-2 rounded-xl bg-[var(--bg-surface)] hover:border-[var(--accent-primary)]/50 text-[var(--text-primary)] border border-[var(--border-subtle)] font-semibold text-[11px] outline-none cursor-pointer"
            >
              <option value="" disabled>
                Pro NLE &amp; Captions ▾
              </option>
              <option value="fcpxml">DaVinci / FCPXML</option>
              <option value="premiere">Adobe Premiere .XML</option>
              <option value="edl">CMX 3600 .EDL</option>
              <option value="srt">Captions .SRT</option>
              <option value="vtt">WebVTT .VTT</option>
              <option value="ass">Styled Subtitles .ASS</option>
              <option value="zip">Full Studio .ZIP</option>
            </select>
          ) : (
            <div />
          )}
        </div>

        <button
          type="button"
          onClick={handleExportPublishPack}
          disabled={!clip || isExportingPack}
          className="w-full h-8 px-2.5 rounded-xl bg-[var(--accent-subtle)] hover:bg-[var(--accent-primary)] hover:text-[var(--accent-foreground)] text-[var(--accent-primary)] border border-[var(--accent-primary)]/40 font-bold text-[11px] flex items-center justify-center gap-1.5 transition disabled:opacity-45 disabled:cursor-not-allowed"
          title="Export 9:16 + 4:5 + 16:9 MP4s, AI Cover Thumbnail, Captions .SRT, and Social Copy .TXT in one .zip"
        >
          {isExportingPack ? (
            <Loader2 className="w-3.5 h-3.5 animate-spin shrink-0" />
          ) : (
            <Package className="w-3.5 h-3.5 shrink-0" />
          )}
          <span className="truncate">{isExportingPack ? 'Building Publish Pack...' : 'Download Multi-Platform Publish Pack (.zip)'}</span>
        </button>

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

            <div className="rounded-xl border border-[var(--border-subtle)] bg-[var(--bg-elevated)] p-3 space-y-2.5 text-xs">
              <div className="flex items-center justify-between gap-2 flex-wrap">
                {isLoadingYtStatus ? (
                  <span className="inline-flex items-center gap-1.5 text-[11px] text-[var(--text-muted)]">
                    <Loader2 className="w-3.5 h-3.5 animate-spin" />
                    Checking YouTube channel status...
                  </span>
                ) : ytStatus?.connected ? (
                  <span
                    data-testid="yt-connected-badge"
                    className="inline-flex items-center gap-1.5 px-2.5 py-1 rounded-full bg-emerald-500/15 text-emerald-400 border border-emerald-500/40 text-[11px] font-bold"
                  >
                    <CheckCircle2 className="w-3.5 h-3.5 shrink-0" />
                    <span>Connected: {ytStatus.channel_title || 'YouTube Channel'}</span>
                  </span>
                ) : (
                  <span
                    data-testid="yt-not-linked-badge"
                    className="inline-flex items-center gap-1.5 px-2.5 py-1 rounded-full bg-amber-500/15 text-amber-400 border border-amber-500/40 text-[11px] font-bold"
                  >
                    <AlertTriangle className="w-3.5 h-3.5 shrink-0" />
                    <span>YouTube Not Linked</span>
                  </span>
                )}

                <div className="flex items-center gap-1.5 flex-wrap">
                  {ytStatus?.connected ? (
                    <button
                      type="button"
                      onClick={handleDisconnectYouTube}
                      className="px-2.5 py-1 rounded-lg bg-[var(--bg-surface)] border border-[var(--border-subtle)] text-[var(--text-muted)] hover:text-red-400 hover:border-red-500/40 text-[11px] font-semibold flex items-center gap-1 transition"
                    >
                      <Unlink className="w-3 h-3" />
                      <span>Disconnect</span>
                    </button>
                  ) : (
                    <>
                      <button
                        type="button"
                        onClick={() => setShowOAuthSetup((prev) => !prev)}
                        className="px-2.5 py-1 rounded-lg bg-[var(--bg-surface)] border border-[var(--border-subtle)] text-[var(--text-primary)] hover:border-[var(--accent-primary)]/50 text-[11px] font-semibold flex items-center gap-1 transition"
                      >
                        <Upload className="w-3 h-3 text-[var(--accent-primary)]" />
                        <span>Setup Google OAuth Client JSON</span>
                        {showOAuthSetup ? <ChevronUp className="w-3 h-3" /> : <ChevronDown className="w-3 h-3" />}
                      </button>
                      <button
                        type="button"
                        onClick={handleConnectYouTube}
                        disabled={isConnectingYt}
                        className="px-3 py-1 rounded-lg bg-red-600 hover:bg-red-700 text-white text-[11px] font-bold flex items-center gap-1.5 transition disabled:opacity-50"
                      >
                        {isConnectingYt ? <Loader2 className="w-3 h-3 animate-spin" /> : <Link2 className="w-3 h-3" />}
                        <span>{isConnectingYt ? 'Waiting for Google Sign-In...' : 'Connect YouTube Channel'}</span>
                      </button>
                    </>
                  )}
                </div>
              </div>

              {!ytStatus?.connected && showOAuthSetup && (
                <div className="rounded-lg bg-[var(--bg-surface)] border border-[var(--border-subtle)] p-3 space-y-2 text-[11px] text-[var(--text-muted)]">
                  <ol className="list-decimal list-inside space-y-1 text-[var(--text-primary)]">
                    <li>Open Google Cloud Console and enable the <strong>YouTube Data API v3</strong>.</li>
                    <li>Go to <strong>Credentials -&gt; Create Credentials -&gt; OAuth client ID (Desktop app)</strong> and download the JSON.</li>
                    <li>Upload your <code>client_secret_*.json</code> file below, then click <strong>Connect YouTube Channel</strong>.</li>
                  </ol>
                  <div className="flex items-center justify-between gap-2 pt-1 flex-wrap">
                    <input
                      ref={clientSecretsInputRef}
                      type="file"
                      accept=".json,application/json"
                      onChange={handleUploadClientSecrets}
                      className="hidden"
                    />
                    <button
                      type="button"
                      onClick={() => clientSecretsInputRef.current?.click()}
                      disabled={isUploadingSecrets}
                      className="px-3 py-1.5 rounded-lg bg-[var(--accent-subtle)] border border-[var(--accent-primary)]/40 text-[var(--accent-primary)] font-bold flex items-center gap-1.5 hover:opacity-90 transition disabled:opacity-50"
                    >
                      {isUploadingSecrets ? <Loader2 className="w-3.5 h-3.5 animate-spin" /> : <Upload className="w-3.5 h-3.5" />}
                      <span>{isUploadingSecrets ? 'Saving JSON...' : 'Upload Google OAuth Client JSON'}</span>
                    </button>
                    {ytStatus?.client_configured && (
                      <span className="text-[10px] font-semibold text-emerald-400">
                        Client JSON configured
                      </span>
                    )}
                  </div>
                </div>
              )}

              {ytAuthError && (
                <div className="rounded-lg bg-red-500/10 border border-red-500/40 px-2.5 py-2 text-[11px] text-red-400">
                  {ytAuthError}
                </div>
              )}
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
                      className={`py-1 rounded-md text-[11px] font-bold transition ${activeCopyPlatform === tab.id
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

              <div className="flex items-center justify-between gap-2 flex-wrap pt-1">
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

                <div className="flex items-center gap-2 flex-wrap ml-auto">
                  <button
                    type="button"
                    onClick={() =>
                      handleCopyText('modal_copy', `${publishTitle}\n\n${publishDesc}`.trim())
                    }
                    className="px-3 py-2 rounded-xl bg-[var(--bg-elevated)] hover:border-[var(--accent-primary)]/50 text-[var(--text-primary)] border border-[var(--border-subtle)] font-semibold text-xs flex items-center gap-1.5 transition"
                  >
                    {copiedField === 'modal_copy' ? (
                      <Check className="w-3.5 h-3.5 text-emerald-400" />
                    ) : (
                      <Copy className="w-3.5 h-3.5" />
                    )}
                    <span>{copiedField === 'modal_copy' ? 'Copied' : 'Copy Title & Hashtags'}</span>
                  </button>

                  <button
                    type="button"
                    onClick={handleExportPublishPack}
                    disabled={!clip || isExportingPack}
                    className="px-3 py-2 rounded-xl bg-[var(--accent-subtle)] hover:bg-[var(--accent-primary)] hover:text-[var(--accent-foreground)] text-[var(--accent-primary)] border border-[var(--accent-primary)]/40 font-bold text-xs flex items-center gap-1.5 transition disabled:opacity-45"
                  >
                    {isExportingPack ? <Loader2 className="w-3.5 h-3.5 animate-spin" /> : <Package className="w-3.5 h-3.5" />}
                    <span>{isExportingPack ? 'Building Zip...' : 'Download Social Pack (.zip)'}</span>
                  </button>

                  <button
                    type="submit"
                    disabled={isPublishing || !ytStatus?.connected || !isCompleted}
                    title={
                      !ytStatus?.connected
                        ? 'Connect your YouTube channel above before uploading'
                        : !isCompleted
                        ? 'Render clip MP4 first'
                        : 'Upload rendered MP4 to your connected YouTube channel'
                    }
                    className={`px-4 py-2 font-bold text-xs rounded-xl transition shadow-md flex items-center gap-1.5 ${
                      ytStatus?.connected
                        ? 'bg-red-600 hover:bg-red-700 text-white disabled:opacity-50'
                        : 'bg-[var(--bg-elevated)] text-[var(--text-muted)] border border-[var(--border-subtle)] opacity-70 cursor-not-allowed'
                    }`}
                  >
                    {isPublishing ? <Loader2 className="w-4 h-4 animate-spin" /> : <YouTubeIcon className="w-4 h-4" />}
                    <span>
                      {!ytStatus?.connected
                        ? 'Connect YouTube to Upload'
                        : isPublishing
                        ? 'Uploading rendered MP4 to YouTube...'
                        : 'Upload Video'}
                    </span>
                  </button>
                </div>
              </div>

              {publishSuccess && (
                <div className="p-3 rounded-xl bg-emerald-500/10 border border-emerald-500/35 text-emerald-400 space-y-1.5 text-xs">
                  <div className="flex items-center justify-between gap-2">
                    <span className="font-bold">
                      Uploaded to {publishSuccess.channel_title || 'YouTube'} ({publishSuccess.privacy_status})
                    </span>
                    <a
                      href={publishSuccess.url}
                      target="_blank"
                      rel="noreferrer"
                      className="underline font-bold inline-flex items-center gap-1 shrink-0"
                    >
                      <span>Open on YouTube</span>
                      <ExternalLink className="w-3 h-3" />
                    </a>
                  </div>
                  <div className="break-all font-mono text-[11px]">{publishSuccess.url}</div>
                  <p className="text-[10px] text-[var(--text-muted)] leading-snug">
                    Note: Unverified Google Cloud OAuth apps upload videos as <strong>Private</strong> in YouTube Studio by default until Google app verification is approved.
                  </p>
                </div>
              )}

              {publishError && (
                <div className="p-3 rounded-xl bg-red-500/10 border border-red-500/40 text-red-400 text-xs break-words">
                  {publishError}
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

                {thumbBestFrames.length > 0 && (
                  <div className="space-y-1.5 pt-1">
                    <div className="flex items-center justify-between text-[10px] font-semibold text-[var(--text-secondary)]">
                      <span>Best Frames Strip (Click frame for cover)</span>
                      <span className="font-mono text-[var(--accent-primary)]">
                        {thumbUsePlayhead
                          ? `Playhead ${relCurrent.toFixed(1)}s`
                          : thumbCustomTimestamp !== null
                            ? `${thumbCustomTimestamp.toFixed(2)}s`
                            : 'Auto Peak'}
                      </span>
                    </div>
                    <div className="grid grid-cols-3 gap-1.5">
                      {thumbBestFrames.slice(0, 6).map((bf) => {
                        const isSel =
                          !thumbUsePlayhead &&
                          thumbCustomTimestamp !== null &&
                          Math.abs(thumbCustomTimestamp - bf.t) < 0.05;
                        return (
                          <button
                            key={bf.index}
                            type="button"
                            onClick={() => {
                              setThumbUsePlayhead(false);
                              setThumbCustomTimestamp(bf.t);
                            }}
                            className={`rounded-lg overflow-hidden border text-left transition bg-[var(--bg-input)] ${isSel
                              ? 'border-[var(--accent-primary)] ring-1 ring-[var(--accent-primary)]'
                              : 'border-[var(--border-subtle)] hover:border-[var(--accent-primary)]/50'
                              }`}
                          >
                            {bf.image_url && (
                              <img
                                src={bf.image_url}
                                alt={bf.role}
                                className="w-full h-11 object-cover bg-black"
                              />
                            )}
                            <div className="px-1.5 py-1 text-[9px] font-semibold truncate text-[var(--text-primary)]">
                              {bf.role.replace(/_/g, ' ')}
                            </div>
                          </button>
                        );
                      })}
                    </div>
                  </div>
                )}

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
                        timestamp: thumbUsePlayhead ? masterT : (thumbCustomTimestamp ?? undefined),
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
