import React from 'react'
import { View, Text } from 'react-native'
import { useTheme } from '../../providers/ThemeProvider'

export const EmptyState: React.FC<{ message: string; testID?: string }>=({ message, testID }) => {
  const { theme } = useTheme()
  return (
    <View testID={testID} style={{ alignItems: 'center', padding: 16, borderRadius: 16, borderWidth: 1, borderColor: theme.color.border, backgroundColor: theme.color.card }}>
      <Text style={{ color: theme.color.mutedForeground }}>{message}</Text>
    </View>
  )
}

export default EmptyState


