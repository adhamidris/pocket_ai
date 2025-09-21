import React, { useRef } from 'react'
import { TouchableOpacity, Text, ViewStyle, TextStyle, Animated } from 'react-native'
import { LinearGradient } from 'expo-linear-gradient'
import * as Haptics from 'expo-haptics'
import { useTheme } from '../../providers/ThemeProvider'
import { LoadingSpinner } from './LoadingSpinner'

type Variant = 'default' | 'secondary' | 'outline' | 'ghost' | 'link' | 'premium' | 'hero' | 'glass' | 'danger' | 'dangerSoft' | 'card'
type Size = 'xs' | 'sm' | 'md' | 'lg' | 'xl'

const HEIGHT: Record<Size, number> = { xs: 32, sm: 36, md: 40, lg: 48, xl: 56 }
const RADIUS: Record<Size, number> = { xs: 10, sm: 12, md: 12, lg: 16, xl: 16 }

export const Button: React.FC<{
  title: string
  onPress?: () => void
  variant?: Variant
  size?: Size
  disabled?: boolean
  loading?: boolean
  fullWidth?: boolean
  iconLeft?: React.ReactNode
  accessibilityLabel?: string
}> = ({ title, onPress, variant = 'default', size = 'md', disabled, loading, fullWidth, iconLeft, accessibilityLabel }) => {
  const { theme } = useTheme()
  const scaleValue = useRef(new Animated.Value(1)).current

  const withAlpha = (c: string, a: number) =>
    c.startsWith('hsl(')
      ? c.replace('hsl(', 'hsla(').replace(')', `,${a})`)
      : c

  const handlePressIn = () => {
    if (!disabled && !loading) {
      Haptics.impactAsync(Haptics.ImpactFeedbackStyle.Light)
      Animated.spring(scaleValue, {
        toValue: 0.95,
        useNativeDriver: true,
      }).start()
    }
  }

  const handlePressOut = () => {
    if (!disabled && !loading) {
      Animated.spring(scaleValue, {
        toValue: 1,
        useNativeDriver: true,
      }).start()
    }
  }

  const handlePress = () => {
    if (!disabled && !loading && onPress) {
      Haptics.impactAsync(Haptics.ImpactFeedbackStyle.Medium)
      onPress()
    }
  }

  const base: ViewStyle = {
    height: HEIGHT[size],
    borderRadius: RADIUS[size],
    paddingHorizontal: size === 'xl' ? 20 : size === 'lg' ? 16 : size === 'xs' ? 10 : 12,
    alignItems: 'center', 
    justifyContent: 'center',
    flexDirection: 'row',
    gap: 8,
  }
  
  const text: TextStyle = { 
    color: theme.color.foreground, 
    fontSize: size === 'xl' ? 18 : size === 'xs' ? 14 : 16, 
    fontWeight: '600' 
  }

  if (variant === 'premium' || variant === 'hero') {
    return (
      <TouchableOpacity 
        onPressIn={handlePressIn}
        onPressOut={handlePressOut}
        onPress={handlePress}
        disabled={disabled || loading} 
        activeOpacity={1} 
        accessibilityLabel={accessibilityLabel}
        style={[{ height: HEIGHT[size], borderRadius: RADIUS[size], overflow: 'hidden', alignSelf: 'stretch' }]}
      > 
        <Animated.View style={{ 
          width: '100%', 
          height: '100%', 
          transform: [{ scale: scaleValue }],
          alignItems: 'center',
          justifyContent: 'center',
          flexDirection: 'row',
          paddingHorizontal: size === 'xl' ? 20 : size === 'lg' ? 16 : size === 'xs' ? 10 : 12
        }}>
          <LinearGradient
            pointerEvents="none"
            colors={[theme.color.primary, theme.color.primaryLight, 'hsl(260,100%,80%)']}
            start={{ x: 0, y: 0 }}
            end={{ x: 1, y: 1 }}
            style={{ 
              position: 'absolute', 
              left: 0, 
              right: 0, 
              top: 0, 
              bottom: 0,
              borderRadius: RADIUS[size],
              ...(theme.shadow.premium as any)
            }}
          />
          {loading && (
            <LoadingSpinner size={20} color="#fff" style={{ marginRight: 8 }} />
          )}
          {iconLeft && (
            <>
              {iconLeft}
            </>
          )}
          <Text style={{ ...text, color: '#fff' }}>{title}</Text>
        </Animated.View>
      </TouchableOpacity>
    )
  }

  const bg: ViewStyle = (() => {
    switch (variant) {
      case 'default': return { backgroundColor: theme.color.primary, ...(theme.shadow.md as any) }
      case 'secondary': return { backgroundColor: theme.color.secondary }
      case 'card': return { backgroundColor: theme.dark ? theme.color.secondary : theme.color.accent, borderWidth: 0, borderColor: 'transparent' }
      case 'danger': return { backgroundColor: theme.color.error }
      case 'dangerSoft': return { backgroundColor: withAlpha(theme.color.error, theme.dark ? 0.22 : 0.12) }
      case 'outline': return { backgroundColor: theme.color.background, borderWidth: 1, borderColor: theme.color.border }
      case 'ghost': return { backgroundColor: 'transparent' }
      case 'glass': return { backgroundColor: 'rgba(255,255,255,0.08)', borderWidth: 1, borderColor: 'rgba(255,255,255,0.2)' }
      case 'link': return { backgroundColor: 'transparent' }
      default: return { backgroundColor: theme.color.primary }
    }
  })()

  const color = (() => {
    if (variant === 'default' || variant === 'danger') return '#fff'
    if (variant === 'dangerSoft') return theme.color.error
    if (variant === 'link') return theme.color.mutedForeground
    if (variant === 'card') return theme.color.cardForeground
    if (variant === 'secondary') return theme.color.foreground
    return theme.color.foreground
  })()

  return (
    <TouchableOpacity 
      onPressIn={handlePressIn}
      onPressOut={handlePressOut}
      onPress={handlePress}
      disabled={disabled || loading} 
      activeOpacity={1} 
      accessibilityLabel={accessibilityLabel}
      style={[disabled && { opacity: 0.5 }]}
    > 
      <Animated.View style={[base, bg, { transform: [{ scale: scaleValue }], ...(fullWidth ? { width: '100%' } : {}) }]}>
        {loading && (
          <LoadingSpinner size={16} color={color} style={{ marginRight: 4 }} />
        )}
        {iconLeft && (
          <>
            {iconLeft}
          </>
        )}
        <Text style={{ ...text, color }}>{title}</Text>
      </Animated.View>
    </TouchableOpacity>
  )
}
