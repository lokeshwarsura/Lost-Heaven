import React from 'react';
import { Zap, PlayCircle, Scissors, ZoomIn } from 'lucide-react';
import { ViralClip } from '../types';

interface TabHooksProps {
  clip: ViralClip;
  onUpdateClip: (updates: Partial<ViralClip>) => void;
}

export const TabHooks: React.FC<TabHooksProps> = ({ clip, onUpdateClip }) => {
  return (
    <div className="space-y-5">
      {/* Visual Hook Banner */}
      <div className="p-4 rounded-xl bg-zinc-900/60 border border-zinc-800 space-y-3">
        <div className="flex items-center justify-between">
          <div className="flex items-center gap-2">
            <Zap className="w-4 h-4 text-amber-400" />
            <span className="text-xs font-bold text-zinc-200">Top Hook Banner (First 3.5s)</span>
          </div>
          <button
            onClick={() => onUpdateClip({ show_hook_banner: !clip.show_hook_banner })}
            className={`w-9 h-5 rounded-full transition-colors relative ${
              clip.show_hook_banner ? 'bg-violet-500' : 'bg-zinc-800'
            }`}
          >
            <div className={`w-4 h-4 rounded-full bg-white transition-transform transform absolute top-0.5 ${
              clip.show_hook_banner ? 'translate-x-4' : 'translate-x-0.5'
            }`} />
          </button>
        </div>

        <p className="text-[11px] text-zinc-400">
          Burns high-contrast headline banner pill in top safe zone for first 3.5s of the clip.
        </p>

        {clip.show_hook_banner && (
          <input
            type="text"
            value={clip.hook_headline}
            onChange={(e) => onUpdateClip({ hook_headline: e.target.value })}
            placeholder="PUNCHY 4-6 WORD HEADLINE..."
            className="w-full px-3 py-2 rounded-lg bg-zinc-950 border border-zinc-800 text-xs font-bold tracking-wide text-amber-300 outline-none uppercase focus:border-violet-500"
          />
        )}
      </div>

      {/* Cold-Open Climax Splicer */}
      <div className="p-4 rounded-xl bg-zinc-900/60 border border-zinc-800 space-y-3">
        <div className="flex items-center justify-between">
          <div className="flex items-center gap-2">
            <PlayCircle className="w-4 h-4 text-violet-400" />
            <span className="text-xs font-bold text-zinc-200">Cold-Open Climax Splicer</span>
          </div>
          <button
            onClick={() => onUpdateClip({ use_cold_open: !clip.use_cold_open })}
            className={`w-9 h-5 rounded-full transition-colors relative ${
              clip.use_cold_open ? 'bg-violet-500' : 'bg-zinc-800'
            }`}
          >
            <div className={`w-4 h-4 rounded-full bg-white transition-transform transform absolute top-0.5 ${
              clip.use_cold_open ? 'translate-x-4' : 'translate-x-0.5'
            }`} />
          </button>
        </div>

        <p className="text-[11px] text-zinc-400">
          Slices the ~2.5s climax quote and prepends it to 00:00 as a shocking cold-open teaser.
        </p>

        {clip.use_cold_open && (
          <div className="bg-zinc-950 p-3 rounded-lg border border-violet-500/30">
            <label className="text-[10px] font-bold text-violet-400 uppercase">Climax Teaser Quote</label>
            <textarea
              value={clip.cold_open_quote}
              onChange={(e) => onUpdateClip({ cold_open_quote: e.target.value })}
              rows={2}
              className="w-full mt-1 bg-transparent text-xs text-zinc-200 outline-none resize-none leading-relaxed"
            />
          </div>
        )}
      </div>

      {/* Retention FX: Auto Trim Dead Air */}
      <div className="p-4 rounded-xl bg-zinc-900/60 border border-zinc-800 flex items-center justify-between">
        <div>
          <div className="flex items-center gap-2">
            <Scissors className="w-4 h-4 text-emerald-400" />
            <span className="text-xs font-bold text-zinc-200">Auto Trim Dead Air</span>
          </div>
          <p className="text-[11px] text-zinc-500 mt-0.5">
            Automatically cuts silent pauses exceeding 0.45s to spike pacing
          </p>
        </div>
        <button
          onClick={() => onUpdateClip({ auto_trim_dead_air: !clip.auto_trim_dead_air })}
          className={`w-9 h-5 rounded-full transition-colors relative ${
            clip.auto_trim_dead_air ? 'bg-violet-500' : 'bg-zinc-800'
          }`}
        >
          <div className={`w-4 h-4 rounded-full bg-white transition-transform transform absolute top-0.5 ${
            clip.auto_trim_dead_air ? 'translate-x-4' : 'translate-x-0.5'
          }`} />
        </button>
      </div>

      {/* Retention FX: Dynamic Punch-Zooms */}
      <div className="p-4 rounded-xl bg-zinc-900/60 border border-zinc-800 flex items-center justify-between">
        <div>
          <div className="flex items-center gap-2">
            <ZoomIn className="w-4 h-4 text-violet-400" />
            <span className="text-xs font-bold text-zinc-200">Dynamic Punch-Zooms</span>
          </div>
          <p className="text-[11px] text-zinc-500 mt-0.5">
            Applies subtle rhythmic 1.10x zoom pulses on key punchlines
          </p>
        </div>
        <button
          onClick={() => onUpdateClip({ dynamic_punch_zoom: !clip.dynamic_punch_zoom })}
          className={`w-9 h-5 rounded-full transition-colors relative ${
            clip.dynamic_punch_zoom ? 'bg-violet-500' : 'bg-zinc-800'
          }`}
        >
          <div className={`w-4 h-4 rounded-full bg-white transition-transform transform absolute top-0.5 ${
            clip.dynamic_punch_zoom ? 'translate-x-4' : 'translate-x-0.5'
          }`} />
        </button>
      </div>
    </div>
  );
};
