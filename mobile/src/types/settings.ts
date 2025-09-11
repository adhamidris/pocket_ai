export interface BusinessProfile {
  id: string
  name: string
  website?: string
  country?: string
  industry?: string
  logoUrl?: string
  faviconUrl?: string
  description?: string
}

export interface ThemeTokens {
  id: string
  name: string
  updatedAt: number
  primary: string
  secondary: string
  bg: string
  surface: string
  text: string
  textMuted: string
  bubbleAgent: string
  bubbleCustomer: string
  bubbleAI: string
  bubbleRadius: number
  fontFamily?: string
  logoUrl?: string
  avatarShape: 'circle' | 'rounded' | 'square'
}

export interface ThemePreset { id: string; name: string; tokens: Partial<ThemeTokens> }

export interface LocaleSettings {
  timezone: string
  locale: string
  dateFormat: 'auto' | 'DMY' | 'MDY' | 'YMD'
  timeFormat: '12h' | '24h'
  rtl: boolean
}

export interface NotificationPrefs {
  email: boolean
  push: boolean
  alerts: { sla: boolean; backlog: boolean; volumeSpike: boolean; billing: boolean }
}

export interface PublishState {
  themeId: string
  lastPublishedAt?: number
  linkedChannels: ('whatsapp'|'instagram'|'facebook'|'web')[]
}


