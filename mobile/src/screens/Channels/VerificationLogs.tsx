import React from 'react'
import { SafeAreaView, View, Text, TouchableOpacity, FlatList } from 'react-native'
import { useSafeAreaInsets } from 'react-native-safe-area-context'
import { useTheme } from '../../providers/ThemeProvider'
import { useNavigation } from '@react-navigation/native'
import { useNavigation, useRoute } from '@react-navigation/native'
import { VerifyState } from '../../types/channels'
import { track } from '../../lib/analytics'
import EntitlementsGate from '../../components/billing/EntitlementsGate'

interface LogItem { ts: number; state: VerifyState; message?: string }

const VerificationLogs: React.FC = () => {
  const { theme } = useTheme()
  const navigation = useNavigation<any>()
  const insets = useSafeAreaInsets()
  const navigation = useNavigation<any>()
  const route = useRoute<any>()

  const initialVerify = (route.params?.verify as { state: VerifyState; lastCheckedAt?: number; message?: string }) || { state: 'unverified' as VerifyState }
  const onUpdate: undefined | ((v: { state: VerifyState; lastCheckedAt?: number; message?: string }) => void) = route.params?.onUpdate

  const [current, setCurrent] = React.useState<{ state: VerifyState; lastCheckedAt?: number; message?: string }>(initialVerify)
  const [logs, setLogs] = React.useState<LogItem[]>([])
  const [running, setRunning] = React.useState(false)

  const runVerification = () => {
    if (running) return
    setRunning(true)
    const startTs = Date.now()
    const verifying: LogItem = { ts: startTs, state: 'verifying', message: 'Verification started' }
    setLogs((l) => [verifying, ...l])
    setCurrent({ state: 'verifying', lastCheckedAt: startTs, message: 'Verifying…' })
    try { onUpdate && onUpdate({ state: 'verifying', lastCheckedAt: startTs, message: 'Verifying…' }) } catch {}

    setTimeout(() => {
      const success = Math.random() > 0.3
      const finalState: VerifyState = success ? 'verified' : 'failed'
      const ts = Date.now()
      const entry: LogItem = { ts, state: finalState, message: success ? 'Domain verified' : 'DNS check failed' }
      setLogs((l) => [entry, ...l])
      const next = { state: finalState, lastCheckedAt: ts, message: entry.message }
      setCurrent(next)
      try { onUpdate && onUpdate(next) } catch {}
      track('channels.verify', { result: finalState })
      setRunning(false)
    }, 1200)
  }

  return (
    <SafeAreaView style={{ flex: 1, backgroundColor: theme.color.background }}>
      {/* Header */}
      <View style={{ paddingHorizontal: 16, paddingTop: insets.top + 8, paddingBottom: 12, borderBottomWidth: 1, borderBottomColor: theme.color.border }}>
        <View style={{ flexDirection: 'row', alignItems: 'center', justifyContent: 'space-between' }}>
          <TouchableOpacity onPress={() => navigation.goBack()} accessibilityLabel="Back" accessibilityRole="button" style={{ padding: 8 }}>
            <Text style={{ color: theme.color.primary, fontWeight: '600' }}>{'< Back'}</Text>
          </TouchableOpacity>
          <Text style={{ color: theme.color.foreground, fontSize: 18, fontWeight: '700' }}>Verification Logs</Text>
          <TouchableOpacity onPress={() => navigation.navigate('Security', { screen: 'AuditLog' })} accessibilityLabel="Open Audit Log" accessibilityRole="button" style={{ padding: 8 }}>
            <Text style={{ color: theme.color.cardForeground, fontWeight: '600' }}>Audit</Text>
          </TouchableOpacity>
        </View>
      </View>

      <View style={{ padding: 16 }}>
        <EntitlementsGate require="channelsVerification">
        <View style={{ flexDirection: 'row', alignItems: 'center', justifyContent: 'space-between', marginBottom: 12 }}>
          <Text style={{ color: theme.color.mutedForeground }}>Current: {current.state.toUpperCase()} {current.lastCheckedAt ? `• ${new Date(current.lastCheckedAt).toLocaleString()}` : ''}</Text>
          <TouchableOpacity onPress={runVerification} accessibilityLabel="Run verification" accessibilityRole="button" style={{ paddingHorizontal: 12, paddingVertical: 8, borderWidth: 1, borderColor: theme.color.border, borderRadius: 12 }}>
            <Text style={{ color: running ? theme.color.mutedForeground : theme.color.primary, fontWeight: '700' }}>{running ? 'Running…' : 'Run verification'}</Text>
          </TouchableOpacity>
        </View>

        <FlatList
          data={logs}
          keyExtractor={(i) => String(i.ts)}
          renderItem={({ item }) => (
            <View style={{ padding: 12, borderWidth: 1, borderColor: theme.color.border, borderRadius: 12, marginBottom: 8 }}>
              <Text style={{ color: theme.color.cardForeground, fontWeight: '600' }}>{item.state.toUpperCase()}</Text>
              <Text style={{ color: theme.color.mutedForeground }}>{new Date(item.ts).toLocaleString()}</Text>
              {item.message && <Text style={{ color: theme.color.mutedForeground, marginTop: 2 }}>{item.message}</Text>}
            </View>
          )}
          ListEmptyComponent={<Text style={{ color: theme.color.mutedForeground }}>No verifications yet.</Text>}
        />
        </EntitlementsGate>
      </View>
    </SafeAreaView>
  )
}

export default VerificationLogs


