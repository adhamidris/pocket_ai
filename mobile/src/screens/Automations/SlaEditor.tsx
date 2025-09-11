import React from 'react'
import { SafeAreaView, View, Text, TouchableOpacity, TextInput, ScrollView, Switch } from 'react-native'
import { useSafeAreaInsets } from 'react-native-safe-area-context'
import { useTheme } from '../../providers/ThemeProvider'
import { useNavigation, useRoute } from '@react-navigation/native'
import { SlaPolicy } from '../../types/automations'
import SlaTargetEditor from '../../components/automations/SlaTargetEditor'
import BreachBadge from '../../components/automations/BreachBadge'
import OfflineBanner from '../../components/dashboard/OfflineBanner'
import { track } from '../../lib/analytics'

export interface SlaEditorParams {
  policy: SlaPolicy
  onApply?: (p: SlaPolicy) => void
}

// Demo current metrics to compare against targets
const currentDemo = {
  vip: { p50: 90, p90: 240 },
  high: { p50: 180, p90: 420 },
  normal: { p50: 360, p90: 1500 },
  low: { p50: 600, p90: 2400 },
}

const SlaEditor: React.FC = () => {
  const { theme } = useTheme()
  const insets = useSafeAreaInsets()
  const navigation = useNavigation<any>()
  const route = useRoute<any>()
  const params: SlaEditorParams = route.params || {}

  const [policy, setPolicy] = React.useState<SlaPolicy>(params.policy)
  const [nameDraft, setNameDraft] = React.useState<string>(params.policy?.name || '')
  React.useEffect(() => {
    const t = setTimeout(() => setPolicy((p) => ({ ...p, name: nameDraft })), 250)
    return () => clearTimeout(t)
  }, [nameDraft])
  const [offline, setOffline] = React.useState(false)

  const save = () => {
    try { params.onApply && params.onApply(policy); track('sla.save', { targets: policy.targets.length }) } catch {}
    navigation.goBack()
  }

  const guidance = 'Guidance: VIP ≤ 120s P50 / 300s P90; High ≤ 300s / 600s; Normal ≤ 600s / 1800s.'

  return (
    <SafeAreaView style={{ flex: 1, backgroundColor: theme.color.background }}>
      {/* Header */}
      <View style={{ paddingHorizontal: 16, paddingTop: insets.top + 8, paddingBottom: 12, borderBottomWidth: 1, borderBottomColor: theme.color.border }}>
        <View style={{ flexDirection: 'row', alignItems: 'center', justifyContent: 'space-between' }}>
          <TouchableOpacity onPress={() => navigation.goBack()} accessibilityLabel="Back" accessibilityRole="button" style={{ padding: 8 }}>
            <Text style={{ color: theme.color.primary, fontWeight: '600' }}>{'< Back'}</Text>
          </TouchableOpacity>
          <Text style={{ color: theme.color.foreground, fontSize: 18, fontWeight: '700' }}>SLA Editor</Text>
          <View style={{ width: 64 }} />
        </View>
      </View>

      <ScrollView style={{ padding: 16 }} contentContainerStyle={{ paddingBottom: 24 }}>
        {offline && <OfflineBanner visible />}
        {/* Name & pause */}
        <View style={{ marginBottom: 12 }}>
          <Text style={{ color: theme.color.mutedForeground, marginBottom: 6 }}>Policy name</Text>
          <TextInput value={nameDraft} onChangeText={setNameDraft} placeholder="Default SLA" placeholderTextColor={theme.color.placeholder} accessibilityLabel="Policy name" style={{ borderWidth: 1, borderColor: theme.color.border, borderRadius: 12, paddingHorizontal: 12, paddingVertical: 10, color: theme.color.cardForeground }} />
          <View style={{ flexDirection: 'row', alignItems: 'center', justifyContent: 'space-between', marginTop: 8 }}>
            <Text style={{ color: theme.color.mutedForeground }}>Pause outside business hours</Text>
            <Switch value={policy.pauseOutsideHours} onValueChange={(v) => setPolicy((p) => ({ ...p, pauseOutsideHours: v }))} accessibilityLabel="Pause outside hours" />
          </View>
        </View>

        {/* Guidance */}
        <View style={{ borderWidth: 1, borderColor: theme.color.border, borderRadius: 12, padding: 12, backgroundColor: theme.color.card, marginBottom: 12 }}>
          <Text style={{ color: theme.color.mutedForeground }}>{guidance}</Text>
        </View>

        {/* Targets editor */}
        <View style={{ marginBottom: 12 }}>
          <SlaTargetEditor policy={policy} onChange={setPolicy} />
        </View>

        {/* Breach preview */}
        <View style={{ gap: 8, marginBottom: 16 }}>
          {policy.targets.map((t, idx) => {
            const cur = currentDemo[t.priority] || currentDemo.normal
            const breach = (cur.p50 > t.frtP50Sec) || (cur.p90 > t.frtP90Sec)
            return (
              <View key={idx} style={{ flexDirection: 'row', alignItems: 'center', gap: 8 }}>
                <BreachBadge level={breach ? 'breach' : 'ok'} label={`${t.priority.toUpperCase()} • current P50 ${Math.round(cur.p50/60)}m vs target ${Math.round(t.frtP50Sec/60)}m`} />
              </View>
            )
          })}
        </View>

        {/* Save */}
        <View style={{ flexDirection: 'row', justifyContent: 'flex-end' }}>
          <TouchableOpacity onPress={save} accessibilityLabel="Save Policy" accessibilityRole="button" style={{ paddingHorizontal: 12, paddingVertical: 10, borderWidth: 1, borderColor: theme.color.border, borderRadius: 12 }} testID="auto-sla-save">
            <Text style={{ color: theme.color.primary, fontWeight: '700' }}>Save Policy</Text>
          </TouchableOpacity>
        </View>
      </ScrollView>
    </SafeAreaView>
  )
}

export default SlaEditor


