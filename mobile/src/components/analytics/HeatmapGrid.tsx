import React from 'react'
import { View, Text } from 'react-native'
import { tokens } from '../../ui/tokens'
import { HeatmapPoint } from '../../types/analytics'

export interface HeatmapGridProps { points: HeatmapPoint[]; xLabels: string[]; yLabels: string[]; testID?: string }

const HeatmapGrid: React.FC<HeatmapGridProps> = ({ points, xLabels, yLabels, testID }) => {
  const max = Math.max(1, ...points.map((p) => p.value))
  const colorFor = (v: number) => {
    const alpha = Math.max(0.15, Math.min(1, v / max))
    return `${tokens.colors.primary}${Math.round(alpha * 255).toString(16).padStart(2, '0')}`
  }
  return (
    <View testID={testID}>
      <View style={{ flexDirection: 'row', marginBottom: 6 }}>
        <View style={{ width: 48 }} />
        {xLabels.map((x, i) => (
          <View key={i} style={{ width: 20, alignItems: 'center' }}>
            <Text style={{ color: tokens.colors.mutedForeground, fontSize: 10 }}>{x}</Text>
          </View>
        ))}
      </View>
      {yLabels.map((y, yi) => (
        <View key={yi} style={{ flexDirection: 'row', alignItems: 'center', marginBottom: 4 }}>
          <View style={{ width: 48 }}>
            <Text style={{ color: tokens.colors.mutedForeground, fontSize: 10 }}>{y}</Text>
          </View>
          {xLabels.map((_, xi) => {
            const p = points.find((pp) => pp.x === xi && pp.y === yi)
            const v = p?.value || 0
            return <View key={xi} style={{ width: 20, height: 16, marginRight: 2, borderRadius: 3, backgroundColor: colorFor(v), borderWidth: 1, borderColor: tokens.colors.border }} />
          })}
        </View>
      ))}
    </View>
  )
}

export default React.memo(HeatmapGrid)


