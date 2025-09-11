import React from 'react'
import { TouchableOpacity } from 'react-native'
import { useNavigation } from '@react-navigation/native'
import { ViewStyle } from 'react-native'
import Box from '../../ui/Box'
import { Text } from '../../ui/Text'
import { tokens } from '../../ui/tokens'
import { DashboardKpi } from '../../types/dashboard'
import Skeleton from './Skeleton'

export interface KpiTileProps {
  item: DashboardKpi
  loading?: boolean
  testID?: string
  style?: ViewStyle
}

const formatValue = (value: number, unit?: DashboardKpi['unit']) => {
  if (unit === '%') return `${value}%`
  if (unit === 's') return `${value}s`
  return String(value)
}

const KpiTile: React.FC<KpiTileProps> = ({ item, loading, testID, style }) => {
  const navigation = useNavigation<any>()
  const onPress = () => {
    const premiumKinds = ['deflectionRate','attributionDepth','cohortRepeat']
    if (premiumKinds.includes(item.kind)) {
      // Navigate to PlanMatrix with a suggested plan id
      navigation.navigate('PlanMatrix', { highlightPlanId: 'starter' })
      return
    }
  }
  return (
    <Box
      testID={testID}
      style={[
        {
          backgroundColor: tokens.colors.card,
          borderRadius: tokens.radius.lg,
          padding: tokens.space.md,
          borderWidth: 1,
          borderColor: tokens.colors.border,
        },
        style,
      ]}
    >
      <TouchableOpacity onPress={onPress} accessibilityRole="button" accessibilityLabel={`Open ${item.kind}`}>
      {loading ? (
        <Skeleton variant="text" width={100} height={16} />
      ) : (
        <Text size={12} color={tokens.colors.mutedForeground}>
          {item.kind}
        </Text>
      )}

      <Box mt={8} row align="flex-end" justify="space-between">
        {loading ? (
          <Skeleton variant="rect" width={80} height={28} />
        ) : (
          <Text size={24} weight="700" color={tokens.colors.cardForeground}>
            {formatValue(item.value, item.unit)}
          </Text>
        )}

        {!loading && typeof item.delta === 'number' && (
          <Text size={12} color={item.delta >= 0 ? tokens.colors.success : tokens.colors.error}>
            {item.delta >= 0 ? `+${item.delta}%` : `${item.delta}%`}
          </Text>
        )}
      </Box>

      {!loading && item.target != null && (
        <Box mt={8}>
          <Text size={11} color={tokens.colors.mutedForeground}>
            Target: {formatValue(item.target, item.unit)}
          </Text>
        </Box>
      )}
      </TouchableOpacity>
    </Box>
  )
}

export default React.memo(KpiTile)


