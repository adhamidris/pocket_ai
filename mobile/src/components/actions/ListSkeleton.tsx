import React from 'react'
import { View } from 'react-native'
import { useTheme } from '../../providers/ThemeProvider'

export const ListSkeleton: React.FC<{ rows?: number }>=({ rows = 6 }) => {
  const { theme } = useTheme()
  return (
    <View style={{ gap: 8 }}>
      {Array.from({ length: rows }).map((_, i) => (
        <View key={i} style={{ height: 56, borderRadius: 12, backgroundColor: theme.color.secondary }} />
      ))}
    </View>
  )
}

export default ListSkeleton


