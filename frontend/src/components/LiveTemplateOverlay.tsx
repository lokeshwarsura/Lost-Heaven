import React, { useEffect, useRef } from 'react';
import { ViralClip, WordTimestamp } from '../types';

interface LiveTemplateOverlayProps {
  clip: ViralClip;
  videoRef: React.RefObject<HTMLVideoElement | null>;
  isPlaying: boolean;
  showExactFrame: boolean;
  hasExactFrameUrl: boolean;
  videoFilterAlreadyBaked: boolean;
  bakedLayout: string;
  relCurrent: number;
  duration: number;
  compareOriginal: boolean;
  safeZoneGuide: string;
  videoCssFilter: string;
  tiltDeg: number;
  tiltScale: number;
}

const CANVAS_TEMPLATES = new Set([
  'fit_blur',
  'cinema_letterbox_blur',
  'portrait_pip_badge',
  'gameplay_webcam_pip',
  'webcam_gameplay_pip',
  'webcam_gameplay_split',
  'split_podcast',
  'triple_gaming_hud',
  'side_by_side_50_50',
]);

function normalizeLayout(raw?: string | null): string {
  const s = (raw || 'auto_track').trim().toLowerCase();
  if (s === 'webcam_gameplay_pip') return 'gameplay_webcam_pip';
  if (s === 'cinema_letterbox' || s === 'letterbox_blur' || s === 'cinema_239') {
    return 'cinema_letterbox_blur';
  }
  return s || 'auto_track';
}

function drawCoverCrop(
  ctx: CanvasRenderingContext2D,
  vid: HTMLVideoElement,
  sx: number,
  sy: number,
  sw: number,
  sh: number,
  dw: number,
  dh: number
) {
  if (sw <= 1 || sh <= 1 || dw <= 1 || dh <= 1) return;
  const srcRatio = sw / sh;
  const dstRatio = dw / dh;
  let cropW = sw;
  let cropH = sh;
  let cropX = sx;
  let cropY = sy;
  if (srcRatio > dstRatio) {
    cropW = sh * dstRatio;
    cropX = sx + (sw - cropW) * 0.5;
  } else if (srcRatio < dstRatio) {
    cropH = sw / dstRatio;
    cropY = sy + (sh - cropH) * 0.38;
  }
  ctx.drawImage(vid, cropX, cropY, cropW, cropH, 0, 0, dw, dh);
}

function getSourceActiveBand(
  vw: number,
  vh: number,
  bakedLayout: string
): { sx: number; sy: number; sw: number; sh: number } {
  const b = normalizeLayout(bakedLayout);
  const isPortraitVideo = vh > vw * 1.1;
  if (isPortraitVideo && b === 'fit_blur') {
    const sy = Math.floor(vh * 0.345);
    const sh = Math.max(32, Math.floor(vh * 0.31));
    return { sx: 0, sy, sw: vw, sh };
  }
  if (isPortraitVideo && b === 'cinema_letterbox_blur') {
    const sy = Math.floor(vh * 0.39);
    const sh = Math.max(32, Math.floor(vh * 0.22));
    return { sx: 0, sy, sw: vw, sh };
  }
  return { sx: 0, sy: 0, sw: vw, sh: vh };
}

function resolveRelativeCaptionWords(
  words: WordTimestamp[],
  clip: ViralClip
): Array<WordTimestamp & { relStart: number; relEnd: number }> {
  const valid = words.filter((w) => !(w as any).muted && !w.deleted && !w.cut_from_video);
  if (!valid.length) return [];
  const startOffset = Number(clip.start_time || 0);
  const dur = Math.max(1, Number(clip.duration || (clip.end_time || 30) - startOffset));
  const minStart = Math.min(...valid.map((w) => Number(w.start)));
  const maxEnd = Math.max(...valid.map((w) => Number(w.end)));
  const looksMasterTime = startOffset > 1.5 && minStart >= startOffset - 0.8 && maxEnd > dur + 0.8;
  return valid.map((w) => ({
    ...w,
    relStart: Math.max(0, Number(w.start) - (looksMasterTime ? startOffset : 0)),
    relEnd: Math.max(0.05, Number(w.end) - (looksMasterTime ? startOffset : 0)),
  }));
}

