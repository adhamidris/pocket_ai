import React from 'react'
import { TextInput, View, Text, TextInputProps, ViewStyle } from 'react-native'
import { useTheme } from '../../providers/ThemeProvider'

interface InputProps extends TextInputProps {
  label?: string
  error?: string
  icon?: React.ReactNode
  containerStyle?: ViewStyle
  borderless?: boolean
  surface?: 'default' | 'accent' | 'secondary'
  size?: 'sm' | 'md'
}

export const Input: React.FC<InputProps> = ({ 
  label, 
  error, 
  icon, 
  containerStyle, 
  borderless,
  surface = 'default',
  size = 'md',
  style,
  ...props 
}) => {
  const { theme } = useTheme()
  
  // Make inputs stand out on accent/secondary surfaces by using card background
  const bgColor = surface === 'accent'
    ? theme.color.card
    : surface === 'secondary'
      ? theme.color.card
      : theme.color.background
  
  const textColor = surface === 'default' ? theme.color.foreground : theme.color.cardForeground
  const placeholderColor = surface === 'default' ? theme.color.placeholder : theme.color.mutedForeground
  const height = size === 'sm' ? 36 : 44

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
        backgroundColor: bgColor,
        borderWidth: borderless ? 0 : 1,
        borderColor: borderless ? 'transparent' : (error ? theme.color.error : theme.color.border),
        borderRadius: theme.radius.md,
        paddingHorizontal: 14,
        height
      }}>
        {icon && (
          <View style={{ marginRight: 8 }}>
            {icon}
          </View>
        )}
        <TextInput
          style={[{
            flex: 1,
            color: textColor,
            fontSize: 16
          }, style]}
          placeholderTextColor={placeholderColor}
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
