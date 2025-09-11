import React from 'react'
import { SafeAreaView, View, Text, TouchableOpacity, ScrollView } from 'react-native'
import { useSafeAreaInsets } from 'react-native-safe-area-context'
import { useTheme } from '../../providers/ThemeProvider'
import { PublishState } from '../../types/settings'
import { track } from '../../lib/analytics'
import { useNavigation } from '@react-navigation/native'

export interface PublishThemeScreenProps {
  initial: PublishState
  onChange: (state: PublishState) => void
}

const channels: Array<{ key: 'whatsapp'|'instagram'|'facebook'|'web'; label: string }> = [
  { key: 'whatsapp', label: 'WhatsApp' },
  { key: 'instagram', label: 'Instagram' },
  { key: 'facebook', label: 'Facebook' },
  { key: 'web', label: 'Web' },
]

const PublishThemeScreen: React.FC<PublishThemeScreenProps> = ({ initial, onChange }) => {
  const { theme } = useTheme()
  const insets = useSafeAreaInsets()
  const navigation = useNavigation<any>()

  const [state, setState] = React.useState<PublishState>(initial)

  const toggle = (key: PublishState['linkedChannels'][number]) => {
    setState((s) => ({
      ...s,
      linkedChannels: s.linkedChannels.includes(key)
        ? s.linkedChannels.filter((c) => c !== key)
        : [...s.linkedChannels, key],
    }))
  }

  const publish = () => {
    const next = { ...state, lastPublishedAt: Date.now() }
    setState(next)
    onChange(next)
    try { track('settings.theme.publish') } catch {}
    // Prompt to update widget
    // eslint-disable-next-line no-alert
    setTimeout(() => {
      // basic confirm via navigation to WidgetSnippet
      navigation.navigate('Channels', { screen: 'WidgetSnippet' })
    }, 300)
  }

  return (
    <SafeAreaView style={{ flex: 1, backgroundColor: theme.color.background }}>
      <ScrollView contentContainerStyle={{ paddingBottom: 24 }}>
        <View style={{ paddingHorizontal: 24, paddingTop: insets.top + 12, paddingBottom: 12 }}>
          <View style={{ flexDirection: 'row', alignItems: 'center', justifyContent: 'space-between' }}>
            <TouchableOpacity onPress={() => navigation.goBack?.()} accessibilityRole="button" accessibilityLabel="Back" style={{ paddingHorizontal: 12, paddingVertical: 8, borderRadius: 10, borderWidth: 1, borderColor: theme.color.border }}>
              <Text style={{ color: theme.color.primary, fontWeight: '600' }}>{'< Back'}</Text>
            </TouchableOpacity>
            <Text style={{ color: theme.color.foreground, fontSize: 20, fontWeight: '700' }}>Publish Theme</Text>
            <View style={{ width: 64 }} />
          </View>
        </View>

        <View style={{ paddingHorizontal: 24, gap: 12 }}>
          <View style={{ borderWidth: 1, borderColor: theme.color.border, borderRadius: 12, padding: 12 }}>
            <Text style={{ color: theme.color.mutedForeground }}>Theme ID</Text>
            <Text style={{ color: theme.color.cardForeground, fontWeight: '700', marginTop: 4 }}>{state.themeId}</Text>
            {state.lastPublishedAt ? (
              <Text style={{ color: theme.color.mutedForeground, marginTop: 4 }}>Last published: {new Date(state.lastPublishedAt).toLocaleString()}</Text>
            ) : (
              <Text style={{ color: theme.color.mutedForeground, marginTop: 4 }}>Not published yet</Text>
            )}
            <TouchableOpacity onPress={publish} accessibilityRole="button" accessibilityLabel="Publish" style={{ alignSelf: 'flex-start', marginTop: 10, paddingHorizontal: 12, paddingVertical: 10, borderRadius: 12, backgroundColor: theme.color.primary }}>
              <Text style={{ color: theme.color.primaryForeground, fontWeight: '700' }}>Publish</Text>
            </TouchableOpacity>
          </View>

          <View style={{ borderWidth: 1, borderColor: theme.color.border, borderRadius: 12, padding: 12 }}>
            <Text style={{ color: theme.color.cardForeground, fontWeight: '700', marginBottom: 8 }}>Linked channels</Text>
            {channels.map((c) => (
              <TouchableOpacity key={c.key} onPress={() => toggle(c.key)} accessibilityRole="button" accessibilityLabel={`Link ${c.label}`} style={{ paddingVertical: 12, borderBottomWidth: 1, borderBottomColor: theme.color.border }}>
                <View style={{ flexDirection: 'row', alignItems: 'center', justifyContent: 'space-between' }}>
                  <Text style={{ color: theme.color.cardForeground }}>{c.label}</Text>
                  <View style={{ width: 18, height: 18, borderRadius: 9, borderWidth: 1, borderColor: theme.color.border, backgroundColor: state.linkedChannels.includes(c.key) ? theme.color.primary : 'transparent' }} />
                </View>
              </TouchableOpacity>
            ))}
          </View>

          <View style={{ borderWidth: 1, borderColor: theme.color.border, borderRadius: 12, padding: 12 }}>
            <Text style={{ color: theme.color.mutedForeground }}>Rotation of link may require updating bios.</Text>
            <TouchableOpacity onPress={() => navigation.navigate('Channels', { screen: 'ChannelsHome' })} accessibilityRole="button" accessibilityLabel="Open Channels" style={{ alignSelf: 'flex-start', marginTop: 8, paddingHorizontal: 12, paddingVertical: 10, borderRadius: 12, borderWidth: 1, borderColor: theme.color.border }}>
              <Text style={{ color: theme.color.cardForeground, fontWeight: '600' }}>Open Channels</Text>
            </TouchableOpacity>
          </View>
        </View>
      </ScrollView>
    </SafeAreaView>
  )
}

export default PublishThemeScreen


