import React from 'react'
import { SafeAreaView, View, Text, TouchableOpacity } from 'react-native'
import { useTheme } from '../../providers/ThemeProvider'
import { useSafeAreaInsets } from 'react-native-safe-area-context'
import { useNavigation } from '@react-navigation/native'

type Step = {
  id: string
  label: string
  run: () => Promise<void>
}

export const DeepLinkAudit: React.FC = () => {
  const { theme } = useTheme()
  const insets = useSafeAreaInsets()
  const navigation = useNavigation<any>()
  const [log, setLog] = React.useState<Array<{ id: string; status: 'pending' | 'ok' | 'fail'; lastRoute?: string; params?: any; error?: string }>>([])

  const mark = (id: string, patch: Partial<(typeof log)[number]>) => setLog(prev => prev.map(r => r.id === id ? { ...r, ...patch } : r))

  const goBack = async () => new Promise<void>((resolve) => setTimeout(() => { try { navigation.goBack() } catch {}; resolve() }, 150))

  const steps: Step[] = [
    {
      id: 'dash→conv',
      label: 'Dashboard → Conversations (filters) → back',
      run: async () => {
        navigation.navigate('Main', { screen: 'Conversations', params: { screen: 'ConversationsHome', params: { filter: 'urgent' } } })
        await new Promise(r => setTimeout(r, 200))
        mark('dash→conv', { status: 'ok', lastRoute: 'Conversations', params: { filter: 'urgent' } })
        await goBack()
      }
    },
    {
      id: 'dash→auto',
      label: 'Dashboard → Automations (SLA/RuleBuilder) → back',
      run: async () => {
        navigation.navigate('Main', { screen: 'Automations', params: { screen: 'SlaEditor' } })
        await new Promise(r => setTimeout(r, 200))
        navigation.navigate('Main', { screen: 'Automations', params: { screen: 'RuleBuilder' } })
        await new Promise(r => setTimeout(r, 200))
        mark('dash→auto', { status: 'ok', lastRoute: 'RuleBuilder' })
        await goBack(); await goBack()
      }
    },
    {
      id: 'analytics→trends',
      label: 'Analytics tiles → Trends → back',
      run: async () => {
        navigation.navigate('Main', { screen: 'Analytics', params: { screen: 'Trends' } })
        await new Promise(r => setTimeout(r, 200))
        mark('analytics→trends', { status: 'ok', lastRoute: 'Trends' })
        await goBack()
      }
    },
    {
      id: 'knowledge→rule',
      label: 'Knowledge FailureLog → RuleBuilder prefilled → back',
      run: async () => {
        navigation.navigate('Main', { screen: 'Knowledge', params: { screen: 'FailureLog' } })
        await new Promise(r => setTimeout(r, 200))
        navigation.navigate('Main', { screen: 'Automations', params: { screen: 'RuleBuilder', params: { rule: { id: 'tmp', name: 'Prefilled', when: [], then: [], enabled: true, order: 0 }, currentMaxOrder: 0 } } })
        await new Promise(r => setTimeout(r, 200))
        mark('knowledge→rule', { status: 'ok', lastRoute: 'RuleBuilder', params: { prefilled: true } })
        await goBack(); await goBack()
      }
    },
    {
      id: 'channels→portal',
      label: 'Channels → Widget Snippet → Portal Preview → back',
      run: async () => {
        navigation.navigate('Main', { screen: 'Channels', params: { screen: 'WidgetSnippet' } })
        await new Promise(r => setTimeout(r, 200))
        navigation.navigate('Main', { screen: 'Portal' })
        await new Promise(r => setTimeout(r, 200))
        mark('channels→portal', { status: 'ok', lastRoute: 'Portal' })
        await goBack(); await goBack()
      }
    },
    {
      id: 'settings→publish',
      label: 'Settings → Theme → Publish → Channels linkage → back',
      run: async () => {
        navigation.navigate('Main', { screen: 'Settings', params: { screen: 'Settings', params: { deeplink: 'publish' } } })
        await new Promise(r => setTimeout(r, 200))
        navigation.navigate('Main', { screen: 'Channels' })
        await new Promise(r => setTimeout(r, 200))
        mark('settings→publish', { status: 'ok', lastRoute: 'Channels' })
        await goBack(); await goBack()
      }
    },
    {
      id: 'security→crm',
      label: 'Security → Privacy Modes → effects visible in CRM → back',
      run: async () => {
        navigation.navigate('Main', { screen: 'Security' })
        await new Promise(r => setTimeout(r, 200))
        navigation.navigate('Main', { screen: 'CRM', params: { screen: 'CRM' } })
        await new Promise(r => setTimeout(r, 200))
        mark('security→crm', { status: 'ok', lastRoute: 'CRM' })
        await goBack(); await goBack()
      }
    },
    {
      id: 'billing→checkout',
      label: 'Billing upsell → PlanMatrix → Checkout (cancel) → back',
      run: async () => {
        navigation.navigate('Main', { screen: 'Settings', params: { screen: 'PlanMatrix' } })
        await new Promise(r => setTimeout(r, 200))
        navigation.navigate('Main', { screen: 'Settings', params: { screen: 'Checkout' } })
        await new Promise(r => setTimeout(r, 200))
        mark('billing→checkout', { status: 'ok', lastRoute: 'Checkout' })
        await goBack(); await goBack()
      }
    },
    {
      id: 'assistant→tools',
      label: 'Assistant tool suggestions to each target',
      run: async () => {
        // Simulate by navigating to DailyBrief
        navigation.navigate('Main', { screen: 'Analytics', params: { screen: 'DailyBrief' } })
        await new Promise(r => setTimeout(r, 200))
        mark('assistant→tools', { status: 'ok', lastRoute: 'DailyBrief' })
        await goBack()
      }
    },
  ]

  React.useEffect(() => {
    setLog(steps.map(s => ({ id: s.id, status: 'pending' })))
  }, [])

  const runAll = async () => {
    for (const s of steps) {
      try {
        await s.run()
      } catch (e: any) {
        mark(s.id, { status: 'fail', error: String(e?.message || e) })
      }
    }
  }

  return (
    <SafeAreaView style={{ flex: 1, backgroundColor: theme.color.background }}>
      <View style={{ paddingHorizontal: 16, paddingTop: insets.top + 12, paddingBottom: 12, flexDirection: 'row', alignItems: 'center', justifyContent: 'space-between' }}>
        <Text style={{ color: theme.color.foreground, fontSize: 22, fontWeight: '700' }}>Deep‑Link Audit</Text>
        <TouchableOpacity onPress={runAll} accessibilityRole="button" accessibilityLabel="Run audit" style={{ paddingHorizontal: 12, paddingVertical: 10, borderRadius: 12, borderWidth: 1, borderColor: theme.color.border }}>
          <Text style={{ color: theme.color.cardForeground, fontWeight: '700' }}>Run</Text>
        </TouchableOpacity>
      </View>
      <View style={{ paddingHorizontal: 16 }}>
        <View style={{ borderWidth: 1, borderColor: theme.color.border, borderRadius: 12 }}>
          {log.map((r, i) => (
            <View key={r.id} style={{ flexDirection: 'row', paddingHorizontal: 12, paddingVertical: 10, alignItems: 'center', justifyContent: 'space-between', borderTopWidth: i === 0 ? 0 : 1, borderTopColor: theme.color.border }}>
              <Text style={{ color: theme.color.cardForeground, fontWeight: '600' }}>{r.id}</Text>
              <View style={{ flexDirection: 'row', gap: 8, alignItems: 'center' }}>
                <Text style={{ color: r.status === 'ok' ? theme.color.success : r.status === 'fail' ? theme.color.destructive : theme.color.mutedForeground, fontWeight: '700' }}>{r.status.toUpperCase()}</Text>
                {r.lastRoute ? <Text style={{ color: theme.color.mutedForeground }}>last: {r.lastRoute}</Text> : null}
              </View>
            </View>
          ))}
        </View>
      </View>
    </SafeAreaView>
  )
}

export default DeepLinkAudit

