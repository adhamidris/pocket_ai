import React from 'react'
import { View, Text, TouchableOpacity } from 'react-native'
import { tokens } from '../../ui/tokens'
import { Plan, Interval } from '../../types/billing'
import PriceToggle from './PriceToggle'
import FeatureList from './FeatureList'

export interface PlanCardProps { plan: Plan; selected?: boolean; onSelect: () => void; testID?: string }

const formatCurrency = (amount: number, currency: string) => {
  try {
    return new Intl.NumberFormat(undefined, { style: 'currency', currency }).format(amount / 100)
  } catch {
    return `$${(amount / 100).toFixed(2)}`
  }
}

const PlanCard: React.FC<PlanCardProps> = ({ plan, selected, onSelect, testID }) => {
  const [interval, setInterval] = React.useState<Interval>('month')
  const price = React.useMemo(() => plan.prices.find(p => p.interval === interval), [plan.prices, interval])
  const topFeatures = React.useMemo(() => plan.features.slice(0, 3), [plan.features])

  return (
    <View
      testID={testID}
      style={{
        borderWidth: 2,
        borderColor: selected ? tokens.colors.primary : tokens.colors.border,
        borderRadius: 12,
        padding: 12,
        backgroundColor: tokens.colors.card
      }}
      accessibilityLabel={`Plan card ${plan.name}${selected ? ' selected' : ''}`}
    >
      <View style={{ flexDirection: 'row', justifyContent: 'space-between', alignItems: 'center', marginBottom: 8 }}>
        <View>
          <Text style={{ color: tokens.colors.cardForeground, fontSize: 18, fontWeight: '700' }}>{plan.name}</Text>
          <Text style={{ color: tokens.colors.mutedForeground, marginTop: 4 }}>{plan.blurb}</Text>
        </View>
        {plan.recommended && (
          <View style={{ backgroundColor: tokens.colors.primary, paddingHorizontal: 8, paddingVertical: 4, borderRadius: 999 }}>
            <Text style={{ color: tokens.colors.primaryForeground, fontWeight: '700' }}>Recommended</Text>
          </View>
        )}
      </View>

      <View style={{ marginBottom: 8 }}>
        <PriceToggle value={interval} onChange={setInterval} />
        {price && (
          <Text style={{ color: tokens.colors.cardForeground, fontSize: 22, fontWeight: '700', marginTop: 8 }}>
            {formatCurrency(price.unitAmount, price.currency)} / {interval}
          </Text>
        )}
      </View>

      <FeatureList features={topFeatures} />

      <TouchableOpacity
        onPress={onSelect}
        accessibilityRole="button"
        accessibilityLabel={`Select plan ${plan.name}${selected ? ', currently selected' : ''}${price ? `, ${formatCurrency(price.unitAmount, price.currency)} per ${interval}` : ''}`}
        style={{
          marginTop: 12,
          backgroundColor: selected ? tokens.colors.secondary : tokens.colors.primary,
          paddingVertical: 12,
          borderRadius: 10,
          alignItems: 'center',
          minHeight: 44
        }}
      >
        <Text style={{ color: selected ? tokens.colors.secondaryForeground : tokens.colors.primaryForeground, fontWeight: '700' }}>
          {selected ? 'Selected' : 'Choose plan'}
        </Text>
      </TouchableOpacity>
    </View>
  )
}

export default React.memo(PlanCard)


