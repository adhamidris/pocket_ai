import React from 'react'
import { View, Text } from 'react-native'
import { tokens } from '../../ui/tokens'

export interface BreachBadgeProps { level: 'ok'|'warn'|'breach'; label?: string; testID?: string }

const colorFor = (l: 'ok'|'warn'|'breach') => l === 'ok' ? tokens.colors.success : l === 'warn' ? tokens.colors.warning : tokens.colors.error

const BreachBadge: React.FC<BreachBadgeProps> = ({ level, label, testID }) => {
  const color = colorFor(level) as any
  return (
    <View testID={testID} style={{ flexDirection: 'row', alignItems: 'center', gap: 6 }}>
      <View style={{ width: 8, height: 8, borderRadius: 4, backgroundColor: color }} />
      {label ? <Text style={{ color: tokens.colors.mutedForeground }}>{label}</Text> : null}
    </View>
  )
}

export default React.memo(BreachBadge)


