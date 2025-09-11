import React from 'react'
import { TouchableOpacity } from 'react-native'
import Box from '../../ui/Box'
import { Text } from '../../ui/Text'
import { tokens } from '../../ui/tokens'

export interface SortBarProps {
  sort: 'recent' | 'oldest' | 'priority'
  onChange: (s: 'recent' | 'oldest' | 'priority') => void
  testID?: string
}

const SortBar: React.FC<SortBarProps> = ({ sort, onChange, testID }) => {
  const options: Array<{ key: 'recent' | 'oldest' | 'priority'; label: string }> = [
    { key: 'recent', label: 'Recent' },
    { key: 'oldest', label: 'Oldest' },
    { key: 'priority', label: 'Priority' },
  ]
  return (
    <Box testID={testID} row gap={8}>
      {options.map((o) => (
        <TouchableOpacity
          key={o.key}
          onPress={() => onChange(o.key)}
          accessibilityLabel={`Sort ${o.label}`}
          accessibilityRole="button"
          style={{
            paddingHorizontal: 12,
            paddingVertical: 8,
            borderRadius: 12,
            backgroundColor: sort === o.key ? tokens.colors.card : 'transparent',
            borderWidth: 1,
            borderColor: sort === o.key ? tokens.colors.primary : tokens.colors.border,
            minHeight: 40,
            justifyContent: 'center',
          }}
        >
          <Text size={12} weight="600" color={sort === o.key ? tokens.colors.primary : tokens.colors.mutedForeground}>{o.label}</Text>
        </TouchableOpacity>
      ))}
    </Box>
  )
}

export default React.memo(SortBar)


