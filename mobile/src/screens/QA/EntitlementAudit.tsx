import React from 'react'
import { SafeAreaView, View, Text, TouchableOpacity } from 'react-native'
import { useTheme } from '../../providers/ThemeProvider'
import { useSafeAreaInsets } from 'react-native-safe-area-context'
import { setEntitlements, getEntitlements } from '../../components/billing/entitlements'
import { useNavigation } from '@react-navigation/native'

type Plan = 'free' | 'starter' | 'pro' | 'scale'

const PRESETS: Record<Plan, Record<string, { enabled: boolean; limit?: number | 'unlimited' }>> = {
  free: { analytics: { enabled: false }, automations: { enabled: false }, portal: { enabled: true, limit: 1 } },
  starter: { analytics: { enabled: true }, automations: { enabled: true, limit: 2 }, portal: { enabled: true, limit: 2 } },
  pro: { analytics: { enabled: true }, automations: { enabled: true, limit: 10 }, portal: { enabled: true, limit: 10 } },
  scale: { analytics: { enabled: true }, automations: { enabled: true, limit: 'unlimited' }, portal: { enabled: true, limit: 'unlimited' } },
}

export const EntitlementAudit: React.FC = () => {
  const { theme } = useTheme()
  const insets = useSafeAreaInsets()
  const navigation = useNavigation<any>()
  const [plan, setPlan] = React.useState<Plan>('free')
  const [snap, setSnap] = React.useState<any>({})

  const applyPlan = (p: Plan) => {
    setPlan(p)
    setEntitlements(PRESETS[p])
    setSnap(getEntitlements())
  }

  React.useEffect(() => { applyPlan('free') }, [])

  const Chip: React.FC<{ p: Plan }> = ({ p }) => (
    <TouchableOpacity onPress={() => applyPlan(p)} accessibilityRole="button" accessibilityLabel={`Plan ${p}`} style={{ paddingHorizontal: 10, paddingVertical: 8, borderRadius: 10, borderWidth: 1, borderColor: plan === p ? theme.color.primary : theme.color.border }}>
      <Text style={{ color: theme.color.cardForeground, fontWeight: '700' }}>{p}</Text>
    </TouchableOpacity>
  )

  const GateRow: React.FC<{ feature: string; path: any }> = ({ feature, path }) => {
    const ent = snap[feature]
    const gated = !ent?.enabled
    return (
      <View style={{ flexDirection: 'row', alignItems: 'center', justifyContent: 'space-between', paddingHorizontal: 12, paddingVertical: 10, borderBottomWidth: 1, borderBottomColor: theme.color.border }}>
        <Text style={{ color: theme.color.cardForeground, fontWeight: '600' }}>{feature}</Text>
        <View style={{ flexDirection: 'row', alignItems: 'center', gap: 8 }}>
          <Text style={{ color: gated ? theme.color.warning : theme.color.success, fontWeight: '800' }}>{gated ? 'GATED' : 'OK'}</Text>
          <TouchableOpacity onPress={() => navigation.navigate('Main', path)} accessibilityRole="button" accessibilityLabel={`Open ${feature}`} style={{ paddingHorizontal: 10, paddingVertical: 6, borderRadius: 8, borderWidth: 1, borderColor: theme.color.border }}>
            <Text style={{ color: theme.color.cardForeground }}>Open</Text>
          </TouchableOpacity>
        </View>
      </View>
    )
  }

  return (
    <SafeAreaView style={{ flex: 1, backgroundColor: theme.color.background }}>
      <View style={{ paddingHorizontal: 16, paddingTop: insets.top + 12, paddingBottom: 12 }}>
        <Text style={{ color: theme.color.foreground, fontSize: 22, fontWeight: '700', marginBottom: 8 }}>Entitlement Audit</Text>
        <View style={{ flexDirection: 'row', gap: 8 }}>
          {(['free','starter','pro','scale'] as Plan[]).map(p => <Chip key={p} p={p} />)}
        </View>
      </View>

      <View style={{ paddingHorizontal: 16 }}>
        <View style={{ borderWidth: 1, borderColor: theme.color.border, borderRadius: 12 }}>
          <GateRow feature="analytics" path={{ screen: 'Analytics' }} />
          <GateRow feature="automations" path={{ screen: 'Automations' }} />
          <GateRow feature="portal" path={{ screen: 'Portal' }} />
        </View>
      </View>
    </SafeAreaView>
  )
}

export default EntitlementAudit


