export type ConversationsFilter = 'urgent' | 'waiting30' | 'unassigned' | 'slaRisk' | 'vip'

export type RootTabParamList = {
  Dashboard: undefined
  Conversations: { filter?: ConversationsFilter } | undefined
  CRM: undefined
  Agents: undefined
  Settings: undefined
}

export type ConversationsStackParamList = {
  ConversationsHome: { filter?: 'urgent'|'waiting30'|'unassigned'|'slaRisk'|'vip' } | { prefill?: string; channel?: string; selectedId?: string; contactId?: string } | undefined
  ConversationThread: { id: string }
}

