import React from 'react'
import Box from '../../ui/Box'
import { Text } from '../../ui/Text'
import { tokens } from '../../ui/tokens'
import { PeakHour } from '../../types/dashboard'

export interface InsightsPeakTimesProps {
  hours: PeakHour[]
  loading?: boolean
  testID?: string
}

// Placeholder grid: 24 columns (hours) → simple bar heights
const InsightsPeakTimes: React.FC<InsightsPeakTimesProps> = ({ hours, loading, testID }) => {
  const max = Math.max(1, ...hours.map(h => h.value))
  return (
    <Box testID={testID} p={16} radius={16} style={{ backgroundColor: tokens.colors.card, borderWidth: 1, borderColor: tokens.colors.border }}>
      <Text size={14} weight="600" color={tokens.colors.cardForeground}>Peak Times</Text>
      <Box row align="flex-end" gap={4} mt={12}>
        {loading ? (
          Array.from({ length: 24 }).map((_, i) => (
            <Box key={i} w={8} h={40} radius={4} style={{ backgroundColor: tokens.colors.muted }} />
          ))
        ) : hours.length === 0 ? (
          <Text size={12} color={tokens.colors.mutedForeground}>No data</Text>
        ) : (
          hours.map((h) => {
            const height = Math.max(4, Math.round((h.value / max) * 40))
            return (
              <Box key={h.hour} w={8} h={40} radius={4} style={{ backgroundColor: tokens.colors.muted }}>
                <Box w={8} h={height} radius={4} style={{ backgroundColor: tokens.colors.primary, position: 'absolute', bottom: 0 }} />
              </Box>
            )
          })
        )}
      </Box>
      <Box row justify="space-between" mt={8}>
        <Text size={10} color={tokens.colors.mutedForeground}>0</Text>
        <Text size={10} color={tokens.colors.mutedForeground}>12</Text>
        <Text size={10} color={tokens.colors.mutedForeground}>23</Text>
      </Box>
    </Box>
  )
}

export default React.memo(InsightsPeakTimes)


