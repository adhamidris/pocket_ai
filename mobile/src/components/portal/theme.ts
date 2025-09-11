import React from 'react'
import { useTheme } from '../../providers/ThemeProvider'

export type AvatarShape = 'round' | 'square'

export interface PortalTokens {
  avatarShape: AvatarShape
  bubbleRadius: number
  highContrast: boolean
  colors: {
    card: string
    cardForeground: string
    border: string
    primary: string
    primaryForeground: string
    muted: string
    mutedForeground: string
    success: string
    error: string
    warning: string
    background: string
  }
}

const PortalThemeTokensContext = React.createContext<PortalTokens | null>(null)

export const useThemeTokens = (opts?: { themeId?: string; highContrast?: boolean; avatarShape?: AvatarShape; bubbleRadius?: number }): PortalTokens => {
  const { theme } = useTheme()
  const highContrast = !!opts?.highContrast
  const tokens: PortalTokens = {
    avatarShape: opts?.avatarShape || 'square',
    bubbleRadius: opts?.bubbleRadius ?? 12,
    highContrast,
    colors: {
      card: highContrast ? theme.color.background : theme.color.card,
      cardForeground: theme.color.cardForeground,
      border: highContrast ? theme.color.foreground : theme.color.border,
      primary: theme.color.primary,
      primaryForeground: theme.color.primaryForeground,
      muted: theme.color.muted,
      mutedForeground: theme.color.mutedForeground,
      success: theme.color.success,
      error: theme.color.error,
      warning: theme.color.warning,
      background: theme.color.background,
    }
  }
  return tokens
}

export const usePortalTokens = (): PortalTokens => {
  const ctx = React.useContext(PortalThemeTokensContext)
  if (!ctx) {
    // Fallback to default tokens
    return useThemeTokens()
  }
  return ctx
}

export const PortalThemeProvider: React.FC<{ value: PortalTokens; children: React.ReactNode }> = ({ value, children }) => {
  return <PortalThemeTokensContext.Provider value={value}>{children}</PortalThemeTokensContext.Provider>
}


