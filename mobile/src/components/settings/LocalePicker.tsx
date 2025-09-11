import React from 'react'
import { View, Text, ScrollView, TouchableOpacity } from 'react-native'
import { useTheme } from '../../providers/ThemeProvider'

export interface LocalePickerProps { value: string; onChange: (v: string) => void; testID?: string }

const LOCALES = ['en-US', 'ar', 'es-ES', 'fr-FR', 'de-DE']

const LocalePicker: React.FC<LocalePickerProps> = ({ value, onChange, testID }) => {
  const { theme } = useTheme()
  return (
    <View testID={testID} style={{ borderWidth: 1, borderColor: theme.color.border, borderRadius: 12, padding: 12 }}>
      <Text style={{ color: theme.color.mutedForeground, marginBottom: 6 }}>Locale</Text>
      <ScrollView horizontal showsHorizontalScrollIndicator={false}>
        <View style={{ flexDirection: 'row', gap: 8 }}>
          {LOCALES.map((loc) => (
            <TouchableOpacity key={loc} onPress={() => onChange(loc)} accessibilityRole="button" accessibilityLabel={`Locale ${loc}`} style={{ paddingHorizontal: 10, paddingVertical: 6, borderRadius: 10, borderWidth: 1, borderColor: value === loc ? theme.color.primary : theme.color.border }}>
              <Text style={{ color: value === loc ? theme.color.primary : theme.color.mutedForeground, fontWeight: '600' }}>{loc}</Text>
            </TouchableOpacity>
          ))}
        </View>
      </ScrollView>
    </View>
  )
}

export default React.memo(LocalePicker)


