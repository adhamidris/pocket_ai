export type Channel = 'whatsapp' | 'instagram' | 'facebook' | 'web' | 'email'
export type Priority = 'low' | 'normal' | 'high' | 'vip'
export type SLAState = 'ok' | 'risk' | 'breach'
export type FilterKey = 'urgent' | 'waiting30' | 'unassigned' | 'slaRisk' | 'vip' | 'channel' | 'intent' | 'tag'

export interface ConversationSummary {
  id: string
  customerName: string
  lastMessageSnippet: string
  lastUpdatedTs: number // epoch ms
  channel: Channel
  tags: string[]
  assignedTo?: string // agent id or name
  priority: Priority
  waitingMinutes: number // since last customer msg
  sla: SLAState
  lowConfidence?: boolean
}

export interface Message {
  id: string
  sender: 'customer' | 'ai' | 'agent'
  text: string
  ts: number // epoch ms
  status?: 'queued' | 'sent'
}

export interface ConversationDetail extends ConversationSummary {
  messages: Message[]
}


