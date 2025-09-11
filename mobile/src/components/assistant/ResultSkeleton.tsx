import React from 'react'
import { View } from 'react-native'
import { useTheme } from '../../providers/ThemeProvider'

export const ResultSkeleton: React.FC = () => {
  const { theme } = useTheme()
  return (
    <View style={{ gap: 12 }}>
      {[...Array(4)].map((_, i) => (
        <View key={i} style={{ height: 16 + (i % 2) * 8, backgroundColor: theme.color.secondary, borderRadius: theme.radius.sm }} />
      ))}
    </View>
  )
}

export default ResultSkeleton


