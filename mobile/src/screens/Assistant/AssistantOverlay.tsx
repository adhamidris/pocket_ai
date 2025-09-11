import React, { useEffect, useMemo, useState } from 'react'
import { View, Text, Modal, TouchableOpacity, ScrollView, FlatList, AccessibilityInfo, findNodeHandle } from 'react-native'
import AsyncStorage from '@react-native-async-storage/async-storage'
import { useTheme } from '../../providers/ThemeProvider'
import { AskInput } from '../../components/assistant/AskInput'
import { AnswerCard, MemoAnswerCard } from '../../components/assistant/AnswerCard'
import { ToolSuggestionRow } from '../../components/assistant/ToolSuggestionRow'
import { FollowUpChips } from '../../components/assistant/FollowUpChips'
import { ShortcutGrid } from '../../components/assistant/ShortcutGrid'
import { Shortcut } from '../../types/assistant'
import { useNavigation } from '@react-navigation/native'
import { Persona, Tone, AskAnswer, AnswerChunk, ToolKey, PromptTemplate, MemoryPin } from '../../types/assistant'
import PromptLibrary from '../../components/assistant/PromptLibrary'
import PinsScreen from './PinsScreen'
import { track } from '../../lib/analytics'

const STORAGE_KEY = 'assistant.recent.answers.v1'

