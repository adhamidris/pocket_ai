import React from 'react'
import { View, Text } from 'react-native'
import { useTheme } from '../../providers/ThemeProvider'

export const EmptyState: React.FC<{ title: string; subtitle?: string }> = ({ title, subtitle }) => {
  const { theme } = useTheme()
  return (
    <View style={{ alignItems: 'center', padding: 24 }}>
      <Text style={{ color: theme.color.cardForeground, fontWeight: '700', fontSize: 18, marginBottom: 6 }}>{title}</Text>
      {!!subtitle && (
        <Text style={{ color: theme.color.mutedForeground, textAlign: 'center' }}>{subtitle}</Text>
      )}
    </View>
  )
}


