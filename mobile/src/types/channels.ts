export type SocialChannel = 'whatsapp' | 'instagram' | 'facebook' | 'web'
export type VerifyState = 'unverified' | 'verifying' | 'verified' | 'failed'

export interface PublishLink {
  id: string
  url: string
  shortUrl?: string
  themeId?: string
  createdAt: number
  lastRotatedAt?: number
  verify: { state: VerifyState; lastCheckedAt?: number; message?: string }
}

export interface ChannelStatus {
  channel: SocialChannel
  connected: boolean
  lastVerifiedAt?: number
  notes?: string
}

export interface RecipeItem { id: string; channel: SocialChannel; title: string; steps: string[]; ctaLabel: string }

export interface WidgetSnippet { framework: 'html' | 'react' | 'next' | 'shopify'; code: string }


