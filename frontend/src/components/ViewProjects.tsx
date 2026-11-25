import React, { useState, useEffect } from 'react';
import { 
  Folder, Film, HardDrive, Clock, Calendar, Trash2, 
  Play, ExternalLink, RefreshCw, X, AlertTriangle, 
  CheckCircle2, Sparkles, Terminal, ArrowRight, Video,
  Search
} from 'lucide-react';
import { Project, ProjectsListResponse, ProjectLogItem } from '../types';
import { api } from '../api/client';

interface ViewProjectsProps {
  onSelectProjectForEditor: (projectId: number) => void;
  onOpenUploadModal: () => void;
}

export const ViewProjects: React.FC<ViewProjectsProps> = ({
  onSelectProjectForEditor,
  onOpenUploadModal
}) => {
  const [projectsData, setProjectsData] = useState<ProjectsListResponse>({
    projects: [],
    total_disk_bytes: 0,
    total_disk_formatted: '0 MB',
    projects_count: 0
  });
  const [loading, setLoading] = useState(true);
  const [searchQuery, setSearchQuery] = useState('');
  
  // Progress Log Modal
  const [selectedLogProject, setSelectedLogProject] = useState<Project | null>(null);
  const [projectLogs, setProjectLogs] = useState<ProjectLogItem[]>([]);
  const [logsLoading, setLogsLoading] = useState(false);

  // Delete State
  const [deletingId, setDeletingId] = useState<number | null>(null);
  const [deleteNotice, setDeleteNotice] = useState<string | null>(null);

  useEffect(() => {
    loadProjects();
  }, []);

  const loadProjects = async () => {
    setLoading(true);
    try {
      const data = await api.getProjectsList();
      setProjectsData(data);
    } catch (err) {
      console.error('Failed to load projects list:', err);
    } finally {
      setLoading(false);
    }
  };

  const handleOpenLogs = async (proj: Project) => {
    setSelectedLogProject(proj);
    setLogsLoading(true);
    try {
      const res = await api.getProjectLogs(proj.id);
      setProjectLogs(res.logs || []);
    } catch (err) {
      console.error('Failed to load project logs:', err);
      setProjectLogs([
        {
          timestamp: new Date().toISOString(),
          stage: 'ERROR',
          message: 'Unable to retrieve execution logs for this project.'
        }
      ]);
    } finally {
      setLogsLoading(false);
    }
  };

  const handleDeleteProject = async (proj: Project, e: React.MouseEvent) => {
    e.stopPropagation();
    if (!window.confirm(`Permanently delete "${proj.title}" and free up ${proj.disk_size_formatted || 'disk space'}?`)) {
      return;
    }

    setDeletingId(proj.id);
    try {
      const res = await api.deleteProject(proj.id);
      setDeleteNotice(`Deleted "${proj.title}". Reclaimed ${res.reclaimed_formatted || 'disk space'}.`);
      setTimeout(() => setDeleteNotice(null), 4000);
      await loadProjects();
    } catch (err) {
      console.error('Failed to delete project:', err);
      alert('Error deleting project. Please check backend logs.');
    } finally {
      setDeletingId(null);
    }
  };

  const formatDuration = (seconds?: number) => {
    if (!seconds) return '00:00';
    const mins = Math.floor(seconds / 60);
    const secs = Math.floor(seconds % 60);
    return `${mins.toString().padStart(2, '0')}:${secs.toString().padStart(2, '0')}`;
  };

  const formatDate = (isoString?: string) => {
    if (!isoString) return 'Recent';
    try {
      const d = new Date(isoString);
      return d.toLocaleDateString(undefined, { month: 'short', day: 'numeric', hour: '2-digit', minute: '2-digit' });
    } catch {
      return isoString;
    }
  };

  const filteredProjects = projectsData.projects.filter((p) => 
    p.title.toLowerCase().includes(searchQuery.toLowerCase()) ||
    (p.source_filename && p.source_filename.toLowerCase().includes(searchQuery.toLowerCase()))
  );

  return (
    <div className="flex-1 overflow-y-auto p-6 md:p-8 space-y-8 animate-fadeIn max-w-7xl mx-auto bg-[var(--bg-canvas)] text-[var(--text-primary)] transition-colors">
      {/* Top Header & Storage Telemetry Banner */}
      <div className="flex flex-col md:flex-row md:items-center justify-between gap-4 pb-6 border-b border-[var(--border-subtle)]">
        <div>
          <div className="flex items-center gap-3 mb-1">
            <h1 className="text-2xl md:text-3xl font-extrabold tracking-tight flex items-center gap-3">
              <Folder className="w-7 h-7 text-[var(--accent-primary)]" />
              <span>My clips</span>
            </h1>
            <span className="px-2.5 py-0.5 rounded-full text-xs font-semibold bg-[var(--bg-card)] text-[var(--text-secondary)] border border-[var(--border-subtle)] tabular-nums">
              {projectsData.projects_count} {projectsData.projects_count === 1 ? 'project' : 'projects'}
            </span>
          </div>
          <p className="text-xs md:text-sm text-[var(--text-secondary)]">
            Manage master recordings, inspect pipeline execution logs, and monitor local SSD disk footprint.
          </p>
        </div>

        {/* Global Storage Meter Card */}
        <div className="flex items-center gap-4 bg-[var(--bg-card)] border border-[var(--border-subtle)] rounded-xl px-4 py-3 shadow-sm">
          <div className="p-2.5 rounded-lg bg-[var(--accent-primary)]/15 border border-[var(--accent-primary)]/30 text-[var(--accent-primary)]">
            <HardDrive className="w-5 h-5" />
          </div>
          <div>
            <div className="text-[10px] uppercase font-bold text-[var(--text-secondary)] tracking-wider">
              Total Disk Usage
            </div>
            <div className="text-lg font-bold flex items-center gap-2 tabular-nums">
              <span>{projectsData.total_disk_formatted}</span>
              <span className="text-[10px] font-normal text-emerald-400 bg-emerald-500/10 px-1.5 py-0.5 rounded border border-emerald-500/20">
                Local SSD
              </span>
            </div>
          </div>
          <button 
            onClick={loadProjects}
            title="Refresh library"
            className="p-2 ml-2 rounded-lg text-[var(--text-secondary)] hover:text-[var(--text-primary)] hover:bg-[var(--bg-input)] transition-colors"
          >
            <RefreshCw className={`w-4 h-4 ${loading ? 'animate-spin text-[var(--accent-primary)]' : ''}`} />
          </button>
        </div>
      </div>

      {/* Feedback Banner for Reclaimed Disk */}
      {deleteNotice && (
        <div className="flex items-center gap-3 px-4 py-3 rounded-xl bg-emerald-500/15 border border-emerald-500/30 text-emerald-400 text-xs font-medium animate-fadeIn">
          <CheckCircle2 className="w-4 h-4 flex-shrink-0" />
          <span>{deleteNotice}</span>
        </div>
      )}

      {/* Search & Actions Bar */}
      <div className="flex flex-col sm:flex-row items-center justify-between gap-4">
        <div className="relative w-full sm:w-80">
          <Search className="w-4 h-4 text-[var(--text-secondary)] absolute left-3 top-1/2 -translate-y-1/2" />
          <input
            type="text"
            placeholder="Search projects..."
            value={searchQuery}
            onChange={(e) => setSearchQuery(e.target.value)}
            className="w-full bg-[var(--bg-card)] border border-[var(--border-subtle)] rounded-xl pl-9 pr-4 py-2 text-xs text-[var(--text-primary)] placeholder-[var(--text-secondary)] focus:outline-none focus:border-[var(--accent-primary)] transition-colors"
          />
        </div>

        <div className="flex items-center gap-2 w-full sm:w-auto">
          <button
            onClick={onOpenUploadModal}
            className="flex-1 sm:flex-none flex items-center justify-center gap-2 px-4 py-2 bg-[var(--accent-primary)] hover:opacity-90 text-[var(--accent-foreground)] font-semibold text-xs rounded-xl shadow-md transition-all"
          >
            <Video className="w-4 h-4" />
            New Ingestion
          </button>
        </div>
      </div>

      {/* Projects Grid */}
      {loading ? (
        <div className="flex flex-col items-center justify-center py-24 space-y-3">
          <RefreshCw className="w-8 h-8 text-[var(--accent-primary)] animate-spin" />
          <span className="text-xs text-[var(--text-secondary)]">Calculating master video file sizes and clips...</span>
        </div>
      ) : filteredProjects.length === 0 ? (
        <div className="text-center py-20 bg-[var(--bg-card)] border border-dashed border-[var(--border-subtle)] rounded-2xl p-8">
          <Folder className="w-12 h-12 text-[var(--text-secondary)] opacity-50 mx-auto mb-3" />
          <h3 className="text-sm font-semibold text-[var(--text-primary)] mb-1">No Projects Found</h3>
          <p className="text-xs text-[var(--text-secondary)] max-w-sm mx-auto mb-4">
            {searchQuery ? 'No projects match your query.' : 'Ingest a local video or YouTube URL to begin creating viral shorts.'}
          </p>
          <button
            onClick={onOpenUploadModal}
            className="px-4 py-2 bg-[var(--accent-primary)] text-[var(--accent-foreground)] font-semibold text-xs rounded-xl hover:opacity-90 transition-colors"
          >
            Ingest First Video
          </button>
        </div>
      ) : (
        <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-5">
          {filteredProjects.map((proj) => {
            const isDeleting = deletingId === proj.id;
            const statusColor = 
              proj.status === 'analyzed' || proj.status === 'finished' ? 'bg-emerald-500/15 text-emerald-400 border-emerald-500/30' :
              proj.status === 'analyzing' || proj.status === 'proxy_ready' || proj.status === 'processing' ? 'bg-[var(--accent-primary)]/15 text-[var(--accent-primary)] border-[var(--accent-primary)]/30' :
              proj.status === 'error' ? 'bg-red-500/15 text-red-400 border-red-500/30' :
              'bg-[var(--bg-input)] text-[var(--text-secondary)] border-[var(--border-subtle)]';

            return (
              <div 
                key={proj.id}
                className="group bg-[var(--bg-card)] border border-[var(--border-subtle)] hover:border-[var(--accent-primary)]/50 rounded-2xl p-5 flex flex-col justify-between transition-all duration-200 hover:shadow-xl"
              >
                {/* Header */}
                <div>
                  <div className="flex items-start justify-between gap-3 mb-3">
                    <div className="flex-1 min-w-0">
                      <h3 className="text-sm font-bold text-[var(--text-primary)] truncate group-hover:text-[var(--accent-primary)] transition-colors">
                        {proj.title}
                      </h3>
                      <p className="text-[11px] text-[var(--text-secondary)] truncate font-mono mt-0.5">
                        {proj.source_filename || 'source.mp4'}
                      </p>
                    </div>
                    <span className={`px-2 py-0.5 text-[10px] font-bold rounded-md border uppercase tracking-wider flex-shrink-0 ${statusColor}`}>
                      {proj.status}
                    </span>
                  </div>

                  {/* Metadata Tags */}
                  <div className="grid grid-cols-2 gap-2 mb-4 text-[11px] text-[var(--text-secondary)] tabular-nums">
                    <div className="flex items-center gap-1.5 bg-[var(--bg-input)] border border-[var(--border-subtle)] rounded-lg px-2.5 py-1.5">
                      <Clock className="w-3.5 h-3.5 text-[var(--accent-primary)]" />
                      <span>{formatDuration(proj.duration)}</span>
                    </div>
                    <div className="flex items-center gap-1.5 bg-[var(--bg-input)] border border-[var(--border-subtle)] rounded-lg px-2.5 py-1.5">
                      <Film className="w-3.5 h-3.5 text-[var(--accent-primary)]" />
                      <span>{proj.resolution_w || 1920}x{proj.resolution_h || 1080}</span>
                    </div>
                    <div className="flex items-center gap-1.5 bg-[var(--bg-input)] border border-[var(--border-subtle)] rounded-lg px-2.5 py-1.5">
                      <HardDrive className="w-3.5 h-3.5 text-emerald-400" />
                      <span className="font-semibold text-[var(--text-primary)]">{proj.disk_size_formatted || '0 MB'}</span>
                    </div>
                    <div className="flex items-center gap-1.5 bg-[var(--bg-input)] border border-[var(--border-subtle)] rounded-lg px-2.5 py-1.5">
                      <Sparkles className="w-3.5 h-3.5 text-[var(--accent-primary)]" />
                      <span>{proj.clips_count ?? proj.clip_count ?? 0} clips</span>
                    </div>
                  </div>

                  <div className="text-[10px] text-[var(--text-secondary)] flex items-center gap-1.5 mb-4">
                    <Calendar className="w-3 h-3" />
                    <span>Created {formatDate(proj.created_at)}</span>
                  </div>
                </div>

                {/* 3 Explicit Action Buttons */}
                <div className="flex items-center gap-2 pt-3 border-t border-[var(--border-subtle)]">
                  {/* Button 1: Clips (Loads into Studio Editor) */}
                  <button
                    onClick={() => onSelectProjectForEditor(proj.id)}
                    className="flex-1 flex items-center justify-center gap-1.5 px-3 py-2 bg-[var(--accent-primary)] hover:opacity-90 text-[var(--accent-foreground)] font-bold text-xs rounded-xl shadow-md transition-all active:scale-[0.98]"
                    title="Load clips into Studio Editor"
                  >
                    <Play className="w-3.5 h-3.5 fill-current" />
                    <span>Clips</span>
                  </button>

                  {/* Button 2: Progress (Opens timestamped pipeline logs modal) */}
                  <button
                    onClick={() => handleOpenLogs(proj)}
                    className="flex items-center justify-center gap-1.5 px-3 py-2 bg-[var(--bg-input)] hover:border-[var(--accent-primary)]/40 text-[var(--text-primary)] font-medium text-xs rounded-xl border border-[var(--border-subtle)] transition-colors"
                    title="Inspect pipeline execution progress & logs"
                  >
                    <Terminal className="w-3.5 h-3.5 text-[var(--text-secondary)]" />
                    <span>Progress</span>
                  </button>

                  {/* Button 3: Delete (Reclaims disk space) */}
                  <button
                    onClick={(e) => handleDeleteProject(proj, e)}
                    disabled={isDeleting}
                    className="p-2 bg-[var(--bg-input)] hover:bg-red-500/15 text-[var(--text-secondary)] hover:text-red-400 rounded-xl border border-[var(--border-subtle)] hover:border-red-500/40 transition-colors disabled:opacity-50"
                    title="Delete master video and all renders to reclaim disk space"
                  >
                    <Trash2 className={`w-4 h-4 ${isDeleting ? 'animate-spin' : ''}`} />
                  </button>
                </div>
              </div>
            );
          })}
        </div>
      )}

      {/* Progress & Pipeline Logs Modal */}
      {selectedLogProject && (
        <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/75 backdrop-blur-sm p-4 animate-fadeIn">
          <div className="bg-[var(--bg-surface)] border border-[var(--border-subtle)] rounded-2xl w-full max-w-2xl max-h-[85vh] flex flex-col shadow-2xl overflow-hidden text-[var(--text-primary)]">
            {/* Modal Header */}
            <div className="flex items-center justify-between px-6 py-4 border-b border-[var(--border-subtle)] bg-[var(--bg-card)]">
              <div className="flex items-center gap-3">
                <div className="p-2 rounded-lg bg-[var(--bg-input)] border border-[var(--border-subtle)] text-[var(--accent-primary)]">
                  <Terminal className="w-5 h-5" />
                </div>
                <div>
                  <h3 className="text-sm font-bold text-[var(--text-primary)] flex items-center gap-2">
                    Pipeline Execution Progress
                  </h3>
                  <p className="text-xs text-[var(--text-secondary)] truncate max-w-md">
                    {selectedLogProject.title}
                  </p>
                </div>
              </div>
              <button
                onClick={() => setSelectedLogProject(null)}
                className="p-1.5 rounded-lg text-[var(--text-secondary)] hover:text-[var(--text-primary)] hover:bg-[var(--bg-input)] transition-colors"
              >
                <X className="w-5 h-5" />
              </button>
            </div>

            {/* Modal Body: Console / Log Stream */}
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
                projectLogs.map((log, idx) => {
                  const stageBadgeColor = 
                    log.stage === 'ANALYSIS' ? 'text-[var(--accent-primary)] border-[var(--accent-primary)]/30 bg-[var(--accent-primary)]/10' :
                    log.stage === 'TRANSCRIPTION' ? 'text-sky-400 border-sky-400/30 bg-sky-400/10' :
                    log.stage === 'PROXY' ? 'text-purple-400 border-purple-400/30 bg-purple-400/10' :
                    log.stage === 'RENDERING' || log.stage === 'RENDER' ? 'text-emerald-400 border-emerald-400/30 bg-emerald-400/10' :
                    'text-[var(--text-secondary)] border-[var(--border-subtle)] bg-[var(--bg-card)]';

                  return (
                    <div key={idx} className="flex items-start gap-3 py-1.5 border-b border-[var(--border-subtle)]">
                      <span className="text-[10px] text-[var(--text-secondary)] flex-shrink-0 pt-0.5 tabular-nums">
                        {log.timestamp ? new Date(log.timestamp).toLocaleTimeString() : '--:--:--'}
                      </span>
                      {log.stage && (
                        <span className={`text-[9px] font-bold px-1.5 py-0.5 rounded border uppercase flex-shrink-0 ${stageBadgeColor}`}>
                          {log.stage}
                        </span>
                      )}
                      <span className="text-[var(--text-primary)] flex-1 leading-relaxed">
                        {log.message}
                      </span>
                    </div>
                  );
                })
              )}
            </div>

            {/* Modal Footer */}
            <div className="flex items-center justify-between px-6 py-3 border-t border-[var(--border-subtle)] bg-[var(--bg-card)]">
              <span className="text-[11px] text-[var(--text-secondary)]">
                100% Local Studio Pipeline Architecture
              </span>
              <div className="flex items-center gap-2">
                <button
                  onClick={() => handleOpenLogs(selectedLogProject)}
                  className="px-3 py-1.5 text-xs text-[var(--text-secondary)] hover:text-[var(--text-primary)] bg-[var(--bg-input)] border border-[var(--border-subtle)] rounded-lg transition-colors flex items-center gap-1.5"
                >
                  <RefreshCw className="w-3.5 h-3.5" />
                  Refresh
                </button>
                <button
                  onClick={() => {
                    const id = selectedLogProject.id;
                    setSelectedLogProject(null);
                    onSelectProjectForEditor(id);
                  }}
                  className="px-3 py-1.5 text-xs font-bold text-[var(--accent-foreground)] bg-[var(--accent-primary)] hover:opacity-90 rounded-lg transition-colors flex items-center gap-1.5"
                >
                  <Play className="w-3.5 h-3.5 fill-current" />
                  Open in Editor
                </button>
              </div>
            </div>
          </div>
        </div>
      )}
    </div>
  );
};
