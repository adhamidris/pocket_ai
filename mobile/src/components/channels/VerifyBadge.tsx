import React from 'react'
import Box from '../../ui/Box'
import { Text } from '../../ui/Text'
import { tokens } from '../../ui/tokens'
import { VerifyState } from '../../types/channels'

export interface VerifyBadgeProps {
  state: VerifyState
  message?: string
  testID?: string
}

const colorFor = (s: VerifyState) => {
  switch (s) {
    case 'verified': return tokens.colors.success
    case 'verifying': return tokens.colors.warning
    case 'failed': return tokens.colors.error
    default: return tokens.colors.mutedForeground
  }
}

const labelFor = (s: VerifyState) => s.toUpperCase()

const VerifyBadge: React.FC<VerifyBadgeProps> = ({ state, message, testID }) => {
  const c = colorFor(state)
  return (
    <Box testID={testID} px={8} py={4} radius={12} style={{ backgroundColor: (c as string) + '20', borderWidth: 1, borderColor: c, minHeight: 28, justifyContent: 'center' }} accessibilityLabel={`Verify state ${state}${message ? `: ${message}` : ''}`} accessibilityRole="text">
      <Text size={11} weight="700" color={c}>{labelFor(state)}</Text>
    </Box>
  )
}

export default React.memo(VerifyBadge)



