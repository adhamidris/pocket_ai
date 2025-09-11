export type Region = 'us'|'eu'|'auto'
export type Risk = 'low'|'medium'|'high'

export interface DataRetentionPolicy {
  id: string
  name: string
  conversationsDays: number        // -1 = indefinite (UI warning)
  messagesDays: number             // truncate message bodies after N days
  auditDays: number                // audit/event log retention
  piiMasking: boolean              // global masking on export/views (UI only)
  applyToBackups: boolean          // UI stub
  updatedAt: number
}

export interface ConsentTemplate {
  id: string
  name: string
  version: number
  languages: { code: string; text: string }[]
  channels: ('whatsapp'|'instagram'|'facebook'|'web'|'email')[]
  lastPublishedAt?: number
}
export type ConsentState = 'granted'|'denied'|'withdrawn'|'unknown'
export interface ConsentRecord { id:string; contactId:string; templateId:string; state:ConsentState; channel:string; ts:number }

export interface AuditEvent {
  id: string
  ts: number
  actor: 'me'|'system'|'ai'|'agent'
  action: string                     // e.g., 'export.create','policy.update'
  entityType: 'conversation'|'contact'|'policy'|'rule'|'agent'|'export'|'deletion'|'login'|'session'
  entityId?: string
  details?: string
  risk?: Risk
}

export interface IpRule { id:string; cidr:string; note?:string; enabled:boolean }
export interface SessionPolicy { enforce2FA:boolean; sessionHours:number; idleTimeoutMins:number; deviceLimit?:number }

export interface ExportJob { id:string; scope:'all'|'contact'|'conversation'|'dateRange'; params?:Record<string,any>; state:'queued'|'running'|'completed'|'failed'; progress:number; createdAt:number; finishedAt?:number; downloadUrl?:string }

export interface DeletionRequest { id:string; subject:'contact'|'conversation'|'account'; refId?:string; status:'pending'|'approved'|'processing'|'completed'|'rejected'; submittedAt:number; dueAt?:number; reason?:string }

export interface ResidencySetting { region: Region; effectiveAt?: number; note?: string }

export interface LegalHold { id:string; scope:'contact'|'conversation'|'all'; refId?:string; reason:string; active:boolean; createdAt:number; createdBy:'me'|'system' }

export interface PrivacyMode { anonymizeAnalytics:boolean; hideContactPII:boolean; strictLogging:boolean }


