import React from 'react'
import { SafeAreaView, View, Text, TouchableOpacity, FlatList, Modal, TextInput, Alert } from 'react-native'
import { useSafeAreaInsets } from 'react-native-safe-area-context'
import { useNavigation, useRoute } from '@react-navigation/native'
import { useTheme } from '../../providers/ThemeProvider'
import { SavedReport, MetricKey, TimeRange } from '../../types/analytics'
import ExportBar from '../../components/analytics/ExportBar'
import EntitlementsGate from '../../components/billing/EntitlementsGate'

type Schedule = { cron?: 'daily' | 'weekly' | 'monthly'; recipients?: string[] }

const SavedReports: React.FC = () => {
  const { theme } = useTheme()
  const insets = useSafeAreaInsets()
  const navigation = useNavigation<any>()
  const route = useRoute<any>()

  const initial: SavedReport[] = []

  const [reports, setReports] = React.useState<SavedReport[]>(initial)
  const [scheduleOpen, setScheduleOpen] = React.useState<null | string>(null)
  const [cron, setCron] = React.useState<'daily' | 'weekly' | 'monthly' | undefined>('weekly')
  const [emails, setEmails] = React.useState<string>('')

  const currentMetrics: MetricKey[] = route.params?.metrics || ['volume', 'frtP50', 'resolutionRate']
  const currentDims: string[] = route.params?.dims || ['time']
  const currentFilters: Record<string, any> = route.params?.filters || {}
  const currentRange: TimeRange | undefined = route.params?.range

  const saveCurrent = () => {
    const name = `Report ${new Date().toLocaleString()}`
    const item: SavedReport = {
      id: `rep-${Date.now()}`,
      name,
      metrics: currentMetrics,
      dims: currentDims as any,
      range: currentRange || { startIso: new Date(Date.now() - 7*86400000).toISOString(), endIso: new Date().toISOString(), grain: 'day' },
      filters: currentFilters,
      createdAt: Date.now(),
    }
    setReports((arr) => [item, ...arr])
    try { (require('../../lib/analytics') as any).track('analytics.report_save', { id: item.id }) } catch {}
    Alert.alert('Saved', 'Current view saved as a report (UI-only).')
  }

  const applySchedule = () => {
    setReports((arr) => arr.map((r) => r.id === scheduleOpen ? ({ ...r, schedule: { cron, recipients: emails.split(',').map((e) => e.trim()).filter(Boolean) } }) : r))
    try { (require('../../lib/analytics') as any).track('analytics.schedule', { id: scheduleOpen }) } catch {}
    setScheduleOpen(null)
    Alert.alert('Scheduled', 'Report will be emailed on schedule (stub).')
  }

  return (
    <SafeAreaView style={{ flex: 1, backgroundColor: theme.color.background }}>
      {/* Header */}
      <View style={{ paddingHorizontal: 16, paddingTop: insets.top + 8, paddingBottom: 12, borderBottomWidth: 1, borderBottomColor: theme.color.border }}>
        <View style={{ flexDirection: 'row', alignItems: 'center', justifyContent: 'space-between' }}>
          <TouchableOpacity onPress={() => navigation.goBack()} accessibilityLabel="Back" accessibilityRole="button" style={{ padding: 8 }}>
            <Text style={{ color: theme.color.primary, fontWeight: '600' }}>{'< Back'}</Text>
          </TouchableOpacity>
          <Text style={{ color: theme.color.foreground, fontSize: 18, fontWeight: '700' }}>Saved Reports</Text>
          <View style={{ width: 64 }} />
        </View>
      </View>

      {/* Actions */}
      <View style={{ padding: 16, gap: 12 }}>
        <EntitlementsGate require="analyticsDepth">
          <TouchableOpacity onPress={saveCurrent} accessibilityRole="button" accessibilityLabel="Save Current" style={{ alignSelf: 'flex-start', paddingHorizontal: 12, paddingVertical: 8, borderRadius: 12, borderWidth: 1, borderColor: theme.color.border }}>
            <Text style={{ color: theme.color.cardForeground, fontWeight: '600' }}>Save Current</Text>
          </TouchableOpacity>
          <ExportBar testID="an-saved-export" />
        </EntitlementsGate>
      </View>

        {/* List */}
      <EntitlementsGate require="analyticsDepth">
      <FlatList
        style={{ paddingHorizontal: 16 }}
        data={reports}
        keyExtractor={(r) => r.id}
        ListEmptyComponent={() => (
          <View style={{ padding: 16 }}>
            <Text style={{ color: theme.color.mutedForeground }}>No saved reports yet.</Text>
          </View>
        )}
        renderItem={({ item }) => (
          <View style={{ borderWidth: 1, borderColor: theme.color.border, borderRadius: 12, padding: 12, marginBottom: 12 }}>
            <View style={{ flexDirection: 'row', alignItems: 'center', justifyContent: 'space-between' }}>
              <Text style={{ color: theme.color.cardForeground, fontWeight: '700' }}>{item.name}</Text>
              <TouchableOpacity onPress={() => setScheduleOpen(item.id)} accessibilityRole="button" accessibilityLabel="Schedule" style={{ paddingHorizontal: 10, paddingVertical: 6, borderRadius: 8, borderWidth: 1, borderColor: theme.color.border }}>
                <Text style={{ color: theme.color.cardForeground, fontWeight: '600' }}>{item.schedule?.cron || 'Schedule…'}</Text>
              </TouchableOpacity>
            </View>
            <Text style={{ color: theme.color.mutedForeground, marginTop: 6, fontSize: 12 }}>Metrics: {item.metrics.join(', ')} | Dims: {item.dims.join(', ')} | Filters: {Object.keys(item.filters || {}).length || 0}</Text>
          </View>
        )}
      />
      </EntitlementsGate>

      {/* Schedule Modal */}
      <Modal visible={!!scheduleOpen} transparent animationType="slide" onRequestClose={() => setScheduleOpen(null)}>
        <View style={{ flex: 1, backgroundColor: 'rgba(0,0,0,0.4)', justifyContent: 'flex-end' }}>
          <View style={{ backgroundColor: theme.color.card, borderTopLeftRadius: 16, borderTopRightRadius: 16, padding: 16, borderWidth: 1, borderColor: theme.color.border }}>
            <Text style={{ color: theme.color.cardForeground, fontWeight: '700', marginBottom: 8 }}>Schedule</Text>
            <View style={{ flexDirection: 'row', gap: 8, marginBottom: 8 }}>
              {(['daily', 'weekly', 'monthly'] as const).map((c) => (
                <TouchableOpacity key={c} onPress={() => setCron(c)} style={{ paddingHorizontal: 10, paddingVertical: 6, borderRadius: 10, borderWidth: 1, borderColor: cron === c ? theme.color.primary : theme.color.border }}>
                  <Text style={{ color: cron === c ? theme.color.primary : theme.color.mutedForeground, fontWeight: '600' }}>{c}</Text>
                </TouchableOpacity>
              ))}
            </View>
            <View style={{ borderWidth: 1, borderColor: theme.color.border, borderRadius: 12, backgroundColor: theme.color.card }}>
              <TextInput value={emails} onChangeText={setEmails} placeholder="Recipients (comma-separated emails)" placeholderTextColor={theme.color.placeholder} style={{ paddingHorizontal: 12, paddingVertical: 10, color: theme.color.cardForeground }} />
            </View>
            <View style={{ flexDirection: 'row', justifyContent: 'flex-end', gap: 8, marginTop: 12 }}>
              <TouchableOpacity onPress={() => setScheduleOpen(null)} style={{ paddingHorizontal: 12, paddingVertical: 8 }}>
                <Text style={{ color: theme.color.mutedForeground }}>Cancel</Text>
              </TouchableOpacity>
              <TouchableOpacity onPress={applySchedule} style={{ paddingHorizontal: 12, paddingVertical: 8, borderRadius: 10, backgroundColor: theme.color.primary }}>
                <Text style={{ color: theme.color.primaryForeground, fontWeight: '700' }}>Save</Text>
              </TouchableOpacity>
            </View>
          </View>
        </View>
      </Modal>
    </SafeAreaView>
  )
}

export default SavedReports


