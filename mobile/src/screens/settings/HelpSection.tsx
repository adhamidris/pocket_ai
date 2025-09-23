import React, { useMemo, useState } from 'react'
import { View, Text, TouchableOpacity, ScrollView, Linking, Platform } from 'react-native'
import { useTheme } from '../../providers/ThemeProvider'
import { Card } from '../../components/ui/Card'
import { Input } from '../../components/ui/Input'
import { Modal } from '../../components/ui/Modal'
import { Button } from '../../components/ui/Button'
import { helpArticles, HelpCategory } from '../../content/help'
import { Search, Info, Mail } from 'lucide-react-native'

export const HelpSection: React.FC = () => {
  const { theme } = useTheme()
  const [query, setQuery] = useState('')
  const [category, setCategory] = useState<'All' | HelpCategory>('All')
  const [activeId, setActiveId] = useState<string | null>(null)

  const categories = useMemo(() => ['All', ...Array.from(new Set(helpArticles.map(a => a.category)))] as ('All' | HelpCategory)[], [])

  const results = useMemo(() => {
    const q = query.trim().toLowerCase()
    return helpArticles.filter(a => {
      if (category !== 'All' && a.category !== category) return false
      if (!q) return true
      const hay = (a.title + ' ' + a.keywords.join(' ')).toLowerCase()
      return hay.includes(q)
    })
  }, [query, category])

  const active = activeId ? helpArticles.find(a => a.id === activeId) || null : null

  const openEmail = () => {
    const subject = encodeURIComponent('Help request')
    const appJson = (() => { try { return require('../../../app.json') } catch { return null } })()
    const version = appJson?.expo?.version || '1.0.0'
    const body = encodeURIComponent(`Please describe your issue here.\n\n---\nDiagnostics:\nPlatform: ${Platform.OS}\nApp Version: ${version}`)
    const url = `mailto:support@pocket.ai?subject=${subject}&body=${body}`
    Linking.openURL(url).catch(() => {})
  }

  return (
    <View>
      {/* Search */}
      <Input
        placeholder="Search help…"
        value={query}
        onChangeText={setQuery}
        borderless
        
        surface="secondary"
        icon={<Search size={16} color={theme.color.mutedForeground as any} />}
        containerStyle={{ marginBottom: 12 }}
      />

      {/* Category chips */}
      <ScrollView horizontal showsHorizontalScrollIndicator={false} style={{ marginBottom: 8 }} contentContainerStyle={{ gap: 8 }}>
        {categories.map((c) => {
          const selected = category === c
          return (
            <TouchableOpacity
              key={c}
              onPress={() => setCategory(c)}
              style={{
                paddingHorizontal: 12,
                paddingVertical: 6,
                borderRadius: theme.radius.md,
                backgroundColor: selected ? (theme.color.primary as any) : (theme.dark ? theme.color.secondary : theme.color.accent)
              }}
            >
              <Text style={{ color: selected ? ('#fff' as any) : (theme.color.mutedForeground as any), fontWeight: '700', fontSize: 12 }}>{c}</Text>
            </TouchableOpacity>
          )
        })}
      </ScrollView>

      {/* Results */}
      <View style={{ gap: 10 }}>
        {results.map((a) => (
          <Card key={a.id} variant="flat" style={{ backgroundColor: theme.dark ? (theme.color.secondary as any) : (theme.color.accent as any), paddingHorizontal: 14, paddingVertical: 12 }}>
            <TouchableOpacity onPress={() => setActiveId(a.id)} style={{ flexDirection: 'row', alignItems: 'center', gap: 10 }}>
              <Info size={16} color={theme.color.primary as any} />
              <View style={{ flex: 1 }}>
                <Text style={{ color: theme.color.cardForeground, fontSize: 14, fontWeight: '700' }}>{a.title}</Text>
                <Text numberOfLines={2} style={{ color: theme.color.mutedForeground, fontSize: 12, marginTop: 2 }}>{a.body[0]}</Text>
              </View>
            </TouchableOpacity>
          </Card>
        ))}
        {results.length === 0 && (
          <Card variant="flat" style={{ backgroundColor: theme.dark ? (theme.color.secondary as any) : (theme.color.accent as any) }}>
            <Text style={{ color: theme.color.mutedForeground }}>No results. Try a different search or category.</Text>
          </Card>
        )}
      </View>

      {/* Contact */}
      <Card variant="flat" style={{ marginTop: 12, backgroundColor: theme.dark ? (theme.color.secondary as any) : (theme.color.accent as any), paddingHorizontal: 14, paddingVertical: 12 }}>
        <Text style={{ color: theme.color.cardForeground, fontSize: 16, fontWeight: '600', marginBottom: 8 }}>Need more help?</Text>
        <Button title="Email Support" variant="default" size="md" fullWidth onPress={openEmail} iconLeft={<Mail size={16} color={'#fff'} />} />
      </Card>

      {/* Article Modal */}
      <Modal visible={!!active} onClose={() => setActiveId(null)} title={active?.title || ''} size="md" autoHeight>
        <View style={{ gap: 10 }}>
          {(active?.body || []).map((p, i) => (
            <Text key={i} style={{ color: theme.color.mutedForeground, fontSize: 14, lineHeight: 20 }}>{p}</Text>
          ))}
        </View>
      </Modal>
    </View>
  )
}