export const AssistantOverlay: React.FC<{ visible: boolean; onClose: () => void; prefill?: { text?: string; persona?: Persona } }>
  = ({ visible, onClose, prefill }) => {
  const { theme } = useTheme()
  const navigation = useNavigation<any>()
  const [persona, setPersona] = useState<Persona>('owner')
  const [tone, setTone] = useState<Tone>('neutral')
  const [query, setQuery] = useState('')
  const [answers, setAnswers] = useState<AskAnswer[]>([])
  const [tab, setTab] = useState<'recent'|'pins'|'templates'>('recent')
  const [templates, setTemplates] = useState<PromptTemplate[]>([
    { id: 't1', name: 'Daily ops brief (last 24h)', text: 'Give me a brief of operations in last 24h', pinned: true },
    { id: 't2', name: 'Why did SLA breach?', text: 'Why did SLA breach today? Show channels and time windows.' },
    { id: 't3', name: 'Which intents to automate?', text: 'Which intents should we automate next and why?' },
  ])
  const [offline, setOffline] = useState<boolean>(false)
  const [sendTimes, setSendTimes] = useState<number[]>([])
  const [cooldownUntil, setCooldownUntil] = useState<number>(0)
  const listRef = React.useRef<FlatList<AskAnswer> | null>(null)
  const inputContainerRef = React.useRef<View | null>(null)

  useEffect(() => {
    (async () => {
      try {
        const raw = await AsyncStorage.getItem(STORAGE_KEY)
        if (raw) setAnswers(JSON.parse(raw))
      } catch {}
    })()
  }, [])

  useEffect(() => { try { track('assistant.open', { surface: 'overlay' }) } catch {} }, [])

  useEffect(() => {
    if (visible && prefill) {
      if (prefill.persona) setPersona(prefill.persona)
      if (prefill.text) setQuery(prefill.text)
    }
  }, [visible, prefill?.text, prefill?.persona])

  useEffect(() => {
    AsyncStorage.setItem(STORAGE_KEY, JSON.stringify(answers.slice(0, 10)))
  }, [answers])

  const makeMockAnswer = (text: string, who: Persona, how: Tone): AskAnswer => {
    const toneWrap = (base: string) => {
      if (how === 'concise') return base.replace(/\s+/g, ' ').split('. ').slice(0, 1).join('. ') + '.'
      if (how === 'friendly') return base + ' Happy to help!'
      return base
    }

    const kpiFrt: AnswerChunk = { kind: 'kpi', kpi: { label: 'FRT P50', value: '02:15', delta: '+12s' } }
    const kpiRes: AnswerChunk = { kind: 'kpi', kpi: { label: 'Resolution Rate', value: '82%', delta: '-3%' } }
    const summary: AnswerChunk = { kind: 'paragraph', text: toneWrap(text || 'Here is the latest summary based on your data.') }
    const listOps: AnswerChunk = { kind: 'list', items: ['3 VIP tickets waiting >30m', '2 breaches predicted next hour', 'Top intent: Order status'] }
    const calloutAgent: AnswerChunk = { kind: 'callout', text: toneWrap('Playbook tip: Acknowledge delay and offer ETA updates.') }
    const chartAnalyst: AnswerChunk = { kind: 'chart', chartKey: 'frt_trend' }

    const ownerOrder = [kpiFrt, kpiRes, summary, listOps]
    const opsOrder = [listOps, kpiFrt, kpiRes, summary]
    const agentOrder = [calloutAgent, listOps, summary, kpiFrt]
    const analystOrder = [chartAnalyst, summary, kpiFrt, kpiRes]

    const chunks = who === 'owner' ? ownerOrder : who === 'ops' ? opsOrder : who === 'agent' ? agentOrder : analystOrder
    const followUps = who === 'owner' ? ['Impact on CSAT?', 'Trend vs last week']
      : who === 'ops' ? ['Show breaches by channel', 'What is backlog now?']
      : who === 'agent' ? ['Open related playbooks', 'Draft quick reply']
      : ['Open cohorts', 'Segment by channel']

    return {
      id: Math.random().toString(36).slice(2),
      queryId: Math.random().toString(36).slice(2),
      createdAt: Date.now(),
      chunks,
      followUps,
      toolSuggestions: [
        { key: 'open_conversations', label: 'Open VIP queue', params: { filter: 'vip' } },
        { key: 'open_sla', label: 'Open SLA editor' },
      ],
      safety: { piiMasked: true },
    }
  }

  const handleSend = (text: string) => {
    if (offline) { try { track('assistant.offline_send') } catch {}; return }
    const nowTs = Date.now()
    if (nowTs < cooldownUntil) return
    const windowStart = nowTs - 10_000
    const recent = sendTimes.filter((t) => t >= windowStart)
    const nextTimes = [...recent, nowTs]
    if (nextTimes.length > 3) {
      setCooldownUntil(nowTs + 5_000)
      setSendTimes(recent)
      try { track('assistant.cooldown') } catch {}
      return
    }
    setSendTimes(nextTimes)
    try { track('assistant.ask', { surface: 'overlay', persona, tone }) } catch {}
    const ans = makeMockAnswer(text, persona, tone)
    setAnswers((prev) => [ans, ...prev].slice(0, 10))
    setQuery('')
    try { AccessibilityInfo.announceForAccessibility?.(`Answer added with ${ans.chunks.length} sections.`) } catch {}
  }

  const handleTool = (key: ToolKey) => {
    // In overlay demo, no-op; real app would deep-link
    try { track('assistant.tool', { key }) } catch {}
  }

  const sendAndPrefill = (text: string) => {
    setQuery(text)
    setTimeout(() => handleSend(text), 0)
  }

  return (
    <Modal visible={visible} onRequestClose={onClose} animationType="slide" presentationStyle="fullScreen">
      <View style={{ flex: 1, backgroundColor: theme.color.background }}>
        {/* Header */}
        <View style={{ paddingHorizontal: 16, paddingTop: 24, paddingBottom: 8, borderBottomWidth: 1, borderBottomColor: theme.color.border }}>
          <View style={{ flexDirection: 'row', justifyContent: 'space-between', alignItems: 'center' }}>
            <Text style={{ color: theme.color.foreground, fontSize: 18, fontWeight: '700' }}>Assistant</Text>
            <TouchableOpacity onPress={onClose} accessibilityLabel="Close" accessibilityRole="button" style={{ padding: 8, minWidth: 44, minHeight: 44, alignItems: 'center', justifyContent: 'center' }}>
              <Text style={{ color: theme.color.mutedForeground, fontWeight: '700' }}>Close</Text>
            </TouchableOpacity>
          </View>
          <View style={{ marginTop: 8, flexDirection: 'row', gap: 8 }}>
            <TouchableOpacity onPress={() => setOffline((v) => !v)} accessibilityLabel="Toggle offline" accessibilityRole="button" style={{ paddingHorizontal: 10, paddingVertical: 6, borderRadius: 8, borderWidth: 1, borderColor: offline ? theme.color.primary : theme.color.border }}>
              <Text style={{ color: offline ? theme.color.primary : theme.color.mutedForeground, fontWeight: '600' }}>{offline ? 'Offline' : 'Go Offline'}</Text>
            </TouchableOpacity>
            {Date.now() < cooldownUntil && (
              <View style={{ paddingHorizontal: 10, paddingVertical: 6, borderRadius: 999, borderWidth: 1, borderColor: theme.color.border }}>
                <Text style={{ color: theme.color.warning, fontWeight: '700' }}>Cooldown…</Text>
              </View>
            )}
          </View>
          {/* Tabs */}
          <View style={{ flexDirection: 'row', gap: 12, marginTop: 8 }}>
            {(['recent','pins','templates'] as const).map(k => (
              <TouchableOpacity key={k} onPress={() => setTab(k)} accessibilityLabel={`tab ${k}`}>
                <Text style={{ color: tab === k ? theme.color.primary : theme.color.mutedForeground, fontWeight: '600' }}>{k.toUpperCase()}</Text>
              </TouchableOpacity>
            ))}
          </View>
        </View>

        {/* Body */}
        {tab === 'recent' && (
          <View style={{ flex: 1 }}>
            <View style={{ paddingHorizontal: 16, paddingTop: 16 }}>
              <View style={{ flexDirection: 'row', gap: 8 }}>
                <TouchableOpacity onPress={() => { const h = findNodeHandle(inputContainerRef.current); if (h) AccessibilityInfo.setAccessibilityFocus?.(h) }} accessibilityLabel="Skip to input" accessibilityRole="button" style={{ paddingHorizontal: 10, paddingVertical: 6, borderRadius: 8, borderWidth: 1, borderColor: theme.color.border }}>
                  <Text style={{ color: theme.color.mutedForeground, fontWeight: '600' }}>Skip to input</Text>
                </TouchableOpacity>
                <TouchableOpacity onPress={() => listRef.current?.scrollToOffset?.({ offset: 0, animated: true })} accessibilityLabel="Skip to newest answer" accessibilityRole="button" style={{ paddingHorizontal: 10, paddingVertical: 6, borderRadius: 8, borderWidth: 1, borderColor: theme.color.border }}>
                  <Text style={{ color: theme.color.mutedForeground, fontWeight: '600' }}>Skip to newest</Text>
                </TouchableOpacity>
              </View>
              <View style={{ marginTop: 12 }}>
                <ShortcutGrid shortcuts={[{ id: 'd1', label: 'Daily Brief', action: { key: 'open_analytics', label: 'Daily Brief' } } as Shortcut]} onPress={() => { try { track('assistant.template.use', { id: 'daily_brief' }) } catch {}; navigation.navigate('Analytics', { screen: 'DailyBrief' }) }} />
              </View>
            </View>
            <FlatList
              ref={listRef}
              contentContainerStyle={{ padding: 16, paddingBottom: 140 }}
              data={answers}
              keyExtractor={(a) => a.id}
              renderItem={({ item }) => (
                <View style={{ marginBottom: 12 }} accessibilityLiveRegion="polite">
                  <MemoAnswerCard answer={item} hidePII onTool={(k) => handleTool(k as ToolKey)} onFollowUp={(s) => sendAndPrefill(s)} />
                </View>
              )}
            />
          </View>
        )}
        {tab === 'templates' && (
          <View style={{ flex: 1, padding: 16, paddingBottom: 140 }}>
            <PromptLibrary
              templates={templates}
              onUse={(tpl) => sendAndPrefill(tpl.text)}
              onUpdate={(tpl) => setTemplates(arr => arr.map(x => x.id === tpl.id ? tpl : x))}
              onDuplicate={(tpl) => setTemplates(arr => [{ ...tpl, id: Math.random().toString(36).slice(2), name: tpl.name + ' copy' }, ...arr])}
              onPinToDashboard={(tpl) => {}}
            />
          </View>
        )}
        {tab === 'pins' && (
          <PinsScreen onOpen={(pin: MemoryPin) => sendAndPrefill(pin.content)} />
        )}

        {/* Bottom composer */}
        <View ref={inputContainerRef} style={{ position: 'absolute', bottom: 0, left: 0, right: 0, padding: 12, backgroundColor: theme.color.card, borderTopWidth: 1, borderTopColor: theme.color.border }}>
          <AskInput
            value={query}
            onChangeText={setQuery}
            onSend={handleSend}
            persona={persona}
            onPersonaChange={setPersona}
            tone={tone}
            onToneChange={setTone}
            rangeLabel="Last 24h"
            disabled={offline || Date.now() < cooldownUntil}
          />
        </View>
      </View>
    </Modal>
  )
}

export default AssistantOverlay


