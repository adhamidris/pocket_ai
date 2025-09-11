import React from 'react'
import { SafeAreaView, View, Text, TouchableOpacity, Alert, ScrollView } from 'react-native'
import { useSafeAreaInsets } from 'react-native-safe-area-context'
import { useTheme } from '../../providers/ThemeProvider'
import { useNavigation, useRoute } from '@react-navigation/native'

const metricsAll = ['volume','frtP50','frtP90','resolutionRate','csat','deflection','aht','slaBreaches']
const grains = ['hour','day','week','month']
const breakdowns = ['channel','intent','agent','priority','segment','source']

const Pill: React.FC<{ label: string; selected: boolean; onPress: () => void }> = ({ label, selected, onPress }) => {
  const { theme } = useTheme()
  return (
    <TouchableOpacity onPress={onPress} style={{ paddingHorizontal: 10, paddingVertical: 6, borderRadius: 12, borderWidth: 1, borderColor: selected ? theme.color.primary : theme.color.border }}>
      <Text style={{ color: selected ? theme.color.primary : theme.color.mutedForeground, fontWeight: '600' }}>{label}</Text>
    </TouchableOpacity>
  )
}

const ExportScreen: React.FC = () => {
  const { theme } = useTheme()
  const insets = useSafeAreaInsets()
  const navigation = useNavigation<any>()
  const route = useRoute<any>()

  const [selectedMetrics, setSelectedMetrics] = React.useState<string[]>(route.params?.metrics || ['volume','resolutionRate'])
  const [grain, setGrain] = React.useState<string>(route.params?.grain || 'day')
  const [selectedBreakdowns, setSelectedBreakdowns] = React.useState<string[]>(route.params?.breakdowns || ['channel'])

  const toggle = (arr: string[], setArr: (v: string[]) => void, v: string) => {
    setArr(arr.includes(v) ? arr.filter((x) => x !== v) : [...arr, v])
  }

  const doExport = (kind: 'CSV' | 'PDF') => {
    try { (require('../../lib/analytics') as any).track('analytics.export', { type: kind.toLowerCase() }) } catch {}
    console.log('Export', { kind, selectedMetrics, grain, selectedBreakdowns })
    Alert.alert('Export', `${kind} generated (stub).`)
    navigation.goBack()
  }

  return (
    <SafeAreaView style={{ flex: 1, backgroundColor: theme.color.background }}>
      {/* Header */}
      <View style={{ paddingHorizontal: 16, paddingTop: insets.top + 8, paddingBottom: 12, borderBottomWidth: 1, borderBottomColor: theme.color.border }}>
        <View style={{ flexDirection: 'row', alignItems: 'center', justifyContent: 'space-between' }}>
          <TouchableOpacity onPress={() => navigation.goBack()} accessibilityLabel="Back" accessibilityRole="button" style={{ padding: 8 }}>
            <Text style={{ color: theme.color.primary, fontWeight: '600' }}>{'< Back'}</Text>
          </TouchableOpacity>
          <Text style={{ color: theme.color.foreground, fontSize: 18, fontWeight: '700' }}>Export</Text>
          <View style={{ width: 64 }} />
        </View>
      </View>

      <ScrollView style={{ padding: 16 }} contentContainerStyle={{ paddingBottom: 24 }}>
        <Text style={{ color: theme.color.cardForeground, fontWeight: '700', marginBottom: 6 }}>Metrics</Text>
        <View style={{ flexDirection: 'row', flexWrap: 'wrap', gap: 8, marginBottom: 12 }}>
          {metricsAll.map((m) => (
            <Pill key={m} label={m} selected={selectedMetrics.includes(m)} onPress={() => toggle(selectedMetrics, setSelectedMetrics, m)} />
          ))}
        </View>

        <Text style={{ color: theme.color.cardForeground, fontWeight: '700', marginBottom: 6 }}>Time grain</Text>
        <View style={{ flexDirection: 'row', flexWrap: 'wrap', gap: 8, marginBottom: 12 }}>
          {grains.map((g) => (
            <Pill key={g} label={g} selected={grain === g} onPress={() => setGrain(g)} />
          ))}
        </View>

        <Text style={{ color: theme.color.cardForeground, fontWeight: '700', marginBottom: 6 }}>Breakdowns</Text>
        <View style={{ flexDirection: 'row', flexWrap: 'wrap', gap: 8, marginBottom: 24 }}>
          {breakdowns.map((b) => (
            <Pill key={b} label={b} selected={selectedBreakdowns.includes(b)} onPress={() => toggle(selectedBreakdowns, setSelectedBreakdowns, b)} />
          ))}
        </View>

        <View style={{ flexDirection: 'row', gap: 8 }}>
          <TouchableOpacity onPress={() => doExport('CSV')} accessibilityRole="button" accessibilityLabel="Export CSV" style={{ paddingHorizontal: 12, paddingVertical: 10, borderRadius: 12, borderWidth: 1, borderColor: theme.color.border }}>
            <Text style={{ color: theme.color.cardForeground, fontWeight: '600' }}>Generate CSV</Text>
          </TouchableOpacity>
          <TouchableOpacity onPress={() => doExport('PDF')} accessibilityRole="button" accessibilityLabel="Export PDF" style={{ paddingHorizontal: 12, paddingVertical: 10, borderRadius: 12, backgroundColor: theme.color.primary }}>
            <Text style={{ color: theme.color.primaryForeground, fontWeight: '700' }}>Generate PDF</Text>
          </TouchableOpacity>
        </View>
      </ScrollView>
    </SafeAreaView>
  )
}

export default ExportScreen


