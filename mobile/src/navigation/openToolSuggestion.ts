import { ToolSuggestion } from '../types/assistant'

export const openToolSuggestion = (navigate: (routeName: string, params?: any) => void, s: ToolSuggestion) => {
  switch (s.key) {
    case 'open_conversations': {
      const filter = s.params?.filter || 'all'
      navigate('Conversations', { filter })
      return
    }
    case 'open_rule_builder': {
      navigate('Actions', { screen: 'AllowRuleBuilder', params: { initial: { actionId: s.params?.actionId || '' } } })
      return
    }
    case 'open_analytics': {
      navigate('Analytics')
      return
    }
    case 'open_channels': {
      navigate('Channels')
      return
    }
    case 'open_agent': {
      navigate('Agents', { screen: 'AgentDetail', params: { id: s.params?.id, name: s.params?.name } })
      return
    }
    case 'open_knowledge': {
      navigate('Knowledge', { screen: s.params?.screen || 'FailureLog' })
      return
    }
    case 'open_billing': {
      navigate('Settings', { screen: 'Billing' })
      return
    }
    case 'open_security': {
      navigate('Security')
      return
    }
    case 'open_hours': {
      navigate('Automations', { screen: 'BusinessHours' })
      return
    }
    case 'open_sla': {
      navigate('Automations', { screen: 'SlaEditor', params: s.params })
      return
    }
    case 'open_portal_preview': {
      navigate('Portal')
      return
    }
    default:
      return
  }
}

export default openToolSuggestion


