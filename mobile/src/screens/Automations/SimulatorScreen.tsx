import React from 'react'
import { SafeAreaView, View, Text, TouchableOpacity, TextInput, ScrollView, Switch } from 'react-native'
import { useSafeAreaInsets } from 'react-native-safe-area-context'
import { useTheme } from '../../providers/ThemeProvider'
import { useNavigation, useRoute } from '@react-navigation/native'
import { Action, BusinessCalendar, Condition, Rule, SlaPolicy, SimulationInput, SimulationResult } from '../../types/automations'
import TagChip from '../../components/crm/TagChip'
import { track } from '../../lib/analytics'

export interface SimulatorParams {
  rules: Rule[]
  calendar: BusinessCalendar
  policy: SlaPolicy
  currentMaxOrder?: number
}

const weekdays = ['Sun','Mon','Tue','Wed','Thu','Fri','Sat'] as const
const channelsAll = ['whatsapp', 'instagram', 'facebook', 'web', 'email'] as const
const priorities = ['low','normal','high','vip'] as const

const toMinutes = (s: string) => {
  const m = /^([01]\d|2[0-3]):([0-5]\d)$/.exec(s || '')
  if (!m) return undefined
  return parseInt(m[1]) * 60 + parseInt(m[2])
}

const withinBusinessHours = (cal: BusinessCalendar, dayIdx: number, minutes: number | undefined) => {
  if (minutes == null) return false
  const d = cal.days.find((x) => x.day === dayIdx)
  if (!d || !d.open) return false
  return d.ranges.some((r) => {
    const a = toMinutes(r.start) || 0
    const b = toMinutes(r.end) || 0
    return minutes >= a && minutes <= b
  })
}

const condMatches = (c: Condition, ctx: any): boolean => {
  const val = (key: string) => ctx[key]
  switch (c.key) {
    case 'intent': return c.op === 'is' ? val('intent') === c.value : c.op === 'contains' ? String(val('intent')||'').includes(String(c.value||'')) : false
    case 'channel': return c.op === 'is' ? val('channel') === c.value : false
    case 'vip': return c.op === 'true' ? !!val('vip') : c.op === 'false' ? !val('vip') : false
    case 'priority': return c.op === 'is' ? val('priority') === c.value : false
    case 'waitingMinutes': {
      const wm = Number(val('waitingMinutes') || 0)
      if (c.op === 'gte') return wm >= Number(c.value || 0)
      if (c.op === 'lte') return wm <= Number(c.value || 0)
      return false
    }
    case 'dayOfWeek': return c.op === 'is' ? val('dayOfWeek') === c.value : false
    case 'timeOfDay': {
      const m = toMinutes(String(val('timeOfDay') || '')) || 0
      const target = toMinutes(String(c.value || '')) || 0
      if (c.op === 'eq') return m === target
      if (c.op === 'gte') return m >= target
      if (c.op === 'lte') return m <= target
      return false
    }
    case 'withinHours': return c.op === 'true' ? !!val('withinHours') : !val('withinHours')
    default:
      return false
  }
}

const runSimulation = (rules: Rule[], input: SimulationInput, cal: BusinessCalendar, policy: SlaPolicy): SimulationResult => {
  const ctx: any = {
    intent: (input.when.find((w) => w.key === 'intent')?.value) || '',
    channel: (input.when.find((w) => w.key === 'channel')?.value) || 'web',
    vip: !!input.when.find((w) => w.key === 'vip' && (w.op === 'true')),
    priority: (input.when.find((w) => w.key === 'priority')?.value) || 'normal',
    waitingMinutes: Number(input.when.find((w) => w.key === 'waitingMinutes')?.value || 0),
    dayOfWeek: (input.when.find((w) => w.key === 'dayOfWeek')?.value) || new Date(input.nowIso).getDay(),
    timeOfDay: (input.when.find((w) => w.key === 'timeOfDay')?.value) || `${String(new Date(input.nowIso).getHours()).padStart(2,'0')}:${String(new Date(input.nowIso).getMinutes()).padStart(2,'0')}`,
  }
  const minutes = toMinutes(ctx.timeOfDay) || 0
  const within = withinBusinessHours(cal, Number(ctx.dayOfWeek), minutes)
  ctx.withinHours = within

  const matched: string[] = []
  const actions: Action[] = []
  for (const r of rules) {
    const ok = r.when.every((cond) => condMatches(cond, ctx))
    if (ok && r.enabled) {
      matched.push(r.id)
      actions.push(...r.then)
    }
  }

  // SLA risk: compare current waiting against target for priority/channel
  const target = policy.targets.find((t) => t.priority === ctx.priority && (!t.channel || t.channel === ctx.channel))
  const atRisk = target ? (ctx.waitingMinutes * 60 > target.frtP50Sec) : false

  const notes: string[] = []
  if (!within) notes.push('Outside business hours')
  if (target) notes.push(`Target P50 ${Math.round(target.frtP50Sec/60)}m, P90 ${Math.round(target.frtP90Sec/60)}m`)

  return { matchedRuleIds: matched, actions, slaAtRisk: atRisk, notes }
}

