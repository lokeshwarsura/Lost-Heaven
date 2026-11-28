export interface ThemeDefinition {
  id: string;
  name: string;
  description: string;
  isDark: boolean;
  category?: 'dark' | 'light' | 'neon' | 'custom';
  isCustom?: boolean;
  bgCanvas: string;
  bgSurface: string;
  bgElevated?: string;
  bgCard: string;
  bgInput: string;
  borderSubtle: string;
  textPrimary: string;
  textSecondary: string;
  textMuted?: string;
  accentPrimary: string;
  accentSecondary?: string;
  accentForeground: string;

  // Legacy mappings for compatibility
  bg: string;
  card: string;
  cardHover: string;
  border: string;
  accent: string;
  accentHover: string;
  accentLight: string;
  text: string;
  muted: string;
  previewColor: string;
}

export const THEMES: ThemeDefinition[] = [
  {
    id: 'Hicksfield',
    name: 'Hicksfield',
    description: 'Classic dark obsidian with warm amber glow',
    isDark: true,
    bgCanvas: '#0D0D0D',
    bgSurface: '#121212',
    bgCard: '#161616',
    bgInput: '#0D0D0D',
    borderSubtle: '#262626',
    textPrimary: '#F4F4F5',
    textSecondary: '#71717A',
    accentPrimary: '#F59E0B',
    accentForeground: '#000000',
    bg: '#0D0D0D',
    card: '#161616',
    cardHover: '#1F1F1F',
    border: '#262626',
    accent: '#F59E0B',
    accentHover: '#D97706',
    accentLight: 'rgba(245, 158, 11, 0.15)',
    text: '#F4F4F5',
    muted: '#71717A',
    previewColor: '#F59E0B'
  },
  {
    id: 'Hicksfield 2',
    name: 'Hicksfield 2',
    description: 'Ultra pitch-black with radiant gold accent',
    isDark: true,
    bgCanvas: '#080808',
    bgSurface: '#0E0E0E',
    bgCard: '#141414',
    bgInput: '#080808',
    borderSubtle: '#222222',
    textPrimary: '#FAFAFA',
    textSecondary: '#71717A',
    accentPrimary: '#EAB308',
    accentForeground: '#000000',
    bg: '#080808',
    card: '#141414',
    cardHover: '#1A1A1A',
    border: '#222222',
    accent: '#EAB308',
    accentHover: '#CA8A04',
    accentLight: 'rgba(234, 179, 8, 0.15)',
    text: '#FAFAFA',
    muted: '#71717A',
    previewColor: '#EAB308'
  },
  {
    id: 'Emerald',
    name: 'Emerald',
    description: 'Deep jewel green with vibrant emerald shine',
    isDark: true,
    bgCanvas: '#06120E',
    bgSurface: '#0A1B14',
    bgCard: '#0E241B',
    bgInput: '#06120E',
    borderSubtle: '#183D2F',
    textPrimary: '#ECFDF5',
    textSecondary: '#6EE7B7',
    accentPrimary: '#10B981',
    accentForeground: '#000000',
    bg: '#06120E',
    card: '#0E241B',
    cardHover: '#143326',
    border: '#183D2F',
    accent: '#10B981',
    accentHover: '#059669',
    accentLight: 'rgba(16, 185, 129, 0.15)',
    text: '#ECFDF5',
    muted: '#6EE7B7',
    previewColor: '#10B981'
  },
  {
    id: 'Forest',
    name: 'Forest',
    description: 'Moody woodland shadow with pine green highlights',
    isDark: true,
    bgCanvas: '#08100A',
    bgSurface: '#0C1810',
    bgCard: '#122217',
    bgInput: '#08100A',
    borderSubtle: '#1C3825',
    textPrimary: '#F0FDF4',
    textSecondary: '#86EFAC',
    accentPrimary: '#22C55E',
    accentForeground: '#000000',
    bg: '#08100A',
    card: '#122217',
    cardHover: '#183020',
    border: '#1C3825',
    accent: '#22C55E',
    accentHover: '#16A34A',
    accentLight: 'rgba(34, 197, 94, 0.15)',
    text: '#F0FDF4',
    muted: '#86EFAC',
    previewColor: '#22C55E'
  },
  {
    id: 'Royal Purple',
    name: 'Royal Purple',
    description: 'Regal midnight plum with electric amethyst glow',
    isDark: true,
    bgCanvas: '#0C0717',
    bgSurface: '#120B22',
    bgCard: '#190F2E',
    bgInput: '#0C0717',
    borderSubtle: '#2B1A4E',
    textPrimary: '#FAF5FF',
    textSecondary: '#C084FC',
    accentPrimary: '#A855F7',
    accentForeground: '#000000',
    bg: '#0C0717',
    card: '#190F2E',
    cardHover: '#231540',
    border: '#2B1A4E',
    accent: '#A855F7',
    accentHover: '#9333EA',
    accentLight: 'rgba(168, 85, 247, 0.15)',
    text: '#FAF5FF',
    muted: '#C084FC',
    previewColor: '#A855F7'
  },
  {
    id: 'Midnight Blue',
    name: 'Midnight Blue',
    description: 'Deep ocean abyss with luminous sapphire blue',
    isDark: true,
    bgCanvas: '#070B18',
    bgSurface: '#0C1326',
    bgCard: '#121B35',
    bgInput: '#070B18',
    borderSubtle: '#1A284D',
    textPrimary: '#EFF6FF',
    textSecondary: '#93C5FD',
    accentPrimary: '#3B82F6',
    accentForeground: '#FFFFFF',
    bg: '#070B18',
    card: '#121B35',
    cardHover: '#19264A',
    border: '#1A284D',
    accent: '#3B82F6',
    accentHover: '#2563EB',
    accentLight: 'rgba(59, 130, 246, 0.15)',
    text: '#EFF6FF',
    muted: '#93C5FD',
    previewColor: '#3B82F6'
  },
  {
    id: 'Carbon',
    name: 'Carbon',
    description: 'Stealth industrial monochrome with titanium silver',
    isDark: true,
    bgCanvas: '#101010',
    bgSurface: '#161616',
    bgCard: '#1F1F1F',
    bgInput: '#101010',
    borderSubtle: '#2E2E2E',
    textPrimary: '#FFFFFF',
    textSecondary: '#A1A1AA',
    accentPrimary: '#E4E4E7',
    accentForeground: '#09090B',
    bg: '#101010',
    card: '#1F1F1F',
    cardHover: '#282828',
    border: '#2E2E2E',
    accent: '#E4E4E7',
    accentHover: '#D4D4D8',
    accentLight: 'rgba(228, 228, 231, 0.15)',
    text: '#FFFFFF',
    muted: '#A1A1AA',
    previewColor: '#E4E4E7'
  },
  {
    id: 'Modern Light',
    name: 'Modern Light',
    description: 'Crisp gallery white with sharp obsidian contrast',
    isDark: false,
    bgCanvas: '#F8FAFC',
    bgSurface: '#FFFFFF',
    bgCard: '#FFFFFF',
    bgInput: '#F1F5F9',
    borderSubtle: '#E2E8F0',
    textPrimary: '#0F172A',
    textSecondary: '#64748B',
    accentPrimary: '#F59E0B',
    accentForeground: '#000000',
    bg: '#F8FAFC',
    card: '#FFFFFF',
    cardHover: '#F1F5F9',
    border: '#E2E8F0',
    accent: '#F59E0B',
    accentHover: '#D97706',
    accentLight: 'rgba(245, 158, 11, 0.15)',
    text: '#0F172A',
    muted: '#64748B',
    previewColor: '#F59E0B'
  },
  {
    id: 'Higgsfield',
    name: 'Higgsfield',
    description: 'Deep cosmic void with cyberpunk electric lime',
    isDark: true,
    bgCanvas: '#050505',
    bgSurface: '#0B0B0B',
    bgCard: '#121212',
    bgInput: '#050505',
    borderSubtle: '#222222',
    textPrimary: '#F5F5F5',
    textSecondary: '#737373',
    accentPrimary: '#C6F32F',
    accentForeground: '#000000',
    bg: '#050505',
    card: '#121212',
    cardHover: '#1B1B1B',
    border: '#222222',
    accent: '#C6F32F',
    accentHover: '#B0DF20',
    accentLight: 'rgba(198, 243, 47, 0.15)',
    text: '#F5F5F5',
    muted: '#737373',
    previewColor: '#C6F32F'
  },
  {
    id: 'Sunset',
    name: 'Sunset',
    description: 'Warm twilight glow with blazing sunset coral',
    isDark: true,
    bgCanvas: '#12090B',
    bgSurface: '#1A0E11',
    bgCard: '#241418',
    bgInput: '#12090B',
    borderSubtle: '#3D1F27',
    textPrimary: '#FFF1F2',
    textSecondary: '#FDA4AF',
    accentPrimary: '#F97316',
    accentForeground: '#000000',
    bg: '#12090B',
    card: '#241418',
    cardHover: '#301A20',
    border: '#3D1F27',
    accent: '#F97316',
    accentHover: '#EA580C',
    accentLight: 'rgba(249, 115, 22, 0.15)',
    text: '#FFF1F2',
    muted: '#FDA4AF',
    previewColor: '#F97316'
  },
  {
    id: 'Paper',
    name: 'Paper',
    description: 'Editorial tactile warm cream with heritage amber',
    isDark: false,
    bgCanvas: '#F6F4EF',
    bgSurface: '#FFFFFF',
    bgCard: '#FAF8F5',
    bgInput: '#EFECE4',
    borderSubtle: '#E3DFD4',
    textPrimary: '#1C1917',
    textSecondary: '#78716C',
    accentPrimary: '#D97706',
    accentForeground: '#FFFFFF',
    bg: '#F6F4EF',
    card: '#FAF8F5',
    cardHover: '#EFECE4',
    border: '#E3DFD4',
    accent: '#D97706',
    accentHover: '#B45309',
    accentLight: 'rgba(217, 119, 6, 0.15)',
    text: '#1C1917',
    muted: '#78716C',
    previewColor: '#D97706'
  },
  {
    id: 'nordic-frost',
    name: 'Nordic Frost',
    description: 'Crisp Arctic daylight slate with glacier cyan precision',
    isDark: false,
    category: 'light',
    bgCanvas: '#F1F5F9',
    bgSurface: '#FFFFFF',
    bgElevated: '#E2E8F0',
    bgCard: '#F8FAFC',
    bgInput: '#E2E8F0',
    borderSubtle: '#CBD5E1',
    textPrimary: '#0F172A',
    textSecondary: '#475569',
    textMuted: '#64748B',
    accentPrimary: '#0284C7',
    accentSecondary: '#0EA5E9',
    accentForeground: '#FFFFFF',
    bg: '#F1F5F9',
    card: '#F8FAFC',
    cardHover: '#E2E8F0',
    border: '#CBD5E1',
    accent: '#0284C7',
    accentHover: '#0369A1',
    accentLight: 'rgba(2, 132, 199, 0.14)',
    text: '#0F172A',
    muted: '#475569',
    previewColor: '#0284C7'
  },
  {
    id: 'tokyo-cyber',
    name: 'Tokyo Cyber',
    description: 'Deep Shinjuku midnight canvas with electric cyan & magenta',
    isDark: true,
    category: 'neon',
    bgCanvas: '#090B14',
    bgSurface: '#101322',
    bgElevated: '#181C32',
    bgCard: '#14182B',
    bgInput: '#1B2038',
    borderSubtle: '#262C4A',
    textPrimary: '#F1F5F9',
    textSecondary: '#94A3B8',
    textMuted: '#64748B',
    accentPrimary: '#00E5FF',
    accentSecondary: '#FF2E97',
    accentForeground: '#061018',
    bg: '#090B14',
    card: '#14182B',
    cardHover: '#1B2038',
    border: '#262C4A',
    accent: '#00E5FF',
    accentHover: '#00B8D4',
    accentLight: 'rgba(0, 229, 255, 0.15)',
    text: '#F1F5F9',
    muted: '#94A3B8',
    previewColor: '#00E5FF'
  },
  {
    id: 'obsidian-gold',
    name: 'Obsidian Gold',
    description: 'Ultra-deep mastering obsidian paired with warm studio gold',
    isDark: true,
    category: 'dark',
    bgCanvas: '#070709',
    bgSurface: '#101014',
    bgElevated: '#18181F',
    bgCard: '#141419',
    bgInput: '#1C1C24',
    borderSubtle: '#2A2A35',
    textPrimary: '#FAFAFA',
    textSecondary: '#A1A1AA',
    textMuted: '#71717A',
    accentPrimary: '#F59E0B',
    accentSecondary: '#FBBF24',
    accentForeground: '#09090B',
    bg: '#070709',
    card: '#141419',
    cardHover: '#1C1C24',
    border: '#2A2A35',
    accent: '#F59E0B',
    accentHover: '#D97706',
    accentLight: 'rgba(245, 158, 11, 0.16)',
    text: '#FAFAFA',
    muted: '#A1A1AA',
    previewColor: '#F59E0B'
  },
  {
    id: 'dracula-pro',
    name: 'Dracula Pro',
    description: 'High-contrast twilight charcoal with orchid purple & pink highlights',
    isDark: true,
    category: 'neon',
    bgCanvas: '#191A21',
    bgSurface: '#21222C',
    bgElevated: '#282A36',
    bgCard: '#252734',
    bgInput: '#2F3242',
    borderSubtle: '#3B3E54',
    textPrimary: '#F8F8F2',
    textSecondary: '#B6B9D0',
    textMuted: '#6272A4',
    accentPrimary: '#BD93F9',
    accentSecondary: '#FF79C6',
    accentForeground: '#191A21',
    bg: '#191A21',
    card: '#252734',
    cardHover: '#2F3242',
    border: '#3B3E54',
    accent: '#BD93F9',
    accentHover: '#A475F6',
    accentLight: 'rgba(189, 147, 249, 0.16)',
    text: '#F8F8F2',
    muted: '#B6B9D0',
    previewColor: '#BD93F9'
  },
  {
    id: 'solarized-studio',
    name: 'Solarized Studio',
    description: 'Eye-friendly warm parchment cream with calibrated Solarized azure',
    isDark: false,
    category: 'light',
    bgCanvas: '#FDF6E3',
    bgSurface: '#F5EFDC',
    bgElevated: '#EEE8D5',
    bgCard: '#FBF5E4',
    bgInput: '#EAE3CE',
    borderSubtle: '#DCD4BD',
    textPrimary: '#073642',
    textSecondary: '#586E75',
    textMuted: '#657B83',
    accentPrimary: '#268BD2',
    accentSecondary: '#2AA198',
    accentForeground: '#FFFFFF',
    bg: '#FDF6E3',
    card: '#FBF5E4',
    cardHover: '#EEE8D5',
    border: '#DCD4BD',
    accent: '#268BD2',
    accentHover: '#1E73B0',
    accentLight: 'rgba(38, 139, 210, 0.14)',
    text: '#073642',
    muted: '#586E75',
    previewColor: '#268BD2'
  },
  {
    id: 'crimson-cinema',
    name: 'Crimson Cinema',
    description: 'Color-grading suite darkness with anamorphic crimson & ember accents',
    isDark: true,
    category: 'neon',
    bgCanvas: '#0B0708',
    bgSurface: '#140D0F',
    bgElevated: '#1E1316',
    bgCard: '#191013',
    bgInput: '#23171B',
    borderSubtle: '#352127',
    textPrimary: '#FAF5F6',
    textSecondary: '#BFA9B0',
    textMuted: '#8B747C',
    accentPrimary: '#E11D48',
    accentSecondary: '#F97316',
    accentForeground: '#FFFFFF',
    bg: '#0B0708',
    card: '#191013',
    cardHover: '#23171B',
    border: '#352127',
    accent: '#E11D48',
    accentHover: '#BE123C',
    accentLight: 'rgba(225, 29, 72, 0.16)',
    text: '#FAF5F6',
    muted: '#BFA9B0',
    previewColor: '#E11D48'
  },
  {
    id: 'sage-studio',
    name: 'Sage Studio',
    description: 'Calm botanical studio paper with deep pine sage contrast',
    isDark: false,
    category: 'light',
    bgCanvas: '#EEF2EE',
    bgSurface: '#FFFFFF',
    bgElevated: '#E2E8E2',
    bgCard: '#F7FAF7',
    bgInput: '#E5ECE5',
    borderSubtle: '#CFDACE',
    textPrimary: '#132419',
    textSecondary: '#475E4E',
    textMuted: '#637A6A',
    accentPrimary: '#15803D',
    accentSecondary: '#0D9488',
    accentForeground: '#FFFFFF',
    bg: '#EEF2EE',
    card: '#F7FAF7',
    cardHover: '#E5ECE5',
    border: '#CFDACE',
    accent: '#15803D',
    accentHover: '#166534',
    accentLight: 'rgba(21, 128, 61, 0.14)',
    text: '#132419',
    muted: '#475E4E',
    previewColor: '#15803D'
  }
];

