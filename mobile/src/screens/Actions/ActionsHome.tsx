import React from 'react'
import { SafeAreaView, View, Text, TouchableOpacity, ScrollView, TextInput, Alert, FlatList } from 'react-native'
import { useSafeAreaInsets } from 'react-native-safe-area-context'
import { useTheme } from '../../providers/ThemeProvider'
import { track } from '../../lib/analytics'
import { ActionSpec, AllowRule, ActionRun } from '../../types/actions'
import { ActionRow } from '../../components/actions/ActionRow'
import { AllowRuleRow } from '../../components/actions/AllowRuleRow'
import { RunRow } from '../../components/actions/RunRow'
import ListSkeleton from '../../components/actions/ListSkeleton'
import OfflineBanner from '../../components/dashboard/OfflineBanner'

const mkAction = (id: string, name: string, category: ActionSpec['category'], kind: ActionSpec['kind'], risk: ActionSpec['riskLevel']): ActionSpec => ({
  id, name, category, kind, riskLevel: risk, version: 1, summary: `Demo action: ${name}`, params: [], effects: ['UI-only']
})

const demoActions: ActionSpec[] = [
  mkAction('conv.reply', 'Reply to Conversation', 'conversations', 'create', 'low'),
  mkAction('conv.close', 'Close Conversation', 'conversations', 'update', 'medium'),
  mkAction('crm.update', 'Update Contact', 'crm', 'update', 'medium'),
  mkAction('orders.refund', 'Issue Refund', 'orders', 'trigger', 'high'),
  mkAction('knowledge.create', 'Create FAQ', 'knowledge', 'create', 'low'),
  mkAction('automations.rule', 'Create Rule', 'automations', 'create', 'medium'),
  mkAction('settings.toggle', 'Toggle Setting', 'settings', 'update', 'low'),
  mkAction('analytics.export', 'Export Report', 'analytics', 'export', 'low'),
]

const demoAllowRules: AllowRule[] = [
  { actionId: 'orders.refund', requireApproval: true, approverRole: 'owner', guard: { maxAmount: 200 }, rateLimit: { count: 3, perSeconds: 3600 } },
  { actionId: 'conv.reply', agentIds: ['ag-1','ag-2'], intents: ['order','billing'] },
]

const now = () => Date.now()
const demoRuns: ActionRun[] = [
  { id: 'run-1', actionId: 'conv.reply', requestedBy: 'ai', createdAt: now() - 120000, state: 'completed', params: { id: 'c-1' }, result: { ok: true, preview: 'Would send a reply', affectedRecords: 1 } },
  { id: 'run-2', actionId: 'orders.refund', requestedBy: 'agent', approvedBy: 'owner', createdAt: now() - 3600000, state: 'approved', params: { amount: 49 }, result: { ok: true, preview: 'Would refund $49' } },
  { id: 'run-3', actionId: 'analytics.export', requestedBy: 'system', createdAt: now() - 7200000, state: 'failed', params: {}, result: { ok: false, preview: 'Export failed', errors: ['Network'] } },
]

const groupBy = <T, K extends string | number>(arr: T[], key: (t: T) => K): Record<K, T[]> => arr.reduce((acc: any, it) => { const k = key(it); (acc[k] ||= []).push(it); return acc }, {})

