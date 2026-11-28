import React, { useEffect, useRef, useState } from 'react';
import {
  Check,
  CheckCircle2,
  ChevronDown,
  ChevronUp,
  Copy,
  Download,
  ExternalLink,
  KeyRound,
  Link2,
  Loader2,
  Package,
  Plus,
  Sparkles,
  Unlink,
  Upload,
  Video,
  X,
} from 'lucide-react';
import { ViralClip } from '../types';
import { api } from '../api/client';

const YouTubeBadgeIcon = ({ className = 'w-4 h-4' }: { className?: string }) => (
  <svg viewBox="0 0 24 24" fill="currentColor" className={className}>
    <path d="M23.498 6.186a3.016 3.016 0 0 0-2.122-2.136C19.505 3.545 12 3.545 12 3.545s-7.505 0-9.377.505A3.017 3.017 0 0 0 .502 6.186C0 8.07 0 12 0 12s0 3.93.502 5.814a3.016 3.016 0 0 0 2.122 2.136c1.871.505 9.376.505 9.376.505s7.505 0 9.377-.505a3.015 3.015 0 0 0 2.122-2.136C24 15.93 24 12 24 12s0-3.93-.502-5.814zM9.545 15.568V8.432L15.818 12l-6.273 3.568z" />
  </svg>
);

type PlatformTabId = 'youtube' | 'instagram' | 'tiktok' | 'x';

interface PlatformDrafts {
  youtube: {
    title: string;
    description: string;
    tags: string[];
    pinned_comment: string;
    category_id: string;
  };
  instagram: {
    title: string;
    caption: string;
    pinned_comment: string;
  };
  tiktok: {
    title: string;
    caption: string;
    pinned_comment: string;
  };
  x: {
    post_text: string;
    pinned_comment: string;
  };
}

const YOUTUBE_CATEGORY_OPTIONS = [
  { id: '20', label: 'Gaming' },
  { id: '24', label: 'Entertainment' },
  { id: '22', label: 'People & Blogs' },
  { id: '27', label: 'Education' },
  { id: '28', label: 'Science & Tech' },
];

function formatDurationPill(seconds: number): string {
  const s = Math.max(0, Math.round(seconds || 0));
  const mm = Math.floor(s / 60);
  const ss = s % 60;
  return `${String(mm).padStart(2, '0')}:${String(ss).padStart(2, '0')}`;
}

function getResolutionLabel(clip: ViralClip): string {
  const size = (clip.clip_size || '1080p').toLowerCase();
  const baseShort = size.includes('4k') || size.includes('2160')
    ? 2160
    : size.includes('720')
      ? 720
      : size.includes('540')
        ? 540
        : size.includes('480')
          ? 480
          : 1080;
  switch (clip.aspect_ratio) {
    case '16:9':
      return `${Math.round((baseShort * 16) / 9)}×${baseShort}`;
    case '1:1':
      return `${baseShort}×${baseShort}`;
    case '4:5':
      return `${baseShort}×${Math.round((baseShort * 5) / 4)}`;
    case '4:3':
      return `${Math.round((baseShort * 4) / 3)}×${baseShort}`;
    case 'custom':
      return `${clip.custom_width || 1080}×${clip.custom_height || 1920}`;
    case '9:16':
    default:
      return `${baseShort}×${Math.round((baseShort * 16) / 9)}`;
  }
}

function formatBytes(bytes?: number | null): string {
  if (!bytes || bytes <= 0) return 'MP4';
  const mb = bytes / (1024 * 1024);
  if (mb >= 1) return `${mb.toFixed(1)} MB`;
  return `${Math.max(1, Math.round(bytes / 1024))} KB`;
}

function counterTone(len: number, warnAt: number, max: number): string {
  if (len > max) return 'bg-red-500/15 text-red-400 border-red-500/40';
  if (len >= warnAt) return 'bg-amber-500/15 text-amber-400 border-amber-500/40';
  return 'bg-emerald-500/15 text-emerald-400 border-emerald-500/35';
}

