import React from 'react'
import { SafeAreaView, View, Text, TouchableOpacity, FlatList, RefreshControl, Alert, SectionList, SectionListData } from 'react-native'
import { useSafeAreaInsets } from 'react-native-safe-area-context'
import { useTheme } from '../../providers/ThemeProvider'
import { useNavigation, useRoute } from '@react-navigation/native'
import { CoverageStat, DriftWarning, KnowledgeSource, TrainingJob } from '../../types/knowledge'
import CoverageTile from '../../components/knowledge/CoverageTile'
import SourceCard from '../../components/knowledge/SourceCard'
import ListSkeleton from '../../components/knowledge/ListSkeleton'
import OfflineBanner from '../../components/dashboard/OfflineBanner'
import SyncCenterSheet from '../../components/dashboard/SyncCenterSheet'
import { track } from '../../lib/analytics'
import { EmptyStateGuide } from '../../components/help/EmptyStateGuide'

const rand = (min: number, max: number) => Math.floor(Math.random() * (max - min + 1)) + min

const genCoverage = (): CoverageStat => ({ topics: rand(20, 80), faqs: rand(50, 200), gaps: rand(1, 12), coveragePct: rand(60, 95) })
const genSources = (): KnowledgeSource[] => {
  const now = Date.now()
  return [
    { id: 'u-1', kind: 'upload', title: 'Onboarding Guide.pdf', enabled: true, scope: 'global', status: 'trained', lastTrainedTs: now - 86400000, filename: 'Onboarding.pdf', mime: 'application/pdf', sizeKB: 320 },
    { id: 'u-2', kind: 'url', title: 'Help Center', enabled: true, scope: 'global', status: 'training', url: 'https://example.com/help', crawlDepth: 1, respectRobots: true },
    { id: 'u-3', kind: 'note', title: 'Holiday Policy', enabled: true, scope: 'agent:demo', status: 'idle', content: 'We are closed on national holidays.' },
  ]
}
const genDrift = (): DriftWarning[] => ([
  { sourceId: 'u-2', kind: 'stale_url', detectedAt: Date.now() - 3600000, detail: 'Content changed 12 days ago' },
])

