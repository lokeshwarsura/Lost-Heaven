import React, { useState, useRef } from 'react';
import {
  Upload, Settings as SettingsIcon, CheckCircle2,
  Loader2, PlaySquare, AlertCircle, Sparkles, Filter, Clock, Hash
} from 'lucide-react';
import { YoutubeIcon } from './Icons';
import { Project } from '../types';

interface TopHeaderProps {
  currentProject: Project | null;
  onUploadFile: (file: File, aspect: string, duration: string, count: number, topic: string) => void;
  onImportYouTube: (url: string, aspect: string, duration: string, count: number, topic: string) => void;
  onOpenSettings: () => void;
  isProcessing: boolean;
}

export const TopHeader: React.FC<TopHeaderProps> = ({
  currentProject,
  onUploadFile,
  onImportYouTube,
  onOpenSettings,
  isProcessing
}) => {
  const [ytUrl, setYtUrl] = useState('');
  const [isImportingYt, setIsImportingYt] = useState(false);
  const fileInputRef = useRef<HTMLInputElement>(null);

  // Pre-Analysis AI Controls State
  const [aspectRatio, setAspectRatio] = useState('9:16');
  const [durationPreset, setDurationPreset] = useState('30-58s');
  const [clipCount, setClipCount] = useState(5);
  const [topicFocus, setTopicFocus] = useState('');

  const handleFileChange = (e: React.ChangeEvent<HTMLInputElement>) => {
    if (e.target.files && e.target.files[0]) {
      onUploadFile(e.target.files[0], aspectRatio, durationPreset, clipCount, topicFocus.trim());
    }
  };

  const handleYtSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!ytUrl.trim()) return;
    setIsImportingYt(true);
    try {
      await onImportYouTube(ytUrl.trim(), aspectRatio, durationPreset, clipCount, topicFocus.trim());
      setYtUrl('');
    } finally {
      setIsImportingYt(false);
    }
  };

  const getStatusBadge = () => {
    if (!currentProject) return null;
    if (currentProject.status === 'analyzed') {
      return (
        <div className="flex items-center gap-1.5 px-2.5 py-1 rounded-full bg-emerald-500/10 border border-emerald-500/30 text-emerald-400 text-xs font-medium">
          <CheckCircle2 className="w-3.5 h-3.5" />
          <span>AI Viral Moments Ready</span>
        </div>
      );
    }
    if (currentProject.status === 'error') {
      return (
        <div className="flex items-center gap-1.5 px-2.5 py-1 rounded-full bg-rose-500/10 border border-rose-500/30 text-rose-400 text-xs font-medium">
          <AlertCircle className="w-3.5 h-3.5" />
          <span>Processing Error</span>
        </div>
      );
    }
    return (
      <div className="flex items-center gap-2 px-3 py-1 rounded-full bg-violet-500/10 border border-violet-500/30 text-violet-300 text-xs font-medium animate-pulse">
        <Loader2 className="w-3.5 h-3.5 animate-spin" />
        <span>
          {currentProject.status === 'created' && 'Transcoding 480p Proxy...'}
          {currentProject.status === 'proxy_ready' && 'Sending to Gemini API...'}
          {currentProject.status === 'analyzing' && 'Gemini AI Detecting Viral Moments...'}
        </span>
      </div>
    );
  };

  return (
    <header className="border-b border-zinc-800 bg-zinc-950/95 backdrop-blur-md px-6 py-2.5 flex flex-col gap-2.5 z-30 sticky top-0">
      {/* Top Main Row */}
      <div className="flex items-center justify-between">
        {/* Brand & Mode */}
        <div className="flex items-center gap-4">
          <div className="flex items-center gap-2.5">
            <div className="w-9 h-9 rounded-xl bg-gradient-to-tr from-violet-600 to-indigo-500 flex items-center justify-center shadow-lg shadow-violet-500/20 text-white">
              <PlaySquare className="w-5 h-5 fill-current" />
            </div>
            <div>
              <div className="flex items-center gap-2">
                <h1 className="font-extrabold text-base tracking-tight bg-gradient-to-r from-zinc-100 to-zinc-400 bg-clip-text text-transparent">
                  Lost Heaven Studio
                </h1>
                <span className="text-[10px] font-bold tracking-wider uppercase px-1.5 py-0.5 rounded bg-violet-500/20 border border-violet-500/40 text-violet-400">
                  ULTRA PRO
                </span>
              </div>
              <p className="text-[11px] text-zinc-400 font-normal">
                Gemini Multimodal Director • Local Multi-Format Rendering Factory
              </p>
            </div>
          </div>

          <div className="hidden xl:flex items-center pl-4 border-l border-zinc-800">
            {getStatusBadge()}
          </div>
        </div>

        {/* Right Actions: YouTube Import & Upload Button */}
        <div className="flex items-center gap-3">
          {/* YouTube URL Form */}
          <form onSubmit={handleYtSubmit} className="hidden sm:flex items-center">
            <div className="relative flex items-center">
              <YoutubeIcon className="w-4 h-4 text-red-500 absolute left-3 pointer-events-none" />
              <input
                type="text"
                value={ytUrl}
                onChange={(e) => setYtUrl(e.target.value)}
                placeholder="Paste YouTube Link..."
                className="w-52 pl-9 pr-16 py-1.5 rounded-lg bg-zinc-900 border border-zinc-800 focus:border-violet-500/50 text-xs text-zinc-200 placeholder-zinc-500 outline-none transition-all"
              />
              <button
                type="submit"
                disabled={isImportingYt || !ytUrl.trim()}
                className="absolute right-1 px-2.5 py-0.5 rounded bg-zinc-800 hover:bg-zinc-700 text-zinc-300 hover:text-white text-[11px] font-medium transition-all disabled:opacity-40"
              >
                {isImportingYt ? <Loader2 className="w-3 h-3 animate-spin" /> : 'Fetch'}
              </button>
            </div>
          </form>

          {/* Upload Button */}
          <input
            ref={fileInputRef}
            type="file"
            accept="video/mp4,video/quicktime,video/x-matroska,video/webm"
            className="hidden"
            onChange={handleFileChange}
          />
          <button
            onClick={() => fileInputRef.current?.click()}
            disabled={isProcessing}
            className="flex items-center gap-2 px-3.5 py-1.5 rounded-lg bg-violet-600 hover:bg-violet-500 text-white text-xs font-bold transition-all shadow-md shadow-violet-600/20 active:scale-95 disabled:opacity-50"
          >
            {isProcessing ? <Loader2 className="w-3.5 h-3.5 animate-spin" /> : <Upload className="w-3.5 h-3.5" />}
            <span>Upload Video</span>
          </button>

          {/* Settings Trigger */}
          <button
            onClick={onOpenSettings}
            className="p-1.5 rounded-lg bg-zinc-900 hover:bg-zinc-800 border border-zinc-800 text-zinc-400 hover:text-zinc-100 transition-colors"
            title="Studio Settings"
          >
            <SettingsIcon className="w-4 h-4" />
          </button>
        </div>
      </div>

      {/* Pre-Analysis AI Controls Toolbar */}
      <div className="flex flex-wrap items-center justify-between gap-3 pt-2 border-t border-zinc-800/80 text-xs bg-zinc-900/40 p-2 rounded-xl">
        <div className="flex flex-wrap items-center gap-4">
          {/* Default Aspect Ratio */}
          <div className="flex items-center gap-1.5">
            <span className="text-[11px] font-semibold text-zinc-400 flex items-center gap-1">
              <Filter className="w-3 h-3 text-violet-400" />
              <span>Aspect:</span>
            </span>
            <select
              value={aspectRatio}
              onChange={(e) => setAspectRatio(e.target.value)}
              className="bg-zinc-950 border border-zinc-800 text-zinc-200 text-xs rounded-md px-2 py-0.5 outline-none focus:border-violet-500"
            >
              <option value="9:16">9:16 (Shorts/Reels)</option>
              <option value="16:9">16:9 (YouTube)</option>
              <option value="1:1">1:1 (Square)</option>
              <option value="4:5">4:5 (Portrait)</option>
              <option value="21:9">21:9 (Ultrawide)</option>
              <option value="custom">Custom Pixel</option>
            </select>
          </div>

          {/* Clip Duration Preset */}
          <div className="flex items-center gap-1.5">
            <span className="text-[11px] font-semibold text-zinc-400 flex items-center gap-1">
              <Clock className="w-3 h-3 text-amber-400" />
              <span>Duration:</span>
            </span>
            <select
              value={durationPreset}
              onChange={(e) => setDurationPreset(e.target.value)}
              className="bg-zinc-950 border border-zinc-800 text-zinc-200 text-xs rounded-md px-2 py-0.5 outline-none focus:border-violet-500"
            >
              <option value="15-30s">15-30s (Punchy)</option>
              <option value="30-58s">30-58s (Shorts)</option>
              <option value="60-180s">60-180s (Highlight)</option>
            </select>
          </div>

          {/* Clip Count */}
          <div className="flex items-center gap-1.5">
            <span className="text-[11px] font-semibold text-zinc-400 flex items-center gap-1">
              <Hash className="w-3 h-3 text-emerald-400" />
              <span>Clips:</span>
            </span>
            <select
              value={clipCount}
              onChange={(e) => setClipCount(parseInt(e.target.value, 10))}
              className="bg-zinc-950 border border-zinc-800 text-zinc-200 text-xs rounded-md px-2 py-0.5 outline-none focus:border-violet-500"
            >
              <option value="3">3 Top Picks</option>
              <option value="5">5 Top Picks</option>
              <option value="8">8 Top Picks</option>
              <option value="10">10 Top Picks</option>
            </select>
          </div>
        </div>

        {/* Topic Focus Prompt */}
        <div className="flex items-center gap-2 flex-1 max-w-md">
          <Sparkles className="w-3.5 h-3.5 text-violet-400 flex-shrink-0" />
          <input
            type="text"
            value={topicFocus}
            onChange={(e) => setTopicFocus(e.target.value)}
            placeholder="Topic Focus (optional): e.g. clutch plays, funny jokes, core tutorials..."
            className="w-full px-2.5 py-1 rounded-md bg-zinc-950 border border-zinc-800 text-xs text-zinc-200 placeholder-zinc-500 outline-none focus:border-violet-500"
          />
        </div>
      </div>
    </header>
  );
};
