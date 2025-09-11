import React from 'react'
import { TouchableOpacity } from 'react-native'
import Box from '../../ui/Box'
import { Text } from '../../ui/Text'
import { tokens } from '../../ui/tokens'
import { IndustryPackId } from '../../types/dashboard'

export interface IndustryTilesProps {
  pack: IndustryPackId
  onTilePress?: (title: string) => void
  testID?: string
}

const Tile: React.FC<{ title: string; subtitle?: string; count?: number; onPress?: () => void }> = ({ title, subtitle, count, onPress }) => (
  <TouchableOpacity
    onPress={onPress}
    accessibilityLabel={`Open ${title}`}
    accessibilityRole="button"
    activeOpacity={0.85}
    style={{ padding: 16, borderRadius: 16, backgroundColor: tokens.colors.card, borderWidth: 1, borderColor: tokens.colors.border, minHeight: 64, justifyContent: 'center' }}
  >
    <Box row align="center" justify="space-between">
      <Box>
        <Text size={14} weight="600" color={tokens.colors.cardForeground}>{title}</Text>
        {subtitle && (
          <Text size={12} color={tokens.colors.mutedForeground} style={{ marginTop: 4 }}>{subtitle}</Text>
        )}
      </Box>
      {typeof count === 'number' && (
        <Text size={16} weight="700" color={tokens.colors.primary}>{count}</Text>
      )}
    </Box>
  </TouchableOpacity>
)

const IndustryTiles: React.FC<IndustryTilesProps> = ({ pack, onTilePress, testID }) => {
  let tiles: { title: string; subtitle?: string; count?: number }[] = []
  if (pack === 'retail') {
    tiles = [
      { title: 'Orders Today', count: 12 },
      { title: 'Pending', count: 4 },
      { title: 'Returns', count: 2 },
    ]
  } else if (pack === 'services') {
    tiles = [
      { title: 'Upcoming Bookings', count: 3 },
      { title: 'No‑Shows', count: 1 },
      { title: 'Callback Queue', count: 2 },
    ]
  } else if (pack === 'saas') {
    tiles = [
      { title: 'Trials Expiring', count: 5 },
      { title: 'At‑Risk Accounts', count: 2 },
    ]
  } else {
    tiles = []
  }

  return (
    <Box testID={testID} gap={12} accessibilityLabel="Industry tiles" accessibilityRole="summary">
      {tiles.map((t, idx) => (
        <Tile key={`${t.title}-${idx}`} title={t.title} subtitle={t.subtitle} count={t.count} onPress={() => onTilePress?.(t.title)} />
      ))}
    </Box>
  )
}

export default IndustryTiles


