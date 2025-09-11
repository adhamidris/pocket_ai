import React from 'react'
import { SafeAreaView, View, TouchableOpacity, Text, FlatList, RefreshControl, Alert, Linking } from 'react-native'
// WebView placeholder note: if not installed, consider adding or using Linking
import { useTheme } from '../../providers/ThemeProvider'
import { useSafeAreaInsets } from 'react-native-safe-area-context'
import { PublishLink, ChannelStatus, RecipeItem, WidgetSnippet } from '../../types/channels'
import LinkCard from '../../components/channels/LinkCard'
import QrBlock from '../../components/channels/QrBlock'
import ChannelCard from '../../components/channels/ChannelCard'
import ListSkeleton from '../../components/channels/ListSkeleton'
import OfflineBanner from '../../components/dashboard/OfflineBanner'
import SyncCenterSheet from '../../components/dashboard/SyncCenterSheet'
import { track } from '../../lib/analytics'
import { useNavigation } from '@react-navigation/native'

const now = () => Date.now()
const genLink = (): PublishLink => ({ id: `pl-${now()}`, url: `https://chat.example.com/u/${Math.random().toString(36).slice(2, 10)}`, shortUrl: `https://ex.am/${Math.random().toString(36).slice(2, 6)}`, themeId: 'default', createdAt: now(), verify: { state: 'verified', lastCheckedAt: now() } })

const genStatuses = (): ChannelStatus[] => ([
  { channel: 'whatsapp', connected: Math.random() > 0.5, lastVerifiedAt: now() - 1000 * 60 * 30, notes: 'Business API connected' },
  { channel: 'instagram', connected: Math.random() > 0.5, lastVerifiedAt: now() - 1000 * 60 * 60 * 6 },
  { channel: 'facebook', connected: Math.random() > 0.5, lastVerifiedAt: now() - 1000 * 60 * 60 * 12 },
  { channel: 'web', connected: true, lastVerifiedAt: now() - 1000 * 60 * 5, notes: 'Widget live' },
])

