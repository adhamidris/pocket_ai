import React from 'react'
import { SafeAreaView, View, Text, TouchableOpacity, ScrollView } from 'react-native'
import { useSafeAreaInsets } from 'react-native-safe-area-context'
import { useTheme } from '../../providers/ThemeProvider'
import { TimeRange, SeriesPoint } from '../../types/analytics'
import DateRangePicker from '../../components/analytics/DateRangePicker'
import AnalyticsFilterBar from '../../components/analytics/FilterBar'
import NumberTile from '../../components/analytics/NumberTile'
import LineChartMini from '../../components/analytics/LineChartMini'
import ExportBar from '../../components/analytics/ExportBar'
import ChannelBreakdown from '../../components/analytics/ChannelBreakdown'
import IntentAnalytics from '../../components/analytics/IntentAnalytics'
import AgentPerformance from '../../components/analytics/AgentPerformance'
import CohortsRepeat from '../../components/analytics/CohortsRepeat'
import PeakHours from '../../components/analytics/PeakHours'
import Funnels from '../../components/analytics/Funnels'
import Attribution from '../../components/analytics/Attribution'
import { track } from '../../lib/analytics'
import { DeviceEventEmitter } from 'react-native'
import OfflineBanner from '../../components/dashboard/OfflineBanner'
import { EmptyStateGuide } from '../../components/help/EmptyStateGuide'

const now = () => new Date()
const iso = (d: Date) => d.toISOString()

const makeRange = (days: number): TimeRange => {
  const end = now()
  const start = new Date(end.getTime() - days * 86400000)
  return { startIso: iso(start), endIso: iso(end), grain: days <= 2 ? 'hour' : days <= 30 ? 'day' : 'week' }
}

const genSeries = (n: number, base = 50): SeriesPoint[] => {
  const start = Date.now() - n * 3600_000
  return Array.from({ length: n }, (_, i) => ({ ts: start + i * 3600_000, value: Math.max(1, Math.round(base + (Math.sin(i / 3) * 8) + (Math.random() * 6 - 3))) }))
}

const sum = (arr: number[]) => arr.reduce((a, b) => a + b, 0)
const avg = (arr: number[]) => (arr.length ? sum(arr) / arr.length : 0)

