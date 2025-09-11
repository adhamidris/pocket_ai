import React from 'react'
import { SafeAreaView, View, Text, TouchableOpacity, Switch, TextInput, FlatList, Alert } from 'react-native'
import { useNavigation } from '@react-navigation/native'
import { useSafeAreaInsets } from 'react-native-safe-area-context'
import { useTheme } from '../../providers/ThemeProvider'
import { RoutingRule, RouteCondition } from '../../types/agents'
import TagChip from '../../components/crm/TagChip'
import { track } from '../../lib/analytics'

type Op = 'is' | 'in' | 'eq' | 'gte' | 'lte'
type ThenAction = 'assign_agent' | 'assign_skill_group' | 'escalate' | 'queue' | 'deflect'

const conds: RouteCondition[] = ['intent','channel','vip','priority','businessHours','overflow']
const ops: Op[] = ['is','in','eq','gte','lte']
const thenActions: ThenAction[] = ['assign_agent','assign_skill_group','escalate','queue','deflect']

const newRule = (): RoutingRule => ({
  id: `rr-${Date.now()}`,
  name: 'New Rule',
  when: [ { cond: 'intent', op: 'is', value: 'billing' } ],
  then: { action: 'assign_skill_group', target: 'billing' },
  enabled: true,
})

const RoutingRulesScreen: React.FC = () => {
  const navigation = useNavigation<any>()
  const { theme } = useTheme()
  const insets = useSafeAreaInsets()

  const [rules, setRules] = React.useState<RoutingRule[]>([newRule()])
  const [selected, setSelected] = React.useState<string | null>(rules[0]?.id || null)

  const move = (idx: number, dir: -1 | 1) => {
    const j = idx + dir
    if (j < 0 || j >= rules.length) return
    const next = rules.slice()
    const [it] = next.splice(idx, 1)
    next.splice(j, 0, it)
    setRules(next)
    track('routing.rule', { action: 'reorder' })
  }

  const toggle = (id: string) => setRules((rs) => rs.map((r) => r.id === id ? { ...r, enabled: !r.enabled } : r))

  const onAddRule = () => {
    const r = newRule()
    setRules((rs) => [r, ...rs])
    setSelected(r.id)
    track('routing.rule', { action: 'create' })
  }

  const idxOf = (id: string) => rules.findIndex((r) => r.id === id)
  const current = rules.find((r) => r.id === selected) || null

  const updateCurrent = (patch: Partial<RoutingRule>) => {
    if (!current) return
    setRules((rs) => rs.map((r) => r.id === current.id ? { ...r, ...patch } : r))
  }

  const updateWhenRow = (rowIdx: number, patch: any) => {
    if (!current) return
    const when = current.when.map((w, i) => i === rowIdx ? { ...w, ...patch } : w)
    updateCurrent({ when })
  }

  const addWhenRow = () => {
    if (!current) return
    updateCurrent({ when: [...current.when, { cond: 'channel', op: 'is', value: 'whatsapp' }] })
  }

  const removeWhenRow = (rowIdx: number) => {
    if (!current) return
    updateCurrent({ when: current.when.filter((_, i) => i !== rowIdx) })
  }

  const previewCount = () => Math.floor(Math.random() * 200)

  return (
    <SafeAreaView style={{ flex: 1, backgroundColor: theme.color.background }}>
      {/* Header */}
      <View style={{ paddingHorizontal: 16, paddingTop: insets.top + 8, paddingBottom: 12, borderBottomWidth: 1, borderBottomColor: theme.color.border }}>
        <View style={{ flexDirection: 'row', alignItems: 'center', justifyContent: 'space-between' }}>
          <TouchableOpacity onPress={() => navigation.goBack()} accessibilityLabel="Back" accessibilityRole="button" style={{ padding: 8 }}>
            <Text style={{ color: theme.color.primary, fontWeight: '600' }}>{'< Back'}</Text>
          </TouchableOpacity>
          <Text style={{ color: theme.color.foreground, fontSize: 18, fontWeight: '700' }}>Routing Rules</Text>
          <View style={{ flexDirection: 'row', gap: 8 }}>
            <TouchableOpacity onPress={() => navigation.navigate('Agents', { screen: 'Policies' })} accessibilityLabel="Policies" accessibilityRole="button" style={{ padding: 8 }}>
              <Text style={{ color: theme.color.cardForeground, fontWeight: '600' }}>Policies</Text>
            </TouchableOpacity>
            <TouchableOpacity onPress={() => navigation.navigate('Automations', { screen: 'RuleBuilder', params: { rule: { id: `tmp-${Date.now()}`, name: 'Escalate policy', when: [{ key: 'priority', op: 'is', value: 'high' } as any], then: [{ key: 'escalate', params: { target: 'L2' } } as any], enabled: true, order: 0 } } })} accessibilityLabel="Escalate suggestions" accessibilityRole="button" style={{ padding: 8 }}>
              <Text style={{ color: theme.color.cardForeground, fontWeight: '600' }}>Escalate → Automations</Text>
            </TouchableOpacity>
            <TouchableOpacity onPress={onAddRule} accessibilityLabel="Add Rule" accessibilityRole="button" style={{ padding: 8 }}>
              <Text style={{ color: theme.color.primary, fontWeight: '700' }}>+ Rule</Text>
            </TouchableOpacity>
          </View>
        </View>
      </View>

      {/* Body */}
      <View style={{ flex: 1, flexDirection: 'row' }}>
        {/* List */}
        <View style={{ width: 240, borderRightWidth: 1, borderRightColor: theme.color.border, padding: 12 }}>
          <FlatList
            data={rules}
            keyExtractor={(r) => r.id}
            renderItem={({ item, index }) => (
              <TouchableOpacity onPress={() => setSelected(item.id)} style={{ padding: 10, borderRadius: 12, borderWidth: 1, borderColor: selected === item.id ? theme.color.primary : theme.color.border, marginBottom: 8 }}>
                <View style={{ flexDirection: 'row', alignItems: 'center', justifyContent: 'space-between' }}>
                  <Text style={{ color: theme.color.cardForeground, fontWeight: '600' }} numberOfLines={1}>{item.name}</Text>
                  <Switch value={item.enabled} onValueChange={() => { toggle(item.id); track('routing.rule', { action: item.enabled ? 'disable' : 'enable' }) }} />
                </View>
                <View style={{ flexDirection: 'row', gap: 8, marginTop: 8 }}>
                  <TouchableOpacity onPress={() => move(index, -1)} style={{ paddingHorizontal: 10, paddingVertical: 6, borderWidth: 1, borderColor: theme.color.border, borderRadius: 8 }}>
                    <Text style={{ color: theme.color.mutedForeground }}>↑</Text>
                  </TouchableOpacity>
                  <TouchableOpacity onPress={() => move(index, +1)} style={{ paddingHorizontal: 10, paddingVertical: 6, borderWidth: 1, borderColor: theme.color.border, borderRadius: 8 }}>
                    <Text style={{ color: theme.color.mutedForeground }}>↓</Text>
                  </TouchableOpacity>
                </View>
              </TouchableOpacity>
            )}
          />
        </View>

        {/* Builder */}
        <View style={{ flex: 1, padding: 16 }}>
          {!current ? (
            <Text style={{ color: theme.color.mutedForeground }}>Select a rule to edit.</Text>
          ) : (
            <View>
              <View style={{ flexDirection: 'row', alignItems: 'center', gap: 8, marginBottom: 12 }}>
                <Text style={{ color: theme.color.mutedForeground }}>Name</Text>
                <TextInput value={current.name} onChangeText={(t) => updateCurrent({ name: t })} placeholder="Rule name" placeholderTextColor={theme.color.placeholder} style={{ flex: 1, color: theme.color.cardForeground, borderBottomWidth: 1, borderBottomColor: theme.color.border }} />
              </View>

              <Text style={{ color: theme.color.cardForeground, fontWeight: '700', marginBottom: 8 }}>WHEN</Text>
              {current.when.map((w, i) => (
                <View key={i} style={{ borderWidth: 1, borderColor: theme.color.border, borderRadius: 12, padding: 10, marginBottom: 8 }}>
                  <View style={{ flexDirection: 'row', gap: 8, alignItems: 'center' }}>
                    <TagChip label={`cond: ${w.cond}`} onPress={() => {
                      const idx = conds.indexOf(w.cond)
                      const next = conds[(idx + 1) % conds.length]
                      updateWhenRow(i, { cond: next })
                    }} />
                    <TagChip label={`op: ${w.op || 'is'}`} onPress={() => {
                      const idx = ops.indexOf((w.op as Op) || 'is')
                      const next = ops[(idx + 1) % ops.length]
                      updateWhenRow(i, { op: next })
                    }} />
                    <TextInput value={String(w.value ?? '')} onChangeText={(t) => updateWhenRow(i, { value: t })} placeholder="value" placeholderTextColor={theme.color.placeholder} style={{ flex: 1, color: theme.color.cardForeground, borderBottomWidth: 1, borderBottomColor: theme.color.border }} />
                    <TouchableOpacity onPress={() => removeWhenRow(i)} style={{ paddingHorizontal: 10, paddingVertical: 6, borderWidth: 1, borderColor: theme.color.border, borderRadius: 8 }}>
                      <Text style={{ color: theme.color.error, fontWeight: '700' }}>Remove</Text>
                    </TouchableOpacity>
                  </View>
                </View>
              ))}
              <TouchableOpacity onPress={addWhenRow} style={{ paddingHorizontal: 12, paddingVertical: 8, borderRadius: 12, borderWidth: 1, borderColor: theme.color.border, alignSelf: 'flex-start', marginBottom: 12 }}>
                <Text style={{ color: theme.color.primary, fontWeight: '700' }}>+ Condition</Text>
              </TouchableOpacity>

              <Text style={{ color: theme.color.cardForeground, fontWeight: '700', marginBottom: 8 }}>THEN</Text>
              <View style={{ borderWidth: 1, borderColor: theme.color.border, borderRadius: 12, padding: 10, marginBottom: 12 }}>
                <View style={{ flexDirection: 'row', gap: 8, alignItems: 'center' }}>
                  <TagChip label={`action: ${current.then.action}`} onPress={() => {
                    const idx = thenActions.indexOf(current.then.action as ThenAction)
                    const next = thenActions[(idx + 1) % thenActions.length]
                    updateCurrent({ then: { ...current.then, action: next } as any })
                  }} />
                  <TextInput value={String(current.then.target ?? '')} onChangeText={(t) => updateCurrent({ then: { ...current.then, target: t } as any })} placeholder="target" placeholderTextColor={theme.color.placeholder} style={{ flex: 1, color: theme.color.cardForeground, borderBottomWidth: 1, borderBottomColor: theme.color.border }} />
                </View>
              </View>

              <View style={{ flexDirection: 'row', alignItems: 'center', justifyContent: 'space-between' }}>
                <Text style={{ color: theme.color.mutedForeground }}>Preview match count (stub): {previewCount()}</Text>
                <TouchableOpacity onPress={() => Alert.alert('Saved', 'Rules saved locally (UI-only).')} style={{ paddingHorizontal: 12, paddingVertical: 8, borderRadius: 12, borderWidth: 1, borderColor: theme.color.border }}>
                  <Text style={{ color: theme.color.primary, fontWeight: '700' }}>Save</Text>
                </TouchableOpacity>
              </View>
            </View>
          )}
        </View>
      </View>
    </SafeAreaView>
  )
}

export default RoutingRulesScreen