const KnowledgeHome: React.FC = () => {
  const { theme } = useTheme()
  const insets = useSafeAreaInsets()
  const navigation = useNavigation<any>()
  const route = useRoute<any>()

  const [loading, setLoading] = React.useState(false)
  const [refreshing, setRefreshing] = React.useState(false)
  const [coverage, setCoverage] = React.useState<CoverageStat>(() => genCoverage())
  const [sources, setSources] = React.useState<KnowledgeSource[]>(() => genSources())
  const [drift, setDrift] = React.useState<DriftWarning[]>(() => genDrift())
  const [offline, setOffline] = React.useState(false)
  const [queued, setQueued] = React.useState<Record<string, boolean>>({})
  const [syncOpen, setSyncOpen] = React.useState(false)

  React.useEffect(() => {
    setLoading(true)
    const t = setTimeout(() => setLoading(false), 300)
    track('knowledge.view')
    return () => clearTimeout(t)
  }, [])

  const onRefresh = () => {
    setRefreshing(true)
    setTimeout(() => {
      setCoverage(genCoverage())
      setSources(genSources())
      setDrift(genDrift())
      setRefreshing(false)
    }, 600)
  }

  const filterTitles: string[] | undefined = route.params?.filterTitles
  const filteredSources = React.useMemo(() => {
    if (!filterTitles || filterTitles.length === 0) return sources
    const lower = filterTitles.map((t: string) => t.toLowerCase())
    return sources.filter((s) => lower.some((t) => s.title.toLowerCase().includes(t)))
  }, [sources, route.params])
  const sections = React.useMemo(() => ([
    { title: 'UPLOAD', data: filteredSources.filter((s) => s.kind === 'upload') },
    { title: 'URL', data: filteredSources.filter((s) => s.kind === 'url') },
    { title: 'NOTE', data: filteredSources.filter((s) => s.kind === 'note') },
  ]), [filteredSources])

  const hasAny = sections.some(s => s.data.length > 0)

  return (
    <SafeAreaView style={{ flex: 1, backgroundColor: theme.color.background }}>
      <View style={{ paddingHorizontal: 24, paddingTop: insets.top + 12, paddingBottom: 12 }}>
        <View style={{ flexDirection: 'row', alignItems: 'center', justifyContent: 'space-between', marginBottom: 12 }}>
          <Text style={{ color: theme.color.foreground, fontSize: 32, fontWeight: '700' }}>Knowledge</Text>
          <View style={{ flexDirection: 'row', gap: 8 }}>
            <TouchableOpacity onPress={() => navigation.navigate('CRM', { screen: 'PrivacyCenter' })} accessibilityLabel="Privacy Center" accessibilityRole="button" hitSlop={{ top: 8, bottom: 8, left: 8, right: 8 }} style={{ paddingHorizontal: 12, paddingVertical: 8, borderWidth: 1, borderColor: theme.color.border, borderRadius: 12 }}>
              <Text style={{ color: theme.color.cardForeground, fontWeight: '600' }}>Privacy Center</Text>
            </TouchableOpacity>
            <TouchableOpacity onPress={() => navigation.navigate('Knowledge', { screen: 'TestHarness' })} accessibilityLabel="Test Harness" accessibilityRole="button" hitSlop={{ top: 8, bottom: 8, left: 8, right: 8 }} style={{ paddingHorizontal: 12, paddingVertical: 8, borderWidth: 1, borderColor: theme.color.border, borderRadius: 12 }}>
              <Text style={{ color: theme.color.cardForeground, fontWeight: '600' }}>Test Harness</Text>
            </TouchableOpacity>
            <TouchableOpacity onPress={() => navigation.navigate('Dashboard')} accessibilityLabel="Open Dashboard Q&A" accessibilityRole="button" hitSlop={{ top: 8, bottom: 8, left: 8, right: 8 }} style={{ paddingHorizontal: 12, paddingVertical: 8, borderWidth: 1, borderColor: theme.color.border, borderRadius: 12 }}>
              <Text style={{ color: theme.color.cardForeground, fontWeight: '600' }}>Dashboard Q&A</Text>
            </TouchableOpacity>
            <TouchableOpacity onPress={() => navigation.navigate('Knowledge', { screen: 'SourcePriority', params: { sources, onApply: (arr: any[]) => setSources(arr) } })} accessibilityLabel="Source Priority" accessibilityRole="button" hitSlop={{ top: 8, bottom: 8, left: 8, right: 8 }} style={{ paddingHorizontal: 12, paddingVertical: 8, borderWidth: 1, borderColor: theme.color.border, borderRadius: 12 }}>
              <Text style={{ color: theme.color.cardForeground, fontWeight: '600' }}>Source Priority</Text>
            </TouchableOpacity>
            <TouchableOpacity onPress={() => navigation.navigate('Knowledge', { screen: 'RedactionRules' })} accessibilityLabel="Redaction Rules" accessibilityRole="button" hitSlop={{ top: 8, bottom: 8, left: 8, right: 8 }} style={{ paddingHorizontal: 12, paddingVertical: 8, borderWidth: 1, borderColor: theme.color.border, borderRadius: 12 }}>
              <Text style={{ color: theme.color.cardForeground, fontWeight: '600' }}>Redaction</Text>
            </TouchableOpacity>
            <TouchableOpacity onPress={() => navigation.navigate('Knowledge', { screen: 'Versions', params: { sources } })} accessibilityLabel="Versions" accessibilityRole="button" hitSlop={{ top: 8, bottom: 8, left: 8, right: 8 }} style={{ paddingHorizontal: 12, paddingVertical: 8, borderWidth: 1, borderColor: theme.color.border, borderRadius: 12 }}>
              <Text style={{ color: theme.color.cardForeground, fontWeight: '600' }}>Versions</Text>
            </TouchableOpacity>
            <TouchableOpacity onPress={() => setOffline((v) => !v)} accessibilityLabel="Toggle Offline" accessibilityRole="button" hitSlop={{ top: 8, bottom: 8, left: 8, right: 8 }} style={{ paddingHorizontal: 12, paddingVertical: 8, borderWidth: 1, borderColor: theme.color.border, borderRadius: 12 }}>
              <Text style={{ color: theme.color.cardForeground, fontWeight: '600' }}>{offline ? 'Go Online' : 'Go Offline'}</Text>
            </TouchableOpacity>
            <TouchableOpacity onPress={() => setSyncOpen(true)} accessibilityLabel="Sync Center" accessibilityRole="button" hitSlop={{ top: 8, bottom: 8, left: 8, right: 8 }} style={{ paddingHorizontal: 12, paddingVertical: 8, borderWidth: 1, borderColor: theme.color.border, borderRadius: 12 }}>
              <Text style={{ color: theme.color.mutedForeground, fontWeight: '600' }}>Sync</Text>
            </TouchableOpacity>
          </View>
        </View>

        {/* Offline banner */}
        {offline && (
          <View style={{ marginBottom: 12 }}>
            <OfflineBanner visible testID="kn-offline" />
          </View>
        )}

        {/* Coverage */}
        <TouchableOpacity onPress={() => navigation.navigate('Knowledge', { screen: 'CoverageHealth', params: { coverage, drift } })} accessibilityLabel="Coverage & Health" accessibilityRole="button" style={{ flexDirection: 'row', alignItems: 'center', gap: 12, marginBottom: 16 }}>
          <View style={{ flex: 1 }}>
            <CoverageTile stat={coverage} testID="kn-coverage" />
          </View>
          <View style={{ paddingHorizontal: 8, paddingVertical: 6, borderRadius: 8, borderWidth: 1, borderColor: theme.color.border }}>
            <Text style={{ color: theme.color.mutedForeground }}>Δ +{rand(1, 4)}%</Text>
          </View>
        </TouchableOpacity>

        {/* Actions */}
        <View style={{ flexDirection: 'row', flexWrap: 'wrap', gap: 8, marginBottom: 12 }}>
          <TouchableOpacity onPress={() => navigation.navigate('Knowledge', { screen: 'AddUrlSource', params: { onSave: (src: any) => setSources((arr) => [src, ...arr]) } })} accessibilityLabel="Add URL" accessibilityRole="button" style={{ paddingHorizontal: 12, paddingVertical: 8, borderRadius: 12, borderWidth: 1, borderColor: theme.color.border }}>
            <Text style={{ color: theme.color.cardForeground, fontWeight: '600' }}>Add URL</Text>
          </TouchableOpacity>
          <TouchableOpacity onPress={() => navigation.navigate('Knowledge', { screen: 'AddUploadSource', params: { onSave: (src: any) => setSources((arr) => [src, ...arr]) } })} accessibilityLabel="Add Upload" accessibilityRole="button" style={{ paddingHorizontal: 12, paddingVertical: 8, borderRadius: 12, borderWidth: 1, borderColor: theme.color.border }}>
            <Text style={{ color: theme.color.cardForeground, fontWeight: '600' }}>Add Upload</Text>
          </TouchableOpacity>
          <TouchableOpacity onPress={() => navigation.navigate('Knowledge', { screen: 'NoteEditor', params: { onSave: (note: any) => setSources((arr) => [note, ...arr]) } })} accessibilityLabel="New Note" accessibilityRole="button" style={{ paddingHorizontal: 12, paddingVertical: 8, borderRadius: 12, borderWidth: 1, borderColor: theme.color.border }}>
            <Text style={{ color: theme.color.cardForeground, fontWeight: '600' }}>New Note</Text>
          </TouchableOpacity>
          <TouchableOpacity onPress={() => navigation.navigate('Knowledge', { screen: 'TrainingCenter', params: { sources } })} accessibilityLabel="Train Now" accessibilityRole="button" style={{ paddingHorizontal: 12, paddingVertical: 8, borderRadius: 12, borderWidth: 1, borderColor: theme.color.border }}>
            <Text style={{ color: theme.color.primary, fontWeight: '700' }}>Train Now</Text>
          </TouchableOpacity>
          <TouchableOpacity onPress={() => navigation.navigate('Knowledge', { screen: 'FailureLog', params: { sources } })} accessibilityLabel="Failures" accessibilityRole="button" style={{ paddingHorizontal: 12, paddingVertical: 8, borderRadius: 12, borderWidth: 1, borderColor: theme.color.border }}>
            <Text style={{ color: theme.color.cardForeground, fontWeight: '600' }}>Failures</Text>
          </TouchableOpacity>
        </View>
      </View>

      {/* Sources grouped */}
      {hasAny ? (
      <SectionList
        sections={sections}
        keyExtractor={(item) => item.id}
        renderSectionHeader={({ section }) => (
          <View style={{ paddingHorizontal: 24, marginTop: 12 }}>
            <Text style={{ color: theme.color.mutedForeground, fontWeight: '700', marginBottom: 6 }}>{section.title}</Text>
          </View>
        )}
        renderItem={({ item }) => (
          <View style={{ paddingHorizontal: 24, marginBottom: 8 }}>
            <SourceCard
              src={item}
              queued={queued[item.id]}
              onPress={(id) => {
                const found = sources.find((x) => x.id === id)
                if (found?.kind === 'note') {
                  // @ts-ignore
                  navigation.navigate('Knowledge', { screen: 'NoteEditor', params: { note: found, onSave: (note: any) => setSources((arr) => arr.map((x) => x.id === note.id ? note : x)) } })
                } else {
                  Alert.alert('Source', id)
                }
              }}
              testID={`kn-source-row-${item.id}`}
            />
          </View>
        )}
        ListHeaderComponent={
          <View style={{ paddingHorizontal: 24 }}>
            {/* Drift */}
            {drift.length > 0 && (
              <View style={{ marginBottom: 16 }}>
                <Text style={{ color: theme.color.cardForeground, fontWeight: '700', marginBottom: 6 }}>Drift & Warnings</Text>
                {drift.map((d, idx) => (
                  <View key={idx} style={{ padding: 12, borderWidth: 1, borderColor: theme.color.border, borderRadius: 12, marginBottom: 8 }}>
                    <Text style={{ color: theme.color.cardForeground, fontWeight: '600' }}>{d.kind.replaceAll('_', ' ').toUpperCase()}</Text>
                    <Text style={{ color: theme.color.mutedForeground }}>{d.detail || ''}</Text>
                  </View>
                ))}
              </View>
            )}
          </View>
        }
        refreshControl={<RefreshControl refreshing={refreshing} onRefresh={onRefresh} />}
        ListEmptyComponent={loading ? <ListSkeleton rows={8} testID="kn-skeleton" /> : null}
        initialNumToRender={12}
        windowSize={12}
        stickySectionHeadersEnabled
      />
      ) : (
        <View style={{ paddingHorizontal: 24, marginTop: 24 }}>
          <EmptyStateGuide
            title="Add your first source"
            lines={["Connect URLs, upload files, or write notes."]}
            cta={{ label: 'Add Source', onPress: () => navigation.navigate('Knowledge', { screen: 'AddUrlSource' }) }}
          />
        </View>
      )}

      <SyncCenterSheet
        visible={syncOpen}
        onClose={() => setSyncOpen(false)}
        lastSyncAt={new Date().toLocaleString()}
        queuedCount={Object.values(queued).filter(Boolean).length}
        onRetryAll={() => setSyncOpen(false)}
      />
    </SafeAreaView>
  )
}

export default KnowledgeHome


