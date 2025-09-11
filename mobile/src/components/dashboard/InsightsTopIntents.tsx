import React from 'react'
import Box from '../../ui/Box'
import { Text } from '../../ui/Text'
import { tokens } from '../../ui/tokens'
import { IntentItem } from '../../types/dashboard'
import Skeleton from './Skeleton'

export interface InsightsTopIntentsProps {
  items: IntentItem[]
  loading?: boolean
  testID?: string
}

const InsightsTopIntents: React.FC<InsightsTopIntentsProps> = ({ items, loading, testID }) => {
  return (
    <Box testID={testID} p={16} radius={16} style={{ backgroundColor: tokens.colors.card, borderWidth: 1, borderColor: tokens.colors.border }}>
      <Text size={14} weight="600" color={tokens.colors.cardForeground}>Top Intents</Text>
      <Box mt={12} gap={8}>
        {loading ? (
          <>
            <Skeleton variant="text" width={160} height={14} />
            <Skeleton variant="text" width={140} height={14} />
            <Skeleton variant="text" width={120} height={14} />
          </>
        ) : items.length === 0 ? (
          <Text size={12} color={tokens.colors.mutedForeground}>No insights available</Text>
        ) : (
          items.map((it) => (
            <Box key={it.name} row align="center" justify="space-between">
              <Text size={13} color={tokens.colors.cardForeground}>{it.name}</Text>
              <Box row align="center" gap={8}>
                {typeof it.deflectionPct === 'number' && (
                  <Text size={12} color={tokens.colors.mutedForeground}>Defl {it.deflectionPct}%</Text>
                )}
                {typeof it.trendDelta === 'number' && (
                  <Text size={12} color={it.trendDelta >= 0 ? tokens.colors.success : tokens.colors.error}>
                    {it.trendDelta >= 0 ? `+${it.trendDelta}%` : `${it.trendDelta}%`}
                  </Text>
                )}
                <Text size={12} weight="600" color={tokens.colors.primary}>{it.sharePct}%</Text>
              </Box>
            </Box>
          ))
        )}
      </Box>
    </Box>
  )
}

export default React.memo(InsightsTopIntents)


