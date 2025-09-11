import React from 'react'
import { View, Text, TouchableOpacity, FlatList } from 'react-native'
import { useNavigation } from '@react-navigation/native'
import { tokens } from '../../ui/tokens'
import { AnyAgent, AgentKind, SkillTag } from '../../types/agents'

export interface AgentPerformanceProps { testID?: string }

type Card = {
  id: string
  name: string
  kind: AgentKind
  skills: SkillTag[]
  metrics: {
    frtP50?: number
    aht?: number
    resolutionRate?: number
    csat?: number
    deflection?: number
    lowConfidence?: number
  }
}

const rand = (min: number, max: number) => Math.floor(Math.random() * (max - min + 1)) + min
const pick = <T,>(arr: T[]): T => arr[rand(0, arr.length - 1)]

const names = ['Sarah Johnson', 'Michael Chen', 'Emma Rodriguez', 'David Park', 'Aisha Khan', 'Omar Ali', 'Liam Smith', 'Noah Brown']
const skillsPool: Array<SkillTag> = [ { name: 'sales' }, { name: 'support' }, { name: 'billing' }, { name: 'tech' }, { name: 'logistics' } ]

const genCards = (): Card[] => Array.from({ length: 12 }).map((_, i) => {
  const isAi = i % 3 === 0
  return {
    id: `ag-${i + 1}`,
    name: pick(names),
    kind: (isAi ? 'ai' : 'human') as AgentKind,
    skills: Array.from(new Set([pick(skillsPool), pick(skillsPool)])).slice(0, rand(1, 2)),
    metrics: isAi ?
      { deflection: rand(25, 75), lowConfidence: rand(2, 12) } :
      { frtP50: rand(2, 8), aht: rand(3, 9), resolutionRate: rand(85, 98), csat: rand(88, 99) },
  }
})

