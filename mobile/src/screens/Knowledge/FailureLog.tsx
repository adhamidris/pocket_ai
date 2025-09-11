import React from 'react'
import { SafeAreaView, View, Text, TouchableOpacity, Alert, FlatList, TextInput, DeviceEventEmitter } from 'react-native'
import { useSafeAreaInsets } from 'react-native-safe-area-context'
import { useTheme } from '../../providers/ThemeProvider'
import { useNavigation, useRoute } from '@react-navigation/native'
import { FailureItem, KnowledgeSource } from '../../types/knowledge'
import FailureRow from '../../components/knowledge/FailureRow'
import TagChip from '../../components/crm/TagChip'
import { track } from '../../lib/analytics'
import UpsellInline from '../../components/billing/UpsellInline'
import { useEntitlements } from '../../components/billing/entitlements'

const rand = (min: number, max: number) => Math.floor(Math.random() * (max - min + 1)) + min
const pick = <T,>(arr: T[]): T => arr[rand(0, arr.length - 1)]

const genFailures = (): FailureItem[] => {
  const intents = ['billing', 'order', 'shipping', 'returns']
  const channels: Array<'whatsapp' | 'instagram' | 'facebook' | 'web' | undefined> = ['whatsapp', 'instagram', 'facebook', 'web']
  const items: FailureItem[] = []
  for (let i = 0; i < 24; i++) {
    items.push({
      id: `f-${i + 1}`,
      question: `Sample failed Q #${i + 1}`,
      matchedIntent: Math.random() > 0.5 ? pick(intents) : undefined,
      confidence: Math.random(),
      occurredAt: Date.now() - rand(0, 1000 * 60 * 60 * 24 * 14),
      channel: pick(channels),
      conversationId: `c-${rand(1, 30)}`,
      suggestedSourceKind: Math.random() > 0.5 ? 'note' : 'url',
    })
  }
  return items
}

