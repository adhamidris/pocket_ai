import React from 'react'
import { View, Text, TextInput, TouchableOpacity, ScrollView } from 'react-native'
import { tokens } from '../../ui/tokens'
import { SlaPolicy, SlaTarget } from '../../types/automations'

export interface SlaTargetEditorProps { policy: SlaPolicy; onChange: (p: SlaPolicy) => void; testID?: string }

const makeTarget = (): SlaTarget => ({ priority: 'normal', frtP50Sec: 300, frtP90Sec: 1200 })

const SlaTargetEditor: React.FC<SlaTargetEditorProps> = ({ policy, onChange, testID }) => {
  const add = () => onChange({ ...policy, targets: [makeTarget(), ...policy.targets] })
  const update = (idx: number, patch: Partial<SlaTarget>) => {
    const next = policy.targets.slice()
    next[idx] = { ...next[idx], ...patch }
    onChange({ ...policy, targets: next })
  }
  const remove = (idx: number) => onChange({ ...policy, targets: policy.targets.filter((_, i) => i !== idx) })

  return (
    <View testID={testID}>
      <View style={{ flexDirection: 'row', alignItems: 'center', justifyContent: 'space-between', marginBottom: 8 }}>
        <Text style={{ color: tokens.colors.cardForeground, fontWeight: '700' }}>SLA Targets</Text>
        <TouchableOpacity onPress={add} accessibilityLabel="Add SLA target" accessibilityRole="button" style={{ paddingHorizontal: 12, paddingVertical: 8, borderRadius: 10, borderWidth: 1, borderColor: tokens.colors.border }}>
          <Text style={{ color: tokens.colors.primary, fontWeight: '700' }}>Add</Text>
        </TouchableOpacity>
      </View>
      <ScrollView style={{ maxHeight: 280 }}>
        <View style={{ gap: 8 }}>
          {policy.targets.map((t, idx) => (
            <View key={idx} style={{ padding: 12, borderWidth: 1, borderColor: tokens.colors.border, borderRadius: 12 }}>
              <View style={{ flexDirection: 'row', gap: 8, marginBottom: 8 }}>
                <TextInput value={t.priority} onChangeText={(v) => update(idx, { priority: v as any })} placeholder="priority" placeholderTextColor={tokens.colors.placeholder as any} style={{ width: 100, borderWidth: 1, borderColor: tokens.colors.border, borderRadius: 10, paddingHorizontal: 10, paddingVertical: 8, color: tokens.colors.cardForeground }} />
                <TextInput value={String(t.frtP50Sec)} onChangeText={(v) => update(idx, { frtP50Sec: Number(v) || 0 })} placeholder="frt p50 sec" placeholderTextColor={tokens.colors.placeholder as any} keyboardType="numeric" style={{ flex: 1, borderWidth: 1, borderColor: tokens.colors.border, borderRadius: 10, paddingHorizontal: 10, paddingVertical: 8, color: tokens.colors.cardForeground }} />
                <TextInput value={String(t.frtP90Sec)} onChangeText={(v) => update(idx, { frtP90Sec: Number(v) || 0 })} placeholder="frt p90 sec" placeholderTextColor={tokens.colors.placeholder as any} keyboardType="numeric" style={{ flex: 1, borderWidth: 1, borderColor: tokens.colors.border, borderRadius: 10, paddingHorizontal: 10, paddingVertical: 8, color: tokens.colors.cardForeground }} />
                <TextInput value={t.channel || ''} onChangeText={(v) => update(idx, { channel: (v || undefined) as any })} placeholder="channel (opt)" placeholderTextColor={tokens.colors.placeholder as any} style={{ width: 120, borderWidth: 1, borderColor: tokens.colors.border, borderRadius: 10, paddingHorizontal: 10, paddingVertical: 8, color: tokens.colors.cardForeground }} />
              </View>
              <View style={{ flexDirection: 'row', justifyContent: 'flex-end' }}>
                <TouchableOpacity onPress={() => remove(idx)} accessibilityLabel="Remove SLA target" accessibilityRole="button" style={{ paddingHorizontal: 12, paddingVertical: 8, borderRadius: 10, borderWidth: 1, borderColor: tokens.colors.border }}>
                  <Text style={{ color: tokens.colors.error, fontWeight: '700' }}>Remove</Text>
                </TouchableOpacity>
              </View>
            </View>
          ))}
        </View>
      </ScrollView>
    </View>
  )
}

export default React.memo(SlaTargetEditor)


