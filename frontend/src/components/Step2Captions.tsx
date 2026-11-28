import React, { useState } from 'react';
import {
  Type, Sparkles, Move, Palette, Upload, AlignLeft, AlignCenter, AlignRight,
  RefreshCw, Volume2, Globe, EyeOff, Eye, Save, Check, RotateCcw, X, Scissors, ShieldAlert, Zap,
  Users, ShieldCheck
} from 'lucide-react';
import { ViralClip, CaptionStylePreset } from '../types';
import { api } from '../api/client';

interface Step2CaptionsProps {
  clip: ViralClip | null;
  projectId?: number;
  captionStyles: CaptionStylePreset[];
  availableFonts: string[];
  onUpdateClip: (updates: Partial<ViralClip>) => void;
  onUploadFont: (file: File) => void;
  onSaveQuickStyle?: (name: string) => void;
  onSeekTimestamp?: (seconds: number) => void;
  currentPlaybackTime?: number;
  detectedLanguage?: string;
}

export const Step2Captions: React.FC<Step2CaptionsProps> = ({
  clip,
  projectId,
  availableFonts,
  onUpdateClip,
  onUploadFont,
  onSaveQuickStyle,
  onSeekTimestamp,
  currentPlaybackTime,
  detectedLanguage
}) => {
  const [subTab, setSubTab] = useState<'styles' | 'typography' | 'colors' | 'animation'>('styles');
  const [selectedWordIdx, setSelectedWordIdx] = useState<number | null>(null);
  const [inlineEditIdx, setInlineEditIdx] = useState<number | null>(null);
  const [isRetranscribing, setIsRetranscribing] = useState(false);
  const [retranscribeMsg, setRetranscribeMsg] = useState<string | null>(null);

  const [showSaveStyle, setShowSaveStyle] = useState(false);
  const [styleNameInput, setStyleNameInput] = useState('');
  const [styleSavedFeedback, setStyleSavedFeedback] = useState(false);
  const [isDiarizing, setIsDiarizing] = useState(false);
  const [diarizeMsg, setDiarizeMsg] = useState<string | null>(null);
  const [editingSpeakerId, setEditingSpeakerId] = useState<string | null>(null);
  const [bilingualLang, setBilingualLang] = useState<string>('Spanish');
  const [isTranslating, setIsTranslating] = useState(false);
  const [translateMsg, setTranslateMsg] = useState<string | null>(null);
  const [isGeneratingChapters, setIsGeneratingChapters] = useState(false);
  const [chapterMsg, setChapterMsg] = useState<string | null>(null);

  if (!clip) {
    return (
      <div className="p-8 rounded-2xl bg-[var(--bg-card)] border border-[var(--border-subtle)] text-center text-xs text-[var(--text-secondary)]">
        Select a clip from the Viral Clips sidebar to edit captions and voice transcript.
      </div>
    );
  }

  const words = clip.transcript_words || [];

  const defaultStyles = [
    { name: 'Cinematic', font: 'Playfair Display', base: '#FFFFFF', active: '#F59E0B', preview: 'THE MASTERPIECE', italic: true, bold: false },
    { name: 'Karaoke pop', font: 'Montserrat', base: '#FFFFFF', active: '#C6F32F', preview: 'UNSTOPPABLE POP', italic: false, bold: true },
    { name: 'Bold impact', font: 'Impact', base: '#FFFFFF', active: '#EF4444', preview: 'RAW POWER', italic: false, bold: true },
    { name: 'Punch Box', font: 'Montserrat', base: '#FFFFFF', active: '#FACC15', preview: 'HIGHLIGHT BOX', box: true, bold: true },
    { name: 'Clean Yellow', font: 'Poppins', base: '#FFFFFF', active: '#FACC15', preview: 'Crisp Yellow Pop', bold: true },
    { name: 'Neon Glow', font: 'Montserrat', base: '#E0F2FE', active: '#06B6D4', preview: 'CYBER NEON', bold: true },
    { name: 'Underline Pop', font: 'Montserrat', base: '#FFFFFF', active: '#EC4899', preview: 'PINK UNDERLINE', underline: true, bold: true },
    { name: 'Documentary', font: 'Roboto', base: '#F4F4F5', active: '#E4E4E7', preview: 'Clean lower-third', bold: false },
    { name: 'Hormozi', font: 'Montserrat', base: '#FFFFFF', active: '#FFFF00', preview: '$100M SECRETS', bold: true },
    { name: 'Creator White', font: 'Poppins', base: '#FFFFFF', active: '#FFFFFF', preview: 'Crisp Minimal', bold: true },
    { name: 'Anton Impact', font: 'Anton', base: '#FFFFFF', active: '#F59E0B', preview: 'TALL CONDENSED', bold: true },
    { name: 'Brand Block', font: 'Montserrat', base: '#000000', active: '#000000', preview: 'AMBER BLOCK', block: true, bold: true }
  ];

  const kineticPhysics = [
    { id: 'Pop Bounce', label: 'Pop Bounce', desc: 'Elastic punch settle' },
    { id: 'Slide Up', label: 'Slide Up', desc: 'Vertical ease-out rise' },
    { id: 'Scale Slam', label: 'Scale Slam', desc: 'High-impact drop' },
    { id: 'Blur Fade', label: 'Blur Fade', desc: 'Soft optical resolve' },
    { id: 'Typewriter Reveal', label: 'Typewriter Reveal', desc: 'Sequential word reveal' },
    { id: 'Instant Cut', label: 'Instant Cut', desc: 'Frame-locked hard cut' },
  ];
  const animationsOnce = ['Fade in', 'Soft bounce', 'Elastic', 'Pulse'];
  const animationsLine = ['Karaoke', 'Blur scroll'];

  const gridPositions = [
    { id: 'top_left', label: 'Top Left', x: 15, y: 18 },
    { id: 'top_center', label: 'Top Center', x: 50, y: 18 },
    { id: 'top_right', label: 'Top Right', x: 85, y: 18 },
    { id: 'mid_left', label: 'Mid Left', x: 15, y: 50 },
    { id: 'center', label: 'Center', x: 50, y: 50 },
    { id: 'mid_right', label: 'Mid Right', x: 85, y: 50 },
    { id: 'bottom_left', label: 'Bottom Left', x: 15, y: 78 },
    { id: 'bottom_center', label: 'Bottom Center', x: 50, y: 78 },
    { id: 'bottom_right', label: 'Bottom Right', x: 85, y: 78 }
  ];

  const formatTimestamp = (sec: number) => {
    const safe = Math.max(0, sec || 0);
    const mins = Math.floor(safe / 60);
    const rem = (safe % 60).toFixed(1).padStart(4, '0');
    return `${String(mins).padStart(2, '0')}:${rem}s`;
  };

  const handleApplyPreset = (s: typeof defaultStyles[0]) => {
    onUpdateClip({
      caption_style: s.name,
      caption_font: s.font,
      base_color: s.base,
      active_color: s.active
    });
  };

  const handleWordTextChange = (index: number, newWord: string) => {
    const updated = [...words];
    if (updated[index]) {
      updated[index] = { ...updated[index], word: newWord };
      onUpdateClip({ transcript_words: updated });
    }
  };

  const handleToggleDeleteWord = (index: number) => {
    const updated = [...words];
    if (updated[index]) {
      updated[index] = {
        ...updated[index],
        deleted: !updated[index].deleted,
      };
      onUpdateClip({ transcript_words: updated });
    }
  };

  const handleToggleCutWord = (index: number) => {
    const updated = [...words];
    if (updated[index]) {
      const nextCut = !updated[index].cut_from_video;
      updated[index] = {
        ...updated[index],
        cut_from_video: nextCut,
      };
      onUpdateClip({ transcript_words: updated });
    }
  };

  const handleRetranscribe = async () => {
    const pId = projectId || clip.project_id;
    if (!pId || !clip.id) return;
    try {
      setIsRetranscribing(true);
      setRetranscribeMsg('Extracting audio & running faster-whisper VAD...');
      const res = await api.retranscribeClip(pId, clip.id);
      if (res && res.transcript_words) {
        onUpdateClip({ transcript_words: res.transcript_words });
        setSelectedWordIdx(null);
        setRetranscribeMsg(`Transcribed ${res.words_count} spoken words.`);
        setTimeout(() => setRetranscribeMsg(null), 3000);
      }
    } catch (err: any) {
      setRetranscribeMsg(`Error: ${err.message || 'Failed to re-transcribe'}`);
      setTimeout(() => setRetranscribeMsg(null), 4000);
    } finally {
      setIsRetranscribing(false);
    }
  };

  const handleCommitStyleSave = () => {
    if (!styleNameInput.trim() || !onSaveQuickStyle) return;
    onSaveQuickStyle(styleNameInput.trim());
    setStyleNameInput('');
    setShowSaveStyle(false);
    setStyleSavedFeedback(true);
    setTimeout(() => setStyleSavedFeedback(false), 2000);
  };

  const selectedWord = selectedWordIdx !== null ? words[selectedWordIdx] : null;

  return (
    <div className="space-y-4 text-xs animate-fadeIn w-full text-[var(--text-primary)]">
      {/* A. Descript-Style Inline Voice Transcript Editor */}
      <div className="bg-[var(--bg-card)] border border-[var(--border-subtle)] rounded-xl p-3.5 space-y-2.5">
        <div className="flex flex-wrap items-center justify-between gap-2">
          <div className="flex items-center gap-2 flex-wrap">
            <span className="font-bold text-xs flex items-center gap-1.5 text-[var(--text-primary)]">
              <Volume2 className="w-3.5 h-3.5 text-[var(--accent-primary)]" />
              Voice Transcript
            </span>
            <span className="text-[10px] font-medium px-2 py-0.5 rounded-full bg-[var(--bg-input)] border border-[var(--border-subtle)] text-[var(--text-secondary)] flex items-center gap-1">
              <Globe className="w-2.5 h-2.5" />
              {detectedLanguage || clip.caption_language || 'Auto-detected'}
            </span>
            <span className="text-[10px] font-mono px-2 py-0.5 rounded-full bg-[var(--accent-subtle)] text-[var(--accent-primary)] border border-[var(--accent-primary)]/30 tabular-nums">
              {words.length} words
            </span>
            <span className="hidden sm:inline text-[10px] text-[var(--text-secondary)]">
              Click any word to seek · Double-click to edit
            </span>
          </div>

          <button
            type="button"
            onClick={handleRetranscribe}
            disabled={isRetranscribing}
            className="px-2.5 py-1 rounded-lg bg-[var(--bg-input)] border border-[var(--border-subtle)] hover:border-[var(--accent-primary)]/50 text-[11px] font-semibold text-[var(--text-primary)] transition flex items-center gap-1.5 disabled:opacity-50"
          >
            <RefreshCw className={`w-3 h-3 text-[var(--accent-primary)] ${isRetranscribing ? 'animate-spin' : ''}`} />
            <span>{isRetranscribing ? 'Transcribing...' : 'Re-transcribe'}</span>
          </button>
        </div>

        {retranscribeMsg && (
          <div className="text-[11px] font-mono text-[var(--accent-primary)] bg-[var(--accent-subtle)] px-2.5 py-1.5 rounded-lg border border-[var(--accent-primary)]/25">
            {retranscribeMsg}
          </div>
        )}

        {/* Descript Pacing & Censoring Controls */}
        <div className="flex flex-wrap items-center gap-2 pt-0.5 pb-1 border-b border-[var(--border-subtle)]/70">
          <button
            type="button"
            onClick={() => onUpdateClip({ tighten_pacing: !clip.tighten_pacing })}
            className={`px-2.5 py-1 rounded-lg border text-[11px] font-semibold flex items-center gap-1.5 transition ${clip.tighten_pacing
              ? 'bg-[var(--accent-subtle)] border-[var(--accent-primary)] text-[var(--accent-primary)]'
              : 'bg-[var(--bg-input)] border-[var(--border-subtle)] text-[var(--text-secondary)] hover:text-[var(--text-primary)]'
              }`}
            title="Automatically jump-cut awkward pauses longer than the threshold"
          >
            <Zap className="w-3 h-3" />
            <span>Tighten Pacing</span>
          </button>

          {clip.tighten_pacing && (
            <div className="flex items-center gap-2 px-2.5 py-1 rounded-lg bg-[var(--bg-input)] border border-[var(--border-subtle)]">
              <span className="text-[10px] font-semibold text-[var(--text-secondary)] whitespace-nowrap">
                Pause &gt; {(clip.silence_threshold ?? 0.5).toFixed(2)}s
              </span>
              <input
                type="range"
                min={0.35}
                max={1.0}
                step={0.05}
                value={clip.silence_threshold ?? 0.5}
                onChange={(e) => onUpdateClip({ silence_threshold: parseFloat(e.target.value) })}
                className="w-20 h-1 bg-[var(--bg-card)] rounded appearance-none cursor-pointer"
                aria-label="Silence gap threshold seconds"
              />
            </div>
          )}

          <button
            type="button"
            onClick={() => onUpdateClip({ clean_filler_words: !clip.clean_filler_words })}
            className={`px-2.5 py-1 rounded-lg border text-[11px] font-semibold flex items-center gap-1.5 transition ${clip.clean_filler_words
              ? 'bg-[var(--accent-subtle)] border-[var(--accent-primary)] text-[var(--accent-primary)]'
              : 'bg-[var(--bg-input)] border-[var(--border-subtle)] text-[var(--text-secondary)] hover:text-[var(--text-primary)]'
              }`}
            title="Cut filler utterances (um, uh, erm, ah) from video & captions"
          >
            <Scissors className="w-3 h-3" />
            <span>Remove Fillers</span>
          </button>

          <button
            type="button"
            disabled={words.length === 0}
            onClick={() => {
              const FILLER_SET = new Set(['um', 'uh', 'uhh', 'umm', 'erm', 'ah', 'ahh', 'eh']);
              const nextWords = words.map((w) => {
                const token = (w.word || '').toLowerCase().replace(/^[^a-z0-9]+|[^a-z0-9]+$/g, '');
                if (FILLER_SET.has(token)) {
                  return { ...w, deleted: true, cut_from_video: true };
                }
                return w;
              });
              onUpdateClip({
                clean_filler_words: true,
                transcript_words: nextWords,
              });
            }}
            className="px-2.5 py-1 rounded-lg border text-[11px] font-semibold flex items-center gap-1.5 transition bg-[var(--bg-input)] border-[var(--border-subtle)] text-[var(--text-secondary)] hover:border-[var(--accent-primary)] hover:text-[var(--text-primary)] disabled:opacity-50"
            title="Immediately strip all filler tokens (um, uh, erm, ah) from transcript & cuts"
          >
            <Zap className="w-3 h-3 text-[var(--accent-primary)]" />
            <span>Strip Filler Words</span>
          </button>

          <button
            type="button"
            onClick={() => {
              const next = !(clip.voice_enhancer ?? false);
              onUpdateClip({
                voice_enhancer: next,
                ...(next
                  ? {
                      normalize_audio: true,
                      loudness_target: '-14 LUFS',
                    }
                  : {}),
              });
            }}
            className={`px-2.5 py-1 rounded-lg border text-[11px] font-semibold flex items-center gap-1.5 transition ${clip.voice_enhancer
              ? 'bg-[var(--accent-subtle)] border-[var(--accent-primary)] text-[var(--accent-primary)]'
              : 'bg-[var(--bg-input)] border-[var(--border-subtle)] text-[var(--text-secondary)] hover:text-[var(--text-primary)]'
              }`}
            title="Studio Voice Enhancer: 80Hz High-Pass Filter + Presence EQ + -14 LUFS Loudness Normalization"
          >
            <Volume2 className="w-3 h-3" />
            <span>{clip.voice_enhancer ? '✓ Voice Enhancer (-14 LUFS)' : 'Studio Voice Enhancer'}</span>
          </button>

          <button
            type="button"
            onClick={() => onUpdateClip({ censor_profanity: !clip.censor_profanity })}
            className={`px-2.5 py-1 rounded-lg border text-[11px] font-semibold flex items-center gap-1.5 transition ${clip.censor_profanity
              ? 'bg-amber-500/15 border-amber-500/40 text-amber-400'
              : 'bg-[var(--bg-input)] border-[var(--border-subtle)] text-[var(--text-secondary)] hover:text-[var(--text-primary)]'
              }`}
            title="Mask profanity in captions (e.g. f***) and replace audio with -14dB 1000Hz bleep"
          >
            <ShieldAlert className="w-3 h-3" />
            <span>Auto-Censor Profanity</span>
          </button>

          <button
            type="button"
            disabled={isDiarizing || words.length === 0}
            onClick={async () => {
              if (!clip) return;
              try {
                setIsDiarizing(true);
                setDiarizeMsg(null);
                const res = await api.diarizeClip(projectId ?? clip.project_id, clip.id);
                onUpdateClip({ speakers: res.speakers, transcript_words: res.words });
                setDiarizeMsg(`Detected ${res.speaker_count} speaker${res.speaker_count === 1 ? '' : 's'}`);
              } catch (err: any) {
                setDiarizeMsg(err?.message || 'Diarization failed');
              } finally {
                setIsDiarizing(false);
              }
            }}
            className={`px-2.5 py-1 rounded-lg border text-[11px] font-semibold flex items-center gap-1.5 transition disabled:opacity-50 ${Object.keys(clip.speakers || {}).length > 0
              ? 'bg-[var(--accent-subtle)] border-[var(--accent-primary)] text-[var(--accent-primary)]'
              : 'bg-[var(--bg-input)] border-[var(--border-subtle)] text-[var(--text-secondary)] hover:text-[var(--text-primary)]'
              }`}
            title="Detect local speakers using acoustic timbre + active face tracking"
          >
            <Users className="w-3 h-3" />
            <span>{isDiarizing ? 'Detecting...' : 'Detect Speakers'}</span>
          </button>

          {Object.keys(clip.speakers || {}).length > 0 && (
            <button
              type="button"
              onClick={() => onUpdateClip({ speaker_lower_thirds: !(clip.speaker_lower_thirds ?? false) })}
              className={`px-2.5 py-1 rounded-lg border text-[11px] font-semibold flex items-center gap-1 transition ${clip.speaker_lower_thirds
                ? 'bg-[var(--accent-subtle)] border-[var(--accent-primary)] text-[var(--accent-primary)]'
                : 'bg-[var(--bg-input)] border-[var(--border-subtle)] text-[var(--text-secondary)] hover:text-[var(--text-primary)]'
                }`}
              title="Burn automatic 2.5s nameplate badge on speaker turns"
            >
              <span>{clip.speaker_lower_thirds ? '✓ ' : ''}Speaker Nameplates</span>
            </button>
          )}
        </div>

        {(diarizeMsg || Object.keys(clip.speakers || {}).length > 0) && (
          <div className="flex flex-wrap items-center gap-1.5 px-2 py-1.5 rounded-lg bg-[var(--bg-surface)] border border-[var(--border-subtle)] text-[11px]">
            {Object.values(clip.speakers || {}).map((spk) => (
              <div
                key={spk.id}
                className="flex items-center gap-1.5 px-2 py-0.5 rounded-full bg-[var(--bg-card)] border border-[var(--border-subtle)]"
              >
                <input
                  type="color"
                  value={spk.color || '#C6F32F'}
                  onChange={(e) => {
                    const nextSpk = { ...(clip.speakers || {}), [spk.id]: { ...spk, color: e.target.value } };
                    onUpdateClip({ speakers: nextSpk });
                  }}
                  className="w-3.5 h-3.5 rounded-full cursor-pointer border-0 bg-transparent p-0"
                  title={`Highlight color for ${spk.name}`}
                />
                {editingSpeakerId === spk.id ? (
                  <input
                    type="text"
                    autoFocus
                    value={spk.name}
                    onChange={(e) => {
                      const nextSpk = { ...(clip.speakers || {}), [spk.id]: { ...spk, name: e.target.value } };
                      onUpdateClip({ speakers: nextSpk });
                    }}
                    onBlur={() => setEditingSpeakerId(null)}
                    onKeyDown={(e) => {
                      if (e.key === 'Enter' || e.key === 'Escape') setEditingSpeakerId(null);
                    }}
                    className="w-24 bg-[var(--bg-input)] border border-[var(--accent-primary)] rounded px-1 py-0 text-[10px] font-semibold text-[var(--text-primary)] outline-none"
                  />
                ) : (
                  <button
                    type="button"
                    onClick={() => setEditingSpeakerId(spk.id)}
                    className="font-semibold text-[var(--text-primary)] hover:text-[var(--accent-primary)]"
                    title="Click to rename speaker"
                  >
                    {spk.name}
                  </button>
                )}
              </div>
            ))}
            {diarizeMsg && (
              <span className="ml-auto text-[10px] font-mono text-[var(--accent-primary)]">{diarizeMsg}</span>
            )}
          </div>
        )}

        {/* Bilingual Stacked Captions Control Bar */}
        <div className="flex flex-wrap items-center gap-2 px-2 py-1.5 rounded-lg bg-[var(--bg-surface)] border border-[var(--border-subtle)] text-[11px]">
          <span className="font-semibold text-[var(--text-secondary)] flex items-center gap-1">
            <Globe className="w-3 h-3 text-[var(--accent-primary)]" />
            Bilingual:
          </span>
          <select
            value={clip.secondary_language || bilingualLang}
            onChange={(e) => setBilingualLang(e.target.value)}
            aria-label="Secondary caption language"
            className="bg-[var(--bg-input)] border border-[var(--border-subtle)] rounded px-2 py-0.5 text-[11px] font-semibold text-[var(--text-primary)] outline-none"
          >
            {['Spanish', 'Hindi', 'Telugu', 'French', 'German', 'Portuguese', 'Japanese', 'Korean', 'Arabic', 'English'].map((lang) => (
              <option key={lang} value={lang}>{lang}</option>
            ))}
          </select>
          <button
            type="button"
            disabled={isTranslating || words.length === 0}
            onClick={async () => {
              if (!clip) return;
              const target = clip.secondary_language && bilingualLang === 'Spanish' ? clip.secondary_language : bilingualLang;
              try {
                setIsTranslating(true);
                setTranslateMsg(null);
                const res = await api.translateClip(projectId ?? clip.project_id, clip.id, target);
                onUpdateClip({
                  transcript_words: res.clip.transcript_words,
                  bilingual_enabled: true,
                  secondary_language: res.target_language,
                });
                setTranslateMsg(`Stacked ${res.target_language} (${res.source})`);
              } catch (err: any) {
                setTranslateMsg(err?.message || 'Translation failed');
              } finally {
                setIsTranslating(false);
              }
            }}
            className="px-2 py-0.5 rounded bg-[var(--bg-card)] border border-[var(--border-subtle)] hover:border-[var(--accent-primary)] font-semibold text-[10px] text-[var(--text-primary)] transition disabled:opacity-50"
          >
            {isTranslating ? 'Translating...' : 'Translate & Stack'}
          </button>
          <button
            type="button"
            onClick={() => onUpdateClip({ bilingual_enabled: !(clip.bilingual_enabled ?? false) })}
            className={`px-2 py-0.5 rounded border text-[10px] font-bold transition ${clip.bilingual_enabled
              ? 'bg-[var(--accent-subtle)] border-[var(--accent-primary)] text-[var(--accent-primary)]'
              : 'bg-[var(--bg-input)] border-[var(--border-subtle)] text-[var(--text-secondary)]'
              }`}
          >
            {clip.bilingual_enabled ? '✓ Stacked ON' : 'Stacked OFF'}
          </button>
          {translateMsg && (
            <span className="ml-auto text-[10px] font-mono text-[var(--accent-primary)]">{translateMsg}</span>
          )}
        </div>

        {/* Animated Mini-Chapter Pills (Glassmorphic Corner Badges) */}
        <div className="mb-3 p-2.5 rounded-xl bg-[var(--bg-input)] border border-[var(--border-subtle)] space-y-2">
          <div className="flex flex-wrap items-center gap-2 text-[11px]">
            <span className="font-semibold text-[var(--text-primary)] flex items-center gap-1">
              <Sparkles className="w-3 h-3 text-[var(--accent-primary)]" />
              Mini-Chapter Pills
            </span>
            <button
              type="button"
              disabled={isGeneratingChapters || !clip}
              onClick={async () => {
                if (!clip) return;
                try {
                  setIsGeneratingChapters(true);
                  setChapterMsg(null);
                  const res = await api.generateChapters(projectId ?? clip.project_id, clip.id, 4, true);
                  onUpdateClip({
                    chapters: res.chapters,
                    chapters_enabled: true,
                  });
                  setChapterMsg(`Generated ${res.chapters.length} chapters`);
                } catch (err: any) {
                  setChapterMsg(err?.message || 'Failed');
                } finally {
                  setIsGeneratingChapters(false);
                }
              }}
              className="px-2 py-0.5 rounded bg-[var(--bg-card)] border border-[var(--border-subtle)] hover:border-[var(--accent-primary)] font-semibold text-[10px] text-[var(--text-primary)] transition disabled:opacity-50"
            >
              {isGeneratingChapters ? 'Detecting...' : 'Generate Chapters'}
            </button>
            <button
              type="button"
              onClick={() => onUpdateClip({ chapters_enabled: !(clip.chapters_enabled ?? false) })}
              className={`px-2 py-0.5 rounded border text-[10px] font-bold transition ${clip.chapters_enabled
                ? 'bg-[var(--accent-subtle)] border-[var(--accent-primary)] text-[var(--accent-primary)]'
                : 'bg-[var(--bg-card)] border-[var(--border-subtle)] text-[var(--text-secondary)]'
                }`}
            >
              {clip.chapters_enabled ? '✓ Pills ON' : 'Pills OFF'}
            </button>
            {chapterMsg && (
              <span className="ml-auto text-[10px] font-mono text-[var(--accent-primary)]">{chapterMsg}</span>
            )}
          </div>

          {(clip.chapters || []).length > 0 && (
            <div className="space-y-1.5 max-h-[120px] overflow-y-auto pr-1">
              {(clip.chapters || []).map((ch, cIdx) => (
                <div
                  key={ch.id || `ch_${cIdx}`}
                  className="flex items-center gap-1.5 text-[10px] bg-[var(--bg-card)] border border-[var(--border-subtle)] rounded-lg px-2 py-1"
                >
                  <button
                    type="button"
                    onClick={() => onSeekTimestamp?.(Number(ch.start || 0))}
                    className="font-mono text-[var(--accent-primary)] hover:underline shrink-0"
                    title="Seek preview to chapter start"
                  >
                    {Number(ch.start || 0).toFixed(1)}s–{Number(ch.end || 0).toFixed(1)}s
                  </button>
                  <input
                    type="text"
                    value={ch.title || ''}
                    onChange={(e) => {
                      const next = (clip.chapters || []).map((item, i) =>
                        i === cIdx ? { ...item, title: e.target.value } : item
                      );
                      onUpdateClip({ chapters: next });
                    }}
                    className="flex-1 min-w-0 bg-[var(--bg-input)] border border-[var(--border-subtle)] rounded px-1.5 py-0.5 text-[var(--text-primary)] outline-none focus:border-[var(--accent-primary)]"
                    placeholder={`Part ${cIdx + 1}`}
                  />
                  <button
                    type="button"
                    onClick={() => {
                      const next = (clip.chapters || []).filter((_, i) => i !== cIdx);
                      onUpdateClip({ chapters: next });
                    }}
                    className="p-0.5 text-[var(--text-muted)] hover:text-red-400"
                    title="Remove chapter"
                  >
                    <X className="w-3 h-3" />
                  </button>
                </div>
              ))}
            </div>
          )}
        </div>

        {/* Flowing Inline Paragraph Surface */}
        {words.length > 0 ? (
          <div className="max-h-[180px] overflow-y-auto bg-[var(--bg-input)] border border-[var(--border-subtle)] rounded-xl p-3 flex flex-wrap gap-x-1.5 gap-y-1.5 leading-relaxed select-text">
            {words.map((w, idx) => {
              const isPlaybackActive =
                currentPlaybackTime !== undefined &&
                !w.deleted &&
                !w.cut_from_video &&
                currentPlaybackTime >= w.start &&
                currentPlaybackTime <= w.end;
              const isSelected = selectedWordIdx === idx;
              const isEditingInline = inlineEditIdx === idx;

              if (isEditingInline) {
                return (
                  <input
                    key={idx}
                    type="text"
                    autoFocus
                    value={w.word}
                    onChange={(e) => handleWordTextChange(idx, e.target.value)}
                    onBlur={() => setInlineEditIdx(null)}
                    onKeyDown={(e) => {
                      if (e.key === 'Enter' || e.key === 'Escape') {
                        setInlineEditIdx(null);
                      }
                    }}
                    className="px-1.5 py-0.5 rounded bg-[var(--bg-card)] border border-[var(--accent-primary)] text-[var(--text-primary)] text-xs font-semibold outline-none w-24"
                  />
                );
              }

              return (
                <button
                  key={idx}
                  type="button"
                  onClick={() => {
                    setSelectedWordIdx(idx);
                    onSeekTimestamp?.(w.start);
                  }}
                  onDoubleClick={() => {
                    setSelectedWordIdx(idx);
                    setInlineEditIdx(idx);
                  }}
                  title={
                    w.cut_from_video
                      ? `${formatTimestamp(w.start)} — Cut from video & audio`
                      : w.deleted
                        ? `${formatTimestamp(w.start)} — Hidden from captions only`
                        : `${formatTimestamp(w.start)} — Click to seek, double-click to edit`
                  }
                  style={
                    w.speaker_id && clip.speakers?.[w.speaker_id]?.color
                      ? { borderBottom: `2px solid ${clip.speakers[w.speaker_id].color}` }
                      : undefined
                  }
                  className={`px-1.5 py-0.5 rounded-md text-xs transition cursor-pointer ${w.cut_from_video
                    ? 'bg-red-500/15 border border-red-500/40 text-red-400 line-through hover:opacity-80'
                    : w.deleted
                      ? 'line-through opacity-40 text-[var(--text-secondary)] hover:opacity-70'
                      : isPlaybackActive
                        ? 'bg-[var(--accent-primary)] text-[var(--accent-foreground)] font-bold shadow-sm'
                        : isSelected
                          ? 'bg-[var(--accent-subtle)] text-[var(--accent-primary)] ring-1 ring-[var(--accent-primary)] font-semibold'
                          : 'text-[var(--text-primary)] hover:bg-[var(--bg-card)]'
                    }`}
                >
                  {w.word}
                </button>
              );
            })}
          </div>
        ) : (
          <div className="p-4 bg-[var(--bg-input)] border border-[var(--border-subtle)] rounded-xl text-center text-[var(--text-secondary)] space-y-2">
            <p>No spoken words detected in this clip yet.</p>
            <button
              type="button"
              onClick={handleRetranscribe}
              disabled={isRetranscribing}
              className="px-3 py-1.5 bg-[var(--accent-primary)] text-[var(--accent-foreground)] rounded-lg font-bold text-xs"
            >
              Run Voice Recognition
            </button>
          </div>
        )}

        {/* Compact Floating Word Toolbar (Shown when a word is clicked) */}
        {selectedWord && selectedWordIdx !== null && (
          <div className="flex flex-wrap items-center justify-between gap-2 px-3 py-2 rounded-lg bg-[var(--bg-surface)] border border-[var(--accent-primary)]/40 animate-fadeIn">
            <div className="flex items-center gap-2 flex-1 min-w-[160px]">
              <span className="text-[10px] font-mono text-[var(--accent-primary)] font-bold tabular-nums shrink-0">
                {formatTimestamp(selectedWord.start)}
              </span>
              <input
                type="text"
                value={selectedWord.word}
                onChange={(e) => handleWordTextChange(selectedWordIdx, e.target.value)}
                aria-label="Edit selected word"
                className="flex-1 min-w-0 max-w-[180px] bg-[var(--bg-input)] border border-[var(--border-subtle)] rounded-md px-2 py-1 text-xs font-semibold text-[var(--text-primary)] focus:border-[var(--accent-primary)] outline-none"
              />
            </div>

            <div className="flex items-center gap-1.5 flex-wrap">
              <button
                type="button"
                onClick={() => handleToggleDeleteWord(selectedWordIdx)}
                className={`px-2.5 py-1 rounded-md border text-[11px] font-semibold flex items-center gap-1 transition ${selectedWord.deleted
                  ? 'bg-emerald-500/15 border-emerald-500/30 text-emerald-400'
                  : 'bg-[var(--bg-input)] border-[var(--border-subtle)] text-[var(--text-secondary)] hover:text-[var(--text-primary)]'
                  }`}
              >
                {selectedWord.deleted ? (
                  <>
                    <Eye className="w-3 h-3" /> Show Caption
                  </>
                ) : (
                  <>
                    <EyeOff className="w-3 h-3" /> Hide Caption Only
                  </>
                )}
              </button>
              <button
                type="button"
                onClick={() => handleToggleCutWord(selectedWordIdx)}
                className={`px-2.5 py-1 rounded-md border text-[11px] font-semibold flex items-center gap-1 transition ${selectedWord.cut_from_video
                  ? 'bg-red-500/20 border-red-500/50 text-red-400'
                  : 'bg-[var(--bg-input)] border-[var(--border-subtle)] text-[var(--text-secondary)] hover:text-red-400 hover:border-red-500/40'
                  }`}
              >
                <Scissors className="w-3 h-3" />
                <span>{selectedWord.cut_from_video ? 'Restore Video Cut' : 'Cut From Video'}</span>
              </button>
              <button
                type="button"
                onClick={() => setSelectedWordIdx(null)}
                className="p-1 text-[var(--text-secondary)] hover:text-[var(--text-primary)]"
                title="Close word editor"
              >
                <X className="w-3.5 h-3.5" />
              </button>
            </div>
          </div>
        )}
      </div>

      {/* B. Clean 4-Tab Bar */}
      <div className="flex items-center gap-1 p-1 rounded-xl bg-[var(--bg-card)] border border-[var(--border-subtle)]">
        {[
          { id: 'styles', label: 'Styles', icon: Type },
          { id: 'typography', label: 'Typography', icon: AlignCenter },
          { id: 'colors', label: 'Colors & Effects', icon: Palette },
          { id: 'animation', label: 'Animation & Position', icon: Move }
        ].map((tab) => {
          const Icon = tab.icon;
          const active = subTab === tab.id;
          return (
            <button
              key={tab.id}
              type="button"
              onClick={() => setSubTab(tab.id as any)}
              className={`flex-1 flex items-center justify-center gap-1.5 py-1.5 px-2 rounded-lg text-xs font-semibold transition whitespace-nowrap ${active
                ? 'bg-[var(--accent-primary)] text-[var(--accent-foreground)] shadow-sm'
                : 'text-[var(--text-secondary)] hover:text-[var(--text-primary)]'
                }`}
            >
              <Icon className="w-3.5 h-3.5 shrink-0" />
              <span className="truncate">{tab.label}</span>
            </button>
          );
        })}
      </div>

      {/* SUB-TAB 1: STYLES */}
      {subTab === 'styles' && (
        <div className="bg-[var(--bg-card)] border border-[var(--border-subtle)] rounded-xl p-4 space-y-4 animate-fadeIn">
          {/* Top Bar: Captions ON/OFF + Timing Offset + Save Current Style */}
          <div className="flex flex-wrap items-center justify-between gap-3 pb-3 border-b border-[var(--border-subtle)]">
            <div className="flex items-center gap-1 bg-[var(--bg-input)] p-0.5 rounded-lg border border-[var(--border-subtle)]">
              <button
                type="button"
                onClick={() => onUpdateClip({ burn_captions: true })}
                className={`px-2.5 py-1 rounded-md text-[11px] font-bold transition ${clip.burn_captions
                  ? 'bg-[var(--accent-primary)] text-[var(--accent-foreground)]'
                  : 'text-[var(--text-secondary)]'
                  }`}
              >
                Captions ON
              </button>
              <button
                type="button"
                onClick={() => onUpdateClip({ burn_captions: false })}
                className={`px-2.5 py-1 rounded-md text-[11px] font-bold transition ${!clip.burn_captions
                  ? 'bg-[var(--accent-primary)] text-[var(--accent-foreground)]'
                  : 'text-[var(--text-secondary)]'
                  }`}
              >
                Captions OFF
              </button>
            </div>

            <div className="flex items-center gap-2.5 flex-1 min-w-[180px] max-w-xs tabular-nums">
              <span className="text-[11px] text-[var(--text-secondary)] shrink-0">Timing Offset</span>
              <input
                type="range"
                min={-1.0}
                max={1.0}
                step={0.05}
                value={clip.timing_offset || 0}
                onChange={(e) => onUpdateClip({ timing_offset: parseFloat(e.target.value) })}
                className="flex-1 h-1.5 bg-[var(--bg-input)] rounded appearance-none cursor-pointer"
              />
              <span className="font-mono text-[11px] font-bold text-[var(--accent-primary)] w-12 text-right">
                {(clip.timing_offset || 0) >= 0
                  ? `+${(clip.timing_offset || 0).toFixed(2)}s`
                  : `${(clip.timing_offset || 0).toFixed(2)}s`}
              </span>
            </div>

            {onSaveQuickStyle && (
              <div className="flex items-center gap-1.5">
                {showSaveStyle ? (
                  <div className="flex items-center gap-1">
                    <input
                      type="text"
                      value={styleNameInput}
                      onChange={(e) => setStyleNameInput(e.target.value)}
                      onKeyDown={(e) => e.key === 'Enter' && handleCommitStyleSave()}
                      placeholder="Style name..."
                      className="w-28 bg-[var(--bg-input)] border border-[var(--border-subtle)] rounded-md px-2 py-1 text-[11px] text-[var(--text-primary)] outline-none focus:border-[var(--accent-primary)]"
                      autoFocus
                    />
                    <button
                      type="button"
                      onClick={handleCommitStyleSave}
                      className="px-2 py-1 rounded-md bg-[var(--accent-primary)] text-[var(--accent-foreground)] text-[11px] font-bold"
                    >
                      Save
                    </button>
                    <button
                      type="button"
                      onClick={() => setShowSaveStyle(false)}
                      className="p-1 text-[var(--text-secondary)] hover:text-[var(--text-primary)]"
                    >
                      <X className="w-3 h-3" />
                    </button>
                  </div>
                ) : (
                  <button
                    type="button"
                    onClick={() => setShowSaveStyle(true)}
                    className="px-2.5 py-1 rounded-lg bg-[var(--bg-input)] border border-[var(--border-subtle)] hover:border-[var(--accent-primary)]/50 text-[11px] font-semibold text-[var(--text-primary)] flex items-center gap-1.5 transition"
                  >
                    {styleSavedFeedback ? (
                      <>
                        <Check className="w-3 h-3 text-emerald-400" />
                        <span>Style Saved</span>
                      </>
                    ) : (
                      <>
                        <Save className="w-3 h-3 text-[var(--accent-primary)]" />
                        <span>Save Current Style</span>
                      </>
                    )}
                  </button>
                )}
              </div>
            )}
          </div>

          {/* 12 Visual Style Cards */}
          <div className="grid grid-cols-3 xl:grid-cols-4 gap-2">
            {defaultStyles.map((s) => {
              const isSelected = clip.caption_style === s.name;
              return (
                <button
                  key={s.name}
                  type="button"
                  onClick={() => handleApplyPreset(s)}
                  className={`h-[76px] p-2.5 rounded-xl border text-center transition flex flex-col items-center justify-between ${isSelected
                    ? 'bg-[var(--accent-subtle)] border-[var(--accent-primary)] ring-1 ring-[var(--accent-primary)]/40'
                    : 'bg-[var(--bg-input)] border-[var(--border-subtle)] hover:border-[var(--accent-primary)]/40'
                    }`}
                >
                  <span className="text-[10px] font-medium text-[var(--text-secondary)] truncate w-full">
                    {s.name}
                  </span>
                  <div
                    className={`my-auto text-xs truncate max-w-full px-1.5 py-0.5 rounded ${s.box ? 'bg-zinc-900/90' : s.block ? 'bg-[var(--accent-primary)]' : ''
                      } ${s.bold ? 'font-black' : 'font-semibold'} ${s.italic ? 'italic' : ''}`}
                    style={{
                      fontFamily: s.font,
                      color: s.block ? '#000000' : s.active,
                      textDecoration: s.underline ? 'underline' : 'none',
                      textShadow: s.block ? 'none' : '0 1px 3px rgba(0,0,0,0.8)'
                    }}
                  >
                    {s.preview}
                  </div>
                </button>
              );
            })}
          </div>
        </div>
      )}

      {/* SUB-TAB 2: TYPOGRAPHY */}
      {subTab === 'typography' && (
        <div className="bg-[var(--bg-card)] border border-[var(--border-subtle)] rounded-xl p-4 grid grid-cols-1 lg:grid-cols-2 gap-4 animate-fadeIn">
          {/* Font Family + Visual Font Library + Upload */}
          <div className="space-y-3">
            <div>
              <label className="text-[11px] font-bold text-[var(--text-secondary)] uppercase tracking-wide block mb-1.5">
                Font Library & Custom Upload
              </label>
              <div className="flex gap-2">
                <select
                  value={clip.caption_font}
                  onChange={(e) => onUpdateClip({ caption_font: e.target.value })}
                  className="flex-1 bg-[var(--bg-input)] border border-[var(--border-subtle)] text-xs text-[var(--text-primary)] rounded-lg py-2 px-2.5 outline-none focus:border-[var(--accent-primary)]"
                >
                  {availableFonts.map((f) => (
                    <option key={f} value={f}>
                      {f}
                    </option>
                  ))}
                </select>
                <label className="px-3 py-2 bg-[var(--bg-input)] border border-[var(--border-subtle)] hover:border-[var(--accent-primary)]/50 rounded-lg cursor-pointer text-[11px] font-semibold text-[var(--text-primary)] flex items-center gap-1.5 shrink-0 transition">
                  <Upload className="w-3.5 h-3.5 text-[var(--accent-primary)]" />
                  <span>Upload .ttf/.otf</span>
                  <input
                    type="file"
                    accept=".ttf,.otf"
                    className="hidden"
                    onChange={(e) => {
                      if (e.target.files?.[0]) onUploadFont(e.target.files[0]);
                    }}
                  />
                </label>
              </div>
              <div className="grid grid-cols-3 gap-1.5 mt-2 max-h-24 overflow-y-auto pr-0.5">
                {availableFonts.slice(0, 9).map((f) => {
                  const sel = (clip.caption_font || 'Montserrat') === f;
                  return (
                    <button
                      key={f}
                      type="button"
                      onClick={() => onUpdateClip({ caption_font: f })}
                      style={{ fontFamily: f }}
                      className={`px-2 py-1 rounded-md border text-[11px] truncate transition ${sel
                        ? 'bg-[var(--accent-subtle)] border-[var(--accent-primary)] text-[var(--text-primary)] font-bold'
                        : 'bg-[var(--bg-input)] border-[var(--border-subtle)] text-[var(--text-secondary)] hover:text-[var(--text-primary)]'
                        }`}
                      title={`Select ${f}`}
                    >
                      {f}
                    </button>
                  );
                })}
              </div>
            </div>

            <div className="tabular-nums">
              <div className="flex justify-between text-[11px] text-[var(--text-secondary)] mb-1">
                <span>Font Size Scale (Safe Auto-Fit)</span>
                <span className="font-mono font-bold text-[var(--accent-primary)]">
                  {clip.font_size_percent || 100}%
                </span>
              </div>
              <input
                type="range"
                min={65}
                max={155}
                value={Math.max(65, Math.min(155, clip.font_size_percent || 100))}
                onChange={(e) => onUpdateClip({ font_size_percent: parseInt(e.target.value, 10) })}
                className="w-full h-1.5 bg-[var(--bg-input)] rounded appearance-none cursor-pointer"
              />
            </div>

            <div>
              <span className="text-[11px] font-bold text-[var(--text-secondary)] uppercase tracking-wide block mb-1.5">
                Pacing Density
              </span>
              <div className="grid grid-cols-3 gap-1 bg-[var(--bg-input)] p-1 rounded-lg border border-[var(--border-subtle)]">
                {['1-2 Words Rapid', '3-4 Words Viral', 'Full Phrase'].map((dens) => {
                  const active = (clip.pacing_density || '3-4 Words Viral') === dens;
                  return (
                    <button
                      key={dens}
                      type="button"
                      onClick={() => onUpdateClip({ pacing_density: dens })}
                      className={`py-1.5 px-1 rounded-md text-[10px] font-bold transition truncate ${active
                        ? 'bg-[var(--accent-primary)] text-[var(--accent-foreground)]'
                        : 'text-[var(--text-secondary)] hover:text-[var(--text-primary)]'
                        }`}
                    >
                      {dens}
                    </button>
                  );
                })}
              </div>
            </div>
          </div>

          {/* Formatting, Text Case & Alignment */}
          <div className="space-y-3">
            <div>
              <span className="text-[11px] font-bold text-[var(--text-secondary)] uppercase tracking-wide block mb-1.5">
                Text Case & Formatting
              </span>
              <div className="grid grid-cols-3 gap-1 bg-[var(--bg-input)] p-1 rounded-lg border border-[var(--border-subtle)] mb-2">
                {['ALL CAPS', 'Title Case', 'As Spoken'].map((tc) => {
                  const currTc = clip.text_case || (clip.all_caps ? 'ALL CAPS' : 'As Spoken');
                  const active = currTc === tc;
                  return (
                    <button
                      key={tc}
                      type="button"
                      onClick={() => onUpdateClip({ text_case: tc, all_caps: tc === 'ALL CAPS' })}
                      className={`py-1.5 rounded-md text-[11px] font-bold transition ${active
                        ? 'bg-[var(--accent-primary)] text-[var(--accent-foreground)]'
                        : 'text-[var(--text-secondary)] hover:text-[var(--text-primary)]'
                        }`}
                    >
                      {tc}
                    </button>
                  );
                })}
              </div>
              <div className="grid grid-cols-2 gap-1.5 bg-[var(--bg-input)] p-1 rounded-lg border border-[var(--border-subtle)]">
                <button
                  type="button"
                  onClick={() => onUpdateClip({ bold: !clip.bold })}
                  className={`py-1.5 rounded-md font-bold text-xs transition ${clip.bold
                    ? 'bg-[var(--accent-primary)] text-[var(--accent-foreground)]'
                    : 'text-[var(--text-secondary)] hover:text-[var(--text-primary)]'
                    }`}
                >
                  Bold
                </button>
                <button
                  type="button"
                  onClick={() => onUpdateClip({ italic: !clip.italic })}
                  className={`py-1.5 rounded-md italic text-xs font-medium transition ${clip.italic
                    ? 'bg-[var(--accent-primary)] text-[var(--accent-foreground)]'
                    : 'text-[var(--text-secondary)] hover:text-[var(--text-primary)]'
                    }`}
                >
                  Italic
                </button>
              </div>
            </div>

            <div>
              <span className="text-[11px] font-bold text-[var(--text-secondary)] uppercase tracking-wide block mb-1.5">
                Alignment
              </span>
              <div className="grid grid-cols-3 gap-1.5 bg-[var(--bg-input)] p-1 rounded-lg border border-[var(--border-subtle)]">
                {[
                  { id: 'Left', icon: AlignLeft },
                  { id: 'Centre', icon: AlignCenter },
                  { id: 'Right', icon: AlignRight }
                ].map((a) => {
                  const Icon = a.icon;
                  const active = (clip.text_align || 'Centre') === a.id;
                  return (
                    <button
                      key={a.id}
                      type="button"
                      onClick={() => onUpdateClip({ text_align: a.id as any })}
                      className={`py-1.5 rounded-md flex items-center justify-center gap-1.5 text-xs font-medium transition ${active
                        ? 'bg-[var(--accent-primary)] text-[var(--accent-foreground)] font-bold'
                        : 'text-[var(--text-secondary)] hover:text-[var(--text-primary)]'
                        }`}
                    >
                      <Icon className="w-3.5 h-3.5" />
                      <span>{a.id}</span>
                    </button>
                  );
                })}
              </div>
            </div>
          </div>
        </div>
      )}

      {/* SUB-TAB 3: COLORS & EFFECTS */}
      {subTab === 'colors' && (
        <div className="bg-[var(--bg-card)] border border-[var(--border-subtle)] rounded-xl p-4 grid grid-cols-1 lg:grid-cols-2 gap-4 animate-fadeIn">
          {/* Left: Pickers + Highlight Style + Toggles */}
          <div className="space-y-3">
            <div className="grid grid-cols-3 gap-2">
              <div>
                <label className="text-[11px] text-[var(--text-secondary)] block mb-1">Base Color</label>
                <div className="flex items-center gap-1.5 bg-[var(--bg-input)] p-1.5 rounded-lg border border-[var(--border-subtle)]">
                  <input
                    type="color"
                    value={clip.base_color || '#FFFFFF'}
                    onChange={(e) => onUpdateClip({ base_color: e.target.value })}
                    className="w-5 h-5 rounded cursor-pointer border-0 bg-transparent shrink-0"
                  />
                  <span className="font-mono text-[10px] font-bold uppercase text-[var(--text-primary)] truncate">
                    {clip.base_color || '#FFFFFF'}
                  </span>
                </div>
              </div>

              <div>
                <label className="text-[11px] text-[var(--text-secondary)] block mb-1">Highlight Color</label>
                <div className="flex items-center gap-1.5 bg-[var(--bg-input)] p-1.5 rounded-lg border border-[var(--border-subtle)]">
                  <input
                    type="color"
                    value={clip.active_color || '#C6F32F'}
                    onChange={(e) => onUpdateClip({ active_color: e.target.value })}
                    className="w-5 h-5 rounded cursor-pointer border-0 bg-transparent shrink-0"
                  />
                  <span className="font-mono text-[10px] font-bold uppercase text-[var(--text-primary)] truncate">
                    {clip.active_color || '#C6F32F'}
                  </span>
                </div>
              </div>

              <div>
                <label className="text-[11px] text-[var(--text-secondary)] block mb-1">Pill Color</label>
                <div className="flex items-center gap-1.5 bg-[var(--bg-input)] p-1.5 rounded-lg border border-[var(--border-subtle)]">
                  <input
                    type="color"
                    value={clip.pill_color || clip.active_color || '#F59E0B'}
                    onChange={(e) => onUpdateClip({ pill_color: e.target.value })}
                    className="w-5 h-5 rounded cursor-pointer border-0 bg-transparent shrink-0"
                  />
                  <span className="font-mono text-[10px] font-bold uppercase text-[var(--text-primary)] truncate">
                    {clip.pill_color || clip.active_color || '#F59E0B'}
                  </span>
                </div>
              </div>
            </div>

            <div>
              <span className="text-[11px] text-[var(--text-secondary)] block mb-1">Active-Word Highlight Style</span>
              <div className="grid grid-cols-2 sm:grid-cols-3 gap-1 bg-[var(--bg-input)] p-1 rounded-lg border border-[var(--border-subtle)]">
                {[
                  { id: 'Color Pop', aliases: ['Colour only', 'Color Pop'], label: 'Color Pop' },
                  { id: 'Rounded Pill Box', aliases: ['Filled box', 'Rounded Pill Box'], label: 'Rounded Pill Box' },
                  { id: 'Neon Outer Glow', aliases: ['Neon Outer Glow'], label: 'Neon Outer Glow' },
                  { id: 'Marker Underline', aliases: ['Underline', 'Marker Underline'], label: 'Marker Underline' },
                  { id: '1.18x Kinetic Pop', aliases: ['1.18x Kinetic Pop'], label: '1.18x Kinetic Pop' },
                ].map((shape) => {
                  const active = shape.aliases.includes(clip.highlight_shape);
                  return (
                    <button
                      key={shape.id}
                      type="button"
                      onClick={() => onUpdateClip({ highlight_shape: shape.id })}
                      className={`py-1 px-1.5 rounded-md text-[10px] font-bold transition truncate ${active
                        ? 'bg-[var(--accent-primary)] text-[var(--accent-foreground)]'
                        : 'text-[var(--text-secondary)] hover:text-[var(--text-primary)]'
                        }`}
                    >
                      {shape.label}
                    </button>
                  );
                })}
              </div>
            </div>

            <div className="grid grid-cols-1 sm:grid-cols-3 gap-2 pt-1">
              <button
                type="button"
                onClick={() => onUpdateClip({ emoji_enabled: !clip.emoji_enabled })}
                className={`px-3 py-2 rounded-lg border flex items-center justify-between text-[11px] font-semibold transition ${clip.emoji_enabled
                  ? 'bg-[var(--accent-subtle)] border-[var(--accent-primary)] text-[var(--text-primary)]'
                  : 'bg-[var(--bg-input)] border-[var(--border-subtle)] text-[var(--text-secondary)]'
                  }`}
              >
                <span>Auto-Emoji</span>
                <span className="text-[10px] font-bold">{clip.emoji_enabled ? 'ON' : 'OFF'}</span>
              </button>

              <button
                type="button"
                onClick={() =>
                  onUpdateClip({ highlight_power_words: !clip.highlight_power_words })
                }
                className={`px-2.5 py-2 rounded-lg border flex items-center justify-between gap-1.5 text-[11px] font-semibold transition ${clip.highlight_power_words
                  ? 'bg-[var(--accent-subtle)] border-[var(--accent-primary)] text-[var(--text-primary)]'
                  : 'bg-[var(--bg-input)] border-[var(--border-subtle)] text-[var(--text-secondary)]'
                  }`}
                title="Auto-Highlight Power Words (1.08x + highlight accent)"
              >
                <span className="truncate">Auto-Highlight Power Words</span>
                <span className="text-[10px] font-bold shrink-0">
                  {clip.highlight_power_words ? 'ON' : 'OFF'}
                </span>
              </button>

              <button
                type="button"
                onClick={() =>
                  onUpdateClip({
                    how_phrase_arrives:
                      clip.how_phrase_arrives === 'Word by word' ? 'All at once' : 'Word by word'
                  })
                }
                className={`px-3 py-2 rounded-lg border flex items-center justify-between text-[11px] font-semibold transition ${clip.how_phrase_arrives === 'Word by word'
                  ? 'bg-[var(--accent-subtle)] border-[var(--accent-primary)] text-[var(--text-primary)]'
                  : 'bg-[var(--bg-input)] border-[var(--border-subtle)] text-[var(--text-secondary)]'
                  }`}
              >
                <span>Word-by-Word</span>
                <span className="text-[10px] font-bold">
                  {clip.how_phrase_arrives === 'Word by word' ? 'ON' : 'OFF'}
                </span>
              </button>
            </div>
          </div>

          {/* Right: Sliders */}
          <div className="space-y-2.5 tabular-nums">
            <div>
              <div className="flex justify-between text-[11px] text-[var(--text-secondary)] mb-1">
                <span>Hold-back Dimming (inactive words)</span>
                <span className="font-mono text-[var(--accent-primary)] font-bold">
                  {Math.round((clip.hold_back_alpha ?? 1.0) * 100)}%
                </span>
              </div>
              <input
                type="range"
                min={0.1}
                max={1.0}
                step={0.05}
                value={clip.hold_back_alpha ?? 1.0}
                onChange={(e) => onUpdateClip({ hold_back_alpha: parseFloat(e.target.value) })}
                className="w-full h-1.5 bg-[var(--bg-input)] rounded appearance-none cursor-pointer"
              />
            </div>

            <div className="grid grid-cols-2 gap-3">
              <div>
                <div className="flex justify-between text-[11px] text-[var(--text-secondary)] mb-1">
                  <span>Outline</span>
                  <span className="font-mono text-[var(--accent-primary)] font-bold">
                    {clip.outline_width ?? 4}px
                  </span>
                </div>
                <input
                  type="range"
                  min={0}
                  max={10}
                  step={0.5}
                  value={Math.min(10, clip.outline_width ?? 4)}
                  onChange={(e) => onUpdateClip({ outline_width: parseFloat(e.target.value) })}
                  className="w-full h-1.5 bg-[var(--bg-input)] rounded appearance-none cursor-pointer"
                />
              </div>

              <div>
                <div className="flex justify-between text-[11px] text-[var(--text-secondary)] mb-1">
                  <span>Drop Shadow</span>
                  <span className="font-mono text-[var(--accent-primary)] font-bold">
                    {Math.round((clip.shadow_depth ?? 3) * 10)}%
                  </span>
                </div>
                <input
                  type="range"
                  min={0}
                  max={10}
                  step={0.5}
                  value={Math.min(10, clip.shadow_depth ?? 3)}
                  onChange={(e) => onUpdateClip({ shadow_depth: parseFloat(e.target.value) })}
                  className="w-full h-1.5 bg-[var(--bg-input)] rounded appearance-none cursor-pointer"
                />
              </div>

              <div>
                <div className="flex justify-between text-[11px] text-[var(--text-secondary)] mb-1">
                  <span>Glow</span>
                  <span className="font-mono text-[var(--accent-primary)] font-bold">
                    {clip.glow_intensity ?? 0}
                  </span>
                </div>
                <input
                  type="range"
                  min={0}
                  max={10}
                  step={0.5}
                  value={clip.glow_intensity ?? 0}
                  onChange={(e) => onUpdateClip({ glow_intensity: parseFloat(e.target.value) })}
                  className="w-full h-1.5 bg-[var(--bg-input)] rounded appearance-none cursor-pointer"
                />
              </div>

              <div>
                <div className="flex justify-between text-[11px] text-[var(--text-secondary)] mb-1">
                  <span>Letter Spacing</span>
                  <span className="font-mono text-[var(--accent-primary)] font-bold">
                    {clip.letter_spacing ?? 0}px
                  </span>
                </div>
                <input
                  type="range"
                  min={-2}
                  max={10}
                  step={0.5}
                  value={clip.letter_spacing ?? 0}
                  onChange={(e) => onUpdateClip({ letter_spacing: parseFloat(e.target.value) })}
                  className="w-full h-1.5 bg-[var(--bg-input)] rounded appearance-none cursor-pointer"
                />
              </div>
            </div>
          </div>
        </div>
      )}

      {/* SUB-TAB 4: ANIMATION & POSITION */}
      {subTab === 'animation' && (
        <div className="bg-[var(--bg-card)] border border-[var(--border-subtle)] rounded-xl p-4 grid grid-cols-1 lg:grid-cols-2 gap-4 animate-fadeIn">
          {/* Left: 3x3 Placement Grid + Free-Form X/Y, Width & Tilt */}
          <div className="space-y-3">
            <div className="flex items-center justify-between gap-2">
              <span className="text-[11px] font-bold uppercase tracking-wide text-[var(--text-secondary)] flex items-center gap-1.5">
                <Move className="w-3.5 h-3.5 text-[var(--accent-primary)]" />
                Free-Form Canvas Position
              </span>
              <button
                type="button"
                onClick={() =>
                  onUpdateClip({
                    position_grid: 'bottom_center',
                    position_x: 50,
                    position_y: 78,
                    caption_x: 0.5,
                    caption_y: 0.78,
                    caption_custom_position: false,
                    face_safe_captions: true,
                    caption_rotation: 0,
                    caption_max_width: 0.82,
                  })
                }
                className="text-[10px] text-[var(--accent-primary)] hover:underline flex items-center gap-1 font-semibold"
              >
                <RotateCcw className="w-3 h-3" /> Reset Position
              </button>
            </div>

            {/* Quick Canvas Presets */}
            <div className="grid grid-cols-4 gap-1">
              {[
                { label: 'Center Bottom', grid: 'bottom_center', x: 0.5, y: 0.78, rot: 0, mw: 0.82, custom: true, faceSafe: false },
                { label: 'Dead Center', grid: 'center', x: 0.5, y: 0.5, rot: 0, mw: 0.82, custom: true, faceSafe: false },
                { label: 'Top Safe', grid: 'top_center', x: 0.5, y: 0.18, rot: 0, mw: 0.82, custom: true, faceSafe: false },
                { label: 'Reset', grid: 'bottom_center', x: 0.5, y: 0.78, rot: 0, mw: 0.82, custom: false, faceSafe: true },
              ].map((qp) => (
                <button
                  key={qp.label}
                  type="button"
                  onClick={() =>
                    onUpdateClip({
                      position_grid: qp.grid,
                      position_x: Math.round(qp.x * 100),
                      position_y: Math.round(qp.y * 100),
                      caption_x: qp.x,
                      caption_y: qp.y,
                      caption_custom_position: qp.custom,
                      face_safe_captions: qp.faceSafe,
                      caption_rotation: qp.rot,
                      caption_max_width: qp.mw,
                    })
                  }
                  className="py-1 px-1.5 rounded-md border border-[var(--border-subtle)] bg-[var(--bg-input)] hover:border-[var(--accent-primary)]/60 text-[10px] font-semibold text-[var(--text-secondary)] hover:text-[var(--text-primary)] transition whitespace-nowrap"
                >
                  {qp.label}
                </button>
              ))}
            </div>

            <div className="flex items-start gap-4">
              <div className="w-24 h-32 bg-[var(--bg-input)] border border-[var(--border-subtle)] rounded-xl p-1.5 grid grid-cols-3 grid-rows-3 gap-1 shrink-0">
                {gridPositions.map((pos) => (
                  <button
                    key={pos.id}
                    type="button"
                    onClick={() =>
                      onUpdateClip({
                        position_grid: pos.id,
                        position_x: pos.x,
                        position_y: pos.y,
                        caption_x: Number((pos.x / 100).toFixed(2)),
                        caption_y: Number((pos.y / 100).toFixed(2)),
                        caption_custom_position: true,
                        face_safe_captions: false,
                      })
                    }
                    className={`rounded-md transition flex items-center justify-center ${clip.position_grid === pos.id
                      ? 'bg-[var(--accent-primary)] text-[var(--accent-foreground)] shadow-sm'
                      : 'bg-[var(--bg-card)] hover:bg-[var(--accent-subtle)] text-[var(--text-secondary)]'
                      }`}
                    title={pos.label}
                  >
                    <div className="w-1.5 h-1.5 rounded-full bg-current" />
                  </button>
                ))}
              </div>

              <div className="flex-1 space-y-2.5 tabular-nums">
                <div>
                  <div className="flex justify-between text-[11px] text-[var(--text-secondary)] mb-1">
                    <span>Horizontal X</span>
                    <span className="font-mono font-bold text-[var(--accent-primary)]">
                      {Math.round((clip.caption_x ?? (clip.position_x ?? 50) / 100) * 100)}%
                    </span>
                  </div>
                  <input
                    type="range"
                    min={5}
                    max={95}
                    value={Math.max(5, Math.min(95, Math.round((clip.caption_x ?? (clip.position_x ?? 50) / 100) * 100)))}
                    onChange={(e) => {
                      const pct = parseFloat(e.target.value);
                      onUpdateClip({
                        position_x: pct,
                        caption_x: Number((pct / 100).toFixed(3)),
                        caption_custom_position: true,
                        face_safe_captions: false,
                      });
                    }}
                    className="w-full h-1.5 bg-[var(--bg-input)] rounded appearance-none cursor-pointer"
                  />
                </div>

                <div>
                  <div className="flex justify-between text-[11px] text-[var(--text-secondary)] mb-1">
                    <span>Vertical Y</span>
                    <span className="font-mono font-bold text-[var(--accent-primary)]">
                      {Math.round((clip.caption_y ?? (clip.position_y ?? 78) / 100) * 100)}%
                    </span>
                  </div>
                  <input
                    type="range"
                    min={8}
                    max={92}
                    value={Math.max(8, Math.min(92, Math.round((clip.caption_y ?? (clip.position_y ?? 78) / 100) * 100)))}
                    onChange={(e) => {
                      const pct = parseFloat(e.target.value);
                      onUpdateClip({
                        position_y: pct,
                        caption_y: Number((pct / 100).toFixed(3)),
                        caption_custom_position: true,
                        face_safe_captions: false,
                      });
                    }}
                    className="w-full h-1.5 bg-[var(--bg-input)] rounded appearance-none cursor-pointer"
                  />
                </div>

                <div>
                  <div className="flex justify-between text-[11px] text-[var(--text-secondary)] mb-1">
                    <span>Box Width</span>
                    <span className="font-mono font-bold text-[var(--accent-primary)]">
                      {Math.round((clip.caption_max_width ?? 0.82) * 100)}%
                    </span>
                  </div>
                  <input
                    type="range"
                    min={45}
                    max={90}
                    value={Math.max(45, Math.min(90, Math.round((clip.caption_max_width ?? 0.82) * 100)))}
                    onChange={(e) =>
                      onUpdateClip({
                        caption_max_width: Number((parseFloat(e.target.value) / 100).toFixed(2)),
                      })
                    }
                    className="w-full h-1.5 bg-[var(--bg-input)] rounded appearance-none cursor-pointer"
                  />
                </div>

                <div>
                  <div className="flex justify-between text-[11px] text-[var(--text-secondary)] mb-1">
                    <span>Tilt Angle</span>
                    <span className="font-mono font-bold text-[var(--accent-primary)]">
                      {(clip.caption_rotation ?? 0) > 0 ? `+${clip.caption_rotation}°` : `${clip.caption_rotation ?? 0}°`}
                    </span>
                  </div>
                  <input
                    type="range"
                    min={-15}
                    max={15}
                    step={0.5}
                    value={Math.max(-15, Math.min(15, clip.caption_rotation ?? 0))}
                    onChange={(e) =>
                      onUpdateClip({ caption_rotation: parseFloat(e.target.value) })
                    }
                    className="w-full h-1.5 bg-[var(--bg-input)] rounded appearance-none cursor-pointer"
                  />
                </div>

                <button
                  type="button"
                  onClick={() => {
                    const nextSafe = !(clip.face_safe_captions ?? true);
                    onUpdateClip({
                      face_safe_captions: nextSafe,
                      caption_custom_position: nextSafe ? false : clip.caption_custom_position,
                    });
                  }}
                  className={`w-full px-2.5 py-1.5 rounded-lg border flex items-center justify-between text-[11px] font-semibold transition ${(clip.face_safe_captions ?? true) && !clip.caption_custom_position
                    ? 'bg-[var(--accent-subtle)] border-[var(--accent-primary)] text-[var(--text-primary)]'
                    : 'bg-[var(--bg-input)] border-[var(--border-subtle)] text-[var(--text-secondary)]'
                    }`}
                  title="Automatically dodge speaker face bounding boxes so captions never cover the subject's eyes or mouth"
                >
                  <span className="flex items-center gap-1.5">
                    <ShieldCheck className="w-3.5 h-3.5 text-[var(--accent-primary)]" />
                    <span>Smart Face-Safe Placement</span>
                  </span>
                  <span className="text-[10px] font-bold">
                    {(clip.face_safe_captions ?? true) && !clip.caption_custom_position ? 'ON' : 'OFF'}
                  </span>
                </button>
              </div>
            </div>
          </div>

          {/* Right: Kinetic Physics Animation Cards */}
          <div className="space-y-3">
            <div>
              <span className="text-[11px] font-bold uppercase tracking-wide text-[var(--text-secondary)] flex items-center gap-1.5 mb-1.5">
                <Sparkles className="w-3 h-3 text-[var(--accent-primary)]" />
                Kinetic Entry Physics
              </span>
              <div className="grid grid-cols-2 gap-1.5">
                {kineticPhysics.map((anim) => (
                  <button
                    key={anim.id}
                    type="button"
                    onClick={() => onUpdateClip({ caption_animation: anim.id })}
                    className={`px-2.5 py-1.5 rounded-lg border text-left transition ${clip.caption_animation === anim.id
                      ? 'bg-[var(--accent-primary)] text-[var(--accent-foreground)] font-bold border-[var(--accent-primary)]'
                      : 'bg-[var(--bg-input)] border-[var(--border-subtle)] text-[var(--text-secondary)] hover:text-[var(--text-primary)]'
                      }`}
                  >
                    <div className="text-[11px] font-bold leading-tight">{anim.label}</div>
                    <div className="text-[9px] opacity-80 leading-tight">{anim.desc}</div>
                  </button>
                ))}
              </div>
            </div>

            <div className="pt-2 border-t border-[var(--border-subtle)]">
              <span className="text-[11px] font-bold uppercase tracking-wide text-[var(--text-secondary)] block mb-1.5">
                Classic & Line Modes
              </span>
              <div className="flex flex-wrap gap-1.5">
                {[...animationsOnce, ...animationsLine].map((anim) => (
                  <button
                    key={anim}
                    type="button"
                    onClick={() => onUpdateClip({ caption_animation: anim })}
                    className={`px-2 py-0.5 rounded-md border text-[10px] transition ${clip.caption_animation === anim
                      ? 'bg-[var(--accent-primary)] text-[var(--accent-foreground)] font-bold border-[var(--accent-primary)]'
                      : 'bg-[var(--bg-input)] border-[var(--border-subtle)] text-[var(--text-secondary)] hover:text-[var(--text-primary)]'
                      }`}
                  >
                    {anim}
                  </button>
                ))}
              </div>
            </div>
          </div>
        </div>
      )}
    </div>
  );
};