const FailureLog: React.FC = () => {
  const { theme } = useTheme()
  const insets = useSafeAreaInsets()
  const navigation = useNavigation<any>()
  const route = useRoute<any>()

  const initialFailures: FailureItem[] = route.params?.failures || genFailures()
  const sources: KnowledgeSource[] = route.params?.sources || []

  const [threshold, setThreshold] = React.useState<number>(70)
  const [channel, setChannel] = React.useState<string | undefined>()
  const [intent, setIntent] = React.useState<string | undefined>()
  const [time, setTime] = React.useState<'24h' | '7d' | '30d' | undefined>()
  const [resolved, setResolved] = React.useState<Record<string, boolean>>({})
  const [list, setList] = React.useState<FailureItem[]>(initialFailures)
  const [query, setQuery] = React.useState('')

  const filtered = React.useMemo(() => {
    let arr = list.filter((f) => !resolved[f.id])
    arr = arr.filter((f) => Math.round(f.confidence * 100) < threshold)
    if (channel) arr = arr.filter((f) => f.channel === channel)
    if (intent) arr = arr.filter((f) => f.matchedIntent === intent)
    if (time) {
      const now = Date.now()
      const win = time === '24h' ? 86400000 : time === '7d' ? 604800000 : 2592000000
      arr = arr.filter((f) => f.occurredAt >= now - win)
    }
    if (query.trim()) arr = arr.filter((f) => f.question.toLowerCase().includes(query.trim().toLowerCase()))
    return arr
  }, [list, resolved, threshold, channel, intent, time, query])

  const ents = useEntitlements()
  const canAttach = !!ents['knowledgeSources']?.enabled

  const openActions = (item: FailureItem) => {
    const options = [
      { text: 'Open conversation', onPress: () => { track('knowledge.failure_action', { action: 'open' }); navigation.navigate('Conversations', { screen: 'ConversationThread', params: { id: item.conversationId } }) } },
      { text: 'Attach to source', onPress: () => { track('knowledge.failure_action', { action: 'attach' }); attachToSource(item) } },
      { text: 'Auto-reply with FAQ', onPress: () => {
        const when = [
          { key: 'intent', op: 'is', value: item.matchedIntent || 'faq' } as any,
          { key: 'withinHours', op: 'false' } as any,
        ]
        const then = [ { key: 'autoReply', params: { message: 'Here is a helpful FAQ answer…' } } as any ]
        navigation.navigate('Automations', { screen: 'RuleBuilder', params: { rule: { id: `tmp-${Date.now()}`, name: 'Auto-reply with FAQ', when, then, enabled: true, order: 0 }, currentMaxOrder: 0 } })
      } },
      { text: 'Create Note', onPress: () => { track('knowledge.failure_action', { action: 'createNote' }); navigation.navigate('Knowledge', { screen: 'NoteEditor', params: { note: { id: '', kind: 'note', title: item.question, enabled: true, scope: 'global', status: 'idle', content: item.question }, onSave: (note: any) => {/* could reflect state */} } }) } },
      { text: 'Mark resolved', onPress: () => { track('knowledge.failure_action', { action: 'resolve' }); setResolved((r) => ({ ...r, [item.id]: true })) } },
      { text: 'Cancel', style: 'cancel' as const },
    ]
    // @ts-ignore
    Alert.alert('Failure', item.question, options)
  }

  const attachToSource = (item: FailureItem) => {
    if (!canAttach) {
      // Show inline upsell via alert replacement
      // @ts-ignore
      Alert.alert('Upgrade required', 'Attach failures to sources is premium. Upgrade to unlock.', [
        { text: 'Upgrade', onPress: () => navigation.navigate('PlanMatrix', { highlightPlanId: 'starter' }) },
        { text: 'Cancel', style: 'cancel' }
      ])
      return
    }
    if (!sources.length) { Alert.alert('No sources', 'No sources to attach.'); return }
    const opts = sources.slice(0, 5).map((s) => ({ text: s.title, onPress: () => Alert.alert('Attached', `Attached to ${s.title} (UI-only).`) }))
    // @ts-ignore
    Alert.alert('Attach to…', 'Choose a source', [...opts, { text: 'Cancel', style: 'cancel' }])
  }

  return (
    <SafeAreaView style={{ flex: 1, backgroundColor: theme.color.background }}>
      {/* Header */}
      <View style={{ paddingHorizontal: 16, paddingTop: insets.top + 8, paddingBottom: 12, borderBottomWidth: 1, borderBottomColor: theme.color.border }}>
        <View style={{ flexDirection: 'row', alignItems: 'center', justifyContent: 'space-between' }}>
          <TouchableOpacity onPress={() => navigation.goBack()} accessibilityLabel="Back" accessibilityRole="button" style={{ padding: 8 }}>
            <Text style={{ color: theme.color.primary, fontWeight: '600' }}>{'< Back'}</Text>
          </TouchableOpacity>
          <Text style={{ color: theme.color.foreground, fontSize: 18, fontWeight: '700' }}>Failure Log</Text>
          <View style={{ flexDirection: 'row', gap: 8 }}>
            <TouchableOpacity onPress={() => DeviceEventEmitter.emit('assistant.open', { text: 'Draft reply from FAQ for recent failures', persona: 'agent' })} accessibilityRole="button" accessibilityLabel="Draft reply from FAQ" style={{ paddingHorizontal: 10, paddingVertical: 6, borderRadius: 8, borderWidth: 1, borderColor: theme.color.border }}>
              <Text style={{ color: theme.color.cardForeground, fontWeight: '600' }}>Draft reply</Text>
            </TouchableOpacity>
          </View>
        </View>
      </View>

      {/* Filters */}
      <View style={{ padding: 16 }}>
        <View style={{ flexDirection: 'row', flexWrap: 'wrap', gap: 8, marginBottom: 8 }}>
          {[50, 60, 70, 80, 90].map((t) => (
            <TagChip key={t} label={`Conf < ${t}%`} selected={threshold === t} onPress={() => setThreshold(t)} />
          ))}
          {(['whatsapp', 'instagram', 'facebook', 'web'] as const).map((ch) => (
            <TagChip key={ch} label={ch} selected={channel === ch} onPress={() => setChannel(channel === ch ? undefined : ch)} />
          ))}
          {(['billing', 'order', 'shipping', 'returns'] as const).map((it) => (
            <TagChip key={it} label={it} selected={intent === it} onPress={() => setIntent(intent === it ? undefined : it)} />
          ))}
          {(['24h', '7d', '30d'] as const).map((w) => (
            <TagChip key={w} label={w} selected={time === w} onPress={() => setTime(time === w ? undefined : w)} />
          ))}
        </View>
        <View style={{ borderWidth: 1, borderColor: theme.color.border, borderRadius: 12, backgroundColor: theme.color.card }}>
          <TextInput value={query} onChangeText={setQuery} placeholder="Search question" placeholderTextColor={theme.color.placeholder} style={{ paddingHorizontal: 12, paddingVertical: 10, color: theme.color.cardForeground }} />
        </View>
      </View>

      {/* List */}
      <FlatList
        style={{ paddingHorizontal: 16 }}
        data={filtered}
        keyExtractor={(i) => i.id}
        renderItem={({ item }) => (
          <View style={{ marginBottom: 8 }}>
            <FailureRow item={item} onPress={() => openActions(item)} />
          </View>
        )}
      />
    </SafeAreaView>
  )
}

export default FailureLog


