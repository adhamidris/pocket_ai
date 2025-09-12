import React from 'react'
import { View, ViewStyle, Platform } from 'react-native'
import { useTheme } from '../../providers/ThemeProvider'

export const Card: React.FC<{ 
  children: React.ReactNode
  style?: ViewStyle
  variant?: 'default' | 'premium' | 'flat'
}> = ({ children, style, variant = 'default' }) => {
  const { theme } = useTheme()
  
  const shadow = variant === 'premium' ? theme.shadow.premium : theme.shadow.md
  const isFlat = variant === 'flat'
  
  const cardStyle: ViewStyle = {
    backgroundColor: theme.color.card,
    borderColor: isFlat ? 'transparent' : (theme.dark ? 'transparent' : theme.color.border),
    borderWidth: isFlat ? 0 : (theme.dark ? 0 : 1),
    borderRadius: theme.radius.xl,
    padding: theme.spacing.lg,
    // Platform-specific shadows
    ...(Platform.OS === 'ios' 
      ? (isFlat 
          ? { shadowColor: 'transparent', shadowOpacity: 0, shadowRadius: 0, shadowOffset: { width: 0, height: 0 } } 
          : { shadowColor: shadow.ios.color, shadowOpacity: shadow.ios.opacity, shadowRadius: shadow.ios.radius, shadowOffset: { width: 0, height: shadow.ios.offsetY } })
      : (isFlat 
          ? { elevation: 0 }
          : { elevation: shadow.androidElevation }
        )
    )
  }

  return (
    <View style={[cardStyle, style]}>
      {children}
    </View>
  )
}
