import React from 'react'
import { View, Text } from 'react-native'
import { useTheme } from '../../providers/ThemeProvider'

const EmptyState: React.FC<{ title: string; subtitle?: string; testID?: string }> = ({ title, subtitle, testID }) => {
  const { theme } = useTheme()
  return (
    <View testID={testID} style={{ alignItems: 'center', padding: 16 }}>
      <View style={{ width: 64, height: 64, borderRadius: 32, backgroundColor: theme.color.muted, marginBottom: 8 }} />
      <Text style={{ color: theme.color.cardForeground, fontWeight: '700' }}>{title}</Text>
      {subtitle ? <Text style={{ color: theme.color.mutedForeground, marginTop: 4, textAlign: 'center' }}>{subtitle}</Text> : null}
    </View>
  )
}

export default React.memo(EmptyState)


