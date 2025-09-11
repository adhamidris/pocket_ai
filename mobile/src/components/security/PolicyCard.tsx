import React from 'react'
import { View, Text } from 'react-native'
import { useTheme } from '../../providers/ThemeProvider'

export interface PolicyCardProps { title: string; subtitle?: string; children?: React.ReactNode; testID?: string }

const PolicyCard: React.FC<PolicyCardProps> = ({ title, subtitle, children, testID }) => {
  const { theme } = useTheme()
  return (
    <View testID={testID} accessibilityLabel={`${title} section`} style={{ borderWidth: 1, borderColor: theme.color.border, borderRadius: 12, padding: 12 }}>
      <Text style={{ color: theme.color.cardForeground, fontWeight: '700', marginBottom: 4 }}>{title}</Text>
      {subtitle ? <Text style={{ color: theme.color.mutedForeground, marginBottom: 8 }}>{subtitle}</Text> : null}
      {children}
    </View>
  )
}

export default React.memo(PolicyCard)


