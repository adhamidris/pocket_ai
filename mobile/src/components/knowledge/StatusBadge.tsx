import React from 'react'
import Box from '../../ui/Box'
import { Text } from '../../ui/Text'
import { tokens } from '../../ui/tokens'
import { SourceStatus } from '../../types/knowledge'

export interface KnowledgeStatusBadgeProps {
  status: SourceStatus
  testID?: string
}

const colorFor = (s: SourceStatus) => {
  switch (s) {
    case 'trained': return tokens.colors.success
    case 'training': return tokens.colors.warning
    case 'error': return tokens.colors.error
    default: return tokens.colors.mutedForeground
  }
}

const labelFor = (s: SourceStatus) => s.toUpperCase()

const StatusBadge: React.FC<KnowledgeStatusBadgeProps> = ({ status, testID }) => {
  const c = colorFor(status)
  return (
    <Box testID={testID} px={8} py={4} radius={12} style={{ backgroundColor: (c as string) + '20', borderWidth: 1, borderColor: c, minHeight: 28, justifyContent: 'center' }} accessibilityLabel={`Status ${status}`} accessibilityRole="text">
      <Text size={11} weight="700" color={c}>{labelFor(status)}</Text>
    </Box>
  )
}

export default React.memo(StatusBadge)


