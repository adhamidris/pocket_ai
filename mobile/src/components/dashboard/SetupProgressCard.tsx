import React from 'react'
import Box from '../../ui/Box'
import { Text } from '../../ui/Text'
import { tokens } from '../../ui/tokens'
import { SetupStep } from '../../types/dashboard'

export interface SetupProgressCardProps {
  steps: SetupStep[]
  testID?: string
}

const SetupProgressCard: React.FC<SetupProgressCardProps> = ({ steps, testID }) => {
  const total = steps.length
  const done = steps.filter(s => s.status === 'done').length
  const pct = total > 0 ? Math.round((done / total) * 100) : 0

  return (
    <Box
      testID={testID}
      p={16}
      radius={16}
      style={{ backgroundColor: tokens.colors.card, borderWidth: 1, borderColor: tokens.colors.border }}
    >
      <Text size={14} weight="600" color={tokens.colors.cardForeground}>Setup Progress</Text>
      <Box mt={8}>
        <Box h={8} radius={8} style={{ backgroundColor: tokens.colors.muted }}>
          <Box h={8} radius={8} w={`${pct}%`} style={{ backgroundColor: tokens.colors.primary }} />
        </Box>
        <Text size={12} color={tokens.colors.mutedForeground} style={{ marginTop: 6 }}>{done}/{total} ({pct}%)</Text>
      </Box>

      <Box mt={12} gap={8}>
        {steps.map((s) => (
          <Box key={s.id} row align="center" justify="space-between">
            <Text size={13} color={tokens.colors.cardForeground}>{s.title}</Text>
            <Text size={12} color={s.status === 'done' ? tokens.colors.success : tokens.colors.mutedForeground}>
              {s.status.toUpperCase()}
            </Text>
          </Box>
        ))}
      </Box>
    </Box>
  )
}

export default SetupProgressCard


