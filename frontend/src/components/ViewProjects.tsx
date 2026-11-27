import React, { useEffect, useMemo, useRef, useState } from 'react';
import {
  AlertTriangle,
  Archive,
  ArrowDownUp,
  ArrowRight,
  Check,
  CheckCircle2,
  CheckSquare,
  Eraser,
  Film,
  Folder,
  HardDrive,
  Key,
  Loader2,
  Play,
  RefreshCw,
  RotateCcw,
  Search,
  Sparkles,
  Square,
  Terminal,
  Trash2,
  Video,
  X,
} from 'lucide-react';
import { Project, ProjectLogItem, ProjectsListResponse } from '../types';
import { api } from '../api/client';
import { AIEngineModal } from './AIEngineModal';

interface ViewProjectsProps {
  onSelectProjectForEditor: (projectId: number) => void;
  onOpenUploadModal: () => void;
  onViewProgress?: (projectId: number) => void;
  geminiConfigured?: boolean;
  isOfflineMultimodal?: boolean;
}

type StatusFilter = 'all' | 'ready' | 'created' | 'failed';
type SortKey = 'newest' | 'most_clips' | 'largest_disk' | 'longest_duration';

function formatBytesLocal(bytes: number): string {
  if (!bytes || bytes <= 0) return '0 B';
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 ** 2) return `${(bytes / 1024).toFixed(1)} KB`;
  if (bytes < 1024 ** 3) return `${(bytes / 1024 ** 2).toFixed(1)} MB`;
  return `${(bytes / 1024 ** 3).toFixed(2)} GB`;
}

function formatDurationBadge(seconds?: number): string {
  if (!seconds || seconds <= 0) return '00:00';
  const total = Math.floor(seconds);
  const hrs = Math.floor(total / 3600);
  const mins = Math.floor((total % 3600) / 60);
  const secs = total % 60;
  if (hrs > 0) {
    return `${hrs}:${mins.toString().padStart(2, '0')}:${secs.toString().padStart(2, '0')}`;
  }
  return `${mins.toString().padStart(2, '0')}:${secs.toString().padStart(2, '0')}`;
}

function formatResolutionBadge(w?: number, h?: number): string | null {
  const rw = Number(w || 0);
  const rh = Number(h || 0);
  if (rw <= 0 || rh <= 0) return null;
  if (rw >= 3840 || rh >= 2160) return `4K ${rw}×${rh}`;
  if (rw >= 2560 || rh >= 1440) return `1440p ${rw}×${rh}`;
  if (rw >= 1920 || rh >= 1080) return `1080p`;
  if (rw >= 1280 || rh >= 720) return `720p`;
  return `${rw}×${rh}`;
}

function formatDateCompact(isoString?: string): string {
  if (!isoString) return 'Recent';
  try {
    const d = new Date(isoString);
    return d.toLocaleDateString(undefined, {
      month: 'short',
      day: 'numeric',
      hour: '2-digit',
      minute: '2-digit',
    });
  } catch {
    return isoString;
  }
}

function getClipCount(p: Project): number {
  return Number(p.clips_count ?? p.clip_count ?? 0);
}

function isReadyProject(p: Project): boolean {
  const s = String(p.status || '').toLowerCase();
  return getClipCount(p) > 0 || s === 'analyzed' || s === 'finished' || s === 'ready';
}

function isCreatedProject(p: Project): boolean {
  const s = String(p.status || '').toLowerCase();
  return !isReadyProject(p) && (s === 'created' || s === 'queued');
}

function isFailedOrInterruptedProject(p: Project): boolean {
  const s = String(p.status || '').toLowerCase();
  return (
    !isReadyProject(p) &&
    (s === 'error' || s === 'failed' || s === 'cancelled' || s === 'awaiting_api_key')
  );
}

