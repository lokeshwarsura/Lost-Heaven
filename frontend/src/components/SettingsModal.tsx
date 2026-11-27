import React, { useState, useEffect } from 'react';
import { X, Sparkles, Check, Key, Cpu, HardDrive, RefreshCw } from 'lucide-react';
import { YoutubeIcon } from './Icons';
import { StudioSettings } from '../types';
import { api } from '../api/client';

interface SettingsModalProps {
  isOpen: boolean;
  onClose: () => void;
  onOpenSetupWizard?: () => void;
}

export const SettingsModal: React.FC<SettingsModalProps> = ({ isOpen, onClose, onOpenSetupWizard }) => {
  const [settings, setSettings] = useState<StudioSettings | null>(null);
  const [apiKey, setApiKey] = useState('');
  const [model, setModel] = useState('gemini-3.8-flash');
  const [whisperModel, setWhisperModel] = useState('large-v3-turbo');
  const [isSaving, setIsSaving] = useState(false);
  const [saveSuccess, setSaveSuccess] = useState(false);

  useEffect(() => {
    if (isOpen) {
      api.getSettings().then((s) => {
        setSettings(s);
        setModel(s.gemini_model || 'gemini-3.8-flash');
        setWhisperModel(s.whisper_model || 'large-v3-turbo');
      });
    }
  }, [isOpen]);

  const handleSave = async (e: React.FormEvent) => {
    e.preventDefault();
    setIsSaving(true);
    try {
      await api.updateSettings({
        gemini_api_key: apiKey.trim() || undefined,
        gemini_model: model,
        whisper_model: whisperModel
      });
      setSaveSuccess(true);
      setTimeout(() => setSaveSuccess(false), 2000);
    } finally {
      setIsSaving(false);
    }
  };

  if (!isOpen) return null;

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/70 backdrop-blur-sm p-4">
      <div className="w-full max-w-lg bg-[var(--bg-surface)] border border-[var(--border-subtle)] rounded-2xl shadow-2xl overflow-hidden animate-fadeIn text-[var(--text-primary)]">
        {/* Header */}
        <div className="p-5 border-b border-[var(--border-subtle)] flex items-center justify-between">
          <div className="flex items-center gap-2">
            <Sparkles className="w-5 h-5 text-[var(--accent-primary)]" />
            <h2 className="text-sm font-bold text-[var(--text-primary)]">Studio Configuration</h2>
          </div>
          <button onClick={onClose} className="p-1 rounded-lg text-[var(--text-secondary)] hover:text-[var(--text-primary)] hover:bg-[var(--bg-card)]">
            <X className="w-4 h-4" />
          </button>
        </div>

        {/* Form Body */}
        <form onSubmit={handleSave} className="p-5 space-y-4">
          {/* Gemini API Key */}
          <div>
            <div className="flex items-center justify-between mb-1.5">
              <label className="text-xs font-bold text-[var(--text-primary)] flex items-center gap-1.5">
                <Key className="w-3.5 h-3.5 text-[var(--accent-primary)]" />
                <span>Google Gemini API Key</span>
              </label>
              {settings?.gemini_configured ? (
                <span className="text-[10px] text-emerald-400 font-semibold flex items-center gap-1">
                  <Check className="w-3 h-3" /> Connected
                </span>
              ) : (
                <span className="text-[10px] text-amber-400 font-semibold">
                  Offline Fallback Active
                </span>
              )}
            </div>
            <input
              type="password"
              value={apiKey}
              onChange={(e) => setApiKey(e.target.value)}
              placeholder="AIzaSy... (Paste to update)"
              className="w-full px-3 py-2 rounded-lg bg-[var(--bg-input)] border border-[var(--border-subtle)] focus:border-[var(--accent-primary)] text-xs text-[var(--text-primary)] outline-none"
            />
            <p className="text-[10px] text-[var(--text-secondary)] mt-1">
              Required for AI viral clip detection. If omitted, the 100% offline acoustic & transcript engine runs locally.
            </p>
          </div>

          {/* Gemini Model Selector */}
          <div>
            <label className="text-xs font-bold text-[var(--text-primary)] block mb-1.5">
              Gemini Vision Model
            </label>
            <select
              value={model}
              onChange={(e) => setModel(e.target.value)}
              className="w-full px-3 py-2 rounded-lg bg-[var(--bg-input)] border border-[var(--border-subtle)] focus:border-[var(--accent-primary)] text-xs text-[var(--text-primary)] outline-none"
            >
              <option value="gemini-3.5-flash-lite">gemini-3.5-flash-lite (Fastest & Recommended)</option>
              <option value="gemini-3.6-flash">gemini-3.6-flash (Balanced Multimodal)</option>
              <option value="gemini-4-hydrogen">gemini-4-hydrogen (Flagship Intelligence)</option>
              <option value="gemini-2.5-flash-lite">gemini-2.5-flash-lite (Stable Fallback)</option>
            </select>
          </div>

          {/* Local Whisper Model */}
          <div>
            <label className="text-xs font-bold text-[var(--text-primary)] flex items-center gap-1.5 mb-1.5">
              <Cpu className="w-3.5 h-3.5 text-[var(--accent-primary)]" />
              <span>Faster-Whisper Model (Local Machine)</span>
            </label>
            <select
              value={whisperModel}
              onChange={(e) => setWhisperModel(e.target.value)}
              className="w-full px-3 py-2 rounded-lg bg-[var(--bg-input)] border border-[var(--border-subtle)] focus:border-[var(--accent-primary)] text-xs text-[var(--text-primary)] outline-none"
            >
              <option value="large-v3-turbo">large-v3-turbo (Highest Accuracy)</option>
              <option value="base">base (Ultra Fast CPU)</option>
              <option value="small">small (Balanced)</option>
              <option value="medium">medium</option>
            </select>
          </div>

          {/* System Environment Status */}
          <div className="p-3.5 rounded-xl bg-[var(--bg-card)] border border-[var(--border-subtle)] space-y-2 text-xs">
            <span className="font-bold text-[var(--text-primary)] block text-[11px] uppercase tracking-wide">
              System Environment
            </span>
            <div className="flex items-center justify-between text-[var(--text-secondary)]">
              <span className="flex items-center gap-1">
                <YoutubeIcon className="w-3.5 h-3.5 text-red-400" /> YouTube OAuth:
              </span>
              <span className={settings?.youtube_authenticated ? 'text-emerald-400 font-semibold' : 'text-[var(--text-secondary)]'}>
                {settings?.youtube_authenticated ? 'Active' : 'Unlinked (Requires client_secrets.json)'}
              </span>
            </div>
            <div className="flex items-center justify-between text-[var(--text-secondary)]">
              <span className="flex items-center gap-1">
                <HardDrive className="w-3.5 h-3.5 text-[var(--accent-primary)]" /> Media Engine:
              </span>
              <span className="text-emerald-400 font-mono text-[11px]">
                FFmpeg Active
              </span>
            </div>
            {onOpenSetupWizard && (
              <div className="pt-2 border-t border-[var(--border-subtle)]">
                <button
                  type="button"
                  onClick={() => {
                    onClose();
                    onOpenSetupWizard();
                  }}
                  className="w-full py-2 px-3 rounded-lg bg-[var(--bg-surface)] hover:bg-[var(--bg-hover)] border border-[var(--border-subtle)] text-xs font-bold text-[var(--text-primary)] flex items-center justify-center gap-1.5 transition-colors"
                >
                  <RefreshCw className="w-3.5 h-3.5 text-[var(--accent-primary)]" />
                  <span>Re-Scan PC Specs &amp; Setup Wizard</span>
                </button>
              </div>
            )}
          </div>

          {/* Submit */}
          <div className="pt-2 flex items-center justify-end gap-2">
            <button
              type="button"
              onClick={onClose}
              className="px-4 py-2 rounded-lg bg-[var(--bg-card)] border border-[var(--border-subtle)] text-[var(--text-secondary)] hover:text-[var(--text-primary)] text-xs font-semibold"
            >
              Cancel
            </button>
            <button
              type="submit"
              disabled={isSaving}
              className="px-4 py-2 rounded-lg bg-[var(--accent-primary)] hover:opacity-90 text-[var(--accent-foreground)] text-xs font-bold transition-all shadow-md"
            >
              {saveSuccess ? 'Saved!' : isSaving ? 'Saving...' : 'Save Settings'}
            </button>
          </div>
        </form>
      </div>
    </div>
  );
};
