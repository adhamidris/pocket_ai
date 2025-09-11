import React from 'react'
import { View, Text, ScrollView, TouchableOpacity } from 'react-native'
import { useTheme } from '../../providers/ThemeProvider'

export interface TimezonePickerProps { value: string; onChange: (v: string) => void; testID?: string }

const ZONES = ['UTC', 'UTC+1', 'UTC+2', 'UTC+3', 'UTC+4', 'UTC+5', 'UTC+7', 'UTC+8', 'UTC+9', 'UTC-1', 'UTC-4', 'UTC-5', 'UTC-8']

const TimezonePicker: React.FC<TimezonePickerProps> = ({ value, onChange, testID }) => {
  const { theme } = useTheme()
  return (
    <View testID={testID} style={{ borderWidth: 1, borderColor: theme.color.border, borderRadius: 12, padding: 12 }}>
      <Text style={{ color: theme.color.mutedForeground, marginBottom: 6 }}>Timezone</Text>
      <ScrollView horizontal showsHorizontalScrollIndicator={false}>
        <View style={{ flexDirection: 'row', gap: 8 }}>
          {ZONES.map((z) => (
            <TouchableOpacity key={z} onPress={() => onChange(z)} accessibilityRole="button" accessibilityLabel={`Timezone ${z}`} style={{ paddingHorizontal: 10, paddingVertical: 6, borderRadius: 10, borderWidth: 1, borderColor: value === z ? theme.color.primary : theme.color.border }}>
              <Text style={{ color: value === z ? theme.color.primary : theme.color.mutedForeground, fontWeight: '600' }}>{z}</Text>
            </TouchableOpacity>
          ))}
        </View>
      </ScrollView>
    </View>
  )
}

export default React.memo(TimezonePicker)


