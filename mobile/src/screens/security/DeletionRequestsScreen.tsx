import React from 'react'
import { SafeAreaView, View, Text, TouchableOpacity, ScrollView, Alert } from 'react-native'
import { useSafeAreaInsets } from 'react-native-safe-area-context'
import { useTheme } from '../../providers/ThemeProvider'
import { useNavigation } from '@react-navigation/native'
import { DeletionRequest, AuditEvent } from '../../types/security'
import DeletionRow from '../../components/security/DeletionRow'
import { track } from '../../lib/analytics'

const now = () => Date.now()

const gen = (): DeletionRequest[] => (
  [
    { id: 'd-1', subject: 'contact', refId: 'c-100', status: 'pending', submittedAt: now() - 3600_000 },
    { id: 'd-2', subject: 'conversation', refId: 'cv-42', status: 'pending', submittedAt: now() - 7200_000 },
    { id: 'd-3', subject: 'account', status: 'completed', submittedAt: now() - 86400_000, dueAt: now() - 86000_000 },
  ]
)

const Chip: React.FC<{ label: string; active?: boolean; onPress: () => void }>=({ label, active, onPress }) => {
  const { theme } = useTheme()
  return (
    <TouchableOpacity onPress={onPress} accessibilityRole="button" accessibilityLabel={label} style={{ paddingHorizontal: 10, paddingVertical: 6, borderRadius: 10, borderWidth: 1, borderColor: active ? theme.color.primary : theme.color.border }}>
      <Text style={{ color: active ? theme.color.primary : theme.color.mutedForeground, fontWeight: '600' }}>{label}</Text>
    </TouchableOpacity>
  )
}

const DeletionRequestsScreen: React.FC = () => {
  const { theme } = useTheme()
  const insets = useSafeAreaInsets()
  const navigation = useNavigation<any>()

  const [items, setItems] = React.useState<DeletionRequest[]>(() => gen())
  const [status, setStatus] = React.useState<DeletionRequest['status'] | undefined>('pending')
  const [subject, setSubject] = React.useState<DeletionRequest['subject'] | undefined>()
  const [timeWin, setTimeWin] = React.useState<'24h'|'7d'|'30d'|undefined>('7d')

  const appendAudit = (evt: AuditEvent) => { console.log('audit', evt) }

  const approve = (id: string) => {
    setItems((arr) => arr.map((r) => r.id === id ? { ...r, status: 'approved' } : r))
    appendAudit({ id: `a-${now()}`, ts: now(), actor: 'me', action: 'deletion.approve', entityType: 'deletion', entityId: id, risk: 'low' })
    try { track('deletion.status', { status: 'approved' }) } catch {}
    setTimeout(() => {
      setItems((arr) => arr.map((r) => r.id === id ? { ...r, status: 'processing' } : r))
      appendAudit({ id: `a-${now()}`, ts: now(), actor: 'system', action: 'deletion.processing', entityType: 'deletion', entityId: id, risk: 'low' })
      try { track('deletion.status', { status: 'processing' }) } catch {}
      setTimeout(() => {
        setItems((arr) => arr.map((r) => r.id === id ? { ...r, status: 'completed' } : r))
        appendAudit({ id: `a-${now()}`, ts: now(), actor: 'system', action: 'deletion.completed', entityType: 'deletion', entityId: id, risk: 'low' })
        try { track('deletion.status', { status: 'completed' }) } catch {}
      }, 1200)
    }, 800)
  }

  const reject = (id: string) => {
    Alert.alert('Reject Request', 'Provide a brief reason (UI-only).
Reason: policy not applicable', [
      { text: 'OK', onPress: () => {
        setItems((arr) => arr.map((r) => r.id === id ? { ...r, status: 'rejected', reason: 'policy not applicable' } : r))
        appendAudit({ id: `a-${now()}`, ts: now(), actor: 'me', action: 'deletion.rejected', entityType: 'deletion', entityId: id, risk: 'low' })
        try { track('deletion.status', { status: 'rejected' }) } catch {}
      }}
    ])
  }

  const openRef = (r: DeletionRequest) => {
    if (r.subject === 'contact' && r.refId) navigation.navigate('CRM', { screen: 'ContactDetail', params: { id: r.refId } })
    else if (r.subject === 'conversation' && r.refId) navigation.navigate('Conversations', { screen: 'ConversationThread', params: { id: r.refId } })
  }

  const filtered = items.filter((r) => {
    let ok = true
    if (status) ok = ok && r.status === status
    if (subject) ok = ok && r.subject === subject
    if (timeWin) {
      const win = timeWin === '24h' ? 86400000 : timeWin === '7d' ? 604800000 : 2592000000
      ok = ok && r.submittedAt >= now() - win
    }
    return ok
  })

  return (
    <SafeAreaView style={{ flex: 1, backgroundColor: theme.color.background }}>
      <ScrollView contentContainerStyle={{ paddingBottom: 24 }}>
        {/* Header */}
        <View style={{ paddingHorizontal: 24, paddingTop: insets.top + 12, paddingBottom: 12 }}>
          <View style={{ flexDirection: 'row', alignItems: 'center', justifyContent: 'space-between' }}>
            <TouchableOpacity onPress={() => navigation.goBack()} accessibilityRole="button" accessibilityLabel="Back" style={{ paddingHorizontal: 12, paddingVertical: 8, borderRadius: 10, borderWidth: 1, borderColor: theme.color.border }}>
              <Text style={{ color: theme.color.primary, fontWeight: '600' }}>{'< Back'}</Text>
            </TouchableOpacity>
            <Text style={{ color: theme.color.foreground, fontSize: 20, fontWeight: '700' }}>Deletion Requests</Text>
            <View style={{ width: 64 }} />
          </View>
        </View>

        {/* Filters */}
        <View style={{ paddingHorizontal: 24, marginBottom: 8 }}>
          <View style={{ flexDirection: 'row', flexWrap: 'wrap', gap: 8 }}>
            {(['pending','approved','processing','completed','rejected'] as const).map((s) => (
              <Chip key={s} label={s} active={status === s} onPress={() => setStatus(status === s ? undefined : s)} />
            ))}
            {(['contact','conversation','account'] as const).map((s) => (
              <Chip key={s} label={s} active={subject === s} onPress={() => setSubject(subject === s ? undefined : s)} />
            ))}
            {(['24h','7d','30d'] as const).map((t) => (
              <Chip key={t} label={t} active={timeWin === t} onPress={() => setTimeWin(timeWin === t ? undefined : t)} />
            ))}
          </View>
        </View>

        {/* List */}
        <View style={{ paddingHorizontal: 24 }}>
          {filtered.map((r) => (
            <View key={r.id} style={{ marginBottom: 8 }}>
              <DeletionRow req={r} onApprove={() => approve(r.id)} onReject={() => reject(r.id)} onView={() => openRef(r)} />
            </View>
          ))}
        </View>
      </ScrollView>
    </SafeAreaView>
  )
}

export default DeletionRequestsScreen


