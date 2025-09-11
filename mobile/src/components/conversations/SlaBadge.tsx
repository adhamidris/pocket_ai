import React from 'react'
import Box from '../../ui/Box'
import { Text } from '../../ui/Text'
import { tokens } from '../../ui/tokens'
import { SLAState } from '../../types/conversations'

export interface SlaBadgeProps {
  state: SLAState
  testID?: string
}

const SlaBadge: React.FC<SlaBadgeProps> = ({ state, testID }) => {
  const color = state === 'ok' ? tokens.colors.success : state === 'risk' ? tokens.colors.warning : tokens.colors.error
  const label = state === 'ok' ? 'SLA OK' : state === 'risk' ? 'SLA Risk' : 'SLA Breach'
  return (
    <Box testID={testID} px={8} py={4} radius={12} style={{ backgroundColor: color + '20', borderWidth: 1, borderColor: color }}>
      <Text size={11} weight="600" color={color}>{label}</Text>
    </Box>
  )
}

export default React.memo(SlaBadge)


