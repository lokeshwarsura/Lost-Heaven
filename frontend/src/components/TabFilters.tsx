import React from 'react';
import { Palette, Sun, Contrast, Eye } from 'lucide-react';
import { ViralClip } from '../types';

interface TabFiltersProps {
  clip: ViralClip;
  onUpdateClip: (updates: Partial<ViralClip>) => void;
}

export const TabFilters: React.FC<TabFiltersProps> = ({ clip, onUpdateClip }) => {
  const presets = [
    { id: 'none', name: 'Original', desc: 'Natural master source colors' },
    { id: 'cinematic', name: 'Cinematic Film', desc: 'Teal/orange S-curve balance' },
    { id: 'vibrant_gaming', name: 'Vibrant Gaming', desc: 'Saturation boost + unsharp' },
    { id: 'warm_vintage', name: 'Warm Vintage', desc: 'Golden highlights warmth' },
    { id: 'cool_tech', name: 'Cool Tech', desc: 'Cyber blue/cyan contrast' },
    { id: 'bw_noir', name: 'B&W Noir', desc: 'High-contrast monochrome' },
    { id: 'hdr_pop', name: 'HDR Pop', desc: 'Vivid gamma & dynamic clarity' },
  ];

  return (
    <div className="space-y-5">
      {/* 7 Color Grading Presets */}
      <div>
        <label className="text-xs font-bold uppercase tracking-wider text-zinc-300 flex items-center gap-1.5 mb-2.5">
          <Palette className="w-3.5 h-3.5 text-violet-400" />
          <span>Color Grading Presets</span>
        </label>
        <div className="grid grid-cols-1 gap-2">
          {presets.map((p) => {
            const isSelected = clip.color_preset === p.id;
            return (
              <div
                key={p.id}
                onClick={() => onUpdateClip({ color_preset: p.id as any })}
                className={`p-2.5 rounded-xl border transition-all cursor-pointer flex items-center justify-between ${
                  isSelected
                    ? 'bg-zinc-900 border-violet-500 ring-1 ring-violet-500/30 shadow-md'
                    : 'bg-zinc-900/40 hover:bg-zinc-900/80 border-zinc-800'
                }`}
              >
                <div>
                  <div className="text-xs font-bold text-zinc-200">{p.name}</div>
                  <div className="text-[10px] text-zinc-500">{p.desc}</div>
                </div>
                {isSelected && (
                  <span className="text-[10px] font-bold px-1.5 py-0.5 rounded bg-violet-500/20 text-violet-400 border border-violet-500/40">
                    ACTIVE
                  </span>
                )}
              </div>
            );
          })}
        </div>
      </div>

      {/* Manual Sliders */}
      <div className="p-3.5 rounded-xl bg-zinc-900/60 border border-zinc-800 space-y-3">
        <span className="text-xs font-bold text-zinc-200 block">Manual Color Adjustments</span>

        {/* Brightness */}
        <div>
          <div className="flex justify-between text-[10px] text-zinc-400 mb-1">
            <span className="flex items-center gap-1"><Sun className="w-3 h-3" /> Brightness</span>
            <span className="font-mono text-violet-400">
              {clip.brightness > 0 ? `+${clip.brightness.toFixed(2)}` : clip.brightness.toFixed(2)}
            </span>
          </div>
          <input
            type="range"
            min="-0.30"
            max="0.30"
            step="0.02"
            value={clip.brightness}
            onChange={(e) => onUpdateClip({ brightness: parseFloat(e.target.value) })}
            className="w-full accent-violet-500"
          />
        </div>

        {/* Contrast */}
        <div>
          <div className="flex justify-between text-[10px] text-zinc-400 mb-1">
            <span className="flex items-center gap-1"><Contrast className="w-3 h-3" /> Contrast</span>
            <span className="font-mono text-violet-400">{clip.contrast.toFixed(2)}x</span>
          </div>
          <input
            type="range"
            min="0.70"
            max="1.50"
            step="0.05"
            value={clip.contrast}
            onChange={(e) => onUpdateClip({ contrast: parseFloat(e.target.value) })}
            className="w-full accent-violet-500"
          />
        </div>

        {/* Saturation */}
        <div>
          <div className="flex justify-between text-[10px] text-zinc-400 mb-1">
            <span>Saturation</span>
            <span className="font-mono text-violet-400">{clip.saturation.toFixed(2)}x</span>
          </div>
          <input
            type="range"
            min="0.00"
            max="2.00"
            step="0.05"
            value={clip.saturation}
            onChange={(e) => onUpdateClip({ saturation: parseFloat(e.target.value) })}
            className="w-full accent-violet-500"
          />
        </div>
      </div>

      {/* Cinematic Vignette Toggle */}
      <div className="p-3.5 rounded-xl bg-zinc-900/60 border border-zinc-800 flex items-center justify-between">
        <div>
          <div className="text-xs font-bold text-zinc-200">Cinematic Edge Vignette</div>
          <div className="text-[10px] text-zinc-500">Darkens outer perimeter edges for focus</div>
        </div>
        <button
          onClick={() => onUpdateClip({ vignette: !clip.vignette })}
          className={`w-9 h-5 rounded-full transition-colors relative ${
            clip.vignette ? 'bg-violet-500' : 'bg-zinc-800'
          }`}
        >
          <div className={`w-4 h-4 rounded-full bg-white transition-transform transform absolute top-0.5 ${
            clip.vignette ? 'translate-x-4' : 'translate-x-0.5'
          }`} />
        </button>
      </div>
    </div>
  );
};
