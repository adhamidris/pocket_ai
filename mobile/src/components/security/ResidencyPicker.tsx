import React from 'react'
import { View, Text, TouchableOpacity, ScrollView } from 'react-native'
import { useTheme } from '../../providers/ThemeProvider'
import { ResidencySetting } from '../../types/security'

export interface ResidencyPickerProps { value: ResidencySetting; onChange: (v: ResidencySetting) => void; testID?: string }

const ResidencyPicker: React.FC<ResidencyPickerProps> = ({ value, onChange, testID }) => {
  const { theme } = useTheme()
  const opts: Array<{ key: ResidencySetting['region']; label: string }> = [
    { key: 'auto', label: 'Auto' },
    { key: 'us', label: 'US' },
    { key: 'eu', label: 'EU' },
  ]
  return (
    <View testID={testID} style={{ borderWidth: 1, borderColor: theme.color.border, borderRadius: 12, padding: 12 }}>
      <Text style={{ color: theme.color.mutedForeground, marginBottom: 6 }}>Region</Text>
      <ScrollView horizontal showsHorizontalScrollIndicator={false}>
        <View style={{ flexDirection: 'row', gap: 8 }}>
          {opts.map((o) => (
            <TouchableOpacity key={o.key} onPress={() => onChange({ ...value, region: o.key })} accessibilityRole="button" accessibilityLabel={`Region ${o.label}`} style={{ paddingHorizontal: 10, paddingVertical: 6, borderRadius: 10, borderWidth: 1, borderColor: value.region === o.key ? theme.color.primary : theme.color.border }}>
              <Text style={{ color: value.region === o.key ? theme.color.primary : theme.color.mutedForeground, fontWeight: '600' }}>{o.label}</Text>
            </TouchableOpacity>
          ))}
        </View>
      </ScrollView>
    </View>
  )
}

export default React.memo(ResidencyPicker)


