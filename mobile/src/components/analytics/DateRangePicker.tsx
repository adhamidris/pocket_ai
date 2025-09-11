import React from 'react'
import { View, Text, TouchableOpacity, Switch } from 'react-native'
import { tokens } from '../../ui/tokens'
import { TimeRange } from '../../types/analytics'

export interface DateRangePickerProps { range: TimeRange; onChange: (r: TimeRange) => void; testID?: string }

const presets: Array<{ label: string; days: number }> = [
  { label: '24h', days: 1 },
  { label: '7d', days: 7 },
  { label: '30d', days: 30 },
  { label: '90d', days: 90 },
]

const nowIso = () => new Date().toISOString()

const DateRangePicker: React.FC<DateRangePickerProps> = ({ range, onChange, testID }) => {
  const [compare, setCompare] = React.useState<boolean>(!!(range.compareStartIso && range.compareEndIso))

  const setPreset = (d: number) => {
    const end = new Date()
    const start = new Date(end.getTime() - d * 86400000)
    onChange({ ...range, startIso: start.toISOString(), endIso: end.toISOString() })
  }

  const toggleCompare = (v: boolean) => {
    setCompare(v)
    if (!v) onChange({ ...range, compareStartIso: undefined, compareEndIso: undefined })
    else onChange({ ...range, compareStartIso: range.startIso, compareEndIso: range.endIso })
  }

  return (
    <View testID={testID}>
      <View style={{ flexDirection: 'row', gap: 8, marginBottom: 8 }}>
        {presets.map((p) => (
          <TouchableOpacity key={p.label} onPress={() => setPreset(p.days)} accessibilityLabel={`Preset ${p.label}`} accessibilityRole="button" style={{ paddingHorizontal: 10, paddingVertical: 6, borderRadius: 10, borderWidth: 1, borderColor: tokens.colors.border }}>
            <Text style={{ color: tokens.colors.cardForeground }}>{p.label}</Text>
          </TouchableOpacity>
        ))}
      </View>
      <View style={{ flexDirection: 'row', alignItems: 'center', justifyContent: 'space-between' }}>
        <Text style={{ color: tokens.colors.mutedForeground }}>Compare to previous</Text>
        <Switch value={compare} onValueChange={toggleCompare} accessibilityLabel="Toggle compare" />
      </View>
    </View>
  )
}

export default React.memo(DateRangePicker)


