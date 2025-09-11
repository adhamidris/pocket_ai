import React from 'react'
import { View, Text, TouchableOpacity, Modal, TextInput, ScrollView, Switch } from 'react-native'
import { tokens } from '../../ui/tokens'
import { BusinessCalendar, BusinessHoursDay } from '../../types/automations'

export interface HoursGridProps { calendar: BusinessCalendar; onChange: (c: BusinessCalendar) => void; testID?: string }

const dayName = (d: number) => ['Sun','Mon','Tue','Wed','Thu','Fri','Sat'][d] || String(d)

const HoursGrid: React.FC<HoursGridProps> = ({ calendar, onChange, testID }) => {
  const [open, setOpen] = React.useState(false)
  const [editingDay, setEditingDay] = React.useState<BusinessHoursDay | null>(null)
  const [start, setStart] = React.useState('09:00')
  const [end, setEnd] = React.useState('17:00')

  const toggleDayOpen = (d: number, v: boolean) => {
    const next = { ...calendar, days: calendar.days.map((dd) => dd.day === d ? { ...dd, open: v } : dd) }
    onChange(next)
  }

  const addRange = (d: number) => {
    const next = { ...calendar, days: calendar.days.map((dd) => dd.day === d ? { ...dd, ranges: [...dd.ranges, { start, end }] } : dd) }
    onChange(next); setOpen(false)
  }

  return (
    <View testID={testID}>
      <ScrollView horizontal showsHorizontalScrollIndicator={false}>
        <View style={{ flexDirection: 'row', gap: 8 }}>
          {calendar.days.map((d) => (
            <View key={d.day} style={{ width: 140, borderWidth: 1, borderColor: tokens.colors.border, borderRadius: 12, padding: 12 }}>
              <Text style={{ color: tokens.colors.cardForeground, fontWeight: '700', marginBottom: 8 }}>{dayName(d.day)}</Text>
              <View style={{ flexDirection: 'row', alignItems: 'center', justifyContent: 'space-between', marginBottom: 8 }}>
                <Text style={{ color: tokens.colors.mutedForeground }}>Open</Text>
                <Switch value={d.open} onValueChange={(v) => toggleDayOpen(d.day, v)} accessibilityLabel={`Open ${dayName(d.day)}`} />
              </View>
              {d.ranges.length === 0 ? (
                <Text style={{ color: tokens.colors.mutedForeground, marginBottom: 8 }}>—</Text>
              ) : (
                <View style={{ gap: 4, marginBottom: 8 }}>
                  {d.ranges.map((r, idx) => (
                    <Text key={idx} style={{ color: tokens.colors.cardForeground }}>{r.start}–{r.end}</Text>
                  ))}
                </View>
              )}
              <TouchableOpacity onPress={() => { setEditingDay(d); setOpen(true) }} accessibilityLabel={`Add range ${dayName(d.day)}`} accessibilityRole="button" style={{ paddingHorizontal: 10, paddingVertical: 6, borderWidth: 1, borderColor: tokens.colors.border, borderRadius: 8 }}>
                <Text style={{ color: tokens.colors.cardForeground }}>+ Range</Text>
              </TouchableOpacity>
            </View>
          ))}
        </View>
      </ScrollView>

      <Modal visible={open} transparent animationType="fade" onRequestClose={() => setOpen(false)}>
        <View style={{ flex: 1, backgroundColor: 'rgba(0,0,0,0.4)', alignItems: 'center', justifyContent: 'center', padding: 16 }}>
          <View style={{ backgroundColor: tokens.colors.card, borderRadius: 16, padding: 16, borderWidth: 1, borderColor: tokens.colors.border, width: '90%' }}>
            <Text style={{ color: tokens.colors.cardForeground, fontWeight: '700', marginBottom: 8 }}>Add Range — {editingDay ? dayName(editingDay.day) : ''}</Text>
            <View style={{ flexDirection: 'row', gap: 8, marginBottom: 12 }}>
              <TextInput value={start} onChangeText={setStart} placeholder="09:00" placeholderTextColor={tokens.colors.placeholder as any} style={{ flex: 1, borderWidth: 1, borderColor: tokens.colors.border, borderRadius: 10, paddingHorizontal: 10, paddingVertical: 8, color: tokens.colors.cardForeground }} />
              <TextInput value={end} onChangeText={setEnd} placeholder="17:00" placeholderTextColor={tokens.colors.placeholder as any} style={{ flex: 1, borderWidth: 1, borderColor: tokens.colors.border, borderRadius: 10, paddingHorizontal: 10, paddingVertical: 8, color: tokens.colors.cardForeground }} />
            </View>
            <View style={{ flexDirection: 'row', justifyContent: 'flex-end', gap: 12 }}>
              <TouchableOpacity onPress={() => setOpen(false)} style={{ paddingHorizontal: 12, paddingVertical: 8 }}>
                <Text style={{ color: tokens.colors.mutedForeground }}>Cancel</Text>
              </TouchableOpacity>
              <TouchableOpacity onPress={() => editingDay && addRange(editingDay.day)} style={{ paddingHorizontal: 12, paddingVertical: 8 }}>
                <Text style={{ color: tokens.colors.primary, fontWeight: '700' }}>Add</Text>
              </TouchableOpacity>
            </View>
          </View>
        </View>
      </Modal>
    </View>
  )
}

export default React.memo(HoursGrid)


