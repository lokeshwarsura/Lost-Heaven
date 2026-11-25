import React from 'react';
import { ThumbsUp, ThumbsDown, MessageSquare, Share2, Disc3 } from 'lucide-react';

interface SafeZoneOverlayProps {
  visible: boolean;
}

export const SafeZoneOverlay: React.FC<SafeZoneOverlayProps> = ({ visible }) => {
  if (!visible) return null;

  return (
    <div className="absolute inset-0 pointer-events-none select-none z-20 flex flex-col justify-between p-4 overflow-hidden">
      {/* Top Safe-Zone Header (Search / Sound) */}
      <div className="w-full flex items-center justify-between text-white/50 text-[10px] font-medium tracking-wide border-b border-dashed border-red-500/40 pb-1">
        <span className="bg-red-500/20 text-red-300 px-1 rounded text-[9px]">Top Safe Boundary (Y: 180-260px)</span>
        <span>YouTube Shorts HUD</span>
      </div>

      {/* Right-Hand Action Bar (Shorts Likes / Comments HUD) */}
      <div className="absolute right-3 bottom-24 flex flex-col items-center gap-4 text-white/70">
        <div className="flex flex-col items-center">
          <div className="w-9 h-9 rounded-full bg-black/40 backdrop-blur-md border border-white/20 flex items-center justify-center">
            <ThumbsUp className="w-4 h-4 fill-white/80" />
          </div>
          <span className="text-[9px] mt-0.5 font-bold">142K</span>
        </div>

        <div className="flex flex-col items-center">
          <div className="w-9 h-9 rounded-full bg-black/40 backdrop-blur-md border border-white/20 flex items-center justify-center">
            <ThumbsDown className="w-4 h-4" />
          </div>
          <span className="text-[9px] mt-0.5 font-bold">Dislike</span>
        </div>

        <div className="flex flex-col items-center">
          <div className="w-9 h-9 rounded-full bg-black/40 backdrop-blur-md border border-white/20 flex items-center justify-center">
            <MessageSquare className="w-4 h-4" />
          </div>
          <span className="text-[9px] mt-0.5 font-bold">2.4K</span>
        </div>

        <div className="flex flex-col items-center">
          <div className="w-9 h-9 rounded-full bg-black/40 backdrop-blur-md border border-white/20 flex items-center justify-center">
            <Share2 className="w-4 h-4" />
          </div>
          <span className="text-[9px] mt-0.5 font-bold">Share</span>
        </div>

        <div className="w-8 h-8 rounded-full bg-zinc-800 border-2 border-white/30 flex items-center justify-center animate-spin">
          <Disc3 className="w-4 h-4 text-emerald-400" />
        </div>
      </div>

      {/* Bottom Safe-Zone Area (Title, Creator Pill & Progress bar) */}
      <div className="w-full space-y-2 border-t border-dashed border-red-500/40 pt-2 bg-gradient-to-t from-black/80 to-transparent">
        <div className="flex items-center gap-2">
          <div className="w-6 h-6 rounded-full bg-emerald-500 flex items-center justify-center text-[10px] font-bold text-black">
            C
          </div>
          <span className="text-xs font-semibold text-white">@ChannelHandle</span>
          <span className="px-2 py-0.5 rounded-full bg-red-600 text-[10px] font-bold text-white">
            Subscribe
          </span>
        </div>
        <p className="text-[11px] text-white/90 line-clamp-2 leading-tight">
          Watch this unbelievable breakthrough moment! #Shorts #Viral
        </p>
        <div className="text-[9px] text-red-300 bg-red-500/20 px-1 py-0.5 rounded w-max">
          Bottom Caption Safe Boundary (Keep text above this)
        </div>
      </div>
    </div>
  );
};
