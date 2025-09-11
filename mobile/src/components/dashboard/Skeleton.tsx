import React, { useEffect, useRef } from 'react'
import { Animated, ViewStyle } from 'react-native'
import Box from '../../ui/Box'
import { tokens } from '../../ui/tokens'

export interface SkeletonProps {
  variant?: 'rect' | 'text'
  width?: number
  height?: number
  radius?: number
  style?: ViewStyle
  testID?: string
}

const Skeleton: React.FC<SkeletonProps> = ({ variant = 'rect', width, height, radius, style, testID }) => {
  const opacity = useRef(new Animated.Value(0.6)).current

  useEffect(() => {
    const loop = Animated.loop(
      Animated.sequence([
        Animated.timing(opacity, { toValue: 0.3, duration: 700, useNativeDriver: true }),
        Animated.timing(opacity, { toValue: 0.6, duration: 700, useNativeDriver: true }),
      ])
    )
    loop.start()
    return () => loop.stop()
  }, [opacity])

  const resolvedHeight = height ?? (variant === 'text' ? 14 : 16)
  const resolvedRadius = radius ?? (variant === 'text' ? 6 : 8)

  return (
    <Animated.View style={{ opacity }}>
      <Box
        testID={testID}
        w={width}
        h={resolvedHeight}
        radius={resolvedRadius}
        style={[{ backgroundColor: tokens.colors.muted }, style]}
      />
    </Animated.View>
  )
}

export default Skeleton


