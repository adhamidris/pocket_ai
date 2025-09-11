import React from 'react'
import { TouchableOpacity } from 'react-native'
import Box from '../../ui/Box'
import { Text } from '../../ui/Text'
import { tokens } from '../../ui/tokens'

export interface QuickActionItem {
  label: string
  deeplink: string
  testID: string
}

export interface QuickActionsProps {
  actions: QuickActionItem[]
  onPress: (deeplink: string) => void
  testID?: string
}

const QuickActions: React.FC<QuickActionsProps> = ({ actions, onPress, testID }) => {
  return (
    <Box testID={testID} accessibilityLabel="Quick Actions" accessibilityRole="summary">
      <Box mb={8}>
        <Text size={14} weight="600" color={tokens.colors.cardForeground}>Quick Actions</Text>
      </Box>
      <Box row gap={8}>
        {actions.map((a) => (
          <TouchableOpacity
            key={a.testID}
            testID={a.testID}
            accessibilityLabel={a.label}
            accessibilityRole="button"
            activeOpacity={0.8}
            onPress={() => onPress(a.deeplink)}
            style={{
              paddingHorizontal: 12,
              paddingVertical: 8,
              borderRadius: 12,
              borderWidth: 1,
              borderColor: tokens.colors.border,
              backgroundColor: tokens.colors.card,
              marginRight: 8,
              minHeight: 44,
              alignItems: 'center',
              justifyContent: 'center',
            }}
            hitSlop={{ top: 8, bottom: 8, left: 8, right: 8 }}
          >
            <Text size={12} weight="600" color={tokens.colors.primary}>{a.label}</Text>
          </TouchableOpacity>
        ))}
      </Box>
    </Box>
  )
}

export default QuickActions


