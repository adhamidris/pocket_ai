export type ActionKind = 'read'|'create'|'update'|'delete'|'notify'|'export'|'trigger'
export type ParamType = 'string'|'number'|'boolean'|'enum'|'date'|'json'|'email'|'phone'|'url'

export interface ParamSpec { key: string; label: string; type: ParamType; required?: boolean; enumVals?: string[]; min?: number; max?: number; regex?: string; help?: string }

export interface ActionSpec {
  id: string
  name: string
  kind: ActionKind
  version: number
  summary: string
  longHelp?: string
  category: 'conversations'|'crm'|'orders'|'knowledge'|'automations'|'settings'|'analytics'|'custom'
  params: ParamSpec[]
  effects: string[]
  riskLevel: 'low'|'medium'|'high'
}

export interface AllowRule {
  actionId: string
  agentIds?: string[]
  intents?: string[]
  channels?: string[]
  timeOfDay?: { start: 'HH:mm'; end: 'HH:mm' }
  weekdays?: number[]
  requireApproval?: boolean
  approverRole?: 'owner'|'admin'|'supervisor'
  rateLimit?: { count: number; perSeconds: number }
  guard?: { maxRecords?: number; maxAmount?: number; businessHoursOnly?: boolean }
}

export interface CapabilityPack { id: string; name: string; industryTags?: string[]; actionIds: string[]; recommended: boolean }

export interface SimInput { actionId: string; params: Record<string, any>; context?: Record<string, any> }
export interface SimResult { ok: boolean; preview: string; warnings?: string[]; errors?: string[]; estimatedLatencyMs?: number; affectedRecords?: number }

export type RunState = 'queued'|'approved'|'running'|'completed'|'failed'|'rejected'|'rate_limited'
export interface ActionRun { id: string; actionId: string; requestedBy: 'ai'|'agent'|'system'; createdAt: number; approvedBy?: string; state: RunState; params: Record<string, any>; result?: SimResult }

export interface SecretRef { key: string; note?: string; lastRotatedAt?: number }


