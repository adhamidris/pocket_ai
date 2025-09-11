import React from 'react'
import { View, Text } from 'react-native'
import { useTheme } from '../../providers/ThemeProvider'

const OfflineBadge: React.FC<{ testID?: string }> = ({ testID }) => {
  const { theme } = useTheme()
  return (
    <View testID={testID} style={{ alignSelf: 'flex-start', paddingHorizontal: 8, paddingVertical: 4, borderRadius: 8, borderWidth: 1, borderColor: theme.color.warning, backgroundColor: '#2a2412' }}>
      <Text style={{ color: theme.color.warning, fontWeight: '700' }}>Offline</Text>
    </View>
  )
}

export default React.memo(OfflineBadge)


