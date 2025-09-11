import React from 'react'
import { View, Text, TextInput } from 'react-native'
import { useTheme } from '../../providers/ThemeProvider'

export interface ColorSwatchProps { label: string; value: string; onChange: (hex: string) => void; testID?: string }

const ColorSwatch: React.FC<ColorSwatchProps> = ({ label, value, onChange, testID }) => {
  const { theme } = useTheme()
  return (
    <View testID={testID} style={{ borderWidth: 1, borderColor: theme.color.border, borderRadius: 12, padding: 12 }}>
      <Text style={{ color: theme.color.mutedForeground, marginBottom: 6 }}>{label}</Text>
      <View style={{ flexDirection: 'row', alignItems: 'center', gap: 8 }}>
        <View accessibilityLabel={`${label} color sample ${value}`} accessibilityRole="image" style={{ width: 28, height: 28, borderRadius: 6, backgroundColor: value, borderWidth: 1, borderColor: theme.color.border }} />
        <TextInput accessibilityLabel={`${label} color picker`} value={value} onChangeText={onChange} placeholder="#000000" placeholderTextColor={theme.color.placeholder} style={{ flex: 1, borderWidth: 1, borderColor: theme.color.border, borderRadius: 10, paddingHorizontal: 10, paddingVertical: 10, color: theme.color.cardForeground, minHeight: 44 }} />
      </View>
      <Text style={{ color: theme.color.mutedForeground, marginTop: 6 }}>Current: {value}</Text>
    </View>
  )
}

export default React.memo(ColorSwatch)