const AgentPerformance: React.FC<AgentPerformanceProps> = ({ testID }) => {
  const navigation = useNavigation<any>()

  const [cards, setCards] = React.useState<Card[]>(() => genCards())
  const [kind, setKind] = React.useState<'all' | AgentKind>('all')
  const [skill, setSkill] = React.useState<string | undefined>(undefined)
  const [sortBy, setSortBy] = React.useState<'frtP50' | 'aht' | 'resolutionRate' | 'csat' | 'deflection' | 'lowConfidence'>('resolutionRate')

  const filtered = React.useMemo(() => {
    let arr = cards
    if (kind !== 'all') arr = arr.filter((c) => c.kind === kind)
    if (skill) arr = arr.filter((c) => c.skills.some((s) => String(s.name) === skill))
    return [...arr].sort((a, b) => {
      const av = (a.metrics as any)[sortBy] ?? -Infinity
      const bv = (b.metrics as any)[sortBy] ?? -Infinity
      return (bv as number) - (av as number)
    })
  }, [cards, kind, skill, sortBy])

  const openAgent = (id: string) => navigation.navigate('Agents', { screen: 'AgentDetail', params: { id } })
  const openAssigned = (name: string) => navigation.navigate('Conversations', { screen: 'Conversations', params: { prefill: name, filter: 'assignedTo' } })

  const FilterPill: React.FC<{ label: string; active: boolean; onPress: () => void }>=({ label, active, onPress }) => (
    <TouchableOpacity onPress={onPress} style={{ paddingHorizontal: 10, paddingVertical: 6, borderRadius: 12, borderWidth: 1, borderColor: active ? tokens.colors.primary : tokens.colors.border }}>
      <Text style={{ color: active ? tokens.colors.primary : tokens.colors.mutedForeground, fontWeight: '600' }}>{label}</Text>
    </TouchableOpacity>
  )

  const SortPill: React.FC<{ k: typeof sortBy; label: string }>=({ k, label }) => (
    <TouchableOpacity onPress={() => setSortBy(k)} style={{ paddingHorizontal: 10, paddingVertical: 6, borderRadius: 12, borderWidth: 1, borderColor: sortBy === k ? tokens.colors.primary : tokens.colors.border }}>
      <Text style={{ color: sortBy === k ? tokens.colors.primary : tokens.colors.mutedForeground, fontWeight: '600' }}>{label}</Text>
    </TouchableOpacity>
  )

  const CardView: React.FC<{ c: Card }>=({ c }) => (
    <View style={{ borderWidth: 1, borderColor: tokens.colors.border, borderRadius: 12, padding: 12 }}>
      <View style={{ flexDirection: 'row', alignItems: 'center', justifyContent: 'space-between' }}>
        <Text style={{ color: tokens.colors.cardForeground, fontWeight: '700' }}>{c.name}</Text>
        <Text style={{ color: tokens.colors.mutedForeground }}>{c.kind === 'ai' ? 'AI' : 'Human'}</Text>
      </View>
      <View style={{ flexDirection: 'row', flexWrap: 'wrap', gap: 6, marginTop: 6 }}>
        {c.skills.map((s, idx) => (
          <View key={idx} style={{ paddingHorizontal: 8, paddingVertical: 4, borderWidth: 1, borderColor: tokens.colors.border, borderRadius: 10 }}>
            <Text style={{ color: tokens.colors.mutedForeground, fontSize: 12 }}>{String(s.name)}</Text>
          </View>
        ))}
      </View>
      <View style={{ marginTop: 8, gap: 6 }}>
        {c.kind === 'human' ? (
          <>
            <Text style={{ color: tokens.colors.cardForeground }}>FRT P50: {c.metrics.frtP50}m</Text>
            <Text style={{ color: tokens.colors.cardForeground }}>AHT: {c.metrics.aht}m</Text>
            <Text style={{ color: tokens.colors.cardForeground }}>Resolution %: {c.metrics.resolutionRate}%</Text>
            <Text style={{ color: tokens.colors.cardForeground }}>CSAT: {c.metrics.csat}%</Text>
          </>
        ) : (
          <>
            <Text style={{ color: tokens.colors.cardForeground }}>Deflection %: {c.metrics.deflection}%</Text>
            <Text style={{ color: tokens.colors.cardForeground }}>Low-confidence %: {c.metrics.lowConfidence}%</Text>
          </>
        )}
      </View>
      <View style={{ flexDirection: 'row', gap: 8, marginTop: 10 }}>
        <TouchableOpacity onPress={() => openAgent(c.id)} accessibilityRole="button" accessibilityLabel={`Open agent ${c.name}`} style={{ paddingHorizontal: 10, paddingVertical: 6, borderRadius: 8, backgroundColor: tokens.colors.primary }}>
          <Text style={{ color: tokens.colors.primaryForeground, fontWeight: '700' }}>Open Agent</Text>
        </TouchableOpacity>
        <TouchableOpacity onPress={() => openAssigned(c.name)} accessibilityRole="button" accessibilityLabel={`Assigned conversations ${c.name}`} style={{ paddingHorizontal: 10, paddingVertical: 6, borderRadius: 8, borderWidth: 1, borderColor: tokens.colors.border }}>
          <Text style={{ color: tokens.colors.cardForeground, fontWeight: '600' }}>Assigned Conversations</Text>
        </TouchableOpacity>
      </View>
    </View>
  )

  return (
    <View testID={testID} style={{ borderWidth: 1, borderColor: tokens.colors.border, borderRadius: 12, overflow: 'hidden' }}>
      <View style={{ padding: 12, backgroundColor: tokens.colors.card, borderBottomWidth: 1, borderBottomColor: tokens.colors.border }}>
        <Text style={{ color: tokens.colors.cardForeground, fontWeight: '700' }}>Agent Performance</Text>
      </View>
      <View style={{ padding: 12, gap: 12 }}>
        <View style={{ flexDirection: 'row', flexWrap: 'wrap', gap: 8 }}>
          <FilterPill label="All" active={kind === 'all'} onPress={() => setKind('all')} />
          <FilterPill label="Human" active={kind === 'human'} onPress={() => setKind('human')} />
          <FilterPill label="AI" active={kind === 'ai'} onPress={() => setKind('ai')} />
          {['sales','support','billing','tech','logistics'].map((sk) => (
            <FilterPill key={sk} label={`Skill: ${sk}`} active={skill === sk} onPress={() => setSkill(skill === sk ? undefined : sk)} />
          ))}
        </View>
        <View style={{ flexDirection: 'row', flexWrap: 'wrap', gap: 8 }}>
          <SortPill k="resolutionRate" label="Sort: Resolution %" />
          <SortPill k="csat" label="CSAT" />
          <SortPill k="frtP50" label="FRT P50" />
          <SortPill k="aht" label="AHT" />
          <SortPill k="deflection" label="Deflection %" />
          <SortPill k="lowConfidence" label="Low‑confidence %" />
        </View>
        <FlatList
          data={filtered}
          keyExtractor={(c) => c.id}
          renderItem={({ item }) => (
            <View style={{ marginBottom: 12 }}>
              <CardView c={item} />
            </View>
          )}
          initialNumToRender={8}
          maxToRenderPerBatch={8}
          windowSize={10}
          removeClippedSubviews
        />
      </View>
    </View>
  )
}

export default React.memo(AgentPerformance)


