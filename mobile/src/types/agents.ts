export type AgentKind = 'ai' | 'human'
export type AgentStatus = 'online' | 'away' | 'offline' | 'dnd'
export type Skill = 'sales' | 'support' | 'billing' | 'tech' | 'logistics' | 'custom'

export interface SkillTag { name: Skill | string; level?: 1 | 2 | 3 | 4 | 5 }

export interface ScheduleSlot { day: 0 | 1 | 2 | 3 | 4 | 5 | 6; start: string; end: string } // '09:00'
export interface CapacityHint { concurrent: number; backlogMax?: number }

export interface AllowedAction { key: string; label: string; description?: string; enabled: boolean; risk?: 'low' | 'medium' | 'high' }

export interface BaseAgent { id: string; kind: AgentKind; name: string; avatarUrl?: string; status: AgentStatus; skills: SkillTag[]; notes?: string }

export interface HumanAgent extends BaseAgent { kind: 'human'; email?: string; phone?: string; schedule?: ScheduleSlot[]; capacity?: CapacityHint; assignedOpen?: number }

export interface AiBehavior { temperature?: number; tone?: 'neutral' | 'friendly' | 'formal'; systemPrompt?: string }
export interface AiAgent extends BaseAgent { kind: 'ai'; behavior: AiBehavior; allowlist: AllowedAction[]; knowledgeSources?: string[]; deflectionTarget?: number }

export type AnyAgent = HumanAgent | AiAgent

// Routing
export type RouteCondition = 'intent' | 'channel' | 'vip' | 'priority' | 'businessHours' | 'overflow'
export interface RoutingRule {
  id: string
  name: string
  when: { cond: RouteCondition; op?: 'is' | 'in' | 'eq' | 'gte' | 'lte'; value?: any }[]
  then: { action: 'assign_agent' | 'assign_skill_group' | 'escalate' | 'queue' | 'deflect'; target?: string }
  enabled: boolean
}

export interface EscalationPolicy { id: string; name: string; rules: { afterMinutes: number; to: 'human_supervisor' | 'vip_queue' | 'on_call'; requiresApproval?: boolean }[] }


