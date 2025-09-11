import React from 'react'
import { SafeAreaView, View, Text, TouchableOpacity, FlatList, RefreshControl, Alert, ScrollView, Switch } from 'react-native'
import { useNavigation } from '@react-navigation/native'
import { useSafeAreaInsets } from 'react-native-safe-area-context'
import { useTheme } from '../../providers/ThemeProvider'
import { track } from '../../lib/analytics'
import OfflineBanner from '../../components/dashboard/OfflineBanner'
import SyncCenterSheet from '../../components/dashboard/SyncCenterSheet'
import RuleCard from '../../components/automations/RuleCard'
import ResponderCard from '../../components/automations/ResponderCard'
import SimulatorPanel from '../../components/automations/SimulatorPanel'
import { Action, AutoResponder, BusinessCalendar, BusinessHoursDay, Condition, Rule, SlaPolicy } from '../../types/automations'

const genRules = (): Rule[] => Array.from({ length: 5 }, (_, i) => ({
  id: `r-${i+1}`,
  name: i === 0 ? 'VIP fast route' : i === 1 ? 'Order intent → billing skill' : `Rule ${i+1}`,
  when: [{ key: 'intent', op: 'is', value: i === 1 ? 'order' : 'vip' } as Condition],
  then: [{ key: 'routeToSkill', params: { skill: i === 1 ? 'billing' : 'priority' } } as Action],
  enabled: true,
  order: i + 1,
}))

const genCalendar = (): BusinessCalendar => ({
  timezone: 'UTC',
  days: Array.from({ length: 7 }, (_, d) => ({ day: d as any, open: d >= 1 && d <= 5, ranges: d >= 1 && d <= 5 ? [{ start: '09:00', end: '17:00' }] : [] } as BusinessHoursDay)),
  holidays: [],
})

const genPolicy = (): SlaPolicy => ({ id: 'sla-1', name: 'Default SLA', pauseOutsideHours: true, targets: [
  { priority: 'vip', frtP50Sec: 60, frtP90Sec: 180 },
  { priority: 'high', frtP50Sec: 120, frtP90Sec: 300 },
  { priority: 'normal', frtP50Sec: 300, frtP90Sec: 1200 },
] })

const genResponders = (): AutoResponder[] => ([
  { id: 'ar-1', name: 'After-hours auto-reply', active: true, message: 'We are currently offline. We will get back to you during business hours.', channels: ['whatsapp', 'web'], onlyOutsideHours: true },
])

