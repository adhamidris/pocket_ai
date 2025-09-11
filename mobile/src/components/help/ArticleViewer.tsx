import React from 'react'
import { View, Text, ScrollView, TouchableOpacity, DeviceEventEmitter } from 'react-native'
import { useTheme } from '../../providers/ThemeProvider'
import { Article } from '../../types/help'
import * as Localization from 'expo-localization'
import { helpStrings } from '../../content/help/strings'
import { track } from '../../lib/analytics'

// Simplified markdown renderer: split paragraphs and bold markers **text**
const renderMarkdown = (md: string, color: string) => {
  return md.split('\n\n').map((para, idx) => (
    <Text key={idx} style={{ color, marginBottom: 12 }}>
      {para.split(/(\*\*[^*]+\*\*)/g).map((chunk, i) => {
        if (chunk.startsWith('**') && chunk.endsWith('**')) {
          return <Text key={i} style={{ fontWeight: '700' }}>{chunk.slice(2, -2)}</Text>
        }
        return <Text key={i}>{chunk}</Text>
      })}
    </Text>
  ))
}

export const ArticleViewer: React.FC<{ article: Article; related?: Article[]; onSelectRelated?: (a: Article) => void }> = ({ article, related = [], onSelectRelated }) => {
  const { theme } = useTheme()
  const lang = Localization.getLocales()[0]?.languageCode === 'ar' ? 'ar' : 'en'
  React.useEffect(() => { try { track('help.article.view', { id: article.id }) } catch {} }, [article?.id])
  return (
    <ScrollView contentContainerStyle={{ padding: 16 }}>
      <Text style={{ color: theme.color.cardForeground, fontWeight: '700', fontSize: 18, marginBottom: 12 }}>{article.title}</Text>
      <View style={{ marginBottom: 12 }}>
        <TouchableOpacity onPress={() => DeviceEventEmitter.emit('assistant.open', { text: `Explain and help me apply: ${article.title}` , persona: 'agent' })} accessibilityLabel={helpStrings[lang].askAboutThis} style={{ alignSelf: 'flex-start', paddingHorizontal: 12, paddingVertical: 8, borderRadius: 10, borderWidth: 1, borderColor: theme.color.border }}>
          <Text style={{ color: theme.color.cardForeground, fontWeight: '600' }}>{helpStrings[lang].askAboutThis}</Text>
        </TouchableOpacity>
      </View>
      <View>
        {renderMarkdown(article.bodyMd, theme.color.body)}
      </View>
      {!!related.length && (
        <View style={{ marginTop: 16 }}>
          <Text style={{ color: theme.color.mutedForeground, fontWeight: '600', marginBottom: 8 }}>Related</Text>
          {related.map(r => (
            <Text key={r.id} onPress={() => onSelectRelated?.(r)} style={{ color: theme.color.primary, marginBottom: 6 }} accessibilityLabel={`Open ${r.title}`}>
              {r.title}
            </Text>
          ))}
        </View>
      )}
    </ScrollView>
  )
}


