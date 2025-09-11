import React from 'react'
import { SafeAreaView, View, Text, TouchableOpacity, TextInput, Switch, FlatList, Alert } from 'react-native'
import { useNavigation } from '@react-navigation/native'
import { useSafeAreaInsets } from 'react-native-safe-area-context'
import { useTheme } from '../../providers/ThemeProvider'
import { EscalationPolicy } from '../../types/agents'
import TagChip from '../../components/crm/TagChip'
import { track } from '../../lib/analytics'

type OverflowTargetKind = 'skill_group' | 'specific_agent' | 'vip_queue'
const overflowKinds: OverflowTargetKind[] = ['skill_group', 'specific_agent', 'vip_queue']

const newPolicy = (): EscalationPolicy => ({ id: `ep-${Date.now()}`, name: 'New Policy', rules: [ { afterMinutes: 30, to: 'human_supervisor' } ] })

const PoliciesScreen: React.FC = () => {
  const navigation = useNavigation<any>()
  const insets = useSafeAreaInsets()
  const { theme } = useTheme()

  const [overflowKind, setOverflowKind] = React.useState<OverflowTargetKind>('skill_group')
  const [overflowTarget, setOverflowTarget] = React.useState<string>('billing')
  const [policies, setPolicies] = React.useState<EscalationPolicy[]>([newPolicy()])
  const [selectedId, setSelectedId] = React.useState<string>(policies[0]?.id || '')

  const current = policies.find((p) => p.id === selectedId) || null

  const addPolicy = () => {
    const p = newPolicy()
    setPolicies((arr) => [p, ...arr])
    setSelectedId(p.id)
    track('routing.escalation', { id: p.id })
  }

  const updateCurrent = (patch: Partial<EscalationPolicy>) => {
    if (!current) return
    setPolicies((arr) => arr.map((p) => p.id === current.id ? { ...p, ...patch } : p))
  }

  const addStep = () => {
    if (!current) return
    updateCurrent({ rules: [...current.rules, { afterMinutes: 60, to: 'on_call' }] })
    track('routing.escalation', { id: current.id })
  }

  const removeStep = (idx: number) => {
    if (!current) return
    updateCurrent({ rules: current.rules.filter((_, i) => i !== idx) })
    track('routing.escalation', { id: current.id })
  }

  const dests: Array<EscalationPolicy['rules'][number]['to']> = ['human_supervisor','vip_queue','on_call']

  return (
    <SafeAreaView style={{ flex: 1, backgroundColor: theme.color.background }}>
      {/* Header */}
      <View style={{ paddingHorizontal: 16, paddingTop: insets.top + 8, paddingBottom: 12, borderBottomWidth: 1, borderBottomColor: theme.color.border }}>
        <View style={{ flexDirection: 'row', alignItems: 'center', justifyContent: 'space-between' }}>
          <TouchableOpacity onPress={() => navigation.goBack()} accessibilityLabel="Back" accessibilityRole="button" style={{ padding: 8 }}>
            <Text style={{ color: theme.color.primary, fontWeight: '600' }}>{'< Back'}</Text>
          </TouchableOpacity>
          <Text style={{ color: theme.color.foreground, fontSize: 18, fontWeight: '700' }}>Policies</Text>
          <TouchableOpacity onPress={addPolicy} accessibilityLabel="Add Policy" accessibilityRole="button" style={{ padding: 8 }}>
            <Text style={{ color: theme.color.primary, fontWeight: '700' }}>+ Policy</Text>
          </TouchableOpacity>
        </View>
      </View>

      {/* Body */}
      <View style={{ flex: 1, flexDirection: 'row' }}>
        {/* Left: List */}
        <View style={{ width: 240, borderRightWidth: 1, borderRightColor: theme.color.border, padding: 12 }}>
          <FlatList
            data={policies}
            keyExtractor={(p) => p.id}
            renderItem={({ item }) => (
              <TouchableOpacity onPress={() => setSelectedId(item.id)} style={{ padding: 10, borderRadius: 12, borderWidth: 1, borderColor: selectedId === item.id ? theme.color.primary : theme.color.border, marginBottom: 8 }}>
                <Text style={{ color: theme.color.cardForeground, fontWeight: '600' }}>{item.name}</Text>
                <Text style={{ color: theme.color.mutedForeground, fontSize: 12 }}>{item.rules.length} steps</Text>
              </TouchableOpacity>
            )}
          />
        </View>

        {/* Right: Editor */}
        <View style={{ flex: 1, padding: 16 }}>
          {/* Overflow */}
          <Text style={{ color: theme.color.cardForeground, fontWeight: '700', marginBottom: 8 }}>Overflow</Text>
          <View style={{ flexDirection: 'row', gap: 8, marginBottom: 8 }}>
            {overflowKinds.map((k) => (
              <TagChip key={k} label={k} selected={overflowKind === k} onPress={() => setOverflowKind(k)} />
            ))}
          </View>
          <View style={{ borderWidth: 1, borderColor: theme.color.border, borderRadius: 12, backgroundColor: theme.color.card, marginBottom: 16 }}>
            <TextInput value={overflowTarget} onChangeText={setOverflowTarget} placeholder={overflowKind === 'specific_agent' ? 'Agent ID' : overflowKind === 'skill_group' ? 'Skill group' : 'VIP queue name'} placeholderTextColor={theme.color.placeholder} style={{ paddingHorizontal: 12, paddingVertical: 10, color: theme.color.cardForeground }} />
          </View>

          {!current ? (
            <Text style={{ color: theme.color.mutedForeground }}>Select a policy to edit.</Text>
          ) : (
            <View>
              <View style={{ flexDirection: 'row', alignItems: 'center', gap: 8, marginBottom: 12 }}>
                <Text style={{ color: theme.color.mutedForeground }}>Name</Text>
                <TextInput value={current.name} onChangeText={(t) => updateCurrent({ name: t })} placeholder="Policy name" placeholderTextColor={theme.color.placeholder} style={{ flex: 1, color: theme.color.cardForeground, borderBottomWidth: 1, borderBottomColor: theme.color.border }} />
              </View>

              <Text style={{ color: theme.color.cardForeground, fontWeight: '700', marginBottom: 8 }}>Escalation Steps</Text>
              {current.rules.map((r, idx) => (
                <View key={idx} style={{ borderWidth: 1, borderColor: theme.color.border, borderRadius: 12, padding: 10, marginBottom: 8 }}>
                  <View style={{ flexDirection: 'row', alignItems: 'center', gap: 8, marginBottom: 8 }}>
                    <Text style={{ color: theme.color.mutedForeground, width: 120 }}>After Minutes</Text>
                    <TextInput value={String(r.afterMinutes)} onChangeText={(t) => {
                      const minutes = Math.max(0, parseInt(t || '0', 10))
                      const rules = current.rules.map((rr, i) => i === idx ? { ...rr, afterMinutes: minutes } : rr)
                      updateCurrent({ rules })
                    }} keyboardType="number-pad" placeholder="30" placeholderTextColor={theme.color.placeholder} style={{ flex: 1, color: theme.color.cardForeground, borderBottomWidth: 1, borderBottomColor: theme.color.border }} />
                  </View>
                  <View style={{ flexDirection: 'row', alignItems: 'center', gap: 8, marginBottom: 8 }}>
                    <Text style={{ color: theme.color.mutedForeground, width: 120 }}>Destination</Text>
                    <TagChip label={r.to} onPress={() => {
                      const next = dests[(dests.indexOf(r.to) + 1) % dests.length]
                      const rules = current.rules.map((rr, i) => i === idx ? { ...rr, to: next } : rr)
                      updateCurrent({ rules })
                    }} />
                  </View>
                  <View style={{ flexDirection: 'row', alignItems: 'center', gap: 8, marginBottom: 8 }}>
                    <Text style={{ color: theme.color.mutedForeground, width: 120 }}>Requires Approval</Text>
                    <Switch value={!!r.requiresApproval} onValueChange={(v) => {
                      const rules = current.rules.map((rr, i) => i === idx ? { ...rr, requiresApproval: v } : rr)
                      updateCurrent({ rules })
                    }} />
                  </View>
                  <TouchableOpacity onPress={() => removeStep(idx)} style={{ alignSelf: 'flex-start', paddingHorizontal: 10, paddingVertical: 6, borderWidth: 1, borderColor: theme.color.border, borderRadius: 8 }}>
                    <Text style={{ color: theme.color.error, fontWeight: '700' }}>Remove Step</Text>
                  </TouchableOpacity>
                </View>
              ))}
              <TouchableOpacity onPress={addStep} style={{ paddingHorizontal: 12, paddingVertical: 8, borderRadius: 12, borderWidth: 1, borderColor: theme.color.border, alignSelf: 'flex-start', marginBottom: 12 }}>
                <Text style={{ color: theme.color.primary, fontWeight: '700' }}>+ Step</Text>
              </TouchableOpacity>

              <View style={{ flexDirection: 'row', alignItems: 'center', justifyContent: 'space-between' }}>
                <Text style={{ color: theme.color.mutedForeground }}>Overflow target: {overflowKind} → {overflowTarget || '—'}</Text>
                <TouchableOpacity onPress={() => Alert.alert('Saved', 'Policies saved locally (UI-only).')} style={{ paddingHorizontal: 12, paddingVertical: 8, borderRadius: 12, borderWidth: 1, borderColor: theme.color.border }}>
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

export default PoliciesScreen


