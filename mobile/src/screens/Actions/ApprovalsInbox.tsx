import React from 'react'
import { SafeAreaView, View, Text, TouchableOpacity, ScrollView, TextInput, Alert } from 'react-native'
import { useSafeAreaInsets } from 'react-native-safe-area-context'
import { useTheme } from '../../providers/ThemeProvider'
import { useNavigation, useRoute } from '@react-navigation/native'
import { ActionRun } from '../../types/actions'
import { track } from '../../lib/analytics'

type Params = { runs?: ActionRun[]; onUpdate?: (next: ActionRun[]) => void }

const riskFromAction = (actionId: string): 'low'|'medium'|'high' => {
  if (actionId.includes('refund')) return 'high'
  if (actionId.includes('close') || actionId.includes('update')) return 'medium'
  return 'low'
}

const ApprovalsInbox: React.FC = () => {
  const { theme } = useTheme()
  const insets = useSafeAreaInsets()
  const navigation = useNavigation<any>()
  const route = useRoute<any>()
  const { runs = [], onUpdate } = (route.params || {}) as Params

  const [reasons, setReasons] = React.useState<Record<string, string>>({})

  const approve = (id: string) => {
    const next = runs.map(r => r.id === id ? { ...r, state: 'approved' as const } : r)
    onUpdate?.(next)
    try { track('actions.approval', { runId: id, decision: 'approve' }) } catch {}
    navigation.goBack()
  }

  const reject = (id: string) => {
    const reason = (reasons[id] || '').trim()
    if (!reason) { Alert.alert('Reason required', 'Please provide a reason for rejection.'); return }
    const next = runs.map(r => r.id === id ? { ...r, state: 'rejected' as const, result: { ...(r.result || { ok: false, preview: '' }), ok: false, warnings: undefined, errors: [reason] } } : r)
    onUpdate?.(next)
    try { track('actions.approval', { runId: id, decision: 'reject' }) } catch {}
    navigation.goBack()
  }

  const queued = runs.filter(r => r.state === 'queued')

  return (
    <SafeAreaView style={{ flex: 1, backgroundColor: theme.color.background }}>
      <ScrollView style={{ flex: 1 }} contentContainerStyle={{ paddingBottom: 24 }}>
        {/* Header */}
        <View style={{ paddingHorizontal: 24, paddingTop: insets.top + 12, paddingBottom: 12 }}>
          <View style={{ flexDirection: 'row', alignItems: 'center', justifyContent: 'space-between' }}>
            <TouchableOpacity onPress={() => navigation.goBack()} accessibilityLabel="Back" accessibilityRole="button" style={{ paddingHorizontal: 12, paddingVertical: 8, borderRadius: 10, borderWidth: 1, borderColor: theme.color.border }}>
              <Text style={{ color: theme.color.primary, fontWeight: '700' }}>{'< Back'}</Text>
            </TouchableOpacity>
            <Text style={{ color: theme.color.foreground, fontSize: 20, fontWeight: '700' }}>Approvals</Text>
            <View style={{ width: 64 }} />
          </View>
          <View style={{ marginTop: 8, padding: 12, borderRadius: 12, borderWidth: 1, borderColor: theme.color.border, backgroundColor: theme.color.secondary }}>
            <Text style={{ color: theme.color.mutedForeground }}>UI-only: approvals do not execute real actions.</Text>
          </View>
        </View>

        {/* List */}
        <View style={{ paddingHorizontal: 24, gap: 12 }}>
          {queued.length === 0 ? (
            <Text style={{ color: theme.color.mutedForeground }}>No queued runs.</Text>
          ) : (
            queued.map((r) => (
              <View key={r.id} style={{ borderWidth: 1, borderColor: theme.color.border, borderRadius: 12, backgroundColor: theme.color.card, padding: 12 }}>
                <Text style={{ color: theme.color.cardForeground, fontWeight: '700' }}>{r.actionId}</Text>
                <Text style={{ color: theme.color.mutedForeground, marginTop: 4, fontSize: 12 }}>Requester: {r.requestedBy} • Risk: {riskFromAction(r.actionId)}</Text>
                <Text style={{ color: theme.color.mutedForeground, marginTop: 6 }}>Params: {JSON.stringify(r.params)}</Text>
                <View style={{ marginTop: 8 }}>
                  <Text style={{ color: theme.color.cardForeground, marginBottom: 4 }}>Reject reason</Text>
                  <View style={{ borderWidth: 1, borderColor: theme.color.border, borderRadius: 10, backgroundColor: theme.color.card }}>
                    <TextInput value={reasons[r.id] || ''} onChangeText={(t) => setReasons((o) => ({ ...o, [r.id]: t }))} placeholder="Reason required to reject" placeholderTextColor={theme.color.placeholder} style={{ paddingHorizontal: 10, paddingVertical: 8, color: theme.color.cardForeground }} />
                  </View>
                </View>
                <View style={{ flexDirection: 'row', gap: 8, marginTop: 10 }}>
                  <TouchableOpacity onPress={() => approve(r.id)} accessibilityLabel="Approve" accessibilityRole="button" style={{ paddingHorizontal: 12, paddingVertical: 10, borderRadius: 12, backgroundColor: theme.color.primary }}>
                    <Text style={{ color: '#fff', fontWeight: '700' }}>Approve</Text>
                  </TouchableOpacity>
                  <TouchableOpacity onPress={() => reject(r.id)} accessibilityLabel="Reject" accessibilityRole="button" style={{ paddingHorizontal: 12, paddingVertical: 10, borderRadius: 12, borderWidth: 1, borderColor: theme.color.error }}>
                    <Text style={{ color: theme.color.error, fontWeight: '700' }}>Reject</Text>
                  </TouchableOpacity>
                </View>
              </View>
            ))
          )}
        </View>
      </ScrollView>
    </SafeAreaView>
  )
}

export default ApprovalsInbox


