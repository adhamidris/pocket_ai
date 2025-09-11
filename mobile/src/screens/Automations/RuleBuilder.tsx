import React from 'react'
import { SafeAreaView, View, Text, TextInput, TouchableOpacity, ScrollView, Alert, Switch } from 'react-native'
import { useSafeAreaInsets } from 'react-native-safe-area-context'
import { useTheme } from '../../providers/ThemeProvider'
import { useNavigation, useRoute } from '@react-navigation/native'
import { Action, Condition, Rule } from '../../types/automations'
import ConditionRow from '../../components/automations/ConditionRow'
import ActionRow from '../../components/automations/ActionRow'
import { track } from '../../lib/analytics'

export interface RuleBuilderParams {
  rule?: Rule
  onSave?: (rule: Rule) => void
  currentMaxOrder?: number
}

const presetsWhen: Array<{ label: string; cond: Condition }> = [
  { label: 'Intent is order', cond: { key: 'intent', op: 'is', value: 'order' } },
  { label: 'Channel is web', cond: { key: 'channel', op: 'is', value: 'web' } },
  { label: 'VIP is true', cond: { key: 'vip', op: 'true' } as any },
  { label: 'Waiting ≥ 30m', cond: { key: 'waitingMinutes', op: 'gte', value: 30 } },
  { label: 'Within hours', cond: { key: 'withinHours', op: 'true' } as any },
]

const presetsThen: Array<{ label: string; action: Action }> = [
  { label: 'Auto-reply', action: { key: 'autoReply', params: { message: 'Thanks! We will help shortly.' } } },
  { label: 'Assign skill: billing', action: { key: 'routeToSkill', params: { skill: 'billing' } } },
  { label: 'Assign agent: Nancy', action: { key: 'routeToAgent', params: { agent: 'Nancy' } } },
  { label: 'Set priority: high', action: { key: 'setPriority', params: { priority: 'high' } } },
  { label: 'Add tag: urgent', action: { key: 'addTag', params: { tag: 'urgent' } } },
  { label: 'Escalate', action: { key: 'escalate' } },
  { label: 'Request CSAT', action: { key: 'requestCSAT' } },
  { label: 'Deflect to FAQ', action: { key: 'deflect', params: { destination: 'faq' } } },
]

