import React from 'react'
import { TouchableOpacity, Text } from 'react-native'
import { useTheme } from '../../providers/ThemeProvider'

export interface QuickReplyChipProps { label: string; onPress: () => void; testID?: string }

const QuickReplyChip: React.FC<QuickReplyChipProps> = ({ label, onPress, testID }) => {
  const { theme } = useTheme()
  return (
    <TouchableOpacity testID={testID} onPress={onPress} accessibilityRole="button" accessibilityLabel={`Quick reply ${label}`} style={{ paddingHorizontal: 12, paddingVertical: 8, borderRadius: 999, backgroundColor: theme.color.muted, borderWidth: 1, borderColor: theme.color.border, minHeight: 44 }}>
      <Text style={{ color: theme.color.cardForeground, fontWeight: '600' }}>{label}</Text>
    </TouchableOpacity>
  )
}

export default React.memo(QuickReplyChip)


