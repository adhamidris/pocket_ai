import React, { useMemo, useState } from 'react'
import { View, TextInput, FlatList, Text, TouchableOpacity } from 'react-native'
import { useTheme } from '../../providers/ThemeProvider'

export type CommandItem = { id: string; title: string; subtitle?: string; onPress?: () => void }

export const CommandPalette: React.FC<{ visible: boolean; items: CommandItem[]; onClose?: () => void }> = ({ visible, items, onClose }) => {
  const { theme } = useTheme()
  const [q, setQ] = useState('')
  const data = useMemo(() => items.filter(it => it.title.toLowerCase().includes(q.toLowerCase())), [items, q])
  if (!visible) return null
  return (
    <View style={{ position: 'absolute', left: 0, right: 0, top: 0, bottom: 0, padding: 16, backgroundColor: 'rgba(0,0,0,0.5)', justifyContent: 'center' }}>
      <View style={{ backgroundColor: theme.color.card, borderRadius: theme.radius.xl, padding: 12, borderWidth: 1, borderColor: theme.color.border }}>
        <TextInput
          placeholder="Search commands"
          placeholderTextColor={theme.color.placeholder}
          value={q}
          onChangeText={setQ}
          style={{ color: theme.color.cardForeground, padding: 12, borderRadius: 12, borderWidth: 1, borderColor: theme.color.border, marginBottom: 8 }}
          accessibilityLabel="Search commands"
          autoFocus
        />
        <FlatList
          keyboardShouldPersistTaps="handled"
          data={data}
          keyExtractor={it => it.id}
          renderItem={({ item }) => (
            <TouchableOpacity
              onPress={() => { item.onPress?.(); onClose?.() }}
              accessibilityLabel={item.title}
              style={{ padding: 12, borderRadius: 12, minHeight: 44 }}
            >
              <Text style={{ color: theme.color.cardForeground, fontWeight: '600' }}>{item.title}</Text>
              {item.subtitle && <Text style={{ color: theme.color.mutedForeground, fontSize: 12 }}>{item.subtitle}</Text>}
            </TouchableOpacity>
          )}
          ItemSeparatorComponent={() => <View style={{ height: 1, backgroundColor: theme.color.border }} />}
          style={{ maxHeight: 360 }}
        />
      </View>
    </View>
  )
}


