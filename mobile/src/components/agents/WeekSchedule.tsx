import React from 'react'
import { View, Modal, TouchableOpacity, TextInput, Text, Alert } from 'react-native'
import { ScheduleSlot } from '../../types/agents'
import { tokens } from '../../ui/tokens'

export interface WeekScheduleProps {
  slots: ScheduleSlot[]
  onChange: (slots: ScheduleSlot[]) => void
  testID?: string
}

const dayNames = ['Sun', 'Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat']

const overlaps = (a: ScheduleSlot, b: ScheduleSlot) => a.day === b.day && !(a.end <= b.start || b.end <= a.start)

const WeekSchedule: React.FC<WeekScheduleProps> = ({ slots, onChange, testID }) => {
  const [open, setOpen] = React.useState(false)
  const [draft, setDraft] = React.useState<ScheduleSlot>({ day: 1, start: '09:00', end: '17:00' })

  const addSlot = () => {
    const conflict = slots.some((s) => overlaps(s, draft))
    if (conflict) {
      Alert.alert('Overlap', 'Slot overlaps with an existing one.')
      return
    }
    onChange([...slots, draft])
    setOpen(false)
  }

  const removeSlot = (idx: number) => {
    const next = slots.filter((_, i) => i !== idx)
    onChange(next)
  }

  return (
    <View testID={testID}>
      {/* Grid */}
      {dayNames.map((dn, d) => (
        <View key={dn} style={{ flexDirection: 'row', alignItems: 'center', marginBottom: 8 }}>
          <Text style={{ width: 40, color: tokens.colors.mutedForeground }}>{dn}</Text>
          <View style={{ flex: 1, flexDirection: 'row', flexWrap: 'wrap', gap: 8 }}>
            {slots.map((s, idx) => s.day === d && (
              <View key={`${d}-${idx}`} style={{ flexDirection: 'row', alignItems: 'center', gap: 6, paddingHorizontal: 10, paddingVertical: 6, borderRadius: 12, borderWidth: 1, borderColor: tokens.colors.border, backgroundColor: tokens.colors.card }}>
                <Text style={{ color: tokens.colors.cardForeground }}>{s.start}–{s.end}</Text>
                <TouchableOpacity onPress={() => removeSlot(idx)} accessibilityLabel="Remove slot" accessibilityRole="button" style={{ paddingHorizontal: 6, paddingVertical: 4 }}>
                  <Text style={{ color: tokens.colors.error, fontWeight: '700' }}>✕</Text>
                </TouchableOpacity>
              </View>
            ))}
          </View>
        </View>
      ))}

      <TouchableOpacity onPress={() => setOpen(true)} accessibilityLabel="Add slot" accessibilityRole="button" style={{ alignSelf: 'flex-start', paddingHorizontal: 12, paddingVertical: 8, borderRadius: 12, borderWidth: 1, borderColor: tokens.colors.border }}>
        <Text style={{ color: tokens.colors.primary, fontWeight: '700' }}>Add Slot</Text>
      </TouchableOpacity>

      {/* Edit Modal */}
      <Modal visible={open} transparent animationType="fade" onRequestClose={() => setOpen(false)}>
        <View style={{ flex: 1, backgroundColor: 'rgba(0,0,0,0.4)', alignItems: 'center', justifyContent: 'center', padding: 16 }}>
          <View style={{ backgroundColor: tokens.colors.card, borderRadius: 16, padding: 16, borderWidth: 1, borderColor: tokens.colors.border, width: '90%' }}>
            <Text style={{ color: tokens.colors.cardForeground, fontWeight: '700', marginBottom: 8 }}>New Slot</Text>
            <View style={{ flexDirection: 'row', alignItems: 'center', gap: 8, marginBottom: 8 }}>
              <Text style={{ color: tokens.colors.mutedForeground, width: 60 }}>Day</Text>
              <TextInput value={String(draft.day)} onChangeText={(t) => setDraft((d) => ({ ...d, day: Math.max(0, Math.min(6, Number(t) || 0)) as any }))} keyboardType="number-pad" style={{ flex: 1, color: tokens.colors.cardForeground, borderBottomWidth: 1, borderBottomColor: tokens.colors.border }} />
            </View>
            <View style={{ flexDirection: 'row', alignItems: 'center', gap: 8, marginBottom: 8 }}>
              <Text style={{ color: tokens.colors.mutedForeground, width: 60 }}>Start</Text>
              <TextInput value={draft.start} onChangeText={(t) => setDraft((d) => ({ ...d, start: t }))} placeholder="09:00" placeholderTextColor={tokens.colors.placeholder as any} style={{ flex: 1, color: tokens.colors.cardForeground, borderBottomWidth: 1, borderBottomColor: tokens.colors.border }} />
            </View>
            <View style={{ flexDirection: 'row', alignItems: 'center', gap: 8, marginBottom: 12 }}>
              <Text style={{ color: tokens.colors.mutedForeground, width: 60 }}>End</Text>
              <TextInput value={draft.end} onChangeText={(t) => setDraft((d) => ({ ...d, end: t }))} placeholder="17:00" placeholderTextColor={tokens.colors.placeholder as any} style={{ flex: 1, color: tokens.colors.cardForeground, borderBottomWidth: 1, borderBottomColor: tokens.colors.border }} />
            </View>
            <View style={{ flexDirection: 'row', justifyContent: 'flex-end', gap: 12 }}>
              <TouchableOpacity onPress={() => setOpen(false)} style={{ paddingHorizontal: 12, paddingVertical: 8 }}>
                <Text style={{ color: tokens.colors.mutedForeground, fontWeight: '600' }}>Cancel</Text>
              </TouchableOpacity>
              <TouchableOpacity onPress={addSlot} style={{ paddingHorizontal: 12, paddingVertical: 8 }}>
                <Text style={{ color: tokens.colors.primary, fontWeight: '700' }}>Add</Text>
              </TouchableOpacity>
            </View>
          </View>
        </View>
      </Modal>
    </View>
  )
}

export default React.memo(WeekSchedule)



