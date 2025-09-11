import React from 'react'
import Box from '../../ui/Box'
import { Text } from '../../ui/Text'
import { tokens } from '../../ui/tokens'

export interface PerformanceStatProps {
  label: string
  value: string | number
  delta?: number
  testID?: string
}

const PerformanceStat: React.FC<PerformanceStatProps> = ({ label, value, delta, testID }) => {
  const deltaColor = delta == null ? tokens.colors.mutedForeground : delta >= 0 ? tokens.colors.success : tokens.colors.error
  const deltaPrefix = delta == null ? '' : delta > 0 ? '▲' : delta < 0 ? '▼' : '—'
  const deltaText = delta == null ? '' : `${deltaPrefix} ${Math.abs(delta)}%`
  return (
    <Box testID={testID} p={12} radius={12} style={{ backgroundColor: tokens.colors.card, borderWidth: 1, borderColor: tokens.colors.border }}>
      <Text size={12} color={tokens.colors.mutedForeground}>{label}</Text>
      <Box row align="center" justify="space-between" mt={4}>
        <Text size={18} weight="700" color={tokens.colors.cardForeground}>{String(value)}</Text>
        {!!deltaText && <Text size={12} weight="600" color={deltaColor}>{deltaText}</Text>}
      </Box>
    </Box>
  )
}

export default React.memo(PerformanceStat)



