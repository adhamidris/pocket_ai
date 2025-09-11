import React from 'react'
import { SafeAreaView, View, TextInput, FlatList, RefreshControl, TouchableOpacity, Text } from 'react-native'
import { useTheme } from '../../providers/ThemeProvider'
import { useSafeAreaInsets } from 'react-native-safe-area-context'
import { useRoute, useNavigation } from '@react-navigation/native'
import { ConversationSummary, Priority, SLAState, Channel } from '../../types/conversations'
import FilterBar from '../../components/conversations/FilterBar'
import SortBar from '../../components/conversations/SortBar'
import ConversationListItem from '../../components/conversations/ConversationListItem'
import { Modal } from 'react-native'
import AgentMiniCard from '../../components/agents/AgentMiniCard'
import { AnyAgent, AgentKind, AgentStatus, SkillTag } from '../../types/agents'
import ListSkeleton from '../../components/conversations/ListSkeleton'
import EmptyState from '../../components/conversations/EmptyState'
import { track } from '../../lib/analytics'

const priorities: Priority[] = ['low', 'normal', 'high', 'vip']

const rand = (min: number, max: number) => Math.floor(Math.random() * (max - min + 1)) + min
const pick = <T,>(arr: T[]): T => arr[rand(0, arr.length - 1)]

const genDemo = (): ConversationSummary[] => {
  const names = ['Sarah Johnson', 'Michael Chen', 'Emma Rodriguez', 'David Park', 'Aisha Khan', 'Omar Ali', 'Liam Smith', 'Noah Brown', 'Ava Davis', 'Sophia Wilson']
  const tagsPool = ['Billing', 'VIP', 'Technical', 'Order', 'Returns', 'Onboarding', 'Shipping']
  const channels: Channel[] = ['whatsapp', 'instagram', 'facebook', 'web', 'email']
  const slaStates: SLAState[] = ['ok', 'risk', 'breach']
  const items: ConversationSummary[] = []
  for (let i = 0; i < 25; i++) {
    const name = pick(names)
    const waiting = rand(0, 120)
    const priority: Priority = waiting > 60 ? 'high' : pick(priorities)
    const sla: SLAState = waiting > 45 ? pick(['risk', 'breach']) : pick(slaStates)
    items.push({
      id: `c-${i + 1}`,
      customerName: name,
      lastMessageSnippet: pick(['I need help with my order', 'Payment charged twice', 'Where is my package?', 'Account access issue', 'Can I change my address?']),
      lastUpdatedTs: Date.now() - rand(0, 1000 * 60 * 60 * 24),
      channel: pick(channels),
      tags: Array.from(new Set([pick(tagsPool), pick(tagsPool)])).slice(0, rand(1, 2)),
      assignedTo: Math.random() > 0.6 ? pick(['Nancy', 'Jack', 'You']) : undefined,
      priority,
      waitingMinutes: waiting,
      sla,
      lowConfidence: Math.random() > 0.75,
    })
  }
  return items
}

