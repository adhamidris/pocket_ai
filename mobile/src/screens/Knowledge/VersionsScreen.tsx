import React from 'react'
import { SafeAreaView, View, Text, FlatList, TouchableOpacity, Modal, Alert } from 'react-native'
import { useSafeAreaInsets } from 'react-native-safe-area-context'
import { useTheme } from '../../providers/ThemeProvider'
import { useNavigation, useRoute } from '@react-navigation/native'
import { KnowledgeSource, TrainingJob } from '../../types/knowledge'
import TrainProgressBar from '../../components/knowledge/TrainProgressBar'
import { track } from '../../lib/analytics'

type VersionRun = {
  id: string
  startedAt: number
  finishedAt?: number
  status: 'completed' | 'running' | 'failed'
  message?: string
  includedSourceIds: string[]
  stats: { topicsDelta: number; faqsDelta: number }
}

const genRuns = (sources: KnowledgeSource[]): VersionRun[] => {
  const now = Date.now()
  const pick = (n: number) => sources.slice(0, Math.max(1, Math.min(sources.length, n))).map((s) => s.id)
  return [
    { id: 'v-3', startedAt: now - 3600_000 * 6, finishedAt: now - 3600_000 * 5, status: 'completed', includedSourceIds: pick(3), stats: { topicsDelta: 5, faqsDelta: 12 } },
    { id: 'v-2', startedAt: now - 3600_000 * 24, finishedAt: now - 3600_000 * 23, status: 'completed', includedSourceIds: pick(2), stats: { topicsDelta: 2, faqsDelta: -1 } },
    { id: 'v-1', startedAt: now - 3600_000 * 48, finishedAt: now - 3600_000 * 47, status: 'failed', message: 'Timeout fetching site map', includedSourceIds: pick(1), stats: { topicsDelta: 0, faqsDelta: 0 } },
  ]
}

