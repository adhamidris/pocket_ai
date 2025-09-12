import React, { useRef, useEffect } from 'react'
import { Animated, TouchableOpacity, ViewStyle, Platform } from 'react-native'
import { useTheme } from '../../providers/ThemeProvider'

interface AnimatedCardProps {
  children: React.ReactNode
  style?: ViewStyle
  onPress?: () => void
  delay?: number
  animationType?: 'fadeIn' | 'slideUp' | 'scale'
  variant?: 'default' | 'premium' | 'flat'
}

export const AnimatedCard: React.FC<AnimatedCardProps> = ({ 
  children, 
  style, 
  onPress,
  delay = 0,
  animationType = 'fadeIn',
  variant = 'default'
}) => {
  const { theme } = useTheme()
  const animatedValue = useRef(new Animated.Value(0)).current
  const scaleValue = useRef(new Animated.Value(1)).current

  useEffect(() => {
    const animation = () => {
      switch (animationType) {
        case 'slideUp':
          animatedValue.setValue(30)
          Animated.timing(animatedValue, {
            toValue: 0,
            duration: 600,
            delay,
            useNativeDriver: true,
          }).start()
          break
        case 'scale':
          animatedValue.setValue(0.8)
          Animated.timing(animatedValue, {
            toValue: 1,
            duration: 500,
            delay,
            useNativeDriver: true,
          }).start()
          break
        default: // fadeIn
          Animated.timing(animatedValue, {
            toValue: 1,
            duration: 500,
            delay,
            useNativeDriver: true,
          }).start()
      }
    }

    animation()
  }, [animatedValue, delay, animationType])

  const handlePressIn = () => {
    Animated.spring(scaleValue, {
      toValue: 0.98,
      useNativeDriver: true,
    }).start()
  }

  const handlePressOut = () => {
    Animated.spring(scaleValue, {
      toValue: 1,
      useNativeDriver: true,
    }).start()
  }

  const getAnimatedStyle = () => {
    switch (animationType) {
      case 'slideUp':
        return {
          opacity: animatedValue.interpolate({
            inputRange: [0, 30],
            outputRange: [1, 0],
          }),
          transform: [
            {
              translateY: animatedValue,
            },
            {
              scale: scaleValue,
            }
          ],
        }
      case 'scale':
        return {
          transform: [
            {
              scale: animatedValue,
            },
            {
              scale: scaleValue,
            }
          ],
        }
      default:
        return {
          opacity: animatedValue,
          transform: [
            {
              scale: scaleValue,
            }
          ],
        }
    }
  }

  const isFlat = variant === 'flat'
  const usePremium = variant === 'premium'
  const shadow = usePremium ? theme.shadow.premium : theme.shadow.md

  const baseStyle: ViewStyle = {
    backgroundColor: theme.color.card,
    borderRadius: theme.radius.xl,
    padding: 20,
    borderWidth: isFlat ? 0 : 1,
    borderColor: isFlat ? 'transparent' : theme.color.border,
    ...(Platform.OS === 'ios'
      ? (isFlat
          ? { shadowColor: 'transparent', shadowOpacity: 0, shadowRadius: 0, shadowOffset: { width: 0, height: 0 } }
          : { shadowColor: shadow.ios.color, shadowOpacity: shadow.ios.opacity, shadowRadius: shadow.ios.radius, shadowOffset: { width: 0, height: shadow.ios.offsetY } }
        )
      : (isFlat
          ? { elevation: 0 }
          : { elevation: shadow.androidElevation }
        )
    )
  }

  const cardStyle = { ...baseStyle, ...style }

  if (onPress) {
    return (
      <TouchableOpacity
        onPressIn={handlePressIn}
        onPressOut={handlePressOut}
        onPress={onPress}
        activeOpacity={1}
      >
        <Animated.View style={[cardStyle, getAnimatedStyle()]}>
          {children}
        </Animated.View>
      </TouchableOpacity>
    )
  }

  return (
    <Animated.View style={[cardStyle, getAnimatedStyle()]}>
      {children}
    </Animated.View>
  )
}
