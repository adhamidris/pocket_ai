import React, { useRef, useEffect } from 'react'
import { View, Animated, Easing } from 'react-native'
import { useTheme } from '../../providers/ThemeProvider'

interface LoadingSpinnerProps {
  size?: number
  color?: string
  style?: any
}

export const LoadingSpinner: React.FC<LoadingSpinnerProps> = ({ 
  size = 24, 
  color,
  style 
}) => {
  const { theme } = useTheme()
  const spinValue = useRef(new Animated.Value(0)).current

  useEffect(() => {
    const spinAnimation = Animated.loop(
      Animated.timing(spinValue, {
        toValue: 1,
        duration: 1000,
        easing: Easing.linear,
        useNativeDriver: true,
      })
    )
    
    spinAnimation.start()
    
    return () => spinAnimation.stop()
  }, [spinValue])

  const spin = spinValue.interpolate({
    inputRange: [0, 1],
    outputRange: ['0deg', '360deg'],
  })

  return (
    <View style={[{ alignItems: 'center', justifyContent: 'center' }, style]}>
      <Animated.View
        style={{
          width: size,
          height: size,
          borderWidth: 2,
          borderColor: color || theme.color.primary,
          borderTopColor: 'transparent',
          borderRadius: size / 2,
          transform: [{ rotate: spin }],
        }}
      />
    </View>
  )
}
