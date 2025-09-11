import React from 'react'
import { SafeAreaView, View, Text, TouchableOpacity, ScrollView, Switch, Alert } from 'react-native'
import { useSafeAreaInsets } from 'react-native-safe-area-context'
import { useTheme } from '../../providers/ThemeProvider'
import { NotificationPrefs } from '../../types/settings'
import { track } from '../../lib/analytics'

export interface NotificationsScreenProps {
  initial: NotificationPrefs
  onSave: (prefs: NotificationPrefs) => void
  onClose?: () => void
}

const RowToggle: React.FC<{ label: string; value: boolean; onChange: (v: boolean) => void }>=({ label, value, onChange }) => {
  const { theme } = useTheme()
  return (
    <View style={{ flexDirection: 'row', alignItems: 'center', justifyContent: 'space-between', borderWidth: 1, borderColor: theme.color.border, borderRadius: 12, paddingHorizontal: 12, paddingVertical: 10, marginBottom: 8 }}>
      <Text style={{ color: theme.color.cardForeground }}>{label}</Text>
      <Switch value={value} onValueChange={onChange} accessibilityLabel={label} />
    </View>
  )
}

const NotificationsScreen: React.FC<NotificationsScreenProps> = ({ initial, onSave, onClose }) => {
  const { theme } = useTheme()
  const insets = useSafeAreaInsets()

  const [prefs, setPrefs] = React.useState<NotificationPrefs>(initial)

  const save = () => { onSave(prefs); try { track('settings.notifications.save') } catch {}; Alert.alert('Saved', 'Notification preferences updated.'); onClose && onClose() }
  const testEmail = () => { try { track('settings.notifications.test', { kind: 'email' }) } catch {}; console.log('Send test email'); Alert.alert('Test Email', 'A test email has been sent (stub).') }
  const testPush = () => { try { track('settings.notifications.test', { kind: 'push' }) } catch {}; console.log('Send test push'); Alert.alert('Test Push', 'A test push has been sent (stub).') }

  return (
    <SafeAreaView style={{ flex: 1, backgroundColor: theme.color.background }}>
      <ScrollView contentContainerStyle={{ paddingBottom: 24 }}>
        <View style={{ paddingHorizontal: 24, paddingTop: insets.top + 12, paddingBottom: 12 }}>
          <View style={{ flexDirection: 'row', alignItems: 'center', justifyContent: 'space-between' }}>
            <TouchableOpacity onPress={onClose} accessibilityRole="button" accessibilityLabel="Back" style={{ paddingHorizontal: 12, paddingVertical: 8, borderRadius: 10, borderWidth: 1, borderColor: theme.color.border }}>
              <Text style={{ color: theme.color.primary, fontWeight: '600' }}>{'< Back'}</Text>
            </TouchableOpacity>
            <Text style={{ color: theme.color.foreground, fontSize: 20, fontWeight: '700' }}>Notifications</Text>
            <TouchableOpacity onPress={save} accessibilityRole="button" accessibilityLabel="Save" style={{ paddingHorizontal: 12, paddingVertical: 8, borderRadius: 10, backgroundColor: theme.color.primary }}>
              <Text style={{ color: theme.color.primaryForeground, fontWeight: '700' }}>Save</Text>
            </TouchableOpacity>
          </View>
        </View>

        <View style={{ paddingHorizontal: 24 }}>
          <Text style={{ color: theme.color.cardForeground, fontWeight: '700', marginBottom: 8 }}>Channels</Text>
          <RowToggle label="Email" value={prefs.email} onChange={(v) => setPrefs((p) => ({ ...p, email: v }))} />
          <RowToggle label="Push" value={prefs.push} onChange={(v) => setPrefs((p) => ({ ...p, push: v }))} />

          <Text style={{ color: theme.color.cardForeground, fontWeight: '700', marginVertical: 8 }}>Alerts</Text>
          <RowToggle label="SLA" value={prefs.alerts.sla} onChange={(v) => setPrefs((p) => ({ ...p, alerts: { ...p.alerts, sla: v } }))} />
          <RowToggle label="Backlog" value={prefs.alerts.backlog} onChange={(v) => setPrefs((p) => ({ ...p, alerts: { ...p.alerts, backlog: v } }))} />
          <RowToggle label="Volume Spike" value={prefs.alerts.volumeSpike} onChange={(v) => setPrefs((p) => ({ ...p, alerts: { ...p.alerts, volumeSpike: v } }))} />
          <RowToggle label="Billing" value={prefs.alerts.billing} onChange={(v) => setPrefs((p) => ({ ...p, alerts: { ...p.alerts, billing: v } }))} />

          <View style={{ flexDirection: 'row', gap: 8, marginTop: 12 }}>
            <TouchableOpacity onPress={testEmail} accessibilityRole="button" accessibilityLabel="Send test email" style={{ paddingHorizontal: 12, paddingVertical: 10, borderRadius: 12, borderWidth: 1, borderColor: theme.color.border }}>
              <Text style={{ color: theme.color.cardForeground, fontWeight: '600' }}>Send test email</Text>
            </TouchableOpacity>
            <TouchableOpacity onPress={testPush} accessibilityRole="button" accessibilityLabel="Send test push" style={{ paddingHorizontal: 12, paddingVertical: 10, borderRadius: 12, borderWidth: 1, borderColor: theme.color.border }}>
              <Text style={{ color: theme.color.cardForeground, fontWeight: '600' }}>Send test push</Text>
            </TouchableOpacity>
          </View>
        </View>
      </ScrollView>
    </SafeAreaView>
  )
}

export default NotificationsScreen


