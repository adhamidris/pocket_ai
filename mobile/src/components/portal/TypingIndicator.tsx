import React from 'react'
import { View, Text } from 'react-native'
import { useTheme } from '../../providers/ThemeProvider'

export interface TypingIndicatorProps { text?: string; testID?: string }

const TypingIndicator: React.FC<TypingIndicatorProps> = ({ text = 'Agent is typing…', testID }) => {
  const { theme } = useTheme()
  return (
    <View testID={testID} accessibilityLiveRegion="polite" style={{ paddingHorizontal: 12, paddingVertical: 8 }}>
      <Text style={{ color: theme.color.mutedForeground }}>{text}</Text>
    </View>
  )
}

export default React.memo(TypingIndicator)


