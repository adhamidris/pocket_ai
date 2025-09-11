import React from 'react'
import { View, Text } from 'react-native'
import { Risk } from '../../types/security'

const colorFor = (r?: Risk) => r === 'high' ? '#ef4444' : r === 'medium' ? '#f59e0b' : '#16a34a'

export interface RiskBadgeProps { level?: Risk; testID?: string }

const RiskBadge: React.FC<RiskBadgeProps> = ({ level, testID }) => {
  const c = colorFor(level)
  return (
    <View testID={testID} style={{ flexDirection: 'row', alignItems: 'center', gap: 6 }}>
      <View style={{ width: 8, height: 8, borderRadius: 4, backgroundColor: c }} />
      <Text style={{ color: c, fontWeight: '700' }}>{level || 'low'}</Text>
    </View>
  )
}

export default React.memo(RiskBadge)


