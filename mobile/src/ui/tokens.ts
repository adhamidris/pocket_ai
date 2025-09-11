// Design system tokens (UI-only, no runtime theme switching here)
// These mirror the app's theme semantics to keep naming consistent

export type SpacingKey = 'xxs' | 'xs' | 'sm' | 'md' | 'lg' | 'xl' | 'xxl'

export const space: Record<SpacingKey, number> = {
  xxs: 4,
  xs: 8,
  sm: 12,
  md: 16,
  lg: 24,
  xl: 32,
  xxl: 40,
}

export type RadiusKey = 'sm' | 'md' | 'lg' | 'xl'

export const radius: Record<RadiusKey, number> = {
  sm: 8,
  md: 12,
  lg: 16,
  xl: 24,
}

export const font = {
  family: {
    // Prefer system stacks; app may load Inter via expo-font, but keep UI-agnostic here
    regular: 'System',
    medium: 'System',
    semibold: 'System',
    bold: 'System',
  },
  size: {
    xs: 12,
    sm: 14,
    md: 16,
    lg: 20,
    xl: 24,
    display: 32,
  },
  weight: {
    regular: '400' as const,
    medium: '500' as const,
    semibold: '600' as const,
    bold: '700' as const,
  },
}

// Baseline semantic colors (light default). Components should prefer useTheme() for live values.
export const colors = {
  background: 'hsl(0,0%,100%)',
  foreground: 'hsl(224,9%,20%)',
  card: 'hsl(0,0%,100%)',
  cardForeground: 'hsl(224,9%,20%)',
  primary: 'hsl(240,75%,48%)',
  primaryLight: 'hsl(250,100%,75%)',
  secondary: 'hsl(240,5%,96%)',
  muted: 'hsl(240,5%,97%)',
  mutedForeground: 'hsl(224,6%,40%)',
  placeholder: 'hsl(224, 5%, 46%)',
  accent: 'hsl(240,5%,96%)',
  border: 'hsl(240,6%,90%)',
  ring: 'hsl(240,75%,48%)',
  success: 'hsl(142,71%,45%)',
  warning: 'hsl(38,92%,50%)',
  error: 'hsl(0,72%,51%)',
}

export type Tokens = {
  space: typeof space
  radius: typeof radius
  font: typeof font
  colors: typeof colors
}

export const tokens: Tokens = { space, radius, font, colors }


