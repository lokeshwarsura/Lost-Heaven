import React, { useState } from 'react';
import {
  Sparkles, Cpu, Palette, CheckCircle2, ArrowRight, X, Key, ShieldCheck
} from 'lucide-react';
import { SystemInfo } from '../types';
import { THEMES, applyTheme, getActiveTheme } from '../themes/themes';
import { api } from '../api/client';

interface FirstTimeSetupWizardProps {
  isOpen: boolean;
  onClose: () => void;
  systemInfo: SystemInfo | null;
}

export const FirstTimeSetupWizard: React.FC<FirstTimeSetupWizardProps> = ({
  isOpen,
  onClose,
  systemInfo
}) => {
  const [step, setStep] = useState<1 | 2 | 3>(1);
  const [aiMode, setAiMode] = useState<'gemini' | 'offline'>('gemini');
  const [apiKey, setApiKey] = useState('');
  const [selectedThemeId, setSelectedThemeId] = useState(getActiveTheme().id);
  const [isSaving, setIsSaving] = useState(false);

  if (!isOpen) return null;

  const handleFinish = async () => {
    setIsSaving(true);
    try {
      applyTheme(selectedThemeId);
      await api.updateSystemSettings({
        active_theme: selectedThemeId,
        gemini_boost_enabled: aiMode === 'gemini',
        ...(apiKey.trim() ? { gemini_api_key: apiKey.trim() } : {})
      });
    } catch (e) {
      console.warn('Wizard save settings warning:', e);
    } finally {
      localStorage.setItem('lost_heaven_onboarded', '1');
      setIsSaving(false);
      onClose();
    }
  };

  const handleSkip = () => {
    localStorage.setItem('lost_heaven_onboarded', '1');
    onClose();
  };

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/75 backdrop-blur-sm p-4 animate-fadeIn">
      <div className="w-full max-w-xl bg-[var(--bg-surface)] border border-[var(--border-subtle)] rounded-3xl shadow-2xl overflow-hidden text-[var(--text-primary)]">
        {/* Header */}
        <div className="px-6 py-4 border-b border-[var(--border-subtle)] flex items-center justify-between bg-[var(--bg-card)]">
          <div className="flex items-center gap-2.5">
            <div className="w-8 h-8 rounded-xl bg-[var(--accent-primary)] text-[var(--accent-foreground)] flex items-center justify-center font-black text-xs tracking-tighter">
              LH
            </div>
            <div>
              <h2 className="text-sm font-bold text-[var(--text-primary)]">Welcome to Lost Heaven Studio</h2>
              <p className="text-[11px] text-[var(--text-secondary)] tabular-nums">Step {step} of 3 · Quick Studio Setup</p>
            </div>
          </div>
          <button
            onClick={handleSkip}
            className="p-1.5 rounded-lg text-[var(--text-secondary)] hover:text-[var(--text-primary)] hover:bg-[var(--bg-input)] transition"
            title="Skip setup"
          >
            <X className="w-4 h-4" />
          </button>
        </div>

        {/* Body */}
        <div className="p-6 space-y-5">
          {step === 1 && (
            <div className="space-y-4">
              <div className="flex items-center gap-2 text-xs font-bold uppercase tracking-wider text-[var(--accent-primary)]">
                <Cpu className="w-4 h-4" />
                <span>1. Detected Local Hardware</span>
              </div>
              <p className="text-xs text-[var(--text-secondary)] leading-relaxed">
                Lost Heaven runs Whisper voice recognition and FFmpeg rendering directly on your hardware for maximum speed and privacy.
              </p>
              <div className="grid grid-cols-2 gap-3 text-xs font-mono tabular-nums">
                <div className="p-3 rounded-xl bg-[var(--bg-card)] border border-[var(--border-subtle)]">
                  <div className="text-[10px] text-[var(--text-secondary)] uppercase">GPU & Encoder</div>
                  <div className="font-bold text-[var(--text-primary)] truncate mt-0.5">
                    {systemInfo?.gpu_name && systemInfo.gpu_name !== 'None' ? systemInfo.gpu_name : 'CPU Fallback'}
                  </div>
                  <div className="text-[10px] text-emerald-400 mt-0.5">{systemInfo?.active_encoder || 'libx264'}</div>
                </div>
                <div className="p-3 rounded-xl bg-[var(--bg-card)] border border-[var(--border-subtle)]">
                  <div className="text-[10px] text-[var(--text-secondary)] uppercase">CPU & Memory</div>
                  <div className="font-bold text-[var(--text-primary)] truncate mt-0.5">
                    {systemInfo?.cpu_cores || '—'} logical cores
                  </div>
                  <div className="text-[10px] text-[var(--text-secondary)] mt-0.5">
                    {systemInfo?.ram_gb ? `${systemInfo.ram_gb} GB RAM` : 'Detecting RAM'}
                  </div>
                </div>
              </div>
            </div>
          )}

          {step === 2 && (
            <div className="space-y-4">
              <div className="flex items-center gap-2 text-xs font-bold uppercase tracking-wider text-[var(--accent-primary)]">
                <Sparkles className="w-4 h-4" />
                <span>2. Viral Moment Intelligence</span>
              </div>
              <div className="grid grid-cols-2 gap-3 text-xs">
                <button
                  type="button"
                  onClick={() => setAiMode('gemini')}
                  className={`p-3.5 rounded-xl border text-left transition ${
                    aiMode === 'gemini'
                      ? 'bg-[var(--accent-primary)]/15 border-[var(--accent-primary)]'
                      : 'bg-[var(--bg-card)] border-[var(--border-subtle)]'
                  }`}
                >
                  <div className="font-bold text-[var(--text-primary)] flex items-center gap-1.5">
                    <Key className="w-3.5 h-3.5 text-[var(--accent-primary)]" />
                    <span>Gemini AI Boost</span>
                  </div>
                  <div className="text-[11px] text-[var(--text-secondary)] mt-1">
                    4-model high-availability cascade with auto-fallback to offline.
                  </div>
                </button>
                <button
                  type="button"
                  onClick={() => setAiMode('offline')}
                  className={`p-3.5 rounded-xl border text-left transition ${
                    aiMode === 'offline'
                      ? 'bg-[var(--accent-primary)]/15 border-[var(--accent-primary)]'
                      : 'bg-[var(--bg-card)] border-[var(--border-subtle)]'
                  }`}
                >
                  <div className="font-bold text-[var(--text-primary)] flex items-center gap-1.5">
                    <ShieldCheck className="w-3.5 h-3.5 text-emerald-400" />
                    <span>100% Offline Engine</span>
                  </div>
                  <div className="text-[11px] text-[var(--text-secondary)] mt-1">
                    Zero cloud calls. Local acoustic energy + Whisper scoring.
                  </div>
                </button>
              </div>

              {aiMode === 'gemini' && (
                <div className="space-y-1.5">
                  <label className="text-[11px] font-semibold text-[var(--text-secondary)] block">
                    Google Gemini API Key (optional — you can also add this later)
                  </label>
                  <input
                    type="password"
                    value={apiKey}
                    onChange={(e) => setApiKey(e.target.value)}
                    placeholder="AIzaSy..."
                    className="w-full bg-[var(--bg-input)] border border-[var(--border-subtle)] rounded-xl px-3.5 py-2.5 text-xs text-[var(--text-primary)] outline-none focus:border-[var(--accent-primary)]"
                  />
                </div>
              )}
            </div>
          )}

          {step === 3 && (
            <div className="space-y-4">
              <div className="flex items-center gap-2 text-xs font-bold uppercase tracking-wider text-[var(--accent-primary)]">
                <Palette className="w-4 h-4" />
                <span>3. Choose Your Studio Theme</span>
              </div>
              <div className="grid grid-cols-3 gap-2 max-h-56 overflow-y-auto pr-1">
                {THEMES.map((t) => {
                  const active = selectedThemeId === t.id;
                  return (
                    <button
                      key={t.id}
                      type="button"
                      onClick={() => {
                        setSelectedThemeId(t.id);
                        applyTheme(t.id);
                      }}
                      className={`p-2.5 rounded-xl border text-left transition flex items-center gap-2 ${
                        active
                          ? 'bg-[var(--accent-primary)]/15 border-[var(--accent-primary)]'
                          : 'bg-[var(--bg-card)] border-[var(--border-subtle)] hover:border-[var(--accent-primary)]/40'
                      }`}
                    >
                      <span
                        className="w-3.5 h-3.5 rounded-full flex-shrink-0 border border-black/20"
                        style={{ backgroundColor: t.accentPrimary }}
                      />
                      <span className="text-[11px] font-semibold text-[var(--text-primary)] truncate">{t.name}</span>
                    </button>
                  );
                })}
              </div>
            </div>
          )}
        </div>

        {/* Footer */}
        <div className="px-6 py-4 border-t border-[var(--border-subtle)] bg-[var(--bg-card)] flex items-center justify-between">
          <button
            type="button"
            onClick={handleSkip}
            className="text-xs text-[var(--text-secondary)] hover:text-[var(--text-primary)] transition"
          >
            Skip for now
          </button>

          <div className="flex items-center gap-2">
            {step > 1 && (
              <button
                type="button"
                onClick={() => setStep((s) => (s === 3 ? 2 : 1))}
                className="px-3.5 py-2 rounded-xl bg-[var(--bg-input)] border border-[var(--border-subtle)] text-xs font-semibold text-[var(--text-primary)]"
              >
                Back
              </button>
            )}
            {step < 3 ? (
              <button
                type="button"
                onClick={() => setStep((s) => (s === 1 ? 2 : 3))}
                className="px-4 py-2 rounded-xl bg-[var(--accent-primary)] text-[var(--accent-foreground)] text-xs font-bold flex items-center gap-1.5"
              >
                <span>Next</span>
                <ArrowRight className="w-3.5 h-3.5" />
              </button>
            ) : (
              <button
                type="button"
                onClick={handleFinish}
                disabled={isSaving}
                className="px-4 py-2 rounded-xl bg-[var(--accent-primary)] text-[var(--accent-foreground)] text-xs font-bold flex items-center gap-1.5 disabled:opacity-50"
              >
                <CheckCircle2 className="w-3.5 h-3.5" />
                <span>{isSaving ? 'Saving...' : 'Finish & Enter Studio'}</span>
              </button>
            )}
          </div>
        </div>
      </div>
    </div>
  );
};
