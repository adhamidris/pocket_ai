import React, { useRef, useEffect } from 'react'
import { Animated, TouchableOpacity, ViewStyle } from 'react-native'
import { useTheme } from '../../providers/ThemeProvider'

interface AnimatedCardProps {
  children: React.ReactNode
  style?: ViewStyle
  onPress?: () => void
  delay?: number
  animationType?: 'fadeIn' | 'slideUp' | 'scale'
}

export const AnimatedCard: React.FC<AnimatedCardProps> = ({ 
  children, 
  style, 
  onPress,
  delay = 0,
  animationType = 'fadeIn'
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

  const cardStyle = {
    backgroundColor: theme.color.card,
    borderRadius: theme.radius.xl,
    padding: 20,
    borderWidth: 1,
    borderColor: theme.color.border,
    ...theme.shadow.premium,
    ...style,
  }

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
