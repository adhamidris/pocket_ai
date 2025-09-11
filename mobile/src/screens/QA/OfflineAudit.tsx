import React from 'react'
import { SafeAreaView, View, Text, TouchableOpacity, Switch } from 'react-native'
import { useTheme } from '../../providers/ThemeProvider'
import { useSafeAreaInsets } from 'react-native-safe-area-context'
import { track } from '../../lib/analytics'

export const OfflineAudit: React.FC = () => {
  const { theme } = useTheme()
  const insets = useSafeAreaInsets()
  const [offline, setOffline] = React.useState<boolean>(false)
  const [queuedCounts, setQueuedCounts] = React.useState<Record<string, number>>({})

  const emit = (name: string, payload?: any) => {
    try { (require('react-native') as any).DeviceEventEmitter.emit(name, payload) } catch {}
  }

  const toggleGlobalOffline = (v: boolean) => {
    setOffline(v)
    emit('app.offline', { value: v })
    track('qa.offline.toggle', { value: v })
  }

  const simulateEdits = async () => {
    // Automations: toggle rule and autoresponder to queue
    emit('nav.goto', { screen: 'Automations' })
    setTimeout(() => emit('automations.sim.toggle'), 50)
    // Settings: pretend to save theme publish (queues)
    setTimeout(() => emit('settings.sim.publish'), 100)
    // Billing: open plan matrix and try checkout (cancel)
    setTimeout(() => emit('settings.sim.billing'), 150)
    // Security: flip a privacy mode to broadcast
    setTimeout(() => emit('privacy.modes', { anonymizeAnalytics: true }), 200)
    // Assistant/Portal: attempt send disabled
    setTimeout(() => emit('assistant.open', { text: 'Test offline send', persona: 'agent' }), 250)
    setTimeout(() => emit('portal.sim.trySend'), 300)
  }

  const simulateSync = () => {
    // Clear queued indicators via synthetic events used by screens
    emit('sync.all')
    setQueuedCounts({})
    setOffline(false)
  }

  return (
    <SafeAreaView style={{ flex: 1, backgroundColor: theme.color.background }}>
      <View style={{ paddingHorizontal: 16, paddingTop: insets.top + 12, paddingBottom: 12 }}>
        <Text style={{ color: theme.color.foreground, fontSize: 22, fontWeight: '700', marginBottom: 8 }}>Offline & Queue Audit</Text>
        <View style={{ flexDirection: 'row', alignItems: 'center', gap: 12 }}>
          <Text style={{ color: theme.color.cardForeground }}>Global Offline</Text>
          <Switch value={offline} onValueChange={toggleGlobalOffline} />
          <TouchableOpacity onPress={simulateEdits} accessibilityRole="button" accessibilityLabel="Simulate edits" style={{ paddingHorizontal: 12, paddingVertical: 10, borderRadius: 12, borderWidth: 1, borderColor: theme.color.border }}>
            <Text style={{ color: theme.color.cardForeground, fontWeight: '700' }}>Simulate Edits</Text>
          </TouchableOpacity>
          <TouchableOpacity onPress={simulateSync} accessibilityRole="button" accessibilityLabel="Simulate sync" style={{ paddingHorizontal: 12, paddingVertical: 10, borderRadius: 12, borderWidth: 1, borderColor: theme.color.border }}>
            <Text style={{ color: theme.color.cardForeground, fontWeight: '700' }}>Simulate Sync</Text>
          </TouchableOpacity>
        </View>
      </View>

      <View style={{ paddingHorizontal: 16 }}>
        <View style={{ borderWidth: 1, borderColor: theme.color.border, borderRadius: 12 }}>
          {[
            { key: 'Dashboard', testID: 'offline-banner' },
            { key: 'Automations', testID: 'auto-offline' },
            { key: 'Analytics', testID: 'an-offline' },
            { key: 'CRM', testID: 'crm-offline' },
            { key: 'Knowledge', testID: 'kn-offline' },
            { key: 'Settings', testID: 'set-offline' },
          ].map((row, i) => (
            <View key={row.key} style={{ flexDirection: 'row', alignItems: 'center', justifyContent: 'space-between', paddingHorizontal: 12, paddingVertical: 10, borderTopWidth: i === 0 ? 0 : 1, borderTopColor: theme.color.border }}>
              <Text style={{ color: theme.color.cardForeground, fontWeight: '600' }}>{row.key}</Text>
              <Text style={{ color: theme.color.mutedForeground }}>banner: expected {offline ? 'visible' : 'hidden'}</Text>
            </View>
          ))}
        </View>
      </View>
    </SafeAreaView>
  )
}

export default OfflineAudit