const toEnglish = (name: string, when: Condition[], then: Action[], order?: number) => {
  const wh = when.map((c) => `${String(c.key)} ${String(c.op)} ${c.value != null ? JSON.stringify(c.value) : ''}`.trim()).join(' AND ')
  const th = then.map((a) => `${String(a.key)}${a.params ? ' ' + JSON.stringify(a.params) : ''}`).join('; ')
  return `${order ? `[#${order}] ` : ''}${name || 'Untitled'}: WHEN ${wh || '—'} THEN ${th || '—'}`
}

const RuleBuilder: React.FC = () => {
  const { theme } = useTheme()
  const insets = useSafeAreaInsets()
  const navigation = useNavigation<any>()
  const route = useRoute<any>()
  const params: RuleBuilderParams = route.params || {}

  const [name, setName] = React.useState<string>(params.rule?.name || '')
  const [nameDraft, setNameDraft] = React.useState<string>(params.rule?.name || '')
  const [enabled, setEnabled] = React.useState<boolean>(params.rule?.enabled ?? true)
  const [when, setWhen] = React.useState<Condition[]>(params.rule?.when?.length ? params.rule.when : [{ key: 'intent', op: 'is', value: 'order' } as any])
  const [then, setThen] = React.useState<Action[]>(params.rule?.then?.length ? params.rule.then : [{ key: 'autoReply', params: { message: 'Thanks!' } } as any])

  React.useEffect(() => {
    const t = setTimeout(() => setName(nameDraft), 300)
    return () => clearTimeout(t)
  }, [nameDraft])

  const preview = toEnglish(nameDraft, when, then, params.rule?.order)

  const addWhen = (c?: Condition) => setWhen((arr) => [...arr, c || ({ key: 'intent', op: 'is', value: '' } as any)])
  const removeWhen = (idx: number) => setWhen((arr) => arr.filter((_, i) => i !== idx))
  const addThen = (a?: Action) => setThen((arr) => [...arr, a || ({ key: 'autoReply', params: { message: '' } } as any)])
  const removeThen = (idx: number) => setThen((arr) => arr.filter((_, i) => i !== idx))

  const save = () => {
    const id = params.rule?.id || `r-${Date.now()}`
    const order = params.rule?.order || (params.currentMaxOrder ? params.currentMaxOrder + 1 : 1)
    const rule: Rule = { id, name: name.trim() || 'Untitled rule', when, then, enabled, order }
    track(params.rule ? 'rule.update' : 'rule.create')
    try { params.onSave && params.onSave(rule) } catch {}
    navigation.goBack()
  }

  const testInSimulator = () => {
    Alert.alert('Simulator', 'Test input sent (UI-only).')
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
          <Text style={{ color: theme.color.foreground, fontSize: 18, fontWeight: '700' }}>{params.rule ? 'Edit Rule' : 'New Rule'}</Text>
          <View style={{ width: 64 }} />
        </View>
      </View>

      <ScrollView style={{ padding: 16 }} contentContainerStyle={{ paddingBottom: 24 }}>
        {/* Name & enable */}
        <View style={{ marginBottom: 12 }}>
          <Text style={{ color: theme.color.mutedForeground, marginBottom: 6 }}>Name</Text>
          <TextInput value={nameDraft} onChangeText={setNameDraft} placeholder="e.g., VIP fast route" placeholderTextColor={theme.color.placeholder} accessibilityLabel="Rule name" style={{ borderWidth: 1, borderColor: theme.color.border, borderRadius: 12, paddingHorizontal: 12, paddingVertical: 10, color: theme.color.cardForeground }} />
          <View style={{ flexDirection: 'row', alignItems: 'center', justifyContent: 'space-between', marginTop: 8 }}>
            <Text style={{ color: theme.color.mutedForeground }}>Enabled</Text>
            <Switch value={enabled} onValueChange={(v) => { setEnabled(v); track('rule.enable', { enabled: v }) }} accessibilityLabel="Enabled" />
          </View>
        </View>

        {/* Presets */}
        <View style={{ marginBottom: 12 }}>
          <Text style={{ color: theme.color.cardForeground, fontWeight: '700', marginBottom: 6 }}>Quick WHEN presets</Text>
          <View style={{ flexDirection: 'row', flexWrap: 'wrap', gap: 8 }}>
            {presetsWhen.map((p, idx) => (
              <TouchableOpacity key={idx} onPress={() => addWhen(p.cond)} accessibilityLabel={`Add ${p.label}`} accessibilityRole="button" style={{ paddingHorizontal: 10, paddingVertical: 6, borderRadius: 10, borderWidth: 1, borderColor: theme.color.border }}>
                <Text style={{ color: theme.color.mutedForeground }}>{p.label}</Text>
              </TouchableOpacity>
            ))}
          </View>
        </View>

        {/* WHEN list */}
        <View style={{ marginBottom: 12 }}>
          <View style={{ flexDirection: 'row', alignItems: 'center', justifyContent: 'space-between', marginBottom: 6 }}>
            <Text style={{ color: theme.color.cardForeground, fontWeight: '700' }}>WHEN</Text>
            <TouchableOpacity onPress={() => addWhen()} accessibilityLabel="Add condition" accessibilityRole="button" style={{ paddingHorizontal: 10, paddingVertical: 6, borderRadius: 10, borderWidth: 1, borderColor: theme.color.border }}>
              <Text style={{ color: theme.color.primary, fontWeight: '700' }}>+ Condition</Text>
            </TouchableOpacity>
          </View>
          <View style={{ gap: 8 }}>
            {when.map((c, idx) => (
              <View key={idx}>
                <ConditionRow cond={c} onChange={(nc) => setWhen((arr) => arr.map((x, i) => i === idx ? nc : x))} />
                <View style={{ alignItems: 'flex-end', marginTop: 6 }}>
                  <TouchableOpacity onPress={() => removeWhen(idx)} accessibilityLabel="Remove condition" accessibilityRole="button" style={{ paddingHorizontal: 10, paddingVertical: 6, borderRadius: 10, borderWidth: 1, borderColor: theme.color.border }}>
                    <Text style={{ color: theme.color.error, fontWeight: '700' }}>Remove</Text>
                  </TouchableOpacity>
                </View>
              </View>
            ))}
          </View>
        </View>

        {/* Action presets */}
        <View style={{ marginBottom: 12 }}>
          <Text style={{ color: theme.color.cardForeground, fontWeight: '700', marginBottom: 6 }}>Quick THEN presets</Text>
          <View style={{ flexDirection: 'row', flexWrap: 'wrap', gap: 8 }}>
            {presetsThen.map((p, idx) => (
              <TouchableOpacity key={idx} onPress={() => addThen(p.action)} accessibilityLabel={`Add ${p.label}`} accessibilityRole="button" style={{ paddingHorizontal: 10, paddingVertical: 6, borderRadius: 10, borderWidth: 1, borderColor: theme.color.border }}>
                <Text style={{ color: theme.color.mutedForeground }}>{p.label}</Text>
              </TouchableOpacity>
            ))}
          </View>
        </View>

        {/* THEN list */}
        <View style={{ marginBottom: 12 }}>
          <View style={{ flexDirection: 'row', alignItems: 'center', justifyContent: 'space-between', marginBottom: 6 }}>
            <Text style={{ color: theme.color.cardForeground, fontWeight: '700' }}>THEN</Text>
            <TouchableOpacity onPress={() => addThen()} accessibilityLabel="Add action" accessibilityRole="button" style={{ paddingHorizontal: 10, paddingVertical: 6, borderRadius: 10, borderWidth: 1, borderColor: theme.color.border }}>
              <Text style={{ color: theme.color.primary, fontWeight: '700' }}>+ Action</Text>
            </TouchableOpacity>
          </View>
          <View style={{ gap: 8 }}>
            {then.map((a, idx) => (
              <View key={idx}>
                <ActionRow action={a} onChange={(na) => setThen((arr) => arr.map((x, i) => i === idx ? na : x))} />
                <View style={{ alignItems: 'flex-end', marginTop: 6 }}>
                  <TouchableOpacity onPress={() => removeThen(idx)} accessibilityLabel="Remove action" accessibilityRole="button" style={{ paddingHorizontal: 10, paddingVertical: 6, borderRadius: 10, borderWidth: 1, borderColor: theme.color.border }}>
                    <Text style={{ color: theme.color.error, fontWeight: '700' }}>Remove</Text>
                  </TouchableOpacity>
                </View>
              </View>
            ))}
          </View>
        </View>

        {/* Preview */}
        <View style={{ padding: 12, borderWidth: 1, borderColor: theme.color.border, borderRadius: 12, marginBottom: 12 }}>
          <Text style={{ color: theme.color.mutedForeground, marginBottom: 6 }}>Preview</Text>
          <Text style={{ color: theme.color.cardForeground }}>{preview}</Text>
        </View>

        {/* Actions */}
        <View style={{ flexDirection: 'row', flexWrap: 'wrap', gap: 8 }}>
          <TouchableOpacity onPress={save} accessibilityLabel="Save rule" accessibilityRole="button" style={{ paddingHorizontal: 12, paddingVertical: 10, borderWidth: 1, borderColor: theme.color.border, borderRadius: 12 }}>
            <Text style={{ color: theme.color.primary, fontWeight: '700' }}>Save</Text>
          </TouchableOpacity>
          <TouchableOpacity onPress={() => setEnabled((v) => !v)} accessibilityLabel="Toggle enabled" accessibilityRole="button" style={{ paddingHorizontal: 12, paddingVertical: 10, borderWidth: 1, borderColor: theme.color.border, borderRadius: 12 }}>
            <Text style={{ color: theme.color.cardForeground, fontWeight: '600' }}>{enabled ? 'Disable' : 'Enable'}</Text>
          </TouchableOpacity>
          <TouchableOpacity onPress={testInSimulator} accessibilityLabel="Test in Simulator" accessibilityRole="button" style={{ paddingHorizontal: 12, paddingVertical: 10, borderWidth: 1, borderColor: theme.color.border, borderRadius: 12 }}>
            <Text style={{ color: theme.color.cardForeground, fontWeight: '600' }}>Test in Simulator</Text>
          </TouchableOpacity>
        </View>
      </ScrollView>
    </SafeAreaView>
  )
}

export default RuleBuilder


