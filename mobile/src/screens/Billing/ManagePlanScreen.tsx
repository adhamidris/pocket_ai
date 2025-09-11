import React from 'react'
import { SafeAreaView, View, Text, TouchableOpacity, ScrollView, Modal, Switch } from 'react-native'
import { useTheme } from '../../providers/ThemeProvider'
import { useNavigation, useRoute } from '@react-navigation/native'
import { Coupon, Interval, Plan, Subscription } from '../../types/billing'
import PriceToggle from '../../components/billing/PriceToggle'
import PlanCard from '../../components/billing/PlanCard'

type Params = { sub?: Subscription; plans?: Plan[] }

const fallbackPlans: Plan[] = [
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
  }
]

const ManagePlanScreen: React.FC = () => {
  const { theme } = useTheme()
  const navigation = useNavigation<any>()
  const route = useRoute<any>()
  const params = (route.params || {}) as Params

  const initialSub: Subscription = params.sub || {
    id: 'sub_demo', planId: 'pro', status: 'active', currentPeriodStart: Math.floor(Date.now() / 1000) - 7 * 86400, currentPeriodEnd: Math.floor(Date.now() / 1000) + 23 * 86400, quantity: 1, currency: 'USD', unitAmount: 4900
  }
  const initialPlans: Plan[] = params.plans || fallbackPlans

  const [sub, setSub] = React.useState<Subscription>(initialSub)
  const [plans] = React.useState<Plan[]>(initialPlans)
  const [interval, setInterval] = React.useState<Interval>('month')
  const [modalOpen, setModalOpen] = React.useState(false)
  const [paused, setPaused] = React.useState(sub.status === 'paused')
  const [coupon, setCoupon] = React.useState<Coupon | undefined>(route.params?.coupon)

  const plan = React.useMemo(() => plans.find((p) => p.id === sub.planId), [plans, sub.planId])
  const nextRenewal = new Date(sub.currentPeriodEnd * 1000).toLocaleDateString()

  const toggleCancelAtPeriodEnd = () => {
    setSub((s) => {
      const next = !s.cancelAtPeriodEnd
      try { (require('../../lib/analytics') as any).track('billing.cancel_toggle', { value: next }) } catch {}
      return { ...s, cancelAtPeriodEnd: next }
    })
  }
  React.useEffect(() => { try { (require('../../lib/analytics') as any).track('billing.change_plan', { from: initialSub.planId, to: sub.planId }) } catch {} }, [sub.planId])

  const togglePaused = () => {
    setPaused((p) => !p)
    setSub((s) => ({ ...s, status: paused ? 'active' : 'paused' }))
    try { (require('../../lib/analytics') as any).track('billing.pause_toggle', { value: !paused }) } catch {}
  }

  const resumeNow = () => { setSub((s) => ({ ...s, cancelAtPeriodEnd: false, status: 'active' })); try { (require('../../lib/analytics') as any).track('billing.cancel_toggle', { value: false }) } catch {} }

  return (
    <SafeAreaView style={{ flex: 1, backgroundColor: theme.color.background }}>
      <ScrollView>
        <View style={{ paddingHorizontal: 24, paddingVertical: 12 }}>
          <Text style={{ color: theme.color.foreground, fontSize: 24, fontWeight: '700', marginBottom: 12 }}>Manage Plan</Text>

          <View style={{ borderWidth: 1, borderColor: theme.color.border, borderRadius: 12, padding: 12, marginBottom: 12 }}>
            <Text style={{ color: theme.color.cardForeground, fontWeight: '700' }}>{plan?.name || 'Unknown plan'}</Text>
            <View style={{ flexDirection: 'row', alignItems: 'center', gap: 8, marginTop: 6 }}>
              <View style={{ paddingHorizontal: 8, paddingVertical: 4, borderRadius: 999, backgroundColor: theme.color.muted }}>
                <Text style={{ color: theme.color.mutedForeground, fontWeight: '700' }}>{sub.status.toUpperCase()}</Text>
              </View>
              <Text style={{ color: theme.color.mutedForeground }}>Renews on {nextRenewal}</Text>
            </View>
            <TouchableOpacity onPress={() => setModalOpen(true)} accessibilityRole="button" accessibilityLabel="Change plan" style={{ alignSelf: 'flex-start', marginTop: 10, paddingHorizontal: 12, paddingVertical: 10, borderRadius: 10, backgroundColor: theme.color.primary }}>
              <Text style={{ color: theme.color.primaryForeground, fontWeight: '700' }}>Change Plan</Text>
            </TouchableOpacity>
          </View>

          <View style={{ borderWidth: 1, borderColor: theme.color.border, borderRadius: 12, padding: 12, marginBottom: 12 }}>
            <Text style={{ color: theme.color.cardForeground, fontWeight: '700', marginBottom: 8 }}>Cancellation</Text>
            <View style={{ flexDirection: 'row', alignItems: 'center', justifyContent: 'space-between' }}>
              <Text style={{ color: theme.color.cardForeground }}>Cancel at period end</Text>
              <Switch value={!!sub.cancelAtPeriodEnd} onValueChange={toggleCancelAtPeriodEnd} accessibilityLabel="Cancel at period end" />
            </View>
            {!!sub.cancelAtPeriodEnd && (
              <TouchableOpacity onPress={resumeNow} accessibilityRole="button" accessibilityLabel="Resume subscription" style={{ alignSelf: 'flex-start', marginTop: 10, paddingHorizontal: 12, paddingVertical: 10, borderRadius: 10, borderWidth: 1, borderColor: theme.color.border }}>
                <Text style={{ color: theme.color.cardForeground, fontWeight: '700' }}>Resume</Text>
              </TouchableOpacity>
            )}
          </View>

          <View style={{ borderWidth: 1, borderColor: theme.color.border, borderRadius: 12, padding: 12, marginBottom: 12 }}>
            <Text style={{ color: theme.color.cardForeground, fontWeight: '700', marginBottom: 8 }}>Promotions</Text>
            <View style={{ flexDirection: 'row', alignItems: 'center', justifyContent: 'space-between' }}>
              <Text style={{ color: theme.color.cardForeground }}>{coupon ? `Applied: ${coupon.code}` : 'No coupon applied'}</Text>
              <TouchableOpacity onPress={() => navigation.navigate('Coupons', { coupon, returnTo: 'ManagePlan' })} accessibilityRole="button" accessibilityLabel="Open coupons" style={{ paddingHorizontal: 10, paddingVertical: 8, borderRadius: 8, borderWidth: 1, borderColor: theme.color.border }}>
                <Text style={{ color: theme.color.cardForeground, fontWeight: '700' }}>{coupon ? 'Change' : 'Browse'}</Text>
              </TouchableOpacity>
            </View>
          </View>

          <View style={{ borderWidth: 1, borderColor: theme.color.border, borderRadius: 12, padding: 12, marginBottom: 12 }}>
            <Text style={{ color: theme.color.cardForeground, fontWeight: '700', marginBottom: 8 }}>Pause Subscription</Text>
            <View style={{ flexDirection: 'row', alignItems: 'center', justifyContent: 'space-between' }}>
              <Text style={{ color: theme.color.cardForeground }}>Pause billing and usage</Text>
              <Switch value={paused} onValueChange={togglePaused} accessibilityLabel="Pause subscription" />
            </View>
            {paused ? (
              <Text style={{ color: theme.color.mutedForeground, marginTop: 6 }}>Paused subscriptions enter a grace period. You can resume anytime.</Text>
            ) : (
              <Text style={{ color: theme.color.mutedForeground, marginTop: 6 }}>Pausing will stop renewals and features after the grace period.</Text>
            )}
          </View>

          <View style={{ borderWidth: 1, borderColor: theme.color.border, borderRadius: 12, padding: 12 }}>
            <Text style={{ color: theme.color.cardForeground, fontWeight: '700', marginBottom: 8 }}>Proration</Text>
            <Text style={{ color: theme.color.mutedForeground }}>Changing plans mid-cycle may result in prorated credits/charges. Example: Credit $12.34 applied to your next invoice.</Text>
          </View>
        </View>
      </ScrollView>

      <Modal visible={modalOpen} transparent animationType="slide" onRequestClose={() => setModalOpen(false)}>
        <View style={{ flex: 1, backgroundColor: 'rgba(0,0,0,0.4)', justifyContent: 'center', alignItems: 'center', padding: 16 }}>
          <View style={{ backgroundColor: theme.color.card, borderRadius: 12, borderWidth: 1, borderColor: theme.color.border, width: '100%', maxHeight: '85%' }}>
            <View style={{ padding: 12, borderBottomWidth: 1, borderBottomColor: theme.color.border, flexDirection: 'row', alignItems: 'center', justifyContent: 'space-between' }}>
              <Text style={{ color: theme.color.cardForeground, fontSize: 18, fontWeight: '700' }}>Choose Plan</Text>
              <PriceToggle value={interval} onChange={setInterval} />
            </View>
            <ScrollView contentContainerStyle={{ padding: 12 }}>
              <View style={{ gap: 12 }}>
                {plans.map((p) => (
                  <PlanCard key={p.id} plan={{ ...p, prices: p.prices.filter(pr => pr.interval === interval) }} selected={p.id === sub.planId} onSelect={() => { setSub((s) => ({ ...s, planId: p.id, proration: true })); setModalOpen(false) }} />
                ))}
              </View>
            </ScrollView>
            <View style={{ padding: 12, borderTopWidth: 1, borderTopColor: theme.color.border, alignItems: 'flex-end' }}>
              <TouchableOpacity onPress={() => setModalOpen(false)} accessibilityRole="button" accessibilityLabel="Close plan chooser" style={{ paddingHorizontal: 12, paddingVertical: 10, borderRadius: 10, borderWidth: 1, borderColor: theme.color.border }}>
                <Text style={{ color: theme.color.cardForeground, fontWeight: '700' }}>Done</Text>
              </TouchableOpacity>
            </View>
          </View>
        </View>
      </Modal>
    </SafeAreaView>
  )
}

export default ManagePlanScreen


