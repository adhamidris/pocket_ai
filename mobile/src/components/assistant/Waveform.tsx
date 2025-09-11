import React, { useEffect, useRef } from 'react'
import { Animated, View } from 'react-native'
import { useTheme } from '../../providers/ThemeProvider'

export const Waveform: React.FC<{ bars?: number }> = ({ bars = 16 }) => {
  const { theme } = useTheme()
  const anims = useRef([...Array(bars)].map(() => new Animated.Value(0.3))).current

  useEffect(() => {
    let isMounted = true
    const timers: any[] = []
    anims.forEach((a, i) => {
      const start = () => {
        if (!isMounted) return
        Animated.sequence([
          Animated.timing(a, { toValue: 1, duration: 500 + (i * 20) % 200, useNativeDriver: true }),
          Animated.timing(a, { toValue: 0.3, duration: 500 + (i * 20) % 200, useNativeDriver: true }),
        ]).start(() => { timers[i] = setTimeout(start, 50) })
      }
      timers[i] = setTimeout(start, 50)
    })
    return () => { isMounted = false; timers.forEach(t => clearTimeout(t)) }
  }, [])

  return (
    <View style={{ flexDirection: 'row', alignItems: 'flex-end', gap: 4 }}>
      {anims.map((a, i) => (
        <Animated.View key={i} style={{ width: 4, height: 24, backgroundColor: theme.color.primary, opacity: a, borderRadius: 2 }} />
      ))}
    </View>
  )
}

export default Waveform


