import React from 'react'
import { View, Text } from 'react-native'
import { useTheme } from '../../providers/ThemeProvider'

const RateLimitBadge: React.FC<{ testID?: string }> = ({ testID }) => {
  const { theme } = useTheme()
  return (
    <View testID={testID} style={{ alignSelf: 'flex-start', paddingHorizontal: 8, paddingVertical: 4, borderRadius: 8, borderWidth: 1, borderColor: theme.color.error, backgroundColor: '#291314' }}>
      <Text style={{ color: theme.color.error, fontWeight: '700' }}>Rate limited</Text>
    </View>
  )
}

export default React.memo(RateLimitBadge)


