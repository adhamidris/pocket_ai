import React from 'react'
import { SafeAreaView, View, Text, TextInput, TouchableOpacity, Alert, Switch, ScrollView } from 'react-native'
import { useSafeAreaInsets } from 'react-native-safe-area-context'
import { useTheme } from '../../providers/ThemeProvider'
import { useNavigation, useRoute } from '@react-navigation/native'
import { SourceScope, UrlSource } from '../../types/knowledge'
import ScopePicker from '../../components/knowledge/ScopePicker'
import { track } from '../../lib/analytics'

const isValidUrl = (s: string) => {
  try {
    const u = new URL(s)
    return u.protocol === 'http:' || u.protocol === 'https:'
  } catch { return false }
}

const estSize = (depth: number, allow: string[], block: string[]) => Math.max(20, 50 + depth * 80 + allow.join(',').length * 0.5 - block.join(',').length * 0.3)

const AddUrlSource: React.FC = () => {
  const { theme } = useTheme()
  const insets = useSafeAreaInsets()
  const navigation = useNavigation<any>()
  const route = useRoute<any>()

  const onSaveCb: undefined | ((s: UrlSource) => void) = route.params?.onSave

  const [title, setTitle] = React.useState('')
  const [url, setUrl] = React.useState('https://')
  const [depth, setDepth] = React.useState<number>(1)
  const [respectRobots, setRespect] = React.useState<boolean>(true)
  const [allow, setAllow] = React.useState('')
  const [block, setBlock] = React.useState('')
  const [scope, setScope] = React.useState<SourceScope>('global')
  const [error, setError] = React.useState<string | null>(null)

  const allowArr = React.useMemo(() => allow.split(',').map(s => s.trim()).filter(Boolean), [allow])
  const blockArr = React.useMemo(() => block.split(',').map(s => s.trim()).filter(Boolean), [block])
  const sizeKB = Math.round(estSize(depth, allowArr, blockArr))

  const save = () => {
    const valid = isValidUrl(url)
    if (!valid) { setError('Enter a valid http(s) URL'); return }
    const id = `url-${Date.now()}`
    const src: UrlSource = {
      id,
      kind: 'url',
      title: title.trim() || url,
      enabled: true,
      scope,
      status: 'idle',
      url,
      crawlDepth: Math.max(0, Math.min(2, depth)),
      allowPaths: allowArr.length ? allowArr : undefined,
      blockPaths: blockArr.length ? blockArr : undefined,
      respectRobots: respectRobots,
      estSizeKB: sizeKB,
    }
    try { onSaveCb && onSaveCb(src) } catch {}
    track('knowledge.source_add', { kind: 'url' })
    Alert.alert('Saved', 'URL source added (UI-only).')
    navigation.goBack()
  }

  return (
    <SafeAreaView style={{ flex: 1, backgroundColor: theme.color.background }}>
      {/* Header */}
      <View style={{ paddingHorizontal: 16, paddingTop: insets.top + 8, paddingBottom: 12, borderBottomWidth: 1, borderBottomColor: theme.color.border }}>
        <View style={{ flexDirection: 'row', alignItems: 'center', justifyContent: 'space-between' }}>
          <TouchableOpacity onPress={() => navigation.goBack()} accessibilityLabel="Back" accessibilityRole="button" style={{ padding: 8 }}>
            <Text style={{ color: theme.color.primary, fontWeight: '600' }}>{'< Back'}</Text>
          </TouchableOpacity>
          <Text style={{ color: theme.color.foreground, fontSize: 18, fontWeight: '700' }}>Add URL Source</Text>
          <View style={{ width: 64 }} />
        </View>
      </View>

      <ScrollView style={{ padding: 16 }} contentContainerStyle={{ paddingBottom: 24 }}>
        {/* Title */}
        <View style={{ marginBottom: 12 }}>
          <Text style={{ color: theme.color.mutedForeground, marginBottom: 6 }}>Title</Text>
          <TextInput value={title} onChangeText={setTitle} placeholder="e.g., Help Center" placeholderTextColor={theme.color.placeholder} accessibilityLabel="Title" style={{ borderWidth: 1, borderColor: theme.color.border, borderRadius: 12, paddingHorizontal: 12, paddingVertical: 10, color: theme.color.cardForeground }} />
        </View>
        {/* URL */}
        <View style={{ marginBottom: 12 }}>
          <Text style={{ color: theme.color.mutedForeground, marginBottom: 6 }}>URL</Text>
          <TextInput value={url} onChangeText={(t) => { setUrl(t); if (error) setError(null) }} placeholder="https://example.com/help" placeholderTextColor={theme.color.placeholder} accessibilityLabel="URL" autoCapitalize="none" autoCorrect={false} keyboardType="url" style={{ borderWidth: 1, borderColor: error ? theme.color.error : theme.color.border, borderRadius: 12, paddingHorizontal: 12, paddingVertical: 10, color: theme.color.cardForeground }} />
          {error && <Text style={{ color: theme.color.error, marginTop: 6 }}>{error}</Text>}
        </View>
        {/* Crawl depth */}
        <View style={{ marginBottom: 12 }}>
          <Text style={{ color: theme.color.mutedForeground, marginBottom: 6 }}>Crawl depth (0–2)</Text>
          <View style={{ flexDirection: 'row', gap: 8 }}>
            {[0,1,2].map((d) => (
              <TouchableOpacity key={d} onPress={() => setDepth(d)} accessibilityLabel={`Depth ${d}`} accessibilityRole="button" style={{ paddingHorizontal: 12, paddingVertical: 8, borderRadius: 12, borderWidth: 1, borderColor: depth === d ? theme.color.primary : theme.color.border }}>
                <Text style={{ color: depth === d ? theme.color.primary : theme.color.mutedForeground, fontWeight: '600' }}>{d}</Text>
              </TouchableOpacity>
            ))}
          </View>
        </View>
        {/* Respect robots */}
        <View style={{ flexDirection: 'row', alignItems: 'center', justifyContent: 'space-between', marginBottom: 12 }}>
          <Text style={{ color: theme.color.mutedForeground }}>Respect robots.txt</Text>
          <Switch value={respectRobots} onValueChange={setRespect} accessibilityLabel="Respect robots" />
        </View>
        {/* Allow/Block */}
        <View style={{ marginBottom: 12 }}>
          <Text style={{ color: theme.color.mutedForeground, marginBottom: 6 }}>Allow paths (comma-separated)</Text>
          <TextInput value={allow} onChangeText={setAllow} placeholder="/help,/docs" placeholderTextColor={theme.color.placeholder} accessibilityLabel="Allow paths" autoCapitalize="none" autoCorrect={false} style={{ borderWidth: 1, borderColor: theme.color.border, borderRadius: 12, paddingHorizontal: 12, paddingVertical: 10, color: theme.color.cardForeground }} />
        </View>
        <View style={{ marginBottom: 12 }}>
          <Text style={{ color: theme.color.mutedForeground, marginBottom: 6 }}>Block paths (comma-separated)</Text>
          <TextInput value={block} onChangeText={setBlock} placeholder="/blog" placeholderTextColor={theme.color.placeholder} accessibilityLabel="Block paths" autoCapitalize="none" autoCorrect={false} style={{ borderWidth: 1, borderColor: theme.color.border, borderRadius: 12, paddingHorizontal: 12, paddingVertical: 10, color: theme.color.cardForeground }} />
        </View>
        {/* Scope */}
        <View style={{ marginBottom: 16 }}>
          <Text style={{ color: theme.color.mutedForeground, marginBottom: 6 }}>Scope</Text>
          <ScopePicker value={scope} onChange={setScope} />
        </View>
        {/* Est size */}
        <Text style={{ color: theme.color.mutedForeground, marginBottom: 12 }}>Estimated size: ~{sizeKB} KB</Text>

        {/* Save */}
        <TouchableOpacity onPress={save} accessibilityLabel="Save URL Source" accessibilityRole="button" style={{ alignSelf: 'flex-start', paddingHorizontal: 12, paddingVertical: 10, borderWidth: 1, borderColor: theme.color.border, borderRadius: 12 }}>
          <Text style={{ color: theme.color.primary, fontWeight: '700' }}>Save</Text>
        </TouchableOpacity>
      </ScrollView>
    </SafeAreaView>
  )
}

export default AddUrlSource


