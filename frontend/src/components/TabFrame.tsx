import React from 'react';
import { Camera, Crop, Sliders, Maximize2, MoveHorizontal, ZoomIn } from 'lucide-react';
import { ViralClip } from '../types';

interface TabFrameProps {
  clip: ViralClip;
  onUpdateClip: (updates: Partial<ViralClip>) => void;
}

export const TabFrame: React.FC<TabFrameProps> = ({ clip, onUpdateClip }) => {
  const aspectRatios = [
    { id: '9:16', label: '9:16', desc: 'Shorts / Reels' },
    { id: '16:9', label: '16:9', desc: 'YouTube Landscape' },
    { id: '1:1', label: '1:1', desc: 'Square' },
    { id: '4:5', label: '4:5', desc: 'Portrait' },
    { id: '21:9', label: '21:9', desc: 'Ultrawide' },
    { id: 'custom', label: 'Custom', desc: 'Custom Pixel' },
  ];

  const frameLayouts = [
    {
      id: 'auto_track',
      name: 'Auto-Track Speaker',
      desc: 'MediaPipe AI face detection + EMA anti-jitter smoothing',
      badge: 'RECOMMENDED'
    },
    {
      id: 'webcam_gameplay_split',
      name: 'Webcam + Gameplay Split',
      desc: 'Top webcam streamer box + bottom gameplay action',
      badge: 'STREAMING'
    },
    {
      id: 'webcam_gameplay_pip',
      name: 'Gameplay + Webcam PiP',
      desc: 'Full-bleed gameplay with bordered PiP webcam overlay',
      badge: 'GAMING'
    },
    {
      id: 'split_podcast',
      name: 'Split-Screen Podcast',
      desc: 'Dual speaker detection stacked vertically or side-by-side',
      badge: 'INTERVIEW'
    },
    {
      id: 'fit_blur',
      name: 'Fit + Blurred Backdrop',
      desc: 'Full uncropped frame over heavy Gaussian blurred background',
      badge: 'SLIDES/SCREEN'
    }
  ];

  const isWebcamLayout = clip.frame_layout === 'webcam_gameplay_split' || clip.frame_layout === 'webcam_gameplay_pip';

  return (
    <div className="space-y-5">
      {/* Aspect Ratio Selector */}
      <div>
        <label className="text-xs font-bold uppercase tracking-wider text-zinc-300 flex items-center gap-1.5 mb-2.5">
          <Crop className="w-3.5 h-3.5 text-violet-400" />
          <span>Target Aspect Ratio</span>
        </label>
        <div className="grid grid-cols-3 gap-2">
          {aspectRatios.map((ar) => {
            const isSelected = clip.aspect_ratio === ar.id;
            return (
              <button
                key={ar.id}
                onClick={() => onUpdateClip({ aspect_ratio: ar.id as any })}
                className={`py-2 px-2.5 rounded-xl border text-left transition-all ${
                  isSelected
                    ? 'bg-zinc-900 border-violet-500 text-white ring-1 ring-violet-500/30'
                    : 'bg-zinc-900/40 hover:bg-zinc-900/80 border-zinc-800 text-zinc-400'
                }`}
              >
                <div className="text-xs font-bold text-zinc-200">{ar.label}</div>
                <div className="text-[10px] text-zinc-500">{ar.desc}</div>
              </button>
            );
          })}
        </div>

        {/* Custom Width & Height Inputs */}
        {clip.aspect_ratio === 'custom' && (
          <div className="grid grid-cols-2 gap-3 mt-3 p-3 bg-zinc-950 rounded-xl border border-zinc-800">
            <div>
              <label className="text-[10px] text-zinc-400 block mb-1">Custom Width (px)</label>
              <input
                type="number"
                step="2"
                value={clip.custom_width}
                onChange={(e) => onUpdateClip({ custom_width: parseInt(e.target.value, 10) || 1080 })}
                className="w-full px-2.5 py-1.5 rounded-lg bg-zinc-900 border border-zinc-800 text-xs font-mono text-zinc-200 outline-none focus:border-violet-500"
              />
            </div>
            <div>
              <label className="text-[10px] text-zinc-400 block mb-1">Custom Height (px)</label>
              <input
                type="number"
                step="2"
                value={clip.custom_height}
                onChange={(e) => onUpdateClip({ custom_height: parseInt(e.target.value, 10) || 1920 })}
                className="w-full px-2.5 py-1.5 rounded-lg bg-zinc-900 border border-zinc-800 text-xs font-mono text-zinc-200 outline-none focus:border-violet-500"
              />
            </div>
          </div>
        )}
      </div>

      {/* 5 Frame Layout Templates */}
      <div>
        <label className="text-xs font-bold uppercase tracking-wider text-zinc-300 flex items-center gap-1.5 mb-2.5">
          <Camera className="w-3.5 h-3.5 text-violet-400" />
          <span>Frame Layout Template</span>
        </label>
        <div className="space-y-2">
          {frameLayouts.map((fl) => {
            const isSelected = clip.frame_layout === fl.id;
            return (
              <div
                key={fl.id}
                onClick={() => onUpdateClip({ frame_layout: fl.id as any })}
                className={`p-3 rounded-xl border transition-all cursor-pointer ${
                  isSelected
                    ? 'bg-zinc-900 border-violet-500 ring-1 ring-violet-500/30 shadow-md'
                    : 'bg-zinc-900/40 hover:bg-zinc-900/80 border-zinc-800'
                }`}
              >
                <div className="flex items-center justify-between mb-1">
                  <span className="text-xs font-bold text-zinc-200">{fl.name}</span>
                  <span className="text-[9px] font-bold px-1.5 py-0.2 rounded bg-zinc-800 text-violet-400 border border-zinc-700">
                    {fl.badge}
                  </span>
                </div>
                <p className="text-[11px] text-zinc-400">{fl.desc}</p>
              </div>
            );
          })}
        </div>
      </div>

      {/* Streamer & Webcam Layout Controls */}
      {isWebcamLayout && (
        <div className="p-3.5 rounded-xl bg-zinc-900/60 border border-zinc-800 space-y-3">
          <span className="text-xs font-bold text-zinc-200 block">Streamer / Webcam Settings</span>

          {/* Webcam Corner */}
          <div>
            <label className="text-[10px] text-zinc-400 block mb-1">Webcam Source Corner</label>
            <select
              value={clip.webcam_corner}
              onChange={(e) => onUpdateClip({ webcam_corner: e.target.value as any })}
              className="w-full px-2.5 py-1.5 rounded-lg bg-zinc-950 border border-zinc-800 text-xs text-zinc-200 outline-none focus:border-violet-500"
            >
              <option value="auto">Auto-Detect Facecam</option>
              <option value="top_left">Top-Left Corner</option>
              <option value="top_right">Top-Right Corner</option>
              <option value="bottom_left">Bottom-Left Corner</option>
              <option value="bottom_right">Bottom-Right Corner</option>
            </select>
          </div>

          {/* Split Ratio (for split layout) */}
          {clip.frame_layout === 'webcam_gameplay_split' && (
            <div>
              <div className="flex justify-between text-[10px] text-zinc-400 mb-1">
                <span>Top Webcam Height Ratio</span>
                <span className="font-mono text-violet-400">{Math.round(clip.webcam_split_ratio * 100)}%</span>
              </div>
              <input
                type="range"
                min="0.25"
                max="0.50"
                step="0.05"
                value={clip.webcam_split_ratio}
                onChange={(e) => onUpdateClip({ webcam_split_ratio: parseFloat(e.target.value) })}
                className="w-full accent-violet-500"
              />
            </div>
          )}

          {/* PiP Border Color (for PiP layout) */}
          {clip.frame_layout === 'webcam_gameplay_pip' && (
            <div>
              <label className="text-[10px] text-zinc-400 block mb-1">PiP Border Color</label>
              <div className="flex items-center gap-2">
                <input
                  type="color"
                  value={clip.pip_border_color}
                  onChange={(e) => onUpdateClip({ pip_border_color: e.target.value })}
                  className="w-8 h-8 rounded-lg cursor-pointer bg-transparent border-0"
                />
                <input
                  type="text"
                  value={clip.pip_border_color}
                  onChange={(e) => onUpdateClip({ pip_border_color: e.target.value })}
                  className="w-24 px-2 py-1 rounded-md bg-zinc-950 border border-zinc-800 text-xs font-mono text-zinc-200 outline-none"
                />
              </div>
            </div>
          )}
        </div>
      )}

      {/* Manual Zoom & Horizontal Pan Offset */}
      <div className="p-3.5 rounded-xl bg-zinc-900/60 border border-zinc-800 space-y-3">
        <span className="text-xs font-bold text-zinc-200 block">Zoom & Pan Controls</span>

        {/* Zoom */}
        <div>
          <div className="flex justify-between text-[10px] text-zinc-400 mb-1">
            <span className="flex items-center gap-1"><ZoomIn className="w-3 h-3" /> Manual Zoom</span>
            <span className="font-mono text-violet-400">{clip.zoom.toFixed(2)}x</span>
          </div>
          <input
            type="range"
            min="1.0"
            max="1.8"
            step="0.05"
            value={clip.zoom}
            onChange={(e) => onUpdateClip({ zoom: parseFloat(e.target.value) })}
            className="w-full accent-violet-500"
          />
        </div>

        {/* Pan Offset */}
        <div>
          <div className="flex justify-between text-[10px] text-zinc-400 mb-1">
            <span className="flex items-center gap-1"><MoveHorizontal className="w-3 h-3" /> Horizontal Pan Offset</span>
            <span className="font-mono text-violet-400">{Math.round(clip.pan_x_offset * 100)}%</span>
          </div>
          <input
            type="range"
            min="-0.40"
            max="0.40"
            step="0.05"
            value={clip.pan_x_offset}
            onChange={(e) => onUpdateClip({ pan_x_offset: parseFloat(e.target.value) })}
            className="w-full accent-violet-500"
          />
        </div>
      </div>

      {/* Boundaries Fine-Tune */}
      <div className="p-3.5 rounded-xl bg-zinc-900/60 border border-zinc-800 space-y-3">
        <div className="flex items-center justify-between">
          <span className="text-xs font-bold text-zinc-200">Clip Boundary Fine-Tune</span>
          <span className="text-xs font-mono font-bold text-violet-400">{clip.duration.toFixed(1)}s</span>
        </div>
        <div className="grid grid-cols-2 gap-3">
          <div>
            <label className="text-[10px] text-zinc-500 block mb-1">Start (s)</label>
            <input
              type="number"
              step="0.5"
              value={clip.start_time}
              onChange={(e) => onUpdateClip({ start_time: parseFloat(e.target.value) || 0 })}
              className="w-full px-2.5 py-1.5 rounded-lg bg-zinc-950 border border-zinc-800 text-xs font-mono text-zinc-200 outline-none focus:border-violet-500"
            />
          </div>
          <div>
            <label className="text-[10px] text-zinc-500 block mb-1">End (s)</label>
            <input
              type="number"
              step="0.5"
              value={clip.end_time}
              onChange={(e) => onUpdateClip({ end_time: parseFloat(e.target.value) || 0 })}
              className="w-full px-2.5 py-1.5 rounded-lg bg-zinc-950 border border-zinc-800 text-xs font-mono text-zinc-200 outline-none focus:border-violet-500"
            />
          </div>
        </div>
      </div>
    </div>
  );
};
