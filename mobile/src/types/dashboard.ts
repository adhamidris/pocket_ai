export type KpiKind =
  | 'liveConversations'
  | 'frtP50'
  | 'frtP90'
  | 'resolutionRate'
  | 'csat'
  | 'deflection'
  | 'vipQueue'

export interface DashboardKpi {
  kind: KpiKind
  value: number
  unit?: '%' | 's' | 'count'
  delta?: number
  period?: '24h' | '7d'
  target?: number
}

export type AlertKind = 'urgentBacklog' | 'slaRisk' | 'unassigned' | 'volumeSpike'

export interface AlertItem {
  id: string
  kind: AlertKind
  count: number
  buckets?: { label: string; count: number }[]
  deeplink: string
}

export interface SetupStep {
  id: string
  title: string
  status: 'done' | 'todo'
  deeplink: string
}

export interface IntentItem {
  name: string
  sharePct: number
  deflectionPct?: number
  trendDelta?: number
}

export interface PeakHour {
  hour: number
  value: number
}

export type IndustryPackId = 'neutral' | 'retail' | 'services' | 'saas'

export interface DashboardConfig {
  pack?: IndustryPackId
  overrides?: Record<string, any>
}


