import React from 'react'
import { View, Text, TouchableOpacity, ScrollView } from 'react-native'
import { useTheme } from '../../providers/ThemeProvider'
import { AskInput } from '../../components/assistant/AskInput'
import { AnswerCard } from '../../components/assistant/AnswerCard'
import { ShortcutGrid } from '../../components/assistant/ShortcutGrid'
import { AskAnswer, AnswerChunk, ToolSuggestion, ToolKey, Persona, Tone, Shortcut } from '../../types/assistant'
import { useNavigation } from '@react-navigation/native'
import { track } from '../../lib/analytics'

const now = () => Date.now()

export const DashboardAskPanel: React.FC<{ testID?: string }> = ({ testID }) => {
  const { theme } = useTheme()
  const navigation = useNavigation<any>()

  const [collapsed, setCollapsed] = React.useState(false)
  const [dismissed, setDismissed] = React.useState(false)
  const [query, setQuery] = React.useState('')
  const [persona, setPersona] = React.useState<Persona>('owner')
  const [tone, setTone] = React.useState<Tone>('neutral')
  const [answers, setAnswers] = React.useState<AskAnswer[]>([])
  const [recent, setRecent] = React.useState<string[]>(['Today’s summary', 'Breaches', 'Top intents', 'VIP queue'])
  const openedRef = React.useRef(false)
  React.useEffect(() => {
    if (!collapsed && !openedRef.current) { openedRef.current = true; try { track('assistant.open', { surface: 'dashboard' }) } catch {} }
  }, [collapsed])

  const shortcuts: Shortcut[] = [
    { id: 'sc1', label: 'Today’s summary', action: { key: 'open_analytics', label: 'Open Analytics' } },
    { id: 'sc2', label: 'Breaches', action: { key: 'open_conversations', label: 'SLA Breaches', params: { filter: 'slaBreaches' } } },
    { id: 'sc3', label: 'Top intents', action: { key: 'open_analytics', label: 'Top Intents' } },
    { id: 'sc4', label: 'VIP queue', action: { key: 'open_conversations', label: 'VIP', params: { filter: 'vip' } } },
    { id: 'sc5', label: 'Daily Brief', action: { key: 'open_analytics', label: 'Daily Brief', params: { screen: 'DailyBrief' } } },
  ]

  if (dismissed) return null

  const makeMockAnswer = (text: string, who: Persona, how: Tone): AskAnswer => {
    const toneWrap = (base: string) => {
      if (how === 'concise') return base.replace(/\s+/g, ' ').split('. ').slice(0, 1).join('. ') + '.'
      if (how === 'friendly') return base + ' Let me know if you want me to dig deeper!'
      return base
    }

    const kpiFrt: AnswerChunk = {
      kind: 'kpi',
      kpi: { label: 'FRT P50', value: '02:15', delta: '+12s' },
      citations: [{ id: 'c1', label: 'FRT last 24h', kind: 'metric' }],
    }
    const kpiRes: AnswerChunk = {
      kind: 'kpi',
      kpi: { label: 'Resolution Rate', value: '82%', delta: '-3%' },
      citations: [{ id: 'c2', label: 'Resolution last 24h', kind: 'metric' }],
    }
    const summary: AnswerChunk = {
      kind: 'paragraph',
      text: toneWrap('Traffic slightly up vs yesterday. VIP queue spiked around 11am; SLA risk mostly in Instagram DM.'),
    }
    const incidents: AnswerChunk = {
      kind: 'list',
      items: ['3 VIP tickets waiting >30m', '2 breaches predicted in next hour', 'Top intent: Order status (+5%)'],
    }
    const calloutPlaybook: AnswerChunk = { kind: 'callout', text: toneWrap('Suggested playbook: Prioritize VIP tickets and send proactive updates.') }
    const chart: AnswerChunk = { kind: 'chart', chartKey: 'vip_vs_time' }
    const table: AnswerChunk = { kind: 'table', text: 'Cohorts table (demo)' }

    const ownerOrder: AnswerChunk[] = [kpiFrt, kpiRes, summary, incidents]
    const opsOrder: AnswerChunk[] = [incidents, kpiFrt, kpiRes, summary]
    const agentOrder: AnswerChunk[] = [calloutPlaybook, incidents, summary, kpiFrt]
    const analystOrder: AnswerChunk[] = [chart, summary, table, kpiFrt]

    const chunks = who === 'owner' ? ownerOrder
      : who === 'ops' ? opsOrder
      : who === 'agent' ? agentOrder
      : analystOrder

    const followUps = who === 'owner'
      ? ['Any revenue impact?', 'Compare to last week']
      : who === 'ops'
      ? ['Why did VIP spike?', 'Show breaches for Instagram']
      : who === 'agent'
      ? ['Show related playbooks', 'Draft response template']
      : ['Break down by cohort', 'Show correlation with volume']

    const tools: ToolSuggestion[] = [
      { key: 'open_conversations', label: 'Open VIP queue', params: { filter: 'vip' } },
      { key: 'open_conversations', label: 'Open SLA risk', params: { filter: 'slaRisk' } },
      { key: 'open_sla', label: 'Open SLA editor' },
    ]

    return {
      id: Math.random().toString(36).slice(2),
      queryId: Math.random().toString(36).slice(2),
      createdAt: now(),
      chunks,
      followUps,
      toolSuggestions: tools,
      safety: { piiMasked: true },
    }
  }

  const handleSend = (text: string) => {
    try { track('assistant.ask', { surface: 'dashboard', persona, tone }) } catch {}
    const ans = makeMockAnswer(text, persona, tone)
    setAnswers((prev) => [ans, ...prev])
    setRecent((prev) => [text, ...prev.filter((r) => r !== text)].slice(0, 8))
  }

  const handleTool = (key: ToolKey, params?: any) => {
    if (key === 'open_conversations') {
      const filter = params?.filter || 'all'
      navigation.navigate('Conversations', { filter })
    } else if (key === 'open_sla') {
      navigation.navigate('Automations', {
        screen: 'SlaEditor',
        params: {
          policy: { id: 'sla-1', name: 'Default SLA', pauseOutsideHours: true, targets: [ { priority: 'vip', frtP50Sec: 60, frtP90Sec: 180 } ] },
          onApply: () => {},
        },
      })
    } else if (key === 'open_analytics') {
      navigation.navigate('Analytics')
    }
  }

  const sendAndPrefill = (text: string) => {
    setQuery(text)
    // Slight timeout to ensure AskInput controlled value updates before send
    setTimeout(() => handleSend(text), 0)
  }

  return (
    <View testID={testID ?? 'dashboard-ask'} style={{ backgroundColor: theme.color.card, borderWidth: 1, borderColor: theme.color.border, borderRadius: theme.radius.lg }}>
      {/* Header */}
      <View style={{ flexDirection: 'row', alignItems: 'center', justifyContent: 'space-between', paddingHorizontal: 12, paddingVertical: 10 }}>
        <Text style={{ color: theme.color.cardForeground, fontWeight: '700', fontSize: 16 }}>Ask the Assistant</Text>
        <View style={{ flexDirection: 'row', gap: 8 }}>
          <TouchableOpacity onPress={() => setCollapsed((v) => !v)} accessibilityLabel={collapsed ? 'Expand' : 'Collapse'} accessibilityRole="button" style={{ padding: 8, minWidth: 44, minHeight: 44, alignItems: 'center', justifyContent: 'center', borderRadius: 8 }}>
            <Text style={{ color: theme.color.mutedForeground }}>{collapsed ? 'Expand' : 'Collapse'}</Text>
          </TouchableOpacity>
          <TouchableOpacity onPress={() => setDismissed(true)} accessibilityLabel="Dismiss" accessibilityRole="button" style={{ padding: 8, minWidth: 44, minHeight: 44, alignItems: 'center', justifyContent: 'center', borderRadius: 8 }}>
            <Text style={{ color: theme.color.mutedForeground }}>Dismiss</Text>
          </TouchableOpacity>
        </View>
      </View>

      {!collapsed && (
        <View style={{ padding: 12, gap: 12 }}>
          {/* Recent queries carousel */}
          {recent.length > 0 && (
            <ScrollView horizontal showsHorizontalScrollIndicator={false}>
              <View style={{ flexDirection: 'row', gap: 8 }}>
                {recent.map((r, i) => (
                  <TouchableOpacity key={i} onPress={() => sendAndPrefill(r)} accessibilityLabel={`recent ${i}`} accessibilityRole="button">
                    <View style={{ paddingHorizontal: 12, paddingVertical: 8, backgroundColor: theme.color.secondary, borderRadius: theme.radius.md, borderWidth: 1, borderColor: theme.color.border }}>
                      <Text style={{ color: theme.color.mutedForeground }}>{r}</Text>
                    </View>
                  </TouchableOpacity>
                ))}
              </View>
            </ScrollView>
          )}

          {/* Ask input */}
          <AskInput
            value={query}
            onChangeText={setQuery}
            onSend={handleSend}
            persona={persona}
            onPersonaChange={setPersona}
            tone={tone}
            onToneChange={setTone}
            rangeLabel="Last 24h"
          />

          {/* Shortcuts (compact) */}
          <ShortcutGrid shortcuts={shortcuts} onPress={(s) => {
            if (s.label === 'Today’s summary') sendAndPrefill("Give me today's summary")
            else if (s.label === 'Breaches') sendAndPrefill('Show SLA breaches in last 24h')
            else if (s.label === 'Top intents') sendAndPrefill('What are top intents today?')
            else if (s.label === 'VIP queue') sendAndPrefill('Show VIP queue over 30m wait')
            else if (s.label === 'Daily Brief') navigation.navigate('Analytics', { screen: 'DailyBrief' })
            try { track('assistant.template.use', { id: s.id }) } catch {}
          }} />

          {/* Answers */}
          {answers.map((a) => (
            <AnswerCard key={a.id} answer={a} hidePII onTool={(k) => handleTool(k as ToolKey)} onFollowUp={(s) => sendAndPrefill(s)} />
          ))}
        </View>
      )}
    </View>
  )
}

export default DashboardAskPanel


