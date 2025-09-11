import React from 'react'
import { SafeAreaView, View, TextInput, FlatList, RefreshControl, TouchableOpacity, Text } from 'react-native'
import { useNavigation } from '@react-navigation/native'
import { useSafeAreaInsets } from 'react-native-safe-area-context'
import { useTheme } from '../../providers/ThemeProvider'
import { AnyAgent, AgentKind, AgentStatus, SkillTag } from '../../types/agents'
import AgentRow from '../../components/agents/AgentRow'
import ListSkeleton from '../../components/agents/ListSkeleton'
import EmptyState from '../../components/agents/EmptyState'
import TagChip from '../../components/crm/TagChip'
import OfflineBanner from '../../components/dashboard/OfflineBanner'
import SyncCenterSheet from '../../components/dashboard/SyncCenterSheet'
import { track } from '../../lib/analytics'

const skillsPool: Array<SkillTag> = [
  { name: 'sales', level: 3 },
  { name: 'support', level: 4 },
  { name: 'billing', level: 2 },
  { name: 'tech', level: 5 },
  { name: 'logistics', level: 3 },
  { name: 'custom', level: 1 },
]

const names = ['Sarah Johnson', 'Michael Chen', 'Emma Rodriguez', 'David Park', 'Aisha Khan', 'Omar Ali', 'Liam Smith', 'Noah Brown', 'Ava Davis', 'Sophia Wilson', 'Lucas Lee', 'Mia Clark']
const rand = (min: number, max: number) => Math.floor(Math.random() * (max - min + 1)) + min
const pick = <T,>(arr: T[]): T => arr[rand(0, arr.length - 1)]

const genRoster = (n = 12): AnyAgent[] => {
  const items: AnyAgent[] = []
  for (let i = 0; i < n; i++) {
    const isAi = Math.random() > 0.5
    const status: AgentStatus = pick(['online', 'away', 'offline', 'dnd']) as AgentStatus
    const base = {
      id: `ag-${i + 1}`,
      kind: (isAi ? 'ai' : 'human') as AgentKind,
      name: `${pick(names)}`,
      status,
      skills: Array.from(new Set([pick(skillsPool), pick(skillsPool), pick(skillsPool)])).slice(0, rand(1, 3)) as SkillTag[],
      notes: undefined as string | undefined,
    }
    if (isAi) {
      items.push({
        ...(base as any),
        kind: 'ai',
        behavior: { temperature: 0.7, tone: 'friendly' },
        allowlist: [ { key: 'refund', label: 'Issue refund', enabled: Math.random() > 0.3, risk: 'medium' } ],
        deflectionTarget: rand(20, 70),
      })
    } else {
      items.push({
        ...(base as any),
        kind: 'human',
        email: undefined,
        phone: undefined,
        schedule: [{ day: 1, start: '09:00', end: '17:00' }],
        capacity: { concurrent: rand(2, 6), backlogMax: rand(10, 40) },
        assignedOpen: rand(0, 12),
      })
    }
  }
  return items
}

type SortKey = 'name' | 'status' | 'open'

const statusOrder: Record<AgentStatus, number> = { online: 0, away: 1, dnd: 2, offline: 3 }

