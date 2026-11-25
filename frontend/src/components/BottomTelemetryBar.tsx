import React, { useEffect, useState } from 'react';
import { Cpu, HardDrive, Zap, Sparkles } from 'lucide-react';
import { SystemInfo, StudioSettings } from '../types';

interface BottomTelemetryBarProps {
  systemInfo: SystemInfo | null;
  studioSettings?: Partial<StudioSettings> | null;
  isRendering: boolean;
  renderProgress: number;
}

export const BottomTelemetryBar: React.FC<BottomTelemetryBarProps> = ({
  systemInfo,
  studioSettings,
  isRendering,
  renderProgress
}) => {
  const [liveOverrides, setLiveOverrides] = useState<Partial<StudioSettings>>({});

  useEffect(() => {
    const handleSettingsUpdated = (evt: Event) => {
      const detail = (evt as CustomEvent<Partial<StudioSettings>>)?.detail;
      if (detail && typeof detail === 'object') {
        setLiveOverrides((prev) => ({ ...prev, ...detail }));
      }
    };
    window.addEventListener('lh-settings-updated', handleSettingsUpdated);
    return () => window.removeEventListener('lh-settings-updated', handleSettingsUpdated);
  }, []);

  const mergedSettings = {
    ...(studioSettings || {}),
    ...liveOverrides,
  };

  const gpuName =
    systemInfo?.gpu_name && systemInfo.gpu_name !== 'None'
      ? systemInfo.gpu_name
      : 'CPU Mode';
  const encoder = systemInfo?.active_encoder || 'libx264';
  const whisperModel =
    mergedSettings?.whisper_model || systemInfo?.whisper_model || 'base';

  const geminiEnabled =
    mergedSettings?.gemini_enabled ??
    mergedSettings?.gemini_boost_enabled ??
    systemInfo?.gemini_enabled ??
    true;
  const aiProvider =
    mergedSettings?.ai_provider || systemInfo?.ai_provider || 'gemini';
  const geminiConfigured =
    mergedSettings?.gemini_configured ??
    systemInfo?.gemini_configured ??
    systemInfo?.gemini_status === 'active';
  const geminiModel =
    mergedSettings?.gemini_model ||
    systemInfo?.gemini_model ||
    'gemini-3.5-flash-lite';

  let aiLabel = 'Offline Local Mode';
  let aiToneClass = 'text-emerald-400';
  if (aiProvider === 'offline' || geminiEnabled === false) {
    aiLabel = 'Offline Local Mode';
    aiToneClass = 'text-[var(--accent-primary)]';
  } else if (aiProvider === 'ollama') {
    aiLabel = `Ollama · ${mergedSettings?.ollama_model || 'local'}`;
    aiToneClass = 'text-emerald-400';
  } else if (geminiConfigured) {
    aiLabel = `Gemini AI · ${geminiModel}`;
    aiToneClass = 'text-emerald-400';
  } else {
    aiLabel = `Gemini AI · Key Needed (${geminiModel})`;
    aiToneClass = 'text-amber-400';
  }

  return (
    <footer className="h-8 shrink-0 border-t border-[var(--border-subtle)] bg-[var(--bg-surface)] px-4 flex items-center justify-between gap-4 text-[11px] text-[var(--text-secondary)] select-none overflow-hidden">
      {/* Left: Status + GPU/Encoder + CPU/RAM */}
      <div className="flex items-center gap-4 min-w-0 tabular-nums">
        {/* Status Dot */}
        <div className="flex items-center gap-1.5 shrink-0">
          <span
            className={`w-1.5 h-1.5 rounded-full ${
              isRendering ? 'bg-[var(--accent-primary)] animate-pulse' : 'bg-emerald-400'
            }`}
          />
          <span className="font-medium text-[var(--text-primary)]">
            {isRendering ? `Rendering... ${renderProgress}%` : 'Ready'}
          </span>
        </div>

        <span className="text-[var(--border-subtle)]">|</span>

        {/* GPU & Encoder */}
        <div className="flex items-center gap-1.5 min-w-0">
          <Zap className="w-3 h-3 text-[var(--accent-primary)] shrink-0" />
          <span className="font-medium text-[var(--text-primary)] truncate">
            {gpuName}
          </span>
          <span>·</span>
          <span className="font-mono text-[10px] font-semibold text-emerald-400 shrink-0">
            {encoder}
          </span>
        </div>

        {/* CPU & RAM (wide viewports) */}
        <div className="hidden lg:flex items-center gap-1.5 shrink-0">
          <span className="text-[var(--border-subtle)] mr-2">|</span>
          <Cpu className="w-3 h-3 text-[var(--text-secondary)]" />
          <span>{systemInfo?.cpu_cores ? `${systemInfo.cpu_cores} cores` : '—'}</span>
          <span>·</span>
          <span>{systemInfo?.ram_gb ? `${systemInfo.ram_gb} GB` : '—'}</span>
        </div>
      </div>

      {/* Right: AI Engine Status & Whisper Model */}
      <div className="flex items-center gap-4 shrink-0 tabular-nums">
        <div className="flex items-center gap-1.5">
          <Sparkles className="w-3 h-3 text-[var(--accent-primary)]" />
          <span className={`font-medium ${aiToneClass}`}>{aiLabel}</span>
        </div>

        <span className="text-[var(--border-subtle)]">|</span>

        <div className="flex items-center gap-1.5">
          <HardDrive className="w-3 h-3 text-[var(--text-secondary)]" />
          <span>
            Whisper · <strong className="font-semibold text-[var(--text-primary)]">{whisperModel}</strong>
          </span>
        </div>
      </div>
    </footer>
  );
};
