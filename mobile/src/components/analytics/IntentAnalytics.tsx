import React from 'react'
import { View, Text, TouchableOpacity, ScrollView } from 'react-native'
import { useNavigation } from '@react-navigation/native'
import { tokens } from '../../ui/tokens'
import { BreakdownRow } from '../../types/analytics'

export interface IntentAnalyticsProps { testID?: string }

interface IntentRow extends BreakdownRow {
  delta?: { share?: number; frtP50?: number; resolutionRate?: number; deflection?: number; repeat?: number }
}

const INTENTS = ['order', 'billing', 'returns', 'shipping', 'support']

const BarInline: React.FC<{ pct: number }> = ({ pct }) => (
  <View style={{ height: 8, backgroundColor: tokens.colors.muted, borderRadius: 8, overflow: 'hidden' }}>
    <View style={{ width: `${Math.max(0, Math.min(100, pct))}%`, backgroundColor: tokens.colors.primary, height: '100%' }} />
  </View>
)

const DeltaText: React.FC<{ v?: number }> = ({ v }) => {
  if (v == null) return <Text style={{ color: tokens.colors.mutedForeground }}>—</Text>
  const color = v >= 0 ? tokens.colors.success : tokens.colors.error
  const arrow = v >= 0 ? '▲' : '▼'
  return <Text style={{ color, fontWeight: '600' }}>{arrow} {Math.abs(v)}%</Text>
}

