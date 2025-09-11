import React from 'react'
import { SafeAreaView, View, Text, TouchableOpacity, ScrollView, TextInput, Switch, Alert } from 'react-native'
import { useSafeAreaInsets } from 'react-native-safe-area-context'
import { useTheme } from '../../providers/ThemeProvider'
import { useNavigation, useRoute } from '@react-navigation/native'
import { ActionSpec, AllowRule } from '../../types/actions'
import GuardEditor from '../../components/actions/GuardEditor'
import RateLimitEditor from '../../components/actions/RateLimitEditor'

type Params = { initial?: AllowRule; actions?: ActionSpec[]; onSave?: (r: AllowRule & { enabled?: boolean }) => void }

const mkAction = (id: string, name: string, category: ActionSpec['category'], kind: ActionSpec['kind'], risk: ActionSpec['riskLevel']): ActionSpec => ({
  id, name, category, kind, riskLevel: risk, version: 1, summary: `Demo action: ${name}`, params: [], effects: ['UI-only']
})

const defaultActions: ActionSpec[] = [
  mkAction('conv.reply', 'Reply to Conversation', 'conversations', 'create', 'low'),
  mkAction('conv.close', 'Close Conversation', 'conversations', 'update', 'medium'),
  mkAction('crm.update', 'Update Contact', 'crm', 'update', 'medium'),
  mkAction('orders.refund', 'Issue Refund', 'orders', 'trigger', 'high'),
  mkAction('analytics.export', 'Export Report', 'analytics', 'export', 'low'),
]

const weekdaysLabels = ['Sun','Mon','Tue','Wed','Thu','Fri','Sat']

