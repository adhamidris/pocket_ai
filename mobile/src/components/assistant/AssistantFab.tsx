import React from 'react'
import { TouchableOpacity, View, Text } from 'react-native'
import { useTheme } from '../../providers/ThemeProvider'

export const AssistantFab: React.FC<{ onPress: () => void; testID?: string }>
  = ({ onPress, testID }) => {
  const { theme } = useTheme()
  return (
    <TouchableOpacity onPress={onPress} testID={testID ?? 'assistant-fab'}
      accessibilityLabel="Open Assistant" accessibilityRole="button"
      activeOpacity={0.9}
      style={{ position: 'absolute', right: 20, bottom: 100, zIndex: 1000 }}
    >
      <View style={{ width: 60, height: 60, borderRadius: 30, backgroundColor: theme.color.primary, alignItems: 'center', justifyContent: 'center', ...(theme.shadow.premium as any) }}>
        <Text style={{ color: '#fff', fontWeight: '800' }}>AI</Text>
      </View>
    </TouchableOpacity>
  )
}

export default AssistantFab


