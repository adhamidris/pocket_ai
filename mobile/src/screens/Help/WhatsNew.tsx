import React from 'react'
import { SafeAreaView, View, Text, TouchableOpacity, ScrollView } from 'react-native'
import { useSafeAreaInsets } from 'react-native-safe-area-context'
import { useTheme } from '../../providers/ThemeProvider'
import { useNavigation } from '@react-navigation/native'
import { releaseNotes } from '../../content/help/releaseNotes'
import { track } from '../../lib/analytics'

const STORAGE_KEY = 'help.seen_versions'

const useSeenVersions = () => {
  const [seen, setSeen] = React.useState<string[]>([])
  React.useEffect(() => {
    (async () => {
      try {
        const raw = await (require('@react-native-async-storage/async-storage') as any).default.getItem(STORAGE_KEY)
        if (raw) setSeen(JSON.parse(raw))
      } catch {}
    })()
  }, [])
  const markSeen = async (version: string) => {
    try {
      const next = Array.from(new Set([...seen, version]))
      setSeen(next)
      await (require('@react-native-async-storage/async-storage') as any).default.setItem(STORAGE_KEY, JSON.stringify(next))
    } catch {}
  }
  return { seen, markSeen }
}

const WhatsNew: React.FC = () => {
  const { theme } = useTheme()
  const insets = useSafeAreaInsets()
  const navigation = useNavigation<any>()
  const { seen, markSeen } = useSeenVersions()

  const latest = releaseNotes[0]
  const unseen = latest && !seen.includes(latest.version)
  React.useEffect(() => { try { track('help.whatsnew.view', { version: latest?.version }) } catch {} }, [latest?.version])

  return (
    <SafeAreaView style={{ flex: 1, backgroundColor: theme.color.background }}>
      <ScrollView>
        <View style={{ paddingHorizontal: 24, paddingTop: insets.top + 12, paddingBottom: 12 }}>
          <View style={{ flexDirection: 'row', alignItems: 'center', justifyContent: 'space-between' }}>
            <TouchableOpacity onPress={() => navigation.goBack()} accessibilityLabel="Back" accessibilityRole="button" style={{ paddingHorizontal: 12, paddingVertical: 8, borderRadius: 10, borderWidth: 1, borderColor: theme.color.border }}>
              <Text style={{ color: theme.color.primary, fontWeight: '600' }}>{'< Back'}</Text>
            </TouchableOpacity>
            <Text style={{ color: theme.color.foreground, fontSize: 20, fontWeight: '700' }}>What’s New</Text>
            <View style={{ width: 64 }} />
          </View>
        </View>

        {unseen && (
          <View style={{ paddingHorizontal: 24, marginBottom: 12 }}>
            <View style={{ backgroundColor: theme.color.warning, borderRadius: 12, padding: 12 }}>
              <Text style={{ color: '#000', fontWeight: '700', marginBottom: 6 }}>New version available: {latest.version}</Text>
              <View style={{ flexDirection: 'row', gap: 8 }}>
                <TouchableOpacity onPress={() => navigation.navigate('HelpCenter', { initialTab: 'whatsnew' })} accessibilityLabel="View details" accessibilityRole="button" style={{ paddingHorizontal: 12, paddingVertical: 8, borderRadius: 10, backgroundColor: '#00000022' }}>
                  <Text style={{ color: '#000', fontWeight: '700' }}>View details</Text>
                </TouchableOpacity>
                <TouchableOpacity onPress={() => markSeen(latest.version)} accessibilityLabel="Dismiss" accessibilityRole="button" style={{ paddingHorizontal: 12, paddingVertical: 8, borderRadius: 10, backgroundColor: '#00000022' }}>
                  <Text style={{ color: '#000', fontWeight: '700' }}>Dismiss</Text>
                </TouchableOpacity>
              </View>
            </View>
          </View>
        )}

        <View style={{ paddingHorizontal: 24, gap: 12 }}>
          {releaseNotes.map((n) => (
            <View key={n.id} style={{ borderWidth: 1, borderColor: theme.color.border, borderRadius: 12, padding: 12 }}>
              <View style={{ flexDirection: 'row', justifyContent: 'space-between', alignItems: 'center' }}>
                <Text style={{ color: theme.color.cardForeground, fontWeight: '700', fontSize: 16 }}>{n.version}</Text>
                {!seen.includes(n.version) && (
                  <View style={{ paddingHorizontal: 8, paddingVertical: 4, borderRadius: 999, backgroundColor: theme.color.warning }}>
                    <Text style={{ color: '#000', fontWeight: '700', fontSize: 12 }}>New</Text>
                  </View>
                )}
              </View>
              <Text style={{ color: theme.color.mutedForeground, marginBottom: 8 }}>{n.dateIso}</Text>
              {n.highlights.map((h, idx) => (
                <View key={idx} style={{ marginBottom: 8 }}>
                  <Text style={{ color: theme.color.cardForeground, fontWeight: '600' }}>{h.title}</Text>
                  <Text style={{ color: theme.color.mutedForeground }}>{h.body}</Text>
                </View>
              ))}
              <TouchableOpacity onPress={() => markSeen(n.version)} accessibilityLabel="Mark as seen" accessibilityRole="button" style={{ alignSelf: 'flex-start', paddingHorizontal: 12, paddingVertical: 8, borderRadius: 10, borderWidth: 1, borderColor: theme.color.border }}>
                <Text style={{ color: theme.color.cardForeground, fontWeight: '600' }}>Mark as seen</Text>
              </TouchableOpacity>
            </View>
          ))}
        </View>
      </ScrollView>
    </SafeAreaView>
  )
}

export default WhatsNew


