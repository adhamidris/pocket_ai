import React from 'react'
import { SafeAreaView, View, Text, TouchableOpacity, Alert, FlatList } from 'react-native'
import { useSafeAreaInsets } from 'react-native-safe-area-context'
import { useTheme } from '../../providers/ThemeProvider'
import { useNavigation, useRoute } from '@react-navigation/native'
import { CoverageStat, DriftWarning } from '../../types/knowledge'
import CoverageTile from '../../components/knowledge/CoverageTile'

const Bar: React.FC<{ label: string; value: number; max: number }> = ({ label, value, max }) => {
  const pct = Math.min(100, Math.round((value / Math.max(1, max)) * 100))
  return (
    <View style={{ marginBottom: 8 }}>
      <Text style={{ fontSize: 12, color: '#6B7280', marginBottom: 4 }}>{label}: {value}</Text>
      <View style={{ height: 8, borderRadius: 4, backgroundColor: '#E5E7EB' }}>
        <View style={{ width: `${pct}%`, height: '100%', backgroundColor: '#3B82F6', borderRadius: 4 }} />
      </View>
    </View>
  )
}

const CoverageHealth: React.FC = () => {
  const { theme } = useTheme()
  const insets = useSafeAreaInsets()
  const navigation = useNavigation<any>()
  const route = useRoute<any>()

  const coverage: CoverageStat = route.params?.coverage || { topics: 40, faqs: 120, gaps: 6, coveragePct: 84 }
  const drift: DriftWarning[] = route.params?.drift || []

  const onWarningPress = (w: DriftWarning) => {
    const isUrl = w.kind === 'stale_url' || w.kind === 'domain_unreachable'
    const buttons = [
      isUrl ? { text: 'Open URL', onPress: () => Alert.alert('Open URL', 'Opening source URL (stub).') } : undefined,
      { text: 'Re-train', onPress: () => Alert.alert('Re-train', 'Queued re-train (UI-only).') },
      { text: 'Remove', style: 'destructive', onPress: () => Alert.alert('Removed', 'Source removed (UI-only).') },
      { text: 'Cancel', style: 'cancel' as const },
    ].filter(Boolean) as any[]
    Alert.alert('Drift Warning', w.kind.replaceAll('_', ' '), buttons)
  }

  return (
    <SafeAreaView style={{ flex: 1, backgroundColor: theme.color.background }}>
      {/* Header */}
      <View style={{ paddingHorizontal: 16, paddingTop: insets.top + 8, paddingBottom: 12, borderBottomWidth: 1, borderBottomColor: theme.color.border }}>
        <View style={{ flexDirection: 'row', alignItems: 'center', justifyContent: 'space-between' }}>
          <TouchableOpacity onPress={() => navigation.goBack()} accessibilityLabel="Back" accessibilityRole="button" style={{ padding: 8 }}>
            <Text style={{ color: theme.color.primary, fontWeight: '600' }}>{'< Back'}</Text>
          </TouchableOpacity>
          <Text style={{ color: theme.color.foreground, fontSize: 18, fontWeight: '700' }}>Coverage & Health</Text>
          <View style={{ width: 64 }} />
        </View>
      </View>

      <View style={{ padding: 16 }}>
        <CoverageTile stat={coverage} testID="kn-coverage-health" />
        <View style={{ height: 12 }} />
        <Bar label="Topics" value={coverage.topics} max={200} />
        <Bar label="FAQs" value={coverage.faqs} max={400} />
        <Bar label="Gaps" value={coverage.gaps} max={50} />
      </View>

      <View style={{ paddingHorizontal: 16 }}>
        <Text style={{ color: theme.color.cardForeground, fontWeight: '700', marginBottom: 8 }}>Drift Warnings</Text>
      </View>
      <FlatList
        style={{ paddingHorizontal: 16 }}
        data={drift}
        keyExtractor={(w, i) => `${w.sourceId}-${w.kind}-${i}`}
        renderItem={({ item }) => (
          <TouchableOpacity onPress={() => onWarningPress(item)} accessibilityLabel={`Drift ${item.kind}`} accessibilityRole="button" style={{ padding: 12, borderWidth: 1, borderColor: theme.color.border, borderRadius: 12, marginBottom: 8 }}>
            <Text style={{ color: theme.color.cardForeground, fontWeight: '600' }}>{item.kind.replaceAll('_', ' ').toUpperCase()}</Text>
            <Text style={{ color: theme.color.mutedForeground }}>Source: {item.sourceId}</Text>
            {item.detail && <Text style={{ color: theme.color.mutedForeground }}>{item.detail}</Text>}
          </TouchableOpacity>
        )}
        ListEmptyComponent={<Text style={{ color: theme.color.mutedForeground }}>No warnings.</Text>}
      />
    </SafeAreaView>
  )
}

export default CoverageHealth


