import React from 'react'
import { SafeAreaView, View, Text, TouchableOpacity, ScrollView, Alert, Switch } from 'react-native'
import { useTheme } from '../../providers/ThemeProvider'
import { UsageMeter } from '../../types/billing'
import UsageBar from '../../components/billing/UsageBar'
import { useRoute } from '@react-navigation/native'
import AsyncStorage from '@react-native-async-storage/async-storage'

type Params = { usage?: UsageMeter[] }

const meterNotes: Record<string, string> = {
  messages: 'Messages sent by agents and automations this period.',
  mtu: 'Monthly Tracked Users. Unique active users this period.',
  uploadsMB: 'Total uploads size in megabytes this period.',
  knowledgeSources: 'Number of connected knowledge sources.',
  automations: 'Active automation workflows configured.',
  agents: 'Number of enabled AI or human agents.'
}

const keyAutoUpgrade = 'billing_auto_upgrade'

const percentOf = (m: UsageMeter) => (m.limit === 'unlimited' ? 0 : Math.min(100, Math.round((m.used / Math.max(1, m.limit)) * 100)))

const Banner: React.FC<{ tone: 'warning'|'danger'; text: string }> = ({ tone, text }) => {
  const { theme } = useTheme()
  const color = tone === 'danger' ? theme.color.error : theme.color.warning
  const bg = tone === 'danger' ? '#291314' : '#2a2412'
  return (
    <View style={{ borderWidth: 1, borderColor: color, backgroundColor: bg, padding: 10, borderRadius: 10 }}>
      <Text style={{ color, fontWeight: '700' }}>{text}</Text>
    </View>
  )
}

const UsageLimitsScreen: React.FC = () => {
  const { theme } = useTheme()
  const route = useRoute<any>()
  const params = (route.params || {}) as Params
  const [usage, setUsage] = React.useState<UsageMeter[]>(params.usage || [])
  const [autoUpgrade, setAutoUpgrade] = React.useState<boolean>(false)

  React.useEffect(() => {
    (async () => {
      try {
        const saved = await AsyncStorage.getItem(keyAutoUpgrade)
        if (saved != null) setAutoUpgrade(saved === '1')
      } catch {}
    })()
  }, [])

  const toggleAutoUpgrade = async () => {
    const next = !autoUpgrade
    setAutoUpgrade(next)
    try { await AsyncStorage.setItem(keyAutoUpgrade, next ? '1' : '0') } catch {}
  }

  const requestIncrease = () => {
    Alert.alert('Request sent', 'We will reach out about increasing your limits.')
  }

  return (
    <SafeAreaView style={{ flex: 1, backgroundColor: theme.color.background }}>
      <ScrollView>
        <View style={{ paddingHorizontal: 24, paddingVertical: 12 }}>
          <Text style={{ color: theme.color.foreground, fontSize: 24, fontWeight: '700', marginBottom: 12 }}>Usage & Limits</Text>

          {usage.map((u) => {
            const p = percentOf(u)
            const warn = p >= 80 && p < 100
            const cap = p >= 100
            return (
              <View key={u.key} style={{ borderWidth: 1, borderColor: theme.color.border, borderRadius: 12, padding: 12, marginBottom: 12 }}>
                {warn && <View style={{ marginBottom: 8 }}><Banner tone="warning" text={`Approaching limit: ${p}% used`} /></View>}
                {cap && <View style={{ marginBottom: 8 }}><Banner tone="danger" text={`Limit reached: ${p}% used`} /></View>}
                <UsageBar meter={u} />
                <Text style={{ color: theme.color.mutedForeground, marginTop: 6 }}>{meterNotes[u.key] || 'Usage meter'}</Text>
                <TouchableOpacity onPress={requestIncrease} accessibilityRole="button" accessibilityLabel="Request limit increase" style={{ alignSelf: 'flex-start', marginTop: 10, paddingHorizontal: 12, paddingVertical: 10, borderRadius: 10, borderWidth: 1, borderColor: theme.color.border }}>
                  <Text style={{ color: theme.color.cardForeground, fontWeight: '700' }}>Request limit increase</Text>
                </TouchableOpacity>
                <View style={{ height: 1, backgroundColor: theme.color.border, marginVertical: 10 }} />
                <Text style={{ color: theme.color.mutedForeground }}>Hard cap behavior: When reaching 100%, new usage may be blocked until the next period, unless Auto-upgrade is enabled.</Text>
              </View>
            )
          })}

          <View style={{ borderWidth: 1, borderColor: theme.color.border, borderRadius: 12, padding: 12 }}>
            <View style={{ flexDirection: 'row', alignItems: 'center', justifyContent: 'space-between' }}>
              <Text style={{ color: theme.color.cardForeground, fontWeight: '700' }}>Auto-upgrade</Text>
              <Switch value={autoUpgrade} onValueChange={toggleAutoUpgrade} accessibilityLabel="Auto-upgrade" />
            </View>
            <Text style={{ color: theme.color.mutedForeground, marginTop: 6 }}>Automatically upgrade to the next plan to avoid hitting hard caps.</Text>
          </View>
        </View>
      </ScrollView>
    </SafeAreaView>
  )
}

export default UsageLimitsScreen


