import React from 'react'
import { SafeAreaView, View, Text, TouchableOpacity, Image, TextInput, FlatList, Alert } from 'react-native'
import { useRoute, useNavigation } from '@react-navigation/native'
import { useSafeAreaInsets } from 'react-native-safe-area-context'
import { useTheme } from '../../providers/ThemeProvider'
import { AnyAgent, AgentKind, AgentStatus, SkillTag } from '../../types/agents'
import StatusBadge from '../../components/agents/StatusBadge'
import SkillChip from '../../components/agents/SkillChip'
import CapacityPill from '../../components/agents/CapacityPill'
import PerformanceStat from '../../components/agents/PerformanceStat'
import WeekSchedule from '../../components/agents/WeekSchedule'
import CapacityEditor from '../../components/agents/CapacityEditor'
import BehaviorEditor from '../../components/agents/BehaviorEditor'
import AllowlistEditor from '../../components/agents/AllowlistEditor'
import KnowledgeLinks from '../../components/agents/KnowledgeLinks'

const names = ['Sarah Johnson', 'Michael Chen', 'Emma Rodriguez', 'David Park', 'Aisha Khan', 'Omar Ali', 'Liam Smith', 'Noah Brown']
const skillsPool: Array<SkillTag> = [
  { name: 'sales', level: 3 },
  { name: 'support', level: 4 },
  { name: 'billing', level: 2 },
  { name: 'tech', level: 5 },
  { name: 'logistics', level: 3 },
  { name: 'custom', level: 1 },
]
const rand = (min: number, max: number) => Math.floor(Math.random() * (max - min + 1)) + min
const pick = <T,>(arr: T[]): T => arr[rand(0, arr.length - 1)]
const initials = (name: string) => name.split(' ').map((n) => n[0]).slice(0, 2).join('').toUpperCase()

const genAgent = (id: string): AnyAgent => {
  const isAi = id.endsWith('1') || Math.random() > 0.5
  const base = {
    id,
    kind: (isAi ? 'ai' : 'human') as AgentKind,
    name: pick(names),
    status: pick(['online', 'away', 'offline', 'dnd']) as AgentStatus,
    skills: Array.from(new Set([pick(skillsPool), pick(skillsPool), pick(skillsPool)])).slice(0, rand(1, 3)) as SkillTag[],
    notes: undefined as string | undefined,
  }
  if (isAi) {
    return {
      ...(base as any),
      kind: 'ai',
      behavior: { temperature: 0.7, tone: 'friendly', systemPrompt: 'You are a helpful assistant.' },
      allowlist: [ { key: 'refund', label: 'Issue refund', enabled: true, risk: 'medium' }, { key: 'order_status', label: 'Check order status', enabled: true } ],
      deflectionTarget: rand(25, 70),
      knowledgeSources: ['FAQ', 'Orders KB'],
    }
  }
  return {
    ...(base as any),
    kind: 'human',
    email: 'agent@example.com',
    phone: '+1-555-123-4567',
    schedule: [ { day: 1, start: '09:00', end: '17:00' }, { day: 3, start: '10:00', end: '18:00' } ],
    capacity: { concurrent: rand(2, 6), backlogMax: rand(10, 40) },
    assignedOpen: rand(0, 12),
  }
}

const DeflectionPill: React.FC<{ pct?: number }> = ({ pct }) => {
  if (pct == null) return null
  return (
    <View style={{ paddingHorizontal: 10, paddingVertical: 6, borderRadius: 12, borderWidth: 1, borderColor: '#E5E7EB', backgroundColor: '#F3F4F6', minHeight: 32, justifyContent: 'center' }}>
      <Text style={{ color: '#6B7280', fontSize: 12, fontWeight: '600' }}>{`Deflect ${pct}%`}</Text>
    </View>
  )
}