const AutomationsHome: React.FC = () => {
  const { theme } = useTheme()
  const insets = useSafeAreaInsets()
  const navigation = useNavigation<any>()

  const [refreshing, setRefreshing] = React.useState(false)
  const [rules, setRules] = React.useState<Rule[]>(() => genRules())
  const [calendar, setCalendar] = React.useState<BusinessCalendar>(() => genCalendar())
  const [policy, setPolicy] = React.useState<SlaPolicy>(() => genPolicy())
  const [responders, setResponders] = React.useState<AutoResponder[]>(() => genResponders())
  const [offline, setOffline] = React.useState(false)
  const [queued, setQueued] = React.useState<{ rules: Record<string, boolean>; responder: Record<string, boolean> }>({ rules: {}, responder: {} })
  const [syncOpen, setSyncOpen] = React.useState(false)

  React.useEffect(() => { track('automations.view') }, [])

  const onRefresh = () => {
    setRefreshing(true)
    setTimeout(() => {
      setRules(genRules().reverse())
      setResponders(genResponders())
      setRefreshing(false)
    }, 500)
  }

  const reorder = (id: string, dir: 'up'|'down') => {
    setRules((arr) => {
      const idx = arr.findIndex((r) => r.id === id)
      if (idx < 0) return arr
      const j = dir === 'up' ? idx - 1 : idx + 1
      if (j < 0 || j >= arr.length) return arr
      const next = arr.slice()
      const [it] = next.splice(idx, 1)
      next.splice(j, 0, it)
      if (offline) {
        setQueued((q) => ({ ...q, rules: { ...q.rules, [id]: true } }))
        setTimeout(() => setQueued((q) => ({ ...q, rules: { ...q.rules, [id]: false } })), 1500)
      }
      track('rule.reorder')
      return next.map((r, i) => ({ ...r, order: i + 1 }))
    })
  }

  return (
    <SafeAreaView style={{ flex: 1, backgroundColor: theme.color.background }}>
      <ScrollView style={{ flex: 1 }} refreshControl={<RefreshControl refreshing={refreshing} onRefresh={onRefresh} />}>
        {/* Header */}
        <View style={{ paddingHorizontal: 24, paddingTop: insets.top + 12, paddingBottom: 12 }}>
          <View style={{ flexDirection: 'row', alignItems: 'center', justifyContent: 'space-between' }}>
            <Text style={{ color: theme.color.foreground, fontSize: 28, fontWeight: '700' }}>Automations & SLAs</Text>
            <View style={{ flexDirection: 'row', gap: 8 }}>
              <TouchableOpacity onPress={() => setOffline((v) => !v)} accessibilityLabel="Toggle Offline" accessibilityRole="button" style={{ paddingHorizontal: 12, paddingVertical: 8, borderRadius: 12, borderWidth: 1, borderColor: theme.color.border }}>
                <Text style={{ color: theme.color.cardForeground, fontWeight: '600' }}>{offline ? 'Go Online' : 'Go Offline'}</Text>
              </TouchableOpacity>
              <TouchableOpacity onPress={() => setSyncOpen(true)} accessibilityLabel="Sync Center" accessibilityRole="button" style={{ paddingHorizontal: 12, paddingVertical: 8, borderRadius: 12, borderWidth: 1, borderColor: theme.color.border }}>
                <Text style={{ color: theme.color.mutedForeground, fontWeight: '600' }}>Sync</Text>
              </TouchableOpacity>
              <TouchableOpacity onPress={() => navigation.navigate('Automations', { screen: 'ImportExportAudit', params: { tab: 'audit', rules, calendar, policy, responders, audit: [] } })} accessibilityLabel="Audit Log" accessibilityRole="button" style={{ paddingHorizontal: 12, paddingVertical: 8, borderRadius: 12, borderWidth: 1, borderColor: theme.color.border }}>
                <Text style={{ color: theme.color.cardForeground, fontWeight: '600' }}>Audit Log</Text>
              </TouchableOpacity>
              <TouchableOpacity onPress={() => navigation.navigate('Automations', { screen: 'ImportExportAudit', params: { tab: 'export', rules, calendar, policy, responders, audit: [] } })} accessibilityLabel="Import Export" accessibilityRole="button" style={{ paddingHorizontal: 12, paddingVertical: 8, borderRadius: 12, borderWidth: 1, borderColor: theme.color.border }}>
                <Text style={{ color: theme.color.cardForeground, fontWeight: '600' }}>Import/Export</Text>
              </TouchableOpacity>
              <TouchableOpacity onPress={() => Alert.alert('Docs', 'UI-only stub')} accessibilityLabel="Docs" accessibilityRole="button" style={{ paddingHorizontal: 12, paddingVertical: 8, borderRadius: 12, borderWidth: 1, borderColor: theme.color.border }}>
                <Text style={{ color: theme.color.cardForeground, fontWeight: '600' }}>Docs</Text>
              </TouchableOpacity>
            </View>
          </View>
        </View>

        <View style={{ paddingHorizontal: 24, gap: 16 }}>
          {offline && (
            <OfflineBanner visible testID="auto-offline" />
          )}
          {/* Rules */}
          <View>
            <View style={{ flexDirection: 'row', alignItems: 'center', justifyContent: 'space-between', marginBottom: 8 }}>
              <Text style={{ color: theme.color.cardForeground, fontWeight: '700' }}>Rules</Text>
              <TouchableOpacity onPress={() => navigation.navigate('Automations', { screen: 'RuleBuilder', params: { currentMaxOrder: rules.length, onSave: (rule: Rule) => setRules((arr) => [rule, ...arr]) } })} accessibilityLabel="New Rule" accessibilityRole="button" style={{ paddingHorizontal: 12, paddingVertical: 8, borderRadius: 10, borderWidth: 1, borderColor: theme.color.border }}>
                <Text style={{ color: theme.color.primary, fontWeight: '700' }}>New Rule</Text>
              </TouchableOpacity>
            </View>
            <View style={{ gap: 8 }}>
              {rules.map((r) => (
                <RuleCard key={r.id} rule={r} queued={!!queued.rules[r.id]} onToggle={() => {
                  setRules((arr) => arr.map((x) => x.id === r.id ? { ...x, enabled: !x.enabled } : x))
                  if (offline) {
                    setQueued((q) => ({ ...q, rules: { ...q.rules, [r.id]: true } }))
                    setTimeout(() => setQueued((q) => ({ ...q, rules: { ...q.rules, [r.id]: false } })), 1500)
                  }
                }} onEdit={() => navigation.navigate('Automations', { screen: 'RuleBuilder', params: { rule: r, onSave: (nr: Rule) => setRules((arr) => arr.map((x) => x.id === nr.id ? nr : x)) } })} onReorder={(dir) => reorder(r.id, dir)} testID={`auto-rule-row-${r.id}`} />
              ))}
            </View>
          </View>

          {/* Business Hours */}
          <View>
            <View style={{ flexDirection: 'row', alignItems: 'center', justifyContent: 'space-between', marginBottom: 8 }}>
              <Text style={{ color: theme.color.cardForeground, fontWeight: '700' }}>Business Hours</Text>
              <TouchableOpacity onPress={() => navigation.navigate('Automations', { screen: 'BusinessHours', params: { calendar, pauseOutsideHours: policy.pauseOutsideHours, onApply: (c: BusinessCalendar, opts: { syncToSla: boolean }) => { setCalendar(c); if (opts.syncToSla) setPolicy((p) => ({ ...p, pauseOutsideHours: true })) } } })} accessibilityLabel="Edit Business Hours" accessibilityRole="button" style={{ paddingHorizontal: 12, paddingVertical: 8, borderRadius: 10, borderWidth: 1, borderColor: theme.color.border }}>
                <Text style={{ color: theme.color.cardForeground, fontWeight: '600' }}>Edit</Text>
              </TouchableOpacity>
            </View>
            <View style={{ flexDirection: 'row', flexWrap: 'wrap', gap: 8 }}>
              <View style={{ paddingHorizontal: 8, paddingVertical: 6, borderRadius: 10, borderWidth: 1, borderColor: theme.color.border }}>
                <Text style={{ color: theme.color.mutedForeground }}>TZ: {calendar.timezone}</Text>
              </View>
              <View style={{ paddingHorizontal: 8, paddingVertical: 6, borderRadius: 10, borderWidth: 1, borderColor: theme.color.border }}>
                <Text style={{ color: theme.color.mutedForeground }}>Open days: {calendar.days.filter(d => d.open).length}</Text>
              </View>
            </View>
          </View>

          {/* SLA Policy */}
          <View>
            <View style={{ flexDirection: 'row', alignItems: 'center', justifyContent: 'space-between', marginBottom: 8 }}>
              <Text style={{ color: theme.color.cardForeground, fontWeight: '700' }}>SLA Policy</Text>
              <TouchableOpacity onPress={() => navigation.navigate('Automations', { screen: 'SlaEditor', params: { policy, onApply: (p: SlaPolicy) => setPolicy(p) } })} accessibilityLabel="Edit SLA" accessibilityRole="button" style={{ paddingHorizontal: 12, paddingVertical: 8, borderRadius: 10, borderWidth: 1, borderColor: theme.color.border }}>
                <Text style={{ color: theme.color.cardForeground, fontWeight: '600' }}>Edit</Text>
              </TouchableOpacity>
            </View>
            <View style={{ flexDirection: 'row', flexWrap: 'wrap', gap: 8, alignItems: 'center' }}>
              {policy.targets.slice(0, 3).map((t, idx) => (
                <View key={idx} style={{ paddingHorizontal: 8, paddingVertical: 6, borderRadius: 10, borderWidth: 1, borderColor: theme.color.border }}>
                  <Text style={{ color: theme.color.mutedForeground }}>{t.priority.toUpperCase()} • P50 {Math.round(t.frtP50Sec/60)}m • P90 {Math.round(t.frtP90Sec/60)}m</Text>
                </View>
              ))}
              <View style={{ flexDirection: 'row', alignItems: 'center', gap: 8, marginLeft: 8 }}>
                <Text style={{ color: theme.color.mutedForeground }}>Pause outside hours</Text>
                <Switch value={policy.pauseOutsideHours} onValueChange={(v) => setPolicy((p) => ({ ...p, pauseOutsideHours: v }))} accessibilityLabel="Pause outside hours" />
              </View>
            </View>
          </View>

          {/* Auto-responders */}
          <View>
            <View style={{ flexDirection: 'row', alignItems: 'center', justifyContent: 'space-between', marginBottom: 8 }}>
              <Text style={{ color: theme.color.cardForeground, fontWeight: '700' }}>Auto-responders</Text>
              <TouchableOpacity onPress={() => navigation.navigate('Automations', { screen: 'AutoResponders', params: { responders, onApply: (arr: AutoResponder[]) => setResponders(arr) } })} accessibilityLabel="Manage Auto-responders" accessibilityRole="button" style={{ paddingHorizontal: 12, paddingVertical: 8, borderRadius: 10, borderWidth: 1, borderColor: theme.color.border }}>
                <Text style={{ color: theme.color.primary, fontWeight: '700' }}>Manage</Text>
              </TouchableOpacity>
            </View>
            <View style={{ gap: 8 }}>
              {responders.map((r) => (
                <ResponderCard key={r.id} res={r} queued={!!queued.responder[r.id]} onToggle={() => {
                  setResponders((arr) => arr.map((x) => x.id === r.id ? { ...x, active: !x.active } : x))
                  if (offline) {
                    setQueued((q) => ({ ...q, responder: { ...q.responder, [r.id]: true } }))
                    setTimeout(() => setQueued((q) => ({ ...q, responder: { ...q.responder, [r.id]: false } })), 1500)
                  }
                }} onEdit={() => navigation.navigate('Automations', { screen: 'AutoResponders', params: { responders, onApply: (arr: AutoResponder[]) => setResponders(arr) } })} />
              ))}
            </View>
          </View>

          {/* Simulator */}
          <View>
            <SimulatorPanel input={{ when: [{ key: 'vip', op: 'true' }], nowIso: new Date().toISOString() }} onRun={() => navigation.navigate('Automations', { screen: 'Simulator', params: { rules, calendar, policy, currentMaxOrder: rules.length } })} />
          </View>
        </View>

        <View style={{ height: 24 }} />
      </ScrollView>
      <SyncCenterSheet
        visible={syncOpen}
        onClose={() => setSyncOpen(false)}
        lastSyncAt={new Date().toLocaleString()}
        queuedCount={Object.values(queued.rules).filter(Boolean).length + Object.values(queued.responder).filter(Boolean).length}
        onRetryAll={() => setSyncOpen(false)}
      />
    </SafeAreaView>
  )
}

export default AutomationsHome