export const ViewProjects: React.FC<ViewProjectsProps> = ({
  onSelectProjectForEditor,
  onOpenUploadModal,
  onViewProgress,
  geminiConfigured = false,
  isOfflineMultimodal = false,
}) => {
  const [projectsData, setProjectsData] = useState<ProjectsListResponse>({
    projects: [],
    total_disk_bytes: 0,
    total_disk_formatted: '0 B',
    projects_count: 0,
    total_clips: 0,
  });
  const [loading, setLoading] = useState(true);
  const [loadError, setLoadError] = useState<string | null>(null);

  const [searchQuery, setSearchQuery] = useState('');
  const [statusFilter, setStatusFilter] = useState<StatusFilter>('all');
  const [sortBy, setSortBy] = useState<SortKey>('newest');

  // Bulk Selection & Cleanup Popover
  const [selectedIds, setSelectedIds] = useState<Set<number>>(new Set());
  const [isBulkDeleting, setIsBulkDeleting] = useState(false);
  const [isCleanupOpen, setIsCleanupOpen] = useState(false);
  const [cleaningMode, setCleaningMode] = useState<string | null>(null);
  const cleanupRef = useRef<HTMLDivElement | null>(null);

  // Action & Feedback States
  const [noticeBanner, setNoticeBanner] = useState<{ type: 'success' | 'error'; text: string } | null>(null);
  const [deletingId, setDeletingId] = useState<number | null>(null);
  const [startingId, setStartingId] = useState<number | null>(null);
  const [brokenThumbs, setBrokenThumbs] = useState<Set<number>>(new Set());

  // Add API Key & Run Modal Target
  const [keyTargetProject, setKeyTargetProject] = useState<Project | null>(null);

  // Fallback Logs Modal (when onViewProgress is not provided)
  const [selectedLogProject, setSelectedLogProject] = useState<Project | null>(null);
  const [projectLogs, setProjectLogs] = useState<ProjectLogItem[]>([]);
  const [logsLoading, setLogsLoading] = useState(false);

  const showNotice = (text: string, type: 'success' | 'error' = 'success') => {
    setNoticeBanner({ type, text });
    window.setTimeout(() => {
      setNoticeBanner((prev) => (prev?.text === text ? null : prev));
    }, 5000);
  };

  useEffect(() => {
    void loadProjects();
  }, []);

  useEffect(() => {
    if (!isCleanupOpen) return;
    const onOutside = (e: MouseEvent) => {
      if (cleanupRef.current && !cleanupRef.current.contains(e.target as Node)) {
        setIsCleanupOpen(false);
      }
    };
    const onEsc = (e: KeyboardEvent) => {
      if (e.key === 'Escape') setIsCleanupOpen(false);
    };
    window.addEventListener('mousedown', onOutside);
    window.addEventListener('keydown', onEsc);
    return () => {
      window.removeEventListener('mousedown', onOutside);
      window.removeEventListener('keydown', onEsc);
    };
  }, [isCleanupOpen]);

  const loadProjects = async () => {
    setLoading(true);
    setLoadError(null);
    try {
      const data = await api.getProjectsList();
      setProjectsData(data);
      const validIds = new Set(data.projects.map((p) => p.id));
      setSelectedIds((prev) => {
        const next = new Set<number>();
        prev.forEach((id) => {
          if (validIds.has(id)) next.add(id);
        });
        return next;
      });
    } catch (err: any) {
      console.error('Failed to load projects list:', err);
      setLoadError(err?.message || 'Unable to load Project Vault from backend.');
    } finally {
      setLoading(false);
    }
  };

  const handleOpenLogs = async (proj: Project) => {
    if (onViewProgress) {
      onViewProgress(proj.id);
      return;
    }
    setSelectedLogProject(proj);
    setLogsLoading(true);
    try {
      const res = await api.getProjectLogs(proj.id);
      setProjectLogs(res.logs || []);
    } catch {
      setProjectLogs([
        {
          timestamp: new Date().toISOString(),
          stage: 'ERROR',
          message: 'Unable to retrieve execution logs for this project.',
        },
      ]);
    } finally {
      setLogsLoading(false);
    }
  };

  const handleStartOrRetry = async (proj: Project, forceOffline?: boolean) => {
    const s = String(proj.status || '').toLowerCase();
    if (s === 'created' && proj.source_missing) {
      showNotice(
        `"${proj.title}" has a missing/0 B source file — clean up test entries or delete this card.`,
        'error'
      );
      return;
    }

    setStartingId(proj.id);
    try {
      const useOffline = forceOffline !== undefined ? forceOffline : proj.ai_mode === 'offline';
      await api.retryProject(proj.id, useOffline);
      showNotice(`Pipeline launched for "${proj.title}".`, 'success');
      if (onViewProgress) {
        onViewProgress(proj.id);
      } else {
        await loadProjects();
      }
    } catch (err: any) {
      showNotice(err?.message || `Failed to start "${proj.title}".`, 'error');
    } finally {
      setStartingId(null);
    }
  };

  const handleDeleteProject = async (proj: Project, e: React.MouseEvent) => {
    e.stopPropagation();
    if (
      !window.confirm(
        `Permanently delete "${proj.title}" and reclaim ${proj.disk_size_formatted || 'disk space'}?`
      )
    ) {
      return;
    }

    setDeletingId(proj.id);
    try {
      const res = await api.deleteProject(proj.id);
      showNotice(`Deleted "${proj.title}". Reclaimed ${res.reclaimed_formatted || 'disk space'}.`);
      await loadProjects();
    } catch (err: any) {
      showNotice(err?.message || 'Error deleting project.', 'error');
    } finally {
      setDeletingId(null);
    }
  };

  const handleBulkDelete = async () => {
    const ids = Array.from(selectedIds);
    if (ids.length === 0) return;
    if (
      !window.confirm(
        `Permanently delete ${ids.length} selected ${ids.length === 1 ? 'project' : 'projects'} and free ${formatBytesLocal(
          selectedBytes
        )} of local SSD storage?`
      )
    ) {
      return;
    }

    setIsBulkDeleting(true);
    try {
      const res = await api.bulkDeleteProjects(ids);
      setSelectedIds(new Set());
      showNotice(
        `Deleted ${res.deleted_count} ${res.deleted_count === 1 ? 'project' : 'projects'} · Reclaimed ${
          res.reclaimed_formatted || formatBytesLocal(res.reclaimed_bytes)
        }.`
      );
      await loadProjects();
    } catch (err: any) {
      showNotice(err?.message || 'Bulk delete failed.', 'error');
    } finally {
      setIsBulkDeleting(false);
    }
  };

  const handleCleanup = async (
    mode: 'test_and_empty' | 'failed_and_cancelled' | 'render_cache',
    label: string
  ) => {
    setCleaningMode(mode);
    try {
      const res = await api.cleanupProjects(mode);
      setIsCleanupOpen(false);
      const countText =
        mode === 'render_cache'
          ? `${res.removed_count} cache file${res.removed_count === 1 ? '' : 's'} cleared`
          : `${res.removed_count} project${res.removed_count === 1 ? '' : 's'} removed`;
      showNotice(`${label}: ${countText} · Reclaimed ${res.reclaimed_formatted || '0 B'}.`);
      setBrokenThumbs(new Set());
      await loadProjects();
    } catch (err: any) {
      showNotice(err?.message || 'Cleanup action failed.', 'error');
    } finally {
      setCleaningMode(null);
    }
  };

  // Live filter counts
  const counts = useMemo(() => {
    const all = projectsData.projects;
    return {
      all: all.length,
      ready: all.filter(isReadyProject).length,
      created: all.filter(isCreatedProject).length,
      failed: all.filter(isFailedOrInterruptedProject).length,
    };
  }, [projectsData.projects]);

  const totalClipsCount = useMemo(() => {
    if (typeof projectsData.total_clips === 'number') return projectsData.total_clips;
    return projectsData.projects.reduce((acc, p) => acc + getClipCount(p), 0);
  }, [projectsData]);

  // Storage Breakdown metrics
  const breakdown = useMemo(() => {
    if (projectsData.storage_breakdown) return projectsData.storage_breakdown;
    const src = projectsData.projects.reduce((s, p) => s + Number(p.source_bytes ?? p.disk_size_bytes ?? 0), 0);
    const clips = projectsData.projects.reduce((s, p) => s + Number(p.clips_bytes ?? 0), 0);
    const cache = projectsData.projects.reduce((s, p) => s + Number(p.cache_bytes ?? 0), 0);
    const failedProjects = projectsData.projects.filter(isFailedOrInterruptedProject);
    const failedBytes = failedProjects.reduce((s, p) => s + Number(p.disk_bytes ?? p.disk_size_bytes ?? 0), 0);
    const testProjects = projectsData.projects.filter(
      (p) => getClipCount(p) === 0 && (p.source_missing || Number(p.source_bytes ?? 0) === 0)
    );
    const testBytes = testProjects.reduce((s, p) => s + Number(p.disk_bytes ?? p.disk_size_bytes ?? 0), 0);

    return {
      source_bytes: src,
      source_formatted: formatBytesLocal(src),
      clips_bytes: clips,
      clips_formatted: formatBytesLocal(clips),
      cache_bytes: cache,
      cache_formatted: formatBytesLocal(cache),
      reclaimable_test_count: testProjects.length,
      reclaimable_test_bytes: testBytes,
      reclaimable_test_formatted: formatBytesLocal(testBytes),
      reclaimable_failed_count: failedProjects.length,
      reclaimable_failed_bytes: failedBytes,
      reclaimable_failed_formatted: formatBytesLocal(failedBytes),
    };
  }, [projectsData]);

  const storageTotalForBar = Math.max(
    1,
    breakdown.source_bytes + breakdown.clips_bytes + breakdown.cache_bytes
  );
  const srcPct = Math.round((breakdown.source_bytes / storageTotalForBar) * 100);
  const clipsPct = Math.round((breakdown.clips_bytes / storageTotalForBar) * 100);
  const cachePct = Math.max(0, 100 - srcPct - clipsPct);

  // Filtered + Sorted list
  const filteredAndSortedProjects = useMemo(() => {
    const q = searchQuery.trim().toLowerCase();
    const filtered = projectsData.projects.filter((p) => {
      if (statusFilter === 'ready' && !isReadyProject(p)) return false;
      if (statusFilter === 'created' && !isCreatedProject(p)) return false;
      if (statusFilter === 'failed' && !isFailedOrInterruptedProject(p)) return false;
      if (q) {
        const titleMatch = p.title?.toLowerCase().includes(q);
        const fileMatch = p.source_filename?.toLowerCase().includes(q);
        if (!titleMatch && !fileMatch) return false;
      }
      return true;
    });

    const sorted = [...filtered];
    sorted.sort((a, b) => {
      if (sortBy === 'most_clips') {
        const diff = getClipCount(b) - getClipCount(a);
        return diff !== 0 ? diff : b.id - a.id;
      }
      if (sortBy === 'largest_disk') {
        const diff = Number(b.disk_bytes ?? b.disk_size_bytes ?? 0) - Number(a.disk_bytes ?? a.disk_size_bytes ?? 0);
        return diff !== 0 ? diff : b.id - a.id;
      }
      if (sortBy === 'longest_duration') {
        const diff = Number(b.duration || 0) - Number(a.duration || 0);
        return diff !== 0 ? diff : b.id - a.id;
      }
      // newest first
      return b.id - a.id;
    });
    return sorted;
  }, [projectsData.projects, searchQuery, statusFilter, sortBy]);

  const allVisibleSelected =
    filteredAndSortedProjects.length > 0 &&
    filteredAndSortedProjects.every((p) => selectedIds.has(p.id));

  const toggleSelectProject = (projectId: number, e?: React.MouseEvent) => {
    if (e) e.stopPropagation();
    setSelectedIds((prev) => {
      const next = new Set(prev);
      if (next.has(projectId)) next.delete(projectId);
      else next.add(projectId);
      return next;
    });
  };

  const toggleSelectAllVisible = () => {
    setSelectedIds((prev) => {
      const next = new Set(prev);
      if (allVisibleSelected) {
        filteredAndSortedProjects.forEach((p) => next.delete(p.id));
      } else {
        filteredAndSortedProjects.forEach((p) => next.add(p.id));
      }
      return next;
    });
  };

  const selectedBytes = useMemo(() => {
    return projectsData.projects.reduce((acc, p) => {
      if (!selectedIds.has(p.id)) return acc;
      return acc + Number(p.disk_bytes ?? p.disk_size_bytes ?? 0);
    }, 0);
  }, [projectsData.projects, selectedIds]);

  const filterPills: { id: StatusFilter; label: string; count: number }[] = [
    { id: 'all', label: 'All', count: counts.all },
    { id: 'ready', label: 'Analyzed / Ready', count: counts.ready },
    { id: 'created', label: 'Needs Analysis (Created)', count: counts.created },
    { id: 'failed', label: 'Failed / Interrupted', count: counts.failed },
  ];

  return (
    <div className="flex-1 overflow-y-auto bg-[var(--bg-canvas)] text-[var(--text-primary)] transition-colors">
      <div className="p-6 md:p-8 space-y-6 animate-fadeIn max-w-7xl mx-auto pb-28">
        {/* TOP HEADER & INTERACTIVE STORAGE BREAKDOWN CARD */}
        <div className="flex flex-col lg:flex-row lg:items-center justify-between gap-5 pb-6 border-b border-[var(--border-subtle)]">
          <div className="space-y-1.5">
            <div className="flex flex-wrap items-center gap-2.5">
              <div className="w-9 h-9 rounded-xl bg-[var(--accent-subtle)] border border-[var(--accent-primary)]/40 flex items-center justify-center text-[var(--accent-primary)] shrink-0">
                <Archive className="w-5 h-5" />
              </div>
              <h1 className="text-2xl md:text-3xl font-extrabold tracking-tight">
                Project Vault
              </h1>
              <span className="px-2.5 py-0.5 rounded-full text-xs font-semibold bg-[var(--bg-card)] text-[var(--text-secondary)] border border-[var(--border-subtle)] tabular-nums">
                {projectsData.projects_count} {projectsData.projects_count === 1 ? 'project' : 'projects'}
              </span>
              <span className="px-2.5 py-0.5 rounded-full text-xs font-semibold bg-[var(--accent-subtle)] text-[var(--accent-primary)] border border-[var(--accent-primary)]/30 tabular-nums">
                {totalClipsCount} extracted {totalClipsCount === 1 ? 'clip' : 'clips'}
              </span>
            </div>
            <p className="text-xs md:text-sm text-[var(--text-secondary)]">
              Master recordings, extracted shorts, and local SSD storage
            </p>
          </div>

          {/* Interactive Storage Breakdown Card */}
          <div
            ref={cleanupRef}
            className="relative bg-[var(--bg-card)] border border-[var(--border-subtle)] rounded-2xl p-4 shadow-sm w-full lg:w-[430px] shrink-0 space-y-3"
          >
            <div className="flex items-center justify-between gap-3">
              <div className="flex items-center gap-3 min-w-0">
                <div className="p-2.5 rounded-xl bg-[var(--accent-subtle)] border border-[var(--accent-primary)]/30 text-[var(--accent-primary)] shrink-0">
                  <HardDrive className="w-5 h-5" />
                </div>
                <div className="min-w-0">
                  <div className="text-[10px] uppercase font-bold text-[var(--text-secondary)] tracking-wider">
                    Total Disk Usage
                  </div>
                  <div className="text-lg font-extrabold flex items-center gap-2 tabular-nums">
                    <span>{projectsData.total_disk_formatted}</span>
                    <span className="text-[10px] font-semibold text-emerald-500 bg-emerald-500/10 px-1.5 py-0.5 rounded border border-emerald-500/25">
                      Local SSD
                    </span>
                  </div>
                </div>
              </div>

              <div className="flex items-center gap-1.5 shrink-0">
                <button
                  type="button"
                  onClick={() => setIsCleanupOpen((v) => !v)}
                  className="flex items-center gap-1.5 px-3 py-1.5 rounded-xl bg-[var(--bg-surface)] hover:bg-[var(--card-hover)] border border-[var(--border-subtle)] hover:border-[var(--accent-primary)]/50 text-xs font-semibold text-[var(--text-primary)] transition"
                >
                  <Eraser className="w-3.5 h-3.5 text-[var(--accent-primary)]" />
                  <span>Clean Up Space</span>
                </button>
                <button
                  type="button"
                  onClick={loadProjects}
                  title="Refresh Project Vault"
                  className="p-2 rounded-xl text-[var(--text-secondary)] hover:text-[var(--text-primary)] hover:bg-[var(--bg-input)] border border-transparent hover:border-[var(--border-subtle)] transition"
                >
                  <RefreshCw
                    className={`w-4 h-4 ${loading ? 'animate-spin text-[var(--accent-primary)]' : ''}`}
                  />
                </button>
              </div>
            </div>

            {/* Segmented Visual Storage Bar */}
            <div className="space-y-1.5">
              <div className="h-2 w-full rounded-full bg-[var(--bg-input)] border border-[var(--border-subtle)] overflow-hidden flex">
                {breakdown.source_bytes > 0 && (
                  <div
                    style={{ width: `${Math.max(4, srcPct)}%` }}
                    className="h-full bg-[var(--accent-primary)] transition-all duration-300"
                    title={`Source Videos: ${breakdown.source_formatted}`}
                  />
                )}
                {breakdown.clips_bytes > 0 && (
                  <div
                    style={{ width: `${Math.max(4, clipsPct)}%` }}
                    className="h-full bg-sky-500 transition-all duration-300"
                    title={`Exported Clips: ${breakdown.clips_formatted}`}
                  />
                )}
                {breakdown.cache_bytes > 0 && (
                  <div
                    style={{ width: `${Math.max(4, cachePct)}%` }}
                    className="h-full bg-amber-500 transition-all duration-300"
                    title={`Temp & Proxy Cache: ${breakdown.cache_formatted}`}
                  />
                )}
              </div>

              <div className="flex flex-wrap items-center justify-between gap-2 text-[11px] text-[var(--text-secondary)] tabular-nums">
                <span className="flex items-center gap-1.5">
                  <span className="w-2 h-2 rounded-full bg-[var(--accent-primary)] inline-block" />
                  Source <strong className="text-[var(--text-primary)]">{breakdown.source_formatted}</strong>
                </span>
                <span className="flex items-center gap-1.5">
                  <span className="w-2 h-2 rounded-full bg-sky-500 inline-block" />
                  Clips <strong className="text-[var(--text-primary)]">{breakdown.clips_formatted}</strong>
                </span>
                <span className="flex items-center gap-1.5">
                  <span className="w-2 h-2 rounded-full bg-amber-500 inline-block" />
                  Cache <strong className="text-[var(--text-primary)]">{breakdown.cache_formatted}</strong>
                </span>
              </div>
            </div>

            {/* 1-Click Storage Cleanup Popover */}
            {isCleanupOpen && (
              <div className="absolute right-0 top-full mt-2 w-full sm:w-[400px] z-40 bg-[var(--bg-surface)] border border-[var(--border-subtle)] rounded-2xl shadow-2xl p-4 space-y-2.5 animate-fadeIn">
                <div className="flex items-center justify-between pb-2 border-b border-[var(--border-subtle)]">
                  <div>
                    <h3 className="text-xs font-bold text-[var(--text-primary)]">
                      1-Click Studio Storage Cleanup
                    </h3>
                    <p className="text-[11px] text-[var(--text-secondary)]">
                      Reclaim local SSD space safely without touching finished clips.
                    </p>
                  </div>
                  <button
                    type="button"
                    onClick={() => setIsCleanupOpen(false)}
                    className="p-1 rounded-lg text-[var(--text-secondary)] hover:text-[var(--text-primary)]"
                  >
                    <X className="w-4 h-4" />
                  </button>
                </div>

                {/* Action 1: Purge Test & 0-Byte Entries */}
                <button
                  type="button"
                  disabled={Boolean(cleaningMode)}
                  onClick={() => handleCleanup('test_and_empty', 'Purge Test & 0-Byte Entries')}
                  className="w-full text-left p-3 rounded-xl bg-[var(--bg-card)] hover:bg-[var(--card-hover)] border border-[var(--border-subtle)] hover:border-[var(--accent-primary)]/40 transition flex items-center justify-between gap-3 disabled:opacity-60"
                >
                  <div className="min-w-0">
                    <div className="text-xs font-semibold text-[var(--text-primary)] flex items-center gap-2">
                      <span>Purge Test &amp; 0-Byte Entries</span>
                    </div>
                    <div className="text-[11px] text-[var(--text-secondary)]">
                      Removes dummy 0 B / source.mp4 tests &amp; 0-clip orphans
                    </div>
                  </div>
                  <span className="shrink-0 px-2 py-1 rounded-lg text-[10px] font-bold bg-[var(--bg-input)] border border-[var(--border-subtle)] text-[var(--accent-primary)] tabular-nums">
                    {cleaningMode === 'test_and_empty' ? (
                      <Loader2 className="w-3.5 h-3.5 animate-spin" />
                    ) : (
                      `${breakdown.reclaimable_test_count} ${
                        breakdown.reclaimable_test_count === 1 ? 'entry' : 'entries'
                      }`
                    )}
                  </span>
                </button>

                {/* Action 2: Delete Failed & Cancelled Runs */}
                <button
                  type="button"
                  disabled={Boolean(cleaningMode)}
                  onClick={() =>
                    handleCleanup('failed_and_cancelled', 'Delete Failed & Cancelled Runs')
                  }
                  className="w-full text-left p-3 rounded-xl bg-[var(--bg-card)] hover:bg-[var(--card-hover)] border border-[var(--border-subtle)] hover:border-red-500/40 transition flex items-center justify-between gap-3 disabled:opacity-60"
                >
                  <div className="min-w-0">
                    <div className="text-xs font-semibold text-[var(--text-primary)]">
                      Delete Failed &amp; Cancelled Runs
                    </div>
                    <div className="text-[11px] text-[var(--text-secondary)]">
                      {breakdown.reclaimable_failed_count} failed/cancelled{' '}
                      {breakdown.reclaimable_failed_count === 1 ? 'run' : 'runs'}
                    </div>
                  </div>
                  <span className="shrink-0 px-2 py-1 rounded-lg text-[10px] font-bold bg-red-500/10 border border-red-500/30 text-red-400 tabular-nums">
                    {cleaningMode === 'failed_and_cancelled' ? (
                      <Loader2 className="w-3.5 h-3.5 animate-spin" />
                    ) : (
                      breakdown.reclaimable_failed_formatted
                    )}
                  </span>
                </button>

                {/* Action 3: Clear Temp Preview Cache */}
                <button
                  type="button"
                  disabled={Boolean(cleaningMode)}
                  onClick={() => handleCleanup('render_cache', 'Clear Temp Preview Cache')}
                  className="w-full text-left p-3 rounded-xl bg-[var(--bg-card)] hover:bg-[var(--card-hover)] border border-[var(--border-subtle)] hover:border-amber-500/40 transition flex items-center justify-between gap-3 disabled:opacity-60"
                >
                  <div className="min-w-0">
                    <div className="text-xs font-semibold text-[var(--text-primary)]">
                      Clear Temp Preview Cache
                    </div>
                    <div className="text-[11px] text-[var(--text-secondary)]">
                      Clears preview frames &amp; scratch; keeps sources &amp; exports
                    </div>
                  </div>
                  <span className="shrink-0 px-2 py-1 rounded-lg text-[10px] font-bold bg-amber-500/10 border border-amber-500/30 text-amber-400 tabular-nums">
                    {cleaningMode === 'render_cache' ? (
                      <Loader2 className="w-3.5 h-3.5 animate-spin" />
                    ) : (
                      breakdown.cache_formatted
                    )}
                  </span>
                </button>
              </div>
            )}
          </div>
        </div>

        {/* Feedback / Reclaimed Disk Banner */}
        {noticeBanner && (
          <div
            className={`flex items-center justify-between gap-3 px-4 py-3 rounded-xl border text-xs font-medium animate-fadeIn ${
              noticeBanner.type === 'error'
                ? 'bg-red-500/15 border-red-500/30 text-red-400'
                : 'bg-emerald-500/15 border-emerald-500/30 text-emerald-500'
            }`}
          >
            <div className="flex items-center gap-2.5 min-w-0">
              {noticeBanner.type === 'error' ? (
                <AlertTriangle className="w-4 h-4 shrink-0" />
              ) : (
                <CheckCircle2 className="w-4 h-4 shrink-0" />
              )}
              <span>{noticeBanner.text}</span>
            </div>
            <button
              type="button"
              onClick={() => setNoticeBanner(null)}
              className="opacity-70 hover:opacity-100 shrink-0"
            >
              <X className="w-4 h-4" />
            </button>
          </div>
        )}

        {loadError && (
          <div className="flex items-center justify-between gap-3 px-4 py-3 rounded-xl bg-red-500/15 border border-red-500/30 text-red-400 text-xs">
            <div className="flex items-center gap-2">
              <AlertTriangle className="w-4 h-4 shrink-0" />
              <span>{loadError}</span>
            </div>
            <button
              type="button"
              onClick={loadProjects}
              className="px-3 py-1 rounded-lg bg-red-500/20 hover:bg-red-500/30 font-semibold transition"
            >
              Retry
            </button>
          </div>
        )}

        {/* FILTER TABS, SEARCH, SORT & BULK SELECT TOOLBAR */}
        <div className="space-y-3">
          <div className="flex flex-wrap items-center justify-between gap-3">
            {/* Status Filter Pills */}
            <div className="flex flex-wrap items-center gap-2">
              {filterPills.map((pill) => {
                const active = statusFilter === pill.id;
                return (
                  <button
                    key={pill.id}
                    type="button"
                    onClick={() => setStatusFilter(pill.id)}
                    className={`flex items-center gap-2 px-3 py-1.5 rounded-xl text-xs font-semibold border transition ${
                      active
                        ? 'bg-[var(--accent-primary)] text-[var(--accent-foreground)] border-[var(--accent-primary)] shadow-sm'
                        : 'bg-[var(--bg-card)] text-[var(--text-secondary)] hover:text-[var(--text-primary)] border-[var(--border-subtle)] hover:border-[var(--accent-primary)]/40'
                    }`}
                  >
                    <span>{pill.label}</span>
                    <span
                      className={`px-1.5 py-0.5 rounded-md text-[10px] font-bold tabular-nums ${
                        active
                          ? 'bg-black/20 text-current'
                          : 'bg-[var(--bg-input)] text-[var(--text-secondary)]'
                      }`}
                    >
                      {pill.count}
                    </span>
                  </button>
                );
              })}
            </div>

            <button
              type="button"
              onClick={onOpenUploadModal}
              className="flex items-center justify-center gap-2 px-4 py-2 bg-[var(--accent-primary)] hover:opacity-90 text-[var(--accent-foreground)] font-bold text-xs rounded-xl shadow-md transition"
            >
              <Video className="w-4 h-4" />
              <span>New Ingestion</span>
            </button>
          </div>

          <div className="flex flex-col sm:flex-row items-stretch sm:items-center justify-between gap-3">
            <div className="flex flex-1 flex-wrap items-center gap-2.5">
              {/* Search Box */}
              <div className="relative flex-1 min-w-[220px] max-w-sm">
                <Search className="w-4 h-4 text-[var(--text-secondary)] absolute left-3 top-1/2 -translate-y-1/2" />
                <input
                  type="text"
                  placeholder="Search recordings or filenames..."
                  value={searchQuery}
                  onChange={(e) => setSearchQuery(e.target.value)}
                  className="w-full bg-[var(--bg-card)] border border-[var(--border-subtle)] rounded-xl pl-9 pr-4 py-2 text-xs text-[var(--text-primary)] placeholder-[var(--text-secondary)] focus:outline-none focus:border-[var(--accent-primary)] transition"
                />
              </div>

              {/* Select All Visible Toggle */}
              {filteredAndSortedProjects.length > 0 && (
                <button
                  type="button"
                  onClick={toggleSelectAllVisible}
                  className={`flex items-center gap-2 px-3 py-2 rounded-xl border text-xs font-semibold transition ${
                    allVisibleSelected
                      ? 'bg-[var(--accent-subtle)] border-[var(--accent-primary)] text-[var(--accent-primary)]'
                      : 'bg-[var(--bg-card)] border-[var(--border-subtle)] text-[var(--text-secondary)] hover:text-[var(--text-primary)]'
                  }`}
                >
                  {allVisibleSelected ? (
                    <CheckSquare className="w-4 h-4" />
                  ) : (
                    <Square className="w-4 h-4" />
                  )}
                  <span>Select All Visible ({filteredAndSortedProjects.length})</span>
                </button>
              )}
            </div>

            {/* Sort Selector */}
            <div className="flex items-center gap-2 self-end sm:self-auto">
              <ArrowDownUp className="w-3.5 h-3.5 text-[var(--text-secondary)]" />
              <select
                aria-label="Sort projects"
                value={sortBy}
                onChange={(e) => setSortBy(e.target.value as SortKey)}
                className="bg-[var(--bg-card)] border border-[var(--border-subtle)] rounded-xl px-3 py-2 text-xs font-medium text-[var(--text-primary)] focus:outline-none focus:border-[var(--accent-primary)]"
              >
                <option value="newest">Newest First</option>
                <option value="most_clips">Most Clips</option>
                <option value="largest_disk">Largest Disk Size</option>
                <option value="longest_duration">Longest Duration</option>
              </select>
            </div>
          </div>
        </div>

        {/* VAULT CARDS GRID */}
        {loading ? (
          <div className="flex flex-col items-center justify-center py-24 space-y-3">
            <RefreshCw className="w-8 h-8 text-[var(--accent-primary)] animate-spin" />
            <span className="text-xs text-[var(--text-secondary)]">
              Scanning local Project Vault and calculating SSD footprints...
            </span>
          </div>
        ) : filteredAndSortedProjects.length === 0 ? (
          <div className="text-center py-20 bg-[var(--bg-card)] border border-dashed border-[var(--border-subtle)] rounded-2xl p-8">
            <Folder className="w-12 h-12 text-[var(--text-secondary)] opacity-50 mx-auto mb-3" />
            <h3 className="text-sm font-bold text-[var(--text-primary)] mb-1">
              {projectsData.projects.length === 0 ? 'Project Vault Is Empty' : 'No Matching Projects'}
            </h3>
            <p className="text-xs text-[var(--text-secondary)] max-w-md mx-auto mb-4">
              {searchQuery || statusFilter !== 'all'
                ? 'Try clearing your status filter or search query.'
                : 'Upload a local recording or paste a YouTube URL to build your first viral clip project.'}
            </p>
            <div className="flex items-center justify-center gap-2">
              {(searchQuery || statusFilter !== 'all') && (
                <button
                  type="button"
                  onClick={() => {
                    setSearchQuery('');
                    setStatusFilter('all');
                  }}
                  className="px-4 py-2 bg-[var(--bg-surface)] border border-[var(--border-subtle)] text-[var(--text-primary)] font-semibold text-xs rounded-xl hover:border-[var(--accent-primary)]/40 transition"
                >
                  Reset Filters
                </button>
              )}
              <button
                type="button"
                onClick={onOpenUploadModal}
                className="px-4 py-2 bg-[var(--accent-primary)] text-[var(--accent-foreground)] font-bold text-xs rounded-xl hover:opacity-90 transition"
              >
                Ingest First Video
              </button>
            </div>
          </div>
        ) : (
          <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-5">
            {filteredAndSortedProjects.map((proj) => {
              const clipsCount = getClipCount(proj);
              const isSelected = selectedIds.has(proj.id);
              const isDeleting = deletingId === proj.id;
              const isStarting = startingId === proj.id;
              const statusLower = String(proj.status || '').toLowerCase();
              const isAnalyzed = clipsCount > 0 || statusLower === 'analyzed' || statusLower === 'finished' || statusLower === 'ready';
              const isInFlight = ['processing', 'analyzing', 'proxy_ready', 'queued'].includes(statusLower);
              const isAwaitingKey = statusLower === 'awaiting_api_key';
              const isFailed = statusLower === 'error' || statusLower === 'failed' || statusLower === 'cancelled';

              const showRealThumb = Boolean(
                !proj.source_missing && proj.thumbnail_url && !brokenThumbs.has(proj.id)
              );
              const resLabel = formatResolutionBadge(proj.resolution_w, proj.resolution_h);

              const statusBadgeText = isAnalyzed
                ? `ANALYZED · ${clipsCount} ${clipsCount === 1 ? 'CLIP' : 'CLIPS'}`
                : isAwaitingKey
                ? 'AWAITING API KEY'
                : statusLower.toUpperCase();

              const statusBadgeClasses = isAnalyzed
                ? 'bg-emerald-500/85 text-white border-emerald-300/40'
                : isFailed || isAwaitingKey
                ? 'bg-red-500/85 text-white border-red-300/40'
                : isInFlight
                ? 'bg-[var(--accent-primary)] text-[var(--accent-foreground)] border-black/15'
                : 'bg-black/75 text-white border-white/20';

              const isOfflineBadge = proj.ai_mode === 'offline';

              return (
                <div
                  key={proj.id}
                  className={`group bg-[var(--bg-card)] rounded-2xl overflow-hidden flex flex-col justify-between transition-all duration-200 hover:shadow-xl border ${
                    isSelected
                      ? 'border-[var(--accent-primary)] ring-2 ring-[var(--accent-primary)]/30'
                      : 'border-[var(--border-subtle)] hover:border-[var(--accent-primary)]/50'
                  }`}
                >
                  {/* 16:9 VISUAL THUMBNAIL BANNER */}
                  <div className="relative aspect-video w-full bg-[var(--bg-input)] border-b border-[var(--border-subtle)] overflow-hidden">
                    {showRealThumb ? (
                      <>
                        <img
                          src={proj.thumbnail_url!}
                          alt={proj.title}
                          loading="lazy"
                          onError={() =>
                            setBrokenThumbs((prev) => {
                              const next = new Set(prev);
                              next.add(proj.id);
                              return next;
                            })
                          }
                          className="w-full h-full object-cover transition-transform duration-300 group-hover:scale-[1.03]"
                        />
                        <div className="absolute inset-0 bg-gradient-to-t from-black/80 via-black/10 to-black/50 pointer-events-none" />
                      </>
                    ) : (
                      <div className="w-full h-full flex flex-col items-center justify-center gap-2 bg-[var(--bg-surface)] px-4 text-center">
                        <div className="w-10 h-10 rounded-xl bg-[var(--bg-card)] border border-[var(--border-subtle)] flex items-center justify-center text-[var(--text-secondary)]">
                          <Film className="w-5 h-5 opacity-70" />
                        </div>
                        <span className="px-2.5 py-1 rounded-lg text-[10px] font-bold bg-[var(--bg-card)] border border-[var(--border-subtle)] text-[var(--text-secondary)]">
                          Missing / Test File · 0 B
                        </span>
                      </div>
                    )}

                    {/* Top-Left: Bulk Checkbox + AI Engine Pill */}
                    <div className="absolute top-2.5 left-2.5 flex items-center gap-1.5 z-10">
                      <button
                        type="button"
                        onClick={(e) => toggleSelectProject(proj.id, e)}
                        aria-label={`Select ${proj.title}`}
                        className={`w-6 h-6 rounded-lg border flex items-center justify-center backdrop-blur-md transition ${
                          isSelected
                            ? 'bg-[var(--accent-primary)] border-[var(--accent-primary)] text-[var(--accent-foreground)]'
                            : 'bg-black/65 border-white/25 text-white hover:border-white/60'
                        }`}
                      >
                        {isSelected && <Check className="w-3.5 h-3.5 stroke-[3]" />}
                      </button>

                      <span
                        className={`px-2 py-0.5 rounded-md text-[10px] font-bold uppercase tracking-wide backdrop-blur-md border flex items-center gap-1 ${
                          isOfflineBadge
                            ? 'bg-sky-950/80 text-sky-200 border-sky-400/35'
                            : 'bg-black/75 text-amber-300 border-amber-400/40'
                        }`}
                      >
                        {!isOfflineBadge && <Sparkles className="w-2.5 h-2.5" />}
                        {isOfflineBadge ? 'Offline AI' : 'Gemini'}
                      </span>
                    </div>

                    {/* Top-Right: Status Badge */}
                    <span
                      className={`absolute top-2.5 right-2.5 px-2 py-0.5 rounded-md text-[10px] font-extrabold uppercase tracking-wider border backdrop-blur-md z-10 ${statusBadgeClasses}`}
                    >
                      {statusBadgeText}
                    </span>

                    {/* Bottom-Left: Resolution Pill */}
                    {resLabel && (
                      <span className="absolute bottom-2.5 left-2.5 px-2 py-0.5 rounded-md text-[10px] font-bold bg-black/75 text-white/95 border border-white/15 backdrop-blur-md tabular-nums">
                        {resLabel}
                      </span>
                    )}

                    {/* Bottom-Right: Duration Badge */}
                    <span className="absolute bottom-2.5 right-2.5 px-2 py-0.5 rounded-md text-[10px] font-bold bg-black/80 text-white border border-white/15 backdrop-blur-md tabular-nums">
                      {formatDurationBadge(proj.duration)}
                    </span>
                  </div>

                  {/* CARD DETAILS & CONTEXT-AWARE ACTION BAR */}
                  <div className="p-4 flex-1 flex flex-col justify-between gap-3">
                    <div className="min-w-0 space-y-1">
                      <h3
                        title={proj.title}
                        className="text-sm font-bold text-[var(--text-primary)] truncate group-hover:text-[var(--accent-primary)] transition-colors"
                      >
                        {proj.title}
                      </h3>
                      <p
                        title={`${proj.source_filename || 'source.mp4'} • ${
                          proj.disk_size_formatted || '0 B'
                        } • ${formatDateCompact(proj.created_at)}`}
                        className="text-[11px] text-[var(--text-secondary)] truncate tabular-nums"
                      >
                        {proj.source_filename || 'source.mp4'} • {proj.disk_size_formatted || '0 B'} •{' '}
                        {formatDateCompact(proj.created_at)}
                      </p>
                      {(isFailed || isAwaitingKey) && proj.error_message && (
                        <p
                          title={proj.error_message}
                          className="text-[11px] text-red-400 truncate pt-0.5"
                        >
                          {proj.error_message}
                        </p>
                      )}
                    </div>

                    {/* Smart Action Row */}
                    <div className="flex items-center gap-2 pt-3 border-t border-[var(--border-subtle)]">
                      {isAnalyzed ? (
                        <button
                          type="button"
                          onClick={() => onSelectProjectForEditor(proj.id)}
                          className="flex-1 flex items-center justify-center gap-1.5 px-3 py-2 bg-[var(--accent-primary)] hover:opacity-90 text-[var(--accent-foreground)] font-bold text-xs rounded-xl shadow-sm transition active:scale-[0.98]"
                        >
                          <span>
                            Open Studio ({clipsCount} {clipsCount === 1 ? 'Clip' : 'Clips'})
                          </span>
                          <ArrowRight className="w-3.5 h-3.5" />
                        </button>
                      ) : isAwaitingKey ? (
                        <button
                          type="button"
                          onClick={() => setKeyTargetProject(proj)}
                          disabled={isStarting}
                          className="flex-1 flex items-center justify-center gap-1.5 px-3 py-2 bg-[var(--accent-primary)] hover:opacity-90 text-[var(--accent-foreground)] font-bold text-xs rounded-xl shadow-sm transition disabled:opacity-60"
                        >
                          <Key className="w-3.5 h-3.5" />
                          <span>Add API Key &amp; Run</span>
                        </button>
                      ) : isFailed ? (
                        <button
                          type="button"
                          onClick={() => handleStartOrRetry(proj)}
                          disabled={isStarting}
                          className="flex-1 flex items-center justify-center gap-1.5 px-3 py-2 bg-[var(--bg-surface)] hover:bg-[var(--card-hover)] border border-[var(--accent-primary)]/50 text-[var(--accent-primary)] font-bold text-xs rounded-xl transition disabled:opacity-60"
                        >
                          {isStarting ? (
                            <Loader2 className="w-3.5 h-3.5 animate-spin" />
                          ) : (
                            <RotateCcw className="w-3.5 h-3.5" />
                          )}
                          <span>Retry Pipeline</span>
                        </button>
                      ) : isInFlight ? (
                        <button
                          type="button"
                          onClick={() => handleOpenLogs(proj)}
                          className="flex-1 flex items-center justify-center gap-1.5 px-3 py-2 bg-[var(--accent-subtle)] border border-[var(--accent-primary)]/40 text-[var(--accent-primary)] font-bold text-xs rounded-xl hover:opacity-90 transition"
                        >
                          <Loader2 className="w-3.5 h-3.5 animate-spin" />
                          <span>Live Progress →</span>
                        </button>
                      ) : (
                        <button
                          type="button"
                          onClick={() => handleStartOrRetry(proj)}
                          disabled={isStarting}
                          className="flex-1 flex items-center justify-center gap-1.5 px-3 py-2 bg-[var(--accent-primary)] hover:opacity-90 text-[var(--accent-foreground)] font-bold text-xs rounded-xl shadow-sm transition active:scale-[0.98] disabled:opacity-60"
                        >
                          {isStarting ? (
                            <Loader2 className="w-3.5 h-3.5 animate-spin" />
                          ) : (
                            <Play className="w-3.5 h-3.5 fill-current" />
                          )}
                          <span>Start Analysis</span>
                        </button>
                      )}

                      {/* Logs Secondary Button */}
                      <button
                        type="button"
                        onClick={() => handleOpenLogs(proj)}
                        title="Open Pipeline Progress console & logs"
                        className="flex items-center justify-center gap-1.5 px-2.5 py-2 bg-[var(--bg-input)] hover:border-[var(--accent-primary)]/40 text-[var(--text-primary)] font-medium text-xs rounded-xl border border-[var(--border-subtle)] transition"
                      >
                        <Terminal className="w-3.5 h-3.5 text-[var(--text-secondary)]" />
                        <span>Logs</span>
                      </button>

                      {/* Delete Secondary Icon Button */}
                      <button
                        type="button"
                        onClick={(e) => handleDeleteProject(proj, e)}
                        disabled={isDeleting}
                        title="Delete project and reclaim SSD space"
                        className="p-2 bg-[var(--bg-input)] hover:bg-red-500/15 text-[var(--text-secondary)] hover:text-red-400 rounded-xl border border-[var(--border-subtle)] hover:border-red-500/40 transition disabled:opacity-50"
                      >
                        <Trash2 className={`w-4 h-4 ${isDeleting ? 'animate-spin' : ''}`} />
                      </button>
                    </div>
                  </div>
                </div>
              );
            })}
          </div>
        )}
      </div>

      {/* FLOATING BULK ACTION BAR */}
      {selectedIds.size > 0 && (
        <div className="fixed bottom-5 left-1/2 -translate-x-1/2 z-40 px-4 py-3 rounded-2xl bg-[var(--bg-surface)] border border-[var(--accent-primary)]/60 shadow-2xl flex items-center gap-4 text-xs animate-fadeIn">
          <div className="flex items-center gap-2 font-semibold text-[var(--text-primary)] tabular-nums">
            <span className="px-2 py-0.5 rounded-md bg-[var(--accent-primary)] text-[var(--accent-foreground)] font-extrabold">
              {selectedIds.size} selected
            </span>
            <span className="text-[var(--text-secondary)]">·</span>
            <span>Reclaim {formatBytesLocal(selectedBytes)}</span>
          </div>

          <div className="flex items-center gap-2">
            <button
              type="button"
              onClick={handleBulkDelete}
              disabled={isBulkDeleting}
              className="flex items-center gap-1.5 px-3.5 py-1.5 rounded-xl bg-red-600 hover:bg-red-500 text-white font-bold transition disabled:opacity-60"
            >
              {isBulkDeleting ? (
                <Loader2 className="w-3.5 h-3.5 animate-spin" />
              ) : (
                <Trash2 className="w-3.5 h-3.5" />
              )}
              <span>Delete Selected</span>
            </button>
            <button
              type="button"
              onClick={() => setSelectedIds(new Set())}
              className="px-2.5 py-1.5 rounded-xl bg-[var(--bg-card)] border border-[var(--border-subtle)] text-[var(--text-secondary)] hover:text-[var(--text-primary)] font-medium transition"
            >
              Clear
            </button>
          </div>
        </div>
      )}

      {/* AI Key / Engine Recovery Modal for AWAITING_API_KEY Cards */}
      <AIEngineModal
        isOpen={Boolean(keyTargetProject)}
        onClose={() => setKeyTargetProject(null)}
        title={
          keyTargetProject
            ? `Resume "${keyTargetProject.title}"`
            : 'Choose AI Analysis Engine'
        }
        subtitle="Add your Gemini API key or switch to 100% local Offline Multimodal AI and resume immediately."
        currentMode={isOfflineMultimodal ? 'offline' : 'gemini'}
        geminiConfigured={geminiConfigured}
        onChosen={async (mode) => {
          const target = keyTargetProject;
          setKeyTargetProject(null);
          if (target) {
            await handleStartOrRetry(target, mode === 'offline');
          }
        }}
      />

      {/* Fallback Inline Logs Modal (only used when parent did not supply onViewProgress) */}
      {selectedLogProject && (
        <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/75 backdrop-blur-sm p-4 animate-fadeIn">
          <div className="bg-[var(--bg-surface)] border border-[var(--border-subtle)] rounded-2xl w-full max-w-2xl max-h-[85vh] flex flex-col shadow-2xl overflow-hidden text-[var(--text-primary)]">
            <div className="flex items-center justify-between px-6 py-4 border-b border-[var(--border-subtle)] bg-[var(--bg-card)]">
              <div className="flex items-center gap-3 min-w-0">
                <div className="p-2 rounded-lg bg-[var(--bg-input)] border border-[var(--border-subtle)] text-[var(--accent-primary)]">
                  <Terminal className="w-5 h-5" />
                </div>
                <div className="min-w-0">
                  <h3 className="text-sm font-bold text-[var(--text-primary)]">
                    Pipeline Execution Progress
                  </h3>
                  <p className="text-xs text-[var(--text-secondary)] truncate max-w-md">
                    {selectedLogProject.title}
                  </p>
                </div>
              </div>
              <button
                type="button"
                onClick={() => setSelectedLogProject(null)}
                className="p-1.5 rounded-lg text-[var(--text-secondary)] hover:text-[var(--text-primary)] hover:bg-[var(--bg-input)] transition"
              >
                <X className="w-5 h-5" />
              </button>
            </div>

            <div className="flex-1 overflow-y-auto p-5 bg-[var(--bg-input)] font-mono text-xs space-y-2">
              {logsLoading ? (
                <div className="flex items-center justify-center py-16 text-[var(--text-secondary)] gap-2">
                  <RefreshCw className="w-4 h-4 animate-spin text-[var(--accent-primary)]" />
                  <span>Loading timestamped activity stream...</span>
                </div>
              ) : projectLogs.length === 0 ? (
                <div className="text-[var(--text-secondary)] text-center py-12">
                  No logs recorded for this project.
                </div>
              ) : (
                projectLogs.map((log, idx) => (
                  <div
                    key={idx}
                    className="flex items-start gap-3 py-1.5 border-b border-[var(--border-subtle)]"
                  >
                    <span className="text-[10px] text-[var(--text-secondary)] shrink-0 pt-0.5 tabular-nums">
                      {log.timestamp ? new Date(log.timestamp).toLocaleTimeString() : '--:--:--'}
                    </span>
                    {log.stage && (
                      <span className="text-[9px] font-bold px-1.5 py-0.5 rounded border border-[var(--border-subtle)] bg-[var(--bg-card)] text-[var(--accent-primary)] uppercase shrink-0">
                        {log.stage}
                      </span>
                    )}
                    <span className="text-[var(--text-primary)] flex-1 leading-relaxed">
                      {log.message}
                    </span>
                  </div>
                ))
              )}
            </div>
          </div>
        </div>
      )}
    </div>
  );
};
