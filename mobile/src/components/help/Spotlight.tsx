import React from 'react'
import { Pressable, View } from 'react-native'
import { useTheme } from '../../providers/ThemeProvider'

export interface Rect { x: number; y: number; width: number; height: number }

export const Spotlight: React.FC<{ visible: boolean; targetFrame: Rect; onDismiss?: () => void }> = ({ visible, targetFrame, onDismiss }) => {
  const { theme } = useTheme()
  if (!visible) return null
  return (
    <Pressable
      onPress={onDismiss}
      accessibilityRole="button"
      accessibilityLabel="Dismiss highlight"
      style={{ position: 'absolute', left: 0, top: 0, right: 0, bottom: 0, backgroundColor: 'rgba(0,0,0,0.5)' }}
    >
      <View
        pointerEvents="none"
        style={{
          position: 'absolute',
          left: targetFrame.x - 4,
          top: targetFrame.y - 4,
          width: targetFrame.width + 8,
          height: targetFrame.height + 8,
          borderRadius: 12,
          borderWidth: 2,
          borderColor: theme.color.ring,
          backgroundColor: 'rgba(255,255,255,0.06)'
        }}
      />
    </Pressable>
  )
}


