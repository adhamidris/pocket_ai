import React from 'react'
import { View, Text } from 'react-native'
import { tokens } from '../../ui/tokens'

export interface BarChartMiniProps { rows: { label: string; value: number }[]; maxBars?: number; testID?: string }

const BarChartMini: React.FC<BarChartMiniProps> = ({ rows, maxBars = 5, testID }) => {
  const top = rows.slice(0, maxBars)
  const maxVal = Math.max(1, ...top.map((r) => r.value))
  return (
    <View testID={testID}>
      {top.map((r, idx) => (
        <View key={idx} style={{ marginBottom: 8 }}>
          <View style={{ flexDirection: 'row', alignItems: 'center', justifyContent: 'space-between' }}>
            <Text style={{ color: tokens.colors.cardForeground }}>{r.label}</Text>
            <Text style={{ color: tokens.colors.mutedForeground }}>{r.value}</Text>
          </View>
          <View style={{ height: 8, borderRadius: 6, backgroundColor: tokens.colors.muted }}>
            <View style={{ width: `${(r.value / maxVal) * 100}%`, height: '100%', backgroundColor: tokens.colors.primary, borderRadius: 6 }} />
          </View>
        </View>
      ))}
    </View>
  )
}

export default React.memo(BarChartMini)


