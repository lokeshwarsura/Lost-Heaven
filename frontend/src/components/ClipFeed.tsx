import React, { useMemo, useState } from 'react';
import { Flame, Check, Loader2, Sparkles, AlertTriangle, ArrowUpDown } from 'lucide-react';
import { ViralClip } from '../types';

interface ClipFeedProps {
  clips: ViralClip[];
  selectedClipId: number | null;
  onSelectClip: (clip: ViralClip) => void;
}

export const ClipFeed: React.FC<ClipFeedProps> = ({
  clips,
  selectedClipId,
  onSelectClip
}) => {
  const [sortBy, setSortBy] = useState<'score' | 'timeline'>('score');

  const formatMMSS = (secs: number) => {
    const safe = Math.max(0, Math.floor(secs || 0));
    const m = Math.floor(safe / 60);
    const s = safe % 60;
    return `${String(m).padStart(2, '0')}:${String(s).padStart(2, '0')}`;
  };

  const sortedClips = useMemo(() => {
    const copy = [...clips];
    if (sortBy === 'timeline') {
      copy.sort((a, b) => a.start_time - b.start_time);
    } else {
      copy.sort((a, b) => b.virality_score - a.virality_score);
    }
    return copy;
  }, [clips, sortBy]);

  const getScoreBadge = (score: number, isSelected: boolean) => {
    if (score >= 90 || isSelected) {
      return 'bg-[var(--accent-primary)] text-[var(--accent-foreground)] border-[var(--accent-primary)]';
    }
    if (score >= 80) {
      return 'bg-[var(--accent-subtle)] text-[var(--accent-primary)] border-[var(--accent-primary)]/40';
    }
    return 'bg-[var(--bg-input)] text-[var(--text-primary)] border-[var(--border-subtle)]';
  };

  return (
    <aside className="w-[290px] xl:w-[320px] shrink-0 h-full min-h-0 border-r border-[var(--border-subtle)] bg-[var(--bg-surface)] flex flex-col select-none overflow-hidden">
      {/* Header */}
      <div className="px-3.5 py-2.5 border-b border-[var(--border-subtle)] flex items-center justify-between gap-2 shrink-0">
        <div className="flex items-center gap-2 min-w-0">
          <Flame className="w-4 h-4 text-[var(--accent-primary)] shrink-0" />
          <h2 className="text-xs font-bold tracking-tight text-[var(--text-primary)] truncate tabular-nums">
            Viral Clips ({clips.length})
          </h2>
        </div>

        <div className="flex items-center gap-1 shrink-0">
          <ArrowUpDown className="w-3 h-3 text-[var(--text-secondary)]" />
          <select
            aria-label="Sort clips"
            value={sortBy}
            onChange={(e) => setSortBy(e.target.value as 'score' | 'timeline')}
            className="bg-[var(--bg-input)] border border-[var(--border-subtle)] text-[var(--text-primary)] text-[11px] font-medium rounded-lg px-2 py-1 outline-none focus:border-[var(--accent-primary)] cursor-pointer"
          >
            <option value="score">Score</option>
            <option value="timeline">Timeline</option>
          </select>
        </div>
      </div>

      {/* Scrollable Clip Cards */}
      <div className="flex-1 min-h-0 overflow-y-auto p-3 space-y-2.5">
        {sortedClips.length === 0 ? (
          <div className="h-60 flex flex-col items-center justify-center text-center p-6 text-[var(--text-secondary)]">
            <Sparkles className="w-7 h-7 mb-2 opacity-50" />
            <p className="text-xs font-semibold text-[var(--text-primary)]">No clips detected yet</p>
            <p className="text-[11px] opacity-80 mt-1">
              Upload a video or import a URL to generate ranked clips.
            </p>
          </div>
        ) : (
          sortedClips.map((clip, index) => {
            const isSelected = clip.id === selectedClipId;
            return (
              <div
                key={clip.id}
                onClick={() => onSelectClip(clip)}
                className={`group rounded-xl p-3 border transition-all cursor-pointer space-y-2 ${isSelected
                    ? 'bg-[var(--accent-subtle)] border-[var(--accent-primary)] shadow-sm'
                    : 'bg-[var(--bg-card)] border-[var(--border-subtle)] hover:border-[var(--accent-primary)]/45'
                  }`}
              >
                {/* Top Row: Rank pill, Duration + Range, Virality Score Badge */}
                <div className="flex items-center justify-between gap-2">
                  <div className="flex items-center gap-1.5 min-w-0 tabular-nums">
                    <span className="text-[10px] font-bold px-1.5 py-0.5 rounded-md bg-[var(--bg-input)] border border-[var(--border-subtle)] text-[var(--text-primary)] shrink-0">
                      #{index + 1}
                    </span>
                    <span className="text-[11px] font-mono text-[var(--text-secondary)] truncate">
                      {formatMMSS(clip.duration)} · {formatMMSS(clip.start_time)}–{formatMMSS(clip.end_time)}
                    </span>
                  </div>

                  <span
                    className={`w-7 h-7 rounded-full border text-xs font-extrabold flex items-center justify-center shrink-0 tabular-nums ${getScoreBadge(
                      clip.virality_score,
                      isSelected
                    )}`}
                    title="Virality Score"
                  >
                    {clip.virality_score}
                  </span>
                </div>

                {/* Middle: Bold 2-line Clip Title + 2-line Rationale Excerpt */}
                <div className="space-y-1">
                  <h3 className="text-sm font-semibold text-[var(--text-primary)] line-clamp-2 leading-snug">
                    {clip.hook_headline || clip.selected_title || 'Viral Clip Moment'}
                  </h3>
                  {clip.viral_rationale && (
                    <p className="text-xs text-[var(--text-secondary)] line-clamp-2 leading-relaxed">
                      {clip.viral_rationale}
                    </p>
                  )}
                </div>

                {/* Bottom: Compact Score Strip + Status Pill */}
                <div className="flex items-center justify-between gap-2 pt-2 border-t border-[var(--border-subtle)] text-[11px] tabular-nums">
                  <span className="text-[var(--text-secondary)] font-medium truncate">
                    Hook <strong className="text-[var(--text-primary)]">{clip.hook_score}</strong> · Ret{' '}
                    <strong className="text-[var(--text-primary)]">{clip.retention_score}</strong> · Emo{' '}
                    <strong className="text-[var(--text-primary)]">{clip.emotional_score}</strong>
                  </span>

                  <div className="shrink-0">
                    {(clip.status === 'ready' || clip.render_status === 'completed') && (
                      <span className="inline-flex items-center gap-1 px-2 py-0.5 rounded-full bg-emerald-500/15 text-emerald-400 border border-emerald-500/30 text-[10px] font-bold">
                        <Check className="w-2.5 h-2.5" /> Ready
                      </span>
                    )}
                    {clip.status !== 'ready' && clip.render_status !== 'completed' && (clip.status === 'rendering' || clip.render_status === 'rendering') && (
                      <span className="inline-flex items-center gap-1 px-2 py-0.5 rounded-full bg-[var(--accent-subtle)] text-[var(--accent-primary)] border border-[var(--accent-primary)]/30 text-[10px] font-bold animate-pulse">
                        <Loader2 className="w-2.5 h-2.5 animate-spin" /> {clip.render_progress}%
                      </span>
                    )}
                    {clip.status !== 'ready' && clip.render_status !== 'completed' && (clip.status === 'failed' || clip.render_status === 'failed') && (
                      <span className="inline-flex items-center gap-1 px-2 py-0.5 rounded-full bg-red-500/15 text-red-400 border border-red-500/30 text-[10px] font-bold">
                        <AlertTriangle className="w-2.5 h-2.5" /> Failed
                      </span>
                    )}
                    {clip.status !== 'ready' && clip.render_status !== 'completed' && clip.status !== 'rendering' && clip.render_status !== 'rendering' && clip.status !== 'failed' && clip.render_status !== 'failed' && (
                      <span className="inline-flex items-center px-2 py-0.5 rounded-full bg-[var(--bg-input)] text-[var(--text-secondary)] border border-[var(--border-subtle)] text-[10px] font-medium">
                        Draft
                      </span>
                    )}
                  </div>
                </div>
              </div>
            );
          })
        )}
      </div>
    </aside>
  );
};