export const LiveTemplateOverlay: React.FC<LiveTemplateOverlayProps> = ({
  clip,
  videoRef,
  isPlaying,
  showExactFrame,
  hasExactFrameUrl,
  videoFilterAlreadyBaked,
  bakedLayout,
  relCurrent,
  duration,
  compareOriginal,
  safeZoneGuide,
  videoCssFilter,
  tiltDeg,
  tiltScale,
}) => {
  const bgCanvasRef = useRef<HTMLCanvasElement | null>(null);
  const paneARef = useRef<HTMLCanvasElement | null>(null);
  const paneBRef = useRef<HTMLCanvasElement | null>(null);
  const paneCRef = useRef<HTMLCanvasElement | null>(null);

  const layout = normalizeLayout(clip.framing_template || clip.frame_layout);
  const overlayNeeded =
    !compareOriginal &&
    !videoFilterAlreadyBaked &&
    (isPlaying || !showExactFrame || !hasExactFrameUrl);
  const usesCanvasCompositing = overlayNeeded && CANVAS_TEMPLATES.has(layout);

  const zVal = Math.max(1, Number(clip.zoom || 1));
  const panVal = Number(clip.pan_x_offset || 0);
  const pipCorner = clip.webcam_corner && clip.webcam_corner !== 'auto' ? clip.webcam_corner : 'top_right';
  const splitRatio = Math.max(0.28, Math.min(0.50, Number(clip.webcam_split_ratio || 0.38)));
  const hasTopSafeZone =
    safeZoneGuide === 'TikTok' ||
    safeZoneGuide === 'Instagram' ||
    safeZoneGuide === 'YT Shorts' ||
    safeZoneGuide === 'All';

  useEffect(() => {
    if (!usesCanvasCompositing) return;
    let rafId = 0;
    let cancelled = false;

    const renderFrame = () => {
      if (cancelled) return;
      const vid = videoRef.current;
      if (vid && vid.readyState >= 2 && vid.videoWidth > 0 && vid.videoHeight > 0) {
        const vw = vid.videoWidth;
        const vh = vid.videoHeight;
        const band = getSourceActiveBand(vw, vh, bakedLayout);
        const isWideSource = vw > vh * 1.15;

        const drawToCanvas = (
          canvas: HTMLCanvasElement | null,
          rx: number,
          ry: number,
          rw: number,
          rh: number
        ) => {
          if (!canvas) return;
          const cw = canvas.clientWidth || 320;
          const ch = canvas.clientHeight || 240;
          if (cw <= 0 || ch <= 0) return;
          if (canvas.width !== cw || canvas.height !== ch) {
            canvas.width = cw;
            canvas.height = ch;
          }
          const ctx = canvas.getContext('2d');
          if (!ctx) return;
          try {
            ctx.clearRect(0, 0, cw, ch);
            drawCoverCrop(ctx, vid, rx, ry, rw, rh, cw, ch);
          } catch {
            // Ignore transient decode frame glitches
          }
        };

        if (layout === 'fit_blur' || layout === 'cinema_letterbox_blur') {
          drawToCanvas(bgCanvasRef.current, band.sx, band.sy, band.sw, band.sh);
          const zoomFrac = 1 / Math.max(1, zVal);
          const zw = band.sw * zoomFrac;
          const zh = band.sh * zoomFrac;
          const panPx = (band.sw - zw) * (0.5 + Math.max(-0.5, Math.min(0.5, panVal)));
          const zx = band.sx + Math.max(0, Math.min(band.sw - zw, panPx));
          const zy = band.sy + (band.sh - zh) * 0.5;
          drawToCanvas(paneARef.current, zx, zy, zw, zh);
        } else if (layout === 'portrait_pip_badge' || layout === 'gameplay_webcam_pip') {
          const cam = clip.source_webcam_region;
          if (isWideSource && cam && cam.w > 0.05 && cam.h > 0.05) {
            drawToCanvas(
              paneARef.current,
              band.sx + cam.x * band.sw,
              band.sy + cam.y * band.sh,
              cam.w * band.sw,
              cam.h * band.sh
            );
          } else {
            // Focus smoothly onto upper-center head/speaker area for corner PiP live mirror
            const cw = band.sw * 0.58;
            const ch = band.sh * 0.44;
            drawToCanvas(
              paneARef.current,
              band.sx + (band.sw - cw) * 0.5,
              band.sy + band.sh * 0.08,
              cw,
              ch
            );
          }
        } else if (layout === 'webcam_gameplay_split') {
          const cam = clip.source_webcam_region;
          if (isWideSource && cam && cam.w > 0.05 && cam.h > 0.05) {
            drawToCanvas(
              paneARef.current,
              band.sx + cam.x * band.sw,
              band.sy + cam.y * band.sh,
              cam.w * band.sw,
              cam.h * band.sh
            );
          } else {
            drawToCanvas(paneARef.current, band.sx, band.sy, band.sw, band.sh * 0.42);
          }
          drawToCanvas(
            paneBRef.current,
            band.sx,
            band.sy + band.sh * (isWideSource ? 0.15 : 0.30),
            band.sw,
            band.sh * 0.68
          );
        } else if (layout === 'split_podcast') {
          if (isWideSource) {
            drawToCanvas(paneARef.current, band.sx + band.sw * 0.02, band.sy + band.sh * 0.08, band.sw * 0.46, band.sh * 0.84);
            drawToCanvas(paneBRef.current, band.sx + band.sw * 0.52, band.sy + band.sh * 0.08, band.sw * 0.46, band.sh * 0.84);
          } else {
            drawToCanvas(paneARef.current, band.sx, band.sy, band.sw, band.sh * 0.56);
            drawToCanvas(paneBRef.current, band.sx + band.sw * 0.06, band.sy + band.sh * 0.42, band.sw * 0.88, band.sh * 0.56);
          }
        } else if (layout === 'triple_gaming_hud') {
          const cam = clip.source_webcam_region;
          if (isWideSource && cam && cam.w > 0.05 && cam.h > 0.05) {
            drawToCanvas(
              paneARef.current,
              band.sx + cam.x * band.sw,
              band.sy + cam.y * band.sh,
              cam.w * band.sw,
              cam.h * band.sh
            );
          } else {
            drawToCanvas(paneARef.current, band.sx + band.sw * 0.15, band.sy, band.sw * 0.70, band.sh * 0.34);
          }
          drawToCanvas(paneBRef.current, band.sx, band.sy + band.sh * 0.18, band.sw, band.sh * 0.62);
          drawToCanvas(paneCRef.current, band.sx, band.sy + band.sh * 0.72, band.sw * 0.55, band.sh * 0.28);
        } else if (layout === 'side_by_side_50_50') {
          drawToCanvas(paneARef.current, band.sx, band.sy, band.sw * 0.52, band.sh);
          drawToCanvas(paneBRef.current, band.sx + band.sw * 0.48, band.sy, band.sw * 0.52, band.sh);
        }
      }
      if (isPlaying) {
        rafId = window.requestAnimationFrame(renderFrame);
      }
    };

    renderFrame();
    const vid = videoRef.current;
    const onSeekOrLoad = () => renderFrame();
    if (vid && !isPlaying) {
      vid.addEventListener('seeked', onSeekOrLoad);
      vid.addEventListener('loadeddata', onSeekOrLoad);
    }

    return () => {
      cancelled = true;
      if (rafId) window.cancelAnimationFrame(rafId);
      if (vid) {
        vid.removeEventListener('seeked', onSeekOrLoad);
        vid.removeEventListener('loadeddata', onSeekOrLoad);
      }
    };
  }, [
    usesCanvasCompositing,
    isPlaying,
    layout,
    bakedLayout,
    zVal,
    panVal,
    clip.id,
    JSON.stringify(clip.source_webcam_region || null),
  ]);

  if (!overlayNeeded) return null;

  const pipBorderStyle = {
    borderColor:
      !clip.pip_border_color || clip.pip_border_color.toUpperCase() === '#FFFFFF'
        ? 'rgba(255,255,255,0.45)'
        : clip.pip_border_color,
  };
  const pipPosClass = `${
    pipCorner.includes('bottom') ? 'bottom-4' : hasTopSafeZone ? 'top-[13%]' : 'top-4'
  } ${pipCorner.includes('left') ? 'left-3' : 'right-3'}`;

  const relWords = clip.burn_captions ? resolveRelativeCaptionWords(clip.transcript_words || [], clip) : [];
  const capTime = Math.max(0, relCurrent - Number(clip.timing_offset || 0));
  const activeIdx = relWords.findIndex((w) => capTime >= w.relStart - 0.12 && capTime <= w.relEnd + 0.18);
  const windowWords =
    activeIdx >= 0
      ? (() => {
          const chunkStart = Math.floor(activeIdx / 3) * 3;
          return relWords.slice(chunkStart, chunkStart + 3);
        })()
      : [];

  const capX = clip.caption_x ?? (clip.position_x ?? 50) / 100;
  const capY = clip.caption_y ?? (clip.position_y ?? 78) / 100;
  const capRot = clip.caption_rotation ?? 0;
  const capMaxW = Math.max(0.45, Math.min(0.92, clip.caption_max_width ?? 0.82));
  const retentionPct = Math.max(0, Math.min(100, (relCurrent / Math.max(0.2, duration || 30)) * 100));
  const hookVisible = Boolean(clip.show_hook ?? clip.show_hook_banner) && Boolean((clip.hook_headline || '').trim());

  const contentTransform =
    tiltDeg !== 0 ? `rotate(${tiltDeg}deg) scale(${tiltScale.toFixed(3)})` : undefined;

  return (
    <>
      {/* Live Client-Side Framing Template Layer */}
      {usesCanvasCompositing && layout === 'fit_blur' && (
        <div
          style={{ transform: contentTransform }}
          className="pointer-events-none absolute inset-0 z-[2] overflow-hidden bg-black"
        >
          <canvas
            ref={bgCanvasRef}
            style={{
              filter: `${videoCssFilter !== 'none' ? `${videoCssFilter} ` : ''}blur(22px) brightness(0.88) saturate(1.15)`,
              transform: 'scale(1.14)',
            }}
            className="absolute inset-0 w-full h-full"
          />
          <div className="absolute inset-x-0 top-1/2 -translate-y-1/2 w-full aspect-video overflow-hidden border-y border-white/30 shadow-[0_0_24px_rgba(0,0,0,0.45)] bg-black/20">
            <canvas
              ref={paneARef}
              style={{ filter: videoCssFilter !== 'none' ? videoCssFilter : undefined }}
              className="w-full h-full block"
            />
          </div>
        </div>
      )}

      {usesCanvasCompositing && layout === 'cinema_letterbox_blur' && (
        <div
          style={{ transform: contentTransform }}
          className="pointer-events-none absolute inset-0 z-[2] overflow-hidden bg-black"
        >
          <canvas
            ref={bgCanvasRef}
            style={{
              filter: `${videoCssFilter !== 'none' ? `${videoCssFilter} ` : ''}blur(24px) brightness(0.84) saturate(1.15)`,
              transform: 'scale(1.14)',
            }}
            className="absolute inset-0 w-full h-full"
          />
          <div
            style={{ aspectRatio: '2.39 / 1' }}
            className="absolute inset-x-0 top-1/2 -translate-y-1/2 w-full overflow-hidden border-y-2 border-black shadow-[0_0_24px_rgba(0,0,0,0.6)]"
          >
            <div className="w-full h-full border-y border-white/25">
              <canvas
                ref={paneARef}
                style={{ filter: videoCssFilter !== 'none' ? videoCssFilter : undefined }}
                className="w-full h-full block"
              />
            </div>
          </div>
        </div>
      )}

      {usesCanvasCompositing && (layout === 'portrait_pip_badge' || layout === 'gameplay_webcam_pip') && (
        <div
          style={pipBorderStyle}
          className={`pointer-events-none absolute z-[4] border-2 rounded-2xl overflow-hidden bg-black/40 shadow-[0_8px_24px_rgba(0,0,0,0.55)] ${pipPosClass} ${
            layout === 'portrait_pip_badge' ? 'w-[27%] aspect-[3/4]' : 'w-[30%] aspect-[4/3]'
          }`}
        >
          <canvas
            ref={paneARef}
            style={{ filter: videoCssFilter !== 'none' ? videoCssFilter : undefined }}
            className="w-full h-full block"
          />
        </div>
      )}

      {usesCanvasCompositing && layout === 'webcam_gameplay_split' && (
        <div
          style={{ transform: contentTransform }}
          className="pointer-events-none absolute inset-0 z-[2] flex flex-col overflow-hidden bg-black"
        >
          <div style={{ height: `${splitRatio * 100}%` }} className="w-full overflow-hidden shrink-0 border-b-[3px] border-white/30">
            <canvas
              ref={paneARef}
              style={{ filter: videoCssFilter !== 'none' ? videoCssFilter : undefined }}
              className="w-full h-full block"
            />
          </div>
          <div className="flex-1 w-full overflow-hidden min-h-0">
            <canvas
              ref={paneBRef}
              style={{ filter: videoCssFilter !== 'none' ? videoCssFilter : undefined }}
              className="w-full h-full block"
            />
          </div>
        </div>
      )}

      {usesCanvasCompositing && layout === 'split_podcast' && (
        <div
          style={{ transform: contentTransform }}
          className={`pointer-events-none absolute inset-0 z-[2] flex ${
            clip.aspect_ratio === '16:9' || clip.aspect_ratio === '2.39:1' ? 'flex-row' : 'flex-col'
          } overflow-hidden bg-black`}
        >
          <div
            className={`flex-1 min-w-0 min-h-0 overflow-hidden ${
              clip.aspect_ratio === '16:9' || clip.aspect_ratio === '2.39:1'
                ? 'border-r-[3px] border-white/30'
                : 'border-b-[3px] border-white/30'
            }`}
          >
            <canvas
              ref={paneARef}
              style={{ filter: videoCssFilter !== 'none' ? videoCssFilter : undefined }}
              className="w-full h-full block"
            />
          </div>
          <div className="flex-1 min-w-0 min-h-0 overflow-hidden">
            <canvas
              ref={paneBRef}
              style={{ filter: videoCssFilter !== 'none' ? videoCssFilter : undefined }}
              className="w-full h-full block"
            />
          </div>
        </div>
      )}

      {usesCanvasCompositing && layout === 'triple_gaming_hud' && (
        <div
          style={{ transform: contentTransform }}
          className="pointer-events-none absolute inset-0 z-[2] flex flex-col overflow-hidden bg-black"
        >
          <div className="h-[26%] w-full overflow-hidden shrink-0 border-b-2 border-white/30">
            <canvas
              ref={paneARef}
              style={{ filter: videoCssFilter !== 'none' ? videoCssFilter : undefined }}
              className="w-full h-full block"
            />
          </div>
          <div className="h-[52%] w-full overflow-hidden shrink-0 border-b-2 border-white/30">
            <canvas
              ref={paneBRef}
              style={{ filter: videoCssFilter !== 'none' ? videoCssFilter : undefined }}
              className="w-full h-full block"
            />
          </div>
          <div className="h-[22%] w-full overflow-hidden shrink-0">
            <canvas
              ref={paneCRef}
              style={{ filter: videoCssFilter !== 'none' ? videoCssFilter : undefined }}
              className="w-full h-full block"
            />
          </div>
        </div>
      )}

      {usesCanvasCompositing && layout === 'side_by_side_50_50' && (
        <div
          style={{ transform: contentTransform }}
          className="pointer-events-none absolute inset-0 z-[2] flex flex-row overflow-hidden bg-black"
        >
          <div className="w-1/2 h-full overflow-hidden border-r-[3px] border-white/30">
            <canvas
              ref={paneARef}
              style={{ filter: videoCssFilter !== 'none' ? videoCssFilter : undefined }}
              className="w-full h-full block"
            />
          </div>
          <div className="w-1/2 h-full overflow-hidden">
            <canvas
              ref={paneBRef}
              style={{ filter: videoCssFilter !== 'none' ? videoCssFilter : undefined }}
              className="w-full h-full block"
            />
          </div>
        </div>
      )}

      {/* Retention Progress Bar when edited/unbaked */}
      {clip.retention_bar_enabled && overlayNeeded && (
        <div className="pointer-events-none absolute top-0 inset-x-0 h-1 bg-black/35 z-[18]">
          <div
            style={{
              width: `${retentionPct}%`,
              backgroundColor: clip.retention_bar_color || '#C6F32F',
            }}
            className="h-full transition-[width] duration-75"
          />
        </div>
      )}

      {/* Top Hook Banner during live playback before MP4 re-render finishes */}
      {hookVisible && overlayNeeded && (
        <div
          className={`pointer-events-none absolute ${
            hasTopSafeZone ? 'top-[13%]' : 'top-4'
          } left-3 right-3 z-[18] flex justify-center`}
        >
          <div className="px-2.5 py-1 rounded-lg bg-black/80 border border-yellow-400 text-yellow-300 font-black text-[10px] uppercase tracking-wide shadow text-center leading-tight">
            ★ {clip.hook_headline} ★
          </div>
        </div>
      )}

      {/* Live Karaoke Captions during instant playback */}
      {clip.burn_captions && windowWords.length > 0 && overlayNeeded && (
        <div
          style={{
            left: `${capX * 100}%`,
            top: `${capY * 100}%`,
            maxWidth: `${capMaxW * 100}%`,
            transform: `translate(-50%, -50%) rotate(${capRot}deg)`,
            fontFamily: clip.caption_font ? `${clip.caption_font}, sans-serif` : undefined,
            fontWeight: clip.bold !== false ? 800 : 500,
            fontStyle: clip.italic ? 'italic' : 'normal',
            textTransform: clip.all_caps !== false ? 'uppercase' : 'none',
            letterSpacing: clip.letter_spacing ? `${clip.letter_spacing}px` : undefined,
            textShadow: '0 2px 6px rgba(0,0,0,0.95), 0 0 2px rgba(0,0,0,0.9)',
          }}
          className="pointer-events-none absolute z-[18] text-center leading-tight px-2 py-0.5 text-[12px] tracking-wide"
        >
          {windowWords.map((w, wi) => {
            const isSpeaking = capTime >= w.relStart && capTime <= w.relEnd;
            const usePill = (clip.highlight_shape || '').toLowerCase().includes('pill') ||
              (clip.highlight_shape || '').toLowerCase().includes('box');
            return (
              <span
                key={`${wi}-${w.start}`}
                style={{
                  color: isSpeaking
                    ? clip.active_color || '#C6F32F'
                    : clip.base_color || '#FFFFFF',
                  opacity:
                    !isSpeaking && clip.hold_back_alpha !== undefined && clip.hold_back_alpha < 1
                      ? Math.max(0.3, clip.hold_back_alpha)
                      : 1,
                  backgroundColor: isSpeaking && usePill ? (clip.pill_color || 'rgba(0,0,0,0.65)') : undefined,
                  borderRadius: isSpeaking && usePill ? '5px' : undefined,
                  padding: isSpeaking && usePill ? '1px 4px' : undefined,
                  transform: isSpeaking ? 'scale(1.06)' : undefined,
                }}
                className="mx-0.5 inline-block transition-transform duration-75"
              >
                {w.word}
              </span>
            );
          })}
        </div>
      )}
    </>
  );
};
