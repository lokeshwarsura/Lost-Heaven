import React from 'react';
import { Target, Zap, Heart, Sparkles } from 'lucide-react';
import { ViralClip } from '../types';

interface ViralityMetersProps {
  clip: ViralClip;
}

export const ViralityMeters: React.FC<ViralityMetersProps> = ({ clip }) => {
  const meters = [
    { label: 'Hook', value: clip.hook_score, icon: Zap, color: 'from-amber-500 to-yellow-400', desc: 'Opening 3s retention' },
    { label: 'Retention', value: clip.retention_score, icon: Target, color: 'from-emerald-500 to-teal-400', desc: 'Narrative pacing' },
    { label: 'Emotion', value: clip.emotional_score, icon: Heart, color: 'from-rose-500 to-pink-400', desc: 'Surprise / Insight' },
    { label: 'Standalone', value: clip.standalone_score, icon: Sparkles, color: 'from-violet-500 to-purple-400', desc: 'Context clarity' },
  ];

  return (
    <div className="w-full bg-zinc-900/60 border border-zinc-800/80 rounded-2xl p-4 space-y-3">
      {/* 4 Pillars Header */}
      <div className="flex items-center justify-between">
        <h3 className="text-xs font-bold uppercase tracking-wider text-zinc-300 flex items-center gap-1.5">
          <Sparkles className="w-3.5 h-3.5 text-emerald-400" />
          <span>Gemini Virality Pillars</span>
        </h3>
        <span className="text-xs font-extrabold text-emerald-400">
          Overall: {clip.virality_score}/100
        </span>
      </div>

      {/* 4 Score Progress Bars */}
      <div className="grid grid-cols-2 sm:grid-cols-4 gap-3">
        {meters.map((m) => {
          const Icon = m.icon;
          return (
            <div key={m.label} className="bg-zinc-950/60 p-2.5 rounded-xl border border-zinc-800/50">
              <div className="flex items-center justify-between text-[11px] mb-1">
                <span className="flex items-center gap-1 text-zinc-400 font-medium">
                  <Icon className="w-3 h-3 text-zinc-500" />
                  {m.label}
                </span>
                <span className="font-bold text-zinc-200">{m.value}%</span>
              </div>
              <div className="w-full h-1.5 bg-zinc-800 rounded-full overflow-hidden">
                <div 
                  className={`h-full rounded-full bg-gradient-to-r ${m.color} transition-all duration-500`}
                  style={{ width: `${m.value}%` }}
                />
              </div>
            </div>
          );
        })}
      </div>

      {/* Viral Rationale Box */}
      {clip.viral_rationale && (
        <div className="bg-zinc-950/80 border border-zinc-800/60 rounded-xl p-3 text-[11px] leading-relaxed text-zinc-300">
          <span className="font-semibold text-emerald-400 mr-1.5">Why This Works:</span>
          {clip.viral_rationale}
        </div>
      )}
    </div>
  );
};
