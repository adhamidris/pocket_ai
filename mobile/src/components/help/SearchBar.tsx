import React from 'react'
import { View, TextInput } from 'react-native'
import { useTheme } from '../../providers/ThemeProvider'

export const SearchBar: React.FC<{ value: string; onChange: (v: string) => void; placeholder?: string }> = ({ value, onChange, placeholder = 'Search help' }) => {
  const { theme } = useTheme()
  return (
    <View style={{ padding: 8 }}>
      <TextInput
        value={value}
        onChangeText={onChange}
        placeholder={placeholder}
        placeholderTextColor={theme.color.placeholder}
        style={{
          color: theme.color.cardForeground,
          backgroundColor: theme.color.secondary,
          borderColor: theme.color.border,
          borderWidth: 1,
          borderRadius: 12,
          paddingHorizontal: 12,
          height: 44
        }}
        accessibilityLabel={placeholder}
      />
    </View>
  )
}


