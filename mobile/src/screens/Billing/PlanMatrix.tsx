import React from 'react'
import { SafeAreaView, View, Text, TouchableOpacity, ScrollView } from 'react-native'
import { useTheme } from '../../providers/ThemeProvider'
import { Interval, Plan } from '../../types/billing'
import PriceToggle from '../../components/billing/PriceToggle'
import PlanCard from '../../components/billing/PlanCard'
import ComparePlansModal from './ComparePlansModal'
import { useNavigation, useRoute } from '@react-navigation/native'
import CurrencySelector from '../../components/billing/CurrencySelector'
import { useCurrency, convertAmount } from '../../components/billing/currency'

const mockPlans: Plan[] = [
  {
    id: 'free', tier: 'Free', name: 'Free', blurb: 'For getting started',
    prices: [{ id: 'free-m', currency: 'USD', unitAmount: 0, interval: 'month' }],
    features: [
      { key: 'Messages/month', value: 1000 },
      { key: 'Knowledge sources', value: 5 },
      { key: 'Automations', value: 2 }
    ],
    entitlements: [
      { key: 'messages', enabled: true, limit: 1000 },
      { key: 'knowledgeSources', enabled: true, limit: 5 },
      { key: 'automations', enabled: true, limit: 2 }
    ]
  },
  {
    id: 'starter', tier: 'Starter', name: 'Starter', blurb: 'For small teams',
    prices: [
      { id: 'starter-m', currency: 'USD', unitAmount: 1900, interval: 'month' },
      { id: 'starter-y', currency: 'USD', unitAmount: 19000, interval: 'year' }
    ],
    features: [
      { key: 'Messages/month', value: 10000 },
      { key: 'Knowledge sources', value: 20 },
      { key: 'Automations', value: 10 }
    ],
    entitlements: [
      { key: 'messages', enabled: true, limit: 10000 },
      { key: 'knowledgeSources', enabled: true, limit: 20 },
      { key: 'automations', enabled: true, limit: 10 }
    ]
  },
  {
    id: 'pro', tier: 'Pro', name: 'Pro', blurb: 'Best for teams', recommended: true,
    prices: [
      { id: 'pro-m', currency: 'USD', unitAmount: 4900, interval: 'month', trialDays: 14 },
      { id: 'pro-y', currency: 'USD', unitAmount: 49000, interval: 'year' }
    ],
    features: [
      { key: 'Messages/month', value: 50000 },
      { key: 'Knowledge sources', value: 50 },
      { key: 'Automations', value: 50 }
    ],
    entitlements: [
      { key: 'messages', enabled: true, limit: 50000 },
      { key: 'knowledgeSources', enabled: true, limit: 50 },
      { key: 'automations', enabled: true, limit: 50 }
    ]
  },
  {
    id: 'scale', tier: 'Scale', name: 'Scale', blurb: 'High volume',
    prices: [
      { id: 'scale-m', currency: 'USD', unitAmount: 14900, interval: 'month' },
      { id: 'scale-y', currency: 'USD', unitAmount: 149000, interval: 'year' }
    ],
    features: [
      { key: 'Messages/month', value: 200000 },
      { key: 'Knowledge sources', value: 200 },
      { key: 'Automations', value: 200 }
    ],
    entitlements: [
      { key: 'messages', enabled: true, limit: 200000 },
      { key: 'knowledgeSources', enabled: true, limit: 200 },
      { key: 'automations', enabled: true, limit: 200 }
    ]
  }
]

const PlanMatrix: React.FC = () => {
  const { theme } = useTheme()
  const navigation = useNavigation<any>()
  const route = useRoute<any>()
  const highlightPlanId: string | undefined = route.params?.highlightPlanId
  const [currency] = useCurrency()
  const [interval, setInterval] = React.useState<Interval>('month')
  const [compareOpen, setCompareOpen] = React.useState(false)
  React.useEffect(() => { try { (require('../../lib/analytics') as any).track('billing.plan_matrix.view') } catch {} }, [])

  return (
    <SafeAreaView style={{ flex: 1, backgroundColor: theme.color.background }}>
      <ScrollView>
        <View style={{ paddingHorizontal: 24, paddingVertical: 12 }}>
          <View style={{ flexDirection: 'row', alignItems: 'center', justifyContent: 'space-between', marginBottom: 12 }}>
            <Text style={{ color: theme.color.foreground, fontSize: 24, fontWeight: '700' }}>Choose a Plan</Text>
            <View style={{ flexDirection: 'row', alignItems: 'center', gap: 8 }}>
              <CurrencySelector />
              <PriceToggle value={interval} onChange={setInterval} />
            </View>
          </View>
          <View style={{ gap: 12 }}>
            {mockPlans.map((p) => (
              <PlanCard key={p.id} plan={{
                ...p,
                prices: p.prices.filter(pr => pr.interval === interval).map(pr => ({ ...pr, currency, unitAmount: convertAmount(pr.unitAmount, pr.currency, currency) }))
              }} selected={highlightPlanId === p.id} onSelect={() => navigation.navigate('Checkout', { plan: { ...p, prices: p.prices.map(pr => ({ ...pr, currency: pr.currency })) }, interval })} />
            ))}
          </View>
          <TouchableOpacity onPress={() => setCompareOpen(true)} accessibilityRole="button" accessibilityLabel="Compare plans" style={{ alignSelf: 'center', marginTop: 16, paddingHorizontal: 12, paddingVertical: 10, borderRadius: 10, borderWidth: 1, borderColor: theme.color.border }}>
            <Text style={{ color: theme.color.cardForeground, fontWeight: '700' }}>Compare table</Text>
          </TouchableOpacity>
        </View>
      </ScrollView>
      <ComparePlansModal visible={compareOpen} onClose={() => setCompareOpen(false)} plans={mockPlans} />
    </SafeAreaView>
  )
}

export default PlanMatrix


