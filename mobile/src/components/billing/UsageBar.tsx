import React from 'react'
import { View, Text } from 'react-native'
import { tokens } from '../../ui/tokens'
import { UsageMeter } from '../../types/billing'

export interface UsageBarProps { meter: UsageMeter; testID?: string }

const UsageBar: React.FC<UsageBarProps> = ({ meter, testID }) => {
  const percent = React.useMemo(() => {
    if (meter.limit === 'unlimited') return 0
    const p = (meter.used / Math.max(1, meter.limit)) * 100
    return Math.min(100, Math.max(0, Math.round(p)))
  }, [meter])

  const accessibilityNow = meter.limit === 'unlimited' ? undefined : Math.min(meter.used, meter.limit as number)
  const accessibilityMax = meter.limit === 'unlimited' ? undefined : (meter.limit as number)
  return (
    <View
      testID={testID}
      accessibilityLabel={`${meter.key} usage${meter.limit !== 'unlimited' ? `, ${percent}% used` : ''}`}
      accessibilityValue={accessibilityNow != null && accessibilityMax != null ? { now: accessibilityNow, min: 0, max: accessibilityMax } : undefined}
    >
      <View style={{ flexDirection: 'row', justifyContent: 'space-between', marginBottom: 6 }}>
        <Text style={{ color: tokens.colors.cardForeground, fontWeight: '700' }}>{meter.key}</Text>
        <Text style={{ color: tokens.colors.mutedForeground }}>
          {meter.used} / {meter.limit === 'unlimited' ? 'Unlimited' : meter.limit}
        </Text>
      </View>
      <View style={{ height: 12, backgroundColor: tokens.colors.muted, borderRadius: 999, overflow: 'hidden', borderWidth: 1, borderColor: tokens.colors.border }}>
        <View style={{ width: `${percent}%`, height: '100%', backgroundColor: tokens.colors.primary }} />
      </View>
      {meter.limit !== 'unlimited' && (
        <Text style={{ color: tokens.colors.mutedForeground, marginTop: 4 }}>{percent}% used</Text>
      )}
    </View>
  )
}

export default React.memo(UsageBar)


