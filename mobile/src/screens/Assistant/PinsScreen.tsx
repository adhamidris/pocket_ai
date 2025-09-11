import React from 'react'
import { View, Text, TouchableOpacity, ScrollView, Alert } from 'react-native'
import AsyncStorage from '@react-native-async-storage/async-storage'
import { useTheme } from '../../providers/ThemeProvider'
import { MemoryPin } from '../../types/assistant'

const KEY = 'assistant.pins.v1'

export const PinsScreen: React.FC<{ onOpen: (pin: MemoryPin) => void }>=({ onOpen }) => {
  const { theme } = useTheme()
  const [pins, setPins] = React.useState<MemoryPin[]>([])

  React.useEffect(() => { (async () => { try { const raw = await AsyncStorage.getItem(KEY); if (raw) setPins(JSON.parse(raw)) } catch {} })() }, [])

  const remove = async (id: string) => {
    const next = pins.filter(p => p.id !== id)
    setPins(next)
    try { await AsyncStorage.setItem(KEY, JSON.stringify(next)) } catch {}
  }

  if (pins.length === 0) {
    return (
      <View style={{ padding: 16 }}>
        <Text style={{ color: theme.color.mutedForeground }}>No pins yet. Long‑press an answer to pin it.</Text>
      </View>
    )
  }

  return (
    <ScrollView>
      <View style={{ padding: 16, gap: 12 }}>
        {pins.map(pin => (
          <TouchableOpacity key={pin.id} onPress={() => onOpen(pin)} onLongPress={() => Alert.alert('Remove pin', 'Delete this pin?', [ { text: 'Cancel' }, { text: 'Delete', style: 'destructive', onPress: () => remove(pin.id) } ])} accessibilityLabel={`pin ${pin.title}`} accessibilityRole="button" style={{ borderWidth: 1, borderColor: theme.color.border, borderRadius: 12, padding: 12, backgroundColor: theme.color.card }}>
            <Text style={{ color: theme.color.cardForeground, fontWeight: '700' }}>{pin.title}</Text>
            <Text style={{ color: theme.color.mutedForeground, marginTop: 4 }} numberOfLines={2}>{pin.content}</Text>
          </TouchableOpacity>
        ))}
      </View>
    </ScrollView>
  )
}

export default PinsScreen


