import React from 'react'
import { SafeAreaView, View, Text, TouchableOpacity, ScrollView, Switch } from 'react-native'
import { useSafeAreaInsets } from 'react-native-safe-area-context'
import { useTheme } from '../../providers/ThemeProvider'
import { useNavigation } from '@react-navigation/native'
import { SeriesPoint, TimeRange, Timegrain } from '../../types/analytics'
import NumberTile from '../../components/analytics/NumberTile'
import { track } from '../../lib/analytics'

const mkSeries = (points: number, base: number): SeriesPoint[] => {
  const start = Date.now() - points * 3600_000
  return Array.from({ length: points }, (_, i) => ({ ts: start + i * 3600_000, value: Math.max(1, Math.round(base + (Math.sin(i / 3) * 8) + (Math.random() * 6 - 3))) }))
}

const metrics = [
  { key: 'FRT P50', base: 40, unit: 'm' },
  { key: 'Resolution %', base: 85, unit: '%' },
  { key: 'CSAT', base: 92, unit: '%' },
  { key: 'Volume', base: 120, unit: '' },
  { key: 'Deflection %', base: 45, unit: '%' },
] as const

const grains: Timegrain[] = ['hour', 'day', 'week', 'month']

const TrendsScreen: React.FC = () => {
  const { theme } = useTheme()
  const insets = useSafeAreaInsets()
  const navigation = useNavigation<any>()

  const [tab, setTab] = React.useState<number>(0)
  const [grain, setGrain] = React.useState<Timegrain>('day')
  const [compare, setCompare] = React.useState<boolean>(true)

  const points = grain === 'hour' ? 48 : grain === 'day' ? 30 * 1 : grain === 'week' ? 26 : 12
  const series = React.useMemo(() => mkSeries(points, metrics[tab].base), [tab, points])

  const cur = Math.round(series.reduce((a, b) => a + b.value, 0) / (series.length || 1))
  const prev = cur + (Math.random() > 0.5 ? -6 : 6)
  const delta = Math.round(((cur - prev) / (prev || 1)) * 100)

  const onBucketPress = (bucketIdx: number) => {
    const ts = series[bucketIdx]?.ts
    if (!ts) return
    navigation.navigate('Conversations', { atTs: ts })
  }

  React.useEffect(() => { track('analytics.trends.view') }, [])

  const Chip: React.FC<{ label: string; active?: boolean; onPress: () => void; a11y?: string }> = ({ label, active, onPress, a11y }) => (
    <TouchableOpacity onPress={onPress} accessibilityLabel={a11y || label} accessibilityRole="button" style={{ paddingHorizontal: 12, paddingVertical: 8, borderRadius: 12, borderWidth: 1, borderColor: active ? theme.color.primary : theme.color.border }}>
      <Text style={{ color: active ? theme.color.primary : theme.color.mutedForeground, fontWeight: '600' }}>{label}</Text>
    </TouchableOpacity>
  )

  return (
    <SafeAreaView style={{ flex: 1, backgroundColor: theme.color.background }}>
      <ScrollView style={{ flex: 1 }}>
        {/* Header */}
        <View style={{ paddingHorizontal: 24, paddingTop: insets.top + 12, paddingBottom: 12 }}>
          <Text style={{ color: theme.color.foreground, fontSize: 28, fontWeight: '700', marginBottom: 8 }}>Trends</Text>
          <View style={{ flexDirection: 'row', flexWrap: 'wrap', gap: 8 }}>
            {metrics.map((m, i) => (
              <Chip key={m.key} label={m.key} active={tab === i} onPress={() => setTab(i)} />
            ))}
          </View>

          {/* Controls */}
          <View style={{ flexDirection: 'row', alignItems: 'center', justifyContent: 'space-between', marginTop: 12 }}>
            <View style={{ flexDirection: 'row', gap: 8 }}>
              {grains.map((g) => (
                <Chip key={g} label={g} active={grain === g} onPress={() => setGrain(g)} a11y={`Grain ${g}`} />
              ))}
            </View>
            <View style={{ flexDirection: 'row', alignItems: 'center', gap: 8 }}>
              <Text style={{ color: theme.color.mutedForeground }}>Compare</Text>
              <Switch value={compare} onValueChange={setCompare} accessibilityLabel="Compare period" />
            </View>
          </View>
        </View>

        {/* Summary cards */}
        <View style={{ paddingHorizontal: 24, marginBottom: 12 }}>
          <View style={{ flexDirection: 'row', gap: 12 }}>
            <View style={{ flex: 1 }}>
              <NumberTile label={metrics[tab].key} value={`${cur}${metrics[tab].unit}`} delta={delta} helpText="vs prev" />
            </View>
            <View style={{ flex: 1 }}>
              <NumberTile label="Previous" value={`${prev}${metrics[tab].unit}`} />
            </View>
          </View>
        </View>

        {/* Big chart with interactive buckets */}
        <View style={{ paddingHorizontal: 24, marginBottom: 16 }}>
          <View style={{ position: 'relative', height: 180, borderWidth: 1, borderColor: theme.color.border, borderRadius: 12, padding: 12, backgroundColor: theme.color.card }}>
            {/* Bars */}
            <View style={{ position: 'absolute', left: 12, right: 12, top: 12, bottom: 12, flexDirection: 'row', alignItems: 'flex-end', gap: 2 }}>
              {(() => {
                const max = Math.max(1, ...series.map((p) => p.value))
                return series.map((p, idx) => (
                  <View key={idx} style={{ width: 8, height: Math.max(2, Math.round((p.value / max) * 156)), backgroundColor: theme.color.primary as any, borderRadius: 3 }} />
                ))
              })()}
            </View>
            {/* Touch overlay */}
            <View style={{ position: 'absolute', left: 12, right: 12, top: 12, bottom: 12, flexDirection: 'row' }} pointerEvents="box-none">
              {series.map((_, idx) => (
                <TouchableOpacity key={idx} onPress={() => onBucketPress(idx)} accessibilityLabel={`Open conversations at bucket ${idx + 1}`} accessibilityRole="button" style={{ flex: 1 }} />
              ))}
            </View>
          </View>
          <Text style={{ color: theme.color.mutedForeground }}>Tap a bar to open Conversations at that time.</Text>
        </View>
      </ScrollView>
    </SafeAreaView>
  )
}

export default TrendsScreen


