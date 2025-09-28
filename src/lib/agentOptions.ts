export const AGENT_ROLES = [
  'Support Agent',
  'Sales Associate',
  'Technical Specialist',
  'Billing Assistant',
] as const

export const AGENT_TONES = [
  'Friendly',
  'Professional',
  'Empathetic',
  'Concise',
  'Playful',
  'Formal',
] as const

export const AGENT_TRAITS = [
  'Patient',
  'Proactive',
  'Detail-oriented',
  'Persuasive',
  'Analytical',
  'Creative',
] as const

export const ESCALATION_OPTIONS = [
  'Never escalate',
  'Negative sentiment',
  'SLA risk',
  'Always escalate complex',
] as const

export type AgentRole = typeof AGENT_ROLES[number]
export type AgentTone = typeof AGENT_TONES[number]
export type AgentTrait = typeof AGENT_TRAITS[number]
export type EscalationRule = typeof ESCALATION_OPTIONS[number]
export type AgentStatus = 'Active' | 'Inactive' | 'Draft'
