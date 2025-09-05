import React from 'react'
import { View, ViewProps } from 'react-native'
import { useTheme } from '@/providers/ThemeProvider'

export const Card: React.FC<ViewProps> = ({ style, children, ...rest }) => {
  const { theme } = useTheme()
  return (
    <View
      style={[{
        backgroundColor: theme.color.card,
        borderColor: theme.color.border,
        borderWidth: 1,
        borderRadius: theme.radius.xl,
        padding: theme.spacing.lg,
        shadowColor: '#000', shadowOpacity: 0.15, shadowRadius: 20, shadowOffset: { width: 0, height: 12 }, elevation: 8
      }, style]}
      {...rest}
    >
      {children}
    </View>
  )
}

