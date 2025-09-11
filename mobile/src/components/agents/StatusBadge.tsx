import React from 'react'
import Box from '../../ui/Box'
import { Text } from '../../ui/Text'
import { tokens } from '../../ui/tokens'
import { AgentStatus } from '../../types/agents'

export interface StatusBadgeProps {
  status: AgentStatus
  testID?: string
}

const colorFor = (s: AgentStatus) => {
  switch (s) {
    case 'online': return tokens.colors.success
    case 'away': return tokens.colors.warning
    case 'offline': return tokens.colors.mutedForeground
    case 'dnd': return tokens.colors.error
    default: return tokens.colors.mutedForeground
  }
}

const labelFor = (s: AgentStatus) => s.toUpperCase()

const StatusBadge: React.FC<StatusBadgeProps> = ({ status, testID }) => {
  const c = colorFor(status)
  return (
    <Box
      testID={testID}
      px={8}
      py={4}
      radius={12}
      style={{ backgroundColor: c + '20', borderWidth: 1, borderColor: c, minHeight: 32, justifyContent: 'center' }}
      accessibilityLabel={`Status ${status}`}
      accessibilityRole="text"
    >
      <Text size={11} weight="700" color={c}>{labelFor(status)}</Text>
    </Box>
  )
}

export default React.memo(StatusBadge)



