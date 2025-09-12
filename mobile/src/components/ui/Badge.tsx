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

  const getColors = () => {
    switch (variant) {
      case 'success':
        return {
          bg: theme.color.success + '20',
          border: theme.color.success + '40',
          text: theme.color.success
        }
      case 'error':
        return {
          bg: theme.color.error + '20',
          border: theme.color.error + '40', 
          text: theme.color.error
        }
      case 'warning':
        return {
          bg: theme.color.warning + '20',
          border: theme.color.warning + '40',
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
          bg: theme.color.primary + '20',
          border: theme.color.primary + '40',
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
      borderWidth: 1,
      borderColor: colors.border,
      borderRadius: theme.radius.sm,
      alignSelf: 'flex-start',
      ...padding
    }, style]}>
      <Text style={{
        color: colors.text,
        fontSize,
        fontWeight: '600'
      }}>
        {children}
      </Text>
    </View>
  )
}
