import AsyncStorage from '@react-native-async-storage/async-storage'
import { AlertItem, DashboardKpi, IndustryPackId } from '../../types/dashboard'

export type PersonalizerFlags = {
  hasWebsite: boolean
  hasUploads: boolean
  channelsConnected: boolean
  slaDefined: boolean
  businessHoursSet: boolean
  csatEnabled: boolean
  industryPack?: IndustryPackId
}

const DEFAULT_FLAGS: PersonalizerFlags = {
  hasWebsite: true,
  hasUploads: true,
  channelsConnected: false,
  slaDefined: false,
  businessHoursSet: false,
  csatEnabled: true,
  industryPack: 'neutral',
}

const STORAGE_KEY = 'dashboard.personalizer.flags'

let inMemoryOverride: Partial<PersonalizerFlags> | null = null

export const setPersonalizerFlags = (flags: Partial<PersonalizerFlags>) => {
  inMemoryOverride = { ...(inMemoryOverride || {}), ...flags }
}

export const getFlags = async (): Promise<PersonalizerFlags> => {
  try {
    const raw = await AsyncStorage.getItem(STORAGE_KEY)
    const stored = raw ? (JSON.parse(raw) as Partial<PersonalizerFlags>) : {}
    return {
      ...DEFAULT_FLAGS,
      ...stored,
      ...(inMemoryOverride || {}),
    }
  } catch {
    return { ...DEFAULT_FLAGS, ...(inMemoryOverride || {}) }
  }
}

const mockKpiValue = (kind: DashboardKpi['kind']): Omit<DashboardKpi, 'kind'> => {
  switch (kind) {
    case 'liveConversations':
      return { value: 2, unit: 'count', delta: 5, period: '24h' }
    case 'frtP50':
      return { value: 1.2, unit: 's', delta: -12, period: '7d', target: 2 }
    case 'frtP90':
      return { value: 2.4, unit: 's', delta: -8, period: '7d', target: 3 }
    case 'resolutionRate':
      return { value: 78, unit: '%', delta: 3, period: '7d' }
    case 'csat':
      return { value: 92, unit: '%', delta: 1, period: '7d' }
    case 'deflection':
      return { value: 55, unit: '%', delta: 4, period: '7d' }
    case 'vipQueue':
      return { value: 1, unit: 'count', delta: 0, period: '24h' }
    default:
      return { value: 0, unit: 'count' }
  }
}

export const selectKpis = (flags: PersonalizerFlags): DashboardKpi[] => {
  // Always include liveConversations, frtP50
  const base: DashboardKpi['kind'][] = ['liveConversations', 'frtP50']
  // Prefer CSAT if enabled else Deflection
  const preference: DashboardKpi['kind'] = flags.csatEnabled ? 'csat' : 'deflection'
  // Fill remaining with resolutionRate; frtP90 optional
  const chosen = [...base, preference, 'resolutionRate'] as DashboardKpi['kind'][]
  return chosen.map((kind) => ({ kind, ...mockKpiValue(kind) }))
}

export const selectAlerts = (flags: PersonalizerFlags): AlertItem[] => {
  if (flags.slaDefined) {
    return [
      { id: 'al-sla', kind: 'slaRisk', count: 1, buckets: [{ label: 'near breach', count: 1 }], deeplink: 'app://conversations?filter=slaRisk' },
      { id: 'al-unassigned', kind: 'unassigned', count: 3, deeplink: 'app://conversations?filter=unassigned' },
    ]
  }
  return [
    { id: 'al-urgent', kind: 'urgentBacklog', count: 2, buckets: [{ label: '0–15m', count: 1 }, { label: '15–60m', count: 1 }], deeplink: 'app://conversations?filter=urgent' },
    { id: 'al-unassigned', kind: 'unassigned', count: 3, deeplink: 'app://conversations?filter=unassigned' },
  ]
}

export const selectQuickActions = (flags: PersonalizerFlags): { label: string; deeplink: string; testID: string }[] => {
  const actions = [
    { label: 'Review Urgent', deeplink: 'app://conversations?filter=urgent', testID: 'qa-urgent' },
    { label: 'Waiting > 30m', deeplink: 'app://conversations?filter=waiting30', testID: 'qa-waiting30' },
    { label: 'Unassigned', deeplink: 'app://conversations?filter=unassigned', testID: 'qa-unassigned' },
  ]
  if (!flags.channelsConnected) {
    actions.push({ label: 'Connect Channels', deeplink: 'app://channels', testID: 'qa-connect-channels' })
  }
  return actions
}

export const selectIndustryPack = (flags: PersonalizerFlags): IndustryPackId => {
  return flags.industryPack || 'neutral'
}