const VersionsScreen: React.FC = () => {
  const { theme } = useTheme()
  const insets = useSafeAreaInsets()
  const navigation = useNavigation<any>()
  const route = useRoute<any>()
  const sources: KnowledgeSource[] = route.params?.sources || []

  const [runs, setRuns] = React.useState<VersionRun[]>(() => genRuns(sources))
  const [selected, setSelected] = React.useState<VersionRun | null>(runs[0] || null)
  const [confirmOpen, setConfirmOpen] = React.useState(false)

  const selectedSources = React.useMemo(() => {
    if (!selected) return []
    return selected.includedSourceIds.map((id) => sources.find((s) => s.id === id)).filter(Boolean) as KnowledgeSource[]
  }, [selected, sources])

  return (
    <SafeAreaView style={{ flex: 1, backgroundColor: theme.color.background }}>
      {/* Header */}
      <View style={{ paddingHorizontal: 16, paddingTop: insets.top + 8, paddingBottom: 12, borderBottomWidth: 1, borderBottomColor: theme.color.border }}>
        <View style={{ flexDirection: 'row', alignItems: 'center', justifyContent: 'space-between' }}>
          <TouchableOpacity onPress={() => navigation.goBack()} accessibilityLabel="Back" accessibilityRole="button" style={{ padding: 8 }}>
            <Text style={{ color: theme.color.primary, fontWeight: '600' }}>{'< Back'}</Text>
          </TouchableOpacity>
          <Text style={{ color: theme.color.foreground, fontSize: 18, fontWeight: '700' }}>Versions</Text>
          <TouchableOpacity onPress={() => { setConfirmOpen(true); track('knowledge.version', { action: 'restore' }) }} accessibilityLabel="Restore Version" accessibilityRole="button" hitSlop={{ top: 8, bottom: 8, left: 8, right: 8 }} style={{ padding: 8 }}>
            <Text style={{ color: theme.color.primary, fontWeight: '700' }}>Restore</Text>
          </TouchableOpacity>
        </View>
      </View>

      <View style={{ flex: 1, flexDirection: 'row' }}>
        {/* Left: timeline */}
        <View style={{ width: 200, borderRightWidth: 1, borderRightColor: theme.color.border }}>
          <FlatList
            data={runs}
            keyExtractor={(r) => r.id}
            renderItem={({ item }) => (
              <TouchableOpacity onPress={() => setSelected(item)} accessibilityLabel={`Select ${item.id}`} accessibilityRole="button" style={{ padding: 12, backgroundColor: selected?.id === item.id ? theme.color.muted : 'transparent' }}>
                <Text style={{ color: theme.color.cardForeground, fontWeight: '600' }}>{item.id.toUpperCase()}</Text>
                <Text style={{ color: theme.color.mutedForeground, fontSize: 12 }}>{new Date(item.startedAt).toLocaleString()}</Text>
                <Text style={{ color: item.status === 'failed' ? theme.color.destructive : theme.color.mutedForeground, fontSize: 12 }}>{item.status.toUpperCase()}</Text>
              </TouchableOpacity>
            )}
          />
        </View>

        {/* Right: details */}
        <View style={{ flex: 1, padding: 16 }}>
          {selected && (
            <View>
              <Text style={{ color: theme.color.cardForeground, fontWeight: '700', marginBottom: 6 }}>Run {selected.id.toUpperCase()}</Text>
              <Text style={{ color: theme.color.mutedForeground, marginBottom: 6 }}>Included sources ({selectedSources.length})</Text>
              {selectedSources.map((s) => (
                <View key={s.id} style={{ padding: 10, borderWidth: 1, borderColor: theme.color.border, borderRadius: 10, marginBottom: 8 }}>
                  <Text style={{ color: theme.color.cardForeground, fontWeight: '600' }}>{s.title}</Text>
                  <Text style={{ color: theme.color.mutedForeground, fontSize: 12 }}>{s.kind.toUpperCase()} • {s.scope}</Text>
                </View>
              ))}

              <View style={{ flexDirection: 'row', gap: 12, marginTop: 8 }}>
                <View style={{ paddingHorizontal: 10, paddingVertical: 6, borderWidth: 1, borderColor: theme.color.border, borderRadius: 8 }}>
                  <Text style={{ color: theme.color.cardForeground }}>+{selected.stats.topicsDelta} topics</Text>
                </View>
                <View style={{ paddingHorizontal: 10, paddingVertical: 6, borderWidth: 1, borderColor: theme.color.border, borderRadius: 8 }}>
                  <Text style={{ color: theme.color.cardForeground }}>{selected.stats.faqsDelta >= 0 ? '+' : ''}{selected.stats.faqsDelta} FAQs</Text>
                </View>
                <View style={{ paddingHorizontal: 10, paddingVertical: 6, borderWidth: 1, borderColor: theme.color.border, borderRadius: 8 }}>
                  <Text style={{ color: theme.color.mutedForeground }}>Diff (UI only)</Text>
                </View>
              </View>

              {selected.status !== 'completed' && (
                <View style={{ marginTop: 12 }}>
                  <TrainProgressBar job={{ id: 'tmp', sourceId: 'n/a', state: selected.status === 'failed' ? 'failed' : 'running', progress: selected.status === 'failed' ? 0 : 64, startedAt: selected.startedAt } as TrainingJob} />
                </View>
              )}

              {!!selected.message && (
                <Text style={{ color: theme.color.destructive, marginTop: 8 }}>{selected.message}</Text>
              )}

              <View style={{ flexDirection: 'row', gap: 12, marginTop: 12 }}>
                <TouchableOpacity onPress={() => setConfirmOpen(true)} accessibilityLabel="Restore" accessibilityRole="button" hitSlop={{ top: 8, bottom: 8, left: 8, right: 8 }} style={{ paddingHorizontal: 12, paddingVertical: 8, borderWidth: 1, borderColor: theme.color.border, borderRadius: 10 }}>
                  <Text style={{ color: theme.color.primary, fontWeight: '700' }}>Restore</Text>
                </TouchableOpacity>
                <TouchableOpacity onPress={() => { track('knowledge.version', { action: 'diff_view' }); Alert.alert('Diff', 'Showing diff is UI-only placeholder.') }} accessibilityLabel="Diff" accessibilityRole="button" hitSlop={{ top: 8, bottom: 8, left: 8, right: 8 }} style={{ paddingHorizontal: 12, paddingVertical: 8, borderWidth: 1, borderColor: theme.color.border, borderRadius: 10 }}>
                  <Text style={{ color: theme.color.cardForeground, fontWeight: '600' }}>View Diff</Text>
                </TouchableOpacity>
              </View>
            </View>
          )}
        </View>
      </View>

      {/* Confirm restore */}
      <Modal transparent visible={confirmOpen} animationType="fade" onRequestClose={() => setConfirmOpen(false)}>
        <View style={{ flex: 1, backgroundColor: 'rgba(0,0,0,0.4)', alignItems: 'center', justifyContent: 'center', padding: 16 }}>
          <View style={{ backgroundColor: theme.color.card, borderRadius: 16, padding: 16, borderWidth: 1, borderColor: theme.color.border, width: '90%' }}>
            <Text style={{ color: theme.color.cardForeground, fontWeight: '700', marginBottom: 8 }}>Restore this version?</Text>
            <Text style={{ color: theme.color.mutedForeground, marginBottom: 12 }}>UI-only: this will not change any real data.</Text>
            <View style={{ flexDirection: 'row', justifyContent: 'flex-end', gap: 12 }}>
              <TouchableOpacity onPress={() => setConfirmOpen(false)} style={{ paddingHorizontal: 12, paddingVertical: 8 }}>
                <Text style={{ color: theme.color.mutedForeground }}>Cancel</Text>
              </TouchableOpacity>
              <TouchableOpacity onPress={() => { setConfirmOpen(false); Alert.alert('Restored', 'Version restore queued (UI-only).') }} style={{ paddingHorizontal: 12, paddingVertical: 8 }}>
                <Text style={{ color: theme.color.primary, fontWeight: '700' }}>Restore</Text>
              </TouchableOpacity>
            </View>
          </View>
        </View>
      </Modal>
    </SafeAreaView>
  )
}

export default VersionsScreen


