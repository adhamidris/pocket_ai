import React, { useEffect, useState } from 'react'
import { View, Text, Platform } from 'react-native'
import NetInfo from '@react-native-community/netinfo'
import { useTheme } from '@/providers/ThemeProvider'

export const OfflineBanner: React.FC = () => {
  const { theme } = useTheme()
  const [offline, setOffline] = useState(false)

  useEffect(() => {
    const unsub = NetInfo.addEventListener(state => {
      const isConnected = state.isConnected && state.isInternetReachable !== false
      setOffline(!isConnected)
    })
    return () => unsub()
  }, [])

  if (!offline) return null
  return (
    <View pointerEvents="box-none" style={{
      position: 'absolute', top: Platform.select({ ios: 50, android: 20, default: 20 }), left: 12, right: 12,
      backgroundColor: theme.color.muted, borderColor: theme.color.border, borderWidth: 1,
      borderRadius: 12, paddingVertical: 10, paddingHorizontal: 12, zIndex: 9999,
    }}>
      <Text style={{ color: theme.color.mutedForeground, textAlign: 'center' }}>
        You’re offline. Some actions are unavailable.
      </Text>
    </View>
  )
}