function buildInitialDrafts(clip: ViralClip): PlatformDrafts {
  const saved = clip.social_copy || (clip.options?.social_copy as ViralClip['social_copy']) || null;
  const fallbackTitle = clip.selected_title || clip.youtube_titles?.[0] || 'Viral Highlight #Shorts';
  const baseDesc = (clip.youtube_description || 'Created with Lost Heaven Studio! #Shorts').trim();
  const extraHash = (clip.hashtags || '').trim();
  const combinedDesc = extraHash && !baseDesc.includes(extraHash) ? `${baseDesc}\n\n${extraHash}` : baseDesc;

  const parsedTags = Array.from(
    new Set(
      (extraHash.match(/#?([A-Za-z0-9_]{2,})/g) || ['Shorts', 'Viral', 'Highlights']).map((t) =>
        t.replace(/^#/, '')
      )
    )
  ).slice(0, 10);

  return {
    youtube: {
      title: saved?.youtube?.title || fallbackTitle,
      description: saved?.youtube?.description || combinedDesc,
      tags: Array.isArray(saved?.youtube?.tags) && saved.youtube.tags.length > 0 ? saved.youtube.tags : parsedTags,
      pinned_comment: saved?.youtube?.pinned_comment || clip.pinned_comment || 'What part caught you off guard?',
      category_id: saved?.youtube?.category_id || '22',
    },
    instagram: {
      title: saved?.instagram?.title || fallbackTitle.replace(/#shorts/ig, '').trim(),
      caption:
        saved?.instagram?.caption ||
        `${fallbackTitle.replace(/#shorts/ig, '').trim()}\n\n${baseDesc}\n\n#reels #reelsinstagram #explorepage #viralreels #shorts #trending #content #creator`,
      pinned_comment: saved?.instagram?.pinned_comment || 'Save this Reel for later 🔥',
    },
    tiktok: {
      title: saved?.tiktok?.title || fallbackTitle.replace(/#shorts/ig, '').trim(),
      caption:
        saved?.tiktok?.caption ||
        `${fallbackTitle.replace(/#shorts/ig, '').trim()} 👀\n\n${baseDesc}\n\n#fyp #viral #trending #storytime #clips`,
      pinned_comment: saved?.tiktok?.pinned_comment || 'Part 2 or full breakdown? 👇',
    },
    x: {
      post_text:
        saved?.x?.post_text ||
        `${fallbackTitle.replace(/#shorts/ig, '').trim()} — ${baseDesc}`.slice(0, 265) + ' #Shorts #Video',
      pinned_comment: saved?.x?.pinned_comment || 'Drop your thoughts below.',
    },
  };
}

interface PublishModalProps {
  isOpen: boolean;
  clip: ViralClip | null;
  videoUrl?: string;
  previewFrameUrl?: string | null;
  clipIsRendered: boolean;
  onClose: () => void;
  onUpdateClip?: (updates: Partial<ViralClip>) => void;
  onTriggerRender?: (clipId: number, projectId: number) => Promise<void> | void;
}

export const PublishModal: React.FC<PublishModalProps> = ({
  isOpen,
  clip,
  videoUrl,
  previewFrameUrl,
  clipIsRendered,
  onClose,
  onUpdateClip,
  onTriggerRender,
}) => {
  const [activeTab, setActiveTab] = useState<PlatformTabId>('youtube');
  const [drafts, setDrafts] = useState<PlatformDrafts | null>(null);
  const [newTagInput, setNewTagInput] = useState('');
  const [aiSourceBadge, setAiSourceBadge] = useState<'gemini' | 'offline' | null>(null);
  const [isGeneratingCopy, setIsGeneratingCopy] = useState(false);

  const [copiedKey, setCopiedKey] = useState<string | null>(null);
  const [isDownloadingMp4, setIsDownloadingMp4] = useState(false);
  const [isExportingPack, setIsExportingPack] = useState(false);
  const [actionError, setActionError] = useState<string | null>(null);

  // YouTube OAuth & Direct API upload state (zero premature errors on modal open)
  const [ytStatus, setYtStatus] = useState<{
    client_configured: boolean;
    connected: boolean;
    channel_title: string | null;
    channel_id: string | null;
  } | null>(null);
  const [isLoadingYtStatus, setIsLoadingYtStatus] = useState(false);
  const [showLinkDrawer, setShowLinkDrawer] = useState(false);
  const [oauthSetupTab, setOauthSetupTab] = useState<'json' | 'paste'>('json');
  const [clientIdInput, setClientIdInput] = useState('');
  const [clientSecretInput, setClientSecretInput] = useState('');
  const [isSavingCreds, setIsSavingCreds] = useState(false);
  const [isUploadingSecrets, setIsUploadingSecrets] = useState(false);
  const [isConnectingYt, setIsConnectingYt] = useState(false);
  const [ytAuthError, setYtAuthError] = useState<string | null>(null);
  const clientSecretsInputRef = useRef<HTMLInputElement>(null);

  const [publishPrivacy, setPublishPrivacy] = useState<'private' | 'unlisted' | 'public'>('private');
  const [isPublishing, setIsPublishing] = useState(false);
  const [publishSuccess, setPublishSuccess] = useState<{
    video_id: string;
    url: string;
    privacy_status: string;
    channel_title?: string | null;
  } | null>(null);
  const [publishError, setPublishError] = useState<string | null>(null);

  // Reset modal state cleanly whenever opened on a clip (NEVER inject errors on open)
  useEffect(() => {
    if (!isOpen || !clip) return;
    setDrafts(buildInitialDrafts(clip));
    setPublishPrivacy(clip.privacy_status || 'private');
    setPublishSuccess(null);
    setPublishError(null);
    setYtAuthError(null);
    setActionError(null);
    setShowLinkDrawer(false);
    setAiSourceBadge(null);

    let cancelled = false;
    setIsLoadingYtStatus(true);
    api
      .getYouTubeStatus()
      .then((st) => {
        if (!cancelled) setYtStatus(st);
      })
      .catch(() => {
        if (!cancelled) {
          setYtStatus({
            client_configured: false,
            connected: false,
            channel_title: null,
            channel_id: null,
          });
        }
      })
      .finally(() => {
        if (!cancelled) setIsLoadingYtStatus(false);
      });

    return () => {
      cancelled = true;
    };
  }, [isOpen, clip?.id]);

  if (!isOpen || !clip || !drafts) return null;

  const emitToast = (message: string, type: 'success' | 'error' | 'info' = 'success') => {
    window.dispatchEvent(new CustomEvent('lh-toast', { detail: { message, type } }));
  };

  const persistSocialDrafts = (nextDrafts: PlatformDrafts) => {
    setDrafts(nextDrafts);
    const ytTagsHash = nextDrafts.youtube.tags.map((t) => `#${t.replace(/^#/, '')}`).join(' ');
    onUpdateClip?.({
      selected_title: nextDrafts.youtube.title,
      youtube_description: nextDrafts.youtube.description,
      hashtags: ytTagsHash,
      pinned_comment: nextDrafts.youtube.pinned_comment,
      privacy_status: publishPrivacy,
      social_copy: nextDrafts as any,
    });
    void api
      .updateClipMetadata(clip.project_id, clip.id, {
        selected_title: nextDrafts.youtube.title,
        youtube_description: nextDrafts.youtube.description,
        hashtags: ytTagsHash,
        pinned_comment: nextDrafts.youtube.pinned_comment,
        privacy_status: publishPrivacy,
        social_copy: nextDrafts,
      })
      .catch(() => {});
  };

  const handleCopy = (key: string, text: string, toastMsg = 'Copied to clipboard') => {
    navigator.clipboard?.writeText(text).catch(() => {});
    setCopiedKey(key);
    emitToast(toastMsg, 'success');
    window.setTimeout(() => setCopiedKey((prev) => (prev === key ? null : prev)), 1500);
  };

  const handleGenerateAllCopy = async () => {
    setIsGeneratingCopy(true);
    setActionError(null);
    try {
      const res = await api.generateSocialCopy(clip.project_id, clip.id);
      const next: PlatformDrafts = {
        youtube: {
          title: res.youtube?.title || drafts.youtube.title,
          description: res.youtube?.description || drafts.youtube.description,
          tags: Array.isArray(res.youtube?.tags) && res.youtube.tags.length > 0 ? res.youtube.tags : drafts.youtube.tags,
          pinned_comment: res.youtube?.pinned_comment || drafts.youtube.pinned_comment,
          category_id: res.youtube?.category_id || drafts.youtube.category_id,
        },
        instagram: {
          title: res.instagram?.title || drafts.instagram.title,
          caption: res.instagram?.caption || drafts.instagram.caption,
          pinned_comment: res.instagram?.pinned_comment || drafts.instagram.pinned_comment,
        },
        tiktok: {
          title: res.tiktok?.title || drafts.tiktok.title,
          caption: res.tiktok?.caption || drafts.tiktok.caption,
          pinned_comment: res.tiktok?.pinned_comment || drafts.tiktok.pinned_comment,
        },
        x: {
          post_text: res.x?.post_text || drafts.x.post_text,
          pinned_comment: res.x?.pinned_comment || drafts.x.pinned_comment,
        },
      };
      persistSocialDrafts(next);
      setAiSourceBadge(res.mode === 'gemini' || res.source === 'gemini' ? 'gemini' : 'offline');
      emitToast('Generated tailored copy across all 4 platforms', 'success');
    } catch (err: any) {
      setActionError(err?.message || 'Failed to generate multi-platform copy.');
    } finally {
      setIsGeneratingCopy(false);
    }
  };

  const handleDownloadFinalMp4 = async () => {
    setActionError(null);
    setIsDownloadingMp4(true);
    try {
      if (!clipIsRendered) {
        if (onTriggerRender) {
          await onTriggerRender(clip.id, clip.project_id);
        } else {
          await api.triggerRender(clip.id, 'final_hq', undefined, clip.project_id);
        }
      }
      const a = document.createElement('a');
      a.href = api.getDownloadUrl(clip.project_id, clip.id);
      a.download = '';
      document.body.appendChild(a);
      a.click();
      a.remove();
      emitToast('Downloading Final MP4...', 'success');
    } catch (err: any) {
      setActionError(err?.message || 'Failed to render or download Final MP4.');
    } finally {
      setIsDownloadingMp4(false);
    }
  };

  const handleDownloadSocialPack = async () => {
    setActionError(null);
    setIsExportingPack(true);
    try {
      const filename = await api.downloadSocialPack(clip.project_id, clip.id);
      emitToast(`Downloaded ${filename}`, 'success');
    } catch (err: any) {
      setActionError(err?.message || 'Failed to build Social Pack (.zip).');
    } finally {
      setIsExportingPack(false);
    }
  };

  const handleCopyAndOpenStudio = () => {
    const activeCopyText =
      activeTab === 'youtube'
        ? `${drafts.youtube.title}\n\n${drafts.youtube.description}\n\n${drafts.youtube.tags.join(', ')}`
        : activeTab === 'instagram'
          ? `${drafts.instagram.title}\n\n${drafts.instagram.caption}`
          : activeTab === 'tiktok'
            ? `${drafts.tiktok.title}\n\n${drafts.tiktok.caption}`
            : drafts.x.post_text;
    navigator.clipboard?.writeText(activeCopyText.trim()).catch(() => {});
    emitToast('Copied! Opening YouTube Studio...', 'success');
    window.open('https://studio.youtube.com', '_blank', 'noopener,noreferrer');
  };

  const handleUploadClientSecrets = async (e: React.ChangeEvent<HTMLInputElement>) => {
    const file = e.target.files?.[0];
    e.target.value = '';
    if (!file) return;
    setIsUploadingSecrets(true);
    setYtAuthError(null);
    try {
      const next = await api.uploadYouTubeClientSecrets(file);
      setYtStatus(next);
      emitToast('Google OAuth Client JSON saved', 'success');
    } catch (err: any) {
      setYtAuthError(err?.message || 'Failed to save Google OAuth Client JSON.');
    } finally {
      setIsUploadingSecrets(false);
    }
  };

  const handleSaveCredentials = async () => {
    if (!clientIdInput.trim() || !clientSecretInput.trim()) {
      setYtAuthError('Paste both your Google Client ID and Client Secret first.');
      return;
    }
    setIsSavingCreds(true);
    setYtAuthError(null);
    try {
      const next = await api.saveYouTubeCredentials(clientIdInput.trim(), clientSecretInput.trim());
      setYtStatus(next);
      setClientSecretInput('');
      emitToast('YouTube OAuth credentials saved', 'success');
    } catch (err: any) {
      setYtAuthError(err?.message || 'Failed to save OAuth credentials.');
    } finally {
      setIsSavingCreds(false);
    }
  };

  const handleConnectGoogle = async () => {
    if (!ytStatus?.client_configured) {
      setYtAuthError('Configure client_secret.json or paste Client ID & Secret first.');
      return;
    }
    setIsConnectingYt(true);
    setYtAuthError(null);
    try {
      const next = await api.connectYouTube();
      setYtStatus(next);
      setShowLinkDrawer(false);
      emitToast(`Connected YouTube channel: ${next.channel_title || 'Ready'}`, 'success');
    } catch (err: any) {
      setYtAuthError(err?.message || 'Google OAuth sign-in did not complete.');
    } finally {
      setIsConnectingYt(false);
    }
  };

  const handleDisconnectYouTube = async () => {
    setYtAuthError(null);
    try {
      const next = await api.disconnectYouTube();
      setYtStatus(next);
      setPublishSuccess(null);
    } catch (err: any) {
      setYtAuthError(err?.message || 'Failed to disconnect YouTube channel.');
    }
  };

  const handleDirectYouTubeUpload = async () => {
    if (!ytStatus?.connected) {
      setPublishError('Link your YouTube account below before uploading directly.');
      return;
    }
    setIsPublishing(true);
    setPublishError(null);
    setPublishSuccess(null);
    try {
      const res = await api.publishToYouTube(
        clip.id,
        {
          title: drafts.youtube.title,
          description: drafts.youtube.description,
          tags: drafts.youtube.tags,
          privacy_status: publishPrivacy,
          category_id: drafts.youtube.category_id,
        },
        clip.project_id
      );
      setPublishSuccess({
        video_id: res.video_id,
        url: res.url,
        privacy_status: res.privacy_status || publishPrivacy,
        channel_title: res.channel_title,
      });
      onUpdateClip?.({
        youtube_url: res.url,
        selected_title: drafts.youtube.title,
        youtube_description: drafts.youtube.description,
        privacy_status: publishPrivacy,
      });
      emitToast('Uploaded directly to YouTube!', 'success');
    } catch (err: any) {
      setPublishError(err?.message || 'Direct YouTube upload failed.');
    } finally {
      setIsPublishing(false);
    }
  };

  const handleAddTagChip = () => {
    const clean = newTagInput.trim().replace(/^#+/, '').replace(/,/g, '');
    if (!clean) return;
    if (!drafts.youtube.tags.includes(clean)) {
      persistSocialDrafts({
        ...drafts,
        youtube: { ...drafts.youtube, tags: [...drafts.youtube.tags, clean].slice(0, 15) },
      });
    }
    setNewTagInput('');
  };

  const handleRemoveTagChip = (tag: string) => {
    persistSocialDrafts({
      ...drafts,
      youtube: { ...drafts.youtube, tags: drafts.youtube.tags.filter((t) => t !== tag) },
    });
  };

  const thumbUrl = previewFrameUrl || clip.best_frames?.[0]?.image_url || null;
  const activeVideoUrl = clip.video_url || videoUrl || null;
  const captionsReady = Boolean(clip.burn_captions);
  const audioNormalized = clip.normalize_audio !== false;

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/75 backdrop-blur-sm p-3 sm:p-5 animate-fadeIn">
      <div className="w-full max-w-4xl max-h-[92vh] bg-[var(--bg-surface)] border border-[var(--border-subtle)] rounded-2xl shadow-2xl text-[var(--text-primary)] flex flex-col overflow-hidden">
        {/* Clean Modal Header */}
        <div className="px-5 py-3.5 border-b border-[var(--border-subtle)] bg-[var(--bg-elevated)] flex items-center justify-between gap-3 shrink-0 flex-wrap">
          <div className="flex items-center gap-2.5 min-w-0">
            <div className="w-8 h-8 rounded-xl bg-red-500/15 border border-red-500/30 flex items-center justify-center shrink-0">
              <YouTubeBadgeIcon className="w-4 h-4 text-red-500" />
            </div>
            <div className="min-w-0">
              <div className="flex items-center gap-2 flex-wrap">
                <h2 className="text-sm font-bold text-[var(--text-primary)] truncate">
                  Publish &amp; Social Studio
                </h2>
                <span className="px-2 py-0.5 rounded-full bg-[var(--bg-card)] border border-[var(--border-subtle)] text-[10px] font-mono font-semibold text-[var(--text-secondary)]">
                  Clip #{clip.id} · {formatDurationPill(clip.duration)}
                </span>
                {aiSourceBadge && (
                  <span className="px-2 py-0.5 rounded-full bg-[var(--accent-subtle)] text-[var(--accent-primary)] border border-[var(--accent-primary)]/30 text-[10px] font-bold">
                    {aiSourceBadge === 'gemini' ? 'Gemini AI Copy' : 'Offline AI Copy'}
                  </span>
                )}
              </div>
            </div>
          </div>

          <div className="flex items-center gap-2 shrink-0">
            <button
              type="button"
              onClick={handleGenerateAllCopy}
              disabled={isGeneratingCopy}
              className="px-3 py-1.5 rounded-xl bg-[var(--accent-primary)] text-[var(--accent-foreground)] font-bold text-xs flex items-center gap-1.5 shadow-sm hover:opacity-90 transition disabled:opacity-55"
            >
              {isGeneratingCopy ? (
                <Loader2 className="w-3.5 h-3.5 animate-spin" />
              ) : (
                <Sparkles className="w-3.5 h-3.5" />
              )}
              <span>{isGeneratingCopy ? 'Writing All 4 Platforms...' : 'Generate All Platform Copy'}</span>
            </button>
            <button
              type="button"
              onClick={onClose}
              className="p-1.5 rounded-lg text-[var(--text-secondary)] hover:text-[var(--text-primary)] hover:bg-[var(--bg-card)] transition"
              title="Close Publish Studio"
            >
              <X className="w-4 h-4" />
            </button>
          </div>
        </div>

        {/* 2-Column Workstation Body */}
        <div className="flex-1 min-h-0 overflow-y-auto p-5">
          <div className="flex flex-col md:flex-row gap-5">
            {/* LEFT COLUMN (38%) — Visual Asset Preview & Instant Creator Delivery */}
            <div className="w-full md:w-[38%] shrink-0 space-y-3.5">
              <div className="rounded-xl border border-[var(--border-subtle)] bg-[var(--bg-card)] p-3.5 space-y-3">
                {/* Compact Cover / Frame Preview */}
                <div className="flex justify-center">
                  <div className="relative w-full max-w-[164px] aspect-[9/16] rounded-xl overflow-hidden bg-black border border-[var(--border-subtle)] shadow-inner flex items-center justify-center">
                    {thumbUrl ? (
                      <img
                        src={thumbUrl}
                        alt={drafts.youtube.title}
                        className="w-full h-full object-cover"
                      />
                    ) : activeVideoUrl ? (
                      <video
                        src={`${activeVideoUrl}#t=0.4`}
                        muted
                        playsInline
                        preload="metadata"
                        className="w-full h-full object-cover"
                      />
                    ) : (
                      <div className="flex flex-col items-center justify-center text-[var(--text-muted)] p-3 text-center gap-1.5">
                        <Video className="w-7 h-7 opacity-60" />
                        <span className="text-[10px]">Ready on Export</span>
                      </div>
                    )}
                    <span className="absolute top-1.5 left-1.5 px-1.5 py-0.5 rounded bg-black/75 text-white text-[9px] font-mono font-bold border border-white/15">
                      {clip.aspect_ratio || '9:16'}
                    </span>
                    <span className="absolute bottom-1.5 right-1.5 px-1.5 py-0.5 rounded bg-black/75 text-white text-[9px] font-mono border border-white/15">
                      {(clip.duration || 0).toFixed(1)}s
                    </span>
                  </div>
                </div>

                {/* Spec Pills */}
                <div className="flex flex-wrap items-center justify-center gap-1.5 text-[10px]">
                  <span className="px-2 py-0.5 rounded-full bg-[var(--bg-elevated)] border border-[var(--border-subtle)] text-[var(--text-primary)] font-mono font-semibold">
                    {getResolutionLabel(clip)}
                  </span>
                  <span className="px-2 py-0.5 rounded-full bg-[var(--bg-elevated)] border border-[var(--border-subtle)] text-[var(--text-primary)] font-mono font-semibold">
                    {(clip.duration || 0).toFixed(1)}s
                  </span>
                  <span className="px-2 py-0.5 rounded-full bg-[var(--bg-elevated)] border border-[var(--border-subtle)] text-[var(--text-secondary)] font-mono">
                    {formatBytes(clip.video_size_bytes)}
                  </span>
                </div>

                {/* Readiness Checkmarks */}
                <div className="grid grid-cols-2 gap-1.5 pt-1 text-[10px]">
                  <div
                    className={`flex items-center gap-1.5 px-2 py-1.5 rounded-lg border ${
                      captionsReady
                        ? 'bg-emerald-500/10 border-emerald-500/30 text-emerald-400'
                        : 'bg-[var(--bg-elevated)] border-[var(--border-subtle)] text-[var(--text-muted)]'
                    }`}
                  >
                    <CheckCircle2 className="w-3 h-3 shrink-0" />
                    <span className="font-semibold truncate">
                      {captionsReady ? 'Burned-In Captions' : 'No Burned Captions'}
                    </span>
                  </div>
                  <div
                    className={`flex items-center gap-1.5 px-2 py-1.5 rounded-lg border ${
                      audioNormalized
                        ? 'bg-emerald-500/10 border-emerald-500/30 text-emerald-400'
                        : 'bg-[var(--bg-elevated)] border-[var(--border-subtle)] text-[var(--text-muted)]'
                    }`}
                  >
                    <CheckCircle2 className="w-3 h-3 shrink-0" />
                    <span className="font-semibold truncate">
                      {audioNormalized ? '-14 LUFS Audio' : 'Original Audio'}
                    </span>
                  </div>
                </div>
              </div>

              {/* 3 Immediate Creator Delivery Buttons (Zero OAuth Required) */}
              <div className="space-y-2">
                <button
                  type="button"
                  onClick={handleDownloadFinalMp4}
                  disabled={isDownloadingMp4}
                  className="w-full py-2.5 px-3 rounded-xl bg-[var(--accent-primary)] text-[var(--accent-foreground)] font-bold text-xs flex items-center justify-center gap-2 hover:opacity-90 transition shadow-sm disabled:opacity-55"
                >
                  {isDownloadingMp4 ? (
                    <Loader2 className="w-4 h-4 animate-spin" />
                  ) : (
                    <Download className="w-4 h-4" />
                  )}
                  <span>{isDownloadingMp4 ? 'Preparing Final MP4...' : 'Download Final MP4'}</span>
                </button>

                <button
                  type="button"
                  onClick={handleDownloadSocialPack}
                  disabled={isExportingPack}
                  className="w-full py-2.5 px-3 rounded-xl bg-[var(--bg-elevated)] hover:border-[var(--accent-primary)]/50 text-[var(--text-primary)] border border-[var(--border-subtle)] font-bold text-xs flex items-center justify-center gap-2 transition disabled:opacity-55"
                >
                  {isExportingPack ? (
                    <Loader2 className="w-4 h-4 animate-spin text-[var(--accent-primary)]" />
                  ) : (
                    <Package className="w-4 h-4 text-[var(--accent-primary)]" />
                  )}
                  <span>{isExportingPack ? 'Building Social Pack...' : 'Download Social Pack (.zip)'}</span>
                </button>

                <button
                  type="button"
                  onClick={handleCopyAndOpenStudio}
                  className="w-full py-2.5 px-3 rounded-xl bg-[var(--bg-card)] hover:border-red-500/40 text-[var(--text-primary)] border border-[var(--border-subtle)] font-semibold text-xs flex items-center justify-center gap-2 transition"
                >
                  <ExternalLink className="w-3.5 h-3.5 text-red-500 shrink-0" />
                  <span className="truncate">Copy Metadata &amp; Open YouTube Studio</span>
                </button>
              </div>

              {actionError && (
                <div className="p-2.5 rounded-xl bg-red-500/10 border border-red-500/35 text-red-400 text-[11px] break-words">
                  {actionError}
                </div>
              )}
            </div>

            {/* RIGHT COLUMN (62%) — 4-Platform Studio + Optional Direct YouTube API Upload */}
            <div className="w-full md:w-[62%] min-w-0 flex-1 space-y-4">
              {/* 4-Platform Segmented Tabs */}
              <div className="grid grid-cols-4 gap-1 bg-[var(--bg-input)] p-1 rounded-xl border border-[var(--border-subtle)]">
                {([
                  { id: 'youtube' as const, label: 'YouTube Shorts' },
                  { id: 'instagram' as const, label: 'Instagram Reels' },
                  { id: 'tiktok' as const, label: 'TikTok' },
                  { id: 'x' as const, label: 'X / Post' },
                ]).map((tab) => (
                  <button
                    key={tab.id}
                    type="button"
                    onClick={() => setActiveTab(tab.id)}
                    className={`py-1.5 px-2 rounded-lg text-[11px] font-bold transition truncate ${
                      activeTab === tab.id
                        ? 'bg-[var(--accent-primary)] text-[var(--accent-foreground)] shadow-sm'
                        : 'text-[var(--text-secondary)] hover:text-[var(--text-primary)]'
                    }`}
                  >
                    {tab.label}
                  </button>
                ))}
              </div>

              {/* Adaptive Platform Metadata Editor */}
              <div className="rounded-xl border border-[var(--border-subtle)] bg-[var(--bg-card)] p-4 space-y-3 text-xs">
                {activeTab === 'youtube' && (
                  <>
                    <div>
                      <div className="flex items-center justify-between mb-1">
                        <label className="text-[var(--text-secondary)] font-semibold">
                          Title (with #Shorts)
                        </label>
                        <span
                          className={`px-1.5 py-0.5 rounded border text-[10px] font-mono font-bold ${counterTone(
                            drafts.youtube.title.length,
                            75,
                            100
                          )}`}
                        >
                          {drafts.youtube.title.length} / 100
                        </span>
                      </div>
                      <input
                        type="text"
                        value={drafts.youtube.title}
                        onChange={(e) =>
                          persistSocialDrafts({
                            ...drafts,
                            youtube: { ...drafts.youtube, title: e.target.value },
                          })
                        }
                        className="w-full bg-[var(--bg-input)] border border-[var(--border-subtle)] rounded-xl px-3 py-2 text-[var(--text-primary)] outline-none focus:border-[var(--accent-primary)]"
                      />
                    </div>

                    <div>
                      <div className="flex items-center justify-between mb-1">
                        <label className="text-[var(--text-secondary)] font-semibold">
                          Description
                        </label>
                        <div className="flex items-center gap-2">
                          <span className="text-[10px] font-mono text-[var(--text-muted)]">
                            {drafts.youtube.description.length} chars
                          </span>
                          <button
                            type="button"
                            onClick={() =>
                              handleCopy(
                                'yt_all',
                                `${drafts.youtube.title}\n\n${drafts.youtube.description}`,
                                'Copied YouTube title & description'
                              )
                            }
                            className="px-2 py-0.5 rounded-md bg-[var(--bg-surface)] border border-[var(--border-subtle)] text-[10px] font-semibold flex items-center gap-1 hover:border-[var(--accent-primary)]/50"
                          >
                            {copiedKey === 'yt_all' ? <Check className="w-3 h-3 text-emerald-400" /> : <Copy className="w-3 h-3" />}
                            <span>{copiedKey === 'yt_all' ? 'Copied' : 'Copy All'}</span>
                          </button>
                        </div>
                      </div>
                      <textarea
                        rows={4}
                        value={drafts.youtube.description}
                        onChange={(e) =>
                          persistSocialDrafts({
                            ...drafts,
                            youtube: { ...drafts.youtube, description: e.target.value },
                          })
                        }
                        className="w-full bg-[var(--bg-input)] border border-[var(--border-subtle)] rounded-xl px-3 py-2 text-[var(--text-primary)] outline-none focus:border-[var(--accent-primary)] resize-y leading-relaxed"
                      />
                    </div>

                    {/* Interactive YouTube Tag Chips */}
                    <div>
                      <div className="flex items-center justify-between mb-1.5">
                        <label className="text-[var(--text-secondary)] font-semibold">
                          Keyword Tags ({drafts.youtube.tags.length})
                        </label>
                        <button
                          type="button"
                          onClick={() =>
                            handleCopy(
                              'yt_tags',
                              drafts.youtube.tags.join(', '),
                              'Copied comma-separated tags'
                            )
                          }
                          className="px-2 py-0.5 rounded-md bg-[var(--bg-surface)] border border-[var(--border-subtle)] text-[10px] font-semibold flex items-center gap-1 hover:border-[var(--accent-primary)]/50"
                        >
                          {copiedKey === 'yt_tags' ? <Check className="w-3 h-3 text-emerald-400" /> : <Copy className="w-3 h-3" />}
                          <span>{copiedKey === 'yt_tags' ? 'Copied' : 'Copy Comma-Separated Tags'}</span>
                        </button>
                      </div>
                      <div className="flex flex-wrap items-center gap-1.5 p-2 rounded-xl bg-[var(--bg-input)] border border-[var(--border-subtle)]">
                        {drafts.youtube.tags.map((tag) => (
                          <span
                            key={tag}
                            className="inline-flex items-center gap-1 px-2 py-0.5 rounded-md bg-[var(--bg-card)] border border-[var(--border-subtle)] text-[11px] font-medium text-[var(--text-primary)]"
                          >
                            <span>#{tag}</span>
                            <button
                              type="button"
                              onClick={() => handleRemoveTagChip(tag)}
                              className="text-[var(--text-muted)] hover:text-red-400"
                              title={`Remove ${tag}`}
                            >
                              <X className="w-2.5 h-2.5" />
                            </button>
                          </span>
                        ))}
                        <div className="inline-flex items-center gap-1">
                          <input
                            type="text"
                            value={newTagInput}
                            onChange={(e) => setNewTagInput(e.target.value)}
                            onKeyDown={(e) => {
                              if (e.key === 'Enter') {
                                e.preventDefault();
                                handleAddTagChip();
                              }
                            }}
                            placeholder="Add tag..."
                            className="w-20 bg-transparent text-[11px] text-[var(--text-primary)] outline-none placeholder:text-[var(--text-muted)]"
                          />
                          <button
                            type="button"
                            onClick={handleAddTagChip}
                            className="p-0.5 rounded text-[var(--accent-primary)] hover:bg-[var(--accent-subtle)]"
                            title="Add tag"
                          >
                            <Plus className="w-3 h-3" />
                          </button>
                        </div>
                      </div>
                    </div>

                    {/* Pinned Comment */}
                    <div>
                      <div className="flex items-center justify-between mb-1">
                        <label className="text-[var(--text-secondary)] font-semibold">
                          Pinned First Comment
                        </label>
                        <button
                          type="button"
                          onClick={() =>
                            handleCopy('yt_comment', drafts.youtube.pinned_comment, 'Copied pinned comment')
                          }
                          className="px-2 py-0.5 rounded-md bg-[var(--bg-surface)] border border-[var(--border-subtle)] text-[10px] font-semibold flex items-center gap-1 hover:border-[var(--accent-primary)]/50"
                        >
                          {copiedKey === 'yt_comment' ? <Check className="w-3 h-3 text-emerald-400" /> : <Copy className="w-3 h-3" />}
                          <span>{copiedKey === 'yt_comment' ? 'Copied' : 'Copy Comment'}</span>
                        </button>
                      </div>
                      <input
                        type="text"
                        value={drafts.youtube.pinned_comment}
                        onChange={(e) =>
                          persistSocialDrafts({
                            ...drafts,
                            youtube: { ...drafts.youtube, pinned_comment: e.target.value },
                          })
                        }
                        className="w-full bg-[var(--bg-input)] border border-[var(--border-subtle)] rounded-xl px-3 py-2 text-[var(--text-primary)] outline-none focus:border-[var(--accent-primary)]"
                      />
                    </div>
                  </>
                )}

                {activeTab === 'instagram' && (
                  <>
                    <div>
                      <div className="flex items-center justify-between mb-1">
                        <label className="text-[var(--text-secondary)] font-semibold">
                          Reels Hook Line
                        </label>
                        <span className={`px-1.5 py-0.5 rounded border text-[10px] font-mono font-bold ${counterTone(drafts.instagram.title.length, 65, 90)}`}>
                          {drafts.instagram.title.length} / 90
                        </span>
                      </div>
                      <input
                        type="text"
                        value={drafts.instagram.title}
                        onChange={(e) =>
                          persistSocialDrafts({
                            ...drafts,
                            instagram: { ...drafts.instagram, title: e.target.value },
                          })
                        }
                        className="w-full bg-[var(--bg-input)] border border-[var(--border-subtle)] rounded-xl px-3 py-2 text-[var(--text-primary)] outline-none focus:border-[var(--accent-primary)]"
                      />
                    </div>
                    <div>
                      <div className="flex items-center justify-between mb-1">
                        <label className="text-[var(--text-secondary)] font-semibold">
                          Caption &amp; Niche Reels Hashtags
                        </label>
                        <div className="flex items-center gap-2">
                          <span className={`px-1.5 py-0.5 rounded border text-[10px] font-mono font-bold ${counterTone(drafts.instagram.caption.length, 1800, 2200)}`}>
                            {drafts.instagram.caption.length} / 2200
                          </span>
                          <button
                            type="button"
                            onClick={() =>
                              handleCopy('ig_all', drafts.instagram.caption, 'Copied Instagram Reels caption')
                            }
                            className="px-2 py-0.5 rounded-md bg-[var(--bg-surface)] border border-[var(--border-subtle)] text-[10px] font-semibold flex items-center gap-1 hover:border-[var(--accent-primary)]/50"
                          >
                            {copiedKey === 'ig_all' ? <Check className="w-3 h-3 text-emerald-400" /> : <Copy className="w-3 h-3" />}
                            <span>{copiedKey === 'ig_all' ? 'Copied' : 'Copy All'}</span>
                          </button>
                        </div>
                      </div>
                      <textarea
                        rows={5}
                        value={drafts.instagram.caption}
                        onChange={(e) =>
                          persistSocialDrafts({
                            ...drafts,
                            instagram: { ...drafts.instagram, caption: e.target.value },
                          })
                        }
                        className="w-full bg-[var(--bg-input)] border border-[var(--border-subtle)] rounded-xl px-3 py-2 text-[var(--text-primary)] outline-none focus:border-[var(--accent-primary)] leading-relaxed"
                      />
                    </div>
                    <div>
                      <div className="flex items-center justify-between mb-1">
                        <label className="text-[var(--text-secondary)] font-semibold">
                          Pinned First Comment
                        </label>
                        <button
                          type="button"
                          onClick={() =>
                            handleCopy('ig_comment', drafts.instagram.pinned_comment, 'Copied pinned comment')
                          }
                          className="px-2 py-0.5 rounded-md bg-[var(--bg-surface)] border border-[var(--border-subtle)] text-[10px] font-semibold flex items-center gap-1 hover:border-[var(--accent-primary)]/50"
                        >
                          {copiedKey === 'ig_comment' ? <Check className="w-3 h-3 text-emerald-400" /> : <Copy className="w-3 h-3" />}
                          <span>{copiedKey === 'ig_comment' ? 'Copied' : 'Copy Comment'}</span>
                        </button>
                      </div>
                      <input
                        type="text"
                        value={drafts.instagram.pinned_comment}
                        onChange={(e) =>
                          persistSocialDrafts({
                            ...drafts,
                            instagram: { ...drafts.instagram, pinned_comment: e.target.value },
                          })
                        }
                        className="w-full bg-[var(--bg-input)] border border-[var(--border-subtle)] rounded-xl px-3 py-2 text-[var(--text-primary)] outline-none focus:border-[var(--accent-primary)]"
                      />
                    </div>
                  </>
                )}

                {activeTab === 'tiktok' && (
                  <>
                    <div>
                      <div className="flex items-center justify-between mb-1">
                        <label className="text-[var(--text-secondary)] font-semibold">
                          TikTok Hook Headline
                        </label>
                        <span className={`px-1.5 py-0.5 rounded border text-[10px] font-mono font-bold ${counterTone(drafts.tiktok.title.length, 65, 90)}`}>
                          {drafts.tiktok.title.length} / 90
                        </span>
                      </div>
                      <input
                        type="text"
                        value={drafts.tiktok.title}
                        onChange={(e) =>
                          persistSocialDrafts({
                            ...drafts,
                            tiktok: { ...drafts.tiktok, title: e.target.value },
                          })
                        }
                        className="w-full bg-[var(--bg-input)] border border-[var(--border-subtle)] rounded-xl px-3 py-2 text-[var(--text-primary)] outline-none focus:border-[var(--accent-primary)]"
                      />
                    </div>
                    <div>
                      <div className="flex items-center justify-between mb-1">
                        <label className="text-[var(--text-secondary)] font-semibold">
                          Caption &amp; Trending Tags
                        </label>
                        <div className="flex items-center gap-2">
                          <span className={`px-1.5 py-0.5 rounded border text-[10px] font-mono font-bold ${counterTone(drafts.tiktok.caption.length, 1800, 2200)}`}>
                            {drafts.tiktok.caption.length} / 2200
                          </span>
                          <button
                            type="button"
                            onClick={() =>
                              handleCopy('tt_all', drafts.tiktok.caption, 'Copied TikTok caption')
                            }
                            className="px-2 py-0.5 rounded-md bg-[var(--bg-surface)] border border-[var(--border-subtle)] text-[10px] font-semibold flex items-center gap-1 hover:border-[var(--accent-primary)]/50"
                          >
                            {copiedKey === 'tt_all' ? <Check className="w-3 h-3 text-emerald-400" /> : <Copy className="w-3 h-3" />}
                            <span>{copiedKey === 'tt_all' ? 'Copied' : 'Copy All'}</span>
                          </button>
                        </div>
                      </div>
                      <textarea
                        rows={5}
                        value={drafts.tiktok.caption}
                        onChange={(e) =>
                          persistSocialDrafts({
                            ...drafts,
                            tiktok: { ...drafts.tiktok, caption: e.target.value },
                          })
                        }
                        className="w-full bg-[var(--bg-input)] border border-[var(--border-subtle)] rounded-xl px-3 py-2 text-[var(--text-primary)] outline-none focus:border-[var(--accent-primary)] leading-relaxed"
                      />
                    </div>
                    <div>
                      <div className="flex items-center justify-between mb-1">
                        <label className="text-[var(--text-secondary)] font-semibold">
                          Pinned First Comment
                        </label>
                        <button
                          type="button"
                          onClick={() =>
                            handleCopy('tt_comment', drafts.tiktok.pinned_comment, 'Copied pinned comment')
                          }
                          className="px-2 py-0.5 rounded-md bg-[var(--bg-surface)] border border-[var(--border-subtle)] text-[10px] font-semibold flex items-center gap-1 hover:border-[var(--accent-primary)]/50"
                        >
                          {copiedKey === 'tt_comment' ? <Check className="w-3 h-3 text-emerald-400" /> : <Copy className="w-3 h-3" />}
                          <span>{copiedKey === 'tt_comment' ? 'Copied' : 'Copy Comment'}</span>
                        </button>
                      </div>
                      <input
                        type="text"
                        value={drafts.tiktok.pinned_comment}
                        onChange={(e) =>
                          persistSocialDrafts({
                            ...drafts,
                            tiktok: { ...drafts.tiktok, pinned_comment: e.target.value },
                          })
                        }
                        className="w-full bg-[var(--bg-input)] border border-[var(--border-subtle)] rounded-xl px-3 py-2 text-[var(--text-primary)] outline-none focus:border-[var(--accent-primary)]"
                      />
                    </div>
                  </>
                )}

                {activeTab === 'x' && (
                  <>
                    <div>
                      <div className="flex items-center justify-between mb-1">
                        <label className="text-[var(--text-secondary)] font-semibold">
                          X Post Text
                        </label>
                        <div className="flex items-center gap-2">
                          <span className={`px-1.5 py-0.5 rounded border text-[10px] font-mono font-bold ${counterTone(drafts.x.post_text.length, 240, 280)}`}>
                            {drafts.x.post_text.length} / 280
                          </span>
                          <button
                            type="button"
                            onClick={() =>
                              handleCopy('x_all', drafts.x.post_text, 'Copied X post text')
                            }
                            className="px-2 py-0.5 rounded-md bg-[var(--bg-surface)] border border-[var(--border-subtle)] text-[10px] font-semibold flex items-center gap-1 hover:border-[var(--accent-primary)]/50"
                          >
                            {copiedKey === 'x_all' ? <Check className="w-3 h-3 text-emerald-400" /> : <Copy className="w-3 h-3" />}
                            <span>{copiedKey === 'x_all' ? 'Copied' : 'Copy All'}</span>
                          </button>
                        </div>
                      </div>
                      <textarea
                        rows={5}
                        value={drafts.x.post_text}
                        onChange={(e) =>
                          persistSocialDrafts({
                            ...drafts,
                            x: { ...drafts.x, post_text: e.target.value },
                          })
                        }
                        className="w-full bg-[var(--bg-input)] border border-[var(--border-subtle)] rounded-xl px-3 py-2 text-[var(--text-primary)] outline-none focus:border-[var(--accent-primary)] leading-relaxed"
                      />
                    </div>
                    <div>
                      <div className="flex items-center justify-between mb-1">
                        <label className="text-[var(--text-secondary)] font-semibold">
                          Pinned / Reply Thread Hook
                        </label>
                        <button
                          type="button"
                          onClick={() =>
                            handleCopy('x_comment', drafts.x.pinned_comment, 'Copied X reply')
                          }
                          className="px-2 py-0.5 rounded-md bg-[var(--bg-surface)] border border-[var(--border-subtle)] text-[10px] font-semibold flex items-center gap-1 hover:border-[var(--accent-primary)]/50"
                        >
                          {copiedKey === 'x_comment' ? <Check className="w-3 h-3 text-emerald-400" /> : <Copy className="w-3 h-3" />}
                          <span>{copiedKey === 'x_comment' ? 'Copied' : 'Copy Comment'}</span>
                        </button>
                      </div>
                      <input
                        type="text"
                        value={drafts.x.pinned_comment}
                        onChange={(e) =>
                          persistSocialDrafts({
                            ...drafts,
                            x: { ...drafts.x, pinned_comment: e.target.value },
                          })
                        }
                        className="w-full bg-[var(--bg-input)] border border-[var(--border-subtle)] rounded-xl px-3 py-2 text-[var(--text-primary)] outline-none focus:border-[var(--accent-primary)]"
                      />
                    </div>
                  </>
                )}
              </div>

              {/* Collapsible Direct YouTube API Auto-Upload Card */}
              <div className="rounded-xl border border-[var(--border-subtle)] bg-[var(--bg-elevated)] p-3.5 space-y-3 text-xs">
                {isLoadingYtStatus ? (
                  <div className="flex items-center gap-2 text-[11px] text-[var(--text-muted)]">
                    <Loader2 className="w-3.5 h-3.5 animate-spin" />
                    <span>Checking YouTube channel link...</span>
                  </div>
                ) : !ytStatus?.connected ? (
                  <>
                    <div className="flex items-center justify-between gap-2">
                      <div className="flex items-center gap-2 min-w-0">
                        <span className="w-2 h-2 rounded-full bg-[var(--text-muted)] shrink-0" />
                        <span className="font-semibold text-[var(--text-primary)] truncate">
                          YouTube Direct Upload · Not Linked
                        </span>
                      </div>
                      <button
                        type="button"
                        onClick={() => {
                          setShowLinkDrawer((prev) => !prev);
                          setYtAuthError(null);
                        }}
                        className="px-3 py-1 rounded-lg bg-[var(--bg-surface)] border border-[var(--border-subtle)] hover:border-[var(--accent-primary)] text-[var(--text-primary)] text-[11px] font-bold flex items-center gap-1.5 transition shrink-0"
                      >
                        <Link2 className="w-3 h-3 text-[var(--accent-primary)]" />
                        <span>Link Account</span>
                        {showLinkDrawer ? <ChevronUp className="w-3 h-3" /> : <ChevronDown className="w-3 h-3" />}
                      </button>
                    </div>

                    {showLinkDrawer && (
                      <div className="rounded-xl bg-[var(--bg-surface)] border border-[var(--border-subtle)] p-3 space-y-3">
                        <div className="grid grid-cols-2 gap-1 bg-[var(--bg-input)] p-1 rounded-lg border border-[var(--border-subtle)]">
                          <button
                            type="button"
                            onClick={() => setOauthSetupTab('json')}
                            className={`py-1 rounded-md text-[11px] font-bold transition ${
                              oauthSetupTab === 'json'
                                ? 'bg-[var(--accent-primary)] text-[var(--accent-foreground)]'
                                : 'text-[var(--text-secondary)] hover:text-[var(--text-primary)]'
                            }`}
                          >
                            Upload client_secret.json
                          </button>
                          <button
                            type="button"
                            onClick={() => setOauthSetupTab('paste')}
                            className={`py-1 rounded-md text-[11px] font-bold transition ${
                              oauthSetupTab === 'paste'
                                ? 'bg-[var(--accent-primary)] text-[var(--accent-foreground)]'
                                : 'text-[var(--text-secondary)] hover:text-[var(--text-primary)]'
                            }`}
                          >
                            Paste Client ID &amp; Secret
                          </button>
                        </div>

                        {oauthSetupTab === 'json' ? (
                          <div className="space-y-2 text-[11px]">
                            <p className="text-[var(--text-secondary)] leading-relaxed">
                              In Google Cloud Console enable <strong>YouTube Data API v3</strong>, create a{' '}
                              <strong>Desktop App OAuth Client ID</strong>, and upload the downloaded JSON file.
                            </p>
                            <div className="flex items-center justify-between gap-2 flex-wrap">
                              <input
                                ref={clientSecretsInputRef}
                                type="file"
                                accept=".json,application/json"
                                onChange={handleUploadClientSecrets}
                                className="hidden"
                              />
                              <button
                                type="button"
                                onClick={() => clientSecretsInputRef.current?.click()}
                                disabled={isUploadingSecrets}
                                className="px-3 py-1.5 rounded-lg bg-[var(--accent-subtle)] border border-[var(--accent-primary)]/40 text-[var(--accent-primary)] font-bold flex items-center gap-1.5 hover:opacity-90 transition disabled:opacity-50"
                              >
                                {isUploadingSecrets ? <Loader2 className="w-3.5 h-3.5 animate-spin" /> : <Upload className="w-3.5 h-3.5" />}
                                <span>{isUploadingSecrets ? 'Saving JSON...' : 'Select client_secret.json'}</span>
                              </button>
                              {ytStatus?.client_configured && (
                                <span className="text-[10px] font-semibold text-emerald-400">
                                  ✓ OAuth client ready
                                </span>
                              )}
                            </div>
                          </div>
                        ) : (
                          <div className="space-y-2 text-[11px]">
                            <input
                              type="text"
                              value={clientIdInput}
                              onChange={(e) => setClientIdInput(e.target.value)}
                              placeholder="Google OAuth Client ID (e.g. ...apps.googleusercontent.com)"
                              className="w-full bg-[var(--bg-input)] border border-[var(--border-subtle)] rounded-lg px-2.5 py-1.5 text-[var(--text-primary)] outline-none focus:border-[var(--accent-primary)]"
                            />
                            <div className="flex items-center gap-2">
                              <input
                                type="password"
                                value={clientSecretInput}
                                onChange={(e) => setClientSecretInput(e.target.value)}
                                placeholder="Google OAuth Client Secret"
                                className="flex-1 bg-[var(--bg-input)] border border-[var(--border-subtle)] rounded-lg px-2.5 py-1.5 text-[var(--text-primary)] outline-none focus:border-[var(--accent-primary)]"
                              />
                              <button
                                type="button"
                                onClick={handleSaveCredentials}
                                disabled={isSavingCreds}
                                className="px-3 py-1.5 rounded-lg bg-[var(--accent-subtle)] border border-[var(--accent-primary)]/40 text-[var(--accent-primary)] font-bold flex items-center gap-1.5 hover:opacity-90 disabled:opacity-50 shrink-0"
                              >
                                {isSavingCreds ? <Loader2 className="w-3.5 h-3.5 animate-spin" /> : <KeyRound className="w-3.5 h-3.5" />}
                                <span>Save Credentials</span>
                              </button>
                            </div>
                          </div>
                        )}

                        <div className="pt-1 border-t border-[var(--border-subtle)] flex items-center justify-between gap-2">
                          <span className="text-[10px] text-[var(--text-muted)]">
                            {ytStatus?.client_configured ? 'Credentials saved · Ready to authorize' : 'Step 2: Authorize your channel'}
                          </span>
                          <button
                            type="button"
                            onClick={handleConnectGoogle}
                            disabled={isConnectingYt}
                            className="px-3.5 py-1.5 rounded-lg bg-red-600 hover:bg-red-700 text-white font-bold text-[11px] flex items-center gap-1.5 transition disabled:opacity-50"
                          >
                            {isConnectingYt ? <Loader2 className="w-3.5 h-3.5 animate-spin" /> : <Link2 className="w-3.5 h-3.5" />}
                            <span>{isConnectingYt ? 'Waiting for Google Sign-In...' : 'Connect Google Account'}</span>
                          </button>
                        </div>
                      </div>
                    )}

                    {ytAuthError && (
                      <div className="rounded-lg bg-red-500/10 border border-red-500/40 px-2.5 py-2 text-[11px] text-red-400">
                        {ytAuthError}
                      </div>
                    )}
                  </>
                ) : (
                  <div className="space-y-3">
                    <div className="flex items-center justify-between gap-2 flex-wrap">
                      <span
                        data-testid="yt-connected-badge"
                        className="inline-flex items-center gap-1.5 text-[11px] font-bold text-emerald-400"
                      >
                        <span className="w-2 h-2 rounded-full bg-emerald-400" />
                        <span>Connected: {ytStatus.channel_title || 'YouTube Channel'}</span>
                      </span>
                      <button
                        type="button"
                        onClick={handleDisconnectYouTube}
                        className="px-2 py-1 rounded-lg bg-[var(--bg-surface)] border border-[var(--border-subtle)] text-[var(--text-muted)] hover:text-red-400 hover:border-red-500/40 text-[10px] font-semibold flex items-center gap-1 transition"
                      >
                        <Unlink className="w-3 h-3" />
                        <span>Disconnect</span>
                      </button>
                    </div>

                    <div className="flex flex-wrap items-end gap-2.5">
                      <div>
                        <label className="text-[10px] text-[var(--text-muted)] font-semibold block mb-1">
                          Visibility
                        </label>
                        <select
                          value={publishPrivacy}
                          onChange={(e) => setPublishPrivacy(e.target.value as 'private' | 'unlisted' | 'public')}
                          className="bg-[var(--bg-input)] border border-[var(--border-subtle)] rounded-lg px-2.5 py-1.5 text-[var(--text-primary)] outline-none text-xs"
                        >
                          <option value="private">Private</option>
                          <option value="unlisted">Unlisted</option>
                          <option value="public">Public</option>
                        </select>
                      </div>

                      <div>
                        <label className="text-[10px] text-[var(--text-muted)] font-semibold block mb-1">
                          Category
                        </label>
                        <select
                          value={drafts.youtube.category_id}
                          onChange={(e) =>
                            persistSocialDrafts({
                              ...drafts,
                              youtube: { ...drafts.youtube, category_id: e.target.value },
                            })
                          }
                          className="bg-[var(--bg-input)] border border-[var(--border-subtle)] rounded-lg px-2.5 py-1.5 text-[var(--text-primary)] outline-none text-xs"
                        >
                          {YOUTUBE_CATEGORY_OPTIONS.map((c) => (
                            <option key={c.id} value={c.id}>
                              {c.label}
                            </option>
                          ))}
                        </select>
                      </div>

                      <button
                        type="button"
                        onClick={handleDirectYouTubeUpload}
                        disabled={isPublishing || !clipIsRendered}
                        title={
                          !clipIsRendered
                            ? 'Download Final MP4 / render clip first before direct API upload'
                            : 'Upload rendered MP4 to your connected YouTube channel'
                        }
                        className="ml-auto px-4 py-2 rounded-xl bg-red-600 hover:bg-red-700 text-white font-bold text-xs flex items-center gap-1.5 shadow-sm transition disabled:opacity-45"
                      >
                        {isPublishing ? (
                          <Loader2 className="w-3.5 h-3.5 animate-spin" />
                        ) : (
                          <span>▶</span>
                        )}
                        <span>{isPublishing ? 'Uploading MP4...' : 'Upload Directly to YouTube'}</span>
                      </button>
                    </div>

                    {!clipIsRendered && (
                      <p className="text-[10px] text-[var(--text-muted)]">
                        Render clip or click <strong>Download Final MP4</strong> on the left first to enable direct YouTube upload.
                      </p>
                    )}

                    {publishSuccess && (
                      <div className="p-2.5 rounded-xl bg-emerald-500/10 border border-emerald-500/35 text-emerald-400 space-y-1 text-xs">
                        <div className="flex items-center justify-between gap-2">
                          <span className="font-bold">
                            Uploaded to {publishSuccess.channel_title || 'YouTube'} ({publishSuccess.privacy_status})
                          </span>
                          <a
                            href={publishSuccess.url}
                            target="_blank"
                            rel="noreferrer"
                            className="underline font-bold inline-flex items-center gap-1 shrink-0"
                          >
                            <span>Open Short</span>
                            <ExternalLink className="w-3 h-3" />
                          </a>
                        </div>
                        <div className="font-mono text-[10px] break-all">{publishSuccess.url}</div>
                      </div>
                    )}

                    {publishError && (
                      <div className="p-2.5 rounded-xl bg-red-500/10 border border-red-500/40 text-red-400 text-[11px] break-words">
                        {publishError}
                      </div>
                    )}
                  </div>
                )}
              </div>
            </div>
          </div>
        </div>
      </div>
    </div>
  );
};
