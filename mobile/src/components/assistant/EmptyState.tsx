import React from 'react'
import { View, Text } from 'react-native'
import { useTheme } from '../../providers/ThemeProvider'

export const EmptyState: React.FC<{ title?: string; subtitle?: string }>
  = ({ title = 'Ask the Assistant', subtitle = 'Try “What happened today?”' }) => {
  const { theme } = useTheme()
  return (
    <View style={{ alignItems: 'center', padding: 24 }}>
      <View style={{ width: 72, height: 72, borderRadius: 36, backgroundColor: theme.color.secondary, marginBottom: 12 }} />
      <Text style={{ color: theme.color.foreground, fontWeight: '700', fontSize: 18 }}>{title}</Text>
      <Text style={{ color: theme.color.mutedForeground, marginTop: 6 }}>{subtitle}</Text>
    </View>
  )
}

export default EmptyState