const AgentsList: React.FC = () => {
  const navigation = useNavigation<any>()
  const { theme } = useTheme()
  const insets = useSafeAreaInsets()

  const [loading, setLoading] = React.useState(false)
  const [refreshing, setRefreshing] = React.useState(false)
  const [data, setData] = React.useState<AnyAgent[]>(() => genRoster())
  const [query, setQuery] = React.useState('')
  const [debouncedQuery, setDebouncedQuery] = React.useState('')

  const [filters, setFilters] = React.useState<{ kind?: AgentKind; status?: AgentStatus; skill?: string; overloaded?: boolean }>({})
  const [sort, setSort] = React.useState<SortKey>('name')
  const [offline, setOffline] = React.useState(false)
  const [syncOpen, setSyncOpen] = React.useState(false)
  const [queued, setQueued] = React.useState<Record<string, { status?: boolean; skill?: boolean; capacity?: boolean; allow?: boolean }>>({})

  React.useEffect(() => {
    setLoading(true)
    const t = setTimeout(() => setLoading(false), 300)
    return () => clearTimeout(t)
  }, [])

  React.useEffect(() => {
    track('agents.view')
  }, [])

  React.useEffect(() => {
    const h = setTimeout(() => setDebouncedQuery(query.trim().toLowerCase()), 300)
    return () => clearTimeout(h)
  }, [query])

  const onRefresh = () => {
    setRefreshing(true)
    setTimeout(() => {
      setData((prev) => [...prev].reverse())
      setRefreshing(false)
    }, 600)
  }

  const filtered = React.useMemo(() => {
    let arr = data
    if (filters.kind) arr = arr.filter((a) => a.kind === filters.kind)
    if (filters.status) arr = arr.filter((a) => a.status === filters.status)
    if (filters.skill) arr = arr.filter((a) => (a.skills || []).some((s) => String(s.name).toLowerCase() === filters.skill!.toLowerCase()))
    if (filters.overloaded) arr = arr.filter((a) => a.kind === 'human' && (a.assignedOpen || 0) > (a.capacity?.concurrent || 0))
    if (debouncedQuery) {
      const q = debouncedQuery
      arr = arr.filter((a) => a.name.toLowerCase().includes(q) || (a.skills || []).some((s) => String(s.name).toLowerCase().includes(q)))
    }
    if (sort === 'name') arr = [...arr].sort((a, b) => a.name.localeCompare(b.name))
    if (sort === 'status') arr = [...arr].sort((a, b) => statusOrder[a.status] - statusOrder[b.status])
    if (sort === 'open') arr = [...arr].sort((a, b) => (b.kind === 'human' ? b.assignedOpen || 0 : 0) - (a.kind === 'human' ? a.assignedOpen || 0 : 0))
    return arr
  }, [data, filters, debouncedQuery, sort])

  return (
    <SafeAreaView style={{ flex: 1, backgroundColor: theme.color.background }}>
      <View style={{ paddingHorizontal: 24, paddingTop: insets.top + 12, paddingBottom: 12 }}>
        <View style={{ flexDirection: 'row', alignItems: 'center', justifyContent: 'space-between', marginBottom: 12 }}>
          <Text style={{ color: theme.color.foreground, fontSize: 32, fontWeight: '700' }}>Agents</Text>
          <View style={{ flexDirection: 'row', gap: 8 }}>
            <TouchableOpacity onPress={() => navigation.navigate('Agents', { screen: 'RoutingRules' })} accessibilityLabel="Routing Rules" accessibilityRole="button" style={{ paddingHorizontal: 12, paddingVertical: 8, borderWidth: 1, borderColor: theme.color.border, borderRadius: 12 }}>
              <Text style={{ color: theme.color.cardForeground, fontWeight: '600' }}>Routing Rules</Text>
            </TouchableOpacity>
            <TouchableOpacity onPress={() => navigation.navigate('Agents', { screen: 'Policies' })} accessibilityLabel="Policies" accessibilityRole="button" style={{ paddingHorizontal: 12, paddingVertical: 8, borderWidth: 1, borderColor: theme.color.border, borderRadius: 12 }}>
              <Text style={{ color: theme.color.cardForeground, fontWeight: '600' }}>Policies</Text>
            </TouchableOpacity>
            <TouchableOpacity onPress={() => setOffline((v) => !v)} accessibilityLabel="Toggle Offline" accessibilityRole="button" style={{ paddingHorizontal: 12, paddingVertical: 8, borderWidth: 1, borderColor: theme.color.border, borderRadius: 12 }}>
              <Text style={{ color: theme.color.cardForeground, fontWeight: '600' }}>{offline ? 'Go Online' : 'Go Offline'}</Text>
            </TouchableOpacity>
            <TouchableOpacity onPress={() => setSyncOpen(true)} accessibilityLabel="Sync Center" accessibilityRole="button" style={{ paddingHorizontal: 12, paddingVertical: 8, borderWidth: 1, borderColor: theme.color.border, borderRadius: 12 }}>
              <Text style={{ color: theme.color.mutedForeground, fontWeight: '600' }}>Sync</Text>
            </TouchableOpacity>
          </View>
        </View>

        {offline && (
          <View style={{ marginBottom: 12 }}>
            <OfflineBanner visible testID="agents-offline" />
          </View>
        )}

        {/* Filters */}
        <View style={{ flexDirection: 'row', flexWrap: 'wrap', gap: 8, marginBottom: 8 }}>
          <TagChip label={`Kind: ${filters.kind || 'any'}`} onPress={() => setFilters((f) => ({ ...f, kind: f.kind ? undefined : 'ai' }))} testID="agents-filter-kind" />
          <TagChip label={`Status: ${filters.status || 'any'}`} onPress={() => setFilters((f) => ({ ...f, status: f.status ? undefined : 'online' }))} testID="agents-filter-status" />
          <TagChip label={`Skill: ${filters.skill || 'any'}`} onPress={() => setFilters((f) => ({ ...f, skill: f.skill ? undefined : 'support' }))} testID="agents-filter-skill" />
          <TagChip label={`Only overloaded: ${filters.overloaded ? 'yes' : 'no'}`} onPress={() => setFilters((f) => ({ ...f, overloaded: !f.overloaded }))} testID="agents-filter-overloaded" />
        </View>

        {/* Sort */}
        <View style={{ flexDirection: 'row', gap: 8, marginBottom: 8 }}>
          <TagChip label="Name A→Z" selected={sort === 'name'} onPress={() => setSort('name')} testID="agents-sort-name" />
          <TagChip label="Status" selected={sort === 'status'} onPress={() => setSort('status')} testID="agents-sort-status" />
          <TagChip label="Open load" selected={sort === 'open'} onPress={() => setSort('open')} testID="agents-sort-open" />
        </View>

        {/* Search */}
        <View style={{ borderWidth: 1, borderColor: theme.color.border, borderRadius: 12, backgroundColor: theme.color.card }}>
          <TextInput
            value={query}
            onChangeText={setQuery}
            placeholder="Search by name or skill"
            placeholderTextColor={theme.color.placeholder}
            style={{ paddingHorizontal: 12, paddingVertical: 10, color: theme.color.cardForeground }}
          />
        </View>
      </View>

      <View style={{ flex: 1, paddingHorizontal: 24 }}>
        {loading ? (
          <ListSkeleton rows={10} testID="agents-skeleton" />
        ) : filtered.length === 0 ? (
          <EmptyState message="No agents match your filters." testID="agents-empty" />
        ) : (
          <FlatList
            testID="agents-list"
            data={filtered}
            keyExtractor={(item) => item.id}
            renderItem={({ item }) => (
              <View style={{ marginBottom: 12 }}>
                <AgentRow
                  item={item}
                  onPress={(id) => navigation.navigate('Agents', { screen: 'AgentDetail', params: { id } })}
                  onQueueStart={(id, type) => {
                    if (!offline) return
                    setQueued((q) => ({ ...q, [id]: { ...(q[id] || {}), [type]: true } }))
                    setTimeout(() => setQueued((q) => ({ ...q, [id]: { ...(q[id] || {}), [type]: false } })), 1500)
                  }}
                  queuedStatus={queued[item.id]?.status}
                  queuedSkill={queued[item.id]?.skill}
                  queuedCapacity={queued[item.id]?.capacity}
                  queuedAllow={queued[item.id]?.allow}
                />
              </View>
            )}
            refreshControl={<RefreshControl refreshing={refreshing} onRefresh={onRefresh} />}
            initialNumToRender={12}
            maxToRenderPerBatch={12}
            windowSize={10}
            getItemLayout={(_, index) => ({ length: 88, offset: 88 * index, index })}
            removeClippedSubviews
          />
        )}
      </View>
      <SyncCenterSheet
        visible={syncOpen}
        onClose={() => setSyncOpen(false)}
        lastSyncAt={new Date().toLocaleString()}
        queuedCount={Object.values(queued).reduce((acc, v) => acc + (v.status ? 1 : 0) + (v.skill ? 1 : 0) + (v.capacity ? 1 : 0) + (v.allow ? 1 : 0), 0)}
        onRetryAll={() => setSyncOpen(false)}
      />
    </SafeAreaView>
  )
}

export default AgentsList



