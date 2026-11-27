import React, { useState } from 'react';
import {
  CheckCircle2,
  Cpu,
  ExternalLink,
  Eye,
  EyeOff,
  Key,
  Loader2,
  ShieldCheck,
  Sparkles,
  X,
} from 'lucide-react';
import { api } from '../api/client';
import { StudioSettings } from '../types';

interface AIEngineModalProps {
  isOpen: boolean;
  onClose: () => void;
  title?: string;
  subtitle?: string;
  currentMode?: 'gemini' | 'offline' | string | null;
  geminiConfigured?: boolean;
  onChosen?: (mode: 'gemini' | 'offline') => Promise<void> | void;
}

export const AIEngineModal: React.FC<AIEngineModalProps> = ({
  isOpen,
  onClose,
  title = 'Choose AI Analysis Engine',
  subtitle = 'Select how Lost Heaven analyzes highlights before launching the pipeline.',
  currentMode,
  geminiConfigured = false,
  onChosen,
}) => {
  const [apiKey, setApiKey] = useState('');
  const [showKey, setShowKey] = useState(false);
  const [isTesting, setIsTesting] = useState(false);
  const [isSaving, setIsSaving] = useState(false);
  const [testResult, setTestResult] = useState<{ ok: boolean; msg: string } | null>(null);
  const [errorMsg, setErrorMsg] = useState<string | null>(null);

  if (!isOpen) return null;

  const broadcastPatch = (patch: Partial<StudioSettings>) => {
    window.dispatchEvent(new CustomEvent('lh-settings-updated', { detail: patch }));
  };

  const handleTestKey = async () => {
    const trimmed = apiKey.trim();
    if (!trimmed && !geminiConfigured) {
      setErrorMsg('Enter your Google Gemini API key (AIza...) first to test connectivity.');
      return;
    }
    setErrorMsg(null);
    setTestResult(null);
    setIsTesting(true);
    try {
      const res = await api.testGeminiModels(trimmed || undefined);
      const okModel = res.models?.find((m) => m.status === 'OK');
      const invalidModel = res.models?.find((m) => m.status === 'Invalid Key');
      if (invalidModel || !res.api_key_configured) {
        setTestResult({
          ok: false,
          msg: invalidModel?.detail || 'API key was rejected by Google AI Studio.',
        });
      } else if (okModel) {
        setTestResult({
          ok: true,
          msg: `Verified! Live response from ${okModel.model} (${okModel.latency_ms} ms).`,
        });
      } else {
        const first = res.models?.[0];
        setTestResult({
          ok: true,
          msg: first
            ? `Key accepted (${first.model}: ${first.status}). Ready for cascade.`
            : 'Key formatted cleanly.',
        });
      }
    } catch (err: any) {
      setTestResult({ ok: false, msg: err?.message || 'Network/API test failed.' });
    } finally {
      setIsTesting(false);
    }
  };

  const handleSelectGemini = async () => {
    const trimmed = apiKey.trim();
    if (!trimmed && !geminiConfigured) {
      setErrorMsg('Paste a Google Gemini API key (AIza...) or choose Offline Multimodal AI Mode below.');
      return;
    }
    setErrorMsg(null);
    setIsSaving(true);
    try {
      const payload: Partial<StudioSettings & { gemini_api_key?: string }> = {
        ai_mode: 'gemini',
        ai_provider: 'gemini',
        gemini_enabled: true,
        gemini_boost_enabled: true,
      };
      if (trimmed) {
        payload.gemini_api_key = trimmed;
      }
      await Promise.all([api.updateSystemSettings(payload), api.updateSettings(payload)]);
      broadcastPatch({
        ai_mode: 'gemini',
        ai_provider: 'gemini',
        gemini_enabled: true,
        gemini_boost_enabled: true,
        gemini_configured: true,
        effective_ai_mode: 'gemini',
        needs_api_key_or_offline_choice: false,
      });
      if (onChosen) await onChosen('gemini');
      else onClose();
    } catch (err: any) {
      setErrorMsg(err?.message || 'Failed to save Gemini configuration.');
    } finally {
      setIsSaving(false);
    }
  };

  const handleSelectOffline = async () => {
    setErrorMsg(null);
    setIsSaving(true);
    try {
      const payload: Partial<StudioSettings> = {
        ai_mode: 'offline',
        ai_provider: 'offline',
        gemini_enabled: false,
        gemini_boost_enabled: false,
      };
      await Promise.all([api.updateSystemSettings(payload), api.updateSettings(payload)]);
      broadcastPatch({
        ai_mode: 'offline',
        ai_provider: 'offline',
        gemini_enabled: false,
        gemini_boost_enabled: false,
        effective_ai_mode: 'offline',
        needs_api_key_or_offline_choice: false,
      });
      if (onChosen) await onChosen('offline');
      else onClose();
    } catch (err: any) {
      setErrorMsg(err?.message || 'Failed to switch to Offline Multimodal AI mode.');
    } finally {
      setIsSaving(false);
    }
  };

  return (
    <div className="fixed inset-0 z-50 bg-black/75 backdrop-blur-sm flex items-center justify-center p-4 animate-fadeIn">
      <div className="w-full max-w-2xl bg-[var(--bg-surface)] border border-[var(--border-subtle)] rounded-2xl shadow-2xl overflow-hidden text-[var(--text-primary)]">
        {/* Header */}
        <div className="px-5 py-4 border-b border-[var(--border-subtle)] bg-[var(--bg-card)] flex items-center justify-between gap-3">
          <div className="flex items-center gap-2.5 min-w-0">
            <div className="w-8 h-8 rounded-xl bg-[var(--accent-subtle)] border border-[var(--accent-primary)]/40 flex items-center justify-center shrink-0">
              <Sparkles className="w-4 h-4 text-[var(--accent-primary)]" />
            </div>
            <div className="min-w-0">
              <h2 className="font-bold text-sm text-[var(--text-primary)] truncate">{title}</h2>
              <p className="text-[11px] text-[var(--text-secondary)] truncate">{subtitle}</p>
            </div>
          </div>
          <button
            type="button"
            onClick={onClose}
            className="p-1.5 rounded-lg text-[var(--text-secondary)] hover:text-[var(--text-primary)] hover:bg-[var(--bg-input)] transition"
            aria-label="Close AI Engine dialog"
          >
            <X className="w-4 h-4" />
          </button>
        </div>

        <div className="p-5 space-y-4">
          {/* Option 1: Gemini Cloud + Multimodal AI */}
          <div
            className={`p-4 rounded-xl border space-y-3 transition ${
              currentMode === 'gemini' && geminiConfigured
                ? 'bg-[var(--accent-subtle)]/40 border-[var(--accent-primary)]'
                : 'bg-[var(--bg-card)] border-[var(--border-subtle)]'
            }`}
          >
            <div className="flex items-start justify-between gap-2">
              <div className="flex items-center gap-2">
                <Key className="w-4 h-4 text-[var(--accent-primary)] shrink-0" />
                <div>
                  <h3 className="text-xs font-extrabold text-[var(--text-primary)]">
                    1. Use Gemini Cloud + Multimodal AI
                  </h3>
                  <p className="text-[11px] text-[var(--text-secondary)]">
                    Pairs local speech, facial emotion &amp; acoustic peaks with Gemini cloud narrative ranking &amp; titles.
                  </p>
                </div>
              </div>
              {geminiConfigured && (
                <span className="px-2 py-0.5 rounded-full bg-emerald-500/15 border border-emerald-500/30 text-emerald-400 text-[10px] font-bold shrink-0">
                  Key Saved
                </span>
              )}
            </div>

            <div className="flex flex-col sm:flex-row gap-2">
              <div className="relative flex-1">
                <input
                  type={showKey ? 'text' : 'password'}
                  value={apiKey}
                  onChange={(e) => {
                    setApiKey(e.target.value);
                    if (errorMsg) setErrorMsg(null);
                  }}
                  placeholder={
                    geminiConfigured
                      ? 'Existing key configured (optional: paste new AIza... key to replace)'
                      : 'Paste Google Gemini API key (AIzaSy...)'
                  }
                  className="w-full bg-[var(--bg-input)] border border-[var(--border-subtle)] focus:border-[var(--accent-primary)] rounded-xl px-3.5 py-2 pr-9 text-xs text-[var(--text-primary)] font-mono outline-none"
                />
                <button
                  type="button"
                  onClick={() => setShowKey((s) => !s)}
                  className="absolute right-2.5 top-1/2 -translate-y-1/2 text-[var(--text-secondary)] hover:text-[var(--text-primary)]"
                  title={showKey ? 'Hide key' : 'Show key'}
                >
                  {showKey ? <EyeOff className="w-3.5 h-3.5" /> : <Eye className="w-3.5 h-3.5" />}
                </button>
              </div>

              <button
                type="button"
                onClick={handleTestKey}
                disabled={isTesting || isSaving}
                className="px-3.5 py-2 rounded-xl bg-[var(--bg-input)] border border-[var(--border-subtle)] hover:border-[var(--accent-primary)]/50 text-xs font-bold text-[var(--text-primary)] flex items-center justify-center gap-1.5 transition disabled:opacity-50 shrink-0"
              >
                {isTesting ? <Loader2 className="w-3.5 h-3.5 animate-spin" /> : <ShieldCheck className="w-3.5 h-3.5 text-emerald-400" />}
                <span>{isTesting ? 'Testing...' : 'Test Key'}</span>
              </button>
            </div>

            {testResult && (
              <div
                className={`px-3 py-2 rounded-lg text-[11px] font-medium border flex items-center gap-2 ${
                  testResult.ok
                    ? 'bg-emerald-500/10 border-emerald-500/30 text-emerald-400'
                    : 'bg-red-500/10 border-red-500/30 text-red-400'
                }`}
              >
                <CheckCircle2 className="w-3.5 h-3.5 shrink-0" />
                <span>{testResult.msg}</span>
              </div>
            )}

            <div className="flex flex-wrap items-center justify-between gap-2 pt-0.5">
              <a
                href="https://aistudio.google.com/apikey"
                target="_blank"
                rel="noopener noreferrer"
                className="text-[11px] text-[var(--accent-primary)] hover:underline inline-flex items-center gap-1"
              >
                <span>Get free Gemini API key</span>
                <ExternalLink className="w-3 h-3" />
              </a>

              <button
                type="button"
                onClick={handleSelectGemini}
                disabled={isSaving || isTesting}
                className="px-4 py-2 rounded-xl bg-[var(--accent-primary)] text-[var(--accent-foreground)] font-extrabold text-xs hover:opacity-90 transition flex items-center gap-1.5 disabled:opacity-50"
              >
                {isSaving ? <Loader2 className="w-3.5 h-3.5 animate-spin" /> : <Sparkles className="w-3.5 h-3.5" />}
                <span>Save &amp; Use Gemini Cloud</span>
              </button>
            </div>
          </div>

          {/* Option 2: 100% Offline Multimodal AI Mode */}
          <div
            className={`p-4 rounded-xl border space-y-3 transition ${
              currentMode === 'offline'
                ? 'bg-emerald-500/10 border-emerald-500/45'
                : 'bg-[var(--bg-card)] border-[var(--border-subtle)]'
            }`}
          >
            <div className="flex flex-col sm:flex-row sm:items-center justify-between gap-3">
              <div className="flex items-start gap-2.5">
                <Cpu className="w-4 h-4 text-emerald-400 mt-0.5 shrink-0" />
                <div>
                  <h3 className="text-xs font-extrabold text-[var(--text-primary)]">
                    2. Run in Offline Multimodal AI Mode (Zero API Key Needed)
                  </h3>
                  <p className="text-[11px] text-[var(--text-secondary)] leading-relaxed mt-0.5">
                    100% local processing via GPU batched <code className="font-mono">faster-whisper</code>, MediaPipe 7-emotion facial analysis, pitch/laughter DSP, and 5 viral narrative arcs. Remembered until changed in Settings.
                  </p>
                </div>
              </div>

              <button
                type="button"
                onClick={handleSelectOffline}
                disabled={isSaving || isTesting}
                className="px-4 py-2.5 rounded-xl bg-emerald-500/20 hover:bg-emerald-500/30 border border-emerald-500/45 text-emerald-300 font-extrabold text-xs transition flex items-center justify-center gap-1.5 disabled:opacity-50 shrink-0"
              >
                <Cpu className="w-3.5 h-3.5" />
                <span>Run in Offline Mode</span>
              </button>
            </div>
          </div>

          {errorMsg && (
            <div className="px-3 py-2 rounded-xl bg-red-500/10 border border-red-500/30 text-red-400 text-xs font-medium">
              {errorMsg}
            </div>
          )}
        </div>
      </div>
    </div>
  );
};
