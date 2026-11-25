import React, { useState } from 'react';
import { Type, Sparkles, Scissors, Check } from 'lucide-react';
import { ViralClip, WordTimestamp } from '../types';

interface TabCaptionsProps {
  clip: ViralClip;
  onUpdateClip: (updates: Partial<ViralClip>) => void;
}

export const TabCaptions: React.FC<TabCaptionsProps> = ({ clip, onUpdateClip }) => {
  const [editingIndex, setEditingIndex] = useState<number | null>(null);
  const [editWordText, setEditWordText] = useState('');

  const fonts = [
    'Montserrat ExtraBold',
    'Anton',
    'Bebas Neue',
    'Impact',
    'Poppins ExtraBold',
    'Roboto Black',
    'Playfair Display Bold',
    'Courier Prime Bold'
  ];

  const animations = [
    { id: 'pop_scale', name: 'Pop Scale (118%)', desc: 'Punchy scale bounce on active word' },
    { id: 'bounce_spring', name: 'Elastic Spring', desc: 'Spring overshoot animation' },
    { id: 'typewriter_reveal', name: 'Typewriter Fade', desc: 'Words fade in sequentially' },
    { id: 'word_pill', name: 'Solid Word Pill', desc: 'Dark solid backdrop pill behind word' },
    { id: 'neon_glow', name: 'Neon Glow', desc: 'Luminous colored glow around active word' },
    { id: 'karaoke_clean', name: 'Karaoke Clean', desc: 'Crisp active-word color swap without bounce' },
  ];

  const handleWordClick = (index: number, word: WordTimestamp) => {
    setEditingIndex(index);
    setEditWordText(word.word);
  };

  const handleWordSave = (index: number) => {
    if (editingIndex === null) return;
    const words = [...clip.transcript_words];
    words[index] = { ...words[index], word: editWordText.trim() };
    onUpdateClip({ transcript_words: words });
    setEditingIndex(null);
  };

  const toggleWordDelete = (e: React.MouseEvent, index: number) => {
    e.stopPropagation();
    const words = [...clip.transcript_words];
    words[index] = { ...words[index], deleted: !words[index].deleted };
    onUpdateClip({ transcript_words: words });
  };

  return (
    <div className="space-y-5">
      {/* 8 Fonts Dropdown */}
      <div>
        <label className="text-xs font-bold uppercase tracking-wider text-zinc-300 flex items-center gap-1.5 mb-2">
          <Type className="w-3.5 h-3.5 text-violet-400" />
          <span>Caption Font</span>
        </label>
        <select
          value={clip.caption_font}
          onChange={(e) => onUpdateClip({ caption_font: e.target.value })}
          className="w-full px-3 py-2 rounded-xl bg-zinc-900 border border-zinc-800 text-xs font-bold text-zinc-200 outline-none focus:border-violet-500"
        >
          {fonts.map((f) => (
            <option key={f} value={f}>{f}</option>
          ))}
        </select>
      </div>

      {/* 6 Real-Time ASS Word Animations */}
      <div>
        <label className="text-xs font-bold uppercase tracking-wider text-zinc-300 flex items-center gap-1.5 mb-2.5">
          <Sparkles className="w-3.5 h-3.5 text-violet-400" />
          <span>Word Animation Style</span>
        </label>
        <div className="grid grid-cols-2 gap-2">
          {animations.map((a) => {
            const isSelected = clip.caption_animation === a.id;
            return (
              <button
                key={a.id}
                onClick={() => onUpdateClip({ caption_animation: a.id as any })}
                className={`p-2.5 rounded-xl border text-left transition-all ${
                  isSelected
                    ? 'bg-zinc-900 border-violet-500 ring-1 ring-violet-500/30'
                    : 'bg-zinc-900/40 hover:bg-zinc-900/80 border-zinc-800'
                }`}
              >
                <div className="text-xs font-bold text-zinc-200">{a.name}</div>
                <div className="text-[10px] text-zinc-500 mt-0.5 line-clamp-1">{a.desc}</div>
              </button>
            );
          })}
        </div>
      </div>

      {/* Typography Color Pickers */}
      <div className="p-3.5 rounded-xl bg-zinc-900/60 border border-zinc-800 space-y-3">
        <span className="text-xs font-bold text-zinc-200 block">Typography Colors</span>
        <div className="grid grid-cols-3 gap-2">
          <div>
            <label className="text-[10px] text-zinc-400 block mb-1">Base Text</label>
            <div className="flex items-center gap-1.5">
              <input
                type="color"
                value={clip.base_color}
                onChange={(e) => onUpdateClip({ base_color: e.target.value })}
                className="w-7 h-7 rounded cursor-pointer bg-transparent border-0"
              />
              <span className="text-[11px] font-mono text-zinc-300">{clip.base_color}</span>
            </div>
          </div>

          <div>
            <label className="text-[10px] text-zinc-400 block mb-1">Active Word</label>
            <div className="flex items-center gap-1.5">
              <input
                type="color"
                value={clip.active_color}
                onChange={(e) => onUpdateClip({ active_color: e.target.value })}
                className="w-7 h-7 rounded cursor-pointer bg-transparent border-0"
              />
              <span className="text-[11px] font-mono text-zinc-300">{clip.active_color}</span>
            </div>
          </div>

          <div>
            <label className="text-[10px] text-zinc-400 block mb-1">Outline / Glow</label>
            <div className="flex items-center gap-1.5">
              <input
                type="color"
                value={clip.outline_color}
                onChange={(e) => onUpdateClip({ outline_color: e.target.value })}
                className="w-7 h-7 rounded cursor-pointer bg-transparent border-0"
              />
              <span className="text-[11px] font-mono text-zinc-300">{clip.outline_color}</span>
            </div>
          </div>
        </div>
      </div>

      {/* Sizing, Stroke, and Offset Sliders */}
      <div className="p-3.5 rounded-xl bg-zinc-900/60 border border-zinc-800 space-y-3">
        <span className="text-xs font-bold text-zinc-200 block">Typography Geometry</span>

        {/* Font Size */}
        <div>
          <div className="flex justify-between text-[10px] text-zinc-400 mb-1">
            <span>Font Size (px)</span>
            <span className="font-mono text-violet-400">{clip.font_size}px</span>
          </div>
          <input
            type="range"
            min="48"
            max="110"
            value={clip.font_size}
            onChange={(e) => onUpdateClip({ font_size: parseInt(e.target.value, 10) })}
            className="w-full accent-violet-500"
          />
        </div>

        {/* Y-Position Offset */}
        <div>
          <div className="flex justify-between text-[10px] text-zinc-400 mb-1">
            <span>Bottom Y-Offset (Safe Margin)</span>
            <span className="font-mono text-violet-400">{clip.y_offset}px</span>
          </div>
          <input
            type="range"
            min="180"
            max="500"
            step="10"
            value={clip.y_offset}
            onChange={(e) => onUpdateClip({ y_offset: parseInt(e.target.value, 10) })}
            className="w-full accent-violet-500"
          />
        </div>

        {/* Stroke Width & Shadow Depth */}
        <div className="grid grid-cols-2 gap-3 pt-1">
          <div>
            <div className="flex justify-between text-[10px] text-zinc-400 mb-1">
              <span>Stroke</span>
              <span className="font-mono text-violet-400">{clip.stroke_width}px</span>
            </div>
            <input
              type="range"
              min="0"
              max="8"
              step="0.5"
              value={clip.stroke_width}
              onChange={(e) => onUpdateClip({ stroke_width: parseFloat(e.target.value) })}
              className="w-full accent-violet-500"
            />
          </div>

          <div>
            <div className="flex justify-between text-[10px] text-zinc-400 mb-1">
              <span>Shadow</span>
              <span className="font-mono text-violet-400">{clip.shadow_depth}px</span>
            </div>
            <input
              type="range"
              min="0"
              max="8"
              step="0.5"
              value={clip.shadow_depth}
              onChange={(e) => onUpdateClip({ shadow_depth: parseFloat(e.target.value) })}
              className="w-full accent-violet-500"
            />
          </div>
        </div>
      </div>

      {/* Toggles: ALL CAPS & Strip Fillers */}
      <div className="grid grid-cols-2 gap-2.5">
        <div className="p-3 rounded-xl bg-zinc-900/60 border border-zinc-800 flex items-center justify-between">
          <span className="text-xs font-semibold text-zinc-300">ALL CAPS</span>
          <button
            onClick={() => onUpdateClip({ all_caps: !clip.all_caps })}
            className={`w-9 h-5 rounded-full transition-colors relative ${
              clip.all_caps ? 'bg-violet-500' : 'bg-zinc-800'
            }`}
          >
            <div className={`w-4 h-4 rounded-full bg-white transition-transform transform absolute top-0.5 ${
              clip.all_caps ? 'translate-x-4' : 'translate-x-0.5'
            }`} />
          </button>
        </div>

        <div className="p-3 rounded-xl bg-zinc-900/60 border border-zinc-800 flex items-center justify-between">
          <div className="flex items-center gap-1.5">
            <Scissors className="w-3.5 h-3.5 text-violet-400" />
            <span className="text-xs font-semibold text-zinc-300">Strip Fillers</span>
          </div>
          <button
            onClick={() => onUpdateClip({ clean_filler_words: !clip.clean_filler_words })}
            className={`w-9 h-5 rounded-full transition-colors relative ${
              clip.clean_filler_words ? 'bg-violet-500' : 'bg-zinc-800'
            }`}
          >
            <div className={`w-4 h-4 rounded-full bg-white transition-transform transform absolute top-0.5 ${
              clip.clean_filler_words ? 'translate-x-4' : 'translate-x-0.5'
            }`} />
          </button>
        </div>
      </div>

      {/* Editable Word-by-Word Transcript */}
      <div>
        <div className="flex items-center justify-between mb-2">
          <label className="text-xs font-bold uppercase tracking-wider text-zinc-300">
            Word Transcript ({clip.transcript_words.length} words)
          </label>
          <span className="text-[10px] text-zinc-500">Click to edit • Strike to delete</span>
        </div>

        <div className="p-3 rounded-xl bg-zinc-950 border border-zinc-800/80 max-h-52 overflow-y-auto flex flex-wrap gap-1.5">
          {clip.transcript_words.map((w, index) => {
            const isEditing = editingIndex === index;
            return isEditing ? (
              <div key={index} className="flex items-center gap-1 bg-zinc-800 px-1.5 py-0.5 rounded">
                <input
                  type="text"
                  value={editWordText}
                  onChange={(e) => setEditWordText(e.target.value)}
                  onKeyDown={(e) => e.key === 'Enter' && handleWordSave(index)}
                  className="w-20 bg-zinc-900 text-xs text-white px-1 py-0.5 rounded outline-none border border-violet-500"
                  autoFocus
                />
                <button onClick={() => handleWordSave(index)} className="text-violet-400">
                  <Check className="w-3.5 h-3.5" />
                </button>
              </div>
            ) : (
              <span
                key={index}
                onClick={() => handleWordClick(index, w)}
                className={`text-xs px-2 py-0.5 rounded-md cursor-pointer transition-colors select-none group relative ${
                  w.deleted
                    ? 'line-through bg-zinc-900 text-zinc-600 opacity-50'
                    : w.is_filler
                    ? 'bg-amber-500/10 text-amber-300 border border-amber-500/30'
                    : 'bg-zinc-900 hover:bg-zinc-800 text-zinc-300 hover:text-white border border-zinc-800'
                }`}
              >
                {w.word}
                <button
                  onClick={(e) => toggleWordDelete(e, index)}
                  className="ml-1 opacity-0 group-hover:opacity-100 text-zinc-500 hover:text-rose-400 text-[9px]"
                >
                  ×
                </button>
              </span>
            );
          })}
        </div>
      </div>
    </div>
  );
};
