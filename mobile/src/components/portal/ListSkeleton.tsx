import React from 'react'
import { View } from 'react-native'
import { useTheme } from '../../providers/ThemeProvider'

const ListSkeleton: React.FC<{ rows?: number; testID?: string }> = ({ rows = 6, testID }) => {
  const { theme } = useTheme()
  return (
    <View testID={testID}>
      {Array.from({ length: rows }).map((_, i) => (
        <View key={i} style={{ height: 18, backgroundColor: theme.color.muted, borderRadius: 6, marginVertical: 6 }} />
      ))}
    </View>
  )
}

export default React.memo(ListSkeleton)


