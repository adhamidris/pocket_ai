import type { Channel } from './conversations'

export type ConsentState = 'granted' | 'denied' | 'unknown' | 'withdrawn'

export interface Contact {
  id: string
  name: string
  email?: string
  phone?: string
  channels: Channel[] // where they contacted us
  tags: string[] // arbitrary labels
  vip?: boolean
  lastInteractionTs?: number // epoch ms
  lifetimeValue?: number // optional (UI only for now)
  consent: ConsentState
  piiRedacted?: boolean // UI toggle only
}

export interface InteractionSummary {
  conversationId: string
  lastMessageSnippet: string
  lastUpdatedTs: number
  channel: Channel
  intentTags?: string[]
  sla?: 'ok' | 'risk' | 'breach'
}

export type RuleOp = 'is' | 'is_not' | 'contains' | 'gt' | 'lt' | 'in'
export interface SegmentRule { field: 'tag' | 'vip' | 'consent' | 'channel' | 'lastInteractionTs' | 'lifetimeValue'; op: RuleOp; value: any }
export interface Segment { id: string; name: string; rules: SegmentRule[]; color?: string }

export interface DedupeCandidate { masterId: string; dupId: string; reason: 'same_email' | 'same_phone' | 'name_similarity'; score: number }


