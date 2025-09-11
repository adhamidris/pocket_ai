import React from 'react'
import { SafeAreaView, View, Text, TouchableOpacity, ScrollView, Alert } from 'react-native'
import { useSafeAreaInsets } from 'react-native-safe-area-context'
import { useTheme } from '../../providers/ThemeProvider'
import { AllowRule, CapabilityPack } from '../../types/actions'
import { track } from '../../lib/analytics'

type Params = { rules?: AllowRule[]; onChangeRules?: (next: AllowRule[]) => void }

const packs: CapabilityPack[] = [
  { id: 'retail', name: 'Retail / E‑commerce', industryTags: ['retail','e‑comm'], actionIds: ['conv.reply','conv.close','orders.refund','analytics.export'], recommended: true },
  { id: 'services', name: 'Services', industryTags: ['services'], actionIds: ['conv.reply','crm.update','analytics.export'], recommended: false },
  { id: 'saas', name: 'SaaS', industryTags: ['saas'], actionIds: ['conv.reply','automations.rule','analytics.export'], recommended: false },
  { id: 'hospitality', name: 'Hospitality', industryTags: ['hospitality'], actionIds: ['conv.reply','conv.close','crm.update'], recommended: false },
]

const lowRisk: Record<string, boolean> = {
  'conv.reply': true,
  'analytics.export': true,
  'crm.update': false,
  'conv.close': false,
  'orders.refund': false,
  'automations.rule': false,
}

const CapabilityPacks: React.FC<{ route?: { params?: Params } }>=({ route }) => {
  const { theme } = useTheme()
  const insets = useSafeAreaInsets()
  const initialRules = route?.params?.rules || []
  const onChangeRules = route?.params?.onChangeRules

  const [rules, setRules] = React.useState<AllowRule[]>(initialRules)
  const [applied, setApplied] = React.useState<Record<string, boolean>>({})

  const applyPack = (pack: CapabilityPack) => {
    try { track('actions.pack.apply', { packId: pack.id }) } catch {}
    const created: AllowRule[] = []
    const bh = { start: '09:00' as const, end: '17:00' as const }
    const weekdays = [1,2,3,4,5]
    let next = rules.slice()
    for (const actionId of pack.actionIds) {
      const exists = next.find(r => r.actionId === actionId)
      if (exists) continue
      const isLow = !!lowRisk[actionId]
      const rule: AllowRule = {
        actionId,
        timeOfDay: bh,
        weekdays,
        guard: isLow ? undefined : { businessHoursOnly: true },
        requireApproval: !isLow,
        approverRole: !isLow ? 'owner' : undefined,
      }
      created.push(rule)
      next = [rule, ...next]
    }
    setRules(next)
    setApplied((a) => ({ ...a, [pack.id]: true }))
    onChangeRules?.(next)
    if (created.length === 0) Alert.alert('No changes', 'All rules already applied.')
  }

  const revertPack = (pack: CapabilityPack) => {
    try { track('actions.pack.revert', { packId: pack.id }) } catch {}
    const ids = new Set(pack.actionIds)
    const next = rules.filter(r => !ids.has(r.actionId))
    setRules(next)
    setApplied((a) => ({ ...a, [pack.id]: false }))
    onChangeRules?.(next)
  }

  return (
    <SafeAreaView style={{ flex: 1, backgroundColor: theme.color.background }}>
      <ScrollView contentContainerStyle={{ paddingBottom: 24 }}>
        <View style={{ paddingHorizontal: 24, paddingTop: insets.top + 12, paddingBottom: 12 }}>
          <Text style={{ color: theme.color.foreground, fontSize: 24, fontWeight: '700' }}>Capability Packs</Text>
        </View>
        <View style={{ paddingHorizontal: 24, gap: 12 }}>
          {packs.map((p) => (
            <View key={p.id} style={{ borderWidth: 1, borderColor: theme.color.border, borderRadius: 12, backgroundColor: theme.color.card, padding: 12 }}>
              <View style={{ flexDirection: 'row', alignItems: 'center', justifyContent: 'space-between' }}>
                <View>
                  <Text style={{ color: theme.color.cardForeground, fontWeight: '700' }}>{p.name}</Text>
                  <Text style={{ color: theme.color.mutedForeground, marginTop: 4, fontSize: 12 }}>{(p.industryTags || []).join(' • ')}</Text>
                </View>
                {p.recommended && (
                  <View style={{ paddingHorizontal: 10, paddingVertical: 6, borderRadius: 999, borderWidth: 1, borderColor: theme.color.border, backgroundColor: theme.color.secondary }}>
                    <Text style={{ color: theme.color.mutedForeground, fontWeight: '600', fontSize: 12 }}>Recommended</Text>
                  </View>
                )}
              </View>
              <View style={{ marginTop: 8 }}>
                <Text style={{ color: theme.color.cardForeground, fontWeight: '700', marginBottom: 6 }}>Included actions</Text>
                {(p.actionIds).map((a) => (
                  <Text key={a} style={{ color: theme.color.mutedForeground }}>• {a} {lowRisk[a] ? '(low)' : '(approval required)'}</Text>
                ))}
              </View>
              <View style={{ flexDirection: 'row', gap: 8, marginTop: 12 }}>
                <TouchableOpacity onPress={() => applyPack(p)} accessibilityLabel={`Apply ${p.name}`} accessibilityRole="button" style={{ paddingHorizontal: 12, paddingVertical: 10, borderRadius: 12, backgroundColor: theme.color.primary }}>
                  <Text style={{ color: '#fff', fontWeight: '700' }}>{applied[p.id] ? 'Reapply' : 'Apply Pack'}</Text>
                </TouchableOpacity>
                <TouchableOpacity onPress={() => revertPack(p)} accessibilityLabel={`Revert ${p.name}`} accessibilityRole="button" style={{ paddingHorizontal: 12, paddingVertical: 10, borderRadius: 12, borderWidth: 1, borderColor: theme.color.border }}>
                  <Text style={{ color: theme.color.cardForeground, fontWeight: '600' }}>Revert</Text>
                </TouchableOpacity>
              </View>
            </View>
          ))}
        </View>
      </ScrollView>
    </SafeAreaView>
  )
}

export default CapabilityPacks


