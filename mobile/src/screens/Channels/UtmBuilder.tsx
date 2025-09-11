import React from 'react'
import { SafeAreaView, View, Text, TextInput, TouchableOpacity, Alert, Switch } from 'react-native'
import { useSafeAreaInsets } from 'react-native-safe-area-context'
import { useTheme } from '../../providers/ThemeProvider'
import { useNavigation, useRoute } from '@react-navigation/native'
import QrBlock from '../../components/channels/QrBlock'
import { track } from '../../lib/analytics'

const buildUrl = (base: string, params: Record<string, string>) => {
  const url = new URL(base)
  Object.entries(params).forEach(([k, v]) => { if (v) url.searchParams.set(k, v) })
  return url.toString()
}

const UtmBuilder: React.FC = () => {
  const { theme } = useTheme()
  const insets = useSafeAreaInsets()
  const navigation = useNavigation<any>()
  const route = useRoute<any>()
  const baseUrl: string = route.params?.linkUrl || 'https://chat.example.com/u/demo'

  const [utm_source, setSource] = React.useState('')
  const [utm_medium, setMedium] = React.useState('')
  const [utm_campaign, setCampaign] = React.useState('')
  const [utm_content, setContent] = React.useState('')
  const [d_source, setDSource] = React.useState('')
  const [d_medium, setDMedium] = React.useState('')
  const [d_campaign, setDCampaign] = React.useState('')
  const [d_content, setDContent] = React.useState('')
  const [shorten, setShorten] = React.useState(false)

  React.useEffect(() => {
    const h = setTimeout(() => { setDSource(utm_source); setDMedium(utm_medium); setDCampaign(utm_campaign); setDContent(utm_content) }, 300)
    return () => clearTimeout(h)
  }, [utm_source, utm_medium, utm_campaign, utm_content])

  const preview = React.useMemo(() => buildUrl(baseUrl, { utm_source: d_source, utm_medium: d_medium, utm_campaign: d_campaign, utm_content: d_content }), [baseUrl, d_source, d_medium, d_campaign, d_content])
  const shortUrl = React.useMemo(() => `https://ex.am/${Math.random().toString(36).slice(2, 8)}`, [preview])
  const displayUrl = shorten ? shortUrl : preview

  const copy = () => { console.log('copy utm', displayUrl); Alert.alert('Copied', 'UTM link copied (stub).'); track('channels.utm_copy') }

  return (
    <SafeAreaView style={{ flex: 1, backgroundColor: theme.color.background }}>
      {/* Header */}
      <View style={{ paddingHorizontal: 16, paddingTop: insets.top + 8, paddingBottom: 12, borderBottomWidth: 1, borderBottomColor: theme.color.border }}>
        <View style={{ flexDirection: 'row', alignItems: 'center', justifyContent: 'space-between' }}>
          <TouchableOpacity onPress={() => navigation.goBack()} accessibilityLabel="Back" accessibilityRole="button" style={{ padding: 8 }}>
            <Text style={{ color: theme.color.primary, fontWeight: '600' }}>{'< Back'}</Text>
          </TouchableOpacity>
          <Text style={{ color: theme.color.foreground, fontSize: 18, fontWeight: '700' }}>UTM Builder</Text>
          <View style={{ width: 64 }} />
        </View>
      </View>

      <View style={{ padding: 16, gap: 12 }}>
        {/* Inputs */}
        {([
          ['utm_source', utm_source, setSource],
          ['utm_medium', utm_medium, setMedium],
          ['utm_campaign', utm_campaign, setCampaign],
          ['utm_content', utm_content, setContent],
        ] as const).map(([label, value, setter]) => (
          <View key={label} style={{ flexDirection: 'row', alignItems: 'center', gap: 8 }}>
            <Text style={{ color: theme.color.mutedForeground, width: 110 }}>{label}</Text>
            <TextInput value={value} onChangeText={setter as any} placeholder={`enter ${label}`} placeholderTextColor={theme.color.placeholder} accessibilityLabel={label} style={{ flex: 1, color: theme.color.cardForeground, borderBottomWidth: 1, borderBottomColor: theme.color.border }} />
          </View>
        ))}

        {/* Shorten toggle */}
        <View style={{ flexDirection: 'row', alignItems: 'center', justifyContent: 'space-between' }}>
          <Text style={{ color: theme.color.mutedForeground }}>Shorten (UI-only)</Text>
          <Switch value={shorten} onValueChange={setShorten} />
        </View>

        {/* Preview */}
        <Text style={{ color: theme.color.cardForeground, fontWeight: '700', marginTop: 4 }}>Preview</Text>
        <View style={{ borderWidth: 1, borderColor: theme.color.border, borderRadius: 12, backgroundColor: theme.color.card, padding: 10 }}>
          <Text style={{ color: theme.color.cardForeground }} numberOfLines={2}>{displayUrl}</Text>
        </View>

        <TouchableOpacity onPress={copy} accessibilityLabel="Copy UTM Link" accessibilityRole="button" style={{ alignSelf: 'flex-start', paddingHorizontal: 12, paddingVertical: 8, borderWidth: 1, borderColor: theme.color.border, borderRadius: 12 }}>
          <Text style={{ color: theme.color.primary, fontWeight: '600' }}>Copy</Text>
        </TouchableOpacity>

        {/* QR Preview */}
        <View style={{ marginTop: 8 }}>
          <QrBlock url={displayUrl} onSave={() => Alert.alert('Saved', 'QR saved (stub).')} />
        </View>
      </View>
    </SafeAreaView>
  )
}

export default UtmBuilder