const AgentDetail: React.FC = () => {
  const { theme } = useTheme()
  const insets = useSafeAreaInsets()
  const route = useRoute<any>()
  const navigation = useNavigation<any>()
  const id: string = route.params?.id || 'ag-1'

  const [agent, setAgent] = React.useState<AnyAgent>(() => genAgent(id))
  const [tab, setTab] = React.useState<'profile' | 'sched' | 'perf' | 'notes'>('profile')
  const [noteInput, setNoteInput] = React.useState('')
  const [notes, setNotes] = React.useState<string[]>([])
  const [schedSlots, setSchedSlots] = React.useState<any[]>(() => (genAgent(id).kind === 'human' ? (genAgent(id) as any).schedule || [] : []))
  const [capacity, setCapacity] = React.useState<any>(() => (genAgent(id).kind === 'human' ? (genAgent(id) as any).capacity : undefined))

  const changeStatus = () => {
    const order: AgentStatus[] = ['online', 'away', 'dnd', 'offline']
    const idx = order.indexOf(agent.status)
    const next = order[(idx + 1) % order.length]
    setAgent((a) => ({ ...a, status: next }))
  }

  const assignTestConversation = () => {
    navigation.navigate('Conversations', { screen: 'Conversations', params: { prefill: agent.name } })
  }
  const viewAssigned = () => {
    navigation.navigate('Conversations', { screen: 'Conversations', params: { filter: 'assignedTo', prefill: agent.name } })
  }

  const avatar = (
    agent && (
      'avatarUrl' in agent && agent.avatarUrl ? (
        <Image source={{ uri: (agent as any).avatarUrl }} style={{ width: 44, height: 44, borderRadius: 22, backgroundColor: theme.color.muted }} />
      ) : (
        <View style={{ width: 44, height: 44, borderRadius: 22, backgroundColor: theme.color.muted, alignItems: 'center', justifyContent: 'center' }}>
          <Text style={{ color: theme.color.mutedForeground, fontWeight: '700' }}>{initials(agent.name)}</Text>
        </View>
      )
    )
  )

  return (
    <SafeAreaView style={{ flex: 1, backgroundColor: theme.color.background }}>
      {/* Header */}
      <View style={{ paddingHorizontal: 16, paddingTop: insets.top + 8, paddingBottom: 12, borderBottomWidth: 1, borderBottomColor: theme.color.border }}>
        <View style={{ flexDirection: 'row', alignItems: 'center', justifyContent: 'space-between' }}>
          <TouchableOpacity onPress={() => navigation.goBack()} accessibilityLabel="Back" accessibilityRole="button" style={{ padding: 8 }}>
            <Text style={{ color: theme.color.primary, fontWeight: '600' }}>{'< Back'}</Text>
          </TouchableOpacity>
          <View style={{ flexDirection: 'row', gap: 8 }}>
            <TouchableOpacity onPress={() => Alert.alert('Edit', 'Edit agent (UI-only).')} accessibilityLabel="Edit" accessibilityRole="button" style={{ padding: 8 }}>
              <Text style={{ color: theme.color.primary, fontWeight: '600' }}>Edit</Text>
            </TouchableOpacity>
            <TouchableOpacity onPress={changeStatus} accessibilityLabel="Change Status" accessibilityRole="button" style={{ padding: 8 }}>
              <Text style={{ color: theme.color.cardForeground, fontWeight: '600' }}>Change Status</Text>
            </TouchableOpacity>
            <TouchableOpacity onPress={() => navigation.navigate('Agents', { screen: 'RoutingRules' })} accessibilityLabel="See routing" accessibilityRole="button" style={{ padding: 8 }}>
              <Text style={{ color: theme.color.cardForeground, fontWeight: '600' }}>See routing</Text>
            </TouchableOpacity>
            <TouchableOpacity onPress={assignTestConversation} accessibilityLabel="Assign Test Conversation" accessibilityRole="button" style={{ padding: 8 }}>
              <Text style={{ color: theme.color.cardForeground, fontWeight: '600' }}>Assign Test Conversation</Text>
            </TouchableOpacity>
            <TouchableOpacity onPress={viewAssigned} accessibilityLabel="View assigned conversations" accessibilityRole="button" style={{ padding: 8 }}>
              <Text style={{ color: theme.color.cardForeground, fontWeight: '600' }}>View assigned</Text>
            </TouchableOpacity>
            <TouchableOpacity onPress={() => Alert.alert('Delete Agent', 'Are you sure?', [{ text: 'Cancel' }, { text: 'Delete', style: 'destructive' }])} accessibilityLabel="Delete" accessibilityRole="button" style={{ padding: 8 }}>
              <Text style={{ color: theme.color.error, fontWeight: '600' }}>Delete</Text>
            </TouchableOpacity>
          </View>
        </View>

        <View style={{ flexDirection: 'row', alignItems: 'center', gap: 10, marginTop: 10 }}>
          {avatar}
          <View style={{ flex: 1 }}>
            <View style={{ flexDirection: 'row', alignItems: 'center', justifyContent: 'space-between' }}>
              <View style={{ flexDirection: 'row', alignItems: 'center', gap: 8, flex: 1 }}>
                <Text style={{ color: theme.color.foreground, fontSize: 18, fontWeight: '700' }} numberOfLines={1}>{agent.name}</Text>
                <StatusBadge status={agent.status} />
              </View>
              {agent.kind === 'human' ? (
                <CapacityPill capacity={(agent as any).capacity} assignedOpen={(agent as any).assignedOpen} />
              ) : (
                <DeflectionPill pct={(agent as any).deflectionTarget} />
              )}
            </View>
            <View style={{ flexDirection: 'row', flexWrap: 'wrap', gap: 6, marginTop: 8 }}>
              {(agent.skills || []).slice(0, 4).map((s, idx) => (
                <SkillChip key={`${String(s.name)}-${idx}`} tag={s} />
              ))}
            </View>
          </View>
        </View>
      </View>

      {/* Tabs */}
      <View style={{ flexDirection: 'row', gap: 8, paddingHorizontal: 16, paddingVertical: 8 }}>
        {(['profile', 'sched', 'perf', 'notes'] as const).map((t) => (
          <TouchableOpacity key={t} onPress={() => setTab(t)} style={{ paddingHorizontal: 12, paddingVertical: 8, borderRadius: 12, borderWidth: 1, borderColor: tab === t ? theme.color.primary : theme.color.border }}>
            <Text style={{ color: tab === t ? theme.color.primary : theme.color.mutedForeground, fontWeight: '600' }}>{t.toUpperCase()}</Text>
          </TouchableOpacity>
        ))}
      </View>

      {/* Body */}
      {tab === 'profile' && (
        <View style={{ padding: 16, gap: 10 }}>
          {agent.kind === 'human' ? (
            <>
              <Text style={{ color: theme.color.mutedForeground }}>Email: {(agent as any).email || '—'}</Text>
              <Text style={{ color: theme.color.mutedForeground }}>Phone: {(agent as any).phone || '—'}</Text>
            </>
          ) : (
            <>
              <Text style={{ color: theme.color.mutedForeground }}>Knowledge: {(agent as any).knowledgeSources?.join(', ') || '—'}</Text>
            </>
          )}
          <Text style={{ color: theme.color.cardForeground, fontWeight: '700', marginTop: 8 }}>Notes</Text>
          <Text style={{ color: theme.color.mutedForeground }}>{agent.notes || 'No notes yet.'}</Text>
        </View>
      )}

      {tab === 'sched' && (
        <View style={{ padding: 16 }}>
          {agent.kind === 'human' ? (
            <View>
              <Text style={{ color: theme.color.cardForeground, fontWeight: '700', marginBottom: 8 }}>Schedule</Text>
              <WeekSchedule slots={schedSlots} onChange={setSchedSlots} />
              <Text style={{ color: theme.color.cardForeground, fontWeight: '700', marginVertical: 12 }}>Capacity</Text>
              <CapacityEditor value={capacity} onChange={setCapacity} />
            </View>
          ) : (
            <View style={{ gap: 12 }}>
              <BehaviorEditor value={(agent as any).behavior} onChange={(v) => setAgent((a) => ({ ...(a as any), behavior: v }))} />
              <Text style={{ color: theme.color.cardForeground, fontWeight: '700' }}>Allowed Actions</Text>
              <AllowlistEditor items={(agent as any).allowlist} onToggle={(key) => setAgent((a) => ({ ...(a as any), allowlist: (a as any).allowlist.map((al: any) => al.key === key ? { ...al, enabled: !al.enabled } : al) }))} />
              <Text style={{ color: theme.color.cardForeground, fontWeight: '700' }}>Knowledge Sources</Text>
              <KnowledgeLinks sources={(agent as any).knowledgeSources || []} onChange={(srcs) => setAgent((a) => ({ ...(a as any), knowledgeSources: srcs }))} />
            </View>
          )}
        </View>
      )}

      {tab === 'perf' && (
        <View style={{ padding: 16, gap: 12 }}>
          {/* Simple sparkline placeholder */}
          {(() => {
            const bars = Array.from({ length: 12 }, () => rand(3, 12))
            const Spark = () => (
              <View style={{ flexDirection: 'row', alignItems: 'flex-end', gap: 3, marginTop: 6 }}>
                {bars.map((h, i) => (
                  <View key={i} style={{ width: 6, height: h, borderRadius: 2, backgroundColor: theme.color.muted }} />
                ))}
              </View>
            )
            return (
              <View>
                {agent.kind === 'human' ? (
                  <View style={{ gap: 12 }}>
                    <View>
                      <PerformanceStat label="FRT P50" value={`${rand(1, 3)}m`} delta={rand(-10, 10)} />
                      <Spark />
                    </View>
                    <View>
                      <PerformanceStat label="FRT P90" value={`${rand(5, 12)}m`} delta={rand(-10, 10)} />
                      <Spark />
                    </View>
                    <View>
                      <PerformanceStat label="AHT" value={`${rand(3, 8)}m`} delta={rand(-10, 10)} />
                      <Spark />
                    </View>
                    <View>
                      <PerformanceStat label="Resolution %" value={`${rand(85, 98)}%`} delta={rand(-5, 5)} />
                      <Spark />
                    </View>
                    <View>
                      <PerformanceStat label="CSAT" value={`${rand(88, 99)}%`} delta={rand(-3, 4)} />
                      <Spark />
                    </View>
                  </View>
                ) : (
                  <View style={{ gap: 12 }}>
                    <View>
                      <PerformanceStat label="Deflection %" value={`${rand(25, 75)}%`} delta={rand(-8, 10)} />
                      <Spark />
                    </View>
                    <View>
                      <PerformanceStat label="Low-confidence rate" value={`${rand(2, 12)}%`} delta={rand(-4, 6)} />
                      <Spark />
                    </View>
                  </View>
                )}
              </View>
            )
          })()}
        </View>
      )}

      {tab === 'notes' && (
        <View style={{ padding: 16, gap: 12 }}>
          <View style={{ borderWidth: 1, borderColor: theme.color.border, borderRadius: 12, backgroundColor: theme.color.card }}>
            <TextInput
              value={noteInput}
              onChangeText={setNoteInput}
              placeholder="Add a note"
              placeholderTextColor={theme.color.placeholder}
              style={{ paddingHorizontal: 12, paddingVertical: 10, color: theme.color.cardForeground }}
              multiline
            />
          </View>
          <TouchableOpacity onPress={() => { if (noteInput.trim()) { setNotes((n) => [noteInput.trim(), ...n]); setNoteInput('') } }} style={{ alignSelf: 'flex-end', paddingHorizontal: 12, paddingVertical: 8, borderRadius: 12, borderWidth: 1, borderColor: theme.color.border }}>
            <Text style={{ color: theme.color.primary, fontWeight: '600' }}>Add Note</Text>
          </TouchableOpacity>
          <FlatList
            data={notes}
            keyExtractor={(_, i) => String(i)}
            renderItem={({ item }) => (
              <View style={{ padding: 12, borderWidth: 1, borderColor: theme.color.border, borderRadius: 12, marginBottom: 8 }}>
                <Text style={{ color: theme.color.cardForeground }}>{item}</Text>
              </View>
            )}
          />
        </View>
      )}
    </SafeAreaView>
  )
}

export default AgentDetail