const ActionsHome: React.FC = () => {
  const { theme } = useTheme()
  const insets = useSafeAreaInsets()

  const [tab, setTab] = React.useState<'catalog'|'allow'|'monitor'>('catalog')
  const [search, setSearch] = React.useState<string>('')
  const [rules, setRules] = React.useState<AllowRule[]>(demoAllowRules)
  const [runs, setRuns] = React.useState<ActionRun[]>(demoRuns)
  const [stateFilter, setStateFilter] = React.useState<ActionRun['state'] | 'all'>('all')
  const [actorFilter, setActorFilter] = React.useState<ActionRun['requestedBy'] | 'all'>('all')
  const [loading, setLoading] = React.useState<boolean>(false)
  const [offline, setOffline] = React.useState<boolean>(false)
  const [queued, setQueued] = React.useState<{ rules: Record<string, boolean> }>({ rules: {} })

  React.useEffect(() => { track('actions.view') }, [])

  React.useEffect(() => {
    const h = setTimeout(() => { if (search.trim()) track('actions.catalog.search', { q: search.trim() }) }, 300)
    return () => clearTimeout(h)
  }, [search])

  const filtered = React.useMemo(() => {
    const q = search.trim().toLowerCase()
    return demoActions.filter(a => !q || a.name.toLowerCase().includes(q) || a.id.includes(q) || a.category.includes(q))
  }, [search])

  const grouped = React.useMemo(() => groupBy(filtered, a => a.category), [filtered])

  const filteredRuns = React.useMemo(() => runs.filter(r => (stateFilter === 'all' || r.state === stateFilter) && (actorFilter === 'all' || r.requestedBy === actorFilter)), [runs, stateFilter, actorFilter])

  return (
    <SafeAreaView style={{ flex: 1, backgroundColor: theme.color.background }}>
      <ScrollView style={{ flex: 1 }}>
        {/* Header */}
        <View style={{ paddingHorizontal: 24, paddingTop: insets.top + 12, paddingBottom: 12 }}>
          <View style={{ flexDirection: 'row', alignItems: 'center', justifyContent: 'space-between' }}>
            <Text style={{ color: theme.color.foreground, fontSize: 28, fontWeight: '700' }}>MCP / Action Pack</Text>
            <View style={{ flexDirection: 'row', gap: 8 }}>
              <TouchableOpacity onPress={() => (require('@react-navigation/native') as any).useNavigation().navigate('Actions', { screen: 'ImportExport', params: { actions: demoActions, rules, packsApplied: [] } })} accessibilityLabel="Import/Export" accessibilityRole="button" style={{ paddingHorizontal: 12, paddingVertical: 8, borderRadius: 12, borderWidth: 1, borderColor: theme.color.border }}>
                <Text style={{ color: theme.color.cardForeground, fontWeight: '600' }}>Import/Export</Text>
              </TouchableOpacity>
              <TouchableOpacity onPress={() => Alert.alert('Docs', 'UI-only stub')} accessibilityLabel="Docs" accessibilityRole="button" style={{ paddingHorizontal: 12, paddingVertical: 8, borderRadius: 12, borderWidth: 1, borderColor: theme.color.border }}>
                <Text style={{ color: theme.color.cardForeground, fontWeight: '600' }}>Docs</Text>
              </TouchableOpacity>
              <TouchableOpacity onPress={() => (require('@react-navigation/native') as any).useNavigation().navigate('Actions', { screen: 'Secrets' })} accessibilityLabel="Secrets" accessibilityRole="button" style={{ paddingHorizontal: 12, paddingVertical: 8, borderRadius: 12, borderWidth: 1, borderColor: theme.color.border }}>
                <Text style={{ color: theme.color.cardForeground, fontWeight: '600' }}>Secrets</Text>
              </TouchableOpacity>
              <TouchableOpacity onPress={() => (require('@react-navigation/native') as any).useNavigation().navigate('Actions', { screen: 'CapabilityPacks', params: { rules, onChangeRules: (next: any) => {} } })} accessibilityLabel="Packs" accessibilityRole="button" style={{ paddingHorizontal: 12, paddingVertical: 8, borderRadius: 12, borderWidth: 1, borderColor: theme.color.border }}>
                <Text style={{ color: theme.color.cardForeground, fontWeight: '600' }}>Packs</Text>
              </TouchableOpacity>
              <TouchableOpacity onPress={() => (require('@react-navigation/native') as any).useNavigation().navigate('Actions', { screen: 'Approvals', params: { runs, onUpdate: (next: any) => {} } })} accessibilityLabel="Approvals" accessibilityRole="button" style={{ paddingHorizontal: 12, paddingVertical: 8, borderRadius: 12, borderWidth: 1, borderColor: theme.color.border }}>
                <Text style={{ color: theme.color.cardForeground, fontWeight: '600' }}>Approvals</Text>
              </TouchableOpacity>
              <TouchableOpacity onPress={() => setOffline(v => !v)} accessibilityLabel="Toggle offline" accessibilityRole="button" style={{ paddingHorizontal: 12, paddingVertical: 8, borderRadius: 12, borderWidth: 1, borderColor: offline ? theme.color.primary : theme.color.border }}>
                <Text style={{ color: offline ? theme.color.primary : theme.color.cardForeground, fontWeight: '600' }}>{offline ? 'Offline' : 'Go Offline'}</Text>
              </TouchableOpacity>
            </View>
          </View>
          {/* Tabs */}
          <View style={{ flexDirection: 'row', gap: 8, marginTop: 12 }}>
            {([
              { k: 'catalog', label: 'Catalog' },
              { k: 'allow', label: 'Allowlist' },
              { k: 'monitor', label: 'Monitor' },
            ] as const).map(t => (
              <TouchableOpacity key={t.k} onPress={() => setTab(t.k as any)} accessibilityLabel={t.label} accessibilityRole="button" style={{ paddingHorizontal: 12, paddingVertical: 8, borderRadius: 12, borderWidth: 1, borderColor: tab === t.k ? theme.color.primary : theme.color.border }}>
                <Text style={{ color: tab === t.k ? theme.color.primary : theme.color.cardForeground, fontWeight: '600' }}>{t.label}</Text>
              </TouchableOpacity>
            ))}
          </View>
        </View>

        {/* Body */}
        {offline && (
          <View style={{ paddingHorizontal: 24 }}>
            <OfflineBanner visible testID="act-offline" />
          </View>
        )}
        {tab === 'catalog' && (
          <View style={{ paddingHorizontal: 24, gap: 12, marginBottom: 24 }}>
            <View style={{ borderWidth: 1, borderColor: theme.color.border, borderRadius: 12, backgroundColor: theme.color.card }}>
              <TextInput value={search} onChangeText={setSearch} placeholder="Search actions" placeholderTextColor={theme.color.placeholder} style={{ paddingHorizontal: 12, paddingVertical: 10, color: theme.color.cardForeground }} />
            </View>
            {loading ? (
              <ListSkeleton rows={8} />
            ) : (
              <FlatList
                data={Object.entries(grouped)}
                keyExtractor={([cat]) => cat}
                renderItem={({ item: [cat, items] }) => (
                  <View>
                    <Text style={{ color: theme.color.mutedForeground, fontWeight: '700', marginBottom: 8 }}>{String(cat).toUpperCase()}</Text>
                    <View style={{ gap: 8 }}>
                      {(items as ActionSpec[]).map(spec => (
                        <ActionRow key={spec.id} spec={spec} onOpen={() => Alert.alert('Action', `${spec.name} (UI-only)`)} />
                      ))}
                    </View>
                  </View>
                )}
              />
            )}
          </View>
        )}

        {tab === 'allow' && (
          <View style={{ paddingHorizontal: 24, gap: 12, marginBottom: 24 }}>
            <View style={{ flexDirection: 'row', justifyContent: 'flex-end' }}>
              <TouchableOpacity onPress={() => {
                if (offline) { const id = `tmp-${Date.now()}`; setQueued(q => ({ rules: { ...q.rules, [id]: true } })); setTimeout(() => setQueued(q => ({ rules: {} })), 1500); return }
                setRules(r => [{ actionId: 'analytics.export', rateLimit: { count: 5, perSeconds: 3600 } }, ...r])
              }} accessibilityLabel="New Rule" accessibilityRole="button" style={{ paddingHorizontal: 12, paddingVertical: 8, borderRadius: 12, backgroundColor: theme.color.primary }}>
                <Text style={{ color: '#fff', fontWeight: '700' }}>New Rule</Text>
              </TouchableOpacity>
            </View>
            <FlatList
              data={rules}
              keyExtractor={(_, i) => `rule-${i}`}
              renderItem={({ item: rule, index }) => (
                <View>
                  <AllowRuleRow rule={rule} onEdit={() => Alert.alert('Edit', 'UI-only')} onToggle={() => {}} onDelete={() => setRules(arr => arr.filter((_, i) => i !== index))} />
                </View>
              )}
            />
          </View>
        )}

        {tab === 'monitor' && (
          <View style={{ paddingHorizontal: 24, gap: 12, marginBottom: 24 }}>
            <View style={{ flexDirection: 'row', gap: 8 }}>
              {(['all','queued','approved','running','completed','failed','rejected','rate_limited'] as const).map(s => (
                <TouchableOpacity key={s} onPress={() => setStateFilter(s)} accessibilityLabel={`state ${s}`} accessibilityRole="button" style={{ paddingHorizontal: 10, paddingVertical: 6, borderRadius: 10, borderWidth: 1, borderColor: stateFilter === s ? theme.color.primary : theme.color.border }}>
                  <Text style={{ color: stateFilter === s ? theme.color.primary : theme.color.cardForeground, fontSize: 12 }}>{s}</Text>
                </TouchableOpacity>
              ))}
            </View>
            <FlatList
              data={filteredRuns}
              keyExtractor={(run) => run.id}
              renderItem={({ item }) => (
                <RunRow run={item} onOpen={() => Alert.alert('Run', item.id)} />
              )}
            />
          </View>
        )}
      </ScrollView>
    </SafeAreaView>
  )
}

export default ActionsHome


