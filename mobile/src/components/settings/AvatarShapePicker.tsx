import React from 'react'
import { View, Text, TouchableOpacity } from 'react-native'
import { useTheme } from '../../providers/ThemeProvider'

export interface AvatarShapePickerProps { value: 'circle' | 'rounded' | 'square'; onChange: (v: 'circle'|'rounded'|'square') => void; testID?: string }

const AvatarShapePicker: React.FC<AvatarShapePickerProps> = ({ value, onChange, testID }) => {
  const { theme } = useTheme()
  const options: Array<{ key: 'circle'|'rounded'|'square'; label: string }> = [
    { key: 'circle', label: 'Circle' },
    { key: 'rounded', label: 'Rounded' },
    { key: 'square', label: 'Square' },
  ]
  return (
    <View testID={testID} style={{ borderWidth: 1, borderColor: theme.color.border, borderRadius: 12, padding: 12 }}>
      <Text style={{ color: theme.color.mutedForeground, marginBottom: 6 }}>Avatar shape</Text>
      <View style={{ flexDirection: 'row', gap: 8 }}>
        {options.map((o) => (
          <TouchableOpacity key={o.key} onPress={() => onChange(o.key)} accessibilityRole="button" accessibilityLabel={`Avatar ${o.label}`} style={{ paddingHorizontal: 10, paddingVertical: 6, borderRadius: 10, borderWidth: 1, borderColor: value === o.key ? theme.color.primary : theme.color.border }}>
            <Text style={{ color: value === o.key ? theme.color.primary : theme.color.mutedForeground, fontWeight: '600' }}>{o.label}</Text>
          </TouchableOpacity>
        ))}
      </View>
    </View>
  )
}

export default React.memo(AvatarShapePicker)


