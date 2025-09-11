import React from 'react'
import Box from '../../ui/Box'
import { Text } from '../../ui/Text'
import { tokens } from '../../ui/tokens'
import { ConsentState } from '../../types/crm'

export interface ConsentBadgeProps {
  state: ConsentState
  testID?: string
}

const colorFor = (state: ConsentState) => {
  switch (state) {
    case 'granted': return tokens.colors.success
    case 'denied': return tokens.colors.error
    case 'withdrawn': return tokens.colors.warning
    default: return tokens.colors.mutedForeground
  }
}

const labelFor = (state: ConsentState) => state.charAt(0).toUpperCase() + state.slice(1)

const ConsentBadge: React.FC<ConsentBadgeProps> = ({ state, testID }) => {
  const c = colorFor(state)
  return (
    <Box
      testID={testID}
      px={8}
      py={4}
      radius={12}
      style={{ backgroundColor: c + '20', borderWidth: 1, borderColor: c, minHeight: 32, justifyContent: 'center' }}
      accessibilityLabel={`Consent ${labelFor(state)}`}
      accessibilityRole="text"
    >
      <Text size={11} weight="600" color={c}>{labelFor(state)}</Text>
    </Box>
  )
}

export default React.memo(ConsentBadge)