const AllowRuleBuilder: React.FC = () => {
  const { theme } = useTheme()
  const insets = useSafeAreaInsets()
  const navigation = useNavigation<any>()
  const route = useRoute<any>()
  const { initial, actions = defaultActions, onSave } = (route.params || {}) as Params

  const [actionId, setActionId] = React.useState<string>(initial?.actionId || actions[0]?.id || '')
  const [agentIds, setAgentIds] = React.useState<string>((initial?.agentIds || []).join(','))
  const [intents, setIntents] = React.useState<string>((initial?.intents || []).join(','))
  const [channels, setChannels] = React.useState<string>((initial?.channels || []).join(','))
  const [start, setStart] = React.useState<string>(initial?.timeOfDay?.start || '09:00')
  const [end, setEnd] = React.useState<string>(initial?.timeOfDay?.end || '17:00')
  const [weekdays, setWeekdays] = React.useState<number[]>(initial?.weekdays || [1,2,3,4,5])
  const [guard, setGuard] = React.useState<AllowRule['guard']>(initial?.guard)
  const [rate, setRate] = React.useState<{ count: number; perSeconds: number } | undefined>(initial?.rateLimit)
  const [requireApproval, setRequireApproval] = React.useState<boolean>(!!initial?.requireApproval)
  const [role, setRole] = React.useState<'owner'|'admin'|'supervisor' | undefined>(initial?.approverRole)
  const [enabled, setEnabled] = React.useState<boolean>(true)

  const toggleWeek = (d: number) => setWeekdays((arr) => arr.includes(d) ? arr.filter(x => x !== d) : [...arr, d])

  const validate = () => {
    if (!actionId) { Alert.alert('Validation', 'Pick an action'); return false }
    return true
  }

  const save = () => {
    if (!validate()) return
    const rule: AllowRule & { enabled?: boolean } = {
      actionId,
      agentIds: agentIds.split(',').map(s => s.trim()).filter(Boolean),
      intents: intents.split(',').map(s => s.trim()).filter(Boolean),
      channels: channels.split(',').map(s => s.trim()).filter(Boolean),
      timeOfDay: { start, end },
      weekdays,
      guard,
      rateLimit: rate,
      requireApproval,
      approverRole: role,
      enabled,
    }
    onSave ? onSave(rule) : Alert.alert('Saved', JSON.stringify(rule, null, 2))
    navigation.goBack()
  }

  const testInSimulator = () => {
    // For demo: navigate to ActionDetail with chosen action
    const spec = actions.find(a => a.id === actionId)
    if (!spec) { Alert.alert('No spec', 'Action spec not found'); return }
    navigation.navigate('Actions', { screen: 'ActionDetail', params: { spec } })
  }

  return (
    <SafeAreaView style={{ flex: 1, backgroundColor: theme.color.background }}>
      <ScrollView style={{ flex: 1 }}>
        {/* Header */}
        <View style={{ paddingHorizontal: 24, paddingTop: insets.top + 12, paddingBottom: 12 }}>
          <View style={{ flexDirection: 'row', alignItems: 'center', justifyContent: 'space-between' }}>
            <TouchableOpacity onPress={() => navigation.goBack()} accessibilityLabel="Back" accessibilityRole="button" style={{ paddingHorizontal: 12, paddingVertical: 8, borderRadius: 10, borderWidth: 1, borderColor: theme.color.border }}>
              <Text style={{ color: theme.color.primary, fontWeight: '700' }}>{'< Back'}</Text>
            </TouchableOpacity>
            <Text style={{ color: theme.color.foreground, fontSize: 20, fontWeight: '700' }}>Allow Rule</Text>
            <View style={{ width: 64 }} />
          </View>
        </View>

        {/* Action picker */}
        <View style={{ paddingHorizontal: 24, marginBottom: 12 }}>
          <Text style={{ color: theme.color.cardForeground, fontWeight: '700', marginBottom: 6 }}>Action</Text>
          <View style={{ flexDirection: 'row', flexWrap: 'wrap', gap: 8 }}>
            {actions.map(a => (
              <TouchableOpacity key={a.id} onPress={() => setActionId(a.id)} accessibilityRole="button" accessibilityLabel={`pick ${a.name}`} style={{ paddingHorizontal: 10, paddingVertical: 6, borderRadius: 10, borderWidth: 1, borderColor: actionId === a.id ? theme.color.primary : theme.color.border }}>
                <Text style={{ color: actionId === a.id ? theme.color.primary : theme.color.cardForeground, fontSize: 12 }}>{a.name}</Text>
              </TouchableOpacity>
            ))}
          </View>
        </View>

        {/* Who */}
        <View style={{ paddingHorizontal: 24, marginBottom: 12 }}>
          <Text style={{ color: theme.color.cardForeground, fontWeight: '700', marginBottom: 6 }}>Who</Text>
          <View style={{ borderWidth: 1, borderColor: theme.color.border, borderRadius: 12, backgroundColor: theme.color.card, marginBottom: 8 }}>
            <TextInput value={agentIds} onChangeText={setAgentIds} placeholder="Agent IDs (comma-separated)" placeholderTextColor={theme.color.placeholder} style={{ paddingHorizontal: 12, paddingVertical: 10, color: theme.color.cardForeground }} />
          </View>
          <View style={{ borderWidth: 1, borderColor: theme.color.border, borderRadius: 12, backgroundColor: theme.color.card, marginBottom: 8 }}>
            <TextInput value={intents} onChangeText={setIntents} placeholder="Intents (comma-separated)" placeholderTextColor={theme.color.placeholder} style={{ paddingHorizontal: 12, paddingVertical: 10, color: theme.color.cardForeground }} />
          </View>
          <View style={{ borderWidth: 1, borderColor: theme.color.border, borderRadius: 12, backgroundColor: theme.color.card }}>
            <TextInput value={channels} onChangeText={setChannels} placeholder="Channels (comma-separated)" placeholderTextColor={theme.color.placeholder} style={{ paddingHorizontal: 12, paddingVertical: 10, color: theme.color.cardForeground }} />
          </View>
        </View>

        {/* When */}
        <View style={{ paddingHorizontal: 24, marginBottom: 12 }}>
          <Text style={{ color: theme.color.cardForeground, fontWeight: '700', marginBottom: 6 }}>When</Text>
          <View style={{ flexDirection: 'row', gap: 8, marginBottom: 8 }}>
            <View style={{ flex: 1, borderWidth: 1, borderColor: theme.color.border, borderRadius: 10, backgroundColor: theme.color.card }}>
              <TextInput value={start} onChangeText={setStart} placeholder="Start HH:mm" placeholderTextColor={theme.color.placeholder} style={{ paddingHorizontal: 12, paddingVertical: 10, color: theme.color.cardForeground }} />
            </View>
            <View style={{ flex: 1, borderWidth: 1, borderColor: theme.color.border, borderRadius: 10, backgroundColor: theme.color.card }}>
              <TextInput value={end} onChangeText={setEnd} placeholder="End HH:mm" placeholderTextColor={theme.color.placeholder} style={{ paddingHorizontal: 12, paddingVertical: 10, color: theme.color.cardForeground }} />
            </View>
          </View>
          <View style={{ flexDirection: 'row', flexWrap: 'wrap', gap: 8 }}>
            {weekdaysLabels.map((lbl, i) => (
              <TouchableOpacity key={i} onPress={() => toggleWeek(i)} accessibilityRole="button" accessibilityLabel={`weekday ${lbl}`} style={{ paddingHorizontal: 10, paddingVertical: 6, borderRadius: 10, borderWidth: 1, borderColor: weekdays.includes(i) ? theme.color.primary : theme.color.border }}>
                <Text style={{ color: weekdays.includes(i) ? theme.color.primary : theme.color.cardForeground, fontSize: 12 }}>{lbl}</Text>
              </TouchableOpacity>
            ))}
          </View>
        </View>

        {/* Safety */}
        <View style={{ paddingHorizontal: 24, marginBottom: 12 }}>
          <GuardEditor value={guard} onChange={setGuard} />
        </View>

        {/* Rate limits */}
        <View style={{ paddingHorizontal: 24, marginBottom: 12 }}>
          <RateLimitEditor value={rate} onChange={setRate} />
        </View>

        {/* Approvals */}
        <View style={{ paddingHorizontal: 24, marginBottom: 12 }}>
          <Text style={{ color: theme.color.cardForeground, fontWeight: '700', marginBottom: 6 }}>Approvals</Text>
          <View style={{ flexDirection: 'row', alignItems: 'center', justifyContent: 'space-between', borderWidth: 1, borderColor: theme.color.border, borderRadius: 12, paddingHorizontal: 12, paddingVertical: 10 }}>
            <Text style={{ color: theme.color.cardForeground }}>Require approval</Text>
            <Switch value={requireApproval} onValueChange={setRequireApproval} accessibilityLabel="Require approval" />
          </View>
          {requireApproval && (
            <View style={{ flexDirection: 'row', gap: 8, marginTop: 8 }}>
              {(['owner','admin','supervisor'] as const).map(r => (
                <TouchableOpacity key={r} onPress={() => setRole(r)} accessibilityRole="button" accessibilityLabel={`role ${r}`} style={{ paddingHorizontal: 10, paddingVertical: 6, borderRadius: 10, borderWidth: 1, borderColor: role === r ? theme.color.primary : theme.color.border }}>
                  <Text style={{ color: role === r ? theme.color.primary : theme.color.cardForeground, fontSize: 12 }}>{r}</Text>
                </TouchableOpacity>
              ))}
            </View>
          )}
        </View>

        {/* Footer actions */}
        <View style={{ paddingHorizontal: 24, marginBottom: 24, flexDirection: 'row', flexWrap: 'wrap', gap: 8 }}>
          <TouchableOpacity onPress={save} accessibilityLabel="Save" accessibilityRole="button" style={{ paddingHorizontal: 12, paddingVertical: 10, borderRadius: 12, backgroundColor: theme.color.primary }}>
            <Text style={{ color: '#fff', fontWeight: '700' }}>Save</Text>
          </TouchableOpacity>
          <TouchableOpacity onPress={() => setEnabled(v => !v)} accessibilityLabel="Enable" accessibilityRole="button" style={{ paddingHorizontal: 12, paddingVertical: 10, borderRadius: 12, borderWidth: 1, borderColor: enabled ? theme.color.primary : theme.color.border }}>
            <Text style={{ color: enabled ? theme.color.primary : theme.color.cardForeground, fontWeight: '600' }}>{enabled ? 'Enabled' : 'Disabled'}</Text>
          </TouchableOpacity>
          <TouchableOpacity onPress={testInSimulator} accessibilityLabel="Test in Simulator" accessibilityRole="button" style={{ paddingHorizontal: 12, paddingVertical: 10, borderRadius: 12, borderWidth: 1, borderColor: theme.color.border }}>
            <Text style={{ color: theme.color.cardForeground, fontWeight: '600' }}>Test in Simulator</Text>
          </TouchableOpacity>
        </View>
      </ScrollView>
    </SafeAreaView>
  )
}

export default AllowRuleBuilder