const AnalyticsHome: React.FC = () => {
  const { theme } = useTheme()
  const insets = useSafeAreaInsets()

  const [range, setRange] = React.useState<TimeRange>(() => makeRange(7))
  const [filters, setFilters] = React.useState<Record<string, any>>({})

  const [series, setSeries] = React.useState(() => ({
    frt: genSeries(48, 40),
    resolution: genSeries(48, 85),
    csat: genSeries(48, 92),
    deflect: genSeries(48, 45),
    volume: genSeries(48, 120),
    breaches: genSeries(48, 4),
  }))
  const [offline, setOffline] = React.useState<boolean>(false)
  const [lastRefreshedAt, setLastRefreshedAt] = React.useState<number>(() => Date.now())
  const [anonymized, setAnonymized] = React.useState<boolean>(false)

  React.useEffect(() => { track('analytics.view') }, [])

  React.useEffect(() => {
    const sub = DeviceEventEmitter.addListener('privacy.modes', (vals: any) => {
      setAnonymized(!!vals?.anonymizeAnalytics)
    })
    return () => sub.remove()
  }, [])

  // When range/filters change, regenerate demo series with slight shifts
  React.useEffect(() => {
    const skew = (k: number) => (filters.channel ? 5 : 0) + (filters.priority ? -3 : 0) + (k % 3)
    setSeries({
      frt: genSeries(48, 40 + skew(1)),
      resolution: genSeries(48, 85 + skew(2)),
      csat: genSeries(48, 92 + skew(3)),
      deflect: genSeries(48, 45 + skew(4)),
      volume: genSeries(48, 120 + skew(5)),
      breaches: genSeries(48, 4 + skew(6)),
    })
  }, [range.startIso, range.endIso, filters.channel, filters.segment, filters.priority])

  const kpi = React.useMemo(() => {
    const cur = {
      frt: Math.round(avg(series.frt.map((p) => p.value))),
      res: Math.round(avg(series.resolution.map((p) => p.value))),
      csat: Math.round(avg(series.csat.map((p) => p.value))),
      defl: Math.round(avg(series.deflect.map((p) => p.value))),
      vol: Math.round(sum(series.volume.map((p) => p.value))),
      br: Math.round(sum(series.breaches.map((p) => p.value))),
    }
    const prev = {
      frt: cur.frt + (Math.random() > 0.5 ? -6 : 6),
      res: cur.res + (Math.random() > 0.5 ? -4 : 4),
      csat: cur.csat + (Math.random() > 0.5 ? -2 : 2),
      defl: cur.defl + (Math.random() > 0.5 ? -5 : 5),
      vol: Math.max(1, cur.vol + (Math.random() > 0.5 ? -50 : 50)),
      br: Math.max(0, cur.br + (Math.random() > 0.5 ? -2 : 2)),
    }
    const deltaPct = (c: number, p: number) => Math.round(((c - p) / (p || 1)) * 100)
    return {
      frt: { value: `${cur.frt}m`, delta: deltaPct(cur.frt, prev.frt), series: series.frt },
      res: { value: `${cur.res}%`, delta: deltaPct(cur.res, prev.res), series: series.resolution },
      csat: { value: `${cur.csat}%`, delta: deltaPct(cur.csat, prev.csat), series: series.csat },
      defl: { value: `${cur.defl}%`, delta: deltaPct(cur.defl, prev.defl), series: series.deflect },
      vol: { value: cur.vol, delta: deltaPct(cur.vol, prev.vol), series: series.volume },
      br: { value: cur.br, delta: deltaPct(cur.br, prev.br), series: series.breaches },
    }
  }, [series])

  const setFilter = (key: string, val: any) => setFilters((f) => ({ ...f, [key]: val }))

  const notEnoughData = false // in demo, we usually have data; toggle for guide

  return (
    <SafeAreaView style={{ flex: 1, backgroundColor: theme.color.background }}>
      <ScrollView style={{ flex: 1 }}>
        {/* Header */}
        <View style={{ paddingHorizontal: 24, paddingTop: insets.top + 12, paddingBottom: 12 }}>
          <View style={{ marginBottom: 8 }}>
            <OfflineBanner visible={offline} testID="an-offline" />
          </View>
          <View style={{ flexDirection: 'row', alignItems: 'center', justifyContent: 'space-between' }}>
            <View style={{ flexDirection: 'row', alignItems: 'center', gap: 8 }}>
              <Text style={{ color: theme.color.foreground, fontSize: 28, fontWeight: '700' }}>Analytics</Text>
              {anonymized && (
                <View style={{ paddingHorizontal: 8, paddingVertical: 4, borderRadius: 10, borderWidth: 1, borderColor: theme.color.border }}>
                  <Text style={{ color: theme.color.mutedForeground, fontSize: 12 }}>Anonymized</Text>
                </View>
              )}
            </View>
            <View style={{ flexDirection: 'row', gap: 8 }}>
              <TouchableOpacity onPress={() => setOffline((v) => !v)} accessibilityLabel="Toggle offline" accessibilityRole="button" style={{ paddingHorizontal: 12, paddingVertical: 8, borderRadius: 12, borderWidth: 1, borderColor: offline ? theme.color.primary : theme.color.border }}>
                <Text style={{ color: offline ? theme.color.primary : theme.color.cardForeground, fontWeight: '600' }}>{offline ? 'Cached' : 'Online'}</Text>
              </TouchableOpacity>
              <TouchableOpacity onPress={() => DeviceEventEmitter.emit('assistant.open', { text: 'Why did FRT worsen today?', persona: 'analyst' })} accessibilityLabel="Ask why FRT" accessibilityRole="button" style={{ paddingHorizontal: 12, paddingVertical: 8, borderRadius: 12, borderWidth: 1, borderColor: theme.color.border }}>
                <Text style={{ color: theme.color.cardForeground, fontWeight: '600' }}>Ask why (FRT)</Text>
              </TouchableOpacity>
              <TouchableOpacity onPress={() => { setLastRefreshedAt(Date.now()); setSeries({
                frt: genSeries(48, 40),
                resolution: genSeries(48, 85),
                csat: genSeries(48, 92),
                deflect: genSeries(48, 45),
                volume: genSeries(48, 120),
                breaches: genSeries(48, 4),
              }) }} accessibilityLabel="Refresh" accessibilityRole="button" style={{ paddingHorizontal: 12, paddingVertical: 8, borderRadius: 12, borderWidth: 1, borderColor: theme.color.border }}>
                <Text style={{ color: theme.color.cardForeground, fontWeight: '600' }}>Refresh</Text>
              </TouchableOpacity>
              <TouchableOpacity onPress={() => {
                // @ts-ignore
                (require('@react-navigation/native') as any).useNavigation().navigate('Analytics', { screen: 'Definitions' })
              }} accessibilityLabel="Definitions" accessibilityRole="button" style={{ paddingHorizontal: 12, paddingVertical: 8, borderRadius: 12, borderWidth: 1, borderColor: theme.color.border }}>
                <Text style={{ color: theme.color.cardForeground, fontWeight: '600' }}>Definitions</Text>
              </TouchableOpacity>
              <TouchableOpacity onPress={() => {
                // pass current filters/range; stub metrics
                // @ts-ignore navigation exists via parent
                (require('@react-navigation/native') as any).useNavigation().navigate('Analytics', { screen: 'SavedReports', params: { metrics: ['volume','frtP50','resolutionRate'], dims: ['time'], filters, range } })
              }} accessibilityLabel="Saved Reports" accessibilityRole="button" style={{ paddingHorizontal: 12, paddingVertical: 8, borderRadius: 12, borderWidth: 1, borderColor: theme.color.border }}>
                <Text style={{ color: theme.color.cardForeground, fontWeight: '600' }}>Saved Reports</Text>
              </TouchableOpacity>
            </View>
          </View>

          {/* Controls */}
          <View style={{ marginTop: 8, gap: 12 }}>
            <DateRangePicker range={range} onChange={(r) => { setRange(r); track('analytics.filter', { key: 'range', value: r }) }} testID="an-range" />
            <AnalyticsFilterBar filters={filters} onChange={(k, v) => { setFilter(k, v); track('analytics.filter', { key: k, value: v }) }} testID="an-filters" />
          </View>
        </View>

        {/* KPI tiles */}
        <View style={{ paddingHorizontal: 24 }}>
          {notEnoughData ? (
            <EmptyStateGuide
              title="Not enough data yet"
              lines={["Increase your date range or wait for more volume."]}
              cta={{ label: 'Set timeframe to 30 days', onPress: () => setRange(makeRange(30)) }}
            />
          ) : (
          <ScrollView horizontal showsHorizontalScrollIndicator={false}>
            <View style={{ flexDirection: 'row', gap: 12, paddingBottom: 8 }}>
              {([
                { key: 'FRT P50', data: kpi.frt, def: '50th percentile of first response latency (agent or AI) within time bucket.' },
                { key: 'Resolution %', data: kpi.res, def: 'Resolved conversations divided by total conversations in the period.' },
                { key: 'CSAT', data: kpi.csat, def: 'Average customer satisfaction score for the period.' },
                { key: 'Deflection', data: kpi.defl, def: 'Conversations resolved by AI with no agent divided by total.' },
                { key: 'Volume', data: kpi.vol, def: 'Total conversations in the selected period.', cta: 'Open Conversations' },
                { key: 'SLA Breaches', data: kpi.br, def: 'Total SLA breaches in the selected period.' },
              ] as const).map((tile, idx) => (
                <View key={idx} style={{ width: 200 }}>
                  <NumberTile label={tile.key} value={tile.data.value} delta={tile.data.delta} helpText="vs prev" definition={tile.def} testID={`an-kpi-${idx}`} />
                  {tile.cta && (
                    <View style={{ marginTop: 6 }}>
                      <TouchableOpacity onPress={() => {
                        // @ts-ignore
                        (require('@react-navigation/native') as any).useNavigation().navigate('Conversations', { screen: 'Conversations' })
                      }} accessibilityLabel="Open Conversations" accessibilityRole="button" style={{ paddingHorizontal: 12, paddingVertical: 8, borderRadius: 10, borderWidth: 1, borderColor: theme.color.border }}>
                        <Text style={{ color: theme.color.cardForeground, fontWeight: '600' }}>{tile.cta}</Text>
                      </TouchableOpacity>
                    </View>
                  )}
                  <View style={{ marginTop: 8 }}>
                    <LineChartMini series={tile.data.series} />
                  </View>
                </View>
              ))}
            </View>
          </ScrollView>
          )}
        </View>

        {/* Top Insights */}
        <View style={{ paddingHorizontal: 24, marginTop: 16 }}>
          <Text style={{ color: theme.color.cardForeground, fontWeight: '700', marginBottom: 8 }}>Top Insights</Text>
          <View style={{ gap: 8 }}>
            <Text style={{ color: theme.color.mutedForeground }}>IG DM FRT improved 18% vs last week</Text>
            <Text style={{ color: theme.color.mutedForeground }}>VIP queue breached twice yesterday</Text>
            <Text style={{ color: theme.color.mutedForeground }}>Web volume +12% WoW; CSAT steady</Text>
          </View>
        </View>

        {/* Export */}
        <View style={{ paddingHorizontal: 24, marginTop: 16, marginBottom: 24 }}>
          <ExportBar onExportCsv={() => { track('analytics.export', { type: 'csv' }) }} onExportPdf={() => { track('analytics.export', { type: 'pdf' }) }} testID="an-export" />
        </View>

        {/* Channel Breakdown */}
        <View style={{ paddingHorizontal: 24, marginBottom: 24 }}>
          <ChannelBreakdown testID="an-channel-breakdown" />
        </View>

        {/* Intent Analytics */}
        <View style={{ paddingHorizontal: 24, marginBottom: 24 }}>
          <IntentAnalytics testID="an-intent-analytics" />
        </View>

        {/* Agent Performance */}
        <View style={{ paddingHorizontal: 24, marginBottom: 24 }}>
          <AgentPerformance testID="an-agent-performance" />
        </View>

        {/* Cohorts & Repeat Contacts */}
        <View style={{ paddingHorizontal: 24, marginBottom: 24 }}>
          <CohortsRepeat testID="an-cohorts-repeat" />
        </View>

        {/* Peak Hours */}
        <View style={{ paddingHorizontal: 24, marginBottom: 24 }}>
          <PeakHours testID="an-peak-hours" />
        </View>

        {/* Funnels */}
        <View style={{ paddingHorizontal: 24, marginBottom: 24 }}>
          <Funnels testID="an-funnels" />
        </View>

        {/* Attribution */}
        <View style={{ paddingHorizontal: 24, marginBottom: 24 }}>
          <Attribution testID="an-attribution" />
        </View>
      </ScrollView>
    </SafeAreaView>
  )
}

export default AnalyticsHome


