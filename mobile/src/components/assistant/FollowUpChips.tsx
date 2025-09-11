import React from 'react'
import { View, TouchableOpacity, Text } from 'react-native'
import { useTheme } from '../../providers/ThemeProvider'

export const FollowUpChips: React.FC<{ followUps: string[]; onSelect?: (s: string) => void }> = ({ followUps, onSelect }) => {
  const { theme } = useTheme()
  return (
    <View style={{ flexDirection: 'row', flexWrap: 'wrap', gap: 8 }}>
      {followUps.map((f, i) => (
        <TouchableOpacity key={i} onPress={() => onSelect?.(f)} accessibilityLabel={`follow up ${i}`}>
          <View style={{ paddingHorizontal: 10, paddingVertical: 6, backgroundColor: theme.color.accent, borderRadius: theme.radius.sm, borderWidth: 1, borderColor: theme.color.border }}>
            <Text style={{ color: theme.color.mutedForeground }}>{f}</Text>
          </View>
        </TouchableOpacity>
      ))}
    </View>
  )
}

export default FollowUpChips


