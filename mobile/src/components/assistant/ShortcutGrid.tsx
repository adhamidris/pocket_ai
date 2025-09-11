import React from 'react'
import { View, TouchableOpacity, Text } from 'react-native'
import { Shortcut } from '../../types/assistant'
import { useTheme } from '../../providers/ThemeProvider'

export const ShortcutGrid: React.FC<{ shortcuts: Shortcut[]; onPress?: (s: Shortcut) => void }>
  = ({ shortcuts, onPress }) => {
  const { theme } = useTheme()
  const col = 3
  return (
    <View style={{ flexDirection: 'row', flexWrap: 'wrap' }}>
      {shortcuts.map((s, i) => (
        <View key={s.id} style={{ width: `${100/col}%`, padding: 8 }}>
          <TouchableOpacity onPress={() => onPress?.(s)} accessibilityLabel={`shortcut ${s.label}`}>
            <View style={{ backgroundColor: theme.color.secondary, borderRadius: theme.radius.lg, padding: 12, alignItems: 'center', borderWidth: 1, borderColor: theme.color.border }}>
              <View style={{ width: 36, height: 36, borderRadius: 18, backgroundColor: theme.color.accent, marginBottom: 8 }} />
              <Text style={{ color: theme.color.foreground, fontWeight: '600', textAlign: 'center' }}>{s.label}</Text>
            </View>
          </TouchableOpacity>
        </View>
      ))}
    </View>
  )
}

export default ShortcutGrid