export type ThemeCategory = 'dark' | 'light' | 'neon' | 'custom';

export const THEME_CATEGORIES_META: Array<{ id: 'all' | ThemeCategory; label: string }> = [
  { id: 'all', label: 'All' },
  { id: 'dark', label: 'Dark Studio' },
  { id: 'light', label: 'Light Editorial' },
  { id: 'neon', label: 'Neon & Cinema' },
  { id: 'custom', label: 'My Custom Themes' },
];

const CUSTOM_THEMES_STORAGE_KEY = 'lost_heaven_custom_themes';

const BUILTIN_CATEGORY_MAP: Record<string, ThemeCategory> = {
  'Hicksfield': 'dark',
  'Hicksfield 2': 'dark',
  'Emerald': 'dark',
  'Forest': 'dark',
  'Royal Purple': 'neon',
  'Midnight Blue': 'dark',
  'Carbon': 'dark',
  'Modern Light': 'light',
  'Higgsfield': 'neon',
  'Sunset': 'neon',
  'Paper': 'light',
  'nordic-frost': 'light',
  'tokyo-cyber': 'neon',
  'obsidian-gold': 'dark',
  'dracula-pro': 'neon',
  'solarized-studio': 'light',
  'crimson-cinema': 'neon',
  'sage-studio': 'light',
};

