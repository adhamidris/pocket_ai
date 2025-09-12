import React, { useRef, useEffect } from 'react'
import { Animated, View } from 'react-native'
import { useTheme } from '../../providers/ThemeProvider'

interface PulseAnimationProps {
  children: React.ReactNode
  duration?: number
  minOpacity?: number
  maxOpacity?: number
  style?: any
}

export const PulseAnimation: React.FC<PulseAnimationProps> = ({ 
  children, 
  duration = 1500,
  minOpacity = 0.3,
  maxOpacity = 1,
  style 
}) => {
  const pulseValue = useRef(new Animated.Value(minOpacity)).current

  useEffect(() => {
    const pulseAnimation = Animated.loop(
      Animated.sequence([
        Animated.timing(pulseValue, {
          toValue: maxOpacity,
          duration: duration / 2,
          useNativeDriver: true,
        }),
        Animated.timing(pulseValue, {
          toValue: minOpacity,
          duration: duration / 2,
          useNativeDriver: true,
        }),
      ])
    )
    
    pulseAnimation.start()
    
    return () => pulseAnimation.stop()
  }, [pulseValue, duration, minOpacity, maxOpacity])

  return (
    <Animated.View style={[{ opacity: pulseValue }, style]}>
      {children}
    </Animated.View>
  )
}

interface TypingDotsProps {
  color?: string
  size?: number
}

export const TypingDots: React.FC<TypingDotsProps> = ({ 
  color,
  size = 6 
}) => {
  const { theme } = useTheme()
  const dot1 = useRef(new Animated.Value(0.3)).current
  const dot2 = useRef(new Animated.Value(0.3)).current
  const dot3 = useRef(new Animated.Value(0.3)).current

  useEffect(() => {
    const createDotAnimation = (dotValue: Animated.Value, delay: number) => {
      return Animated.loop(
        Animated.sequence([
          Animated.timing(dotValue, {
            toValue: 1,
            duration: 400,
            delay,
            useNativeDriver: true,
          }),
          Animated.timing(dotValue, {
            toValue: 0.3,
            duration: 400,
            useNativeDriver: true,
          }),
        ])
      )
    }

    const animation1 = createDotAnimation(dot1, 0)
    const animation2 = createDotAnimation(dot2, 200)
    const animation3 = createDotAnimation(dot3, 400)

    animation1.start()
    animation2.start()
    animation3.start()

    return () => {
      animation1.stop()
      animation2.stop()
      animation3.stop()
    }
  }, [dot1, dot2, dot3])

  const dotStyle = {
    width: size,
    height: size,
    borderRadius: size / 2,
    backgroundColor: color || theme.color.mutedForeground,
  }

  return (
    <View style={{ flexDirection: 'row', alignItems: 'center', gap: 4 }}>
      <Animated.View style={[dotStyle, { opacity: dot1 }]} />
      <Animated.View style={[dotStyle, { opacity: dot2 }]} />
      <Animated.View style={[dotStyle, { opacity: dot3 }]} />
    </View>
  )
}
