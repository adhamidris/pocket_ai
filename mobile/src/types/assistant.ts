export type Persona = 'ops'|'owner'|'agent'|'analyst'
export type Tone = 'concise'|'neutral'|'friendly'

export interface AskQuery {
  id: string
  text: string
  createdAt: number
  persona: Persona
  tone: Tone
  context?: { range?: { startIso: string; endIso: string }; filters?: Record<string, any> }
}

export interface Citation {
  id: string
  label: string
  kind: 'conversation'|'intent'|'metric'|'doc'|'policy'
  refId?: string
  url?: string
  masked?: boolean
}

export type AnswerChunkKind = 'paragraph'|'kpi'|'list'|'table'|'callout'|'chart'|'code'

export interface AnswerChunk {
  kind: AnswerChunkKind
  text?: string
  items?: string[]
  kpi?: { label: string; value: string; delta?: string }
  chartKey?: string
  code?: string
  citations?: Citation[]
}

export interface AskAnswer {
  id: string
  queryId: string
  createdAt: number
  chunks: AnswerChunk[]
  followUps: string[]
  toolSuggestions: ToolSuggestion[]
  safety?: { piiMasked: boolean; disclaimer?: string }
}

export type ToolKey =
  | 'open_conversations'
  | 'open_rule_builder'
  | 'open_analytics'
  | 'open_channels'
  | 'open_agent'
  | 'open_knowledge'
  | 'open_billing'
  | 'open_security'
  | 'create_note'
  | 'create_test'
  | 'start_training'
  | 'open_hours'
  | 'open_sla'
  | 'open_portal_preview'

export interface ToolSuggestion {
  key: ToolKey
  label: string
  params?: Record<string, any>
}

export interface VoiceState {
  enabled: boolean
  recording: boolean
  durationMs: number
  transcript?: string
}

export interface PromptTemplate {
  id: string
  name: string
  text: string
  persona?: Persona
  tone?: Tone
  pinned?: boolean
}

export interface MemoryPin {
  id: string
  title: string
  content: string
  createdAt: number
  tags?: string[]
}

export interface Shortcut {
  id: string
  label: string
  icon?: string
  action: ToolSuggestion
}


