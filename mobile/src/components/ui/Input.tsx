import React from 'react'
import { TextInput, View, ViewStyle } from 'react-native'
import { useTheme } from '@/providers/ThemeProvider'

export const Input: React.FC<{
  value?: string
  placeholder?: string
  onChangeText?: (t: string) => void
  style?: ViewStyle
  editable?: boolean
}> = ({ value, placeholder, onChangeText, style, editable = true }) => {
  const { theme } = useTheme()
  return (
    <View style={[{
      height: 40, borderRadius: 8, borderWidth: 1,
      borderColor: theme.color.border, backgroundColor: theme.color.background,
      paddingHorizontal: 12, justifyContent: 'center'
    }, style]}
    >
      <TextInput
        value={value}
        placeholder={placeholder}
        onChangeText={onChangeText}
        editable={editable}
        placeholderTextColor={theme.color.mutedForeground}
        style={{ color: theme.color.foreground }}
      />
    </View>
  )
}