export const ConversationsList: React.FC = () => {
  const { theme } = useTheme()
  const insets = useSafeAreaInsets()
  const route = useRoute<any>()
  const navigation = useNavigation<any>()

  const [query, setQuery] = React.useState('')
  const [debouncedQuery, setDebouncedQuery] = React.useState('')
  const [active, setActive] = React.useState<Partial<Record<string, string>>>({})
  const [sort, setSort] = React.useState<'recent' | 'oldest' | 'priority'>('recent')
  const [loading, setLoading] = React.useState(false)
  const [refreshing, setRefreshing] = React.useState(false)
  const [data, setData] = React.useState<ConversationSummary[]>(() => genDemo())
  const [assignOpen, setAssignOpen] = React.useState<null | string>(null)
  const [agents] = React.useState<AnyAgent[]>(() => {
    const skills: Array<SkillTag> = [{ name: 'support', level: 3 }]
    const mk = (i: number): AnyAgent => ({ id: `ag-${i}`, kind: 'human', name: `Agent ${i}`, status: 'online', skills, email: undefined, phone: undefined, schedule: [], capacity: { concurrent: 3 }, assignedOpen: 0 } as any)
    return Array.from({ length: 8 }).map((_, i) => mk(i + 1))
  })

  // Analytics: view
  React.useEffect(() => {
    track('conversations.view')
  }, [])

  // Debounce query to avoid stutter
  React.useEffect(() => {
    const h = setTimeout(() => setDebouncedQuery(query.trim().toLowerCase()), 300)
    return () => clearTimeout(h)
  }, [query])

  // Initialize filters from deep link
  React.useEffect(() => {
    const f = route.params?.filter as string | undefined
    const prefill = route.params?.prefill as string | undefined
    if (!f) return
    switch (f) {
      case 'urgent':
        setActive((a) => ({ ...a, urgent: '1' }))
        break
      case 'waiting30':
        setActive((a) => ({ ...a, waiting30: '1' }))
        break
      case 'unassigned':
        setActive((a) => ({ ...a, unassigned: '1' }))
        break
      case 'slaRisk':
        setActive((a) => ({ ...a, slaRisk: '1' }))
        break
      case 'vip':
        setActive((a) => ({ ...a, vip: '1' }))
        break
      default:
        break
    }
  }, [route.params])

  // Prefill search from cross-app navigation
  React.useEffect(() => {
    const prefill = route.params?.prefill as string | undefined
    if (prefill) setQuery(prefill)
  }, [route.params?.prefill])

  const onFilterChange = (key: any, value?: string) => {
    setActive((prev) => {
      const next = { ...prev }
      if (!value) delete (next as any)[key]
      else (next as any)[key] = value
      return next
    })
    track('conversations.filter', { key, value })
  }

  const filtered = React.useMemo(() => {
    let arr = data
    // Apply filters
    if (active.urgent) arr = arr.filter((c) => c.priority === 'high' || c.sla !== 'ok')
    if (active.waiting30) arr = arr.filter((c) => c.waitingMinutes >= 30)
    if (active.unassigned) arr = arr.filter((c) => !c.assignedTo)
    if (active.slaRisk) arr = arr.filter((c) => c.sla !== 'ok')
    if (active.vip) arr = arr.filter((c) => c.priority === 'vip')
    if (active.channel) arr = arr.filter((c) => c.channel === active.channel)
    if (active.intent) arr = arr // placeholder
    if (active.tag) arr = arr.filter((c) => c.tags.includes(active.tag as string))
    // Search
    if (debouncedQuery) {
      const q = debouncedQuery
      arr = arr.filter((c) => c.customerName.toLowerCase().includes(q) || c.lastMessageSnippet.toLowerCase().includes(q))
    }
    // Sort
    if (sort === 'recent') arr = [...arr].sort((a, b) => b.lastUpdatedTs - a.lastUpdatedTs)
    if (sort === 'oldest') arr = [...arr].sort((a, b) => a.lastUpdatedTs - b.lastUpdatedTs)
    if (sort === 'priority') {
      const order: Record<Priority, number> = { vip: 0, high: 1, normal: 2, low: 3 }
      arr = [...arr].sort((a, b) => order[a.priority] - order[b.priority])
    }
    return arr
  }, [data, active, debouncedQuery, sort])

  const onRefresh = () => {
    setRefreshing(true)
    setTimeout(() => {
      // Simple reorder
      setData((prev) => [...prev].reverse())
      setRefreshing(false)
    }, 600)
  }

  React.useEffect(() => {
    // Simulate initial loading brief
    setLoading(true)
    const t = setTimeout(() => setLoading(false), 400)
    return () => clearTimeout(t)
  }, [])

  return (
    <SafeAreaView style={{ flex: 1, backgroundColor: theme.color.background }}>
      <View style={{ paddingHorizontal: 24, paddingTop: insets.top + 12, paddingBottom: 12 }}>
        <Text style={{ color: theme.color.foreground, fontSize: 32, fontWeight: '700', marginBottom: 12 }}>Conversations</Text>
        <FilterBar active={active as any} onChange={onFilterChange as any} testID="conv-filterbar" />
        <View style={{ height: 12 }} />
        <SortBar sort={sort} onChange={setSort} testID="conv-sortbar" />
        <View style={{ height: 12 }} />
        <View style={{ borderWidth: 1, borderColor: theme.color.border, borderRadius: 12, backgroundColor: theme.color.card }}>
          <TextInput
            value={query}
            onChangeText={setQuery}
            placeholder="Search by name or snippet"
            placeholderTextColor={theme.color.placeholder}
            style={{ paddingHorizontal: 12, paddingVertical: 10, color: theme.color.cardForeground }}
          />
        </View>
        {/* Cross-app hook: create rule from current filters */}
        <View style={{ flexDirection: 'row', justifyContent: 'flex-end', marginTop: 8 }}>
          <TouchableOpacity onPress={() => {
            const when: any[] = []
            if (active.waiting30) when.push({ key: 'waitingMinutes', op: 'gte', value: 30 })
            if (active.unassigned) when.push({ key: 'unassigned', op: 'true' })
            if (active.channel) when.push({ key: 'channel', op: 'is', value: active.channel })
            if (active.vip) when.push({ key: 'vip', op: 'true' })
            navigation.navigate('Automations', { screen: 'RuleBuilder', params: { rule: { id: `tmp-${Date.now()}`, name: 'Rule from filter', when, then: [{ key: 'autoReply', params: { message: 'We will be right with you' } }], enabled: true, order: 0 }, currentMaxOrder: 0 } })
          }} accessibilityLabel="Create rule from filter" accessibilityRole="button" style={{ paddingHorizontal: 12, paddingVertical: 8, borderRadius: 10, borderWidth: 1, borderColor: theme.color.border }}>
            <Text style={{ color: theme.color.cardForeground, fontWeight: '600' }}>Create rule from filter…</Text>
          </TouchableOpacity>
        </View>
      </View>
      <View style={{ flex: 1, paddingHorizontal: 24 }}>
        {loading ? (
          <ListSkeleton rows={8} testID="conv-skeleton" />
        ) : filtered.length === 0 ? (
          <EmptyState message="No conversations match your filters." testID="conv-empty" />
        ) : (
          <FlatList
            testID="conv-list"
            data={filtered}
            keyExtractor={(item) => item.id}
            renderItem={({ item }) => (
              <ConversationListItem
                item={item}
                onPress={(id) => { track('conversation.open', { id }); navigation.navigate('Conversations', { selectedId: id }) }}
                onAssignToAgent={(id) => setAssignOpen(id)}
                testID={`conv-row-${item.id}`}
              />
            )}
            ItemSeparatorComponent={() => <View style={{ height: 12 }} />}
            refreshControl={<RefreshControl refreshing={refreshing} onRefresh={onRefresh} />}
            contentContainerStyle={{ paddingBottom: 24 }}
            initialNumToRender={12}
            maxToRenderPerBatch={12}
            windowSize={10}
            removeClippedSubviews
          />
        )}
      </View>

      {/* Assign to Agent sheet */}
      <Modal visible={!!assignOpen} transparent animationType="slide" onRequestClose={() => setAssignOpen(null)}>
        <View style={{ flex: 1, backgroundColor: 'rgba(0,0,0,0.4)', justifyContent: 'flex-end' }}>
          <View style={{ backgroundColor: theme.color.card, borderTopLeftRadius: 16, borderTopRightRadius: 16, padding: 16, maxHeight: 480, borderWidth: 1, borderColor: theme.color.border }}>
            <View style={{ flexDirection: 'row', alignItems: 'center', justifyContent: 'space-between', marginBottom: 12 }}>
              <Text style={{ color: theme.color.cardForeground, fontWeight: '700', fontSize: 16 }}>Assign to Agent</Text>
              <TouchableOpacity onPress={() => setAssignOpen(null)} style={{ paddingHorizontal: 12, paddingVertical: 8 }}>
                <Text style={{ color: theme.color.mutedForeground }}>Close</Text>
              </TouchableOpacity>
            </View>
            <FlatList
              data={agents}
              keyExtractor={(a) => a.id}
              renderItem={({ item: ag }) => (
                <TouchableOpacity onPress={() => {
                  setData((prev) => prev.map((c) => c.id === assignOpen ? { ...c, assignedTo: ag.name } : c))
                  setAssignOpen(null)
                }} style={{ marginBottom: 8 }}>
                  <AgentMiniCard item={ag} />
                </TouchableOpacity>
              )}
            />
          </View>
        </View>
      </Modal>
    </SafeAreaView>
  )
}

export default ConversationsList


