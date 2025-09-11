export type ConversationsFilter = 'urgent' | 'waiting30' | 'unassigned' | 'slaRisk' | 'vip'

export type RootTabParamList = {
  Dashboard: undefined
  Conversations: { filter?: ConversationsFilter } | undefined
  CRM: undefined
  Agents: undefined
  Settings: undefined
}

export type ConversationsStackParamList = {
  Conversations: { filter?: 'urgent'|'waiting30'|'unassigned'|'slaRisk'|'vip' } | undefined
  ConversationThread: { id: string }
}


