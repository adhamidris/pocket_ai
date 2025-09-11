export type MetricKey = 'frtP50'|'frtP90'|'resolutionRate'|'csat'|'deflection'|'volume'|'slaBreaches'|'repeatContactRate'|'aht'|'backlog'|'vipQueue'
export type Dimension = 'time'|'channel'|'intent'|'agent'|'priority'|'segment'|'source'
export type Timegrain = 'hour'|'day'|'week'|'month'
export interface TimeRange { startIso: string; endIso: string; compareStartIso?: string; compareEndIso?: string; grain: Timegrain }

export interface SeriesPoint { ts: number; value: number }
export interface MetricSeries { key: MetricKey; points: SeriesPoint[]; summary?: { current: number; previous?: number; delta?: number } }

export interface BreakdownRow { name: string; value: number; sharePct?: number; extra?: Record<string, number | undefined> }

export interface HeatmapPoint { x: number; y: number; value: number }

export interface SavedReport {
  id: string
  name: string
  metrics: MetricKey[]
  dims: Dimension[]
  range: TimeRange
  filters?: Record<string, any>
  createdAt: number
  schedule?: { cron?: string; recipients?: string[] }
}

export interface AttributionRow { source: string; medium?: string; campaign?: string; volume: number; conversions?: number; resolutionRate?: number }


