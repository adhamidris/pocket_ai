import React from 'react'
import { View, TouchableOpacity, Text } from 'react-native'
import { useTheme } from '../../providers/ThemeProvider'
import { Persona } from '../../types/assistant'

export const PersonaSwitcher: React.FC<{ value: Persona; onChange?: (p: Persona) => void }>
  = ({ value, onChange }) => {
  const { theme } = useTheme()
  const options: Persona[] = ['ops', 'owner', 'agent', 'analyst']
  return (
    <View style={{ flexDirection: 'row', backgroundColor: theme.color.secondary, borderRadius: theme.radius.lg, padding: 4, borderWidth: 1, borderColor: theme.color.border }}>
      {options.map(o => {
        const active = value === o
        return (
          <TouchableOpacity key={o} onPress={() => onChange?.(o)} accessibilityLabel={`persona ${o}`}>
            <View style={{ paddingHorizontal: 10, paddingVertical: 6, borderRadius: theme.radius.md, backgroundColor: active ? theme.color.primary : 'transparent' }}>
              <Text style={{ color: active ? '#fff' : theme.color.mutedForeground, fontWeight: '600' }}>{o}</Text>
            </View>
          </TouchableOpacity>
        )
      })}
    </View>
  )
}

export default PersonaSwitcher


