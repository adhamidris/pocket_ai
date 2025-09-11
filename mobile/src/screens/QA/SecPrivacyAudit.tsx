import React from 'react'
import { SafeAreaView, View, Text, TouchableOpacity } from 'react-native'
import { useTheme } from '../../providers/ThemeProvider'
import { useSafeAreaInsets } from 'react-native-safe-area-context'
import { useNavigation } from '@react-navigation/native'

export const SecPrivacyAudit: React.FC = () => {
  const { theme } = useTheme()
  const insets = useSafeAreaInsets()
  const navigation = useNavigation<any>()
  const [hidePII, setHidePII] = React.useState<boolean>(false)
  const [anonAnalytics, setAnonAnalytics] = React.useState<boolean>(false)

  const emit = (vals: any) => {
    try { (require('react-native') as any).DeviceEventEmitter.emit('privacy.modes', vals) } catch {}
  }

  const toggleHidePII = () => { const v = !hidePII; setHidePII(v); emit({ hideContactPII: v }) }
  const toggleAnon = () => { const v = !anonAnalytics; setAnonAnalytics(v); emit({ anonymizeAnalytics: v }) }

  return (
    <SafeAreaView style={{ flex: 1, backgroundColor: theme.color.background }}>
      <View style={{ paddingHorizontal: 16, paddingTop: insets.top + 12, paddingBottom: 12 }}>
        <Text style={{ color: theme.color.foreground, fontSize: 22, fontWeight: '700', marginBottom: 8 }}>Security & Privacy Audit</Text>
        <View style={{ flexDirection: 'row', gap: 8, flexWrap: 'wrap' }}>
          <Chip label={hidePII ? 'Hide PII: On' : 'Hide PII: Off'} onPress={toggleHidePII} />
          <Chip label={anonAnalytics ? 'Anon Analytics: On' : 'Anon Analytics: Off'} onPress={toggleAnon} />
          <Chip label="Open CRM" onPress={() => navigation.navigate('Main', { screen: 'CRM' })} />
          <Chip label="Open Portal" onPress={() => navigation.navigate('Main', { screen: 'Portal' })} />
          <Chip label="Open Assistant" onPress={() => (require('react-native') as any).DeviceEventEmitter.emit('assistant.open', { text: 'Show a metric with citation', persona: 'analyst' })} />
          <Chip label="Open Analytics" onPress={() => navigation.navigate('Main', { screen: 'Analytics' })} />
          <Chip label="Consent Center" onPress={() => navigation.navigate('Security', { screen: 'ConsentCenter' })} />
          <Chip label="Retention Editor" onPress={() => navigation.navigate('Security', { screen: 'RetentionEditor' })} />
          <Chip label="Audit Log" onPress={() => navigation.navigate('Automations', { screen: 'ImportExportAudit', params: { tab: 'audit' } })} />
          <Chip label="Residency" onPress={() => navigation.navigate('Security', { screen: 'DataResidency' })} />
        </View>
      </View>
      <View style={{ paddingHorizontal: 16 }}>
        <Card title="Checklist">
          <Item text="CRM contact PII masked when Hide PII is ON" done={hidePII} />
          <Item text="Portal consent banner shows as configured" />
          <Item text="Assistant citations visible; redact if configured" />
          <Item text="Analytics shows anonymized badge when anonymize ON" done={anonAnalytics} />
          <Item text="Retention warnings display for -1 values" />
          <Item text="Consent templates publishing UI reachable" />
          <Item text="Audit log entries append when actions performed" />
          <Item text="Residency badge visible on relevant screens" />
        </Card>
      </View>
    </SafeAreaView>
  )
}

const Chip: React.FC<{ label: string; onPress: () => void }> = ({ label, onPress }) => (
  <TouchableOpacity onPress={onPress} accessibilityRole="button" accessibilityLabel={label} style={{ paddingHorizontal: 10, paddingVertical: 8, borderRadius: 10, borderWidth: 1, borderColor: '#ccc' }}>
    <Text style={{ color: '#333', fontWeight: '700' }}>{label}</Text>
  </TouchableOpacity>
)

const Card: React.FC<{ title: string; children: React.ReactNode }> = ({ title, children }) => {
  const { theme } = useTheme()
  return (
    <View style={{ borderWidth: 1, borderColor: theme.color.border, borderRadius: 12, padding: 12 }}>
      <Text style={{ color: theme.color.mutedForeground, fontWeight: '700', marginBottom: 8 }}>{title}</Text>
      {children}
    </View>
  )
}

const Item: React.FC<{ text: string; done?: boolean }> = ({ text, done }) => {
  const { theme } = useTheme()
  return (
    <View style={{ flexDirection: 'row', alignItems: 'center', justifyContent: 'space-between', paddingVertical: 8 }}>
      <Text style={{ color: theme.color.cardForeground }}>{text}</Text>
      <Text style={{ color: done ? theme.color.success : theme.color.mutedForeground, fontWeight: '700' }}>{done ? 'OK' : 'Check'}</Text>
    </View>
  )
}

export default SecPrivacyAudit


