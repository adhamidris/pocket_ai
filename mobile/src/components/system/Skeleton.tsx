import React, { useEffect, useRef } from 'react'
import { Animated, View, ViewStyle } from 'react-native'

export const Skeleton: React.FC<{ width?: number | string; height?: number; radius?: number; style?: ViewStyle }>
  = ({ width = '100%', height = 16, radius = 8, style }) => {
  const shimmer = useRef(new Animated.Value(0)).current
  useEffect(() => {
    const loop = Animated.loop(
      Animated.sequence([
        Animated.timing(shimmer, { toValue: 1, duration: 1200, useNativeDriver: true }),
        Animated.timing(shimmer, { toValue: 0, duration: 1200, useNativeDriver: true }),
      ])
    )
    loop.start()
    return () => loop.stop()
  }, [shimmer])

  const translateX = shimmer.interpolate({ inputRange: [0, 1], outputRange: [-50, 50] })

  return (
    <View style={[{ width, height, borderRadius: radius, backgroundColor: 'rgba(255,255,255,0.08)', overflow: 'hidden' }, style]}>
      <Animated.View style={{ position: 'absolute', left: 0, top: 0, bottom: 0, width: '40%', transform: [{ translateX }], backgroundColor: 'rgba(255,255,255,0.12)' }} />
    </View>
  )
}

