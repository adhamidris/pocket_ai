import React from 'react'
import Box from '../../ui/Box'
import { Text } from '../../ui/Text'
import { tokens } from '../../ui/tokens'
import { MessageCircle } from 'lucide-react-native'

export interface EmptyStateProps {
  message: string
  testID?: string
}

const EmptyState: React.FC<EmptyStateProps> = ({ message, testID }) => {
  return (
    <Box testID={testID} align="center" justify="center" p={16} radius={16} style={{ backgroundColor: tokens.colors.card, borderWidth: 1, borderColor: tokens.colors.border }}>
      <Box mb={8}><MessageCircle size={20} color={tokens.colors.mutedForeground as any} /></Box>
      <Text size={13} color={tokens.colors.mutedForeground}>{message}</Text>
    </Box>
  )
}

export default React.memo(EmptyState)


