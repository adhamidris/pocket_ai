import React from 'react'
import { View, TouchableOpacity, Text } from 'react-native'
import { useTheme } from '../../providers/ThemeProvider'

export interface ActionSuggestionsProps { actions: { id: string; label: string }[]; onSelect: (id: string) => void; testID?: string }

const ActionSuggestions: React.FC<ActionSuggestionsProps> = ({ actions, onSelect, testID }) => {
  const { theme } = useTheme()
  if (!actions?.length) return null
  return (
    <View testID={testID} style={{ flexDirection: 'row', gap: 8, paddingHorizontal: 12, paddingVertical: 8 }}>
      {actions.slice(0, 3).map((a) => (
        <TouchableOpacity key={a.id} onPress={() => onSelect(a.id)} accessibilityRole="button" accessibilityLabel={a.label} style={{ paddingHorizontal: 12, paddingVertical: 10, borderRadius: 10, backgroundColor: theme.color.primary, minHeight: 44 }}>
          <Text style={{ color: theme.color.primaryForeground, fontWeight: '700' }}>{a.label}</Text>
        </TouchableOpacity>
      ))}
    </View>
  )
}

export default React.memo(ActionSuggestions)


