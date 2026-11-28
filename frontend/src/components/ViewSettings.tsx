import React, { useState, useEffect, useMemo, useRef } from 'react';
import {
  CheckCircle2, AlertTriangle, XCircle, RefreshCw, Copy, Check,
  Palette, Cpu, HardDrive, Download, Trash2, Shield,
  Sparkles, Terminal, Settings as SettingsIcon, Zap,
  Eye, EyeOff, Radio, ExternalLink, ArrowUpRight,
  RotateCcw, Upload, Plus, Sliders, Volume2, Film
} from 'lucide-react';
import { 
  SystemCheckReport, MachineSpecs, ModelsStatusReport, 
  StudioSettings, GeminiModelTestResult, GeminiModelInfo
} from '../types';
import { api } from '../api/client';
import {
  THEMES,
  THEME_CATEGORIES_META,
  applyTheme,
  getActiveTheme,
  getAllThemes,
  getCustomThemes,
  setCustomThemes,
  getThemeCategory,
  buildCustomTheme,
  normalizeCustomTheme,
  computeContrastRatio,
  ThemeCategory,
  ThemeDefinition,
} from '../themes/themes';

interface ViewSettingsProps {
  onOpenSetupWizard?: () => void;
}

export const ViewSettings: React.FC<ViewSettingsProps> = ({ onOpenSetupWizard }) => {
  // System Check
  const [systemCheck, setSystemCheck] = useState<SystemCheckReport | null>(null);
  const [checkingSystem, setCheckingSystem] = useState(false);
  const [copiedReport, setCopiedReport] = useState(false);

  // 4-Tab Studio Preferences Navigation
  type SettingsTabId = 'appearance' | 'ai' | 'render' | 'diagnostics';
  const [activeTab, setActiveTab] = useState<SettingsTabId>('appearance');
  const [resettingDefaults, setResettingDefaults] = useState(false);

  // Themes & Custom Theme Studio
  const [customThemesState, setCustomThemesState] = useState<ThemeDefinition[]>(() => getCustomThemes());
  const [activeTheme, setActiveTheme] = useState<ThemeDefinition>(() => getActiveTheme());
  const [themeCategoryFilter, setThemeCategoryFilter] = useState<'all' | ThemeCategory>('all');
  const [showCustomStudio, setShowCustomStudio] = useState<boolean>(false);
  const [editingCustomId, setEditingCustomId] = useState<string | null>(null);
  const [themeStudioNotice, setThemeStudioNotice] = useState<string | null>(null);
  const [customDraft, setCustomDraft] = useState({
    name: 'Midnight Obsidian',
    description: 'Custom high-contrast studio palette',
    bgCanvas: '#09090E',
    bgSurface: '#12131A',
    bgElevated: '#1B1D28',
    accentPrimary: '#8B5CF6',
    accentSecondary: '#06B6D4',
    textPrimary: '#F8FAFC',
    borderSubtle: '#272A3A',
  });

  // Default Render & Audio Mastering Settings
  const [videoEncoder, setVideoEncoder] = useState<string>('auto');
  const [defaultResolution, setDefaultResolution] = useState<string>('1080x1920');
  const [defaultFps, setDefaultFps] = useState<string>('30');
  const [loudnessTarget, setLoudnessTarget] = useState<number>(-14.0);
  const [audioNormalizationEnabled, setAudioNormalizationEnabled] = useState<boolean>(true);
  const [autoCleanupDays, setAutoCleanupDays] = useState<number>(30);
  const [savingRenderDefaults, setSavingRenderDefaults] = useState<boolean>(false);
  const [renderDefaultsNotice, setRenderDefaultsNotice] = useState<string | null>(null);

  // Whisper Models
  const [modelsData, setModelsData] = useState<ModelsStatusReport | null>(null);
  const [downloadingModel, setDownloadingModel] = useState<string | null>(null);
  const [deletingModel, setDeletingModel] = useState<string | null>(null);
  const [activeWhisperModel, setActiveWhisperModel] = useState<string>('base');

  // Hardware Specs & Binaries
  const [specs, setSpecs] = useState<MachineSpecs | null>(null);
  const [specsLoading, setSpecsLoading] = useState(false);

  // Hardware Acceleration Selection
  const [hardwareMode, setHardwareMode] = useState<string>('gpu');

  // yt-dlp Update
  const [updatingYtdlp, setUpdatingYtdlp] = useState(false);
  const [ytdlpMessage, setYtdlpMessage] = useState<string | null>(null);

  // Gemini Settings & Boost
  const [geminiBoost, setGeminiBoost] = useState<boolean>(true);
  const [geminiApiKey, setGeminiApiKey] = useState<string>('');
  const [geminiModel, setGeminiModel] = useState<string>('gemini-3.5-flash-lite');
  const [geminiAnalysisMode, setGeminiAnalysisMode] = useState<'transcript' | 'multimodal'>('transcript');
  const [autoFallbackOffline, setAutoFallbackOffline] = useState<boolean>(true);
  const [showApiKey, setShowApiKey] = useState<boolean>(false);
  const [savingGemini, setSavingGemini] = useState<boolean>(false);
  const [geminiNotice, setGeminiNotice] = useState<string | null>(null);

  // Gemini Live Model Discovery & Diagnostics
  const [availableGeminiModels, setAvailableGeminiModels] = useState<GeminiModelInfo[]>([]);
  const [loadingGeminiModels, setLoadingGeminiModels] = useState<boolean>(false);
  const [discoveredModelCount, setDiscoveredModelCount] = useState<number>(0);
  const [showCustomModelInput, setShowCustomModelInput] = useState<boolean>(false);
  const [customModelDraft, setCustomModelDraft] = useState<string>('');
  const [testingGemini, setTestingGemini] = useState<boolean>(false);
  const [geminiTestResults, setGeminiTestResults] = useState<GeminiModelTestResult[] | null>(null);
  const [geminiTestError, setGeminiTestError] = useState<string | null>(null);

  // Batch Watch Folder & Pexels B-Roll Integration
  const [pexelsApiKey, setPexelsApiKey] = useState<string>('');
  const [watchFolderPath, setWatchFolderPath] = useState<string>('');
  const [watchFolderEnabled, setWatchFolderEnabled] = useState<boolean>(false);
  const [integrationNotice, setIntegrationNotice] = useState<string | null>(null);
  const [scanningWatch, setScanningWatch] = useState<boolean>(false);
  const [importingCsv, setImportingCsv] = useState<boolean>(false);
  const [csvImportSummary, setCsvImportSummary] = useState<string | null>(null);

  useEffect(() => {
    loadAllDiagnostics();
  }, []);

  const loadGeminiModels = async (explicitKey?: string) => {
    setLoadingGeminiModels(true);
    try {
      const res = await api.getGeminiModels(explicitKey);
      if (res && Array.isArray(res.models) && res.models.length > 0) {
        setAvailableGeminiModels(res.models);
        setDiscoveredModelCount(res.discovered_count || 0);
      }
    } catch (err) {
      console.warn('Failed to fetch Gemini models list:', err);
    } finally {
      setLoadingGeminiModels(false);
    }
  };

  const broadcastSettingsUpdate = (patch: Partial<StudioSettings>) => {
    window.dispatchEvent(new CustomEvent('lh-settings-updated', { detail: patch }));
  };

  const loadAllDiagnostics = async () => {
    setCheckingSystem(true);
    setSpecsLoading(true);
    try {
      const [check, machine, models, sysSettings, gemModels] = await Promise.all([
        api.getSystemCheck().catch(() => null),
        api.getMachineSpecs().catch(() => null),
        api.getModelsStatus().catch(() => null),
        api.getSystemSettings().catch(() => null),
        api.getGeminiModels().catch(() => null)
      ]);

      if (gemModels?.models?.length) {
        setAvailableGeminiModels(gemModels.models);
        setDiscoveredModelCount(gemModels.discovered_count || 0);
      }

      if (check) setSystemCheck(check);
      if (machine) setSpecs(machine);
      if (models) {
        setModelsData(models);
        if (models.active_model) setActiveWhisperModel(models.active_model);
      }
      if (sysSettings) {
        if (typeof sysSettings.gemini_boost_enabled === 'boolean') {
          setGeminiBoost(sysSettings.gemini_boost_enabled);
        }
        if (sysSettings.gemini_model) {
          setGeminiModel(sysSettings.gemini_model);
        }
        if (sysSettings.gemini_analysis_mode) setGeminiAnalysisMode(sysSettings.gemini_analysis_mode);
        if (typeof sysSettings.auto_fallback_offline === 'boolean') {
          setAutoFallbackOffline(sysSettings.auto_fallback_offline);
        }
        if (sysSettings.whisper_model) setActiveWhisperModel(sysSettings.whisper_model);
        if (sysSettings.hardware_acceleration) setHardwareMode(sysSettings.hardware_acceleration);
        if (typeof sysSettings.pexels_api_key === 'string') setPexelsApiKey(sysSettings.pexels_api_key);
        if (typeof sysSettings.watch_folder_path === 'string') setWatchFolderPath(sysSettings.watch_folder_path);
        if (typeof sysSettings.watch_folder_enabled === 'boolean') {
          setWatchFolderEnabled(sysSettings.watch_folder_enabled);
        }
        if (sysSettings.custom_themes && Array.isArray(sysSettings.custom_themes)) {
          const synced = setCustomThemes(sysSettings.custom_themes);
          setCustomThemesState(synced);
        }
        if (sysSettings.video_encoder) setVideoEncoder(sysSettings.video_encoder);
        if (sysSettings.default_resolution) setDefaultResolution(sysSettings.default_resolution);
        if (sysSettings.default_fps) setDefaultFps(String(sysSettings.default_fps));
        if (sysSettings.loudness_target !== undefined && sysSettings.loudness_target !== null) {
          const parsedLt = parseFloat(String(sysSettings.loudness_target).replace(/[^0-9.-]/g, ''));
          if (Number.isFinite(parsedLt)) setLoudnessTarget(parsedLt);
        }
        if (typeof sysSettings.audio_normalization_enabled === 'boolean') setAudioNormalizationEnabled(sysSettings.audio_normalization_enabled);
        if (typeof sysSettings.auto_cleanup_days === 'number') setAutoCleanupDays(sysSettings.auto_cleanup_days);
        if (sysSettings.active_theme) {
          const allList = getAllThemes(
            Array.isArray(sysSettings.custom_themes) ? sysSettings.custom_themes : undefined
          );
          const matched = allList.find((t) => t.id === sysSettings.active_theme);
          if (matched) {
            setActiveTheme(matched);
            applyTheme(matched.id, allList.filter((t) => t.isCustom));
          }
        }
      }
    } catch (err) {
      console.error('Failed to load settings diagnostics:', err);
    } finally {
      setCheckingSystem(false);
      setSpecsLoading(false);
    }
  };

  const handleRunSystemCheck = async () => {
    setCheckingSystem(true);
    try {
      const check = await api.getSystemCheck();
      setSystemCheck(check);
      const machine = await api.getMachineSpecs();
      if (machine) setSpecs(machine);
    } catch (err) {
      console.error('System check failed:', err);
    } finally {
      setCheckingSystem(false);
    }
  };

  const handleCopyReport = () => {
    const checksList = systemCheck?.checks || [];
    const lines = [
      '# Lost Heaven Studio — System Diagnostics Report',
      `Timestamp: ${systemCheck?.timestamp || new Date().toISOString()}`,
      `Status: ${systemCheck?.all_passed ? 'ALL CHECKS PASSED (8/8 OK)' : 'DIAGNOSTIC VERIFIED'}`,
      '',
      '## System Checks:',
      ...checksList.map((c: any) => `- [${c.ok ? 'x' : ' '}] ${c.label || c.name || 'Check'}: ${c.detail}`),
      '',
      '## Hardware Specs:',
      `- CPU: ${specs?.cpu?.model || 'Unknown'} (${specs?.cpu?.cores_logical || 'Unknown'} logical cores)`,
      `- RAM: ${specs?.ram?.total_gb ?? 'Unknown'} GB total, ${specs?.ram?.available_gb ?? 'Unknown'} GB free`,
      `- GPU: ${specs?.gpu?.name || 'None'} (${specs?.gpu?.vram_gb ?? 0} GB VRAM, CUDA: ${specs?.gpu?.has_cuda ? 'Yes' : 'No'})`,
      `- Disk: ${specs?.storage?.free_gb ?? 'Unknown'} GB free of ${specs?.storage?.total_gb ?? 'Unknown'} GB`,
      `- Active Theme: ${activeTheme.name}`
    ];

    navigator.clipboard.writeText(lines.join('\n'));
    setCopiedReport(true);
    setTimeout(() => setCopiedReport(false), 2500);
  };

  const handleSelectTheme = (theme: ThemeDefinition) => {
    setActiveTheme(theme);
    applyTheme(theme.id, customThemesState);
    Promise.all([
      api.updateSystemSettings({ active_theme: theme.id }),
      api.updateSettings({ active_theme: theme.id }),
    ]).catch(() => {});
  };

  const allThemesList = useMemo(() => getAllThemes(customThemesState), [customThemesState]);

  const filteredThemes = useMemo(() => {
    if (themeCategoryFilter === 'all') return allThemesList;
    return allThemesList.filter((t) => getThemeCategory(t) === themeCategoryFilter);
  }, [allThemesList, themeCategoryFilter]);

  const previewDraftTheme = useMemo(
    () =>
      buildCustomTheme({
        id: editingCustomId || undefined,
        ...customDraft,
      }),
    [editingCustomId, customDraft]
  );

  const primaryContrastRatio = useMemo(
    () => computeContrastRatio(customDraft.textPrimary, customDraft.bgCanvas),
    [customDraft.textPrimary, customDraft.bgCanvas]
  );

  const surfaceContrastRatio = useMemo(
    () => computeContrastRatio(customDraft.textPrimary, customDraft.bgSurface),
    [customDraft.textPrimary, customDraft.bgSurface]
  );

  const persistCustomThemesAndSelect = async (
    nextCustom: ThemeDefinition[],
    selectThemeId: string,
    noticeText?: string
  ) => {
    const synced = setCustomThemes(nextCustom);
    setCustomThemesState(synced);
    const chosen =
      getAllThemes(synced).find((t) => t.id === selectThemeId) || THEMES[0];
    setActiveTheme(chosen);
    applyTheme(chosen.id, synced);
    try {
      await Promise.all([
        api.updateSettings({ custom_themes: synced, active_theme: chosen.id }),
        api.updateSystemSettings({ custom_themes: synced, active_theme: chosen.id }),
      ]);
      window.dispatchEvent(
        new CustomEvent('lost-heaven:settings-updated', {
          detail: { custom_themes: synced, active_theme: chosen.id },
        })
      );
      if (noticeText) {
        setThemeStudioNotice(noticeText);
        setTimeout(() => setThemeStudioNotice(null), 3000);
      }
    } catch {
      setThemeStudioNotice('Applied locally; backend settings sync failed.');
    }
  };

  const handleSaveCustomTheme = async () => {
    const cleanName = customDraft.name.trim() || 'Custom Studio Theme';
    let targetId = editingCustomId;
    if (!targetId) {
      const candidateBase = buildCustomTheme({ ...customDraft, name: cleanName }).id;
      const existingIds = new Set(allThemesList.map((t) => t.id));
      targetId = existingIds.has(candidateBase)
        ? `${candidateBase}-${Date.now().toString(36).slice(-4)}`
        : candidateBase;
    }
    const built = buildCustomTheme({
      id: targetId,
      name: cleanName,
      description: customDraft.description.trim() || 'User-crafted Studio Theme',
      bgCanvas: customDraft.bgCanvas,
      bgSurface: customDraft.bgSurface,
      bgElevated: customDraft.bgElevated,
      accentPrimary: customDraft.accentPrimary,
      accentSecondary: customDraft.accentSecondary,
      textPrimary: customDraft.textPrimary,
      borderSubtle: customDraft.borderSubtle,
    });
    const existsIdx = customThemesState.findIndex((t) => t.id === built.id);
    const nextList =
      existsIdx >= 0
        ? customThemesState.map((t, idx) => (idx === existsIdx ? built : t))
        : [...customThemesState, built];
    setEditingCustomId(built.id);
    await persistCustomThemesAndSelect(
      nextList,
      built.id,
      existsIdx >= 0 ? `Updated theme "${built.name}"` : `Saved theme "${built.name}"`
    );
  };

  const handleEditCustomTheme = (t: ThemeDefinition) => {
    setEditingCustomId(t.isCustom ? t.id : null);
    setCustomDraft({
      name: t.isCustom ? t.name : `${t.name} Remix`,
      description: t.description || 'Custom Studio Theme',
      bgCanvas: t.bgCanvas,
      bgSurface: t.bgSurface,
      bgElevated: t.bgElevated || t.cardHover || t.bgCard,
      accentPrimary: t.accentPrimary,
      accentSecondary: t.accentSecondary || t.accentHover || t.accentPrimary,
      textPrimary: t.textPrimary,
      borderSubtle: t.borderSubtle,
    });
    setShowCustomStudio(true);
  };

  const handleDeleteCustomTheme = async (themeId: string) => {
    const nextList = customThemesState.filter((t) => t.id !== themeId);
    const fallbackId = activeTheme.id === themeId ? THEMES[0].id : activeTheme.id;
    if (editingCustomId === themeId) setEditingCustomId(null);
    await persistCustomThemesAndSelect(nextList, fallbackId, 'Deleted custom theme.');
  };

  const handleExportCustomTheme = (theme: ThemeDefinition) => {
    const payload = JSON.stringify(theme, null, 2);
    const blob = new Blob([payload], { type: 'application/json' });
    const url = URL.createObjectURL(blob);
    const a = document.createElement('a');
    a.href = url;
    a.download = `${theme.id}.lost-heaven-theme.json`;
    a.click();
    URL.revokeObjectURL(url);
  };

  const themeImportInputRef = useRef<HTMLInputElement | null>(null);

  const handleImportCustomThemeFile = async (file: File) => {
    try {
      const text = await file.text();
      const parsed = JSON.parse(text);
      const rawItems = Array.isArray(parsed) ? parsed : [parsed];
      const imported: ThemeDefinition[] = [];
      for (const item of rawItems) {
        const norm = normalizeCustomTheme(item);
        if (norm) {
          const uniqueId = THEMES.some((b) => b.id === norm.id)
            ? `custom-${norm.id}-${Date.now().toString(36).slice(-3)}`
            : norm.id;
          imported.push({ ...norm, id: uniqueId });
        }
      }
      if (imported.length === 0) {
        setThemeStudioNotice('Invalid theme JSON format.');
        return;
      }
      const merged = [...customThemesState];
      for (const imp of imported) {
        const idx = merged.findIndex((m) => m.id === imp.id);
        if (idx >= 0) merged[idx] = imp;
        else merged.push(imp);
      }
      await persistCustomThemesAndSelect(
        merged,
        imported[imported.length - 1].id,
        `Imported ${imported.length} custom theme(s)`
      );
    } catch {
      setThemeStudioNotice('Failed to parse theme JSON file.');
    }
  };

  const handleSaveRenderDefaults = async () => {
    setSavingRenderDefaults(true);
    setRenderDefaultsNotice(null);
    const formattedLufs = `${Number.isInteger(loudnessTarget) ? loudnessTarget : loudnessTarget.toFixed(1)} LUFS`;
    const payload = {
      hardware_acceleration: hardwareMode,
      video_encoder: videoEncoder,
      default_resolution: defaultResolution,
      default_fps: defaultFps,
      loudness_target: formattedLufs,
      audio_normalization_enabled: audioNormalizationEnabled,
      auto_cleanup_days: autoCleanupDays,
    };
    try {
      await Promise.all([
        api.updateSystemSettings(payload),
        api.updateSettings(payload),
      ]);
      window.dispatchEvent(new CustomEvent('lost-heaven:settings-updated', { detail: payload }));
      setRenderDefaultsNotice('Render & audio mastering defaults saved.');
      setTimeout(() => setRenderDefaultsNotice(null), 3000);
    } catch {
      setRenderDefaultsNotice('Failed to save render & audio defaults.');
    } finally {
      setSavingRenderDefaults(false);
    }
  };

  const handleResetDefaults = async () => {
    setResettingDefaults(true);
    try {
      const res = await api.resetSettingsDefaults();
      const s = res?.settings || {};
      const nextThemeId = s.active_theme || 'Hicksfield';
      const defaultTheme = getAllThemes(customThemesState).find((t) => t.id === nextThemeId) || THEMES[0];
      setActiveTheme(defaultTheme);
      applyTheme(defaultTheme.id, customThemesState);
      setHardwareMode(s.hardware_acceleration || 'auto');
      setVideoEncoder(s.video_encoder || 'auto');
      setDefaultResolution(s.default_resolution || '1080x1920');
      setDefaultFps(String(s.default_fps || '30'));
      const parsedResetLt = parseFloat(String(s.loudness_target ?? '-14 LUFS').replace(/[^0-9.-]/g, ''));
      setLoudnessTarget(Number.isFinite(parsedResetLt) ? parsedResetLt : -14.0);
      setAudioNormalizationEnabled(
        typeof s.audio_normalization_enabled === 'boolean' ? s.audio_normalization_enabled : true
      );
      setAutoCleanupDays(typeof s.auto_cleanup_days === 'number' ? s.auto_cleanup_days : 30);
      if (s.whisper_model) setActiveWhisperModel(s.whisper_model);
      if (s.gemini_analysis_mode) setGeminiAnalysisMode(s.gemini_analysis_mode);
      window.dispatchEvent(new CustomEvent('lost-heaven:settings-updated', { detail: s }));
      setRenderDefaultsNotice('Studio defaults restored (API keys & custom themes preserved).');
      setThemeStudioNotice('Restored default Studio theme & preferences.');
      setTimeout(() => {
        setRenderDefaultsNotice(null);
        setThemeStudioNotice(null);
      }, 3500);
      await loadAllDiagnostics();
    } catch {
      setRenderDefaultsNotice('Failed to reset defaults.');
    } finally {
      setResettingDefaults(false);
    }
  };

  const handleSelectWhisperModel = async (modelName: string) => {
    setActiveWhisperModel(modelName);
    try {
      await api.updateSystemSettings({ whisper_model: modelName });
      const updated = await api.getModelsStatus();
      setModelsData(updated);
    } catch (err) {
      console.error('Failed to set active whisper model:', err);
    }
  };

  const handleDownloadModel = async (modelName: string) => {
    setDownloadingModel(modelName);
    try {
      await api.downloadModel(modelName);
      const updated = await api.getModelsStatus();
      setModelsData(updated);
      setActiveWhisperModel(modelName);
      await api.updateSystemSettings({ whisper_model: modelName });
    } catch (err) {
      console.error('Download model failed:', err);
      alert(`Failed to download model ${modelName}`);
    } finally {
      setDownloadingModel(null);
    }
  };

  const handleDeleteModel = async (modelName: string) => {
    if (!window.confirm(`Delete downloaded model "${modelName}" from disk?`)) return;
    setDeletingModel(modelName);
    try {
      await api.deleteModel(modelName);
      const updated = await api.getModelsStatus();
      setModelsData(updated);
      if (activeWhisperModel === modelName) {
        setActiveWhisperModel('base');
        await api.updateSystemSettings({ whisper_model: 'base' });
      }
    } catch (err) {
      console.error('Delete model failed:', err);
    } finally {
      setDeletingModel(null);
    }
  };

  const handleUpdateYtdlp = async () => {
    setUpdatingYtdlp(true);
    setYtdlpMessage(null);
    try {
      const res = await api.updateYtDlp();
      setYtdlpMessage(res.output || (res as any).message || 'yt-dlp is up to date.');
      const m = await api.getMachineSpecs();
      if (m) setSpecs(m);
    } catch (err) {
      setYtdlpMessage('Failed to update yt-dlp package.');
    } finally {
      setUpdatingYtdlp(false);
    }
  };

  const handleToggleGeminiBoost = async (val: boolean) => {
    setGeminiBoost(val);
    const modePatch: Partial<StudioSettings> = {
      gemini_enabled: val,
      gemini_boost_enabled: val,
      ai_mode: val ? 'gemini' : 'offline',
      ai_provider: val ? 'gemini' : 'offline',
    };
    broadcastSettingsUpdate(modePatch);
    try {
      await Promise.all([
        api.updateSystemSettings(modePatch),
        api.updateSettings(modePatch)
      ]);
    } catch (err) {
      console.error('Failed to update gemini boost:', err);
    }
  };

  const persistGeminiModelSelection = async (modelId: string) => {
    const clean = modelId.trim().replace(/^models\//, '');
    if (!clean) return;
    setGeminiModel(clean);
    setGeminiBoost(true);
    setShowCustomModelInput(false);
    try {
      const payload = {
        gemini_model: clean,
        gemini_enabled: true,
        gemini_boost_enabled: true
      };
      await Promise.all([
        api.updateSystemSettings(payload),
        api.updateSettings(payload)
      ]);
      broadcastSettingsUpdate(payload);
      setGeminiNotice(`Active model switched to ${clean}`);
      setTimeout(() => setGeminiNotice(null), 2500);
    } catch (err) {
      console.error('Failed to persist selected Gemini model:', err);
    }
  };

  const handleSaveGeminiKey = async () => {
    setSavingGemini(true);
    setGeminiNotice(null);
    const activeModel = (showCustomModelInput && customModelDraft.trim())
      ? customModelDraft.trim().replace(/^models\//, '')
      : geminiModel;
    try {
      const payload: any = { 
        gemini_model: activeModel, 
        gemini_analysis_mode: geminiAnalysisMode,
        auto_fallback_offline: autoFallbackOffline,
        gemini_enabled: true,
        gemini_boost_enabled: true,
        ai_mode: 'gemini',
        ai_provider: 'gemini'
      };
      if (geminiApiKey.trim()) {
        payload.gemini_api_key = geminiApiKey.trim();
      }
      await Promise.all([
        api.updateSystemSettings(payload),
        api.updateSettings(payload)
      ]);
      setGeminiModel(activeModel);
      setGeminiBoost(true);
      setShowCustomModelInput(false);
      broadcastSettingsUpdate({
        gemini_model: activeModel,
        gemini_enabled: true,
        gemini_boost_enabled: true,
        ai_mode: 'gemini',
        ai_provider: 'gemini',
        ...(geminiApiKey.trim() ? { gemini_configured: true } : {})
      });
      await loadGeminiModels(geminiApiKey.trim() || undefined);
      setGeminiNotice('Gemini configuration successfully saved.');
      setTimeout(() => setGeminiNotice(null), 3000);
    } catch (err) {
      setGeminiNotice('Failed to save Gemini credentials.');
    } finally {
      setSavingGemini(false);
    }
  };

  const handleTestGemini = async () => {
    setTestingGemini(true);
    setGeminiTestError(null);
    try {
      const keyArg = geminiApiKey.trim() || undefined;
      const [res] = await Promise.all([
        api.testGeminiModels(keyArg),
        loadGeminiModels(keyArg)
      ]);
      setGeminiTestResults(res.models);
    } catch (err: any) {
      setGeminiTestError(err?.message || 'Failed to test Gemini models');
    } finally {
      setTestingGemini(false);
    }
  };

  const handleToggleHardwareMode = async (mode: string) => {
    setHardwareMode(mode);
    try {
      await api.updateSystemSettings({ hardware_acceleration: mode });
    } catch (err) {
      console.error('Failed to update hardware mode:', err);
    }
  };

  // Safe checks array (never invent fake hardware specs before API responds)
  const activeChecks = systemCheck?.checks || [];

  const okChecksCount = activeChecks.filter((c: any) => c.ok !== false && c.status !== 'error').length;

  return (
    <div className="flex-1 overflow-y-auto bg-[var(--bg-canvas)] text-[var(--text-primary)] transition-colors">
      {/* Sticky Workstation Header + Quick Actions + 4-Tab Bar */}
      <div className="sticky top-0 z-20 bg-[var(--bg-canvas)]/95 backdrop-blur-md border-b border-[var(--border-subtle)]">
        <div className="max-w-7xl mx-auto px-6 md:px-8 pt-5 pb-3 space-y-4">
          <div className="flex flex-col lg:flex-row lg:items-center justify-between gap-4">
            <div>
              <h1 className="text-2xl md:text-3xl font-extrabold tracking-tight flex items-center gap-3">
                <SettingsIcon className="w-7 h-7 text-[var(--accent-primary)]" />
                <span>Settings &amp; Diagnostics</span>
              </h1>
              <p className="text-xs md:text-sm text-[var(--text-secondary)] mt-1">
                {allThemesList.length} Studio Themes • {hardwareMode === 'cpu' ? 'CPU Mode' : 'Hardware Acceleration'} • Theme:{' '}
                <span className="font-semibold text-[var(--accent-primary)]">{activeTheme.name}</span>
              </p>
            </div>

            <div className="flex flex-wrap items-center gap-2">
              {onOpenSetupWizard && (
                <button
                  onClick={onOpenSetupWizard}
                  className="flex items-center gap-1.5 px-3 py-2 bg-[var(--bg-elevated)] hover:opacity-90 text-xs font-semibold rounded-xl border border-[var(--border-subtle)] transition shadow-sm"
                >
                  <Sparkles className="w-3.5 h-3.5 text-[var(--accent-primary)]" />
                  <span>Re-Scan PC Specs</span>
                </button>
              )}
              <button
                onClick={handleCopyReport}
                className="flex items-center gap-1.5 px-3 py-2 bg-[var(--bg-elevated)] hover:opacity-90 text-xs font-semibold rounded-xl border border-[var(--border-subtle)] transition shadow-sm"
              >
                {copiedReport ? <Check className="w-3.5 h-3.5 text-emerald-500" /> : <Copy className="w-3.5 h-3.5" />}
                <span>{copiedReport ? 'Copied' : 'Copy Diagnostics'}</span>
              </button>
              <button
                onClick={handleResetDefaults}
                disabled={resettingDefaults}
                className="flex items-center gap-1.5 px-3 py-2 bg-[var(--bg-elevated)] hover:opacity-90 text-xs font-semibold rounded-xl border border-[var(--border-subtle)] transition shadow-sm disabled:opacity-50"
                title="Reset render, audio, AI preference, and theme defaults (keeps API keys & custom themes)"
              >
                <RotateCcw className={`w-3.5 h-3.5 ${resettingDefaults ? 'animate-spin' : ''}`} />
                <span>{resettingDefaults ? 'Resetting...' : 'Reset Defaults'}</span>
              </button>
              <button
                onClick={handleRunSystemCheck}
                disabled={checkingSystem}
                className="flex items-center gap-1.5 px-3.5 py-2 bg-[var(--accent-primary)] text-[var(--accent-foreground)] text-xs font-bold rounded-xl shadow-md transition hover:opacity-90 disabled:opacity-50"
              >
                <RefreshCw className={`w-3.5 h-3.5 ${checkingSystem ? 'animate-spin' : ''}`} />
                <span>Check Again</span>
              </button>
            </div>
          </div>

          {/* 4-Tab Workstation Navigation */}
          <div className="flex flex-wrap items-center gap-1.5" role="tablist">
            {[
              { id: 'appearance' as const, label: 'Appearance & Custom Themes', icon: Palette, badge: `${allThemesList.length}` },
              { id: 'ai' as const, label: 'AI Engines & Models', icon: Sparkles, badge: activeWhisperModel },
              { id: 'render' as const, label: 'Default Render & Audio Mastering', icon: Sliders, badge: `${loudnessTarget} LUFS` },
              { id: 'diagnostics' as const, label: 'System Health & Diagnostics', icon: Shield, badge: `${okChecksCount}/${activeChecks.length}` },
            ].map((tab) => {
              const Icon = tab.icon;
              const isActive = activeTab === tab.id;
              return (
                <button
                  key={tab.id}
                  type="button"
                  role="tab"
                  aria-selected={isActive}
                  onClick={() => setActiveTab(tab.id)}
                  className={`flex items-center gap-2 px-3.5 py-2 rounded-xl text-xs font-bold transition-all border ${
                    isActive
                      ? 'bg-[var(--accent-primary)] text-[var(--accent-foreground)] border-[var(--accent-primary)] shadow-md'
                      : 'bg-[var(--bg-surface)] text-[var(--text-secondary)] border-[var(--border-subtle)] hover:text-[var(--text-primary)] hover:bg-[var(--bg-elevated)]'
                  }`}
                >
                  <Icon className="w-3.5 h-3.5" />
                  <span>{tab.label}</span>
                  <span
                    className={`px-1.5 py-0.5 rounded-md text-[10px] font-mono ${
                      isActive ? 'bg-black/20 text-[var(--accent-foreground)]' : 'bg-[var(--bg-elevated)] text-[var(--text-muted)]'
                    }`}
                  >
                    {tab.badge}
                  </span>
                </button>
              );
            })}
          </div>
        </div>
      </div>

      <div className="max-w-7xl mx-auto p-6 md:p-8 space-y-8 animate-fadeIn">

      {/* TAB 4 PART A: SYSTEM CHECK (8 Status Badges) */}
      {activeTab === 'diagnostics' && (
      <div className="space-y-4">
        <div className="flex items-center justify-between">
          <div className="flex items-center gap-2">
            <h2 className="text-sm font-bold tracking-wide uppercase">System Check</h2>
            <span className="px-2 py-0.5 rounded-full text-[10px] font-bold bg-emerald-500/15 text-emerald-400 border border-emerald-500/30">
              {systemCheck?.all_passed ? `${activeChecks.length}/${activeChecks.length} ALL OK` : `${okChecksCount}/${activeChecks.length} PASSED`}
            </span>
          </div>
          <span className="text-[11px] text-[var(--text-secondary)]">
            Real-time validation against video pipeline &amp; GPU runtime
          </span>
        </div>

        <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-4 gap-3">
          {activeChecks.map((check: any) => (
            <div 
              key={check.id}
              className="bg-[var(--bg-surface)] border border-[var(--border-subtle)] rounded-xl p-3 flex items-start gap-3 hover:border-[var(--accent-primary)]/50 transition-colors shadow-sm"
            >
              <div className="p-1.5 rounded-lg bg-emerald-500/15 text-emerald-400 flex-shrink-0 mt-0.5">
                <CheckCircle2 className="w-4 h-4" />
              </div>
              <div className="min-w-0 flex-1">
                <div className="flex items-center justify-between gap-1">
                  <span className="text-xs font-bold truncate">{check.label || check.name}</span>
                  <span className="text-[10px] font-extrabold text-emerald-400">OK</span>
                </div>
                <p className="text-[11px] text-[var(--text-secondary)] truncate mt-0.5">{check.detail}</p>
              </div>
            </div>
          ))}
        </div>
      </div>
      )}

      {/* TAB 1: APPEARANCE & CUSTOM THEMES (18+ Built-In Themes + Live Custom Theme Studio) */}
      {activeTab === 'appearance' && (
      <div className="space-y-6">
        <div className="flex flex-col md:flex-row md:items-center justify-between gap-3">
          <div className="flex items-center gap-2.5">
            <Palette className="w-4 h-4 text-[var(--accent-primary)]" />
            <h2 className="text-sm font-bold tracking-wide uppercase">Studio Theme Library</h2>
            <span className="text-xs text-[var(--text-secondary)]">({allThemesList.length} Themes)</span>
            <span className="px-2 py-0.5 rounded-full text-[11px] bg-[var(--bg-elevated)] border border-[var(--border-subtle)] text-[var(--accent-primary)] font-semibold">
              Active: {activeTheme.name}
            </span>
          </div>

          <div className="flex flex-wrap items-center gap-2">
            <input
              ref={themeImportInputRef}
              type="file"
              accept="application/json,.json"
              className="hidden"
              onChange={(e) => {
                const file = e.target.files?.[0];
                if (file) handleImportCustomThemeFile(file);
                e.currentTarget.value = '';
              }}
            />
            <button
              type="button"
              onClick={() => themeImportInputRef.current?.click()}
              className="flex items-center gap-1.5 px-3 py-1.5 rounded-xl border border-[var(--border-subtle)] bg-[var(--bg-surface)] hover:bg-[var(--bg-elevated)] text-xs font-semibold transition"
            >
              <Upload className="w-3.5 h-3.5" />
              <span>Import .json</span>
            </button>
            <button
              type="button"
              onClick={() => handleExportCustomTheme(activeTheme)}
              className="flex items-center gap-1.5 px-3 py-1.5 rounded-xl border border-[var(--border-subtle)] bg-[var(--bg-surface)] hover:bg-[var(--bg-elevated)] text-xs font-semibold transition"
            >
              <Download className="w-3.5 h-3.5" />
              <span>Export Active</span>
            </button>
            <button
              type="button"
              onClick={() => {
                setEditingCustomId(null);
                setShowCustomStudio((prev) => !prev);
              }}
              className="flex items-center gap-1.5 px-3.5 py-1.5 rounded-xl bg-[var(--accent-primary)] text-[var(--accent-foreground)] text-xs font-bold shadow-sm hover:opacity-90 transition"
            >
              <Plus className="w-3.5 h-3.5" />
              <span>{showCustomStudio ? 'Close Theme Studio' : 'Create Custom Theme'}</span>
            </button>
          </div>
        </div>

        {themeStudioNotice && (
          <div className="px-3.5 py-2 rounded-xl bg-emerald-500/15 border border-emerald-500/30 text-xs font-medium text-emerald-400">
            {themeStudioNotice}
          </div>
        )}

        {/* Interactive Custom Theme Studio Drawer */}
        {showCustomStudio && (
          <div className="bg-[var(--bg-surface)] border border-[var(--accent-primary)]/40 rounded-2xl p-5 space-y-5 shadow-lg">
            <div className="flex flex-col sm:flex-row sm:items-center justify-between gap-2 pb-3 border-b border-[var(--border-subtle)]">
              <div>
                <h3 className="text-sm font-extrabold flex items-center gap-2">
                  <Sparkles className="w-4 h-4 text-[var(--accent-primary)]" />
                  <span>{editingCustomId ? 'Edit Custom Studio Theme' : 'Custom Theme Studio'}</span>
                </h3>
                <p className="text-[11px] text-[var(--text-secondary)] mt-0.5">
                  Craft semantic canvas, elevated surface, dual accents, and text tokens with live WCAG contrast verification.
                </p>
              </div>
              <div className="flex items-center gap-2 text-[11px] font-mono">
                <span
                  className={`px-2 py-0.5 rounded-full border font-bold ${
                    primaryContrastRatio >= 4.5
                      ? 'bg-emerald-500/15 text-emerald-400 border-emerald-500/30'
                      : 'bg-amber-500/15 text-amber-400 border-amber-500/30'
                  }`}
                >
                  Canvas Contrast {primaryContrastRatio}:1 {primaryContrastRatio >= 4.5 ? 'AA' : 'LOW'}
                </span>
                <span
                  className={`px-2 py-0.5 rounded-full border font-bold ${
                    surfaceContrastRatio >= 4.5
                      ? 'bg-emerald-500/15 text-emerald-400 border-emerald-500/30'
                      : 'bg-amber-500/15 text-amber-400 border-amber-500/30'
                  }`}
                >
                  Surface {surfaceContrastRatio}:1
                </span>
              </div>
            </div>

            <div className="grid grid-cols-1 lg:grid-cols-12 gap-5">
              {/* Token Pickers */}
              <div className="lg:col-span-7 space-y-4">
                <div className="grid grid-cols-1 sm:grid-cols-2 gap-3">
                  <div>
                    <label className="block text-[11px] font-semibold text-[var(--text-secondary)] mb-1">Theme Name</label>
                    <input
                      type="text"
                      value={customDraft.name}
                      onChange={(e) => setCustomDraft((prev) => ({ ...prev, name: e.target.value }))}
                      placeholder="e.g. Velvet Amber"
                      className="w-full px-3 py-2 rounded-xl bg-[var(--bg-input)] border border-[var(--border-subtle)] text-xs font-semibold outline-none focus:border-[var(--accent-primary)]"
                    />
                  </div>
                  <div>
                    <label className="block text-[11px] font-semibold text-[var(--text-secondary)] mb-1">Description</label>
                    <input
                      type="text"
                      value={customDraft.description}
                      onChange={(e) => setCustomDraft((prev) => ({ ...prev, description: e.target.value }))}
                      placeholder="Short editorial description"
                      className="w-full px-3 py-2 rounded-xl bg-[var(--bg-input)] border border-[var(--border-subtle)] text-xs outline-none focus:border-[var(--accent-primary)]"
                    />
                  </div>
                </div>

                <div className="grid grid-cols-2 sm:grid-cols-3 md:grid-cols-4 gap-3">
                  {([
                    { key: 'bgCanvas', label: 'Canvas (--bg-canvas)' },
                    { key: 'bgSurface', label: 'Surface (--bg-surface)' },
                    { key: 'bgElevated', label: 'Elevated (--bg-elevated)' },
                    { key: 'accentPrimary', label: 'Primary Accent' },
                    { key: 'accentSecondary', label: 'Secondary Accent' },
                    { key: 'textPrimary', label: 'Primary Text' },
                    { key: 'borderSubtle', label: 'Subtle Border' },
                  ] as const).map((field) => (
                    <div
                      key={field.key}
                      className="p-2.5 rounded-xl bg-[var(--bg-elevated)] border border-[var(--border-subtle)] space-y-1.5"
                    >
                      <div className="text-[10px] font-bold text-[var(--text-secondary)] truncate">{field.label}</div>
                      <div className="flex items-center gap-2">
                        <input
                          type="color"
                          value={customDraft[field.key]}
                          onChange={(e) =>
                            setCustomDraft((prev) => ({ ...prev, [field.key]: e.target.value.toUpperCase() }))
                          }
                          className="w-8 h-7 rounded cursor-pointer border-0 bg-transparent p-0"
                        />
                        <input
                          type="text"
                          value={customDraft[field.key]}
                          onChange={(e) =>
                            setCustomDraft((prev) => ({ ...prev, [field.key]: e.target.value }))
                          }
                          className="w-full px-2 py-1 rounded bg-[var(--bg-input)] border border-[var(--border-subtle)] text-[11px] font-mono outline-none"
                        />
                      </div>
                    </div>
                  ))}
                </div>

                <div className="flex flex-wrap items-center gap-2 pt-1">
                  <button
                    type="button"
                    onClick={handleSaveCustomTheme}
                    className="px-4 py-2 rounded-xl bg-[var(--accent-primary)] text-[var(--accent-foreground)] text-xs font-bold shadow hover:opacity-90 transition"
                  >
                    {editingCustomId ? 'Update Custom Theme' : 'Save & Apply Theme'}
                  </button>
                  <button
                    type="button"
                    onClick={() => {
                      const preview = buildCustomTheme({ id: 'live-preview', ...customDraft });
                      applyTheme(preview.id, [...customThemesState, preview]);
                    }}
                    className="px-3.5 py-2 rounded-xl bg-[var(--bg-elevated)] border border-[var(--border-subtle)] text-xs font-semibold hover:opacity-90 transition"
                  >
                    Live Preview on App
                  </button>
                  <button
                    type="button"
                    onClick={() => {
                      setShowCustomStudio(false);
                      setEditingCustomId(null);
                      applyTheme(activeTheme.id, customThemesState);
                    }}
                    className="px-3 py-2 rounded-xl text-xs font-semibold text-[var(--text-secondary)] hover:text-[var(--text-primary)]"
                  >
                    Cancel
                  </button>
                </div>
              </div>

              {/* Miniature Live Studio Preview Card */}
              <div className="lg:col-span-5">
                <div
                  className="rounded-2xl p-4 h-full flex flex-col justify-between gap-3 transition-colors"
                  style={{
                    backgroundColor: previewDraftTheme.bgCanvas,
                    color: previewDraftTheme.textPrimary,
                    border: `1px solid ${previewDraftTheme.borderSubtle}`,
                  }}
                >
                  <div className="flex items-center justify-between">
                    <span className="text-[11px] font-bold uppercase tracking-wider opacity-80">
                      Live Miniature Studio Preview
                    </span>
                    <span
                      className="px-2 py-0.5 rounded-full text-[10px] font-bold"
                      style={{
                        backgroundColor: previewDraftTheme.accentLight,
                        color: previewDraftTheme.accentPrimary,
                      }}
                    >
                      {previewDraftTheme.isDark ? 'Dark Studio' : 'Light Editorial'}
                    </span>
                  </div>

                  <div
                    className="rounded-xl p-3 space-y-2.5"
                    style={{
                      backgroundColor: previewDraftTheme.bgSurface,
                      border: `1px solid ${previewDraftTheme.borderSubtle}`,
                    }}
                  >
                    <div className="flex items-center justify-between">
                      <span className="text-xs font-extrabold">{previewDraftTheme.name}</span>
                      <span
                        className="w-2.5 h-2.5 rounded-full"
                        style={{ backgroundColor: previewDraftTheme.accentSecondary }}
                      />
                    </div>
                    <div
                      className="rounded-lg p-2.5 text-[11px]"
                      style={{
                        backgroundColor: previewDraftTheme.bgElevated,
                        border: `1px solid ${previewDraftTheme.borderSubtle}`,
                        color: previewDraftTheme.textSecondary,
                      }}
                    >
                      Hook Score 94 • 9:16 CFR Timeline • -14 LUFS Master
                    </div>
                    <div className="flex items-center gap-2 pt-1">
                      <span
                        className="px-3 py-1 rounded-lg text-[11px] font-bold"
                        style={{
                          backgroundColor: previewDraftTheme.accentPrimary,
                          color: previewDraftTheme.accentForeground,
                        }}
                      >
                        Primary CTA
                      </span>
                      <span
                        className="px-3 py-1 rounded-lg text-[11px] font-semibold"
                        style={{
                          backgroundColor: previewDraftTheme.bgElevated,
                          color: previewDraftTheme.accentSecondary,
                          border: `1px solid ${previewDraftTheme.borderSubtle}`,
                        }}
                      >
                        Secondary Pill
                      </span>
                    </div>
                  </div>
                </div>
              </div>
            </div>
          </div>
        )}

        {/* Category Filter Pills */}
        <div className="flex flex-wrap items-center gap-2">
          {THEME_CATEGORIES_META.map((cat) => {
            const count =
              cat.id === 'all'
                ? allThemesList.length
                : allThemesList.filter((t) => getThemeCategory(t) === cat.id).length;
            const activePill = themeCategoryFilter === cat.id;
            return (
              <button
                key={cat.id}
                type="button"
                onClick={() => setThemeCategoryFilter(cat.id)}
                className={`px-3 py-1.5 rounded-full text-xs font-bold border transition-all ${
                  activePill
                    ? 'bg-[var(--accent-primary)] text-[var(--accent-foreground)] border-[var(--accent-primary)] shadow-sm'
                    : 'bg-[var(--bg-surface)] text-[var(--text-secondary)] border-[var(--border-subtle)] hover:text-[var(--text-primary)]'
                }`}
              >
                {cat.label} ({count})
              </button>
            );
          })}
        </div>

        {/* Theme Grid (18+ Built-in + User Custom Themes) */}
        <div className="grid grid-cols-1 sm:grid-cols-2 md:grid-cols-3 lg:grid-cols-4 xl:grid-cols-6 gap-3">
          {filteredThemes.map((t) => {
            const isSelected = activeTheme.id === t.id;
            return (
              <div
                key={t.id}
                onClick={() => handleSelectTheme(t)}
                className={`flex flex-col justify-between text-left p-3 rounded-xl border transition-all relative cursor-pointer group ${
                  isSelected 
                    ? 'border-[var(--accent-primary)] bg-[var(--bg-elevated)] ring-2 ring-[var(--accent-primary)]/40 shadow-lg' 
                    : 'border-[var(--border-subtle)] bg-[var(--bg-surface)] hover:border-[var(--accent-primary)]/50 hover:bg-[var(--bg-elevated)]'
                }`}
              >
                <div>
                  <div className="flex items-center justify-between mb-2.5">
                    <div className="flex items-center gap-1.5">
                      <span 
                        className="w-4 h-4 rounded-full border border-black/20 dark:border-white/20 shadow-sm flex-shrink-0" 
                        style={{ backgroundColor: t.bgCanvas }} 
                        title={`Canvas: ${t.bgCanvas}`}
                      />
                      <span 
                        className="w-4 h-4 rounded-full border border-black/20 dark:border-white/20 shadow-sm flex-shrink-0" 
                        style={{ backgroundColor: t.bgSurface }} 
                        title={`Surface: ${t.bgSurface}`}
                      />
                      <span 
                        className="w-4 h-4 rounded-full shadow-sm flex-shrink-0 ring-1 ring-black/20 dark:ring-white/30" 
                        style={{ backgroundColor: t.accentPrimary }} 
                        title={`Primary Accent: ${t.accentPrimary}`}
                      />
                      {t.accentSecondary && (
                        <span 
                          className="w-3 h-3 rounded-full shadow-sm flex-shrink-0 opacity-90" 
                          style={{ backgroundColor: t.accentSecondary }} 
                          title={`Secondary Accent: ${t.accentSecondary}`}
                        />
                      )}
                    </div>
                    <span className="text-[9px] font-bold uppercase px-1.5 py-0.5 rounded bg-[var(--bg-canvas)] text-[var(--text-muted)] border border-[var(--border-subtle)]">
                      {t.isCustom ? 'Custom' : getThemeCategory(t)}
                    </span>
                  </div>

                  <div className="flex items-center justify-between w-full">
                    <span className="text-xs font-bold truncate">{t.name}</span>
                    {isSelected && (
                      <Check className="w-3.5 h-3.5 text-[var(--accent-primary)] flex-shrink-0" />
                    )}
                  </div>
                  <p className="text-[10px] text-[var(--text-secondary)] truncate mt-0.5">{t.description}</p>
                </div>

                <div
                  className="flex items-center justify-end gap-1 mt-2 pt-2 border-t border-[var(--border-subtle)]/60"
                  onClick={(e) => e.stopPropagation()}
                >
                  <button
                    type="button"
                    onClick={() => handleEditCustomTheme(t)}
                    className="text-[10px] font-semibold px-1.5 py-0.5 rounded bg-[var(--bg-canvas)] hover:text-[var(--accent-primary)] border border-[var(--border-subtle)]"
                    title={t.isCustom ? 'Edit custom theme' : 'Remix into custom theme'}
                  >
                    {t.isCustom ? 'Edit' : 'Remix'}
                  </button>
                  {t.isCustom && (
                    <>
                      <button
                        type="button"
                        onClick={() => handleExportCustomTheme(t)}
                        className="p-1 rounded bg-[var(--bg-canvas)] border border-[var(--border-subtle)] hover:text-[var(--accent-primary)]"
                        title="Export theme JSON"
                      >
                        <Download className="w-3 h-3" />
                      </button>
                      <button
                        type="button"
                        onClick={() => handleDeleteCustomTheme(t.id)}
                        className="p-1 rounded bg-[var(--bg-canvas)] border border-[var(--border-subtle)] text-rose-400 hover:bg-rose-500/15"
                        title="Delete custom theme"
                      >
                        <Trash2 className="w-3 h-3" />
                      </button>
                    </>
                  )}
                </div>
              </div>
            );
          })}
        </div>
      </div>
      )}

      {/* TAB 2 PART A: FASTER-WHISPER MODELS MANAGER */}
      {activeTab === 'ai' && (
      <div className="space-y-4">
        <div className="flex items-center justify-between">
          <div className="flex items-center gap-2">
            <HardDrive className="w-4 h-4 text-[var(--accent-primary)]" />
            <h2 className="text-sm font-bold tracking-wide uppercase">Whisper AI Models</h2>
            {specs?.gpu?.has_cuda && (
              <span className="px-2 py-0.5 rounded-full text-[10px] font-bold bg-emerald-500/15 text-emerald-400 border border-emerald-500/30">
                CUDA Accelerated
              </span>
            )}
          </div>
          <div className="text-xs text-[var(--text-secondary)]">
            Storage used: <span className="font-semibold text-[var(--text-primary)]">{modelsData?.storage?.used_mb ? `${modelsData.storage.used_mb} MB` : '0 MB'}</span>
          </div>
        </div>

        <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-5 gap-3">
          {(modelsData?.models || [
            { id: 'tiny', name: 'Tiny', size_mb: 75, ram_required_gb: 1, is_installed: false, is_recommended: false },
            { id: 'base', name: 'Base', size_mb: 145, ram_required_gb: 1.5, is_installed: true, is_recommended: true },
            { id: 'small', name: 'Small', size_mb: 480, ram_required_gb: 2.5, is_installed: false, is_recommended: false },
            { id: 'medium', name: 'Medium', size_mb: 1500, ram_required_gb: 5, is_installed: false, is_recommended: false },
            { id: 'large-v3-turbo', name: 'Large v3 Turbo', size_mb: 1600, ram_required_gb: 6, is_installed: false, is_recommended: false }
          ]).map((m: any) => {
            const modelKey = m.id || m.name.toLowerCase();
            const isDownloading = downloadingModel === modelKey;
            const isDeleting = deletingModel === modelKey;
            const isInstalled = m.is_installed ?? m.downloaded ?? false;
            const isActive = activeWhisperModel === modelKey;

            return (
              <div 
                key={modelKey}
                className={`bg-[var(--bg-card)] border rounded-xl p-4 flex flex-col justify-between transition-all ${
                  isActive ? 'border-[var(--accent-primary)] ring-1 ring-[var(--accent-primary)]/30' : 'border-[var(--border-subtle)]'
                }`}
              >
                <div>
                  <div className="flex items-center justify-between mb-1">
                    <span className="text-sm font-bold capitalize">{m.name || modelKey}</span>
                    {m.is_recommended && (
                      <span className="text-[9px] font-extrabold uppercase px-1.5 py-0.5 rounded bg-[var(--accent-primary)]/15 text-[var(--accent-primary)] border border-[var(--accent-primary)]/30">
                        RECOMMENDED
                      </span>
                    )}
                  </div>
                  <div className="text-[11px] text-[var(--text-secondary)] space-y-0.5 mb-3 font-mono">
                    <div>~{m.size_mb} MB disk</div>
                    <div>Needs {m.ram_required_gb} GB RAM</div>
                  </div>
                </div>

                <div className="pt-3 border-t border-[var(--border-subtle)] flex items-center justify-between gap-2">
                  {isInstalled ? (
                    <>
                      <button
                        onClick={() => handleSelectWhisperModel(modelKey)}
                        className={`text-[11px] font-semibold px-2 py-1 rounded-lg flex items-center gap-1 transition ${
                          isActive 
                            ? 'bg-emerald-500/15 text-emerald-400 border border-emerald-500/30' 
                            : 'bg-[var(--bg-surface)] text-[var(--text-secondary)] hover:text-[var(--text-primary)] border border-[var(--border-subtle)]'
                        }`}
                      >
                        <CheckCircle2 className="w-3.5 h-3.5" />
                        <span>{isActive ? 'In Use' : 'Select'}</span>
                      </button>
                      <button
                        onClick={() => handleDeleteModel(modelKey)}
                        disabled={isDeleting}
                        title="Delete from disk to save space"
                        className="p-1.5 rounded-lg text-[var(--text-secondary)] hover:text-red-400 hover:bg-red-500/10 transition-colors"
                      >
                        <Trash2 className="w-3.5 h-3.5" />
                      </button>
                    </>
                  ) : (
                    <button
                      onClick={() => handleDownloadModel(modelKey)}
                      disabled={isDownloading}
                      className="w-full py-1.5 px-2 bg-[var(--bg-surface)] hover:opacity-90 text-xs font-semibold rounded-lg border border-[var(--border-subtle)] flex items-center justify-center gap-1.5 transition-colors"
                    >
                      <Download className={`w-3.5 h-3.5 ${isDownloading ? 'animate-bounce' : ''}`} />
                      <span>{isDownloading ? 'Downloading...' : 'Download'}</span>
                    </button>
                  )}
                </div>
              </div>
            );
          })}
        </div>
      </div>
      )}

      {/* TAB 3: DEFAULT RENDER & AUDIO MASTERING */}
      {activeTab === 'render' && (
      <div className="grid grid-cols-1 lg:grid-cols-2 gap-6">
        {/* Hardware Acceleration Mode */}
        <div className="bg-[var(--bg-surface)] border border-[var(--border-subtle)] rounded-2xl p-5 space-y-4 shadow-sm">
          <div className="flex items-center justify-between">
            <div className="flex items-center gap-2">
              <Zap className="w-4 h-4 text-[var(--accent-primary)]" />
              <h2 className="text-sm font-bold uppercase">Graphics Acceleration</h2>
            </div>
            {specs?.gpu?.has_cuda && (
              <span className="px-2 py-0.5 rounded-full text-[10px] font-extrabold bg-emerald-500/15 text-emerald-400 border border-emerald-500/30 flex items-center gap-1">
                <span className="w-1.5 h-1.5 rounded-full bg-emerald-400 animate-pulse" />
                <span>IN USE: {specs.gpu.name}</span>
              </span>
            )}
          </div>

          <div className="space-y-2">
            <label 
              onClick={() => handleToggleHardwareMode('gpu')}
              className={`flex items-start gap-3 p-3 rounded-xl border cursor-pointer transition-colors ${
                hardwareMode === 'gpu' 
                  ? 'border-[var(--accent-primary)] bg-[var(--accent-primary)]/10' 
                  : 'border-[var(--border-subtle)] bg-[var(--bg-elevated)] hover:border-[var(--accent-primary)]/30'
              }`}
            >
              <input 
                type="radio" 
                name="hw_mode" 
                checked={hardwareMode === 'gpu'} 
                onChange={() => handleToggleHardwareMode('gpu')}
                className="mt-0.5 accent-[var(--accent-primary)]" 
              />
              <div>
                <div className="text-xs font-bold">Automatic (Hardware Acceleration)</div>
                <div className="text-[11px] text-[var(--text-secondary)]">
                  Utilizes NVIDIA NVENC & CUDA for high-throughput encoding and VRAM-safe Faster-Whisper.
                </div>
              </div>
            </label>

            <label 
              onClick={() => handleToggleHardwareMode('cpu')}
              className={`flex items-start gap-3 p-3 rounded-xl border cursor-pointer transition-colors ${
                hardwareMode === 'cpu' 
                  ? 'border-[var(--accent-primary)] bg-[var(--accent-primary)]/10' 
                  : 'border-[var(--border-subtle)] bg-[var(--bg-elevated)] hover:border-[var(--accent-primary)]/30'
              }`}
            >
              <input 
                type="radio" 
                name="hw_mode" 
                checked={hardwareMode === 'cpu'} 
                onChange={() => handleToggleHardwareMode('cpu')}
                className="mt-0.5 accent-[var(--accent-primary)]" 
              />
              <div>
                <div className="text-xs font-bold">Processor only (CPU)</div>
                <div className="text-[11px] text-[var(--text-secondary)]">
                  Falls back to libx264 software rendering and multi-threaded CPU inference.
                </div>
              </div>
            </label>
          </div>
        </div>

        {/* Default Codec, FPS, Resolution, Audio Mastering & Cleanup */}
        <div className="bg-[var(--bg-surface)] border border-[var(--border-subtle)] rounded-2xl p-5 space-y-4 shadow-sm">
          <div className="flex items-center justify-between">
            <div className="flex items-center gap-2">
              <Film className="w-4 h-4 text-[var(--accent-primary)]" />
              <h2 className="text-sm font-bold uppercase">Render &amp; Audio Mastering Defaults</h2>
            </div>
            <span className="text-[10px] font-mono px-2 py-0.5 rounded bg-[var(--bg-elevated)] border border-[var(--border-subtle)] text-[var(--text-muted)]">
              {loudnessTarget} LUFS
            </span>
          </div>

          <div className="grid grid-cols-1 sm:grid-cols-2 gap-3">
            <div>
              <label className="block text-[11px] font-semibold text-[var(--text-secondary)] mb-1">
                Default Video Codec
              </label>
              <select
                value={videoEncoder}
                onChange={(e) => setVideoEncoder(e.target.value)}
                className="w-full px-3 py-2 rounded-xl bg-[var(--bg-input)] border border-[var(--border-subtle)] text-xs font-semibold outline-none focus:border-[var(--accent-primary)]"
              >
                <option value="auto">Auto (NVENC GPU if available)</option>
                <option value="h264_nvenc">H.264 NVENC (GPU Fast)</option>
                <option value="hevc_nvenc">HEVC / H.265 NVENC (GPU 4K)</option>
                <option value="libx264">H.264 Software (libx264 Master)</option>
                <option value="libx265">HEVC / H.265 Software (libx265)</option>
                <option value="prores_ks">Apple ProRes 422 LT (.mov)</option>
              </select>
            </div>

            <div>
              <label className="block text-[11px] font-semibold text-[var(--text-secondary)] mb-1">
                Default Canvas Resolution
              </label>
              <select
                value={defaultResolution}
                onChange={(e) => setDefaultResolution(e.target.value)}
                className="w-full px-3 py-2 rounded-xl bg-[var(--bg-input)] border border-[var(--border-subtle)] text-xs font-semibold outline-none focus:border-[var(--accent-primary)]"
              >
                <option value="1080x1920">1080x1920 (9:16 Vertical HD)</option>
                <option value="2160x3840">2160x3840 (9:16 Vertical 4K)</option>
                <option value="1080x1350">1080x1350 (4:5 Portrait Feed)</option>
                <option value="1080x1080">1080x1080 (1:1 Square)</option>
                <option value="1920x1080">1920x1080 (16:9 Landscape HD)</option>
              </select>
            </div>

            <div>
              <label className="block text-[11px] font-semibold text-[var(--text-secondary)] mb-1">
                Target Frame Rate (CFR)
              </label>
              <select
                value={defaultFps}
                onChange={(e) => setDefaultFps(e.target.value)}
                className="w-full px-3 py-2 rounded-xl bg-[var(--bg-input)] border border-[var(--border-subtle)] text-xs font-semibold outline-none focus:border-[var(--accent-primary)]"
              >
                <option value="source">Match Source FPS</option>
                <option value="24">24 fps (Cinema)</option>
                <option value="30">30 fps (Social Standard)</option>
                <option value="60">60 fps (High Motion)</option>
              </select>
            </div>

            <div>
              <label className="block text-[11px] font-semibold text-[var(--text-secondary)] mb-1">
                Temp Proxy Auto-Cleanup
              </label>
              <select
                value={autoCleanupDays}
                onChange={(e) => setAutoCleanupDays(Number(e.target.value))}
                className="w-full px-3 py-2 rounded-xl bg-[var(--bg-input)] border border-[var(--border-subtle)] text-xs font-semibold outline-none focus:border-[var(--accent-primary)]"
              >
                <option value={0}>Keep Forever (Manual Cleanup)</option>
                <option value={7}>Purge Temp Files Older Than 7 Days</option>
                <option value={14}>Purge Temp Files Older Than 14 Days</option>
                <option value={30}>Purge Temp Files Older Than 30 Days</option>
                <option value={90}>Purge Temp Files Older Than 90 Days</option>
              </select>
            </div>
          </div>

          {/* Audio Loudness Mastering */}
          <div className="p-3.5 rounded-xl bg-[var(--bg-elevated)] border border-[var(--border-subtle)] space-y-2.5">
            <div className="flex items-center justify-between">
              <div className="flex items-center gap-2">
                <Volume2 className="w-4 h-4 text-[var(--accent-primary)]" />
                <span className="text-xs font-bold">Broadcast Loudness Normalization</span>
              </div>
              <label className="flex items-center gap-2 text-xs font-semibold cursor-pointer">
                <input
                  type="checkbox"
                  checked={audioNormalizationEnabled}
                  onChange={(e) => setAudioNormalizationEnabled(e.target.checked)}
                  className="accent-[var(--accent-primary)]"
                />
                <span>{audioNormalizationEnabled ? 'Enabled' : 'Bypass'}</span>
              </label>
            </div>
            <div className="space-y-1">
              <div className="flex items-center justify-between text-[11px] text-[var(--text-secondary)]">
                <span>Target Integrated Loudness (Social Standard: -14 LUFS)</span>
                <span className="font-mono font-bold text-[var(--text-primary)]">{loudnessTarget.toFixed(1)} LUFS</span>
              </div>
              <input
                type="range"
                min={-24}
                max={-9}
                step={0.5}
                value={loudnessTarget}
                disabled={!audioNormalizationEnabled}
                onChange={(e) => setLoudnessTarget(Number(e.target.value))}
                className="w-full accent-[var(--accent-primary)] disabled:opacity-50"
              />
            </div>
          </div>

          <div className="flex items-center justify-between gap-3 pt-1">
            <button
              type="button"
              onClick={handleSaveRenderDefaults}
              disabled={savingRenderDefaults}
              className="px-4 py-2 rounded-xl bg-[var(--accent-primary)] text-[var(--accent-foreground)] text-xs font-bold shadow hover:opacity-90 disabled:opacity-50 transition"
            >
              {savingRenderDefaults ? 'Saving...' : 'Save Render & Audio Defaults'}
            </button>
            {renderDefaultsNotice && (
              <span className="text-xs text-emerald-400 font-medium truncate">{renderDefaultsNotice}</span>
            )}
          </div>
        </div>
      </div>
      )}

      {/* TAB 2 PART B: GEMINI MULTIMODAL AI BOOST */}
      {activeTab === 'ai' && (
      <div className="space-y-6">
      {/* SECTION 5: GEMINI MULTIMODAL AI BOOST */}
      <div className="bg-[var(--bg-card)] border border-[var(--border-subtle)] rounded-2xl p-6 space-y-5 shadow-sm">
        <div className="flex flex-col sm:flex-row sm:items-center justify-between gap-3">
          <div className="flex items-center gap-3">
            <div className="p-2.5 rounded-xl bg-[var(--accent-primary)]/15 border border-[var(--accent-primary)]/30 text-[var(--accent-primary)]">
              <Sparkles className="w-6 h-6" />
            </div>
            <div>
              <h2 className="text-base font-bold flex items-center gap-2">
                <span>Gemini Multimodal AI Boost</span>
                <span className={`text-[10px] font-extrabold px-2 py-0.5 rounded-md border ${
                  geminiBoost 
                    ? 'bg-emerald-500/15 text-emerald-400 border-emerald-500/30' 
                    : 'bg-[var(--bg-surface)] text-[var(--text-secondary)] border-[var(--border-subtle)]'
                }`}>
                  {geminiBoost ? 'ENABLED' : 'DISABLED'}
                </span>
              </h2>
              <p className="text-xs text-[var(--text-secondary)]">
                Google Gemini API multimodal viral clip blueprint director.
              </p>
            </div>
          </div>

          <label className="relative inline-flex items-center cursor-pointer">
            <input 
              type="checkbox" 
              checked={geminiBoost} 
              onChange={(e) => handleToggleGeminiBoost(e.target.checked)}
              className="sr-only peer" 
            />
            <div className="w-11 h-6 bg-[var(--border-subtle)] peer-focus:outline-none rounded-full peer peer-checked:after:translate-x-full peer-checked:after:border-white after:content-[''] after:absolute after:top-[2px] after:left-[2px] after:bg-white after:border-gray-300 after:border after:rounded-full after:h-5 after:w-5 after:transition-all peer-checked:bg-[var(--accent-primary)]"></div>
          </label>
        </div>

        {/* Privacy & Architecture Guarantee Card */}
        <div className="p-4 rounded-xl bg-[var(--bg-input)] border border-[var(--border-subtle)] space-y-2 text-xs">
          <div className="flex items-center gap-2 font-semibold">
            <Shield className="w-4 h-4 text-emerald-400" />
            <span>Zero Cloud Rendering & High-Speed Architecture</span>
          </div>
          <p className="text-[var(--text-secondary)] leading-relaxed">
            {geminiBoost ? (
              <>
                <strong className="text-[var(--text-primary)]">Transcript-First High-Speed Mode (Active):</strong> Voice is transcribed 100% locally via faster-whisper. Only timestamped segment text is sent to Gemini — <span className="text-[var(--accent-primary)] font-semibold">zero video bytes touch the cloud</span>, eliminating proxy generation delay and bypasses file upload timeouts. In Multimodal mode, a lightweight 480p proxy MP4 is sent via Google GenAI Files API and deleted immediately afterward.
              </>
            ) : (
              <>
                <strong className="text-[var(--text-primary)]">Gemini AI Boost is Disabled (100% Offline Mode):</strong> Lost Heaven will execute all viral moment detection locally using acoustic excitement peaks and silence segmentation. Zero network calls or cloud services are contacted.
              </>
            )}
          </p>
        </div>

        {/* Gemini API Key & Model Configuration */}
        <div className="grid grid-cols-1 md:grid-cols-3 gap-4 pt-2">
          <div className="md:col-span-2 space-y-1.5">
            <label className="text-xs font-semibold text-[var(--text-secondary)]">Gemini API Key</label>
            <div className="relative">
              <input
                type={showApiKey ? 'text' : 'password'}
                placeholder="Enter AI Studio API Key (AIzaSy...)"
                value={geminiApiKey}
                onChange={(e) => setGeminiApiKey(e.target.value)}
                className="w-full bg-[var(--bg-input)] border border-[var(--border-subtle)] rounded-xl px-3.5 py-2 text-xs text-[var(--text-primary)] placeholder-[var(--text-secondary)] focus:outline-none focus:border-[var(--accent-primary)] font-mono transition-colors"
              />
              <button
                type="button"
                onClick={() => setShowApiKey(!showApiKey)}
                className="absolute right-3 top-1/2 -translate-y-1/2 text-[var(--text-secondary)] hover:text-[var(--text-primary)]"
              >
                {showApiKey ? <EyeOff className="w-4 h-4" /> : <Eye className="w-4 h-4" />}
              </button>
            </div>
          </div>

          <div className="space-y-1.5">
            <div className="flex items-center justify-between">
              <label className="text-xs font-semibold text-[var(--text-secondary)]">
                Primary Cascade Model
                {discoveredModelCount > 0 && (
                  <span className="ml-1.5 text-[10px] font-mono text-emerald-400">
                    ({discoveredModelCount} live)
                  </span>
                )}
              </label>
              <button
                type="button"
                onClick={() => loadGeminiModels(geminiApiKey.trim() || undefined)}
                disabled={loadingGeminiModels}
                title="Refresh available Gemini models from API key"
                className="text-[10px] text-[var(--accent-primary)] hover:underline flex items-center gap-1 disabled:opacity-50"
              >
                <RefreshCw className={`w-2.5 h-2.5 ${loadingGeminiModels ? 'animate-spin' : ''}`} />
                <span>{loadingGeminiModels ? 'Loading...' : 'Refresh'}</span>
              </button>
            </div>
            <select
              value={showCustomModelInput ? '__custom__' : geminiModel}
              onChange={(e) => {
                const val = e.target.value;
                if (val === '__custom__') {
                  setCustomModelDraft(geminiModel);
                  setShowCustomModelInput(true);
                } else {
                  persistGeminiModelSelection(val);
                }
              }}
              className="w-full bg-[var(--bg-input)] border border-[var(--border-subtle)] rounded-xl px-3 py-2 text-xs text-[var(--text-primary)] focus:outline-none focus:border-[var(--accent-primary)] transition-colors"
            >
              {(availableGeminiModels.length > 0
                ? availableGeminiModels
                : [
                    { id: 'gemini-3.5-flash-lite', display_name: 'gemini-3.5-flash-lite (Fastest / Recommended)' },
                    { id: 'gemini-3.8-flash', display_name: 'gemini-3.8-flash (Balanced Next-Gen)' },
                    { id: 'gemini-4-hydrogen', display_name: 'gemini-4-hydrogen (Maximum Reasoning)' },
                    { id: 'gemini-3.6-flash', display_name: 'gemini-3.6-flash (Multimodal)' },
                    { id: 'gemini-4-carbon', display_name: 'gemini-4-carbon' },
                  ]
              ).map((m) => (
                <option key={m.id} value={m.id}>
                  {m.display_name && m.display_name !== m.id && !m.display_name.includes(m.id)
                    ? `${m.id} — ${m.display_name}`
                    : (m.display_name || m.id)}
                </option>
              ))}
              {geminiModel &&
                !availableGeminiModels.some((m) => m.id === geminiModel) &&
                !['gemini-3.5-flash-lite', 'gemini-3.8-flash', 'gemini-4-hydrogen', 'gemini-3.6-flash', 'gemini-4-carbon'].includes(geminiModel) && (
                  <option value={geminiModel}>{geminiModel} (Custom)</option>
                )}
              <option value="__custom__">+ Custom Model ID...</option>
            </select>

            {showCustomModelInput && (
              <div className="flex items-center gap-1.5 pt-1">
                <input
                  type="text"
                  placeholder="e.g. gemini-4-neon, gemma-3-27b-it"
                  value={customModelDraft}
                  onChange={(e) => setCustomModelDraft(e.target.value)}
                  onKeyDown={(e) => {
                    if (e.key === 'Enter' && customModelDraft.trim()) {
                      e.preventDefault();
                      persistGeminiModelSelection(customModelDraft);
                    }
                  }}
                  className="flex-1 bg-[var(--bg-input)] border border-[var(--accent-primary)] rounded-lg px-2.5 py-1.5 text-xs text-[var(--text-primary)] font-mono focus:outline-none"
                />
                <button
                  type="button"
                  onClick={() => persistGeminiModelSelection(customModelDraft)}
                  disabled={!customModelDraft.trim()}
                  className="px-2.5 py-1.5 bg-[var(--accent-primary)] text-[var(--accent-foreground)] text-xs font-bold rounded-lg hover:opacity-90 disabled:opacity-40"
                >
                  Apply
                </button>
              </div>
            )}
          </div>
        </div>

        {/* Analysis Mode & Auto-Fallback Toggles */}
        <div className="grid grid-cols-1 md:grid-cols-2 gap-4 pt-1">
          {/* Analysis Mode Toggle */}
          <div className="p-3.5 rounded-xl bg-[var(--bg-input)] border border-[var(--border-subtle)] space-y-2">
            <label className="text-xs font-semibold text-[var(--text-primary)] flex items-center justify-between">
              <span>Analysis Strategy</span>
              <span className="text-[10px] font-mono text-[var(--accent-primary)] uppercase">
                {geminiAnalysisMode === 'transcript' ? 'Fastest' : 'Proxy Upload'}
              </span>
            </label>
            <div className="grid grid-cols-2 gap-2 text-xs">
              <button
                type="button"
                onClick={() => setGeminiAnalysisMode('transcript')}
                className={`px-3 py-2 rounded-lg border text-left transition ${
                  geminiAnalysisMode === 'transcript'
                    ? 'bg-[var(--accent-primary)]/15 border-[var(--accent-primary)] text-[var(--text-primary)] font-bold'
                    : 'bg-[var(--bg-card)] border-[var(--border-subtle)] text-[var(--text-secondary)] hover:text-[var(--text-primary)]'
                }`}
              >
                <div className="font-semibold">Transcript-First</div>
                <div className="text-[10px] opacity-75">No proxy, instant inference</div>
              </button>
              <button
                type="button"
                onClick={() => setGeminiAnalysisMode('multimodal')}
                className={`px-3 py-2 rounded-lg border text-left transition ${
                  geminiAnalysisMode === 'multimodal'
                    ? 'bg-[var(--accent-primary)]/15 border-[var(--accent-primary)] text-[var(--text-primary)] font-bold'
                    : 'bg-[var(--bg-card)] border-[var(--border-subtle)] text-[var(--text-secondary)] hover:text-[var(--text-primary)]'
                }`}
              >
                <div className="font-semibold">Multimodal Proxy</div>
                <div className="text-[10px] opacity-75">Encodes 480p proxy MP4</div>
              </button>
            </div>
          </div>

          {/* Auto-Fallback to Offline Mode Toggle */}
          <div className="p-3.5 rounded-xl bg-[var(--bg-input)] border border-[var(--border-subtle)] flex items-center justify-between gap-3">
            <div className="space-y-0.5">
              <div className="text-xs font-semibold text-[var(--text-primary)]">
                Auto-Fallback to Local Offline Engine
              </div>
              <p className="text-[11px] text-[var(--text-secondary)] leading-relaxed">
                If all Gemini models in the dynamic cascade return high-demand or unavailable errors, seamlessly finish Stage 4 locally without failing.
              </p>
            </div>
            <label className="relative inline-flex items-center cursor-pointer flex-shrink-0">
              <input 
                type="checkbox" 
                checked={autoFallbackOffline} 
                onChange={(e) => setAutoFallbackOffline(e.target.checked)}
                className="sr-only peer" 
              />
              <div className="w-11 h-6 bg-[var(--border-subtle)] peer-focus:outline-none rounded-full peer peer-checked:after:translate-x-full peer-checked:after:border-white after:content-[''] after:absolute after:top-[2px] after:left-[2px] after:bg-white after:border-gray-300 after:border after:rounded-full after:h-5 after:w-5 after:transition-all peer-checked:bg-emerald-500"></div>
            </label>
          </div>
        </div>

        {/* Live Test Models Diagnostic Box */}
        {geminiTestResults && (
          <div className="p-4 rounded-xl bg-[var(--bg-input)] border border-[var(--border-subtle)] space-y-3 font-mono text-xs animate-fadeIn">
            <div className="flex items-center justify-between font-sans">
              <span className="font-bold text-xs text-[var(--text-primary)] flex items-center gap-2">
                <Sparkles className="w-3.5 h-3.5 text-[var(--accent-primary)]" />
                Live Gemini Cascade Diagnostics ({geminiTestResults.length} Models)
              </span>
              <span className="text-[10px] text-[var(--text-secondary)]">Instant 1-token probe</span>
            </div>

            <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-4 gap-2.5">
              {geminiTestResults.map((r, i) => {
                const isOk = r.status === 'OK';
                const isOverloaded = r.status.includes('503');
                return (
                  <div
                    key={r.model || i}
                    className={`p-3 rounded-xl border flex flex-col justify-between gap-2 ${
                      isOk
                        ? 'bg-emerald-500/10 border-emerald-500/30 text-emerald-400'
                        : isOverloaded
                        ? 'bg-amber-500/10 border-amber-500/30 text-amber-400'
                        : 'bg-red-500/10 border-red-500/30 text-red-400'
                    }`}
                  >
                    <div>
                      <div className="font-bold text-[11px] truncate">{r.model}</div>
                      <div className="text-[10px] opacity-75 truncate">{r.detail}</div>
                    </div>
                    <div className="flex items-center justify-between text-[10px] pt-1 border-t border-[var(--border-subtle)] font-bold tabular-nums">
                      <span>{r.status}</span>
                      {r.latency_ms > 0 && <span>{r.latency_ms}ms</span>}
                    </div>
                  </div>
                );
              })}
            </div>
          </div>
        )}

        {geminiTestError && (
          <div className="p-3 rounded-xl bg-red-500/10 border border-red-500/30 text-red-400 text-xs flex items-center gap-2">
            <AlertTriangle className="w-4 h-4 flex-shrink-0" />
            <span>{geminiTestError}</span>
          </div>
        )}

        <div className="flex flex-col sm:flex-row items-stretch sm:items-center justify-between gap-3 pt-2">
          <div className="flex items-center gap-2">
            <button
              type="button"
              onClick={handleTestGemini}
              disabled={testingGemini}
              className="px-4 py-2 bg-[var(--bg-card)] border border-[var(--border-subtle)] hover:bg-[var(--bg-input)] text-xs font-semibold text-[var(--text-primary)] rounded-xl transition flex items-center gap-2 disabled:opacity-50"
            >
              <RefreshCw className={`w-3.5 h-3.5 text-[var(--accent-primary)] ${testingGemini ? 'animate-spin' : ''}`} />
              <span>{testingGemini ? 'Testing Available Models...' : 'Test Connection & Models'}</span>
            </button>
            {geminiNotice && (
              <span className="text-xs text-emerald-400 font-medium">{geminiNotice}</span>
            )}
          </div>

          <button
            onClick={handleSaveGeminiKey}
            disabled={savingGemini}
            className="px-5 py-2 bg-[var(--accent-primary)] text-[var(--accent-foreground)] text-xs font-bold rounded-xl shadow-md transition hover:opacity-90 disabled:opacity-50"
          >
            {savingGemini ? 'Saving...' : 'Save Gemini Configuration'}
          </button>
        </div>
      </div>

      {/* SECTION 5B: WATCH FOLDER & B-ROLL INTEGRATIONS */}
      <div className="bg-[var(--bg-card)] border border-[var(--border-subtle)] rounded-2xl p-5 space-y-4 shadow-sm">
        <div className="flex items-center justify-between">
          <div>
            <h2 className="text-sm font-bold tracking-wide uppercase">
              Batch Watch Folder &amp; B-Roll Integrations
            </h2>
            <p className="text-xs text-[var(--text-secondary)]">
              Auto-ingest new footage placed in a local watch folder and optionally query Pexels for B-Roll cutaways.
            </p>
          </div>
        </div>

        <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
          <div className="space-y-1.5">
            <label className="text-xs font-semibold text-[var(--text-secondary)]">
              Local Watch Folder Path
            </label>
            <input
              type="text"
              placeholder="e.g. C:\Videos\Incoming"
              value={watchFolderPath}
              onChange={(e) => setWatchFolderPath(e.target.value)}
              className="w-full bg-[var(--bg-input)] border border-[var(--border-subtle)] rounded-xl px-3.5 py-2 text-xs text-[var(--text-primary)] font-mono outline-none focus:border-[var(--accent-primary)]"
            />
          </div>

          <div className="space-y-1.5">
            <label className="text-xs font-semibold text-[var(--text-secondary)]">
              Pexels B-Roll API Key (Optional)
            </label>
            <input
              type="password"
              placeholder="Optional Pexels API Key (uses local storage/broll/ if empty)"
              value={pexelsApiKey}
              onChange={(e) => setPexelsApiKey(e.target.value)}
              className="w-full bg-[var(--bg-input)] border border-[var(--border-subtle)] rounded-xl px-3.5 py-2 text-xs text-[var(--text-primary)] font-mono outline-none focus:border-[var(--accent-primary)]"
            />
          </div>
        </div>

        <div className="flex flex-wrap items-center justify-between gap-3 pt-1">
          <label className="flex items-center gap-2 text-xs font-semibold text-[var(--text-primary)] cursor-pointer">
            <input
              type="checkbox"
              checked={watchFolderEnabled}
              onChange={(e) => setWatchFolderEnabled(e.target.checked)}
              className="rounded border-[var(--border-subtle)]"
            />
            <span>Enable Watch Folder Auto-Queue</span>
          </label>

          <div className="flex items-center gap-2">
            {integrationNotice && (
              <span className="text-xs text-emerald-400 font-medium">{integrationNotice}</span>
            )}
            <button
              type="button"
              disabled={scanningWatch || !watchFolderPath.trim()}
              onClick={async () => {
                try {
                  setScanningWatch(true);
                  const res = await api.scanWatchFolder(watchFolderPath.trim());
                  setIntegrationNotice(
                    res.error
                      ? `Watch error: ${res.error}`
                      : `Scanned watch folder: enqueued ${res.scanned} video(s).`
                  );
                } finally {
                  setScanningWatch(false);
                }
              }}
              className="px-3.5 py-2 bg-[var(--bg-input)] border border-[var(--border-subtle)] hover:border-[var(--accent-primary)]/50 text-xs font-semibold rounded-xl transition disabled:opacity-50"
            >
              {scanningWatch ? 'Scanning...' : 'Scan Watch Folder Now'}
            </button>
            <button
              type="button"
              onClick={async () => {
                await api.updateSystemSettings({
                  pexels_api_key: pexelsApiKey.trim(),
                  watch_folder_path: watchFolderPath.trim(),
                  watch_folder_enabled: watchFolderEnabled
                });
                setIntegrationNotice('Saved Watch Folder & B-Roll settings.');
                setTimeout(() => setIntegrationNotice(null), 3000);
              }}
              className="px-4 py-2 bg-[var(--accent-primary)] text-[var(--accent-foreground)] text-xs font-bold rounded-xl shadow-sm hover:opacity-90 transition"
            >
              Save Integrations
            </button>
          </div>

          {/* Local Export & Style Intelligence CSV Analytics Import */}
          <div className="pt-3 border-t border-[var(--border-subtle)] flex flex-wrap items-center justify-between gap-2">
            <div>
              <div className="text-xs font-bold text-[var(--text-primary)]">
                Style Intelligence Ledger (YouTube Studio CSV)
              </div>
              <div className="text-[11px] text-[var(--text-secondary)]">
                Import Shorts Analytics CSVs to learn your top-performing aspect, style, and duration formula.
              </div>
              {csvImportSummary && (
                <div className="text-[11px] font-mono text-[var(--accent-primary)] mt-1">
                  {csvImportSummary}
                </div>
              )}
            </div>
            <label className="px-3.5 py-2 rounded-xl bg-[var(--bg-input)] border border-[var(--border-subtle)] hover:border-[var(--accent-primary)] text-xs font-semibold text-[var(--text-primary)] cursor-pointer transition shrink-0">
              {importingCsv ? 'Importing CSV...' : 'Import Analytics CSV'}
              <input
                type="file"
                accept=".csv,text/csv"
                className="hidden"
                disabled={importingCsv}
                onChange={async (e) => {
                  const file = e.target.files?.[0];
                  if (!file) return;
                  try {
                    setImportingCsv(true);
                    const text = await file.text();
                    const res = await api.importAnalyticsCsv(text);
                    setCsvImportSummary(
                      `Imported ${res.imported_rows} row(s) (${res.matched_clips} matched) · Formula: ${res.formula?.headline || 'Updated'}`
                    );
                  } catch (err: any) {
                    setCsvImportSummary(err?.message || 'CSV import failed.');
                  } finally {
                    setImportingCsv(false);
                    e.target.value = '';
                  }
                }}
              />
            </label>
          </div>
        </div>
      </div>
      </div>
      )}

      {/* TAB 4 PART B: YOUTUBE ENGINE, MACHINE SPECS & BUNDLED BINARIES */}
      {activeTab === 'diagnostics' && (
      <div className="space-y-6">
        {/* YouTube Import Engine (yt-dlp) */}
        <div className="bg-[var(--bg-surface)] border border-[var(--border-subtle)] rounded-2xl p-5 space-y-3 shadow-sm">
          <div className="flex items-center justify-between">
            <div className="flex items-center gap-2">
              <Terminal className="w-4 h-4 text-[var(--accent-primary)]" />
              <h2 className="text-sm font-bold uppercase">YouTube Import Engine (yt-dlp)</h2>
            </div>
            <span className="text-xs text-[var(--text-secondary)] font-mono">
              v{specs?.binaries?.ytdlp?.version || '2026.11.23'}
            </span>
          </div>

          <p className="text-xs text-[var(--text-secondary)]">
            Extracts high-resolution streams from YouTube URLs for automatic multimodal viral moment clipping.
          </p>

          <div className="flex items-center gap-3">
            <button
              onClick={handleUpdateYtdlp}
              disabled={updatingYtdlp}
              className="px-4 py-2 bg-[var(--bg-elevated)] hover:opacity-90 text-xs font-semibold rounded-xl border border-[var(--border-subtle)] flex items-center gap-2 transition-colors"
            >
              <RefreshCw className={`w-3.5 h-3.5 ${updatingYtdlp ? 'animate-spin text-[var(--accent-primary)]' : ''}`} />
              <span>{updatingYtdlp ? 'Updating yt-dlp...' : 'Update yt-dlp'}</span>
            </button>
            {ytdlpMessage && (
              <span className="text-xs text-emerald-400 truncate">{ytdlpMessage}</span>
            )}
          </div>
        </div>

      <div className="space-y-4">
        <h2 className="text-sm font-bold tracking-wide uppercase">Machine Specs &amp; Binaries</h2>
        
        <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
          {/* Host Hardware Card */}
          <div className="bg-[var(--bg-card)] border border-[var(--border-subtle)] rounded-2xl p-5 space-y-3 font-mono text-xs shadow-sm">
            <div className="text-xs font-bold font-sans uppercase flex items-center gap-2">
              <Cpu className="w-4 h-4 text-[var(--accent-primary)]" />
              <span>Host Compute Specs</span>
            </div>
            <div className="space-y-1.5 text-[var(--text-secondary)]">
              <div className="flex justify-between py-1 border-b border-[var(--border-subtle)]">
                <span>CPU:</span>
                <span className="text-[var(--text-primary)] text-right font-medium truncate max-w-[250px]">
                  {specs?.cpu?.model || 'Detecting CPU...'}
                </span>
              </div>
              <div className="flex justify-between py-1 border-b border-[var(--border-subtle)] tabular-nums">
                <span>Cores:</span>
                <span className="text-[var(--text-primary)]">
                  {specs?.cpu?.cores_logical ? `${specs.cpu.cores_logical} logical cores` : 'Detecting...'}
                </span>
              </div>
              <div className="flex justify-between py-1 border-b border-[var(--border-subtle)] tabular-nums">
                <span>RAM:</span>
                <span className="text-[var(--text-primary)]">
                  {specs?.ram ? `${specs.ram.available_gb} GB free / ${specs.ram.total_gb} GB` : 'Detecting...'}
                </span>
              </div>
              <div className="flex justify-between py-1 border-b border-[var(--border-subtle)]">
                <span>GPU:</span>
                <span className="text-emerald-400 font-semibold text-right truncate max-w-[250px]">
                  {specs?.gpu?.name || 'None'}
                </span>
              </div>
              <div className="flex justify-between py-1 border-b border-[var(--border-subtle)] tabular-nums">
                <span>VRAM:</span>
                <span className="text-[var(--text-primary)]">
                  {specs?.gpu ? `${specs.gpu.vram_gb} GB (CUDA: ${specs.gpu.has_cuda ? 'Yes' : 'No'})` : 'None'}
                </span>
              </div>
              <div className="flex justify-between py-1 tabular-nums">
                <span>Free Disk:</span>
                <span className="text-[var(--text-primary)]">
                  {specs?.storage ? `${specs.storage.free_gb} GB free` : 'Detecting...'}
                </span>
              </div>
            </div>
          </div>

          {/* Bundled Binaries Table Card */}
          <div className="bg-[var(--bg-card)] border border-[var(--border-subtle)] rounded-2xl p-5 space-y-3 font-mono text-xs shadow-sm">
            <div className="text-xs font-bold font-sans uppercase flex items-center gap-2">
              <Terminal className="w-4 h-4 text-[var(--accent-primary)]" />
              <span>Active Binaries & Packages</span>
            </div>
            <div className="space-y-1.5 text-[var(--text-secondary)]">
              <div className="flex justify-between py-1 border-b border-[var(--border-subtle)]">
                <span>ffmpeg:</span>
                <span className="text-[var(--text-primary)] text-right truncate max-w-[280px]">
                  {specs?.binaries?.ffmpeg?.version || 'FFmpeg'}
                </span>
              </div>
              <div className="flex justify-between py-1 border-b border-[var(--border-subtle)]">
                <span>ffprobe:</span>
                <span className="text-[var(--text-primary)] text-right truncate max-w-[280px]">
                  {specs?.binaries?.ffprobe?.version || 'FFprobe'}
                </span>
              </div>
              <div className="flex justify-between py-1 border-b border-[var(--border-subtle)]">
                <span>faster-whisper:</span>
                <span className="text-[var(--text-primary)]">
                  v{specs?.binaries?.faster_whisper?.version || '1.2.1'} (int8_float16)
                </span>
              </div>
              <div className="flex justify-between py-1 border-b border-[var(--border-subtle)]">
                <span>yt-dlp:</span>
                <span className="text-[var(--text-primary)]">
                  v{specs?.binaries?.ytdlp?.version || 'Installed'}
                </span>
              </div>
              <div className="flex justify-between py-1">
                <span>google-genai:</span>
                <span className="text-[var(--accent-primary)] font-semibold">
                  Google Gemini SDK (Files API proxy)
                </span>
              </div>
            </div>
          </div>
        </div>
      </div>
      </div>
      )}
      </div>
    </div>
  );
};
