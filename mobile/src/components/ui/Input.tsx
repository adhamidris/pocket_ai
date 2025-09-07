import React from 'react'
import { TextInput, View, Text, TextInputProps, ViewStyle } from 'react-native'
import { useTheme } from '../../providers/ThemeProvider'

interface InputProps extends TextInputProps {
  label?: string
  error?: string
  icon?: React.ReactNode
  containerStyle?: ViewStyle
}

export const Input: React.FC<InputProps> = ({ 
  label, 
  error, 
  icon, 
  containerStyle, 
  style,
  ...props 
}) => {
  const { theme } = useTheme()

  return (
    <View style={[{ marginBottom: 16 }, containerStyle]}>
      {label && (
        <Text style={{
          color: theme.color.foreground,
          fontSize: 14,
          fontWeight: '600',
          marginBottom: 8
        }}>
          {label}
        </Text>
      )}
      <View style={{
        flexDirection: 'row',
        alignItems: 'center',
        backgroundColor: theme.color.background,
        borderWidth: 1,
        borderColor: error ? theme.color.error : theme.color.border,
        borderRadius: theme.radius.md,
        paddingHorizontal: 12,
        height: 44
      }}>
        {icon && (
          <View style={{ marginRight: 8 }}>
            {icon}
          </View>
        )}
        <TextInput
          style={[{
            flex: 1,
            color: theme.color.foreground,
            fontSize: 16
          }, style]}
          placeholderTextColor={theme.color.mutedForeground}
          {...props}
        />
      </View>
      {error && (
        <Text style={{
          color: theme.color.error,
          fontSize: 12,
          marginTop: 4
        }}>
          {error}
        </Text>
      )}
    </View>
  )
}
