import React from 'react'
import { View, ViewStyle, Platform } from 'react-native'
import { useTheme } from '../../providers/ThemeProvider'

export const Card: React.FC<{ 
  children: React.ReactNode
  style?: ViewStyle
  variant?: 'default' | 'premium'
}> = ({ children, style, variant = 'default' }) => {
  const { theme } = useTheme()
  
  const shadow = variant === 'premium' ? theme.shadow.premium : theme.shadow.md
  
  const cardStyle: ViewStyle = {
    backgroundColor: theme.color.card,
    borderColor: 'transparent',
    borderWidth: 0,
    borderRadius: theme.radius.xl,
    padding: theme.spacing.lg,
    // Platform-specific shadows
    ...(Platform.OS === 'ios' ? {
      shadowColor: shadow.ios.color,
      shadowOpacity: shadow.ios.opacity,
      shadowRadius: shadow.ios.radius,
      shadowOffset: { width: 0, height: shadow.ios.offsetY }
    } : {
      elevation: shadow.androidElevation
    })
  }

  return (
    <View style={[cardStyle, style]}>
      {children}
    </View>
  )
}
