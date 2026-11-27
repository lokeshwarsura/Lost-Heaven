import React, { useState, useEffect, useRef } from 'react';
import { TopBar } from './components/TopBar';
import { ClipFeed } from './components/ClipFeed';
import { SmartphonePreview } from './components/SmartphonePreview';
import { Step1Setup } from './components/Step1Setup';
import { Step2Captions } from './components/Step2Captions';
import { Step3Video } from './components/Step3Video';
import { BottomTelemetryBar } from './components/BottomTelemetryBar';
import { ViewProjects } from './components/ViewProjects';
import { ViewSettings } from './components/ViewSettings';
import { HomeView } from './components/HomeView';
import { ProgressView } from './components/ProgressView';
import { ErrorBoundary } from './components/ErrorBoundary';
import { FirstTimeSetupWizard } from './components/FirstTimeSetupWizard';
import { AnalysisInspectorModal } from './components/AnalysisInspectorModal';
import { AIEngineModal } from './components/AIEngineModal';
import {
  Project,
  ViralClip,
  CaptionStylePreset,
  StudioProfile,
  SystemInfo,
  StudioSettings,
  BestFrameItem
} from './types';
import { api } from './api/client';
import { applyTheme, getActiveTheme } from './themes/themes';
import { useEditorHistory, extractEditableSnapshot } from './hooks/useEditorHistory';
import {
  Home,
  Activity,
  Sliders,
  Folder,
  Settings as SettingsIcon,
  Zap,
  Palette,
  Search,
  Scissors,
  X,
  Keyboard
} from 'lucide-react';

