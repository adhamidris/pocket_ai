import React from 'react'
import { SafeAreaView, View, Text, TouchableOpacity, FlatList, Switch } from 'react-native'
import { useSafeAreaInsets } from 'react-native-safe-area-context'
import { useTheme } from '../../providers/ThemeProvider'
import { useNavigation, useRoute } from '@react-navigation/native'
import { KnowledgeSource } from '../../types/knowledge'

const ScopeChip: React.FC<{ text: string }> = ({ text }) => (
  <View style={{ paddingHorizontal: 8, paddingVertical: 4, borderRadius: 10, borderWidth: 1, borderColor: '#E5E7EB' }}>
    <Text style={{ color: '#6B7280', fontSize: 11 }}>{text}</Text>
  </View>
)

const SourcePriority: React.FC = () => {
  const { theme } = useTheme()
  const insets = useSafeAreaInsets()
  const navigation = useNavigation<any>()
  const route = useRoute<any>()
  const initial: KnowledgeSource[] = (route.params?.sources as KnowledgeSource[]) || []
  const onApply: undefined | ((next: KnowledgeSource[]) => void) = route.params?.onApply

  const [items, setItems] = React.useState<KnowledgeSource[]>(() => initial.slice())
  const [offline, setOffline] = React.useState(false)
  const [queued, setQueued] = React.useState<Record<string, boolean>>({})

  const move = (idx: number, dir: -1 | 1) => {
    const j = idx + dir
    if (j < 0 || j >= items.length) return
    const next = items.slice()
    const [it] = next.splice(idx, 1)
    next.splice(j, 0, it)
    setItems(next)
  }

  const toggleEnabled = (id: string, v: boolean) => {
    setItems((arr) => arr.map((s) => (s.id === id ? { ...s, enabled: v } : s)))
    if (!offline) return
    setQueued((q) => ({ ...q, [id]: true }))
    setTimeout(() => setQueued((q) => ({ ...q, [id]: false })), 1500)
  }

  const applyAndClose = () => {
    onApply && onApply(items)
    navigation.goBack()
  }

  return (
    <SafeAreaView style={{ flex: 1, backgroundColor: theme.color.background }}>
      {/* Header */}
      <View style={{ paddingHorizontal: 16, paddingTop: insets.top + 8, paddingBottom: 12, borderBottomWidth: 1, borderBottomColor: theme.color.border }}>
        <View style={{ flexDirection: 'row', alignItems: 'center', justifyContent: 'space-between' }}>
          <TouchableOpacity onPress={() => navigation.goBack()} accessibilityLabel="Back" accessibilityRole="button" hitSlop={{ top: 8, bottom: 8, left: 8, right: 8 }} style={{ padding: 8 }}>
            <Text style={{ color: theme.color.primary, fontWeight: '600' }}>{'< Back'}</Text>
          </TouchableOpacity>
          <Text style={{ color: theme.color.foreground, fontSize: 18, fontWeight: '700' }}>Source Priority</Text>
          <View style={{ flexDirection: 'row', gap: 8 }}>
            <TouchableOpacity onPress={() => setOffline((v) => !v)} accessibilityLabel="Toggle Offline" accessibilityRole="button" style={{ paddingHorizontal: 10, paddingVertical: 6, borderWidth: 1, borderColor: theme.color.border, borderRadius: 8 }}>
              <Text style={{ color: theme.color.mutedForeground }}>{offline ? 'Online' : 'Offline'}</Text>
            </TouchableOpacity>
          </View>
        </View>
      </View>

      {/* Info note */}
      <View style={{ padding: 16, borderBottomWidth: 1, borderBottomColor: theme.color.border }}>
        <Text style={{ color: theme.color.mutedForeground }}>
          Higher sources override when conflicts occur. Reorder to change precedence.
        </Text>
      </View>

      {/* List */}
      <FlatList
        style={{ padding: 16 }}
        data={items}
        keyExtractor={(s) => s.id}
        renderItem={({ item, index }) => (
          <View style={{ padding: 12, borderWidth: 1, borderColor: theme.color.border, borderRadius: 12, marginBottom: 10 }}>
            <View style={{ flexDirection: 'row', alignItems: 'center', justifyContent: 'space-between' }}>
              <View style={{ flex: 1, marginRight: 12 }}>
                <Text style={{ color: theme.color.cardForeground, fontWeight: '600' }} numberOfLines={1}>{item.title}</Text>
                <View style={{ flexDirection: 'row', alignItems: 'center', gap: 8, marginTop: 6 }}>
                  <ScopeChip text={item.scope} />
                  <Text style={{ color: theme.color.mutedForeground, fontSize: 12 }}>{item.kind.toUpperCase()}</Text>
                </View>
              </View>
              <View style={{ flexDirection: 'row', alignItems: 'center', gap: 8 }}>
                <TouchableOpacity onPress={() => move(index, -1)} accessibilityLabel="Move up" accessibilityRole="button" style={{ paddingHorizontal: 10, paddingVertical: 6, borderWidth: 1, borderColor: theme.color.border, borderRadius: 8 }}>
                  <Text style={{ color: theme.color.mutedForeground }}>↑</Text>
                </TouchableOpacity>
                <TouchableOpacity onPress={() => move(index, +1)} accessibilityLabel="Move down" accessibilityRole="button" style={{ paddingHorizontal: 10, paddingVertical: 6, borderWidth: 1, borderColor: theme.color.border, borderRadius: 8 }}>
                  <Text style={{ color: theme.color.mutedForeground }}>↓</Text>
                </TouchableOpacity>
                <Switch value={!!item.enabled} onValueChange={(v) => toggleEnabled(item.id, v)} accessibilityLabel={`Enabled ${item.title}`} />
                {queued[item.id] ? <Text style={{ color: theme.color.mutedForeground, fontSize: 12 }}>⏱</Text> : null}
              </View>
            </View>
          </View>
        )}
      />

      {/* Footer */}
      <View style={{ padding: 16 }}>
        <TouchableOpacity onPress={applyAndClose} accessibilityLabel="Apply" accessibilityRole="button" style={{ alignSelf: 'flex-end', paddingHorizontal: 12, paddingVertical: 10, borderWidth: 1, borderColor: theme.color.border, borderRadius: 12 }}>
          <Text style={{ color: theme.color.primary, fontWeight: '700' }}>Apply</Text>
        </TouchableOpacity>
      </View>
    </SafeAreaView>
  )
}

export default SourcePriority


