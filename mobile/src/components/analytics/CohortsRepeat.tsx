import React from 'react'
import { View, Text, TouchableOpacity } from 'react-native'
import { useNavigation } from '@react-navigation/native'
import { tokens } from '../../ui/tokens'
import HeatmapGrid from './HeatmapGrid'
import { HeatmapPoint } from '../../types/analytics'
import EntitlementsGate from '../billing/EntitlementsGate'

export interface CohortsRepeatProps { testID?: string }

const weeks = Array.from({ length: 10 }).map((_, i) => `${i}`)
const cohorts = Array.from({ length: 8 }).map((_, i) => `Week ${i + 1}`)

const genPoints = (): HeatmapPoint[] => {
  const pts: HeatmapPoint[] = []
  for (let y = 0; y < cohorts.length; y++) {
    for (let x = 0; x < weeks.length; x++) {
      // Decay over weeks since first contact
      const base = Math.max(5, 40 - x * 4 + Math.round(Math.random() * 6 - 3))
      pts.push({ x, y, value: base })
    }
  }
  return pts
}

const CohortsRepeat: React.FC<CohortsRepeatProps> = ({ testID }) => {
  const navigation = useNavigation<any>()
  const [points, setPoints] = React.useState<HeatmapPoint[]>(() => genPoints())

  const kpi48h = React.useMemo(() => {
    const cells = points.filter((p) => p.x <= 1)
    const avg = Math.round(cells.reduce((a, b) => a + b.value, 0) / Math.max(1, cells.length))
    return avg
  }, [points])

  const reductionVsPrev = React.useMemo(() => {
    const cur = kpi48h
    const prev = Math.max(1, cur + (Math.random() > 0.5 ? 6 : -6))
    return Math.round(((prev - cur) / prev) * 100)
  }, [kpi48h])

  const openFailureLogForCohort = (y: number) => {
    navigation.navigate('Knowledge', { screen: 'FailureLog', params: { time: '7d', cohort: cohorts[y] } })
  }

  return (
    <EntitlementsGate require="analyticsDepth">
    <View testID={testID} style={{ borderWidth: 1, borderColor: tokens.colors.border, borderRadius: 12, overflow: 'hidden' }}>
      <View style={{ padding: 12, backgroundColor: tokens.colors.card, borderBottomWidth: 1, borderBottomColor: tokens.colors.border }}>
        <Text style={{ color: tokens.colors.cardForeground, fontWeight: '700' }}>Cohorts & Repeat Contacts</Text>
      </View>
      <View style={{ padding: 12, gap: 12 }}>
        {/* KPI cards */}
        <View style={{ flexDirection: 'row', gap: 12 }}>
          <View style={{ flex: 1, borderWidth: 1, borderColor: tokens.colors.border, borderRadius: 12, padding: 12 }}>
            <Text style={{ color: tokens.colors.mutedForeground, marginBottom: 6 }}>Repeat contact % (48h)</Text>
            <Text style={{ color: tokens.colors.cardForeground, fontWeight: '700', fontSize: 20 }}>{kpi48h}%</Text>
          </View>
          <View style={{ flex: 1, borderWidth: 1, borderColor: tokens.colors.border, borderRadius: 12, padding: 12 }}>
            <Text style={{ color: tokens.colors.mutedForeground, marginBottom: 6 }}>Reduction vs last period</Text>
            <Text style={{ color: tokens.colors.success, fontWeight: '700', fontSize: 20 }}>▼ {reductionVsPrev}%</Text>
          </View>
        </View>

        {/* Heatmap */}
        <HeatmapGrid points={points} xLabels={weeks} yLabels={cohorts} testID="an-cohort-heatmap" />

        {/* Row CTAs */}
        <View style={{ gap: 6 }}>
          {cohorts.map((c, yi) => (
            <View key={c} style={{ flexDirection: 'row', alignItems: 'center', justifyContent: 'space-between' }}>
              <Text style={{ color: tokens.colors.mutedForeground, fontSize: 12 }}>{c}</Text>
              <TouchableOpacity onPress={() => openFailureLogForCohort(yi)} accessibilityRole="button" accessibilityLabel={`Failure log ${c}`} style={{ paddingHorizontal: 10, paddingVertical: 6, borderRadius: 8, backgroundColor: tokens.colors.primary }}>
                <Text style={{ color: tokens.colors.primaryForeground, fontWeight: '700' }}>See failure log for cohort</Text>
              </TouchableOpacity>
            </View>
          ))}
        </View>
      </View>
    </View>
    </EntitlementsGate>
  )
}

export default React.memo(CohortsRepeat)