const ChannelsHome: React.FC = () => {
  const { theme } = useTheme()
  const insets = useSafeAreaInsets()
  const navigation = useNavigation<any>()

  const [loading, setLoading] = React.useState(false)
  const [refreshing, setRefreshing] = React.useState(false)
  const [link, setLink] = React.useState<PublishLink>(() => genLink())
  const [useShort, setUseShort] = React.useState<boolean>(true)
  const [showBroadcast, setShowBroadcast] = React.useState<boolean>(false)
  const [statuses, setStatuses] = React.useState<ChannelStatus[]>(() => genStatuses())
  const [testOpen, setTestOpen] = React.useState(false)
  const [offline, setOffline] = React.useState(false)
  const [syncOpen, setSyncOpen] = React.useState(false)
  const [queued, setQueued] = React.useState<{ rotate?: boolean; verify?: boolean; connect?: boolean }>({})

  React.useEffect(() => {
    track('channels.view')
    setLoading(true)
    const t = setTimeout(() => setLoading(false), 300)
    return () => clearTimeout(t)
  }, [])

  const onRefresh = () => {
    setRefreshing(true)
    setTimeout(() => {
      setStatuses(genStatuses())
      setRefreshing(false)
    }, 600)
  }

  const copyLink = () => { console.log('copy', (useShort && link.shortUrl) ? link.shortUrl : link.url); Alert.alert('Copied', 'Link copied to clipboard (stub).'); track('channels.copy_link') }
  const openLink = () => { Linking.openURL((useShort && link.shortUrl) ? link.shortUrl! : link.url) }
  const rotateLink = () => {
    Alert.alert('Rotate link', 'Are you sure you want to rotate the link?', [
      { text: 'Cancel', style: 'cancel' },
      { text: 'Rotate', style: 'destructive', onPress: () => { setQueued((q) => ({ ...q, rotate: true })); setLink((l) => ({ ...genLink(), createdAt: l.createdAt, lastRotatedAt: now() })); setShowBroadcast(true); track('channels.rotate_link'); setTimeout(() => setQueued((q) => ({ ...q, rotate: false })), 1500) } },
    ])
  }

  return (
    <SafeAreaView style={{ flex: 1, backgroundColor: theme.color.background }}>
      <View style={{ paddingHorizontal: 24, paddingTop: insets.top + 12, paddingBottom: 12 }}>
        <View style={{ flexDirection: 'row', alignItems: 'center', justifyContent: 'space-between', marginBottom: 12 }}>
          <Text style={{ color: theme.color.foreground, fontSize: 32, fontWeight: '700' }}>Channels & Publishing</Text>
          <View style={{ flexDirection: 'row', gap: 8 }}>
            <TouchableOpacity onPress={() => navigation.navigate('Settings', { screen: 'ThemePreview', params: { themeId: link.themeId, onApply: (v: any) => setLink((l) => ({ ...l, themeId: l.themeId })) } })} accessibilityLabel="Theming" accessibilityRole="button" style={{ paddingHorizontal: 12, paddingVertical: 8, borderWidth: 1, borderColor: theme.color.border, borderRadius: 12 }}>
              <Text style={{ color: theme.color.cardForeground, fontWeight: '600' }}>Theming</Text>
            </TouchableOpacity>
            <TouchableOpacity onPress={() => navigation.navigate('Channels', { screen: 'WidgetSnippet', params: { linkUrl: link.url } })} accessibilityLabel="Widget Snippet" accessibilityRole="button" style={{ paddingHorizontal: 12, paddingVertical: 8, borderWidth: 1, borderColor: theme.color.border, borderRadius: 12 }}>
              <Text style={{ color: theme.color.cardForeground, fontWeight: '600' }}>Widget Snippet</Text>
            </TouchableOpacity>
            <TouchableOpacity onPress={() => navigation.navigate('Channels', { screen: 'UtmBuilder', params: { linkUrl: link.url } })} accessibilityLabel="UTM Builder" accessibilityRole="button" style={{ paddingHorizontal: 12, paddingVertical: 8, borderWidth: 1, borderColor: theme.color.border, borderRadius: 12 }}>
              <Text style={{ color: theme.color.cardForeground, fontWeight: '600' }}>UTM Builder</Text>
            </TouchableOpacity>
            <TouchableOpacity onPress={() => navigation.navigate('Channels', { screen: 'VerificationLogs', params: { verify: link.verify, onUpdate: (v: any) => setLink((l) => ({ ...l, verify: v })) } })} accessibilityLabel="Verification Logs" accessibilityRole="button" style={{ paddingHorizontal: 12, paddingVertical: 8, borderWidth: 1, borderColor: theme.color.border, borderRadius: 12 }}>
              <Text style={{ color: theme.color.cardForeground, fontWeight: '600' }}>Logs</Text>
            </TouchableOpacity>
          </View>
        </View>

        {/* Link card */}
        <View style={{ marginBottom: 16 }}>
          <LinkCard link={link} onCopy={copyLink} onOpen={openLink} onRotate={rotateLink} themeName={link.themeId} themeColor={theme.color.primary as any} shortPreferred={useShort} onToggleShort={setUseShort} queued={queued.rotate} testID="chn-link" />
        </View>

        {showBroadcast && (
          <View style={{ backgroundColor: theme.color.card, borderWidth: 1, borderColor: theme.color.border, borderRadius: 12, padding: 12, marginBottom: 12 }}>
            <View style={{ flexDirection: 'row', alignItems: 'center', justifyContent: 'space-between' }}>
              <Text style={{ color: theme.color.mutedForeground, flex: 1, marginRight: 8 }}>
                Reminder: Update bios and auto-replies with your new link so customers can still reach you.
              </Text>
              <TouchableOpacity onPress={() => setShowBroadcast(false)} accessibilityLabel="Dismiss" accessibilityRole="button" style={{ paddingHorizontal: 10, paddingVertical: 6, borderWidth: 1, borderColor: theme.color.border, borderRadius: 8 }}>
                <Text style={{ color: theme.color.cardForeground }}>Dismiss</Text>
              </TouchableOpacity>
            </View>
          </View>
        )}

        {/* Status row + offline */}
        {offline && (
          <View style={{ marginBottom: 8 }}>
            <OfflineBanner visible testID="chn-offline" />
          </View>
        )}
        <Text style={{ color: theme.color.mutedForeground, marginBottom: 8 }}>Verify: {link.verify.state.toUpperCase()} • Last check {link.verify.lastCheckedAt ? new Date(link.verify.lastCheckedAt).toLocaleString() : '—'}</Text>

        {/* QR */}
        <View style={{ marginBottom: 16 }}>
          <QrBlock url={link.url} onSave={() => { Alert.alert('Saved', 'QR saved to gallery (stub).'); track('channels.qr_save') }} testID="chn-qr" />
        </View>
      </View>

      {/* Channels */}
      <View style={{ flex: 1, paddingHorizontal: 24 }}>
        <View style={{ flexDirection: 'row', justifyContent: 'space-between', marginBottom: 8 }}>
          <TouchableOpacity onPress={() => setOffline((v) => !v)} accessibilityLabel="Toggle offline" accessibilityRole="button" style={{ paddingHorizontal: 12, paddingVertical: 8, borderWidth: 1, borderColor: theme.color.border, borderRadius: 12 }}>
            <Text style={{ color: theme.color.cardForeground, fontWeight: '600' }}>{offline ? 'Go Online' : 'Go Offline'}</Text>
          </TouchableOpacity>
          <TouchableOpacity onPress={() => setTestOpen(true)} accessibilityLabel="Test in Portal" accessibilityRole="button" style={{ paddingHorizontal: 12, paddingVertical: 8, borderWidth: 1, borderColor: theme.color.border, borderRadius: 12 }}>
            <Text style={{ color: theme.color.cardForeground, fontWeight: '600' }}>Test in Portal</Text>
          </TouchableOpacity>
          <TouchableOpacity onPress={() => setSyncOpen(true)} accessibilityLabel="Sync Center" accessibilityRole="button" style={{ paddingHorizontal: 12, paddingVertical: 8, borderWidth: 1, borderColor: theme.color.border, borderRadius: 12 }}>
            <Text style={{ color: theme.color.mutedForeground, fontWeight: '600' }}>Sync</Text>
          </TouchableOpacity>
        </View>
        {loading ? (
          <ListSkeleton rows={8} testID="chn-skeleton" />
        ) : (
          <FlatList
            testID="chn-list"
            data={statuses}
            keyExtractor={(s) => s.channel}
            renderItem={({ item }) => (
              <View style={{ marginBottom: 12 }}>
                <ChannelCard status={item} onOpenRecipe={() => navigation.navigate('Channels', { screen: 'RecipeCenter', params: { channel: item.channel } })} />
              </View>
            )}
            refreshControl={<RefreshControl refreshing={refreshing} onRefresh={onRefresh} />}
            initialNumToRender={8}
            maxToRenderPerBatch={8}
            windowSize={10}
            removeClippedSubviews
          />
        )}
      </View>
      <SyncCenterSheet visible={syncOpen} onClose={() => setSyncOpen(false)} lastSyncAt={new Date().toLocaleString()} queuedCount={Object.values(queued).filter(Boolean).length} onRetryAll={() => setSyncOpen(false)} />
    </SafeAreaView>
  )
}

export default ChannelsHome


