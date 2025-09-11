import React from 'react'
import { SafeAreaView, View, TouchableOpacity, Text, FlatList, Alert, Switch } from 'react-native'
import { useTheme } from '../../providers/ThemeProvider'
import { useSafeAreaInsets } from 'react-native-safe-area-context'
import { useNavigation, useRoute } from '@react-navigation/native'
import { RecipeItem, SocialChannel } from '../../types/channels'
import RecipeCard from '../../components/channels/RecipeCard'
import { track } from '../../lib/analytics'

const buildTemplates = (channel: SocialChannel): RecipeItem[] => {
  switch (channel) {
    case 'whatsapp':
      return [
        { id: 'wa-1', channel, title: 'Auto-reply Template', ctaLabel: 'Copy Text', steps: [
          'Open WhatsApp Business settings',
          'Navigate to Away/Auto-reply',
          'Paste the template and enable'
        ] },
      ]
    case 'instagram':
      return [
        { id: 'ig-1', channel, title: 'Bio + Link-in-bio', ctaLabel: 'Copy Bio Text', steps: [
          'Open Instagram profile → Edit profile',
          'Paste the text in bio and add the link',
          'Save changes'
        ] },
      ]
    case 'facebook':
      return [
        { id: 'fb-1', channel, title: 'Page About + Pinned Post', ctaLabel: 'Copy Post Text', steps: [
          'Update About with the link',
          'Create a post and paste the text',
          'Pin the post to the top'
        ] },
      ]
    case 'web':
    default:
      return [
        { id: 'web-1', channel, title: 'CTA + Link Button', ctaLabel: 'Copy CTA', steps: [
          'Add CTA text to homepage',
          'Add button linking to chat URL',
          'Publish changes'
        ] },
      ]
  }
}

const templateTextFor = (channel: SocialChannel) => {
  switch (channel) {
    case 'whatsapp': return 'Thanks for reaching us! Start chat here: <LINK>'
    case 'instagram': return 'Chat with us anytime: <LINK>'
    case 'facebook': return 'Need help? Start chat: <LINK>'
    case 'web': default: return 'Start a chat with our team: <LINK>'
  }
}

const RecipeCenter: React.FC = () => {
  const { theme } = useTheme()
  const insets = useSafeAreaInsets()
  const navigation = useNavigation<any>()
  const route = useRoute<any>()
  const channel: SocialChannel = route.params?.channel || 'web'
  const onConnectedChange: undefined | ((ch: SocialChannel, connected: boolean) => void) = route.params?.onConnectedChange

  const [connected, setConnected] = React.useState<boolean>(!!route.params?.connected)
  const recipes = React.useMemo(() => buildTemplates(channel), [channel])

  const onCopyTemplate = (id: string) => {
    console.log('copy template', channel, id)
    Alert.alert('Copied', 'Template copied to clipboard (stub).')
    track('channels.recipe_copy', { channel })
  }

  const toggleConnected = (v: boolean) => {
    setConnected(v)
    try { onConnectedChange && onConnectedChange(channel, v) } catch {}
  }

  return (
    <SafeAreaView style={{ flex: 1, backgroundColor: theme.color.background }}>
      <View style={{ paddingHorizontal: 16, paddingTop: insets.top + 8, paddingBottom: 12, borderBottomWidth: 1, borderBottomColor: theme.color.border }}>
        <View style={{ flexDirection: 'row', alignItems: 'center', justifyContent: 'space-between' }}>
          <TouchableOpacity onPress={() => navigation.goBack()} accessibilityLabel="Back" accessibilityRole="button" style={{ padding: 8 }}>
            <Text style={{ color: theme.color.primary, fontWeight: '600' }}>{'< Back'}</Text>
          </TouchableOpacity>
          <Text style={{ color: theme.color.foreground, fontSize: 18, fontWeight: '700' }}>Recipe: {channel.toUpperCase()}</Text>
          <View style={{ width: 64 }} />
        </View>
      </View>

      <View style={{ paddingHorizontal: 16, paddingVertical: 12 }}>
        <View style={{ flexDirection: 'row', alignItems: 'center', justifyContent: 'space-between', marginBottom: 12 }}>
          <Text style={{ color: theme.color.mutedForeground }}>Mark as connected</Text>
          <Switch value={connected} onValueChange={(v) => { toggleConnected(v); track('channels.mark_connected', { channel, value: v }) }} />
        </View>
        <FlatList
          data={recipes}
          keyExtractor={(r) => r.id}
          renderItem={({ item }) => (
            <View style={{ marginBottom: 12 }}>
              <RecipeCard recipe={item} onCopyTemplate={() => onCopyTemplate(item.id)} />
            </View>
          )}
          initialNumToRender={6}
          maxToRenderPerBatch={6}
          windowSize={8}
          removeClippedSubviews
        />
      </View>
    </SafeAreaView>
  )
}

export default RecipeCenter


