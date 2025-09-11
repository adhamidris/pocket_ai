import React from 'react'
import { SafeAreaView, View, Text, TouchableOpacity, ScrollView, Modal, TextInput } from 'react-native'
import { useSafeAreaInsets } from 'react-native-safe-area-context'
import { useTheme } from '../../providers/ThemeProvider'
import { useNavigation } from '@react-navigation/native'
import { ExportJob, AuditEvent } from '../../types/security'
import ExportJobRow from '../../components/security/ExportJobRow'
import { track } from '../../lib/analytics'

const now = () => Date.now()

const ExportsCenter: React.FC = () => {
  const { theme } = useTheme()
  const insets = useSafeAreaInsets()
  const navigation = useNavigation<any>()

  const [jobs, setJobs] = React.useState<ExportJob[]>([])
  const [editorOpen, setEditorOpen] = React.useState(false)
  const [scope, setScope] = React.useState<ExportJob['scope']>('all')
  const [contactId, setContactId] = React.useState('')
  const [conversationId, setConversationId] = React.useState('')
  const [dateStart, setDateStart] = React.useState('')
  const [dateEnd, setDateEnd] = React.useState('')

  const Chip: React.FC<{ v: ExportJob['scope']; active: boolean; onPress: () => void }>=({ v, active, onPress }) => (
    <TouchableOpacity onPress={onPress} accessibilityRole="button" accessibilityLabel={`Scope ${v}`} style={{ paddingHorizontal: 10, paddingVertical: 6, borderRadius: 10, borderWidth: 1, borderColor: active ? theme.color.primary : theme.color.border }}>
      <Text style={{ color: active ? theme.color.primary : theme.color.mutedForeground, fontWeight: '600' }}>{v}</Text>
    </TouchableOpacity>
  )

  const appendAudit = (evt: AuditEvent) => {
    // In this UI-only demo, we log to console.
    // eslint-disable-next-line no-console
    console.log('audit', evt)
  }

  const createJob = () => {
    const id = `ex-${now()}`
    const params: Record<string, any> = {}
    if (scope === 'contact') params.contactId = contactId.trim()
    if (scope === 'conversation') params.conversationId = conversationId.trim()
    if (scope === 'dateRange') params.start = dateStart, params.end = dateEnd
    const job: ExportJob = { id, scope, params, state: 'queued', progress: 0, createdAt: now() }
    setJobs((arr) => [job, ...arr])
    setEditorOpen(false)
    appendAudit({ id: `a-${now()}`, ts: now(), actor: 'me', action: 'export.create', entityType: 'export', entityId: id, details: `${scope}`, risk: 'low' })
    try { track('export.create') } catch {}
    // Simulate progress
    setTimeout(() => {
      setJobs((arr) => arr.map((j) => j.id === id ? { ...j, state: 'running' } : j))
      let p = 0
      const t = setInterval(() => {
        p += Math.random() * 25 + 10
        setJobs((arr) => arr.map((j) => j.id === id ? { ...j, progress: Math.min(100, p) } : j))
        if (p >= 100) {
          clearInterval(t)
          const succeed = Math.random() > 0.1
          setJobs((arr) => arr.map((j) => j.id === id ? ({ ...j, state: succeed ? 'completed' : 'failed', finishedAt: now(), downloadUrl: succeed ? 'https://example.com/export.zip' : undefined }) : j))
          appendAudit({ id: `a-${now()}`, ts: now(), actor: 'system', action: succeed ? 'export.completed' : 'export.failed', entityType: 'export', entityId: id, risk: succeed ? 'low' : 'medium' })
          if (succeed) { try { track('export.complete') } catch {} }
        }
      }, 500)
    }, 400)
  }

  return (
    <SafeAreaView style={{ flex: 1, backgroundColor: theme.color.background }}>
      <ScrollView contentContainerStyle={{ paddingBottom: 24 }}>
        {/* Header */}
        <View style={{ paddingHorizontal: 24, paddingTop: insets.top + 12, paddingBottom: 12 }}>
          <View style={{ flexDirection: 'row', alignItems: 'center', justifyContent: 'space-between' }}>
            <TouchableOpacity onPress={() => navigation.goBack()} accessibilityRole="button" accessibilityLabel="Back" style={{ paddingHorizontal: 12, paddingVertical: 8, borderRadius: 10, borderWidth: 1, borderColor: theme.color.border }}>
              <Text style={{ color: theme.color.primary, fontWeight: '600' }}>{'< Back'}</Text>
            </TouchableOpacity>
            <Text style={{ color: theme.color.foreground, fontSize: 20, fontWeight: '700' }}>Exports (DSAR)</Text>
            <TouchableOpacity onPress={() => setEditorOpen(true)} accessibilityRole="button" accessibilityLabel="New Export" style={{ paddingHorizontal: 12, paddingVertical: 8, borderRadius: 10, borderWidth: 1, borderColor: theme.color.border }}>
              <Text style={{ color: theme.color.cardForeground, fontWeight: '600' }}>New Export</Text>
            </TouchableOpacity>
          </View>
        </View>

        {/* Jobs */}
        <View style={{ paddingHorizontal: 24 }}>
          {jobs.map((j) => (
            <View key={j.id} style={{ marginBottom: 8 }}>
              <ExportJobRow job={j} onOpen={() => { /* stub open */ }} />
              {/* Progress bar */}
              <View style={{ height: 6, borderRadius: 4, backgroundColor: theme.color.muted }}>
                <View style={{ width: `${j.progress}%`, height: '100%', backgroundColor: theme.color.primary, borderRadius: 4 }} />
              </View>
            </View>
          ))}
        </View>
      </ScrollView>

      {/* New Export Modal */}
      <Modal visible={editorOpen} transparent animationType="slide" onRequestClose={() => setEditorOpen(false)}>
        <View style={{ flex: 1, backgroundColor: 'rgba(0,0,0,0.4)', justifyContent: 'flex-end' }}>
          <View style={{ backgroundColor: theme.color.card, borderTopLeftRadius: 16, borderTopRightRadius: 16, padding: 16, borderWidth: 1, borderColor: theme.color.border }}>
            <Text style={{ color: theme.color.cardForeground, fontWeight: '700', marginBottom: 8 }}>Create Export</Text>
            <View style={{ flexDirection: 'row', flexWrap: 'wrap', gap: 8, marginBottom: 8 }}>
              {(['all','contact','conversation','dateRange'] as const).map((s) => (
                <Chip key={s} v={s} active={scope === s} onPress={() => setScope(s)} />
              ))}
            </View>
            {scope === 'contact' && (
              <TextInput value={contactId} onChangeText={setContactId} placeholder="Contact ID" placeholderTextColor={theme.color.placeholder} accessibilityLabel="Contact ID" style={{ borderWidth: 1, borderColor: theme.color.border, borderRadius: 10, paddingHorizontal: 10, paddingVertical: 8, color: theme.color.cardForeground, marginBottom: 8 }} />
            )}
            {scope === 'conversation' && (
              <TextInput value={conversationId} onChangeText={setConversationId} placeholder="Conversation ID" placeholderTextColor={theme.color.placeholder} accessibilityLabel="Conversation ID" style={{ borderWidth: 1, borderColor: theme.color.border, borderRadius: 10, paddingHorizontal: 10, paddingVertical: 8, color: theme.color.cardForeground, marginBottom: 8 }} />
            )}
            {scope === 'dateRange' && (
              <View style={{ gap: 8 }}>
                <TextInput value={dateStart} onChangeText={setDateStart} placeholder="Start ISO" placeholderTextColor={theme.color.placeholder} accessibilityLabel="Start date" style={{ borderWidth: 1, borderColor: theme.color.border, borderRadius: 10, paddingHorizontal: 10, paddingVertical: 8, color: theme.color.cardForeground }} />
                <TextInput value={dateEnd} onChangeText={setDateEnd} placeholder="End ISO" placeholderTextColor={theme.color.placeholder} accessibilityLabel="End date" style={{ borderWidth: 1, borderColor: theme.color.border, borderRadius: 10, paddingHorizontal: 10, paddingVertical: 8, color: theme.color.cardForeground }} />
              </View>
            )}
            <View style={{ flexDirection: 'row', justifyContent: 'flex-end', gap: 8, marginTop: 12 }}>
              <TouchableOpacity onPress={() => setEditorOpen(false)} accessibilityRole="button" accessibilityLabel="Cancel" style={{ paddingHorizontal: 12, paddingVertical: 8, borderRadius: 10, borderWidth: 1, borderColor: theme.color.border }}>
                <Text style={{ color: theme.color.cardForeground }}>Cancel</Text>
              </TouchableOpacity>
              <TouchableOpacity onPress={createJob} accessibilityRole="button" accessibilityLabel="Create export" style={{ paddingHorizontal: 12, paddingVertical: 8, borderRadius: 10, backgroundColor: theme.color.primary }}>
                <Text style={{ color: theme.color.primaryForeground, fontWeight: '700' }}>Create</Text>
              </TouchableOpacity>
            </View>
          </View>
        </View>
      </Modal>
    </SafeAreaView>
  )
}

export default ExportsCenter


