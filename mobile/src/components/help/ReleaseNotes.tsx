import React, { useState } from 'react'
import { View, Text, TouchableOpacity, FlatList } from 'react-native'
import { useTheme } from '../../providers/ThemeProvider'
import { ReleaseNote } from '../../types/help'

export const ReleaseNotes: React.FC<{ notes: ReleaseNote[]; unseenVersionId?: string; onSeen?: (id: string) => void }> = ({ notes, unseenVersionId, onSeen }) => {
  const { theme } = useTheme()
  const [selected, setSelected] = useState<ReleaseNote | null>(notes[0] ?? null)

  return (
    <View style={{ flexDirection: 'row', gap: 12 }}>
      <View style={{ width: 160 }}>
        {unseenVersionId && (
          <View style={{ backgroundColor: theme.color.warning, borderRadius: 12, padding: 8, marginBottom: 8 }}>
            <Text style={{ color: '#000', fontWeight: '700' }}>What's new</Text>
          </View>
        )}
        <FlatList
          data={notes}
          keyExtractor={n => n.id}
          renderItem={({ item }) => (
            <TouchableOpacity onPress={() => { setSelected(item); onSeen?.(item.id) }} style={{ padding: 10, borderRadius: 12, backgroundColor: selected?.id === item.id ? theme.color.secondary : 'transparent' }} accessibilityLabel={`Open release ${item.version}`}>
              <Text style={{ color: theme.color.cardForeground, fontWeight: '600' }}>{item.version}</Text>
              <Text style={{ color: theme.color.mutedForeground, fontSize: 12 }}>{item.dateIso}</Text>
            </TouchableOpacity>
          )}
          ItemSeparatorComponent={() => <View style={{ height: 8 }} />}
        />
      </View>
      <View style={{ flex: 1, padding: 8 }}>
        {selected ? (
          <View>
            <Text style={{ color: theme.color.cardForeground, fontWeight: '700', fontSize: 18, marginBottom: 8 }}>{selected.version}</Text>
            {selected.highlights.map((h, idx) => (
              <View key={idx} style={{ marginBottom: 10 }}>
                <Text style={{ color: theme.color.cardForeground, fontWeight: '600' }}>{h.title}</Text>
                <Text style={{ color: theme.color.mutedForeground }}>{h.body}</Text>
              </View>
            ))}
          </View>
        ) : (
          <Text style={{ color: theme.color.mutedForeground }}>Select a version</Text>
        )}
      </View>
    </View>
  )
}


