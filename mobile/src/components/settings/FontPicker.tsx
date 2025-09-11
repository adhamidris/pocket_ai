import React from 'react'
import { View, Text, TouchableOpacity } from 'react-native'
import { useTheme } from '../../providers/ThemeProvider'

export interface FontPickerProps { value?: string; onChange: (v: string) => void; testID?: string }

const FONTS = ['System', 'Inter', 'Roboto', 'SF Pro', 'Open Sans']

const FontPicker: React.FC<FontPickerProps> = ({ value, onChange, testID }) => {
  const { theme } = useTheme()
  return (
    <View testID={testID} style={{ borderWidth: 1, borderColor: theme.color.border, borderRadius: 12, padding: 12 }}>
      <Text style={{ color: theme.color.mutedForeground, marginBottom: 6 }}>Font</Text>
      <View style={{ flexDirection: 'row', flexWrap: 'wrap', gap: 8 }}>
        {FONTS.map((f) => (
          <TouchableOpacity key={f} onPress={() => onChange(f)} accessibilityRole="button" accessibilityLabel={`Font ${f}`} style={{ paddingHorizontal: 10, paddingVertical: 6, borderRadius: 10, borderWidth: 1, borderColor: value === f ? theme.color.primary : theme.color.border }}>
            <Text style={{ color: value === f ? theme.color.primary : theme.color.mutedForeground, fontWeight: '600' }}>{f}</Text>
          </TouchableOpacity>
        ))}
      </View>
    </View>
  )
}

export default React.memo(FontPicker)


