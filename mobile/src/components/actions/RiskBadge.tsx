import React from 'react'
import { View, Text } from 'react-native'
import { useTheme } from '../../providers/ThemeProvider'

export const RiskBadge: React.FC<{ risk: 'low'|'medium'|'high' }>=({ risk }) => {
  const { theme } = useTheme()
  const color = risk === 'high' ? theme.color.error : risk === 'medium' ? theme.color.warning : theme.color.success
  return (
    <View style={{ paddingHorizontal: 10, paddingVertical: 6, borderRadius: 999, borderWidth: 1, borderColor: color + '66', backgroundColor: color + '22' }} accessibilityLabel={`Risk ${risk}`} accessibilityRole="text">
      <Text style={{ color, fontWeight: '700', textTransform: 'uppercase' }}>{risk}</Text>
    </View>
  )
}

export default RiskBadge


