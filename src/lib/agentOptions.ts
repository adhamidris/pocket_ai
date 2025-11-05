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
'Never escalate',  'On fallback',  'Negative sentiment',  'High value',  'Always escalate'] as const

export type AgentRole = typeof AGENT_ROLES[number]
export type AgentTone = typeof AGENT_TONES[number]
export type AgentTrait = typeof AGENT_TRAITS[number]
export type EscalationRule = typeof ESCALATION_OPTIONS[number]
export type AgentStatus = 'Active' | 'Inactive' | 'Draft'

// Mapping utilities for frontend-backend alignment
export const mapRoleToBackend = (role: AgentRole): string => {
  const mapping: Record<AgentRole, string> = {
    'Support Agent': 'support',
    'Sales Associate': 'sales', 
    'Technical Specialist': 'support', // Map to support for now
    'Billing Assistant': 'success' // Map to success for billing
  };
  return mapping[role] || 'support';
};

export const mapRoleFromBackend = (backendRole: string): AgentRole => {
  const mapping: Record<string, AgentRole> = {
    'support': 'Support Agent',
    'sales': 'Sales Associate',
    'research': 'Technical Specialist', // Map research to Technical Specialist
    'success': 'Billing Assistant', // Map success to Billing Assistant
    'marketing': 'Sales Associate' // Map marketing to Sales Associate
  };
  return mapping[backendRole] || 'Support Agent';
};

export const mapToneToBackend = (tone: AgentTone): string => {
  const mapping: Record<AgentTone, string> = {
    'Friendly': 'friendly',
    'Professional': 'professional',
    'Empathetic': 'empathetic',
    'Concise': 'casual', // Map concise to casual
    'Playful': 'playful',
    'Formal': 'formal'
  };
  return mapping[tone] || 'friendly';
};

export const mapToneFromBackend = (backendTone: string): AgentTone => {
  const mapping: Record<string, AgentTone> = {
    'friendly': 'Friendly',
    'professional': 'Professional',
    'casual': 'Concise', // Map casual to Concise
    'formal': 'Formal',
    'empathetic': 'Empathetic',
    'playful': 'Playful'
  };
  return mapping[backendTone] || 'Friendly';
};

export const mapTraitToBackend = (trait: AgentTrait): string => {
  const mapping: Record<AgentTrait, string> = {
    'Patient': 'patient',
    'Proactive': 'proactive',
    'Detail-oriented': 'detailed',
    'Persuasive': 'direct', // Map persuasive to direct
    'Analytical': 'curious', // Map analytical to curious
    'Creative': 'creative'
  };
  return mapping[trait] || 'patient';
};

export const mapTraitFromBackend = (backendTrait: string): AgentTrait => {
  const mapping: Record<string, AgentTrait> = {
    'patient': 'Patient',
    'proactive': 'Proactive',
    'detailed': 'Detail-oriented',
    'curious': 'Analytical', // Map curious to Analytical
    'direct': 'Persuasive', // Map direct to Persuasive
    'creative': 'Creative'
  };
  return mapping[backendTrait] || 'Patient';
};

export const mapEscalationToBackend = (rule: EscalationRule): string => {
  const mapping: Record<EscalationRule, string> = {
    'Never escalate': 'never',
    'On fallback': 'on_fallback',
    'Negative sentiment': 'on_negative_sentiment',
    'High value': 'on_high_value',
    'Always escalate': 'always'
  };
  return mapping[rule] || 'never';
};

export const mapEscalationFromBackend = (backendRule: string): EscalationRule => {
  const mapping: Record<string, EscalationRule> = {
    'never': 'Never escalate',
    'on_fallback': 'On fallback',
    'on_negative_sentiment': 'Negative sentiment',
    'on_high_value': 'High value',
    'always': 'Always escalate'
  };
  return mapping[backendRule] || 'Never escalate';
};
