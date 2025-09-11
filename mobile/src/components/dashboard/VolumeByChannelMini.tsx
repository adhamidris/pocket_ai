import React from 'react'
import Box from '../../ui/Box'
import { Text } from '../../ui/Text'
import { tokens } from '../../ui/tokens'

export interface VolumeByChannelMiniProps {
  items: { channel: string; value: number }[]
  testID?: string
}

const VolumeByChannelMini: React.FC<VolumeByChannelMiniProps> = ({ items, testID }) => {
  const max = Math.max(1, ...items.map(i => i.value))

  return (
    <Box testID={testID} p={16} radius={16} style={{ backgroundColor: tokens.colors.card, borderWidth: 1, borderColor: tokens.colors.border }}>
      <Text size={14} weight="600" color={tokens.colors.cardForeground}>Volume by Channel</Text>
      <Box mt={12} gap={8}>
        {items.map((i) => (
          <Box key={i.channel}>
            <Box row align="center" justify="space-between" mb={4}>
              <Text size={12} color={tokens.colors.cardForeground}>{i.channel}</Text>
              <Text size={12} color={tokens.colors.mutedForeground}>{i.value}</Text>
            </Box>
            <Box h={6} radius={6} style={{ backgroundColor: tokens.colors.muted }}>
              <Box h={6} radius={6} w={`${Math.round((i.value / max) * 100)}%`} style={{ backgroundColor: tokens.colors.primary }} />
            </Box>
          </Box>
        ))}
      </Box>
    </Box>
  )
}

export default React.memo(VolumeByChannelMini)


