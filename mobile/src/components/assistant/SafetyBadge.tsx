import React from 'react'
import { View, Text } from 'react-native'
import { useTheme } from '../../providers/ThemeProvider'

export const SafetyBadge: React.FC<{ piiMasked: boolean }>
  = ({ piiMasked }) => {
  const { theme } = useTheme()
  const bg = piiMasked ? theme.color.warning + '20' : theme.color.success + '20'
  const border = piiMasked ? theme.color.warning + '40' : theme.color.success + '40'
  const text = piiMasked ? theme.color.warning : theme.color.success
  const label = piiMasked ? 'PII masked' : 'Safe'
  return (
    <View style={{ alignSelf: 'flex-start', paddingHorizontal: 10, paddingVertical: 6, backgroundColor: bg, borderWidth: 1, borderColor: border, borderRadius: 999 }}>
      <Text style={{ color: text, fontWeight: '600' }}>{label}</Text>
    </View>
  )
}

export default SafetyBadge


