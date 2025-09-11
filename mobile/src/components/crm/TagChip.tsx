import React from 'react'
import { TouchableOpacity } from 'react-native'
import Box from '../../ui/Box'
import { Text } from '../../ui/Text'
import { tokens } from '../../ui/tokens'

export interface TagChipProps {
  label: string
  onPress?: () => void
  selected?: boolean
  testID?: string
}

const TagChip: React.FC<TagChipProps> = ({ label, onPress, selected, testID }) => {
  return (
    <TouchableOpacity
      testID={testID}
      onPress={onPress}
      accessibilityLabel={`Tag ${label}`}
      accessibilityRole="button"
      hitSlop={{ top: 8, bottom: 8, left: 8, right: 8 }}
      style={{
        paddingHorizontal: 12,
        paddingVertical: 8,
        borderRadius: 12,
        borderWidth: 1,
        borderColor: selected ? tokens.colors.primary : tokens.colors.border,
        backgroundColor: selected ? tokens.colors.card : 'transparent',
        minHeight: 44,
        justifyContent: 'center',
      }}
    >
      <Text size={12} weight="600" color={selected ? tokens.colors.primary : tokens.colors.mutedForeground}>{label}</Text>
    </TouchableOpacity>
  )
}

export default React.memo(TagChip)


