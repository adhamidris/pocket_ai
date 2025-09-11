import React from 'react'
import { SafeAreaView, View, Text, TouchableOpacity, ScrollView } from 'react-native'
import { useTheme } from '../../providers/ThemeProvider'
import { useSafeAreaInsets } from 'react-native-safe-area-context'
import { useNavigation } from '@react-navigation/native'

type Item = { id: string; label: string; link?: { screen: string } }

export const RC_Preflight: React.FC = () => {
  const { theme } = useTheme()
  const insets = useSafeAreaInsets()
  const navigation = useNavigation<any>()
  const [done, setDone] = React.useState<Record<string, boolean>>({})

  const buildId = String(Date.now())

  const items: Item[] = [
    { id: 'build', label: `Build ID stamped (${buildId})` },
    { id: 'testids', label: 'testIDs on critical controls' },
    { id: 'smoke', label: 'Crash‑free smoke on 3 device classes' },
    { id: 'a11y', label: 'Accessibility pass', link: { screen: 'AccessibilityAudit' } },
    { id: 'i18n', label: 'i18n/RTL pass', link: { screen: 'I18nAudit' } },
    { id: 'theme', label: 'Theming parity', link: { screen: 'ThemeAudit' } },
    { id: 'offline', label: 'Offline & queues', link: { screen: 'OfflineAudit' } },
    { id: 'perf', label: 'Performance budgets', link: { screen: 'PerfAudit' } },
    { id: 'telemetry', label: 'Telemetry validated', link: { screen: 'TelemetryAudit' } },
    { id: 'entitlements', label: 'Entitlements & gating', link: { screen: 'EntitlementAudit' } },
    { id: 'secpriv', label: 'Security/Privacy UX', link: { screen: 'SecPrivacyAudit' } },
    { id: 'labels', label: 'Dev flags off; demo data labeled' },
    { id: 'about', label: 'About → licenses present', link: { screen: 'Settings' } },
    { id: 'whatsnew', label: 'Help → What’s New updated', link: { screen: 'WhatsNew' } },
    { id: 'rollback', label: 'Rollback path documented (flags/kill switch placeholder)' },
    { id: 'gaps_doc', label: 'KNOWN_GAPS_RC.md reviewed', link: { screen: 'RC_Preflight' } },
  ]

  const toggle = (id: string) => setDone((d) => ({ ...d, [id]: !d[id] }))

  const Chip: React.FC<{ label: string; onPress: () => void }> = ({ label, onPress }) => (
    <TouchableOpacity onPress={onPress} accessibilityRole="button" accessibilityLabel={label} style={{ paddingHorizontal: 10, paddingVertical: 8, borderRadius: 10, borderWidth: 1, borderColor: theme.color.border }}>
      <Text style={{ color: theme.color.cardForeground, fontWeight: '700' }}>{label}</Text>
    </TouchableOpacity>
  )

  return (
    <SafeAreaView style={{ flex: 1, backgroundColor: theme.color.background }}>
      <View style={{ paddingHorizontal: 16, paddingTop: insets.top + 12, paddingBottom: 12 }}>
        <Text style={{ color: theme.color.foreground, fontSize: 22, fontWeight: '700' }}>RC Preflight</Text>
        <View style={{ flexDirection: 'row', flexWrap: 'wrap', gap: 8, marginTop: 8 }}>
          <Chip label="ComponentGallery" onPress={() => navigation.navigate('ComponentGallery')} />
          <Chip label="DeepLinkAudit" onPress={() => navigation.navigate('DeepLinkAudit')} />
          <Chip label="StateCoverage" onPress={() => navigation.navigate('StateCoverageAudit')} />
          <Chip label="ScriptedWalks" onPress={() => navigation.navigate('ScriptedWalks')} />
        </View>
      </View>
      <ScrollView style={{ flex: 1 }} contentContainerStyle={{ paddingHorizontal: 16, paddingBottom: 24 }}>
        <View style={{ borderWidth: 1, borderColor: theme.color.border, borderRadius: 12 }}>
          {items.map((it, i) => (
            <View key={it.id} style={{ flexDirection: 'row', alignItems: 'center', justifyContent: 'space-between', paddingHorizontal: 12, paddingVertical: 10, borderTopWidth: i === 0 ? 0 : 1, borderTopColor: theme.color.border }}>
              <TouchableOpacity onPress={() => toggle(it.id)} accessibilityRole="button" accessibilityLabel={`Toggle ${it.label}`} style={{ paddingHorizontal: 10, paddingVertical: 6, borderRadius: 10, borderWidth: 1, borderColor: done[it.id] ? theme.color.primary : theme.color.border, backgroundColor: done[it.id] ? theme.color.primary + '22' : 'transparent' }}>
                <Text style={{ color: theme.color.cardForeground }}>{done[it.id] ? '✓' : '○'}</Text>
              </TouchableOpacity>
              <Text style={{ color: theme.color.cardForeground, flex: 1, marginHorizontal: 12 }}>{it.label}</Text>
              {it.link ? (
                <TouchableOpacity onPress={() => navigation.navigate(it.link!.screen as any)} accessibilityRole="button" accessibilityLabel={`Open ${it.link!.screen}`} style={{ paddingHorizontal: 10, paddingVertical: 6, borderRadius: 8, borderWidth: 1, borderColor: theme.color.border }}>
                  <Text style={{ color: theme.color.mutedForeground }}>Open</Text>
                </TouchableOpacity>
              ) : <View />}
            </View>
          ))}
        </View>
      </ScrollView>
    </SafeAreaView>
  )
}

export default RC_Preflight


