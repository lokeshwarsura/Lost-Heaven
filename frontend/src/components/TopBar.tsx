import React, { useEffect, useRef, useState } from 'react';
import {
  Upload,
  Link2,
  Sliders,
  Type,
  Film,
  X,
  Loader2,
  Layers,
  Search,
  Undo2,
  Redo2,
  Keyboard,
  Sparkles,
  ChevronDown,
  Pencil,
  Check,
  Plus,
} from 'lucide-react';
import { StudioProfile } from '../types';

interface TopBarProps {
  projectTitle?: string;
  onRenameProject?: (title: string) => void;
  activeStep: 1 | 2 | 3;
  onSelectStep: (step: 1 | 2 | 3) => void;
  onUploadVideo: (file: File) => void;
  onImportYouTube: (url: string) => void;
  isUploading: boolean;
  profiles?: StudioProfile[];
  onApplyProfile?: (profile: StudioProfile) => void;
  onSaveProfile?: (name: string) => void;
  onOpenTranscriptSearch?: () => void;
  onOpenAnalysisWorkstation?: () => void;
  canUndo?: boolean;
  canRedo?: boolean;
  onUndo?: () => void;
  onRedo?: () => void;
  onOpenShortcuts?: () => void;
  isOfflineMultimodal?: boolean;
  geminiConfigured?: boolean;
  onOpenEngineModal?: () => void;
}

