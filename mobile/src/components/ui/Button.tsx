import React from 'react'
import { TouchableOpacity, Text, ViewStyle, TextStyle, View } from 'react-native'
import { LinearGradient } from 'expo-linear-gradient'
import { useTheme } from '@/providers/ThemeProvider'

type Variant = 'default' | 'secondary' | 'outline' | 'ghost' | 'link' | 'premium' | 'hero' | 'glass'
type Size = 'sm' | 'md' | 'lg' | 'xl'

const HEIGHT: Record<Size, number> = { sm: 36, md: 40, lg: 48, xl: 56 }
const RADIUS: Record<Size, number> = { sm: 12, md: 12, lg: 16, xl: 16 }

export const Button: React.FC<{
  title: string
  onPress?: () => void
  variant?: Variant
  size?: Size
  disabled?: boolean
}> = ({ title, onPress, variant = 'default', size = 'md', disabled }) => {
  const { theme } = useTheme()
  const base: ViewStyle = {
    height: HEIGHT[size],
    borderRadius: RADIUS[size],
    paddingHorizontal: size === 'xl' ? 20 : size === 'lg' ? 16 : 12,
    alignItems: 'center', justifyContent: 'center'
  }
  const text: TextStyle = { color: theme.color.foreground, fontSize: 16, fontWeight: '600' }

  if (variant === 'premium' || variant === 'hero') {
    return (
      <TouchableOpacity onPress={onPress} disabled={disabled} activeOpacity={0.8} style={[base, { overflow: 'hidden' }]}> 
        <LinearGradient
          pointerEvents="none"
          colors={[theme.color.primary, theme.color.primaryLight, 'hsl(260,100%,80%)']}
          start={{ x: 0, y: 0 }}
          end={{ x: 1, y: 1 }}
          style={{ ...base, position: 'absolute', left: 0, right: 0, top: 0, bottom: 0 }}
        />
        <Text style={{ ...text, color: '#fff' }}>{title}</Text>
      </TouchableOpacity>
    )
  }

  const bg: ViewStyle = (() => {
    switch (variant) {
      case 'default': return { backgroundColor: theme.color.primary }
      case 'secondary': return { backgroundColor: theme.color.secondary }
      case 'outline': return { backgroundColor: theme.color.background, borderWidth: 1, borderColor: theme.color.border }
      case 'ghost': return { backgroundColor: 'transparent' }
      case 'glass': return { backgroundColor: 'rgba(255,255,255,0.08)', borderWidth: 1, borderColor: 'rgba(255,255,255,0.2)' }
      case 'link': return { backgroundColor: 'transparent' }
      default: return { backgroundColor: theme.color.primary }
    }
  })()

  const color = variant === 'default' || variant === 'secondary' ? '#fff' : theme.color.foreground

  return (
    <TouchableOpacity onPress={onPress} disabled={disabled} activeOpacity={0.8} style={[base, bg, disabled && { opacity: 0.5 }]}> 
      <Text style={{ ...text, color }}>{title}</Text>
    </TouchableOpacity>
  )
}
