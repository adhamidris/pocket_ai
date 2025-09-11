import React from 'react'
import { SafeAreaView, View, Text, TouchableOpacity, FlatList, Image, InteractionManager } from 'react-native'
import { useTheme } from '../../providers/ThemeProvider'
import { useSafeAreaInsets } from 'react-native-safe-area-context'

type MetricRow = { id: string; label: string; value: string; warn?: boolean }

const BUDGETS = {
  initialNavMs: 300,
  scrollFps: 55,
  renderPerItemMs: 2,
}

const makeConversations = (n: number) => Array.from({ length: n }).map((_, i) => ({ id: `c-${i}`, title: `Conv ${i}`, snippet: 'Where is my order?', updated: Date.now() - i * 1000 }))
const makeAudit = (n: number) => Array.from({ length: n }).map((_, i) => ({ id: `a-${i}`, actor: `user_${i%50}`, action: 'update', at: Date.now() - i * 60000 }))
const makeSeries = (days: number) => Array.from({ length: days }).map((_, i) => ({ x: i, y: Math.round(Math.abs(Math.sin(i/5))*100) }))

export const PerfAudit: React.FC = () => {
  const { theme } = useTheme()
  const insets = useSafeAreaInsets()
  const [convCount, setConvCount] = React.useState<number>(500)
  const [auditCount, setAuditCount] = React.useState<number>(1000)
  const [days, setDays] = React.useState<number>(84) // 12 weeks
  const [imgs, setImgs] = React.useState<boolean>(false)
  const [virt, setVirt] = React.useState<boolean>(true)
  const [metrics, setMetrics] = React.useState<MetricRow[]>([])

  const conv = React.useMemo(() => makeConversations(convCount), [convCount])
  const audit = React.useMemo(() => makeAudit(auditCount), [auditCount])
  const series = React.useMemo(() => makeSeries(days), [days])

  const measureInitial = async () => {
    const t0 = Date.now()
    await InteractionManager.runAfterInteractions(() => {})
    const t1 = Date.now() - t0
    addMetric({ id: 'initial', label: 'Initial nav (ms)', value: String(t1), warn: t1 > BUDGETS.initialNavMs })
  }

  const addMetric = (m: MetricRow) => setMetrics(prev => [{ ...m }, ...prev].slice(0, 20))

  const onScrollPerf = (() => {
    let last = 0, frames = 0, start = 0
    return (e: any) => {
      const now = Date.now()
      if (!start) start = now
      frames++
      if (now - last >= 1000) {
        const secs = (now - start) / 1000
        const fps = Math.round(frames / secs)
        addMetric({ id: `fps-${now}`, label: 'Scroll FPS', value: String(fps), warn: fps < BUDGETS.scrollFps })
        last = now; frames = 0; start = now
      }
    }
  })()

  React.useEffect(() => { measureInitial() }, [])

  const renderConv = ({ item }: any) => {
    const t0 = global.performance?.now?.() || Date.now()
    const view = (
      <View style={{ padding: 12, borderBottomWidth: 1, borderBottomColor: theme.color.border, flexDirection: 'row', alignItems: 'center', gap: 12 }}>
        {imgs ? <Image source={{ uri: `https://picsum.photos/seed/${item.id}/56/56` }} style={{ width: 56, height: 56, borderRadius: 8 }} /> : null}
        <View style={{ flex: 1 }}>
          <Text style={{ color: theme.color.cardForeground, fontWeight: '700' }}>{item.title}</Text>
          <Text style={{ color: theme.color.mutedForeground }}>{item.snippet}</Text>
        </View>
        <Text style={{ color: theme.color.mutedForeground }}>{new Date(item.updated).toLocaleTimeString()}</Text>
      </View>
    )
    const t1 = global.performance?.now?.() || Date.now()
    const dt = t1 - t0
    if (dt > 0) addMetric({ id: `conv-${item.id}`, label: 'Render/item (ms)', value: dt.toFixed(2), warn: dt > BUDGETS.renderPerItemMs })
    return view
  }

  const renderAudit = ({ item }: any) => (
    <View style={{ padding: 12, borderBottomWidth: 1, borderBottomColor: theme.color.border, flexDirection: 'row', justifyContent: 'space-between' }}>
      <Text style={{ color: theme.color.cardForeground }}>{item.actor}</Text>
      <Text style={{ color: theme.color.mutedForeground }}>{item.action}</Text>
      <Text style={{ color: theme.color.mutedForeground }}>{new Date(item.at).toLocaleString()}</Text>
    </View>
  )

  return (
    <SafeAreaView style={{ flex: 1, backgroundColor: theme.color.background }}>
      <View style={{ paddingHorizontal: 16, paddingTop: insets.top + 12, paddingBottom: 12, flexDirection: 'row', alignItems: 'center', justifyContent: 'space-between' }}>
        <Text style={{ color: theme.color.foreground, fontSize: 22, fontWeight: '700' }}>Performance Audit</Text>
        <View style={{ flexDirection: 'row', gap: 8 }}>
          <TouchableOpacity onPress={() => setImgs(v => !v)} accessibilityRole="button" accessibilityLabel="Toggle images" style={{ paddingHorizontal: 10, paddingVertical: 8, borderRadius: 8, borderWidth: 1, borderColor: theme.color.border }}>
            <Text style={{ color: theme.color.cardForeground }}>{imgs ? 'Images: On' : 'Images: Off'}</Text>
          </TouchableOpacity>
          <TouchableOpacity onPress={() => setVirt(v => !v)} accessibilityRole="button" accessibilityLabel="Toggle virtualization" style={{ paddingHorizontal: 10, paddingVertical: 8, borderRadius: 8, borderWidth: 1, borderColor: theme.color.border }}>
            <Text style={{ color: theme.color.cardForeground }}>{virt ? 'Virt: On' : 'Virt: Off'}</Text>
          </TouchableOpacity>
        </View>
      </View>
      <View style={{ paddingHorizontal: 16, gap: 12 }}>
        <View style={{ borderWidth: 1, borderColor: theme.color.border, borderRadius: 12 }}>
          {metrics.map((m) => (
            <View key={m.id} style={{ flexDirection: 'row', alignItems: 'center', justifyContent: 'space-between', paddingHorizontal: 12, paddingVertical: 8, borderBottomWidth: 1, borderBottomColor: theme.color.border }}>
              <Text style={{ color: theme.color.cardForeground }}>{m.label}</Text>
              <Text style={{ color: m.warn ? theme.color.warning : theme.color.mutedForeground, fontWeight: '700' }}>{m.value}</Text>
            </View>
          ))}
        </View>
      </View>

      <View style={{ flex: 1, paddingHorizontal: 16, paddingTop: 12 }}>
        <Text style={{ color: theme.color.mutedForeground, marginBottom: 6 }}>Conversations ({convCount})</Text>
        <FlatList
          data={conv}
          renderItem={renderConv}
          keyExtractor={(it) => it.id}
          onScroll={onScrollPerf}
          initialNumToRender={virt ? 12 : conv.length}
          windowSize={virt ? 10 : 100}
          maxToRenderPerBatch={virt ? 12 : 200}
          removeClippedSubviews={virt}
        />
        <View style={{ height: 12 }} />
        <Text style={{ color: theme.color.mutedForeground, marginBottom: 6 }}>Audit Log ({auditCount})</Text>
        <FlatList
          data={audit}
          renderItem={renderAudit}
          keyExtractor={(it) => it.id}
          initialNumToRender={virt ? 20 : audit.length}
          windowSize={virt ? 12 : 100}
          maxToRenderPerBatch={virt ? 24 : 400}
          removeClippedSubviews={virt}
        />
      </View>
    </SafeAreaView>
  )
}

export default PerfAudit


