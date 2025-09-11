import React from 'react'
import { TouchableOpacity } from 'react-native'
import { HelpCircle } from 'lucide-react-native'
import { useTheme } from '../../providers/ThemeProvider'

export const HelpButton: React.FC<{ onPress?: () => void; testID?: string; accessibilityLabel?: string }> = ({ onPress, testID = 'help-button', accessibilityLabel = 'Open help' }) => {
  const { theme } = useTheme()
  return (
    <TouchableOpacity
      onPress={onPress}
      activeOpacity={0.9}
      accessibilityLabel={accessibilityLabel}
      testID={testID}
      style={{
        position: 'absolute',
        right: 24,
        bottom: 24,
        width: 56,
        height: 56,
        borderRadius: 28,
        backgroundColor: theme.color.primary,
        alignItems: 'center',
        justifyContent: 'center',
        ...(theme.shadow.premium as any)
      }}
    >
      <HelpCircle size={24} color={'#fff'} />
    </TouchableOpacity>
  )
}


