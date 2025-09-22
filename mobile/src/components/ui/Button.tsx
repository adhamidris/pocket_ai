import React, { useRef } from 'react'
import { TouchableOpacity, Text, ViewStyle, TextStyle, Animated } from 'react-native'
import { LinearGradient } from 'expo-linear-gradient'
import * as Haptics from 'expo-haptics'
import { useTheme } from '../../providers/ThemeProvider'
import { LoadingSpinner } from './LoadingSpinner'

type Variant = 'default' | 'secondary' | 'outline' | 'ghost' | 'link' | 'premium' | 'hero' | 'glass' | 'danger' | 'dangerSoft' | 'success' | 'successSoft' | 'card'
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

  const desaturateHsl = (c: string, ratio: number) => {
    // Reduce saturation by a ratio (0-1). Only handles hsl/hsla strings; returns original if not hsl(a)
    const m = c.match(/^hsl\(\s*(\d+(?:\.\d+)?)\s*,\s*(\d+(?:\.\d+)?)%\s*,\s*(\d+(?:\.\d+)?)%\s*\)$/i)
    if (m) {
      const h = parseFloat(m[1])
      const s = Math.max(0, Math.min(100, parseFloat(m[2]) * ratio))
      const l = parseFloat(m[3])
      return `hsl(${h},${s}%,${l}%)`
    }
    const m2 = c.match(/^hsla\(\s*(\d+(?:\.\d+)?)\s*,\s*(\d+(?:\.\d+)?)%\s*,\s*(\d+(?:\.\d+)?)%\s*,\s*([\d.]+)\s*\)$/i)
    if (m2) {
      const h = parseFloat(m2[1])
      const s = Math.max(0, Math.min(100, parseFloat(m2[2]) * ratio))
      const l = parseFloat(m2[3])
      const a = parseFloat(m2[4])
      return `hsla(${h},${s}%,${l}%,${a})`
    }
    return c
  }

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
      case 'success': return { backgroundColor: desaturateHsl(theme.color.success, theme.dark ? 0.88 : 0.82) }
      case 'successSoft': return { backgroundColor: withAlpha(desaturateHsl(theme.color.success, theme.dark ? 0.70 : 0.60), theme.dark ? 0.20 : 0.10) }
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
    if (variant === 'default' || variant === 'danger' || variant === 'success') return '#fff'
    if (variant === 'dangerSoft') return theme.color.error
    if (variant === 'successSoft') return theme.color.success
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
