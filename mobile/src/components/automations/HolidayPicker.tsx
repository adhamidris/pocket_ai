import React from 'react'
import { View, Text, TextInput, TouchableOpacity } from 'react-native'
import { tokens } from '../../ui/tokens'
import { Holiday } from '../../types/automations'

export interface HolidayPickerProps { holidays: Holiday[]; onChange: (h: Holiday[]) => void; testID?: string }

const HolidayPicker: React.FC<HolidayPickerProps> = ({ holidays, onChange, testID }) => {
  const [name, setName] = React.useState('')
  const [date, setDate] = React.useState('2025-12-25')

  const add = () => {
    if (!name.trim() || !date.trim()) return
    onChange([{ id: `h-${Date.now()}`, name: name.trim(), date }, ...holidays])
    setName(''); setDate('2025-12-25')
  }

  const remove = (id: string) => onChange(holidays.filter((h) => h.id !== id))

  return (
    <View testID={testID}>
      <Text style={{ color: tokens.colors.cardForeground, fontWeight: '700', marginBottom: 8 }}>Holidays</Text>
      <View style={{ flexDirection: 'row', gap: 8, marginBottom: 8 }}>
        <TextInput value={name} onChangeText={setName} placeholder="Name" placeholderTextColor={tokens.colors.placeholder as any} style={{ flex: 1, borderWidth: 1, borderColor: tokens.colors.border, borderRadius: 10, paddingHorizontal: 10, paddingVertical: 8, color: tokens.colors.cardForeground }} />
        <TextInput value={date} onChangeText={setDate} placeholder="YYYY-MM-DD" placeholderTextColor={tokens.colors.placeholder as any} style={{ width: 140, borderWidth: 1, borderColor: tokens.colors.border, borderRadius: 10, paddingHorizontal: 10, paddingVertical: 8, color: tokens.colors.cardForeground }} />
        <TouchableOpacity onPress={add} accessibilityLabel="Add holiday" accessibilityRole="button" style={{ paddingHorizontal: 12, paddingVertical: 8, borderRadius: 10, borderWidth: 1, borderColor: tokens.colors.border }}>
          <Text style={{ color: tokens.colors.primary, fontWeight: '700' }}>Add</Text>
        </TouchableOpacity>
      </View>
      {holidays.length === 0 ? (
        <Text style={{ color: tokens.colors.mutedForeground }}>None</Text>
      ) : (
        <View style={{ gap: 8 }}>
          {holidays.map((h) => (
            <View key={h.id} style={{ flexDirection: 'row', alignItems: 'center', justifyContent: 'space-between', padding: 12, borderWidth: 1, borderColor: tokens.colors.border, borderRadius: 12 }}>
              <Text style={{ color: tokens.colors.cardForeground }}>{h.date} • {h.name}</Text>
              <TouchableOpacity onPress={() => remove(h.id)} accessibilityLabel={`Remove ${h.name}`} accessibilityRole="button" style={{ paddingHorizontal: 10, paddingVertical: 6 }}>
                <Text style={{ color: tokens.colors.error, fontWeight: '700' }}>Remove</Text>
              </TouchableOpacity>
            </View>
          ))}
        </View>
      )}
    </View>
  )
}

export default React.memo(HolidayPicker)


