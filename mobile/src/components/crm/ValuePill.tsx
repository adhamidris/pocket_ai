import React from 'react'
import Box from '../../ui/Box'
import { Text } from '../../ui/Text'
import { tokens } from '../../ui/tokens'

export interface ValuePillProps {
  value?: number
  testID?: string
}

const format = (v?: number) => (typeof v === 'number' ? `$${v.toLocaleString()}` : '—')

const ValuePill: React.FC<ValuePillProps> = ({ value, testID }) => {
  return (
    <Box testID={testID} px={10} py={6} radius={12} style={{ backgroundColor: tokens.colors.accent, borderWidth: 1, borderColor: tokens.colors.border }}>
      <Text size={12} weight="600" color={tokens.colors.cardForeground}>{format(value)}</Text>
    </Box>
  )
}

export default React.memo(ValuePill)