export function getThemeCategory(theme: ThemeDefinition): ThemeCategory {
  if (theme.isCustom || theme.category === 'custom') return 'custom';
  if (theme.category) return theme.category;
  return BUILTIN_CATEGORY_MAP[theme.id] || (theme.isDark ? 'dark' : 'light');
}

function normalizeHex(hex: string, fallback = '#000000'): string {
  const clean = String(hex || '').trim();
  if (/^#[0-9a-fA-F]{6}$/.test(clean)) return clean.toUpperCase();
  if (/^#[0-9a-fA-F]{3}$/.test(clean)) {
    const r = clean[1], g = clean[2], b = clean[3];
    return `#${r}${r}${g}${g}${b}${b}`.toUpperCase();
  }
  return fallback;
}

function hexToRgb(hex: string): [number, number, number] {
  const h = normalizeHex(hex, '#000000').slice(1);
  return [
    parseInt(h.slice(0, 2), 16),
    parseInt(h.slice(2, 4), 16),
    parseInt(h.slice(4, 6), 16),
  ];
}

function hexToRgba(hex: string, alpha: number): string {
  const [r, g, b] = hexToRgb(hex);
  return `rgba(${r}, ${g}, ${b}, ${alpha})`;
}

export function relativeLuminance(hex: string): number {
  const [r, g, b] = hexToRgb(hex).map((c) => {
    const s = c / 255;
    return s <= 0.03928 ? s / 12.92 : Math.pow((s + 0.055) / 1.055, 2.4);
  });
  return 0.2126 * r + 0.7152 * g + 0.0722 * b;
}

export function computeContrastRatio(foregroundHex: string, backgroundHex: string): number {
  const l1 = relativeLuminance(foregroundHex);
  const l2 = relativeLuminance(backgroundHex);
  const lighter = Math.max(l1, l2);
  const darker = Math.min(l1, l2);
  return Number(((lighter + 0.05) / (darker + 0.05)).toFixed(2));
}

export function isHexDark(hex: string): boolean {
  return relativeLuminance(hex) < 0.35;
}

export interface CustomThemeTokens {
  id?: string;
  name: string;
  description?: string;
  bgCanvas: string;
  bgSurface: string;
  bgElevated: string;
  accentPrimary: string;
  accentSecondary: string;
  textPrimary: string;
  borderSubtle: string;
}

export function buildCustomTheme(tokens: CustomThemeTokens): ThemeDefinition {
  const bgCanvas = normalizeHex(tokens.bgCanvas, '#09090B');
  const bgSurface = normalizeHex(tokens.bgSurface, '#111114');
  const bgElevated = normalizeHex(tokens.bgElevated, '#1A1A20');
  const accentPrimary = normalizeHex(tokens.accentPrimary, '#8B5CF6');
  const accentSecondary = normalizeHex(tokens.accentSecondary, '#06B6D4');
  const textPrimary = normalizeHex(tokens.textPrimary, '#F8FAFC');
  const borderSubtle = normalizeHex(tokens.borderSubtle, '#27272A');
  const dark = isHexDark(bgCanvas);
  const textSecondary = dark ? '#94A3B8' : '#475569';
  const textMuted = dark ? '#71717A' : '#64748B';
  const accentForeground = isHexDark(accentPrimary) ? '#FFFFFF' : '#09090B';
  const slugBase = String(tokens.name || 'Custom Studio')
    .trim()
    .toLowerCase()
    .replace(/[^a-z0-9]+/g, '-')
    .replace(/^-|-$/g, '') || 'custom';
  const id = (tokens.id && tokens.id.trim()) || `custom-${slugBase}`;

  return {
    id,
    name: String(tokens.name || 'Custom Studio Theme').trim() || 'Custom Studio Theme',
    description: String(tokens.description || 'User-crafted Studio Theme').trim(),
    isDark: dark,
    category: 'custom',
    isCustom: true,
    bgCanvas,
    bgSurface,
    bgElevated,
    bgCard: bgSurface,
    bgInput: bgElevated,
    borderSubtle,
    textPrimary,
    textSecondary,
    textMuted,
    accentPrimary,
    accentSecondary,
    accentForeground,
    bg: bgCanvas,
    card: bgSurface,
    cardHover: bgElevated,
    border: borderSubtle,
    accent: accentPrimary,
    accentHover: accentSecondary,
    accentLight: hexToRgba(accentPrimary, 0.16),
    text: textPrimary,
    muted: textSecondary,
    previewColor: accentPrimary,
  };
}

export function normalizeCustomTheme(raw: any): ThemeDefinition | null {
  if (!raw || typeof raw !== 'object') return null;
  const built = buildCustomTheme({
    id: typeof raw.id === 'string' && raw.id.trim() ? raw.id.trim() : undefined,
    name: typeof raw.name === 'string' && raw.name.trim() ? raw.name.trim() : 'Custom Studio Theme',
    description: typeof raw.description === 'string' ? raw.description : undefined,
    bgCanvas: raw.bgCanvas || raw.bg || '#09090B',
    bgSurface: raw.bgSurface || raw.card || '#111114',
    bgElevated: raw.bgElevated || raw.bgInput || raw.cardHover || '#1A1A20',
    accentPrimary: raw.accentPrimary || raw.accent || raw.previewColor || '#8B5CF6',
    accentSecondary: raw.accentSecondary || raw.accentHover || raw.accentPrimary || '#06B6D4',
    textPrimary: raw.textPrimary || raw.text || '#F8FAFC',
    borderSubtle: raw.borderSubtle || raw.border || '#27272A',
  });
  return built;
}

export function getCustomThemes(): ThemeDefinition[] {
  try {
    const raw = localStorage.getItem(CUSTOM_THEMES_STORAGE_KEY);
    if (!raw) return [];
    const parsed = JSON.parse(raw);
    if (!Array.isArray(parsed)) return [];
    return parsed
      .map((item) => normalizeCustomTheme(item))
      .filter((t): t is ThemeDefinition => Boolean(t));
  } catch {
    return [];
  }
}

export function setCustomThemes(list: any[]): ThemeDefinition[] {
  const normalized = (Array.isArray(list) ? list : [])
    .map((item) => normalizeCustomTheme(item))
    .filter((t): t is ThemeDefinition => Boolean(t));
  try {
    localStorage.setItem(CUSTOM_THEMES_STORAGE_KEY, JSON.stringify(normalized));
  } catch {
    // ignore storage quota errors
  }
  return normalized;
}

export function getAllThemes(customList?: ThemeDefinition[]): ThemeDefinition[] {
  const custom = customList || getCustomThemes();
  const seen = new Set<string>(THEMES.map((t) => t.id));
  const merged = [...THEMES];
  for (const c of custom) {
    if (!seen.has(c.id)) {
      merged.push(c);
      seen.add(c.id);
    }
  }
  return merged;
}

export function applyTheme(themeId: string, customList?: any[]) {
  const normalizedCustom = Array.isArray(customList) ? setCustomThemes(customList) : getCustomThemes();
  const all = getAllThemes(normalizedCustom);
  const theme = all.find((t) => t.id === themeId) || THEMES[0];
  const root = document.documentElement;
  const bgElevated = theme.bgElevated || theme.cardHover || theme.bgCard || theme.bgSurface;
  const accentSecondary = theme.accentSecondary || theme.accentHover || theme.accentPrimary;
  const textMuted = theme.textMuted || theme.muted || theme.textSecondary;

  // Apply core semantic CSS variables
  root.style.setProperty('--bg-canvas', theme.bgCanvas);
  root.style.setProperty('--bg-surface', theme.bgSurface);
  root.style.setProperty('--bg-elevated', bgElevated);
  root.style.setProperty('--bg-card', theme.bgCard);
  root.style.setProperty('--bg-input', theme.bgInput);
  root.style.setProperty('--border-subtle', theme.borderSubtle);
  root.style.setProperty('--text-primary', theme.textPrimary);
  root.style.setProperty('--text-secondary', theme.textSecondary);
  root.style.setProperty('--text-muted', textMuted);
  root.style.setProperty('--accent-primary', theme.accentPrimary);
  root.style.setProperty('--accent-secondary', accentSecondary);
  root.style.setProperty('--accent-foreground', theme.accentForeground);

  // Backward-compatible variable aliases
  root.style.setProperty('--bg', theme.bgCanvas);
  root.style.setProperty('--card', theme.bgCard);
  root.style.setProperty('--card-hover', theme.cardHover);
  root.style.setProperty('--border', theme.borderSubtle);
  root.style.setProperty('--accent', theme.accentPrimary);
  root.style.setProperty('--accent-hover', theme.accentHover);
  root.style.setProperty('--accent-light', theme.accentLight);
  root.style.setProperty('--accent-subtle', theme.accentLight);
  root.style.setProperty('--text', theme.textPrimary);
  root.style.setProperty('--muted', theme.textSecondary);

  document.body.style.backgroundColor = theme.bgCanvas;
  document.body.style.color = theme.textPrimary;

  if (theme.isDark) {
    document.documentElement.classList.add('dark');
  } else {
    document.documentElement.classList.remove('dark');
  }

  localStorage.setItem('lost_heaven_theme', theme.id);
  return theme;
}

export function getActiveTheme(customList?: ThemeDefinition[]): ThemeDefinition {
  const saved =
    localStorage.getItem('lost_heaven_theme') ||
    localStorage.getItem('clipshlip_theme');
  return getAllThemes(customList).find((t) => t.id === saved) || THEMES[0];
}