const IntentAnalytics: React.FC<IntentAnalyticsProps> = ({ testID }) => {
  const navigation = useNavigation<any>()

  const [rows, setRows] = React.useState<IntentRow[]>(() => INTENTS.map((name) => ({
    name,
    value: Math.round(100 + Math.random() * 400),
    sharePct: Math.round(5 + Math.random() * 40),
    extra: {
      frtP50: Math.round(20 + Math.random() * 60),
      resolutionRate: Math.round(65 + Math.random() * 30),
      deflection: Math.round(20 + Math.random() * 50),
      repeatContactRate: Math.round(5 + Math.random() * 25),
    },
    delta: {
      share: Math.round(Math.random() * 10 - 5),
      frtP50: Math.round(Math.random() * 10 - 5),
      resolutionRate: Math.round(Math.random() * 10 - 5),
      deflection: Math.round(Math.random() * 10 - 5),
      repeat: Math.round(Math.random() * 10 - 5),
    },
  })))

  const openConversations = (intent: string) => {
    navigation.navigate('Conversations', { screen: 'ConversationsHome', params: { prefill: intent, filter: undefined } })
  }

  const createAutomation = (intent: string) => {
    navigation.navigate('Automations', {
      screen: 'RuleBuilder',
      params: { rule: { id: `tmp-${Date.now()}`, name: `WHEN intent is ${intent}`, when: [{ key: 'intent', op: 'is', value: intent }], then: [{ key: 'autoReply', params: { message: 'Thanks! We will help shortly.' } }], enabled: true, order: 0 }, currentMaxOrder: 0 }
    })
  }

  return (
    <View testID={testID} style={{ borderWidth: 1, borderColor: tokens.colors.border, borderRadius: 12, overflow: 'hidden' }}>
      <View style={{ padding: 12, backgroundColor: tokens.colors.card, borderBottomWidth: 1, borderBottomColor: tokens.colors.border }}>
        <Text style={{ color: tokens.colors.cardForeground, fontWeight: '700' }}>Intent Analytics</Text>
      </View>
      <ScrollView horizontal showsHorizontalScrollIndicator={false}>
        <View style={{ minWidth: 720 }}>
          <View style={{ flexDirection: 'row', backgroundColor: tokens.colors.card, borderBottomWidth: 1, borderBottomColor: tokens.colors.border }}>
            {['Intent', 'Share %', 'FRT P50', 'Resolution %', 'Deflection %', 'Repeat %', 'Actions'].map((c, idx) => (
              <View key={idx} style={{ paddingHorizontal: 12, paddingVertical: 8, minWidth: 120 }}>
                <Text style={{ color: tokens.colors.cardForeground, fontWeight: '700' }}>{c}</Text>
              </View>
            ))}
          </View>
          <ScrollView style={{ maxHeight: 320 }}>
            {rows.map((r, idx) => (
              <View key={idx} style={{ flexDirection: 'row', alignItems: 'center', borderBottomWidth: 1, borderBottomColor: tokens.colors.border }}>
                <View style={{ paddingHorizontal: 12, paddingVertical: 8, minWidth: 140 }}>
                  <Text style={{ color: tokens.colors.cardForeground, fontWeight: '600' }}>{r.name}</Text>
                </View>
                <View style={{ paddingHorizontal: 12, paddingVertical: 8, minWidth: 160 }}>
                  <BarInline pct={r.sharePct || 0} />
                  <View style={{ flexDirection: 'row', justifyContent: 'space-between', marginTop: 4 }}>
                    <Text style={{ color: tokens.colors.mutedForeground }}>{r.sharePct}%</Text>
                    <DeltaText v={r.delta?.share} />
                  </View>
                </View>
                <View style={{ paddingHorizontal: 12, paddingVertical: 8, minWidth: 120 }}>
                  <View style={{ flexDirection: 'row', gap: 6, alignItems: 'center' }}>
                    <Text style={{ color: tokens.colors.cardForeground }}>{r.extra?.frtP50}m</Text>
                    <DeltaText v={r.delta?.frtP50} />
                  </View>
                </View>
                <View style={{ paddingHorizontal: 12, paddingVertical: 8, minWidth: 140 }}>
                  <View style={{ flexDirection: 'row', gap: 6, alignItems: 'center' }}>
                    <Text style={{ color: tokens.colors.cardForeground }}>{r.extra?.resolutionRate}%</Text>
                    <DeltaText v={r.delta?.resolutionRate} />
                  </View>
                </View>
                <View style={{ paddingHorizontal: 12, paddingVertical: 8, minWidth: 140 }}>
                  <View style={{ flexDirection: 'row', gap: 6, alignItems: 'center' }}>
                    <Text style={{ color: tokens.colors.cardForeground }}>{r.extra?.deflection}%</Text>
                    <DeltaText v={r.delta?.deflection} />
                  </View>
                </View>
                <View style={{ paddingHorizontal: 12, paddingVertical: 8, minWidth: 120 }}>
                  <View style={{ flexDirection: 'row', gap: 6, alignItems: 'center' }}>
                    <Text style={{ color: tokens.colors.cardForeground }}>{r.extra?.repeatContactRate}%</Text>
                    <DeltaText v={r.delta?.repeat} />
                  </View>
                </View>
                <View style={{ paddingHorizontal: 12, paddingVertical: 8, minWidth: 220 }}>
                  <View style={{ flexDirection: 'row', gap: 8, flexWrap: 'wrap' }}>
                    <TouchableOpacity onPress={() => openConversations(r.name)} accessibilityRole="button" accessibilityLabel={`Open conversations ${r.name}`} style={{ paddingHorizontal: 10, paddingVertical: 6, borderRadius: 8, borderWidth: 1, borderColor: tokens.colors.border }}>
                      <Text style={{ color: tokens.colors.cardForeground, fontWeight: '600' }}>Open conversations</Text>
                    </TouchableOpacity>
                    <TouchableOpacity onPress={() => createAutomation(r.name)} accessibilityRole="button" accessibilityLabel={`Create automation ${r.name}`} style={{ paddingHorizontal: 10, paddingVertical: 6, borderRadius: 8, backgroundColor: tokens.colors.primary }}>
                      <Text style={{ color: tokens.colors.primaryForeground, fontWeight: '700' }}>Create automation…</Text>
                    </TouchableOpacity>
                  </View>
                </View>
              </View>
            ))}
          </ScrollView>
        </View>
      </ScrollView>
    </View>
  )
}

export default React.memo(IntentAnalytics)

