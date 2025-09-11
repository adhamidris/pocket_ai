import React from 'react'
import { View, TouchableOpacity, Text } from 'react-native'
import { useTheme } from '../../providers/ThemeProvider'

export interface ActionBarProps { actions: { id: string; label: string }[]; onAction: (id: string) => void; testID?: string }

const ActionBar: React.FC<ActionBarProps> = ({ actions, onAction, testID }) => {
  const { theme } = useTheme()
  return (
    <View testID={testID} style={{ flexDirection: 'row', flexWrap: 'wrap', gap: 8 }}>
      {actions.map((a) => (
        <TouchableOpacity key={a.id} onPress={() => onAction(a.id)} accessibilityRole="button" accessibilityLabel={`Action ${a.label}`} style={{ paddingHorizontal: 12, paddingVertical: 8, borderRadius: 10, borderWidth: 1, borderColor: theme.color.border, backgroundColor: theme.color.card, minHeight: 44 }}>
          <Text style={{ color: theme.color.cardForeground, fontWeight: '600' }}>{a.label}</Text>
        </TouchableOpacity>
      ))}
    </View>
  )
}

export default React.memo(ActionBar)


