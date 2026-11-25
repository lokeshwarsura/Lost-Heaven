import React, { useState, useRef, useEffect } from 'react';
import { Eye, EyeOff, RotateCw, Play, Pause, Volume2, VolumeX } from 'lucide-react';
import { ViralClip } from '../types';
import { SafeZoneOverlay } from './SafeZoneOverlay';
import { ViralityMeters } from './ViralityMeters';

interface VerticalPreviewProps {
  clip: ViralClip | null;
  onRefreshPreview: () => void;
}

export const VerticalPreview: React.FC<VerticalPreviewProps> = ({
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

  if (!clip) {
    return (
      <div className="flex-1 h-full flex flex-col items-center justify-center p-8 text-center text-zinc-600 bg-zinc-950">
        <div className="w-16 h-16 rounded-2xl border border-dashed border-zinc-800 flex items-center justify-center mb-3 text-zinc-700">
          <Play className="w-6 h-6 ml-0.5" />
        </div>
        <p className="text-sm font-semibold text-zinc-400">Select a viral clip to preview</p>
        <p className="text-xs text-zinc-600 mt-1 max-w-sm">
          Interactive 9:16 vertical player with YouTube Shorts safe-zone verification.
        </p>
      </div>
    );
  }

  return (
    <div className="flex-1 h-full overflow-y-auto bg-zinc-950 p-6 flex flex-col items-center justify-between">
      {/* Top Preview Controls Toolbar */}
      <div className="w-full max-w-md flex items-center justify-between mb-4">
        <div className="flex items-center gap-2">
          <span className="text-xs font-bold text-zinc-300">
            {clip.rendered_video_path ? '1080x1920 Rendered Short' : 'Source Clip Preview'}
          </span>
          {clip.rendered_video_path && (
            <span className="text-[10px] font-bold px-1.5 py-0.5 rounded bg-emerald-500/20 text-emerald-400 border border-emerald-500/40">
              9:16 MASTERED
            </span>
          )}
        </div>

        {/* Safe-Zone Toggle & Reload */}
        <div className="flex items-center gap-2">
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

          <button
            onClick={onRefreshPreview}
            className="p-1.5 rounded-lg bg-zinc-900 hover:bg-zinc-800 border border-zinc-800 text-zinc-400 hover:text-zinc-100 transition-colors"
            title="Reload Video Player"
          >
            <RotateCw className="w-3.5 h-3.5" />
          </button>
        </div>
      </div>

      {/* True 9:16 Vertical Video Frame */}
      <div className="relative w-[340px] h-[604px] rounded-3xl overflow-hidden bg-black border-2 border-zinc-800 shadow-2xl shadow-black flex items-center justify-center flex-shrink-0 group">
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

        {/* YouTube Shorts Safe-Zone HUD */}
        <SafeZoneOverlay visible={showSafeZone} />

        {/* Hover Media Controls */}
        <div className="absolute bottom-4 left-4 right-4 flex items-center justify-between z-30 opacity-0 group-hover:opacity-100 transition-opacity bg-black/60 backdrop-blur-md px-3 py-2 rounded-xl border border-white/10">
          <div className="flex items-center gap-2">
            <button onClick={togglePlay} className="text-white hover:text-emerald-400 transition-colors">
              {isPlaying ? <Pause className="w-4 h-4" /> : <Play className="w-4 h-4" />}
            </button>
            <button onClick={toggleMute} className="text-white hover:text-emerald-400 transition-colors">
              {isMuted ? <VolumeX className="w-4 h-4" /> : <Volume2 className="w-4 h-4" />}
            </button>
          </div>
          <span className="text-[11px] font-mono text-zinc-300">
            {Math.round(clip.duration)}s
          </span>
        </div>
      </div>

      {/* Virality Breakdown Bar */}
      <div className="w-full max-w-md mt-5">
        <ViralityMeters clip={clip} />
      </div>
    </div>
  );
};
