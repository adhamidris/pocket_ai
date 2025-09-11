import React from 'react'
import { SafeAreaView, View, Text, TouchableOpacity, FlatList, Switch } from 'react-native'
import { useSafeAreaInsets } from 'react-native-safe-area-context'
import { useTheme } from '../../providers/ThemeProvider'
import { useNavigation, useRoute } from '@react-navigation/native'
import { KnowledgeSource, TrainingJob } from '../../types/knowledge'
import TrainProgressBar from '../../components/knowledge/TrainProgressBar'
import { track } from '../../lib/analytics'

type UpdatesMap = Record<string, { status?: 'trained' | 'error' | 'idle' | 'training'; lastTrainedTs?: number }>

const TrainingCenter: React.FC = () => {
  const { theme } = useTheme()
  const insets = useSafeAreaInsets()
  const navigation = useNavigation<any>()
  const route = useRoute<any>()
  const sources: KnowledgeSource[] = (route.params?.sources as KnowledgeSource[]) || []
  const onApply: undefined | ((updates: UpdatesMap) => void) = route.params?.onApply

  const [jobs, setJobs] = React.useState<TrainingJob[]>([])
  const [errorMode, setErrorMode] = React.useState<boolean>(false)

  React.useEffect(() => {
    // Initialize one job per enabled source
    const now = Date.now()
    const initial: TrainingJob[] = sources
      .filter((s) => s.enabled)
      .map((s, idx) => ({ id: `job-${now}-${idx}`, sourceId: s.id, progress: 0, state: 'running', startedAt: now }))
    setJobs(initial)
    if (initial.length) track('knowledge.train', { count: initial.length })
  }, [])

  React.useEffect(() => {
    if (jobs.length === 0) return
    const timer = setInterval(() => {
      setJobs((prev) => prev.map((j) => {
        if (j.state !== 'running') return j
        const next = Math.min(100, j.progress + Math.floor(5 + Math.random() * 15))
        if (next >= 100) {
          const fail = errorMode && Math.random() > 0.7
          return { ...j, progress: 100, state: fail ? 'failed' : 'completed', finishedAt: Date.now(), message: fail ? 'Simulated failure' : 'Trained' }
        }
        return { ...j, progress: next }
      }))
    }, 300)
    return () => clearInterval(timer)
  }, [jobs.length, errorMode])

  const applyUpdatesAndClose = () => {
    const updates: UpdatesMap = {}
    jobs.forEach((j) => {
      if (j.state === 'completed') updates[j.sourceId] = { status: 'trained', lastTrainedTs: j.finishedAt }
      if (j.state === 'failed') updates[j.sourceId] = { status: 'error' }
    })
    onApply && onApply(updates)
    navigation.goBack()
  }

  const allDone = jobs.length > 0 && jobs.every((j) => j.state === 'completed' || j.state === 'failed')

  return (
    <SafeAreaView style={{ flex: 1, backgroundColor: theme.color.background }}>
      {/* Header */}
      <View style={{ paddingHorizontal: 16, paddingTop: insets.top + 8, paddingBottom: 12, borderBottomWidth: 1, borderBottomColor: theme.color.border }}>
        <View style={{ flexDirection: 'row', alignItems: 'center', justifyContent: 'space-between' }}>
          <TouchableOpacity onPress={() => navigation.goBack()} accessibilityLabel="Back" accessibilityRole="button" style={{ padding: 8 }}>
            <Text style={{ color: theme.color.primary, fontWeight: '600' }}>{'< Back'}</Text>
          </TouchableOpacity>
          <Text style={{ color: theme.color.foreground, fontSize: 18, fontWeight: '700' }}>Training Center</Text>
          <View style={{ width: 64 }} />
        </View>
      </View>

      {/* Controls */}
      <View style={{ padding: 16, borderBottomWidth: 1, borderBottomColor: theme.color.border }}>
        <View style={{ flexDirection: 'row', alignItems: 'center', justifyContent: 'space-between' }}>
          <Text style={{ color: theme.color.mutedForeground }}>Inject error (dev)</Text>
          <Switch value={errorMode} onValueChange={setErrorMode} accessibilityLabel="Inject error" />
        </View>
      </View>

      {/* Jobs */}
      <FlatList
        style={{ padding: 16 }}
        data={jobs}
        keyExtractor={(j) => j.id}
        renderItem={({ item }) => (
          <View style={{ padding: 12, borderWidth: 1, borderColor: theme.color.border, borderRadius: 12, marginBottom: 10 }}>
            <Text style={{ color: theme.color.cardForeground, fontWeight: '600', marginBottom: 6 }}>Source: {item.sourceId}</Text>
            <TrainProgressBar job={item} />
            {item.state === 'failed' && (
              <Text style={{ color: theme.color.error, marginTop: 6 }}>{item.message}</Text>
            )}
          </View>
        )}
      />

      {/* Footer */}
      <View style={{ padding: 16 }}>
        <TouchableOpacity onPress={applyUpdatesAndClose} disabled={!allDone} accessibilityLabel="Done" accessibilityRole="button" style={{ alignSelf: 'flex-end', paddingHorizontal: 12, paddingVertical: 10, borderWidth: 1, borderColor: theme.color.border, borderRadius: 12, opacity: allDone ? 1 : 0.5 }}>
          <Text style={{ color: theme.color.primary, fontWeight: '700' }}>Done</Text>
        </TouchableOpacity>
      </View>
    </SafeAreaView>
  )
}

export default TrainingCenter


