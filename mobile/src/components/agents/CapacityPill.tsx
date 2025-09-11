import React from 'react'
import Box from '../../ui/Box'
import { Text } from '../../ui/Text'
import { tokens } from '../../ui/tokens'
import { CapacityHint } from '../../types/agents'

export interface CapacityPillProps {
  capacity?: CapacityHint
  assignedOpen?: number
  testID?: string
}

const CapacityPill: React.FC<CapacityPillProps> = ({ capacity, assignedOpen, testID }) => {
  const concurrent = capacity?.concurrent ?? 0
  const backlog = capacity?.backlogMax
  const value = backlog != null ? `${assignedOpen ?? 0}/${concurrent} · ≤${backlog}` : `${assignedOpen ?? 0}/${concurrent}`
  return (
    <Box testID={testID} px={10} py={6} radius={12} style={{ backgroundColor: tokens.colors.muted, borderWidth: 1, borderColor: tokens.colors.border, minHeight: 32, justifyContent: 'center' }} accessibilityLabel={`Capacity ${value}`} accessibilityRole="text">
      <Text size={12} weight="600" color={tokens.colors.mutedForeground}>{value}</Text>
    </Box>
  )
}

export default React.memo(CapacityPill)



