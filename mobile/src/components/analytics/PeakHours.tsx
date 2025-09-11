import React from 'react'
import { View, Text, TouchableOpacity } from 'react-native'
import { useNavigation } from '@react-navigation/native'
import { tokens } from '../../ui/tokens'
import HeatmapGrid from './HeatmapGrid'
import { HeatmapPoint } from '../../types/analytics'

export interface PeakHoursProps { testID?: string }

const xLabels = Array.from({ length: 24 }).map((_, i) => String(i))
const yLabels = ['Mon','Tue','Wed','Thu','Fri','Sat','Sun']

const genPoints = (): HeatmapPoint[] => {
  const pts: HeatmapPoint[] = []
  for (let y = 0; y < yLabels.length; y++) {
    for (let x = 0; x < xLabels.length; x++) {
      const weekdayFactor = y >= 5 ? 0.8 : 1
      const peak = (x >= 18 && x <= 21) ? 1.6 : (x >= 10 && x <= 13 ? 1.3 : 0.8)
      const base = Math.max(2, Math.round(25 * weekdayFactor * peak + (Math.random() * 8 - 4)))
      pts.push({ x, y, value: base })
    }
  }
  return pts
}

const PeakHours: React.FC<PeakHoursProps> = ({ testID }) => {
  const navigation = useNavigation<any>()
  const [points] = React.useState<HeatmapPoint[]>(() => genPoints())

  return (
    <View testID={testID} style={{ borderWidth: 1, borderColor: tokens.colors.border, borderRadius: 12, overflow: 'hidden' }}>
      <View style={{ padding: 12, backgroundColor: tokens.colors.card, borderBottomWidth: 1, borderBottomColor: tokens.colors.border }}>
        <Text style={{ color: tokens.colors.cardForeground, fontWeight: '700' }}>Peak Hours & Staffing Hints</Text>
      </View>
      <View style={{ padding: 12, gap: 12 }}>
        <HeatmapGrid points={points} xLabels={xLabels} yLabels={yLabels} testID="an-peak-heatmap" />

        {/* Hints */}
        <View style={{ flexDirection: 'row', gap: 12 }}>
          <View style={{ flex: 1, borderWidth: 1, borderColor: tokens.colors.border, borderRadius: 12, padding: 12 }}>
            <Text style={{ color: tokens.colors.mutedForeground, marginBottom: 6 }}>Hint</Text>
            <Text style={{ color: tokens.colors.cardForeground, fontWeight: '700' }}>Add 1 agent 18–21 UTC+0</Text>
          </View>
          <View style={{ flex: 1, borderWidth: 1, borderColor: tokens.colors.border, borderRadius: 12, padding: 12 }}>
            <Text style={{ color: tokens.colors.mutedForeground, marginBottom: 6 }}>Hint</Text>
            <Text style={{ color: tokens.colors.cardForeground, fontWeight: '700' }}>Enable outside‑hours auto‑responder</Text>
          </View>
        </View>

        {/* CTA */}
        <View style={{ alignItems: 'flex-end' }}>
          <TouchableOpacity onPress={() => navigation.navigate('Automations', { screen: 'BusinessHours' })} accessibilityRole="button" accessibilityLabel="Open Business Hours" style={{ paddingHorizontal: 12, paddingVertical: 8, borderRadius: 8, backgroundColor: tokens.colors.primary }}>
            <Text style={{ color: tokens.colors.primaryForeground, fontWeight: '700' }}>Open Business Hours</Text>
          </TouchableOpacity>
        </View>
      </View>
    </View>
  )
}

export default React.memo(PeakHours)


