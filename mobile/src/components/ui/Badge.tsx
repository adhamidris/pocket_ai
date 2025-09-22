import React from 'react'
import { View, Text, ViewStyle, TextStyle } from 'react-native'
import { useTheme } from '../../providers/ThemeProvider'

interface BadgeProps {
  children: React.ReactNode
  variant?: 'default' | 'success' | 'error' | 'warning' | 'secondary'
  size?: 'sm' | 'md'
  style?: ViewStyle
}

export const Badge: React.FC<BadgeProps> = ({ 
  children, 
  variant = 'default', 
  size = 'md',
  style 
}) => {
  const { theme } = useTheme()

  const withAlpha = (c: string, a: number) =>
    c.startsWith('hsl(')
      ? c.replace('hsl(', 'hsla(').replace(')', `,${a})`)
      : c

  const getColors = () => {
    switch (variant) {
      case 'success':
        return {
          bg: withAlpha(theme.color.success, theme.dark ? 0.22 : 0.12),
          border: withAlpha(theme.color.success, theme.dark ? 0.32 : 0.24),
          text: theme.color.success
        }
      case 'error':
        return {
          bg: withAlpha(theme.color.error, theme.dark ? 0.22 : 0.12),
          border: withAlpha(theme.color.error, theme.dark ? 0.32 : 0.24), 
          text: theme.color.error
        }
      case 'warning':
        return {
          bg: withAlpha(theme.color.warning, theme.dark ? 0.22 : 0.12),
          border: withAlpha(theme.color.warning, theme.dark ? 0.32 : 0.24),
          text: theme.color.warning
        }
      case 'secondary':
        return {
          bg: theme.color.secondary,
          border: theme.color.border,
          text: theme.color.mutedForeground
        }
      default:
        return {
          bg: withAlpha(theme.color.primary, theme.dark ? 0.22 : 0.12),
          border: withAlpha(theme.color.primary, theme.dark ? 0.32 : 0.24),
          text: theme.color.primary
        }
    }
  }

  const colors = getColors()
  const padding = size === 'sm' ? { paddingHorizontal: 8, paddingVertical: 4 } : { paddingHorizontal: 12, paddingVertical: 6 }
  const fontSize = size === 'sm' ? 12 : 14

  return (
    <View style={[{
      backgroundColor: colors.bg,
      borderWidth: 0,
      borderColor: 'transparent',
      borderRadius: theme.radius.md,
      ...padding
    }, style]}>
      <Text style={{
        color: colors.text,
        fontSize,
        lineHeight: fontSize + 2,
        fontWeight: '600'
      }}>
        {children}
      </Text>
    </View>
  )
}