const SimulatorScreen: React.FC = () => {
  const { theme } = useTheme()
  const insets = useSafeAreaInsets()
  const navigation = useNavigation<any>()
  const route = useRoute<any>()
  const { rules, calendar, policy, currentMaxOrder }: SimulatorParams = route.params || { rules: [], calendar: undefined, policy: undefined }

  const [intent, setIntent] = React.useState('order')
  const [channel, setChannel] = React.useState<typeof channelsAll[number]>('web')
  const [vip, setVip] = React.useState(false)
  const [priority, setPriority] = React.useState<typeof priorities[number]>('normal')
  const [waiting, setWaiting] = React.useState('10')
  const [day, setDay] = React.useState<number>(new Date().getDay())
  const [tod, setTod] = React.useState(`${String(new Date().getHours()).padStart(2,'0')}:${String(new Date().getMinutes()).padStart(2,'0')}`)
  const [within, setWithin] = React.useState<boolean>(false)
  const [result, setResult] = React.useState<SimulationResult | undefined>()

  React.useEffect(() => {
    const m = toMinutes(tod)
    setWithin(withinBusinessHours(calendar, day, m))
  }, [tod, day, calendar])

  const buildInput = (): SimulationInput => ({
    nowIso: new Date().toISOString(),
    when: [
      { key: 'intent', op: 'is', value: intent } as any,
      { key: 'channel', op: 'is', value: channel } as any,
      { key: 'vip', op: vip ? 'true' : 'false' } as any,
      { key: 'priority', op: 'is', value: priority } as any,
      { key: 'waitingMinutes', op: 'gte', value: Number(waiting || 0) } as any,
      { key: 'dayOfWeek', op: 'is', value: day } as any,
      { key: 'timeOfDay', op: 'gte', value: tod } as any,
      { key: 'withinHours', op: within ? 'true' : 'false' } as any,
    ]
  })

  const onRun = () => {
    const input = buildInput()
    const res = runSimulation(rules || [], input, calendar, policy)
    setResult(res)
    track('simulator.run', { matched: res.matchedRuleIds.length })
  }

  const applyAsRule = () => {
    const input = buildInput()
    const then = (result?.actions?.length ? result.actions : [{ key: 'autoReply', params: { message: 'Thanks, we will help soon' } } as Action])
    const rule: Rule = { id: `tmp-${Date.now()}`, name: 'Simulated rule', when: input.when, then, enabled: true, order: (currentMaxOrder || 0) + 1 }
    navigation.navigate('Automations', { screen: 'RuleBuilder', params: { rule, currentMaxOrder, onSave: (r: Rule) => navigation.goBack() } })
  }

  return (
    <SafeAreaView style={{ flex: 1, backgroundColor: theme.color.background }}>
      {/* Header */}
      <View style={{ paddingHorizontal: 16, paddingTop: insets.top + 8, paddingBottom: 12, borderBottomWidth: 1, borderBottomColor: theme.color.border }}>
        <View style={{ flexDirection: 'row', alignItems: 'center', justifyContent: 'space-between' }}>
          <TouchableOpacity onPress={() => navigation.goBack()} accessibilityLabel="Back" accessibilityRole="button" style={{ padding: 8 }}>
            <Text style={{ color: theme.color.primary, fontWeight: '600' }}>{'< Back'}</Text>
          </TouchableOpacity>
          <Text style={{ color: theme.color.foreground, fontSize: 18, fontWeight: '700' }}>Simulator</Text>
          <View style={{ width: 64 }} />
        </View>
      </View>

      <ScrollView style={{ padding: 16 }} contentContainerStyle={{ paddingBottom: 24 }}>
        {/* Inputs */}
        <View style={{ gap: 12 }}>
          <View>
            <Text style={{ color: theme.color.mutedForeground, marginBottom: 6 }}>Intent</Text>
            <TextInput value={intent} onChangeText={setIntent} placeholder="e.g., order" placeholderTextColor={theme.color.placeholder} accessibilityLabel="Intent" style={{ borderWidth: 1, borderColor: theme.color.border, borderRadius: 12, paddingHorizontal: 12, paddingVertical: 10, color: theme.color.cardForeground }} />
          </View>
          <View>
            <Text style={{ color: theme.color.mutedForeground, marginBottom: 6 }}>Channel</Text>
            <View style={{ flexDirection: 'row', flexWrap: 'wrap', gap: 8 }}>
              {channelsAll.map((ch) => (
                <TagChip key={ch} label={ch} selected={channel === ch} onPress={() => setChannel(ch)} />
              ))}
            </View>
          </View>
          <View style={{ flexDirection: 'row', alignItems: 'center', justifyContent: 'space-between' }}>
            <Text style={{ color: theme.color.mutedForeground }}>VIP</Text>
            <Switch value={vip} onValueChange={setVip} accessibilityLabel="VIP" />
          </View>
          <View>
            <Text style={{ color: theme.color.mutedForeground, marginBottom: 6 }}>Priority</Text>
            <View style={{ flexDirection: 'row', flexWrap: 'wrap', gap: 8 }}>
              {priorities.map((p) => (
                <TagChip key={p} label={p} selected={priority === p} onPress={() => setPriority(p as any)} />
              ))}
            </View>
          </View>
          <View>
            <Text style={{ color: theme.color.mutedForeground, marginBottom: 6 }}>Waiting minutes</Text>
            <TextInput value={waiting} onChangeText={setWaiting} keyboardType="numeric" placeholder="10" placeholderTextColor={theme.color.placeholder} accessibilityLabel="Waiting minutes" style={{ borderWidth: 1, borderColor: theme.color.border, borderRadius: 12, paddingHorizontal: 12, paddingVertical: 10, color: theme.color.cardForeground }} />
          </View>
          <View>
            <Text style={{ color: theme.color.mutedForeground, marginBottom: 6 }}>Day of week</Text>
            <View style={{ flexDirection: 'row', flexWrap: 'wrap', gap: 8 }}>
              {weekdays.map((w, idx) => (
                <TagChip key={w} label={w} selected={day === idx} onPress={() => setDay(idx)} />
              ))}
            </View>
          </View>
          <View>
            <Text style={{ color: theme.color.mutedForeground, marginBottom: 6 }}>Time of day (HH:MM)</Text>
            <TextInput value={tod} onChangeText={setTod} placeholder="14:30" placeholderTextColor={theme.color.placeholder} accessibilityLabel="Time of day" style={{ borderWidth: 1, borderColor: theme.color.border, borderRadius: 12, paddingHorizontal: 12, paddingVertical: 10, color: theme.color.cardForeground }} />
          </View>
          <View style={{ flexDirection: 'row', alignItems: 'center', justifyContent: 'space-between' }}>
            <Text style={{ color: theme.color.mutedForeground }}>Within business hours</Text>
            <Switch value={within} onValueChange={setWithin} accessibilityLabel="Within hours" />
          </View>
        </View>

        {/* Actions */}
        <View style={{ flexDirection: 'row', gap: 8, marginTop: 16 }}>
          <TouchableOpacity onPress={onRun} accessibilityLabel="Run Simulation" accessibilityRole="button" style={{ paddingHorizontal: 12, paddingVertical: 10, borderWidth: 1, borderColor: theme.color.border, borderRadius: 12 }}>
            <Text style={{ color: theme.color.primary, fontWeight: '700' }}>Run</Text>
          </TouchableOpacity>
          <TouchableOpacity onPress={applyAsRule} accessibilityLabel="Apply as New Rule" accessibilityRole="button" style={{ paddingHorizontal: 12, paddingVertical: 10, borderWidth: 1, borderColor: theme.color.border, borderRadius: 12 }}>
            <Text style={{ color: theme.color.cardForeground, fontWeight: '600' }}>Apply as New Rule</Text>
          </TouchableOpacity>
        </View>

        {/* Result */}
        {result && (
          <View style={{ marginTop: 16, padding: 12, borderWidth: 1, borderColor: theme.color.border, borderRadius: 12 }}>
            <Text style={{ color: theme.color.cardForeground, fontWeight: '700', marginBottom: 6 }}>Result</Text>
            <Text style={{ color: theme.color.mutedForeground, marginBottom: 6 }}>Matched Rules: {result.matchedRuleIds.join(', ') || '—'}</Text>
            <Text style={{ color: theme.color.mutedForeground, marginBottom: 6 }}>Actions: {result.actions.map((a) => a.key).join(', ') || '—'}</Text>
            <Text style={{ color: result.slaAtRisk ? theme.color.error : theme.color.success, fontWeight: '700' }}>{result.slaAtRisk ? 'SLA AT RISK' : 'SLA OK'}</Text>
            {!!(result.notes || []).length && (
              <View style={{ marginTop: 8 }}>
                {result.notes!.map((n, i) => (
                  <Text key={i} style={{ color: theme.color.mutedForeground }}>• {n}</Text>
                ))}
              </View>
            )}
          </View>
        )}
      </ScrollView>
    </SafeAreaView>
  )
}

export default SimulatorScreen


