export type Color = string

export type Theme = {
  dark: boolean
  color: {
    background: Color
    foreground: Color
    card: Color
    cardForeground: Color
    primary: Color
    primaryLight: Color
    secondary: Color
    muted: Color
    mutedForeground: Color
    accent: Color
    border: Color
    ring: Color
    success: Color
    warning: Color
    error: Color
  }
  radius: { sm: number; md: number; lg: number; xl: number }
  shadow: {
    premium: { androidElevation: number; ios: { color: Color; opacity: number; radius: number; offsetY: number } }
    md: { androidElevation: number; ios: { color: Color; opacity: number; radius: number; offsetY: number } }
  }
  spacing: { xs: number; sm: number; md: number; lg: number; xl: number }
  typography: {
    display: { fontSize: number; fontWeight: '700'; lineHeight: number }
    title: { fontSize: number; fontWeight: '600' }
    body: { fontSize: number; fontWeight: '400' }
    small: { fontSize: number; fontWeight: '400' }
  }
}

export const darkTheme: Theme = {
  dark: true,
  color: {
    background: 'hsl(215,25%,6%)',
    foreground: 'hsl(210,20%,98%)',
    card: 'hsl(215,20%,8%)',
    cardForeground: 'hsl(210,20%,98%)',
    primary: 'hsl(240,100%,70%)',
    primaryLight: 'hsl(250,100%,75%)',
    secondary: 'hsl(215,20%,15%)',
    muted: 'hsl(215,20%,15%)',
    mutedForeground: 'hsl(215,8%,65%)',
    accent: 'hsl(215,15%,18%)',
    border: 'hsl(215,15%,20%)',
    ring: 'hsl(240,100%,70%)',
    success: 'hsl(142,71%,45%)',
    warning: 'hsl(38,92%,50%)',
    error: 'hsl(0,72%,51%)',
  },
  radius: { sm: 8, md: 12, lg: 16, xl: 24 },
  shadow: {
    premium: { androidElevation: 8, ios: { color: '#000', opacity: 0.15, radius: 20, offsetY: 12 } },
    md: { androidElevation: 4, ios: { color: '#000', opacity: 0.08, radius: 6, offsetY: 4 } },
  },
  spacing: { xs: 8, sm: 12, md: 16, lg: 24, xl: 32 },
  typography: {
    display: { fontSize: 56, fontWeight: '700', lineHeight: 60 },
    title: { fontSize: 32, fontWeight: '600' },
    body: { fontSize: 16, fontWeight: '400' },
    small: { fontSize: 14, fontWeight: '400' },
  },
}

export const lightTheme: Theme = {
  ...darkTheme,
  dark: false,
  color: {
    ...darkTheme.color,
    background: 'hsl(0,0%,100%)',
    foreground: 'hsl(224,9%,20%)',
    card: 'hsl(0,0%,100%)',
    cardForeground: 'hsl(224,9%,20%)',
    secondary: 'hsl(240,5%,96%)',
    muted: 'hsl(240,5%,97%)',
    mutedForeground: 'hsl(224,6%,40%)',
    accent: 'hsl(240,5%,96%)',
    border: 'hsl(240,6%,90%)',
    primary: 'hsl(240,75%,48%)',
  },
}

