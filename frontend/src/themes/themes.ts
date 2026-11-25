export interface ThemeDefinition {
  id: string;
  name: string;
  description: string;
  isDark: boolean;
  bgCanvas: string;
  bgSurface: string;
  bgCard: string;
  bgInput: string;
  borderSubtle: string;
  textPrimary: string;
  textSecondary: string;
  accentPrimary: string;
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
  }
];

export function applyTheme(themeId: string) {
  const theme = THEMES.find((t) => t.id === themeId) || THEMES[0];
  const root = document.documentElement;

  // Apply the 9 core CSS variables
  root.style.setProperty('--bg-canvas', theme.bgCanvas);
  root.style.setProperty('--bg-surface', theme.bgSurface);
  root.style.setProperty('--bg-card', theme.bgCard);
  root.style.setProperty('--bg-input', theme.bgInput);
  root.style.setProperty('--border-subtle', theme.borderSubtle);
  root.style.setProperty('--text-primary', theme.textPrimary);
  root.style.setProperty('--text-secondary', theme.textSecondary);
  root.style.setProperty('--accent-primary', theme.accentPrimary);
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

export function getActiveTheme(): ThemeDefinition {
  const saved =
    localStorage.getItem('lost_heaven_theme') ||
    localStorage.getItem('clipshlip_theme');
  return THEMES.find((t) => t.id === saved) || THEMES[0];
}
