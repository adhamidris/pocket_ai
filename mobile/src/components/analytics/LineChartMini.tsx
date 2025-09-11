import React from 'react'
import { View } from 'react-native'
import { tokens } from '../../ui/tokens'
import { SeriesPoint } from '../../types/analytics'

export interface LineChartMiniProps { series: SeriesPoint[]; testID?: string }

const LineChartMini: React.FC<LineChartMiniProps> = ({ series, testID }) => {
  const max = Math.max(1, ...series.map((p) => p.value))
  const bars = series.slice(-24) // keep small
  return (
    <View testID={testID} style={{ height: 60, flexDirection: 'row', alignItems: 'flex-end', gap: 2 }}>
      {bars.map((p, idx) => (
        <View key={idx} style={{ width: 6, height: Math.max(2, Math.round((p.value / max) * 56)), backgroundColor: tokens.colors.primary, borderRadius: 2 }} />
      ))}
    </View>
  )
}

export default React.memo(LineChartMini)


