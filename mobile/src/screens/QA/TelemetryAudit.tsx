import React from 'react'
import { SafeAreaView, View, Text, TouchableOpacity, Alert } from 'react-native'
import { useTheme } from '../../providers/ThemeProvider'
import { useSafeAreaInsets } from 'react-native-safe-area-context'
import * as Analytics from '../../lib/analytics'
import { useNavigation } from '@react-navigation/native'

type LogRow = { id: string; ts: number; event: string; props?: Record<string, any> }

const EXPECTED_MAX: Record<string, number> = {
  'dashboard.view': 1,
  'conversations.view': 1,
  'crm.view': 1,
  'settings.view': 1,
  'analytics.view': 1,
}

const scrubPII = (obj: any): any => {
  if (!obj || typeof obj !== 'object') return obj
  const redacted: any = Array.isArray(obj) ? [] : {}
  for (const k of Object.keys(obj)) {
    const v = (obj as any)[k]
    if (/(email|phone|name|address|token|id)/i.test(k)) redacted[k] = '[REDACTED]'
    else if (typeof v === 'object') redacted[k] = scrubPII(v)
    else redacted[k] = v
  }
  return redacted
}

export const TelemetryAudit: React.FC = () => {
  const { theme } = useTheme()
  const insets = useSafeAreaInsets()
  const navigation = useNavigation<any>()
  const [logs, setLogs] = React.useState<LogRow[]>([])
  const [hidePII, setHidePII] = React.useState<boolean>(false)

  React.useEffect(() => {
    const orig = (Analytics as any).track as (e: string, p?: any) => void
    ;(Analytics as any).track = (event: string, props?: Record<string, any>) => {
      try {
        const row: LogRow = { id: `${Date.now()}-${Math.random().toString(36).slice(2, 6)}`, ts: Date.now(), event, props }
        setLogs(prev => [row, ...prev].slice(0, 200))
      } finally {
        try { orig(event, props) } catch {}
      }
    }
    const sub = (require('react-native') as any).DeviceEventEmitter.addListener('privacy.modes', (vals: any) => setHidePII(!!vals?.anonymizeAnalytics))
    return () => { (Analytics as any).track = orig; sub.remove() }
  }, [])

  const counts = React.useMemo(() => {
    const map: Record<string, number> = {}
    logs.forEach(l => { map[l.event] = (map[l.event] || 0) + 1 })
    return map
  }, [logs])

  const warnings = React.useMemo(() => Object.keys(counts).filter(ev => EXPECTED_MAX[ev] && counts[ev] > EXPECTED_MAX[ev]), [counts])

  const triggerSamples = () => {
    // Navigate to some screens to fire built-in view events
    navigation.navigate('Main', { screen: 'Dashboard' })
    setTimeout(() => navigation.navigate('Main', { screen: 'Conversations', params: { screen: 'ConversationsHome' } }), 100)
    setTimeout(() => navigation.navigate('Main', { screen: 'CRM', params: { screen: 'CRM' } }), 200)
    setTimeout(() => navigation.navigate('Main', { screen: 'Settings', params: { screen: 'SettingsHome' } }), 300)
    setTimeout(() => (Analytics as any).track('qa.telemetry.sample', { ok: true }), 400)
  }

  const exportJson = () => {
    const payload = logs.map(l => ({ ts: l.ts, event: l.event, props: hidePII ? scrubPII(l.props || {}) : (l.props || {}) }))
    try { console.log('TelemetryExport', JSON.stringify(payload, null, 2)) } catch {}
    Alert.alert('Export', 'Exported to console (stub).')
  }

  return (
    <SafeAreaView style={{ flex: 1, backgroundColor: theme.color.background }}>
      <View style={{ paddingHorizontal: 16, paddingTop: insets.top + 12, paddingBottom: 12 }}>
        <Text style={{ color: theme.color.foreground, fontSize: 22, fontWeight: '700', marginBottom: 8 }}>Telemetry Audit</Text>
        <View style={{ flexDirection: 'row', flexWrap: 'wrap', gap: 8 }}>
          <Chip label={hidePII ? 'Hide PII: On' : 'Hide PII: Off'} onPress={() => setHidePII(v => !v)} />
          <Chip label="Trigger Samples" onPress={triggerSamples} />
          <Chip label="Export JSON" onPress={exportJson} />
        </View>
      </View>

      <View style={{ paddingHorizontal: 16, marginBottom: 12 }}>
        <View style={{ borderWidth: 1, borderColor: theme.color.border, borderRadius: 12 }}>
          {Object.keys(counts).length === 0 ? (
            <Row left="No events yet" right="" muted />
          ) : (
            Object.entries(counts).map(([ev, cnt], i) => (
              <Row key={ev} left={ev} right={String(cnt)} warn={warnings.includes(ev)} />
            ))
          )}
        </View>
      </View>

      <View style={{ flex: 1, paddingHorizontal: 16 }}>
        <View style={{ borderWidth: 1, borderColor: theme.color.border, borderRadius: 12 }}>
          {logs.map((l, i) => (
            <View key={l.id} style={{ paddingHorizontal: 12, paddingVertical: 10, borderTopWidth: i === 0 ? 0 : 1, borderTopColor: theme.color.border }}>
              <Text style={{ color: theme.color.cardForeground, fontWeight: '700' }}>{l.event}</Text>
              <Text style={{ color: theme.color.mutedForeground, fontSize: 12 }}>{new Date(l.ts).toLocaleString()}</Text>
              {l.props ? (
                <Text selectable style={{ color: theme.color.cardForeground, marginTop: 4, fontSize: 12 }}>{JSON.stringify(hidePII ? scrubPII(l.props) : l.props)}</Text>
              ) : null}
            </View>
          ))}
        </View>
      </View>
    </SafeAreaView>
  )
}

const Chip: React.FC<{ label: string; onPress: () => void }> = ({ label, onPress }) => (
  <TouchableOpacity onPress={onPress} accessibilityRole="button" accessibilityLabel={label} style={{ paddingHorizontal: 10, paddingVertical: 8, borderRadius: 10, borderWidth: 1, borderColor: '#ccc' }}>
    <Text style={{ color: '#333', fontWeight: '700' }}>{label}</Text>
  </TouchableOpacity>
)

const Row: React.FC<{ left: string; right: string; warn?: boolean; muted?: boolean }> = ({ left, right, warn, muted }) => {
  const { theme } = useTheme()
  return (
    <View style={{ flexDirection: 'row', alignItems: 'center', justifyContent: 'space-between', paddingHorizontal: 12, paddingVertical: 8, borderBottomWidth: 1, borderBottomColor: theme.color.border }}>
      <Text style={{ color: muted ? theme.color.mutedForeground : theme.color.cardForeground }}>{left}</Text>
      <Text style={{ color: warn ? theme.color.warning : theme.color.mutedForeground, fontWeight: '700' }}>{right}</Text>
    </View>
  )
}

export default TelemetryAudit