export const TopBar: React.FC<TopBarProps> = ({
  projectTitle,
  onRenameProject,
  activeStep,
  onSelectStep,
  onUploadVideo,
  onImportYouTube,
  isUploading,
  profiles = [],
  onApplyProfile,
  onSaveProfile,
  onOpenTranscriptSearch,
  onOpenAnalysisWorkstation,
  canUndo = false,
  canRedo = false,
  onUndo,
  onRedo,
  onOpenShortcuts,
  isOfflineMultimodal = false,
  geminiConfigured = false,
  onOpenEngineModal,
}) => {
  const [ytUrl, setYtUrl] = useState('');
  const [showImportMenu, setShowImportMenu] = useState(false);
  const [showProfilesMenu, setShowProfilesMenu] = useState(false);
  const [profName, setProfName] = useState('');
  const [isEditingTitle, setIsEditingTitle] = useState(false);
  const [draftTitle, setDraftTitle] = useState(projectTitle || '');
  const importMenuRef = useRef<HTMLDivElement>(null);
  const profilesMenuRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    setDraftTitle(projectTitle || '');
    setIsEditingTitle(false);
  }, [projectTitle]);

  useEffect(() => {
    const onDocClick = (e: MouseEvent) => {
      const target = e.target as Node;
      if (importMenuRef.current && !importMenuRef.current.contains(target)) {
        setShowImportMenu(false);
      }
      if (profilesMenuRef.current && !profilesMenuRef.current.contains(target)) {
        setShowProfilesMenu(false);
      }
    };
    document.addEventListener('mousedown', onDocClick);
    return () => document.removeEventListener('mousedown', onDocClick);
  }, []);

  const commitTitleRename = () => {
    setIsEditingTitle(false);
    const cleaned = draftTitle.trim();
    if (cleaned && cleaned !== (projectTitle || '').trim()) {
      onRenameProject?.(cleaned);
    } else {
      setDraftTitle(projectTitle || '');
    }
  };

  const handleYtSubmit = (e: React.FormEvent) => {
    e.preventDefault();
    if (ytUrl.trim()) {
      onImportYouTube(ytUrl.trim());
      setYtUrl('');
      setShowImportMenu(false);
    }
  };

  const steps: {
    num: 1 | 2 | 3;
    label: string;
    shortLabel: string;
    icon: React.ComponentType<{ className?: string }>;
  }[] = [
    { num: 1, label: '1. Framing & Cuts', shortLabel: '1. Framing', icon: Sliders },
    { num: 2, label: '2. Captions & Audio', shortLabel: '2. Captions', icon: Type },
    { num: 3, label: '3. Color & Export', shortLabel: '3. Export', icon: Film },
  ];

  return (
    <header className="h-[52px] shrink-0 border-b border-[var(--border-subtle)] bg-[var(--bg-surface)] px-3.5 flex items-center justify-between gap-2.5 z-40 relative select-none">
      {/* Left: LH Studio Badge + Editable Project Title + AI Engine Status Pill */}
      <div className="flex items-center gap-2 min-w-0 flex-1 max-w-[34%]">
        <div className="flex items-center gap-1.5 px-2 py-1 rounded-lg bg-[var(--bg-elevated,var(--bg-card))] border border-[var(--border-subtle)] shrink-0">
          <span className="w-5 h-4 rounded bg-[var(--accent-primary)] text-[var(--accent-foreground)] flex items-center justify-center font-black text-[9px] tracking-tighter">
            LH
          </span>
          <span className="font-bold text-[11px] tracking-tight text-[var(--text-primary)] hidden sm:inline">
            Studio
          </span>
        </div>

        {projectTitle !== undefined && (
          <div className="flex items-center gap-1 min-w-0">
            {isEditingTitle && onRenameProject ? (
              <div className="flex items-center gap-1 min-w-0">
                <input
                  type="text"
                  value={draftTitle}
                  onChange={(e) => setDraftTitle(e.target.value)}
                  onBlur={commitTitleRename}
                  onKeyDown={(e) => {
                    if (e.key === 'Enter') commitTitleRename();
                    if (e.key === 'Escape') {
                      setDraftTitle(projectTitle || '');
                      setIsEditingTitle(false);
                    }
                  }}
                  autoFocus
                  aria-label="Project title"
                  className="h-7 px-2 rounded-lg text-xs font-semibold bg-[var(--bg-input)] border border-[var(--accent-primary)] text-[var(--text-primary)] outline-none w-36 sm:w-48"
                />
                <button
                  type="button"
                  onMouseDown={(e) => e.preventDefault()}
                  onClick={commitTitleRename}
                  className="p-1 rounded bg-[var(--accent-primary)] text-[var(--accent-foreground)]"
                  title="Save project title"
                >
                  <Check className="w-3 h-3" />
                </button>
              </div>
            ) : (
              <button
                type="button"
                onClick={() => onRenameProject && setIsEditingTitle(true)}
                title={onRenameProject ? `${projectTitle || 'Untitled Project'} (Click to rename)` : projectTitle}
                className="group flex items-center gap-1 min-w-0 px-1.5 py-1 rounded-lg hover:bg-[var(--bg-card)] transition text-left"
              >
                <span className="text-xs font-semibold text-[var(--text-primary)] truncate max-w-[130px] lg:max-w-[190px]">
                  {projectTitle || 'Untitled Project'}
                </span>
                {onRenameProject && (
                  <Pencil className="w-3 h-3 text-[var(--text-muted,var(--text-secondary))] opacity-0 group-hover:opacity-100 shrink-0 transition" />
                )}
              </button>
            )}
          </div>
        )}

        {onOpenEngineModal ? (
          <button
            type="button"
            onClick={onOpenEngineModal}
            className={`hidden md:inline-flex items-center gap-1.5 px-2.5 py-1 rounded-full text-[10px] font-bold border transition shrink-0 cursor-pointer hover:brightness-110 whitespace-nowrap ${
              isOfflineMultimodal
                ? 'bg-emerald-500/15 border-emerald-500/35 text-emerald-400'
                : geminiConfigured
                ? 'bg-[var(--accent-subtle)] border-[var(--accent-primary)]/40 text-[var(--accent-primary)]'
                : 'bg-amber-500/15 border-amber-500/40 text-amber-400'
            }`}
            title="Switch between Gemini Cloud AI and Offline Local Multimodal AI"
          >
            <span
              className={`w-1.5 h-1.5 rounded-full ${
                isOfflineMultimodal
                  ? 'bg-emerald-400 animate-pulse'
                  : geminiConfigured
                  ? 'bg-[var(--accent-primary)]'
                  : 'bg-amber-400 animate-pulse'
              }`}
            />
            <span>{isOfflineMultimodal ? 'Offline AI' : geminiConfigured ? 'Gemini' : 'Gemini Setup'}</span>
          </button>
        ) : (
          isOfflineMultimodal && (
            <span className="hidden md:inline-flex items-center gap-1 px-2 py-0.5 rounded-full bg-emerald-500/15 border border-emerald-500/35 text-emerald-400 text-[10px] font-bold shrink-0 whitespace-nowrap">
              <span className="w-1.5 h-1.5 rounded-full bg-emerald-400 animate-pulse" />
              Offline AI
            </span>
          )
        )}
      </div>

      {/* Center: Segmented 3-Step Switcher + AI Analysis Trigger */}
      <div className="flex items-center gap-2 shrink-0">
        <nav
          aria-label="Studio editor steps"
          className="flex items-center p-1 rounded-xl bg-[var(--bg-input)] border border-[var(--border-subtle)] shrink-0"
        >
          {steps.map((s) => {
            const isActive = activeStep === s.num;
            const Icon = s.icon;
            return (
              <button
                key={s.num}
                type="button"
                onClick={() => onSelectStep(s.num)}
                className={`flex items-center gap-1.5 px-2.5 lg:px-3 py-1 rounded-lg text-xs font-semibold transition whitespace-nowrap ${
                  isActive
                    ? 'bg-[var(--accent-primary)] text-[var(--accent-foreground)] shadow-sm'
                    : 'text-[var(--text-secondary)] hover:text-[var(--text-primary)]'
                }`}
              >
                <Icon className="w-3.5 h-3.5 shrink-0" />
                <span className="hidden xl:inline">{s.label}</span>
                <span className="xl:hidden">{s.shortLabel}</span>
              </button>
            );
          })}
        </nav>

        {onOpenAnalysisWorkstation && (
          <button
            type="button"
            onClick={onOpenAnalysisWorkstation}
            className="flex items-center gap-1.5 h-8 px-2.5 rounded-xl text-xs font-semibold text-[var(--text-primary)] bg-[var(--bg-card)] hover:border-[var(--accent-primary)]/50 border border-[var(--border-subtle)] transition shrink-0 whitespace-nowrap"
            title="Open AI Analysis & Best-Frame Workstation"
          >
            <Sparkles className="w-3.5 h-3.5 text-[var(--accent-primary)] shrink-0" />
            <span className="hidden sm:inline">AI Analysis</span>
          </button>
        )}
      </div>

      {/* Right: Undo/Redo, + Manual Clip, Profiles, New Video / Import Dropdown */}
      <div className="flex items-center justify-end gap-1.5 shrink-0 flex-1 max-w-[34%]">
        {(onUndo || onRedo || onOpenShortcuts) && (
          <div className="hidden xl:flex items-center gap-1 border-r border-[var(--border-subtle)] pr-2 mr-0.5 shrink-0">
            {onUndo && (
              <button
                type="button"
                onClick={onUndo}
                disabled={!canUndo}
                className="h-8 w-8 rounded-lg bg-[var(--bg-card)] border border-[var(--border-subtle)] text-[var(--text-primary)] hover:border-[var(--accent-primary)]/50 flex items-center justify-center transition disabled:opacity-35 disabled:cursor-not-allowed"
                title="Undo edit (Ctrl+Z)"
                aria-label="Undo"
              >
                <Undo2 className="w-3.5 h-3.5" />
              </button>
            )}
            {onRedo && (
              <button
                type="button"
                onClick={onRedo}
                disabled={!canRedo}
                className="h-8 w-8 rounded-lg bg-[var(--bg-card)] border border-[var(--border-subtle)] text-[var(--text-primary)] hover:border-[var(--accent-primary)]/50 flex items-center justify-center transition disabled:opacity-35 disabled:cursor-not-allowed"
                title="Redo edit (Ctrl+Y / Ctrl+Shift+Z)"
                aria-label="Redo"
              >
                <Redo2 className="w-3.5 h-3.5" />
              </button>
            )}
            {onOpenShortcuts && (
              <button
                type="button"
                onClick={onOpenShortcuts}
                className="h-8 w-8 rounded-lg bg-[var(--bg-card)] border border-[var(--border-subtle)] text-[var(--text-secondary)] hover:text-[var(--accent-primary)] hover:border-[var(--accent-primary)]/50 flex items-center justify-center transition"
                title="Keyboard Shortcuts (?)"
                aria-label="Keyboard Shortcuts"
              >
                <Keyboard className="w-3.5 h-3.5" />
              </button>
            )}
          </div>
        )}

        {onOpenTranscriptSearch && (
          <button
            type="button"
            onClick={onOpenTranscriptSearch}
            className="flex items-center gap-1.5 h-8 px-2.5 rounded-lg text-xs font-semibold text-[var(--text-primary)] bg-[var(--bg-card)] hover:border-[var(--accent-primary)]/50 border border-[var(--border-subtle)] transition whitespace-nowrap shrink-0"
            title="Search full project transcript & create custom manual clip"
          >
            <Search className="w-3.5 h-3.5 text-[var(--accent-primary)] shrink-0" />
            <span>+ Manual Clip</span>
          </button>
        )}

        {(onApplyProfile || onSaveProfile) && (
          <div className="relative shrink-0" ref={profilesMenuRef}>
            <button
              type="button"
              onClick={() => {
                setShowProfilesMenu((v) => !v);
                setShowImportMenu(false);
              }}
              className="flex items-center gap-1.5 h-8 px-2.5 rounded-lg text-xs font-semibold text-[var(--text-primary)] bg-[var(--bg-card)] hover:border-[var(--accent-primary)]/50 border border-[var(--border-subtle)] transition whitespace-nowrap"
              title="Apply or save Studio Profile presets"
            >
              <Layers className="w-3.5 h-3.5 text-[var(--accent-primary)] shrink-0" />
              <span className="hidden lg:inline">Profiles ({profiles.length})</span>
              <ChevronDown className="w-3 h-3 text-[var(--text-secondary)]" />
            </button>

            {showProfilesMenu && (
              <div className="absolute right-0 top-[42px] w-72 p-3 rounded-xl bg-[var(--bg-surface)] border border-[var(--border-subtle)] shadow-2xl space-y-2.5 z-50 animate-fadeIn">
                <div className="flex items-center justify-between">
                  <span className="text-[11px] font-bold uppercase tracking-wide text-[var(--text-primary)]">
                    Studio Profiles
                  </span>
                  <button
                    type="button"
                    onClick={() => setShowProfilesMenu(false)}
                    className="p-0.5 text-[var(--text-secondary)] hover:text-[var(--text-primary)]"
                  >
                    <X className="w-3.5 h-3.5" />
                  </button>
                </div>

                {profiles.length > 0 && onApplyProfile && (
                  <div className="max-h-44 overflow-y-auto space-y-1">
                    {profiles.map((p) => (
                      <button
                        key={p.id}
                        type="button"
                        onClick={() => {
                          onApplyProfile(p);
                          setShowProfilesMenu(false);
                        }}
                        className="w-full text-left px-2.5 py-1.5 rounded-lg bg-[var(--bg-input)] border border-[var(--border-subtle)] hover:border-[var(--accent-primary)] text-xs font-medium text-[var(--text-primary)] flex items-center justify-between transition"
                      >
                        <span className="truncate">{p.name}</span>
                        <span className="text-[10px] text-[var(--accent-primary)] font-semibold shrink-0">
                          Apply
                        </span>
                      </button>
                    ))}
                  </div>
                )}

                {onSaveProfile && (
                  <form
                    onSubmit={(e) => {
                      e.preventDefault();
                      if (profName.trim()) {
                        onSaveProfile(profName.trim());
                        setProfName('');
                        setShowProfilesMenu(false);
                      }
                    }}
                    className="pt-2 border-t border-[var(--border-subtle)] flex items-center gap-1.5"
                  >
                    <input
                      type="text"
                      placeholder="Save current preset as..."
                      value={profName}
                      onChange={(e) => setProfName(e.target.value)}
                      className="flex-1 min-w-0 bg-[var(--bg-input)] border border-[var(--border-subtle)] rounded-lg px-2.5 py-1.5 text-xs text-[var(--text-primary)] placeholder-[var(--text-secondary)] focus:border-[var(--accent-primary)] outline-none"
                    />
                    <button
                      type="submit"
                      className="bg-[var(--accent-primary)] text-[var(--accent-foreground)] font-bold text-xs px-2.5 py-1.5 rounded-lg hover:opacity-90 transition shrink-0"
                    >
                      Save
                    </button>
                  </form>
                )}
              </div>
            )}
          </div>
        )}

        {/* Unified New Video / Import Dropdown */}
        <div className="relative shrink-0" ref={importMenuRef}>
          <button
            type="button"
            disabled={isUploading}
            onClick={() => {
              setShowImportMenu((v) => !v);
              setShowProfilesMenu(false);
            }}
            className="flex items-center gap-1.5 h-8 px-3 rounded-lg text-xs font-bold bg-[var(--accent-primary)] text-[var(--accent-foreground)] hover:opacity-90 transition shadow-sm whitespace-nowrap disabled:opacity-50"
            title="Upload local video or import from URL"
          >
            {isUploading ? <Loader2 className="w-3.5 h-3.5 animate-spin" /> : <Plus className="w-3.5 h-3.5" />}
            <span>{isUploading ? 'Importing...' : 'New Video / Import'}</span>
            <ChevronDown className="w-3 h-3 opacity-80" />
          </button>

          {showImportMenu && (
            <div className="absolute right-0 top-[42px] w-80 p-3 rounded-xl bg-[var(--bg-surface)] border border-[var(--border-subtle)] shadow-2xl space-y-3 z-50 animate-fadeIn">
              <div className="flex items-center justify-between">
                <span className="text-[11px] font-bold uppercase tracking-wide text-[var(--text-primary)]">
                  Ingest New Footage
                </span>
                <button
                  type="button"
                  onClick={() => setShowImportMenu(false)}
                  className="p-0.5 text-[var(--text-secondary)] hover:text-[var(--text-primary)]"
                >
                  <X className="w-3.5 h-3.5" />
                </button>
              </div>

              <label className="w-full flex items-center justify-center gap-2 py-2 px-3 rounded-lg bg-[var(--accent-primary)] text-[var(--accent-foreground)] font-bold text-xs cursor-pointer hover:opacity-90 transition">
                <Upload className="w-3.5 h-3.5" />
                <span>Upload Local Video File...</span>
                <input
                  type="file"
                  accept="video/*"
                  disabled={isUploading}
                  onChange={(e) => {
                    if (e.target.files?.[0]) {
                      onUploadVideo(e.target.files[0]);
                      setShowImportMenu(false);
                    }
                  }}
                  className="hidden"
                />
              </label>

              <form onSubmit={handleYtSubmit} className="space-y-1.5 pt-2 border-t border-[var(--border-subtle)]">
                <label className="text-[10px] font-semibold text-[var(--text-secondary)] flex items-center gap-1">
                  <Link2 className="w-3 h-3 text-[var(--accent-primary)]" />
                  <span>Import from YouTube / Video URL</span>
                </label>
                <div className="flex items-center gap-1.5">
                  <input
                    type="text"
                    placeholder="https://youtube.com/watch?v=..."
                    value={ytUrl}
                    onChange={(e) => setYtUrl(e.target.value)}
                    className="flex-1 min-w-0 bg-[var(--bg-input)] border border-[var(--border-subtle)] rounded-lg px-2.5 py-1.5 text-xs text-[var(--text-primary)] placeholder-[var(--text-secondary)] focus:border-[var(--accent-primary)] outline-none font-mono"
                  />
                  <button
                    type="submit"
                    className="bg-[var(--bg-elevated,var(--bg-card))] border border-[var(--accent-primary)]/60 text-[var(--accent-primary)] font-bold text-xs px-3 py-1.5 rounded-lg hover:bg-[var(--accent-subtle)] transition shrink-0"
                  >
                    Fetch
                  </button>
                </div>
              </form>
            </div>
          )}
        </div>
      </div>
    </header>
  );
};
