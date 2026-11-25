import React, { useState, useRef, useEffect } from 'react';
import { Eye, EyeOff, RotateCw, Play, Pause, Volume2, VolumeX, Sparkles, Target, Zap, Heart } from 'lucide-react';
import { ViralClip } from '../types';
import { SafeZoneOverlay } from './SafeZoneOverlay';

interface CenterPreviewProps {
  clip: ViralClip | null;
  onRefreshPreview: () => void;
}

export const CenterPreview: React.FC<CenterPreviewProps> = ({
  clip,
  onRefreshPreview
}) => {
  const [showSafeZone, setShowSafeZone] = useState(false);
  const [isPlaying, setIsPlaying] = useState(false);
  const [isMuted, setIsMuted] = useState(false);
  const videoRef = useRef<HTMLVideoElement>(null);

  useEffect(() => {
    if (videoRef.current && clip) {
      videoRef.current.currentTime = clip.rendered_video_path ? 0 : clip.start_time;
      videoRef.current.play().then(() => setIsPlaying(true)).catch(() => setIsPlaying(false));
    }
  }, [clip?.id, clip?.rendered_video_path]);

  const togglePlay = () => {
    if (!videoRef.current) return;
    if (isPlaying) {
      videoRef.current.pause();
      setIsPlaying(false);
    } else {
      videoRef.current.play();
      setIsPlaying(true);
    }
  };

  const toggleMute = () => {
    if (!videoRef.current) return;
    videoRef.current.muted = !isMuted;
    setIsMuted(!isMuted);
  };

  // Determine viewport aspect ratio box styling
  const getViewportDimensions = (aspect: string) => {
    switch (aspect) {
      case '16:9':
        return 'w-[560px] h-[315px]';
      case '1:1':
        return 'w-[400px] h-[400px]';
      case '4:5':
        return 'w-[360px] h-[450px]';
      case '21:9':
        return 'w-[580px] h-[248px]';
      case '9:16':
      default:
        return 'w-[320px] h-[568px]';
    }
  };

  if (!clip) {
    return (
      <div className="flex-1 h-full flex flex-col items-center justify-center p-8 text-center text-zinc-600 bg-zinc-950">
        <div className="w-16 h-16 rounded-2xl border border-dashed border-zinc-800 flex items-center justify-center mb-3 text-zinc-700">
          <Play className="w-6 h-6 ml-0.5" />
        </div>
        <p className="text-sm font-semibold text-zinc-400">Select a viral clip to preview</p>
        <p className="text-xs text-zinc-600 mt-1 max-w-sm">
          Multi-format interactive player with real-time framing and YouTube safe-zone verification.
        </p>
      </div>
    );
  }

  const viewportClass = getViewportDimensions(clip.aspect_ratio);

  return (
    <div className="flex-1 h-full overflow-y-auto bg-zinc-950 p-6 flex flex-col items-center justify-between">
      {/* Top Preview Controls Toolbar */}
      <div className="w-full max-w-xl flex items-center justify-between mb-3">
        <div className="flex items-center gap-2">
          <span className="text-xs font-bold text-zinc-300">
            {clip.rendered_video_path ? `Rendered (${clip.aspect_ratio})` : `Source Master (${clip.aspect_ratio})`}
          </span>
          <span className="text-[10px] font-bold px-2 py-0.5 rounded bg-violet-500/20 text-violet-400 border border-violet-500/40 uppercase">
            {clip.frame_layout.replace(/_/g, ' ')}
          </span>
        </div>

        {/* Safe-Zone Toggle & Reload */}
        <div className="flex items-center gap-2">
          {clip.aspect_ratio === '9:16' && (
            <button
              onClick={() => setShowSafeZone(!showSafeZone)}
              className={`flex items-center gap-1.5 px-2.5 py-1 rounded-lg text-xs font-medium border transition-all ${
                showSafeZone
                  ? 'bg-red-500/20 border-red-500/50 text-red-300'
                  : 'bg-zinc-900 border-zinc-800 text-zinc-400 hover:text-zinc-200'
              }`}
              title="Toggle YouTube Shorts Safe-Zone HUD"
            >
              {showSafeZone ? <EyeOff className="w-3.5 h-3.5" /> : <Eye className="w-3.5 h-3.5" />}
              <span>Safe Zone</span>
            </button>
          )}

          <button
            onClick={onRefreshPreview}
            className="p-1.5 rounded-lg bg-zinc-900 hover:bg-zinc-800 border border-zinc-800 text-zinc-400 hover:text-zinc-100 transition-colors"
            title="Reload Video Player"
          >
            <RotateCw className="w-3.5 h-3.5" />
          </button>
        </div>
      </div>

      {/* Adaptive Viewport Container */}
      <div className={`relative ${viewportClass} rounded-2xl overflow-hidden bg-black border-2 border-zinc-800 shadow-2xl shadow-black flex items-center justify-center flex-shrink-0 group transition-all duration-300`}>
        {clip.preview_url ? (
          <video
            ref={videoRef}
            src={clip.preview_url}
            className="w-full h-full object-cover"
            loop
            playsInline
            onClick={togglePlay}
          />
        ) : (
          <div className="text-center p-6 text-zinc-600">
            <p className="text-xs font-medium">Video preview loading...</p>
          </div>
        )}

        {/* YouTube Shorts Safe-Zone HUD (Active on 9:16) */}
        {clip.aspect_ratio === '9:16' && <SafeZoneOverlay visible={showSafeZone} />}

        {/* Media Control Bar */}
        <div className="absolute bottom-3 left-3 right-3 flex items-center justify-between z-30 opacity-0 group-hover:opacity-100 transition-opacity bg-black/60 backdrop-blur-md px-3 py-1.5 rounded-xl border border-white/10">
          <div className="flex items-center gap-2">
            <button onClick={togglePlay} className="text-white hover:text-violet-400 transition-colors">
              {isPlaying ? <Pause className="w-4 h-4" /> : <Play className="w-4 h-4" />}
            </button>
            <button onClick={toggleMute} className="text-white hover:text-violet-400 transition-colors">
              {isMuted ? <VolumeX className="w-4 h-4" /> : <Volume2 className="w-4 h-4" />}
            </button>
          </div>
          <span className="text-[11px] font-mono text-zinc-300">
            {Math.round(clip.duration)}s
          </span>
        </div>
      </div>

      {/* 4 Virality Pillars Breakdown */}
      <div className="w-full max-w-xl mt-4 bg-zinc-900/60 border border-zinc-800/80 rounded-2xl p-3.5 space-y-2.5">
        <div className="flex items-center justify-between">
          <span className="text-xs font-bold uppercase tracking-wider text-zinc-300 flex items-center gap-1.5">
            <Sparkles className="w-3.5 h-3.5 text-violet-400" />
            <span>Gemini Virality Pillars</span>
          </span>
          <span className="text-xs font-extrabold text-violet-400">
            Composite: {clip.virality_score}/100
          </span>
        </div>

        <div className="grid grid-cols-2 sm:grid-cols-4 gap-2.5">
          <div className="bg-zinc-950/70 p-2 rounded-xl border border-zinc-800/50">
            <div className="flex items-center justify-between text-[11px] mb-1">
              <span className="flex items-center gap-1 text-zinc-400"><Zap className="w-3 h-3 text-amber-400" /> Hook</span>
              <span className="font-bold text-zinc-200">{clip.hook_score}%</span>
            </div>
            <div className="w-full h-1 bg-zinc-800 rounded-full overflow-hidden">
              <div className="h-full bg-amber-400" style={{ width: `${clip.hook_score}%` }} />
            </div>
          </div>

          <div className="bg-zinc-950/70 p-2 rounded-xl border border-zinc-800/50">
            <div className="flex items-center justify-between text-[11px] mb-1">
              <span className="flex items-center gap-1 text-zinc-400"><Target className="w-3 h-3 text-emerald-400" /> Retention</span>
              <span className="font-bold text-zinc-200">{clip.retention_score}%</span>
            </div>
            <div className="w-full h-1 bg-zinc-800 rounded-full overflow-hidden">
              <div className="h-full bg-emerald-400" style={{ width: `${clip.retention_score}%` }} />
            </div>
          </div>

          <div className="bg-zinc-950/70 p-2 rounded-xl border border-zinc-800/50">
            <div className="flex items-center justify-between text-[11px] mb-1">
              <span className="flex items-center gap-1 text-zinc-400"><Heart className="w-3 h-3 text-rose-400" /> Emotion</span>
              <span className="font-bold text-zinc-200">{clip.emotional_score}%</span>
            </div>
            <div className="w-full h-1 bg-zinc-800 rounded-full overflow-hidden">
              <div className="h-full bg-rose-400" style={{ width: `${clip.emotional_score}%` }} />
            </div>
          </div>

          <div className="bg-zinc-950/70 p-2 rounded-xl border border-zinc-800/50">
            <div className="flex items-center justify-between text-[11px] mb-1">
              <span className="flex items-center gap-1 text-zinc-400"><Sparkles className="w-3 h-3 text-violet-400" /> Standalone</span>
              <span className="font-bold text-zinc-200">{clip.standalone_score}%</span>
            </div>
            <div className="w-full h-1 bg-zinc-800 rounded-full overflow-hidden">
              <div className="h-full bg-violet-400" style={{ width: `${clip.standalone_score}%` }} />
            </div>
          </div>
        </div>

        {clip.viral_rationale && (
          <p className="text-[11px] text-zinc-300 bg-zinc-950/80 p-2.5 rounded-xl border border-zinc-800/60 leading-relaxed">
            <span className="font-semibold text-violet-400 mr-1.5">AI Virality Insight:</span>
            {clip.viral_rationale}
          </p>
        )}
      </div>
    </div>
  );
};
