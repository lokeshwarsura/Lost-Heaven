import React, { useState } from 'react';
import { Upload, Link2, Sliders, Type, Film, X, Loader2, Layers, Search, Undo2, Redo2, Keyboard } from 'lucide-react';
import { StudioProfile } from '../types';

interface TopBarProps {
  projectTitle?: string;
  activeStep: 1 | 2 | 3;
  onSelectStep: (step: 1 | 2 | 3) => void;
  onUploadVideo: (file: File) => void;
  onImportYouTube: (url: string) => void;
  isUploading: boolean;
  profiles?: StudioProfile[];
  onApplyProfile?: (profile: StudioProfile) => void;
  onSaveProfile?: (name: string) => void;
  onOpenTranscriptSearch?: () => void;
  canUndo?: boolean;
  canRedo?: boolean;
  onUndo?: () => void;
  onRedo?: () => void;
  onOpenShortcuts?: () => void;
}

export const TopBar: React.FC<TopBarProps> = ({
  projectTitle,
  activeStep,
  onSelectStep,
  onUploadVideo,
  onImportYouTube,
  isUploading,
  profiles = [],
  onApplyProfile,
  onSaveProfile,
  onOpenTranscriptSearch,
  canUndo = false,
  canRedo = false,
  onUndo,
  onRedo,
  onOpenShortcuts
}) => {
  const [ytUrl, setYtUrl] = useState('');
  const [showYtPopover, setShowYtPopover] = useState(false);
  const [showSaveProf, setShowSaveProf] = useState(false);
  const [profName, setProfName] = useState('');

  const handleYtSubmit = (e: React.FormEvent) => {
    e.preventDefault();
    if (ytUrl.trim()) {
      onImportYouTube(ytUrl.trim());
      setYtUrl('');
      setShowYtPopover(false);
    }
  };

  const steps: { num: 1 | 2 | 3; label: string; shortLabel: string; icon: React.ComponentType<{ className?: string }> }[] = [
    { num: 1, label: '1. Setup & Framing', shortLabel: '1. Setup', icon: Sliders },
    { num: 2, label: '2. Captions & Voice', shortLabel: '2. Captions', icon: Type },
    { num: 3, label: '3. Color & Video', shortLabel: '3. Video', icon: Film }
  ];

  return (
    <header className="h-[52px] shrink-0 border-b border-[var(--border-subtle)] bg-[var(--bg-surface)] px-4 flex items-center justify-between gap-3 z-40 relative">
      {/* Left: Studio Badge + Active Project Title */}
      <div className="flex items-center gap-2.5 min-w-0">
        <div className="flex items-center gap-1.5 px-2.5 py-1 rounded-lg bg-[var(--bg-card)] border border-[var(--border-subtle)] shrink-0">
          <span className="w-5 h-4 rounded bg-[var(--accent-primary)] text-[var(--accent-foreground)] flex items-center justify-center font-black text-[9px] tracking-tighter">
            LH
          </span>
          <span className="font-bold text-xs tracking-tight text-[var(--text-primary)]">Lost Heaven</span>
          <span className="text-[9px] uppercase font-bold tracking-wider px-1 py-0.5 rounded bg-[var(--bg-input)] text-[var(--accent-primary)] border border-[var(--border-subtle)]">
            Studio
          </span>
        </div>

        {projectTitle && (
          <>
            <span className="text-[var(--border-subtle)] select-none">/</span>
            <span
              title={projectTitle}
              className="text-xs font-medium text-[var(--text-secondary)] truncate max-w-[140px] lg:max-w-[220px] xl:max-w-[280px]"
            >
              {projectTitle}
            </span>
          </>
        )}
      </div>

      {/* Center: Pill-Style 3-Step Segmented Switcher */}
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
              className={`flex items-center gap-1.5 px-2.5 lg:px-3.5 py-1 rounded-lg text-xs font-semibold transition whitespace-nowrap ${
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

      {/* Right: Studio Profile Switcher, Transcript Search, Import URL & Upload Video */}
      <div className="flex items-center gap-2 shrink-0 relative">
        {profiles.length > 0 && onApplyProfile && (
          <div className="hidden lg:flex items-center gap-1">
            <select
              aria-label="Apply Studio Profile"
              defaultValue=""
              onChange={(e) => {
                const chosen = profiles.find((p) => p.id === e.target.value);
                if (chosen) onApplyProfile(chosen);
                e.target.value = '';
              }}
              className="h-8 px-2 rounded-lg text-[11px] font-semibold bg-[var(--bg-card)] border border-[var(--border-subtle)] text-[var(--text-primary)] hover:border-[var(--accent-primary)]/50 outline-none cursor-pointer"
              title="Apply reusable Studio Profile preset to current clip"
            >
              <option value="" disabled>
                Profiles ({profiles.length}) ▾
              </option>
              {profiles.map((p) => (
                <option key={p.id} value={p.id}>
                  {p.name}
                </option>
              ))}
            </select>
            {onSaveProfile && (
              <button
                type="button"
                onClick={() => setShowSaveProf((v) => !v)}
                className="h-8 px-2 rounded-lg text-[11px] font-semibold bg-[var(--bg-card)] border border-[var(--border-subtle)] text-[var(--text-secondary)] hover:text-[var(--accent-primary)] transition"
                title="Save current settings as reusable Studio Profile"
              >
                <Layers className="w-3.5 h-3.5" />
              </button>
            )}
          </div>
        )}

        {(onUndo || onRedo || onOpenShortcuts) && (
          <div className="hidden md:flex items-center gap-1 border-r border-[var(--border-subtle)] pr-2 mr-0.5">
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
            className="flex items-center gap-1.5 h-8 px-2.5 rounded-lg text-xs font-medium text-[var(--text-primary)] bg-[var(--bg-card)] hover:border-[var(--accent-primary)]/50 border border-[var(--border-subtle)] transition"
            title="Search full project transcript & create custom manual clip"
          >
            <Search className="w-3.5 h-3.5 text-[var(--accent-primary)]" />
            <span className="hidden xl:inline">+ Manual Clip</span>
          </button>
        )}

        <button
          type="button"
          onClick={() => setShowYtPopover((v) => !v)}
          disabled={isUploading}
          className="flex items-center gap-1.5 h-8 px-2.5 rounded-lg text-xs font-medium text-[var(--text-primary)] bg-[var(--bg-card)] hover:border-[var(--accent-primary)]/50 border border-[var(--border-subtle)] transition disabled:opacity-50"
          title="Import YouTube or video URL"
        >
          <Link2 className="w-3.5 h-3.5 text-[var(--accent-primary)]" />
          <span className="hidden sm:inline">Import URL</span>
        </button>

        <label className="flex items-center gap-1.5 h-8 px-3 rounded-lg text-xs font-bold bg-[var(--accent-primary)] text-[var(--accent-foreground)] hover:opacity-90 cursor-pointer transition shadow-sm">
          {isUploading ? (
            <Loader2 className="w-3.5 h-3.5 animate-spin" />
          ) : (
            <Upload className="w-3.5 h-3.5" />
          )}
          <span>{isUploading ? 'Uploading...' : 'Upload Video'}</span>
          <input
            type="file"
            accept="video/*"
            disabled={isUploading}
            onChange={(e) => {
              if (e.target.files?.[0]) {
                onUploadVideo(e.target.files[0]);
              }
            }}
            className="hidden"
          />
        </label>

        {/* Compact Import URL Popover */}
        {showYtPopover && (
          <form
            onSubmit={handleYtSubmit}
            className="absolute right-0 top-[46px] w-80 p-2.5 rounded-xl bg-[var(--bg-surface)] border border-[var(--border-subtle)] shadow-2xl flex items-center gap-1.5 z-50 animate-fadeIn"
          >
            <input
              type="text"
              placeholder="Paste YouTube Shorts / Video URL..."
              value={ytUrl}
              onChange={(e) => setYtUrl(e.target.value)}
              className="flex-1 min-w-0 bg-[var(--bg-input)] border border-[var(--border-subtle)] rounded-lg px-2.5 py-1.5 text-xs text-[var(--text-primary)] placeholder-[var(--text-secondary)] focus:border-[var(--accent-primary)] outline-none font-mono"
              autoFocus
            />
            <button
              type="submit"
              className="bg-[var(--accent-primary)] text-[var(--accent-foreground)] font-bold text-xs px-3 py-1.5 rounded-lg hover:opacity-90 transition shrink-0"
            >
              Fetch
            </button>
            <button
              type="button"
              onClick={() => setShowYtPopover(false)}
              className="p-1 text-[var(--text-secondary)] hover:text-[var(--text-primary)] shrink-0"
              title="Close"
            >
              <X className="w-3.5 h-3.5" />
            </button>
          </form>
        )}

        {/* Compact Save Studio Profile Popover */}
        {showSaveProf && onSaveProfile && (
          <form
            onSubmit={(e) => {
              e.preventDefault();
              if (profName.trim()) {
                onSaveProfile(profName.trim());
                setProfName('');
                setShowSaveProf(false);
              }
            }}
            className="absolute right-0 top-[46px] w-72 p-2.5 rounded-xl bg-[var(--bg-surface)] border border-[var(--border-subtle)] shadow-2xl flex items-center gap-1.5 z-50 animate-fadeIn"
          >
            <input
              type="text"
              placeholder="New profile name..."
              value={profName}
              onChange={(e) => setProfName(e.target.value)}
              className="flex-1 min-w-0 bg-[var(--bg-input)] border border-[var(--border-subtle)] rounded-lg px-2.5 py-1.5 text-xs text-[var(--text-primary)] placeholder-[var(--text-secondary)] focus:border-[var(--accent-primary)] outline-none"
              autoFocus
            />
            <button
              type="submit"
              className="bg-[var(--accent-primary)] text-[var(--accent-foreground)] font-bold text-xs px-3 py-1.5 rounded-lg hover:opacity-90 transition shrink-0"
            >
              Save
            </button>
            <button
              type="button"
              onClick={() => setShowSaveProf(false)}
              className="p-1 text-[var(--text-secondary)] hover:text-[var(--text-primary)] shrink-0"
            >
              <X className="w-3.5 h-3.5" />
            </button>
          </form>
        )}
      </div>
    </header>
  );
};