export const App: React.FC = () => {
  const [activeView, setActiveView] = useState<'home' | 'progress' | 'editor' | 'projects' | 'settings'>('home');
  const [activeProjectId, setActiveProjectId] = useState<number | null>(null);
  const [progressTargetId, setProgressTargetId] = useState<number | null>(null);
  const [previewSeekTime, setPreviewSeekTime] = useState<number | undefined>(undefined);
  const [currentPlaybackTime, setCurrentPlaybackTime] = useState<number | undefined>(undefined);

  // Studio Projects & Clips State
  const [projects, setProjects] = useState<Project[]>([]);
  const [currentProject, setCurrentProject] = useState<Project | null>(null);
  const [clips, setClips] = useState<ViralClip[]>([]);
  const [selectedClip, setSelectedClip] = useState<ViralClip | null>(null);

  // Strict 3-Step Editor State: 1: Setup & Framing, 2: Captions & Voice, 3: Color & Video
  const [activeStep, setActiveStep] = useState<1 | 2 | 3>(1);

  // Styles, Fonts & Telemetry
  const [captionStyles, setCaptionStyles] = useState<CaptionStylePreset[]>([]);
  const [availableFonts, setAvailableFonts] = useState<string[]>([]);
  const [profiles, setProfiles] = useState<StudioProfile[]>([]);
  const [systemInfo, setSystemInfo] = useState<SystemInfo | null>(null);
  const [studioSettings, setStudioSettings] = useState<Partial<StudioSettings> | null>(null);

  // Transcript Search & Manual Clip Creator Modal
  const [isTranscriptModalOpen, setIsTranscriptModalOpen] = useState(false);
  const [isAnalysisModalOpen, setIsAnalysisModalOpen] = useState(false);
  const [thumbRequest, setThumbRequest] = useState<{ timestamp: number; seq: number } | null>(null);
  const [transcriptQuery, setTranscriptQuery] = useState('');
  const [transcriptMatches, setTranscriptMatches] = useState<Array<{ start: number; end: number; text: string }>>([]);
  const [manualStart, setManualStart] = useState('0');
  const [manualEnd, setManualEnd] = useState('15');
  const [manualTitle, setManualTitle] = useState('');
  const [isCreatingManual, setIsCreatingManual] = useState(false);
  const [isShortcutsModalOpen, setIsShortcutsModalOpen] = useState(false);
  const [isEngineModalOpen, setIsEngineModalOpen] = useState(false);

  const {
    canUndo,
    canRedo,
    pushSnapshot,
    undo: popUndoSnapshot,
    redo: popRedoSnapshot
  } = useEditorHistory(selectedClip?.id ?? null);

  // Platform Safe-Zone Guide & Color Grade Compare Hold
  const [safeZoneGuide, setSafeZoneGuide] = useState<string>('TikTok');
  const [compareOriginal, setCompareOriginal] = useState<boolean>(false);

  useEffect(() => {
    if (!compareOriginal) return;
    const release = () => setCompareOriginal(false);
    window.addEventListener('pointerup', release);
    window.addEventListener('blur', release);
    return () => {
      window.removeEventListener('pointerup', release);
      window.removeEventListener('blur', release);
    };
  }, [compareOriginal]);

  // Modals, Wizard, Toast & Loading states
  const [isWizardOpen, setIsWizardOpen] = useState(false);
  const [toast, setToast] = useState<{ type: 'error' | 'success' | 'info'; message: string } | null>(null);
  const [isUploading, setIsUploading] = useState(false);
  const [isRendering, setIsRendering] = useState(false);
  const [isMerging, setIsMerging] = useState(false);
  const [isCompilingTrailer, setIsCompilingTrailer] = useState(false);
  const [isAutoFramingAll, setIsAutoFramingAll] = useState(false);
  const pendingClipPatchRef = useRef<Promise<any> | null>(null);

  const showToast = (message: string, type: 'error' | 'success' | 'info' = 'info') => {
    setToast({ type, message });
    setTimeout(() => {
      setToast((prev) => (prev?.message === message ? null : prev));
    }, 4500);
  };

  useEffect(() => {
    const savedTheme = getActiveTheme();
    applyTheme(savedTheme.id);
    loadInitialData();

    const handleSettingsUpdated = (evt: Event) => {
      const detail = (evt as CustomEvent<Partial<StudioSettings>>)?.detail;
      if (detail && typeof detail === 'object') {
        setStudioSettings((prev) => ({ ...(prev || {}), ...detail }));
      } else {
        api.getSystemSettings().then((s) => setStudioSettings(s)).catch(() => {});
      }
    };
    window.addEventListener('lh-settings-updated', handleSettingsUpdated);
    return () => window.removeEventListener('lh-settings-updated', handleSettingsUpdated);
  }, []);

  // Refresh telemetry & settings whenever switching back to editor/home
  useEffect(() => {
    if (activeView === 'editor' || activeView === 'home') {
      Promise.all([
        api.getSystemInfo().catch(() => null),
        api.getSystemSettings().catch(() => null)
      ]).then(([sys, sysSet]) => {
        if (sys) setSystemInfo(sys);
        if (sysSet) setStudioSettings(sysSet);
      });
    }
  }, [activeView]);

  const loadInitialData = async () => {
    try {
      const [sys, sysSet, styles, fonts, projsData, profsData] = await Promise.all([
        api.getSystemInfo().catch(() => null),
        api.getSystemSettings().catch(() => null),
        api.getCaptionStyles().catch(() => []),
        api.getFonts().catch(() => ['Montserrat', 'Impact', 'Anton', 'Poppins', 'Playfair Display']),
        api.getProjectsList().catch(() => ({ projects: [] })),
        api.getProfiles().catch(() => [])
      ]);

      if (sys) setSystemInfo(sys);
      if (sysSet) setStudioSettings(sysSet);
      if (styles?.length) setCaptionStyles(styles);
      if (fonts?.length) setAvailableFonts(fonts);
      if (profsData?.length) setProfiles(profsData);

      const projs = projsData?.projects || [];
      if (projs.length > 0) {
        setProjects(projs);
        selectProject(projs[0].id);
      } else if (
        !localStorage.getItem('lost_heaven_onboarded') &&
        !localStorage.getItem('clipship_onboarded')
      ) {
        setIsWizardOpen(true);
      }
    } catch (err) {
      console.error('Failed to load initial data:', err);
    }
  };

  const selectProject = async (projectId: number) => {
    try {
      const data = await api.getProject(projectId);
      setCurrentProject(data.project);
      setActiveProjectId(projectId);
      setClips(data.clips);
      if (data.clips.length > 0) {
        setSelectedClip(data.clips[0]);
      } else {
        setSelectedClip(null);
      }
    } catch (err) {
      console.error('Failed to select project:', err);
    }
  };

  const handleUpdateProject = async (updates: Partial<Project>) => {
    if (!currentProject) return;
    const updated = { ...currentProject, ...updates };
    setCurrentProject(updated);
    setProjects(projects.map((p) => (p.id === updated.id ? updated : p)));
    try {
      await api.updateProject(currentProject.id, updates);
    } catch (err) {
      console.error('Failed to update project:', err);
    }
  };

  // Periodic polling for background pipeline analysis & renders
  useEffect(() => {
    const interval = setInterval(async () => {
      if (currentProject) {
        const needsProjectPoll = ['created', 'proxy_ready', 'analyzing', 'processing'].includes(
          currentProject.status
        );
        const hasRenderingClip = clips.some((c) => c.render_status === 'rendering');

        if (needsProjectPoll || hasRenderingClip) {
          try {
            const data = await api.getProject(currentProject.id);
            setCurrentProject(data.project);

            const updatedClips = data.clips.map((c) => {
              const isReady = c.status === 'ready' || c.render_status === 'completed';
              const nextStatus: ViralClip['status'] = isReady
                ? 'ready'
                : c.render_status === 'rendering' || c.status === 'rendering'
                ? 'rendering'
                : c.render_status === 'failed' || c.status === 'failed'
                ? 'failed'
                : 'draft';
              const baseUrl = c.video_url || c.preview_url;
              const cacheBustedUrl =
                isReady && baseUrl && !baseUrl.includes('?v=') && !baseUrl.includes('?t=')
                  ? `${baseUrl}?v=${Date.now()}`
                  : baseUrl;
              return {
                ...c,
                status: nextStatus,
                preview_url: cacheBustedUrl,
                video_url: cacheBustedUrl
              };
            });

            setClips(updatedClips);
            if (selectedClip) {
              const updated = updatedClips.find((c) => c.id === selectedClip.id);
              if (updated) setSelectedClip(updated);
            }
          } catch {
            // ignore background poll error
          }
        }
      }
    }, 2000);

    return () => clearInterval(interval);
  }, [currentProject?.id, currentProject?.status, clips, selectedClip?.id]);

  // Patch active clip updates with per-clip Undo/Redo snapshot recording
  const handleUpdateClip = async (
    updates: Partial<ViralClip>,
    options?: { skipHistory?: boolean }
  ) => {
    if (!selectedClip) return;
    const clipId = selectedClip.id;
    const editableKeys = new Set(Object.keys(extractEditableSnapshot(selectedClip)));
    const hasEditableChange = Object.keys(updates).some((k) => editableKeys.has(k));
    if (!options?.skipHistory && hasEditableChange) {
      pushSnapshot(clipId, selectedClip);
    }
    const updated = { ...selectedClip, ...updates };
    setSelectedClip(updated);
    setClips((prev) => prev.map((c) => (c.id === clipId ? { ...c, ...updates } : c)));

    const patchPromise = api.updateClip(clipId, updates);
    pendingClipPatchRef.current = patchPromise;
    try {
      await patchPromise;
    } catch (err) {
      console.error('Failed to update clip:', err);
    }
  };

  const handleUndo = async () => {
    if (!selectedClip) return;
    const prevSnap = popUndoSnapshot(selectedClip.id, selectedClip);
    if (!prevSnap) return;
    await handleUpdateClip(prevSnap, { skipHistory: true });
    showToast('Undid last clip edit', 'info');
  };

  const handleRedo = async () => {
    if (!selectedClip) return;
    const nextSnap = popRedoSnapshot(selectedClip.id, selectedClip);
    if (!nextSnap) return;
    await handleUpdateClip(nextSnap, { skipHistory: true });
    showToast('Redid clip edit', 'info');
  };

  const shortcutActionsRef = useRef({ handleUndo, handleRedo });
  shortcutActionsRef.current = { handleUndo, handleRedo };

  useEffect(() => {
    const onKeyDown = (e: KeyboardEvent) => {
      if (activeView !== 'editor') return;
      const target = e.target as HTMLElement | null;
      const tag = target?.tagName?.toLowerCase();
      if (
        tag === 'input' ||
        tag === 'textarea' ||
        tag === 'select' ||
        target?.isContentEditable
      ) {
        return;
      }

      const isMod = e.ctrlKey || e.metaKey;
      const keyLower = e.key.toLowerCase();

      if (isMod && keyLower === 'z' && !e.shiftKey) {
        e.preventDefault();
        void shortcutActionsRef.current.handleUndo();
        return;
      }
      if (isMod && (keyLower === 'y' || (keyLower === 'z' && e.shiftKey))) {
        e.preventDefault();
        void shortcutActionsRef.current.handleRedo();
        return;
      }
      if (isMod || e.altKey) return;

      if (e.key === '?') {
        e.preventDefault();
        setIsShortcutsModalOpen((v) => !v);
      } else if (e.key === 'Escape') {
        setIsShortcutsModalOpen(false);
      } else if (e.code === 'Space') {
        e.preventDefault();
        window.dispatchEvent(new CustomEvent('lh-player-cmd', { detail: { type: 'togglePlay' } }));
      } else if (e.key === 'ArrowLeft') {
        e.preventDefault();
        window.dispatchEvent(
          new CustomEvent('lh-player-cmd', { detail: { type: 'seekDelta', delta: e.shiftKey ? -5 : -1 } })
        );
      } else if (e.key === 'ArrowRight') {
        e.preventDefault();
        window.dispatchEvent(
          new CustomEvent('lh-player-cmd', { detail: { type: 'seekDelta', delta: e.shiftKey ? 5 : 1 } })
        );
      } else if (keyLower === 'i') {
        e.preventDefault();
        window.dispatchEvent(new CustomEvent('lh-player-cmd', { detail: { type: 'trimIn' } }));
      } else if (keyLower === 'o') {
        e.preventDefault();
        window.dispatchEvent(new CustomEvent('lh-player-cmd', { detail: { type: 'trimOut' } }));
      } else if (keyLower === 'b') {
        e.preventDefault();
        window.dispatchEvent(new CustomEvent('lh-player-cmd', { detail: { type: 'bladeSplit' } }));
      } else if (keyLower === 'm') {
        e.preventDefault();
        window.dispatchEvent(new CustomEvent('lh-player-cmd', { detail: { type: 'toggleMute' } }));
      }
    };
    const onExternalToast = (ev: Event) => {
      const detail = (ev as CustomEvent<{ message?: string; type?: 'success' | 'error' | 'info' }>).detail;
      if (detail?.message) {
        showToast(detail.message, detail.type || 'info');
      }
    };
    window.addEventListener('keydown', onKeyDown);
    window.addEventListener('lh-toast', onExternalToast);
    return () => {
      window.removeEventListener('keydown', onKeyDown);
      window.removeEventListener('lh-toast', onExternalToast);
    };
  }, [activeView]);

  // Fast cancellable auto-render invoked by SmartphonePreview after 900ms debounce
  const handleAutoRenderClip = async (
    projectId: number,
    clipId: number,
    signal: AbortSignal
  ) => {
    if (pendingClipPatchRef.current) {
      await pendingClipPatchRef.current.catch(() => {});
    }
    return api.triggerRender(clipId, 'fast_preview', signal, projectId);
  };

  const handleAutoRenderComplete = (clipId: number, nextVideoUrl: string, renderedPath?: string) => {
    const readyPatch: Partial<ViralClip> = {
      status: 'ready',
      render_status: 'completed',
      render_progress: 100,
      preview_url: nextVideoUrl,
      video_url: nextVideoUrl,
      ...(renderedPath ? { rendered_path: renderedPath, rendered_video_path: renderedPath } : {})
    };
    setClips((prev) =>
      prev.map((c) => (c.id === clipId ? { ...c, ...readyPatch } : c))
    );
    setSelectedClip((prev) =>
      prev && prev.id === clipId ? { ...prev, ...readyPatch } : prev
    );
  };

  const handleTrimClip = async (clipId: number, startTime: number, endTime: number) => {
    const clipObj = clips.find((c) => c.id === clipId) || selectedClip;
    const pId = clipObj?.project_id || currentProject?.id;
    if (!pId) return;
    if (clipObj) {
      pushSnapshot(clipObj.id, clipObj);
    }
    try {
      const res = await api.trimClip(pId, clipId, startTime, endTime);
      if (res?.clip) {
        setClips((prev) => prev.map((c) => (c.id === clipId ? { ...c, ...res.clip } : c)));
        setSelectedClip((prev) => (prev && prev.id === clipId ? { ...prev, ...res.clip } : prev));
        showToast(`Trimmed clip to ${res.clip.duration.toFixed(1)}s`, 'success');
      }
    } catch (err: any) {
      console.error('Trim clip failed:', err);
      showToast(err?.message || 'Failed to trim clip', 'error');
    }
  };

  const handleUpdateSegments = async (clipId: number, keepSegments: [number, number][]) => {
    const clipObj = clips.find((c) => c.id === clipId) || selectedClip;
    const pId = clipObj?.project_id || currentProject?.id;
    if (!pId) return;
    if (clipObj) {
      pushSnapshot(clipObj.id, clipObj);
    }
    try {
      const res = await api.updateClipSegments(pId, clipId, keepSegments);
      if (res?.clip) {
        setClips((prev) => prev.map((c) => (c.id === clipId ? { ...c, ...res.clip } : c)));
        setSelectedClip((prev) => (prev && prev.id === clipId ? { ...prev, ...res.clip } : prev));
        showToast(
          `Updated timeline (${keepSegments.length} seg${keepSegments.length === 1 ? '' : 's'}, ${res.clip.duration.toFixed(1)}s)`,
          'success'
        );
      }
    } catch (err: any) {
      console.error('Update segments failed:', err);
      showToast(err?.message || 'Failed to update clip segments', 'error');
    }
  };

  const handleApplyFixClip = async (clipId: number, updates: Partial<ViralClip>) => {
    if (selectedClip?.id === clipId) {
      await handleUpdateClip(updates);
      showToast('Applied 1-click retention fix', 'success');
      return;
    }
    const clipObj = clips.find((c) => c.id === clipId);
    if (clipObj) {
      pushSnapshot(clipObj.id, clipObj);
      setClips((prev) => prev.map((c) => (c.id === clipId ? { ...c, ...updates } : c)));
    }
    try {
      await api.updateClip(clipId, updates);
      showToast('Applied 1-click retention fix', 'success');
    } catch (err: any) {
      showToast(err?.message || 'Failed to apply retention fix', 'error');
    }
  };

  const handleSeekMasterTime = (masterTime: number) => {
    if (!selectedClip) return;
    const segs =
      Array.isArray(selectedClip.keep_segments) && selectedClip.keep_segments.length > 0
        ? selectedClip.keep_segments
        : [[Number(selectedClip.start_time || 0), Number(selectedClip.end_time || 30)]];
    const xfade = Number(selectedClip.merge_crossfade_sec ?? 0.03);
    let outStart = 0;
    let prevDur = 0;
    let relT = Math.max(0, masterTime - Number(selectedClip.start_time || 0));
    for (let i = 0; i < segs.length; i++) {
      const [s, e] = segs[i];
      const segDur = Math.max(0.05, Number(e) - Number(s));
      const effX = i > 0 ? Math.max(0, Math.min(xfade, prevDur * 0.35, segDur * 0.35)) : 0;
      const segOutStart = i === 0 ? 0 : Math.max(0, outStart - effX);
      if (masterTime >= Number(s) && masterTime <= Number(e)) {
        relT = segOutStart + (masterTime - Number(s));
        break;
      }
      outStart = segOutStart + segDur;
      prevDur = segDur;
    }
    setPreviewSeekTime(Math.max(0, relT) + (Date.now() % 1000) * 1e-6);
  };

  const handleSnapClipStartFrame = async (frame: BestFrameItem) => {
    if (!selectedClip) return;
    const nextStart = Number(frame.t.toFixed(2));
    const safeEnd = Math.max(nextStart + 0.8, Number(selectedClip.end_time || nextStart + 5));
    await handleTrimClip(selectedClip.id, nextStart, safeEnd);
    setPreviewSeekTime(0.0001);
  };

  const handleCalibrateCameraFrame = async (frame: BestFrameItem) => {
    if (!selectedClip) return;
    const pId = currentProject?.id || selectedClip.project_id;
    try {
      const fmap = await api.getFramingMap(pId, selectedClip.id, false);
      const kfs = fmap?.visual_map?.keyframes || [];
      const shots = fmap?.visual_map?.shots || [];
      const nearestKf = kfs.length
        ? kfs.reduce((best, cur) =>
            Math.abs(Number(cur.t || 0) - Number(frame.rel_t || 0)) <
            Math.abs(Number(best.t || 0) - Number(frame.rel_t || 0))
              ? cur
              : best
          )
        : shots.length
          ? shots.reduce((best, cur) => {
              const bMid = 0.5 * (Number(best.start_t || 0) + Number(best.end_t || 0));
              const cMid = 0.5 * (Number(cur.start_t || 0) + Number(cur.end_t || 0));
              return Math.abs(cMid - Number(frame.rel_t || 0)) < Math.abs(bMid - Number(frame.rel_t || 0))
                ? cur
                : best;
            })
          : null;
      const anchor = {
        x: Math.max(0.08, Math.min(0.92, Number(nearestKf?.cx ?? 0.5))),
        y: Math.max(0.08, Math.min(0.92, Number(nearestKf?.cy ?? 0.4))),
      };
      const cam = fmap?.source_webcam_region || fmap?.visual_map?.webcam;
      const webcamBox =
        cam && Number(cam.w) > 0.04 && Number(cam.h) > 0.04
          ? { x: Number(cam.x), y: Number(cam.y), w: Number(cam.w), h: Number(cam.h) }
          : undefined;

      const ovRes = await api.updateFramingOverride(pId, selectedClip.id, {
        focus_anchor: anchor,
        ...(webcamBox ? { webcam_box: webcamBox } : {}),
        lock_camera: true,
      });
      await handleUpdateClip({
        framing_override: ovRes.framing_override,
        source_webcam_region: ovRes.source_webcam_region,
      });
      handleSeekMasterTime(frame.t);
      showToast(
        `Calibrated framing anchor (${Math.round(anchor.x * 100)}%, ${Math.round(anchor.y * 100)}%) from ${frame.role.replace(/_/g, ' ')}`,
        'success'
      );
    } catch (err: any) {
      showToast(err?.message || 'Camera calibration failed', 'error');
    }
  };

  const handleSetThumbnailCoverFrame = (frame: BestFrameItem) => {
    setThumbRequest({ timestamp: frame.t, seq: Date.now() });
    setIsAnalysisModalOpen(false);
    handleSeekMasterTime(frame.t);
  };

  const handleMergeClips = async (clipIds: number[], smoothTransition: boolean, title?: string) => {
    const pId = currentProject?.id || selectedClip?.project_id;
    if (!pId || clipIds.length < 2) return;
    try {
      setIsMerging(true);
      const res = await api.mergeClips(pId, {
        clip_ids: clipIds,
        smooth_transition: smoothTransition,
        transition_duration: smoothTransition ? 0.35 : 0.02,
        title
      });
      if (res?.clip) {
        setClips((prev) => [res.clip, ...prev]);
        setSelectedClip(res.clip);
        setPreviewSeekTime(undefined);
        setCurrentPlaybackTime(undefined);
        showToast(`Merged ${clipIds.length} clips (${res.clip.duration.toFixed(1)}s)`, 'success');
      }
    } catch (err: any) {
      console.error('Merge clips failed:', err);
      showToast(err?.message || 'Failed to merge clips', 'error');
    } finally {
      setIsMerging(false);
    }
  };

  const handleCompileTrailer = async () => {
    const pId = currentProject?.id || selectedClip?.project_id;
    if (!pId) return;
    try {
      setIsCompilingTrailer(true);
      const res = await api.compileTrailer(pId);
      if (res?.clip) {
        setClips((prev) => [res.clip, ...prev]);
        setSelectedClip(res.clip);
        setPreviewSeekTime(undefined);
        setCurrentPlaybackTime(undefined);
        showToast(`Compiled AI Supercut Trailer (${res.clip.duration.toFixed(1)}s)`, 'success');
      }
    } catch (err: any) {
      console.error('Trailer compilation failed:', err);
      showToast(err?.message || 'Failed to compile trailer', 'error');
    } finally {
      setIsCompilingTrailer(false);
    }
  };

  const handleAutoFrameAll = async () => {
    const pId = currentProject?.id || selectedClip?.project_id;
    if (!pId || isAutoFramingAll) return;
    try {
      setIsAutoFramingAll(true);
      const res = await api.autoFrameAll(pId);
      if (Array.isArray(res?.clips)) {
        setClips(res.clips);
        if (selectedClip) {
          const updatedSelected = res.clips.find((c) => c.id === selectedClip.id);
          if (updatedSelected) setSelectedClip(updatedSelected);
        }
        showToast(`Smart Auto-Frame applied across ${res.updated_count} clips`, 'success');
      }
    } catch (err: any) {
      console.error('Smart Auto-Frame failed:', err);
      showToast(err?.message || 'Failed to auto-frame clips', 'error');
    } finally {
      setIsAutoFramingAll(false);
    }
  };

  const handleApplyProfile = (profile: StudioProfile) => {
    if (!selectedClip) return;
    handleUpdateClip(profile.settings);
    showToast(`Applied Studio Profile "${profile.name}"`, 'success');
  };

  const handleSaveProfile = async (name: string) => {
    if (!selectedClip) return;
    try {
      const saved = await api.createProfile({
        name,
        description: 'Custom saved studio profile',
        settings: {
          aspect_ratio: selectedClip.aspect_ratio,
          frame_layout: selectedClip.frame_layout,
          caption_style: selectedClip.caption_style,
          caption_font: selectedClip.caption_font,
          font_size_percent: selectedClip.font_size_percent,
          all_caps: selectedClip.all_caps,
          base_color: selectedClip.base_color,
          active_color: selectedClip.active_color,
          highlight_shape: selectedClip.highlight_shape,
          tighten_pacing: selectedClip.tighten_pacing,
          silence_threshold: selectedClip.silence_threshold,
          clean_filler_words: selectedClip.clean_filler_words,
          censor_profanity: selectedClip.censor_profanity,
          dynamic_punch_zoom: selectedClip.dynamic_punch_zoom,
          punch_zoom_intensity: selectedClip.punch_zoom_intensity,
          voice_isolation: selectedClip.voice_isolation,
          auto_sfx: selectedClip.auto_sfx,
          retention_bar_enabled: selectedClip.retention_bar_enabled,
          retention_bar_color: selectedClip.retention_bar_color,
          color_preset: selectedClip.color_preset
        }
      });
      if (saved?.id) {
        const refreshed = await api.getProfiles();
        setProfiles(refreshed);
        showToast(`Saved Studio Profile "${name}"`, 'success');
      }
    } catch {
      showToast('Failed to save profile', 'error');
    }
  };

  const handleOpenTranscriptSearch = async () => {
    if (!currentProject) return;
    setIsTranscriptModalOpen(true);
    try {
      const res = await api.searchProjectTranscript(currentProject.id, transcriptQuery);
      setTranscriptMatches(res?.matches || []);
    } catch {
      setTranscriptMatches([]);
    }
  };

  const handleCreateManualClip = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!currentProject) return;
    try {
      setIsCreatingManual(true);
      const res = await api.createManualClip(currentProject.id, {
        start_time: parseFloat(manualStart) || 0,
        end_time: parseFloat(manualEnd) || 15,
        title: manualTitle.trim() || undefined
      });
      if (res?.clip) {
        setClips((prev) => [res.clip, ...prev]);
        setSelectedClip(res.clip);
        setIsTranscriptModalOpen(false);
        showToast(`Created manual clip (${res.clip.duration.toFixed(1)}s)`, 'success');
      }
    } catch (err: any) {
      showToast(err?.message || 'Failed to create manual clip', 'error');
    } finally {
      setIsCreatingManual(false);
    }
  };

  // File Upload Ingestion
  const handleUploadVideo = async (file: File) => {
    setIsUploading(true);
    try {
      const res = await api.uploadVideo(
        file,
        selectedClip?.aspect_ratio || '9:16',
        'Auto',
        5,
        undefined,
        selectedClip?.caption_language || 'As spoken',
        selectedClip?.hardware_mode || 'gpu'
      );
      await loadInitialData();
      if (res && res.project_id) {
        setActiveProjectId(res.project_id);
        setActiveView('progress');
      }
    } catch (err: any) {
      console.error('Upload failed:', err);
      showToast(`Video upload failed: ${err.message || err}`, 'error');
    } finally {
      setIsUploading(false);
    }
  };

  // YouTube Import Ingestion
  const handleImportYouTube = async (url: string) => {
    setIsUploading(true);
    try {
      const res = await api.importYouTube(
        url,
        selectedClip?.aspect_ratio || '9:16',
        'Auto',
        5,
        undefined,
        selectedClip?.caption_language || 'As spoken',
        selectedClip?.hardware_mode || 'gpu'
      );
      await loadInitialData();
      if (res && res.project_id) {
        setActiveProjectId(res.project_id);
        setActiveView('progress');
      }
    } catch (err: any) {
      console.error('YouTube import failed:', err);
      showToast(`YouTube import failed: ${err.message || err}`, 'error');
    } finally {
      setIsUploading(false);
    }
  };

  // Save Quick Caption Style into SQLite & local gallery
  const handleSaveQuickStyle = async (name: string) => {
    if (!selectedClip) return;
    try {
      await api.createCaptionStyle({
        name,
        font_family: selectedClip.caption_font,
        font_size_percent: selectedClip.font_size_percent,
        bold: selectedClip.bold,
        italic: selectedClip.italic,
        uppercase: selectedClip.all_caps,
        text_align: selectedClip.text_align,
        base_color: selectedClip.base_color,
        active_color: selectedClip.active_color,
        hold_back_alpha: selectedClip.hold_back_alpha,
        highlight_shape: selectedClip.highlight_shape,
        animation_type: selectedClip.caption_animation,
        position_grid: selectedClip.position_grid,
        position_x: selectedClip.position_x,
        position_y: selectedClip.position_y,
        outline_width: selectedClip.outline_width,
        shadow_depth: selectedClip.shadow_depth,
        glow_intensity: selectedClip.glow_intensity,
        letter_spacing: selectedClip.letter_spacing,
        timing_offset: selectedClip.timing_offset,
        emoji_enabled: selectedClip.emoji_enabled,
        how_phrase_arrives: selectedClip.how_phrase_arrives
      });
      const freshStyles = await api.getCaptionStyles();
      setCaptionStyles(freshStyles);
      showToast(`Saved caption style "${name}"`, 'success');
    } catch (err) {
      console.error('Failed to save caption style:', err);
      showToast('Failed to save caption style', 'error');
    }
  };

  // Upload Custom Font
  const handleUploadFont = async (file: File) => {
    try {
      const res = await api.uploadFont(file);
      const freshFonts = await api.getFonts();
      setAvailableFonts(freshFonts);
      handleUpdateClip({ caption_font: res.font_name });
      showToast(`Uploaded font "${res.font_name}"`, 'success');
    } catch (err) {
      console.error('Failed to upload font:', err);
      showToast('Failed to upload font', 'error');
    }
  };

  // Trigger Master Video Rendering
  const handleGenerateClips = async () => {
    if (!selectedClip) return;
    setIsRendering(true);
    handleUpdateClip({ status: 'rendering', render_status: 'rendering', render_progress: 5 });

    try {
      await api.triggerRender(selectedClip.id, 'final_hq', undefined, selectedClip.project_id);
    } catch (err) {
      console.error('Failed to trigger render:', err);
      handleUpdateClip({ status: 'failed', render_status: 'failed', render_message: 'Trigger failed' });
      showToast('Failed to start render job', 'error');
    } finally {
      setIsRendering(false);
    }
  };

  const navItems: {
    id: 'home' | 'editor' | 'progress' | 'projects' | 'settings';
    label: string;
    icon: React.ComponentType<{ className?: string }>;
    badge?: boolean;
  }[] = [
    { id: 'home', label: 'Home / Ingestion', icon: Home },
    { id: 'editor', label: 'Studio Editor', icon: Sliders },
    {
      id: 'progress',
      label: 'Pipeline Progress',
      icon: Activity,
      badge: Boolean(
        currentProject &&
          ['created', 'proxy_ready', 'analyzing', 'processing'].includes(currentProject.status)
      )
    },
    { id: 'projects', label: 'My clips', icon: Folder },
    { id: 'settings', label: 'Settings & Diagnostics', icon: SettingsIcon }
  ];

  const isOfflineMultimodal = Boolean(
    studioSettings &&
      (studioSettings.ai_provider === 'offline' ||
        studioSettings.gemini_boost_enabled === false ||
        studioSettings.gemini_configured === false)
  );

  return (
    <div className="h-screen w-screen overflow-hidden flex bg-[var(--bg-canvas)] text-[var(--text-primary)] select-none transition-colors">
      {/* ============================================================== */}
      {/* 1. LEFT NAVIGATION RAIL (Fixed 56px, strict hover tooltips)    */}
      {/* ============================================================== */}
      <aside className="w-14 shrink-0 h-full flex flex-col items-center justify-between py-3 bg-[var(--bg-surface)] border-r border-[var(--border-subtle)] z-50">
        <div className="flex flex-col items-center gap-4">
          <button
            type="button"
            onClick={() => setActiveView('home')}
            className="w-9 h-9 rounded-xl bg-[var(--accent-primary)] text-[var(--accent-foreground)] flex items-center justify-center font-black text-xs tracking-tighter shadow-md hover:scale-105 active:scale-95 transition-transform"
            title="Lost Heaven (Home)"
          >
            LH
          </button>

          <nav className="flex flex-col items-center gap-1.5">
            {navItems.map((item) => {
              const Icon = item.icon;
              const isActive = activeView === item.id;
              return (
                <button
                  key={item.id}
                  type="button"
                  onClick={() => {
                    if (item.id === 'progress') {
                      setProgressTargetId(null);
                    }
                    setActiveView(item.id);
                  }}
                  className={`w-10 h-10 rounded-xl flex items-center justify-center transition-colors group relative ${
                    isActive
                      ? 'bg-[var(--bg-card)] text-[var(--accent-primary)] border border-[var(--accent-primary)]/50 shadow-sm'
                      : 'text-[var(--text-secondary)] hover:text-[var(--text-primary)] hover:bg-[var(--bg-card)]'
                  }`}
                >
                  <Icon className="w-4 h-4" />
                  {item.badge && (
                    <span className="absolute top-1.5 right-1.5 w-2 h-2 rounded-full bg-[var(--accent-primary)] animate-pulse" />
                  )}
                  <span className="absolute left-12 bg-[var(--bg-card)] text-[var(--text-primary)] text-[11px] font-semibold px-2.5 py-1 rounded-md border border-[var(--border-subtle)] shadow-lg invisible opacity-0 group-hover:visible group-hover:opacity-100 pointer-events-none transition-opacity whitespace-nowrap z-50">
                    {item.label}
                  </span>
                </button>
              );
            })}
          </nav>
        </div>

        {/* Bottom Rail Indicators */}
        <div className="flex flex-col items-center gap-2">
          <button
            type="button"
            onClick={() => setActiveView('settings')}
            className="w-9 h-9 rounded-xl bg-[var(--bg-card)] border border-[var(--border-subtle)] text-[var(--text-secondary)] hover:text-[var(--accent-primary)] flex items-center justify-center transition group relative"
          >
            <Palette className="w-4 h-4" />
            <span className="absolute left-12 bg-[var(--bg-card)] text-[var(--text-primary)] text-[11px] font-semibold px-2.5 py-1 rounded-md border border-[var(--border-subtle)] shadow-lg invisible opacity-0 group-hover:visible group-hover:opacity-100 pointer-events-none transition-opacity whitespace-nowrap z-50">
              Change Theme
            </span>
          </button>

          <button
            type="button"
            onClick={() => setActiveView('settings')}
            className="w-9 h-9 rounded-xl bg-[var(--bg-card)] border border-[var(--border-subtle)] flex items-center justify-center text-emerald-400 group relative"
          >
            <Zap className="w-4 h-4 text-emerald-400" />
            <span className="absolute top-1 right-1 w-1.5 h-1.5 rounded-full bg-emerald-400" />
            <span className="absolute left-12 bg-[var(--bg-card)] text-[var(--text-primary)] text-[11px] font-semibold px-2.5 py-1 rounded-md border border-[var(--border-subtle)] shadow-lg invisible opacity-0 group-hover:visible group-hover:opacity-100 pointer-events-none transition-opacity whitespace-nowrap z-50">
              {systemInfo?.gpu_name && systemInfo.gpu_name !== 'None'
                ? systemInfo.gpu_name
                : 'Hardware Diagnostics'}
            </span>
          </button>
        </div>
      </aside>

      {/* ============================================================== */}
      {/* 2. MAIN ACTIVE VIEW ROUTING                                    */}
      {/* ============================================================== */}
      <div className="flex-1 min-w-0 h-full flex flex-col overflow-hidden">
        {/* VIEW 0: HOME INGESTION VIEW */}
        {activeView === 'home' && (
          <HomeView
            onStartJob={(id) => {
              setActiveProjectId(id);
              setProgressTargetId(id);
              setActiveView('progress');
            }}
            onOpenProject={(id) => {
              selectProject(id);
              setActiveView('editor');
            }}
            onViewProgress={(id) => {
              setActiveProjectId(id);
              setProgressTargetId(id);
              setActiveView('progress');
            }}
            onViewLibrary={() => setActiveView('projects')}
            isOfflineMultimodal={isOfflineMultimodal}
            studioSettings={studioSettings}
          />
        )}

        {/* VIEW 1: DEDICATED PROGRESS PIPELINE VIEW */}
        {activeView === 'progress' && (
          <ProgressView
            projectId={progressTargetId}
            onOpenEditor={(id) => {
              selectProject(id);
              setActiveView('editor');
            }}
            onBackToHome={() => setActiveView('home')}
          />
        )}

        {/* VIEW 2: STUDIO EDITOR (ZERO-OVERFLOW 3-COLUMN WORKSTATION) */}
        {activeView === 'editor' && (
          <div className="flex-1 min-w-0 h-full flex flex-col overflow-hidden">
            {/* Top 52px Header with 3-Step Segmented Switcher */}
            <TopBar
              projectTitle={currentProject?.title}
              activeStep={activeStep}
              onSelectStep={setActiveStep}
              onUploadVideo={handleUploadVideo}
              onImportYouTube={handleImportYouTube}
              isUploading={isUploading}
              profiles={profiles}
              onApplyProfile={handleApplyProfile}
              onSaveProfile={handleSaveProfile}
              onOpenTranscriptSearch={currentProject ? handleOpenTranscriptSearch : undefined}
              onOpenAnalysisWorkstation={selectedClip ? () => setIsAnalysisModalOpen(true) : undefined}
              canUndo={canUndo}
              canRedo={canRedo}
              onUndo={handleUndo}
              onRedo={handleRedo}
              onOpenShortcuts={() => setIsShortcutsModalOpen(true)}
              isOfflineMultimodal={isOfflineMultimodal}
              geminiConfigured={Boolean(studioSettings?.gemini_configured)}
              onOpenEngineModal={() => setIsEngineModalOpen(true)}
            />

            {/* Pipeline Error Banner */}
            {currentProject?.status === 'error' && currentProject?.error_message && (
              <div className="bg-red-950/90 border-b border-red-500/50 px-4 py-2 flex items-center justify-between text-xs text-red-200 shrink-0 z-30">
                <div className="flex items-center gap-2 min-w-0 flex-1 mr-4">
                  <span className="px-1.5 py-0.5 rounded text-[10px] font-black uppercase bg-red-600 text-white shrink-0">
                    PIPELINE ERROR
                  </span>
                  <span className="truncate font-mono text-red-300">
                    {currentProject.error_message}
                  </span>
                </div>
                <div className="flex items-center gap-2 shrink-0">
                  <button
                    type="button"
                    onClick={() => setActiveView('settings')}
                    className="px-2.5 py-1 bg-red-800 hover:bg-red-700 text-white font-semibold rounded-lg text-[11px] transition"
                  >
                    Configure Gemini API
                  </button>
                  <button
                    type="button"
                    onClick={() => handleUpdateProject({ status: 'created', error_message: undefined })}
                    className="p-1 text-red-400 hover:text-white"
                    title="Dismiss"
                  >
                    ✕
                  </button>
                </div>
              </div>
            )}

            {/* 3-Column Studio Workstation (Strict Flex Grid, Independent Scrollbars) */}
            <div className="flex-1 min-h-0 flex overflow-hidden">
              {/* Column 1: Viral Clips Sidebar */}
              <ClipFeed
                clips={clips}
                selectedClipId={selectedClip?.id || null}
                onSelectClip={(clip) => {
                  setSelectedClip(clip);
                  setPreviewSeekTime(undefined);
                  setCurrentPlaybackTime(undefined);
                }}
                onMergeClips={handleMergeClips}
                isMerging={isMerging}
                onCompileTrailer={handleCompileTrailer}
                isCompilingTrailer={isCompilingTrailer}
                onAutoFrameAll={handleAutoFrameAll}
                isAutoFramingAll={isAutoFramingAll}
              />

              {/* Column 2: Full-Width Center Inspector */}
              <main className="flex-1 min-w-0 h-full min-h-0 overflow-y-auto p-4 lg:p-5 bg-[var(--bg-canvas)]">
                {activeStep === 1 && (
                  <Step1Setup
                    clip={selectedClip}
                    project={currentProject}
                    onUpdateClip={handleUpdateClip}
                    onUpdateProject={handleUpdateProject}
                    onOpenAnalysisWorkstation={() => setIsAnalysisModalOpen(true)}
                    onSeekToMasterTime={handleSeekMasterTime}
                    onCalibrateFrame={handleCalibrateCameraFrame}
                    onOpenThumbnailFrame={handleSetThumbnailCoverFrame}
                  />
                )}

                {activeStep === 2 && (
                  <Step2Captions
                    clip={selectedClip}
                    projectId={currentProject?.id}
                    captionStyles={captionStyles}
                    availableFonts={availableFonts}
                    onUpdateClip={handleUpdateClip}
                    onUploadFont={handleUploadFont}
                    onSaveQuickStyle={handleSaveQuickStyle}
                    onSeekTimestamp={(sec) => setPreviewSeekTime(sec)}
                    currentPlaybackTime={currentPlaybackTime}
                    detectedLanguage={currentProject?.caption_language}
                  />
                )}

                {activeStep === 3 && (
                  <Step3Video
                    clip={selectedClip}
                    onUpdateClip={handleUpdateClip}
                    onUploadAsset={api.uploadAsset}
                    compareOriginal={compareOriginal}
                    onCompareOriginalChange={setCompareOriginal}
                  />
                )}
              </main>

              {/* Column 3: Auto-Scaled Smartphone Preview & Export Dock */}
              <SmartphonePreview
                clip={selectedClip}
                activeStep={activeStep}
                videoUrl={selectedClip?.video_url || selectedClip?.preview_url || undefined}
                safeZoneGuide={safeZoneGuide}
                onSafeZoneChange={setSafeZoneGuide}
                compareOriginal={compareOriginal}
                onCompareOriginalChange={setCompareOriginal}
                seekTime={previewSeekTime}
                onPlaybackTimeChange={setCurrentPlaybackTime}
                onGenerateClips={handleGenerateClips}
                onAutoRenderClip={handleAutoRenderClip}
                onAutoRenderComplete={handleAutoRenderComplete}
                onTrimClip={handleTrimClip}
                onUpdateSegments={handleUpdateSegments}
                onApplyFixClip={handleApplyFixClip}
                onUpdateClip={handleUpdateClip}
                onOpenAnalysisWorkstation={() => setIsAnalysisModalOpen(true)}
                externalThumbRequest={thumbRequest}
                isRendering={isRendering || selectedClip?.render_status === 'rendering'}
                renderProgress={selectedClip?.render_progress || 0}
              />
            </div>

            {/* Bottom 32px Hardware Telemetry Strip */}
            <BottomTelemetryBar
              systemInfo={systemInfo}
              studioSettings={studioSettings}
              isRendering={isRendering || selectedClip?.render_status === 'rendering'}
              renderProgress={selectedClip?.render_progress || 0}
            />
          </div>
        )}

        {/* VIEW 3: MY CLIPS (PROJECT & DISK MANAGER) */}
        {activeView === 'projects' && (
          <ViewProjects
            onSelectProjectForEditor={(projectId) => {
              selectProject(projectId);
              setActiveView('editor');
            }}
            onOpenUploadModal={() => {
              setActiveView('home');
            }}
          />
        )}

        {/* VIEW 4: SETTINGS & HARDWARE DIAGNOSTICS */}
        {activeView === 'settings' && (
          <ErrorBoundary fallbackTitle="Settings & Diagnostics Error">
            <ViewSettings onOpenSetupWizard={() => setIsWizardOpen(true)} />
          </ErrorBoundary>
        )}
      </div>

      {/* First-Time Setup Wizard */}
      <FirstTimeSetupWizard
        isOpen={isWizardOpen}
        onClose={() => setIsWizardOpen(false)}
        systemInfo={systemInfo}
      />

      {/* Multimodal AI Video & Audio Analysis + Best-Frame Workstation */}
      <AnalysisInspectorModal
        isOpen={isAnalysisModalOpen}
        onClose={() => setIsAnalysisModalOpen(false)}
        clip={selectedClip}
        projectId={currentProject?.id}
        currentPlaybackTime={currentPlaybackTime}
        onSeekToMasterTime={handleSeekMasterTime}
        onSetThumbnailCover={handleSetThumbnailCoverFrame}
        onSnapClipStart={handleSnapClipStartFrame}
        onCalibrateCamera={handleCalibrateCameraFrame}
      />

      {/* Full Transcript Search & Manual Clip Creator Modal */}
      {isTranscriptModalOpen && currentProject && (
        <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/75 backdrop-blur-sm p-4 animate-fadeIn">
          <div className="bg-[var(--bg-surface)] border border-[var(--border-subtle)] rounded-2xl w-full max-w-2xl p-6 space-y-4 shadow-2xl text-[var(--text-primary)]">
            <div className="flex items-center justify-between border-b border-[var(--border-subtle)] pb-3">
              <div className="flex items-center gap-2 font-bold text-sm">
                <Search className="w-4 h-4 text-[var(--accent-primary)]" />
                <span>Full Project Transcript &amp; Manual Clip Creator</span>
              </div>
              <button
                type="button"
                onClick={() => setIsTranscriptModalOpen(false)}
                className="text-[var(--text-secondary)] hover:text-[var(--text-primary)]"
              >
                <X className="w-5 h-5" />
              </button>
            </div>

            <div className="flex gap-2">
              <input
                type="text"
                placeholder="Search full transcript words..."
                value={transcriptQuery}
                onChange={async (e) => {
                  const val = e.target.value;
                  setTranscriptQuery(val);
                  const res = await api.searchProjectTranscript(currentProject.id, val).catch(() => null);
                  if (res) setTranscriptMatches(res.matches || []);
                }}
                className="flex-1 bg-[var(--bg-input)] border border-[var(--border-subtle)] rounded-xl px-3.5 py-2 text-xs text-[var(--text-primary)] outline-none focus:border-[var(--accent-primary)]"
                autoFocus
              />
            </div>

            <div className="max-h-56 overflow-y-auto space-y-1.5 bg-[var(--bg-input)] border border-[var(--border-subtle)] rounded-xl p-2.5">
              {transcriptMatches.length === 0 ? (
                <p className="text-xs text-[var(--text-secondary)] text-center py-6">
                  No matching transcript blocks found. You can still enter custom In/Out seconds below.
                </p>
              ) : (
                transcriptMatches.map((m, idx) => (
                  <button
                    key={idx}
                    type="button"
                    onClick={() => {
                      setManualStart(String(Math.max(0, m.start - 1.0).toFixed(1)));
                      setManualEnd(String((m.end + 15.0).toFixed(1)));
                      if (!manualTitle) setManualTitle(m.text.slice(0, 32));
                    }}
                    className="w-full text-left px-3 py-2 rounded-lg bg-[var(--bg-card)] hover:border-[var(--accent-primary)]/50 border border-[var(--border-subtle)] flex items-start gap-2.5 transition"
                  >
                    <span className="text-[10px] font-mono font-bold text-[var(--accent-primary)] shrink-0 mt-0.5">
                      {m.start.toFixed(1)}s
                    </span>
                    <span className="text-xs text-[var(--text-primary)]">{m.text}</span>
                  </button>
                ))
              )}
            </div>

            <form onSubmit={handleCreateManualClip} className="grid grid-cols-1 sm:grid-cols-4 gap-2.5 pt-2 border-t border-[var(--border-subtle)] text-xs">
              <div>
                <label className="text-[10px] text-[var(--text-secondary)] block mb-1">Start (sec)</label>
                <input
                  type="number"
                  step="0.5"
                  min="0"
                  value={manualStart}
                  onChange={(e) => setManualStart(e.target.value)}
                  className="w-full bg-[var(--bg-input)] border border-[var(--border-subtle)] rounded-lg px-2.5 py-1.5 text-xs outline-none focus:border-[var(--accent-primary)]"
                />
              </div>
              <div>
                <label className="text-[10px] text-[var(--text-secondary)] block mb-1">End (sec)</label>
                <input
                  type="number"
                  step="0.5"
                  min="1"
                  value={manualEnd}
                  onChange={(e) => setManualEnd(e.target.value)}
                  className="w-full bg-[var(--bg-input)] border border-[var(--border-subtle)] rounded-lg px-2.5 py-1.5 text-xs outline-none focus:border-[var(--accent-primary)]"
                />
              </div>
              <div>
                <label className="text-[10px] text-[var(--text-secondary)] block mb-1">Hook Headline</label>
                <input
                  type="text"
                  placeholder="Custom clip title..."
                  value={manualTitle}
                  onChange={(e) => setManualTitle(e.target.value)}
                  className="w-full bg-[var(--bg-input)] border border-[var(--border-subtle)] rounded-lg px-2.5 py-1.5 text-xs outline-none focus:border-[var(--accent-primary)]"
                />
              </div>
              <div className="flex items-end">
                <button
                  type="submit"
                  disabled={isCreatingManual}
                  className="w-full py-1.5 px-3 rounded-lg bg-[var(--accent-primary)] text-[var(--accent-foreground)] font-bold flex items-center justify-center gap-1.5 hover:opacity-90 transition disabled:opacity-50"
                >
                  <Scissors className="w-3.5 h-3.5" />
                  <span>{isCreatingManual ? 'Creating...' : 'Create Clip'}</span>
                </button>
              </div>
            </form>
          </div>
        </div>
      )}

      {/* Keyboard Shortcuts Reference Modal */}
      {isShortcutsModalOpen && (
        <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/75 backdrop-blur-sm p-4 animate-fadeIn">
          <div className="bg-[var(--bg-surface)] border border-[var(--border-subtle)] rounded-2xl w-full max-w-md p-5 space-y-4 shadow-2xl text-[var(--text-primary)]">
            <div className="flex items-center justify-between border-b border-[var(--border-subtle)] pb-2.5">
              <div className="flex items-center gap-2 font-bold text-sm">
                <Keyboard className="w-4 h-4 text-[var(--accent-primary)]" />
                <span>Studio NLE Keyboard Shortcuts</span>
              </div>
              <button
                type="button"
                onClick={() => setIsShortcutsModalOpen(false)}
                className="text-[var(--text-secondary)] hover:text-[var(--text-primary)]"
              >
                <X className="w-4 h-4" />
              </button>
            </div>
            <div className="space-y-2 text-xs">
              {[
                { key: 'Space', desc: 'Play / Pause preview player' },
                { key: '← / →', desc: 'Seek backward / forward 1 second' },
                { key: 'Shift + ← / →', desc: 'Seek backward / forward 5 seconds' },
                { key: 'I / O', desc: 'Set Clip In / Out boundary at playhead' },
                { key: 'B', desc: 'Blade split timeline segment at playhead' },
                { key: 'M', desc: 'Mute / Unmute preview audio' },
                { key: 'Ctrl + Z', desc: 'Undo last clip edit' },
                { key: 'Ctrl + Y / Shift+Z', desc: 'Redo clip edit' },
                { key: '?', desc: 'Toggle shortcuts guide' },
              ].map((item) => (
                <div
                  key={item.key}
                  className="flex items-center justify-between px-3 py-1.5 rounded-lg bg-[var(--bg-input)] border border-[var(--border-subtle)]"
                >
                  <span className="text-[var(--text-secondary)]">{item.desc}</span>
                  <kbd className="px-2 py-0.5 rounded bg-[var(--bg-card)] border border-[var(--border-subtle)] font-mono text-[10px] font-bold text-[var(--accent-primary)]">
                    {item.key}
                  </kbd>
                </div>
              ))}
            </div>
          </div>
        </div>
      )}

      <AIEngineModal
        isOpen={isEngineModalOpen}
        onClose={() => setIsEngineModalOpen(false)}
        currentMode={isOfflineMultimodal ? 'offline' : 'gemini'}
        geminiConfigured={Boolean(studioSettings?.gemini_configured)}
      />

      {/* Global Toast Notification */}
      {toast && (
        <div
          className={`fixed bottom-12 right-5 z-50 px-4 py-2.5 rounded-xl border shadow-2xl text-xs font-semibold flex items-center gap-2.5 animate-fadeIn ${
            toast.type === 'error'
              ? 'bg-red-950/95 border-red-500/50 text-red-200'
              : toast.type === 'success'
              ? 'bg-emerald-950/95 border-emerald-500/50 text-emerald-200'
              : 'bg-[var(--bg-surface)] border-[var(--border-subtle)] text-[var(--text-primary)]'
          }`}
        >
          <span>{toast.message}</span>
          <button
            type="button"
            onClick={() => setToast(null)}
            className="opacity-70 hover:opacity-100 ml-1"
          >
            ✕
          </button>
        </div>
      )}
    </div>
  );
};

export default App;
