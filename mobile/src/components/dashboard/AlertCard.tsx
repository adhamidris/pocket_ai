import React from 'react'
import { ViewStyle, TouchableOpacity } from 'react-native'
import Box from '../../ui/Box'
import { Text } from '../../ui/Text'
import { tokens } from '../../ui/tokens'
import { AlertItem } from '../../types/dashboard'
import Skeleton from './Skeleton'

export interface AlertCardProps {
  alert: AlertItem
  loading?: boolean
  testID?: string
  style?: ViewStyle
  onPress?: (deeplink: string) => void
}

const kindToColor = (kind: AlertItem['kind']) => {
  switch (kind) {
    case 'urgentBacklog':
      return tokens.colors.error
    case 'slaRisk':
      return tokens.colors.warning
    case 'unassigned':
      return tokens.colors.primary
    case 'volumeSpike':
      return tokens.colors.primaryLight
    default:
      return tokens.colors.mutedForeground
  }
}

import { track } from '../../lib/analytics'

const AlertCard: React.FC<AlertCardProps> = ({ alert, loading, testID, style, onPress }) => {
  const color = kindToColor(alert.kind)

  return (
    <TouchableOpacity
      testID={testID}
      accessibilityLabel={`Alert: ${alert.kind}`}
      accessibilityRole="button"
      activeOpacity={0.85}
      onPress={() => { track('alert.clicked', { kind: alert.kind }); onPress?.(alert.deeplink) }}
      style={{
        backgroundColor: tokens.colors.card,
        borderRadius: tokens.radius.lg,
        padding: tokens.space.md,
        borderWidth: 1,
        borderColor: tokens.colors.border,
        marginRight: 12,
        minHeight: 56,
        justifyContent: 'center',
      }}
      hitSlop={{ top: 8, bottom: 8, left: 8, right: 8 }}
    >
      {loading ? (
        <Skeleton variant="text" width={140} height={16} />
      ) : (
        <Text size={14} weight="600" color={tokens.colors.cardForeground}>
          {alert.kind}
        </Text>
      )}

      <Box mt={8} row align="center" justify="space-between">
        {loading ? (
          <Skeleton variant="rect" width={60} height={24} />
        ) : (
          <Text size={18} weight="700" color={color}>
            {alert.count}
          </Text>
        )}
        {!loading && alert.buckets && alert.buckets.length > 0 && (
          <Box row gap={8}>
            {alert.buckets.map((b) => (
              <Box key={b.label} px={8} py={4} radius={8} style={{ backgroundColor: tokens.colors.muted }}>
                <Text size={12} color={tokens.colors.mutedForeground}>{b.label}: {b.count}</Text>
              </Box>
            ))}
          </Box>
        )}
      </Box>
    </TouchableOpacity>
  )
}

export default React.memo(AlertCard)


