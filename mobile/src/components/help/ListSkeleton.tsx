import React from 'react'
import { View } from 'react-native'
import { useTheme } from '../../providers/ThemeProvider'

export const ListSkeleton: React.FC<{ rows?: number }> = ({ rows = 6 }) => {
  const { theme } = useTheme()
  return (
    <View style={{ padding: 12, gap: 12 }}>
      {Array.from({ length: rows }).map((_, i) => (
        <View key={i} style={{ height: 20, borderRadius: 8, backgroundColor: theme.color.secondary }} />
      ))}
    </View>
  )
}


