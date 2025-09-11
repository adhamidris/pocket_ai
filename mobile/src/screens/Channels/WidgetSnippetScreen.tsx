import React from 'react'
import { SafeAreaView, View, TouchableOpacity, Text, Alert, ScrollView } from 'react-native'
import { useSafeAreaInsets } from 'react-native-safe-area-context'
import { useTheme } from '../../providers/ThemeProvider'
import { useNavigation, useRoute } from '@react-navigation/native'
import WidgetSnippetBlock from '../../components/channels/WidgetSnippetBlock'
import { track } from '../../lib/analytics'

type TabKey = 'html' | 'react' | 'next' | 'shopify'

const buildCode = (tab: TabKey, url: string) => {
  switch (tab) {
    case 'react':
      return `// React example\nimport React from 'react'\nexport default function ChatButton(){\n  return (<a href="${url}" target="_blank" rel="noopener noreferrer">Chat with us</a>)\n}`
    case 'next':
      return `// Next.js example (app router)\nexport default function Page(){\n  return (<a href="${url}" className="btn">Chat</a>)\n}\n// Consider defer/hydration to avoid blocking rendering.`
    case 'shopify':
      return `<!-- Shopify Liquid -->\n<a href="${url}" class="btn">Chat with us</a>\n{%- comment -%} Add to theme.liquid or a section and publish {%- endcomment -%}`
    case 'html':
    default:
      return `<!-- HTML snippet -->\n<a href="${url}" target="_blank" rel="noopener">Chat with us</a>\n<!-- Tip: defer any heavy scripts until after hydration. -->`
  }
}

const WidgetSnippetScreen: React.FC = () => {
  const { theme } = useTheme()
  const insets = useSafeAreaInsets()
  const navigation = useNavigation<any>()
  const route = useRoute<any>()
  const linkUrl: string = route.params?.linkUrl || 'https://chat.example.com'

  const [tab, setTab] = React.useState<TabKey>('html')

  const snippet = { framework: tab, code: buildCode(tab, linkUrl) } as any

  const copy = () => { console.log('copy snippet', tab); Alert.alert('Copied', 'Snippet copied (stub).'); track('channels.widget_copy', { framework: tab }) }

  return (
    <SafeAreaView style={{ flex: 1, backgroundColor: theme.color.background }}>
      <View style={{ paddingHorizontal: 16, paddingTop: insets.top + 8, paddingBottom: 12, borderBottomWidth: 1, borderBottomColor: theme.color.border }}>
        <View style={{ flexDirection: 'row', alignItems: 'center', justifyContent: 'space-between' }}>
          <TouchableOpacity onPress={() => navigation.goBack()} accessibilityLabel="Back" accessibilityRole="button" style={{ padding: 8 }}>
            <Text style={{ color: theme.color.primary, fontWeight: '600' }}>{'< Back'}</Text>
          </TouchableOpacity>
          <Text style={{ color: theme.color.foreground, fontSize: 18, fontWeight: '700' }}>Widget Snippet</Text>
          <View style={{ width: 64 }} />
        </View>
      </View>

      <View style={{ paddingHorizontal: 16, paddingVertical: 12 }}>
        {/* Tabs */}
        <View style={{ flexDirection: 'row', gap: 8, marginBottom: 12 }}>
          {(['html', 'react', 'next', 'shopify'] as TabKey[]).map((t) => (
            <TouchableOpacity key={t} onPress={() => setTab(t)} accessibilityLabel={`Tab ${t}`} accessibilityRole="button" style={{ paddingHorizontal: 12, paddingVertical: 8, borderRadius: 12, borderWidth: 1, borderColor: tab === t ? theme.color.primary : theme.color.border }}>
              <Text style={{ color: tab === t ? theme.color.primary : theme.color.mutedForeground, fontWeight: '600' }}>{t.toUpperCase()}</Text>
            </TouchableOpacity>
          ))}
        </View>

        {/* Snippet */}
        <WidgetSnippetBlock snippet={snippet} onCopy={copy} testID="chn-widget-copy" />

        {/* Info note */}
        <ScrollView style={{ marginTop: 12 }}>
          <Text style={{ color: theme.color.mutedForeground }}>
            Tips: In SPA frameworks, ensure hydration timing doesn’t block initial render. Defer non-critical scripts and load the chat widget lazily after the page becomes interactive.
          </Text>
        </ScrollView>
      </View>
    </SafeAreaView>
  )
}

export default WidgetSnippetScreen


