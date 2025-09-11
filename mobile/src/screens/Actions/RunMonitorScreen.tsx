import React from 'react'
import { SafeAreaView, View, Text, TouchableOpacity, ScrollView, Alert } from 'react-native'
import { useSafeAreaInsets } from 'react-native-safe-area-context'
import { useTheme } from '../../providers/ThemeProvider'
import { useNavigation, useRoute } from '@react-navigation/native'
import { ActionRun } from '../../types/actions'
import RunRow from '../../components/actions/RunRow'
import { track } from '../../lib/analytics'

type Params = { runs?: ActionRun[]; onUpdate?: (next: ActionRun[]) => void }

const riskFromAction = (actionId: string): 'low'|'medium'|'high' => {
  if (actionId.includes('refund')) return 'high'
  if (actionId.includes('close') || actionId.includes('update')) return 'medium'
  return 'low'
}

const now = () => Date.now()
const demoRuns: ActionRun[] = [
  { id: 'run-m1', actionId: 'conv.reply', requestedBy: 'ai', createdAt: now() - 12_000, state: 'completed', params: { id: 'c-2' }, result: { ok: true, preview: 'Would send a reply' } },
  { id: 'run-m2', actionId: 'orders.refund', requestedBy: 'agent', approvedBy: 'owner', createdAt: now() - 62_000, state: 'approved', params: { amount: 99 }, result: { ok: true, preview: 'Would refund $99' } },
  { id: 'run-m3', actionId: 'analytics.export', requestedBy: 'system', createdAt: now() - 3_600_000, state: 'failed', params: {}, result: { ok: false, preview: 'Export failed', errors: ['Timeout'] } },
]

const RunMonitorScreen: React.FC = () => {
  const { theme } = useTheme()
  const insets = useSafeAreaInsets()
  const navigation = useNavigation<any>()
  const route = useRoute<any>()
  const { runs: initialRuns = demoRuns, onUpdate } = (route.params || {}) as Params

  const [runs, setRuns] = React.useState<ActionRun[]>(initialRuns)
  const [stateFilter, setStateFilter] = React.useState<ActionRun['state'] | 'all'>('all')
  const [actorFilter, setActorFilter] = React.useState<ActionRun['requestedBy'] | 'all'>('all')
  const [riskFilter, setRiskFilter] = React.useState<'all'|'low'|'medium'|'high'>('all')

  const filtered = React.useMemo(() => runs.filter(r => (stateFilter === 'all' || r.state === stateFilter) && (actorFilter === 'all' || r.requestedBy === actorFilter) && (riskFilter === 'all' || riskFromAction(r.actionId) === riskFilter)), [runs, stateFilter, actorFilter, riskFilter])

  const createIncident = () => {
    Alert.alert('Incident created', 'UI-only: linked to Security Audit. Open Security?', [
      { text: 'Cancel', style: 'cancel' },
      { text: 'Open', onPress: () => navigation.navigate('Security') },
    ])
  }

  return (
    <SafeAreaView style={{ flex: 1, backgroundColor: theme.color.background }}>
      <ScrollView contentContainerStyle={{ paddingBottom: 24 }}>
        {/* Header */}
        <View style={{ paddingHorizontal: 24, paddingTop: insets.top + 12, paddingBottom: 12 }}>
          <View style={{ flexDirection: 'row', alignItems: 'center', justifyContent: 'space-between' }}>
            <TouchableOpacity onPress={() => navigation.goBack()} accessibilityLabel="Back" accessibilityRole="button" style={{ paddingHorizontal: 12, paddingVertical: 8, borderRadius: 10, borderWidth: 1, borderColor: theme.color.border }}>
              <Text style={{ color: theme.color.primary, fontWeight: '700' }}>{'< Back'}</Text>
            </TouchableOpacity>
            <Text style={{ color: theme.color.foreground, fontSize: 20, fontWeight: '700' }}>Run Monitor</Text>
            <TouchableOpacity onPress={createIncident} accessibilityLabel="Create Incident" accessibilityRole="button" style={{ paddingHorizontal: 12, paddingVertical: 8, borderRadius: 10, borderWidth: 1, borderColor: theme.color.border }}>
              <Text style={{ color: theme.color.cardForeground, fontWeight: '600' }}>Create Incident</Text>
            </TouchableOpacity>
          </View>
        </View>

        {/* Filters */}
        <View style={{ paddingHorizontal: 24, gap: 8 }}>
          <View style={{ flexDirection: 'row', flexWrap: 'wrap', gap: 8 }}>
            {(['all','queued','approved','running','completed','failed','rejected','rate_limited'] as const).map(s => (
              <TouchableOpacity key={s} onPress={() => setStateFilter(s)} accessibilityLabel={`state ${s}`} accessibilityRole="button" style={{ paddingHorizontal: 10, paddingVertical: 6, borderRadius: 10, borderWidth: 1, borderColor: stateFilter === s ? theme.color.primary : theme.color.border }}>
                <Text style={{ color: stateFilter === s ? theme.color.primary : theme.color.cardForeground, fontSize: 12 }}>{s}</Text>
              </TouchableOpacity>
            ))}
          </View>
          <View style={{ flexDirection: 'row', flexWrap: 'wrap', gap: 8 }}>
            {(['all','ai','agent','system'] as const).map(a => (
              <TouchableOpacity key={a} onPress={() => setActorFilter(a)} accessibilityLabel={`actor ${a}`} accessibilityRole="button" style={{ paddingHorizontal: 10, paddingVertical: 6, borderRadius: 10, borderWidth: 1, borderColor: actorFilter === a ? theme.color.primary : theme.color.border }}>
                <Text style={{ color: actorFilter === a ? theme.color.primary : theme.color.cardForeground, fontSize: 12 }}>{a}</Text>
              </TouchableOpacity>
            ))}
          </View>
          <View style={{ flexDirection: 'row', flexWrap: 'wrap', gap: 8, marginBottom: 8 }}>
            {(['all','low','medium','high'] as const).map(r => (
              <TouchableOpacity key={r} onPress={() => setRiskFilter(r)} accessibilityLabel={`risk ${r}`} accessibilityRole="button" style={{ paddingHorizontal: 10, paddingVertical: 6, borderRadius: 10, borderWidth: 1, borderColor: riskFilter === r ? theme.color.primary : theme.color.border }}>
                <Text style={{ color: riskFilter === r ? theme.color.primary : theme.color.cardForeground, fontSize: 12 }}>{r}</Text>
              </TouchableOpacity>
            ))}
          </View>
        </View>

        {/* List */}
        <View style={{ paddingHorizontal: 24, gap: 8, marginTop: 8 }}>
          {filtered.map((run) => (
            <RunRow key={run.id} run={run} onOpen={() => { try { track('actions.monitor.open', { runId: run.id }) } catch {}; navigation.navigate('Actions', { screen: 'RunDetail', params: { run } }) }} />
          ))}
        </View>
      </ScrollView>
    </SafeAreaView>
  )
}

export default RunMonitorScreen


