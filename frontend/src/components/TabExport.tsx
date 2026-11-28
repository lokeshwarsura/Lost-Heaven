import React, { useState, useEffect } from 'react';
import { 
  Download, Film, Copy, Check, ExternalLink, 
  Loader2, AlertCircle, Sparkles, Package
} from 'lucide-react';
import { YoutubeIcon } from './Icons';
import { ViralClip } from '../types';
import { api } from '../api/client';

interface TabExportProps {
  clip: ViralClip;
  onUpdateClip: (updates: Partial<ViralClip>) => void;
  onTriggerRender: () => void;
  isRendering: boolean;
}

export const TabExport: React.FC<TabExportProps> = ({
  clip,
  onUpdateClip,
  onTriggerRender,
  isRendering
}) => {
  const [copiedComment, setCopiedComment] = useState(false);
  const [copiedMeta, setCopiedMeta] = useState(false);
  const [isExportingPack, setIsExportingPack] = useState(false);
  const [isPublishing, setIsPublishing] = useState(false);
  const [ytConnected, setYtConnected] = useState<boolean>(false);
  const [publishResult, setPublishResult] = useState<{ url?: string; privacy_status?: string } | null>(null);
  const [publishError, setPublishError] = useState<string | null>(null);

  useEffect(() => {
    api.getYouTubeStatus()
      .then((st) => setYtConnected(Boolean(st.connected)))
      .catch(() => setYtConnected(false));
  }, []);

  const copyPinnedComment = () => {
    if (!clip.pinned_comment) return;
    navigator.clipboard.writeText(clip.pinned_comment);
    setCopiedComment(true);
    setTimeout(() => setCopiedComment(false), 2000);
  };

  const copyTitleAndHashtags = () => {
    const text = `${clip.selected_title || ''}\n\n${clip.youtube_description || ''}`.trim();
    navigator.clipboard?.writeText(text).catch(() => {});
    setCopiedMeta(true);
    setTimeout(() => setCopiedMeta(false), 2000);
  };

  const handleDownloadPack = async () => {
    setIsExportingPack(true);
    setPublishError(null);
    try {
      await api.exportPublishPack(clip.project_id, clip.id, 'fast_preview');
    } catch (err: any) {
      setPublishError(err?.message || 'Failed to build Social Pack .zip');
    } finally {
      setIsExportingPack(false);
    }
  };

  const handlePublishYouTube = async () => {
    if (!ytConnected) {
      setPublishError('YouTube account not linked. Connect your channel before uploading.');
      return;
    }
    setIsPublishing(true);
    setPublishError(null);
    try {
      const res = await api.publishToYouTube(
        clip.id,
        {
          title: clip.selected_title,
          description: clip.youtube_description,
          privacy_status: clip.privacy_status
        },
        clip.project_id
      );
      setPublishResult(res);
      onUpdateClip({ youtube_url: res.url });
    } catch (err: any) {
      setPublishError(err.message || 'Failed to post to YouTube');
    } finally {
      setIsPublishing(false);
    }
  };

  return (
    <div className="space-y-5">
      {/* Primary Render Action */}
      <div className="p-4 rounded-xl bg-zinc-900/60 border border-zinc-800 space-y-3">
        <div className="flex items-center justify-between">
          <span className="text-xs font-bold uppercase tracking-wider text-zinc-300">
            Export {clip.aspect_ratio} Video
          </span>
          <span className="text-[10px] text-zinc-500 font-mono">-14 LUFS Audio</span>
        </div>

        <div className="flex flex-col gap-2">
          {/* Render Button */}
          <button
            onClick={onTriggerRender}
            disabled={isRendering}
            className="w-full py-3 rounded-xl bg-gradient-to-r from-violet-600 to-indigo-600 hover:from-violet-500 hover:to-indigo-500 text-white font-extrabold text-xs tracking-wide shadow-lg shadow-violet-600/20 active:scale-[0.98] transition-all flex items-center justify-center gap-2 disabled:opacity-50"
          >
            {isRendering ? (
              <>
                <Loader2 className="w-4 h-4 animate-spin text-white" />
                <span>Rendering {clip.aspect_ratio} MP4 ({clip.render_progress}%)...</span>
              </>
            ) : (
              <>
                <Film className="w-4 h-4" />
                <span>
                  {clip.rendered_video_path ? `Re-Render ${clip.aspect_ratio} MP4` : `Render ${clip.aspect_ratio} MP4`}
                </span>
              </>
            )}
          </button>

          {/* Progress Bar when rendering */}
          {isRendering && (
            <div className="space-y-1">
              <div className="w-full h-1.5 bg-zinc-800 rounded-full overflow-hidden">
                <div 
                  className="h-full bg-violet-500 transition-all duration-300"
                  style={{ width: `${clip.render_progress}%` }}
                />
              </div>
              <p className="text-[10px] text-zinc-400 text-center font-mono">
                {clip.render_message || 'Processing video filters...'}
              </p>
            </div>
          )}

          {/* Direct Download Button */}
          {clip.rendered_video_path && (
            <a
              href={api.getDownloadUrl(clip.project_id, clip.id)}
              download
              className="w-full py-2.5 rounded-xl bg-zinc-800 hover:bg-zinc-700 text-zinc-200 hover:text-white font-bold text-xs tracking-wide transition-all flex items-center justify-center gap-2 border border-zinc-700 active:scale-[0.98]"
            >
              <Download className="w-4 h-4 text-violet-400" />
              <span>Download Rendered MP4</span>
            </a>
          )}
        </div>
      </div>

      {/* 3 A/B YouTube Titles */}
      <div>
        <label className="text-xs font-bold uppercase tracking-wider text-zinc-300 flex items-center gap-1.5 mb-2.5">
          <Sparkles className="w-3.5 h-3.5 text-violet-400" />
          <span>A/B Tested YouTube Titles</span>
        </label>

        <div className="space-y-2 mb-2.5">
          {clip.youtube_titles.map((title, idx) => {
            const isSelected = clip.selected_title === title;
            return (
              <div
                key={idx}
                onClick={() => onUpdateClip({ selected_title: title })}
                className={`p-2.5 rounded-xl border text-xs cursor-pointer transition-all flex items-center gap-2.5 ${
                  isSelected
                    ? 'bg-zinc-900 border-violet-500 text-zinc-100 ring-1 ring-violet-500/30'
                    : 'bg-zinc-900/40 hover:bg-zinc-900/80 border-zinc-800 text-zinc-400'
                }`}
              >
                <div className={`w-3.5 h-3.5 rounded-full border flex items-center justify-center flex-shrink-0 ${
                  isSelected ? 'border-violet-500 bg-violet-500' : 'border-zinc-700'
                }`}>
                  {isSelected && <div className="w-1.5 h-1.5 rounded-full bg-white" />}
                </div>
                <span className="font-semibold line-clamp-1">{title}</span>
              </div>
            );
          })}
        </div>

        <input
          type="text"
          value={clip.selected_title}
          onChange={(e) => onUpdateClip({ selected_title: e.target.value })}
          placeholder="Selected YouTube Title..."
          className="w-full px-3 py-2 rounded-lg bg-zinc-950 border border-zinc-800 text-xs text-zinc-200 outline-none focus:border-violet-500"
        />
      </div>

      {/* Description & Hashtags */}
      <div>
        <label className="text-xs font-bold uppercase tracking-wider text-zinc-300 block mb-1.5">
          YouTube Description & Hashtags
        </label>
        <textarea
          value={clip.youtube_description}
          onChange={(e) => onUpdateClip({ youtube_description: e.target.value })}
          rows={3}
          className="w-full px-3 py-2 rounded-lg bg-zinc-950 border border-zinc-800 text-xs text-zinc-300 outline-none focus:border-violet-500 resize-none leading-relaxed"
        />
      </div>

      {/* Pinned Engagement Comment */}
      <div className="p-3.5 rounded-xl bg-zinc-900/60 border border-zinc-800 space-y-2">
        <div className="flex items-center justify-between">
          <span className="text-xs font-bold text-zinc-300">Pinned Comment (Algorithm Booster)</span>
          <button
            onClick={copyPinnedComment}
            className="flex items-center gap-1 text-[11px] text-violet-400 hover:text-violet-300 font-semibold"
          >
            {copiedComment ? <Check className="w-3.5 h-3.5" /> : <Copy className="w-3.5 h-3.5" />}
            <span>{copiedComment ? 'Copied!' : 'Copy'}</span>
          </button>
        </div>
        <p className="text-xs text-zinc-400 italic bg-zinc-950 p-2.5 rounded-lg border border-zinc-800/80">
          "{clip.pinned_comment}"
        </p>
      </div>

      {/* 1-Click YouTube Publishing */}
      <div className="p-4 rounded-xl bg-zinc-900/60 border border-zinc-800 space-y-3">
        <div className="flex items-center justify-between">
          <div className="flex items-center gap-2">
            <YoutubeIcon className="w-4 h-4 text-red-500" />
            <span className="text-xs font-bold text-zinc-200">
              {clip.aspect_ratio === '9:16' ? 'Post to YouTube Shorts' : 'Post to YouTube'}
            </span>
          </div>
          <select
            value={clip.privacy_status}
            onChange={(e) => onUpdateClip({ privacy_status: e.target.value as any })}
            className="bg-zinc-950 border border-zinc-800 text-zinc-300 text-xs rounded-lg px-2 py-1 outline-none"
          >
            <option value="private">Private</option>
            <option value="unlisted">Unlisted</option>
            <option value="public">Public</option>
          </select>
        </div>

        <button
          onClick={handlePublishYouTube}
          disabled={isPublishing || !ytConnected || !clip.rendered_video_path}
          className="w-full py-2.5 rounded-xl bg-red-600 hover:bg-red-500 text-white font-bold text-xs tracking-wide shadow-lg shadow-red-600/20 active:scale-[0.98] transition-all flex items-center justify-center gap-2 disabled:opacity-40"
        >
          {isPublishing ? (
            <>
              <Loader2 className="w-4 h-4 animate-spin text-white" />
              <span>Uploading rendered MP4 to YouTube...</span>
            </>
          ) : (
            <>
              <YoutubeIcon className="w-4 h-4" />
              <span>{ytConnected ? 'Upload Video' : 'Connect YouTube to Upload'}</span>
            </>
          )}
        </button>

        <div className="grid grid-cols-2 gap-2">
          <button
            type="button"
            onClick={copyTitleAndHashtags}
            className="py-2 rounded-xl bg-zinc-800 hover:bg-zinc-700 text-zinc-200 text-xs font-semibold flex items-center justify-center gap-1.5 border border-zinc-700"
          >
            {copiedMeta ? <Check className="w-3.5 h-3.5 text-emerald-400" /> : <Copy className="w-3.5 h-3.5" />}
            <span>{copiedMeta ? 'Copied!' : 'Copy Title & Hashtags'}</span>
          </button>
          <button
            type="button"
            onClick={handleDownloadPack}
            disabled={isExportingPack}
            className="py-2 rounded-xl bg-zinc-800 hover:bg-zinc-700 text-zinc-200 text-xs font-semibold flex items-center justify-center gap-1.5 border border-zinc-700 disabled:opacity-40"
          >
            {isExportingPack ? <Loader2 className="w-3.5 h-3.5 animate-spin" /> : <Package className="w-3.5 h-3.5 text-violet-400" />}
            <span>Download Social Pack (.zip)</span>
          </button>
        </div>

        {!clip.rendered_video_path && (
          <p className="text-[10px] text-amber-400 flex items-center gap-1">
            <AlertCircle className="w-3 h-3" />
            Render MP4 before broadcasting to YouTube.
          </p>
        )}

        {publishResult?.url && (
          <div className="p-3 rounded-lg bg-emerald-500/10 border border-emerald-500/30 text-xs text-emerald-300 space-y-1">
            <div className="flex items-center justify-between font-bold">
              <span>Uploaded ({publishResult.privacy_status || 'private'})</span>
              <a
                href={publishResult.url}
                target="_blank"
                rel="noreferrer"
                className="flex items-center gap-1 underline text-emerald-400"
              >
                <span>View on YouTube</span>
                <ExternalLink className="w-3 h-3" />
              </a>
            </div>
            <p className="text-[10px] text-zinc-400 leading-tight">
              Unverified Google Cloud OAuth apps default uploaded videos to Private in YouTube Studio until approved.
            </p>
          </div>
        )}

        {publishError && (
          <div className="p-2.5 rounded-lg bg-rose-500/10 border border-rose-500/30 text-rose-300 text-xs">
            {publishError}
          </div>
        )}
      </div>
    </div>
  );
};
