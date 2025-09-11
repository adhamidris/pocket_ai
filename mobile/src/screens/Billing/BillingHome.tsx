import React from 'react'
import { SafeAreaView, View, Text, TouchableOpacity, ScrollView, RefreshControl } from 'react-native'
import { useSafeAreaInsets } from 'react-native-safe-area-context'
import { useTheme } from '../../providers/ThemeProvider'
import { track } from '../../lib/analytics'
import { BillingPortalState, UsageMeter, Invoice, PaymentMethod, Plan } from '../../types/billing'
import { setEntitlements } from '../../components/billing/entitlements'
import DunningBanner from '../../components/billing/DunningBanner'
import UsageBar from '../../components/billing/UsageBar'
import InvoiceRow from '../../components/billing/InvoiceRow'
import PaymentMethodRow from '../../components/billing/PaymentMethodRow'
import PlanCard from '../../components/billing/PlanCard'
import { useNavigation, useRoute } from '@react-navigation/native'
import OfflineBanner from '../../components/dashboard/OfflineBanner'

const mockState: BillingPortalState = {
  currency: 'USD',
  plans: [
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
  ],
  sub: {
    id: 'sub_123', planId: 'pro', status: 'active', currentPeriodStart: Math.floor(Date.now() / 1000) - 7 * 86400, currentPeriodEnd: Math.floor(Date.now() / 1000) + 23 * 86400, quantity: 1, currency: 'USD', unitAmount: 4900
  },
  paymentMethods: [
    { id: 'pm_1', brand: 'visa', last4: '4242', expMonth: 12, expYear: 2030, isDefault: true }
  ],
  invoices: Array.from({ length: 7 }).map((_, i) => ({ id: `inv_${i}`, number: `000${i + 1}`, amountDue: 4900, currency: 'USD', created: Math.floor(Date.now() / 1000) - i * 30 * 86400, status: i === 0 ? 'open' : 'paid' } as Invoice)),
  usage: [
    { key: 'messages', periodStart: Math.floor(Date.now() / 1000) - 7 * 86400, periodEnd: Math.floor(Date.now() / 1000) + 23 * 86400, used: 12450, limit: 50000 },
    { key: 'mtu', periodStart: Math.floor(Date.now() / 1000) - 7 * 86400, periodEnd: Math.floor(Date.now() / 1000) + 23 * 86400, used: 320, limit: 1000 },
    { key: 'uploadsMB', periodStart: Math.floor(Date.now() / 1000) - 7 * 86400, periodEnd: Math.floor(Date.now() / 1000) + 23 * 86400, used: 5400, limit: 10000 }
  ] as UsageMeter[],
  coupons: [],
  tax: { country: 'USA' },
  dunning: { cardExpiring: false }
}

const Section: React.FC<{ title: string; right?: React.ReactNode }>=({ title, right, children }) => {
  const { theme } = useTheme()
  return (
    <View style={{ marginBottom: 16 }}>
      <View style={{ flexDirection: 'row', alignItems: 'center', justifyContent: 'space-between', marginBottom: 8 }}>
        <Text style={{ color: theme.color.cardForeground, fontSize: 16, fontWeight: '700' }}>{title}</Text>
        {right}
      </View>
      <View style={{ borderWidth: 1, borderColor: theme.color.border, borderRadius: 12, padding: 12 }}>
        {children}
      </View>
    </View>
  )
}

const BillingHome: React.FC = () => {
  const insets = useSafeAreaInsets()
  const { theme } = useTheme()
  const navigation = useNavigation<any>()
  const route = useRoute<any>()
  const [state, setState] = React.useState<BillingPortalState>(mockState)
  const [refreshing, setRefreshing] = React.useState(false)
  const [offline, setOffline] = React.useState(false)
  const [queued, setQueued] = React.useState<Record<string, boolean>>({})

  React.useEffect(() => { try { track('billing.view') } catch {} }, [])

  // Handle subscription updates coming back from Checkout
  React.useEffect(() => {
    const params = route.params as any
    if (params?.subscribed && params.planId) {
      const selectedPlan = state.plans.find((p) => p.id === params.planId)
      const price = selectedPlan?.prices.find((p) => p.interval === params.interval) || selectedPlan?.prices[0]
      if (selectedPlan && price) {
        setState((s) => ({
          ...s,
          sub: {
            id: s.sub?.id || 'sub_demo',
            planId: selectedPlan.id,
            status: 'active',
            currentPeriodStart: Math.floor(Date.now() / 1000),
            currentPeriodEnd: Math.floor(Date.now() / 1000) + (params.interval === 'year' ? 365 : 30) * 86400,
            quantity: params.quantity || 1,
            currency: price.currency,
            unitAmount: price.unitAmount
          }
        }))
      }
    }
    if (params?.updatedMethods) {
      setState((s) => ({ ...s, paymentMethods: params.updatedMethods, dunning: params.dunningCleared ? undefined : s.dunning }))
    }
    if (params?.tax) {
      setState((s) => ({ ...s, tax: params.tax }))
    }
    if (params?.queuedSub) {
      setQueued((q) => ({ ...q, sub: true })); setTimeout(() => setQueued((q) => ({ ...q, sub: false })), 1500)
    }
  }, [route.params])

  const onRefresh = React.useCallback(() => {
    setRefreshing(true)
    setTimeout(() => {
      // reshuffle demo state minimally
      setState((s) => ({
        ...s,
        usage: s.usage.map((u) => ({ ...u, used: Math.max(0, u.used + Math.round((Math.random() - 0.5) * 500)) }))
      }))
      setRefreshing(false)
    }, 500)
  }, [])

  const subPlan: Plan | undefined = React.useMemo(() => state.plans.find((p) => p.id === state.sub?.planId), [state])
  const nextRenewal = state.sub ? new Date(state.sub.currentPeriodEnd * 1000).toLocaleDateString() : '-'

  React.useEffect(() => {
    const map = (subPlan?.entitlements || []).reduce((acc, e) => { acc[e.key] = { enabled: e.enabled, limit: e.limit }; return acc }, {} as any)
    setEntitlements(map)
  }, [subPlan])

  return (
    <SafeAreaView style={{ flex: 1, backgroundColor: theme.color.background }}>
      <ScrollView refreshControl={<RefreshControl refreshing={refreshing} onRefresh={onRefresh} />}>
        <View style={{ paddingHorizontal: 24, paddingTop: insets.top + 12, paddingBottom: 12 }}>
          <View style={{ flexDirection: 'row', alignItems: 'center', justifyContent: 'space-between' }}>
            <Text style={{ color: theme.color.foreground, fontSize: 28, fontWeight: '700' }}>Billing & Plans</Text>
            <View style={{ flexDirection: 'row', gap: 8 }}>
              <TouchableOpacity onPress={() => {}} accessibilityRole="button" accessibilityLabel="Download invoices" style={{ paddingHorizontal: 12, paddingVertical: 8, borderRadius: 10, borderWidth: 1, borderColor: theme.color.border }}>
                <Text style={{ color: theme.color.cardForeground }}>Download</Text>
              </TouchableOpacity>
              <TouchableOpacity onPress={() => {}} accessibilityRole="button" accessibilityLabel="Billing contacts" style={{ paddingHorizontal: 12, paddingVertical: 8, borderRadius: 10, borderWidth: 1, borderColor: theme.color.border }}>
                <Text style={{ color: theme.color.cardForeground }}>Contacts</Text>
              </TouchableOpacity>
              <TouchableOpacity onPress={() => setOffline((v) => !v)} accessibilityRole="button" accessibilityLabel="Toggle offline" style={{ paddingHorizontal: 12, paddingVertical: 8, borderRadius: 10, borderWidth: 1, borderColor: offline ? theme.color.primary : theme.color.border }}>
                <Text style={{ color: offline ? theme.color.primary : theme.color.cardForeground }}>{offline ? 'Cached' : 'Online'}</Text>
              </TouchableOpacity>
              <TouchableOpacity onPress={() => {}} accessibilityRole="button" accessibilityLabel="Help" style={{ paddingHorizontal: 12, paddingVertical: 8, borderRadius: 10, borderWidth: 1, borderColor: theme.color.border }}>
                <Text style={{ color: theme.color.cardForeground }}>Help</Text>
              </TouchableOpacity>
              <TouchableOpacity onPress={() => navigation.navigate('Settings', { screen: 'Settings' })} accessibilityRole="button" accessibilityLabel="Sync Center" style={{ paddingHorizontal: 12, paddingVertical: 8, borderRadius: 10, borderWidth: 1, borderColor: theme.color.border }}>
                <Text style={{ color: theme.color.cardForeground }}>Sync</Text>
              </TouchableOpacity>
            </View>
          </View>
        </View>

        <View style={{ paddingHorizontal: 24 }}>
          {offline && (
            <View style={{ marginBottom: 12 }}>
              <OfflineBanner visible testID="bill-offline" />
            </View>
          )}
          {!!state.dunning && (
            <View style={{ marginBottom: 16 }}>
              <DunningBanner state={state.dunning} onUpdateCard={() => navigation.navigate('PaymentMethods', { methods: state.paymentMethods, dunning: state.dunning })} />
              <TouchableOpacity onPress={() => navigation.navigate('Dunning', { dunning: state.dunning })} accessibilityRole="button" accessibilityLabel="View details" style={{ alignSelf: 'flex-start', marginTop: 8, paddingHorizontal: 10, paddingVertical: 8, borderRadius: 8, borderWidth: 1, borderColor: theme.color.border }}>
                <Text style={{ color: theme.color.cardForeground, fontWeight: '700' }}>View details</Text>
              </TouchableOpacity>
            </View>
          )}

          <Section title="Current Plan" right={
            <TouchableOpacity onPress={() => navigation.navigate('ManagePlan', { sub: state.sub, plans: state.plans })} accessibilityRole="button" accessibilityLabel="Manage plan" style={{ paddingHorizontal: 10, paddingVertical: 8, borderRadius: 8, backgroundColor: theme.color.primary }}>
              <Text style={{ color: theme.color.primaryForeground, fontWeight: '700' }}>Manage Plan</Text>
            </TouchableOpacity>
          }>
            <View style={{ gap: 8 }}>
              <Text style={{ color: theme.color.cardForeground, fontWeight: '700' }}>{subPlan?.name || 'No plan'}</Text>
              {state.sub && (
                <View style={{ flexDirection: 'row', alignItems: 'center', gap: 8 }}>
                  <View style={{ paddingHorizontal: 8, paddingVertical: 4, borderRadius: 999, backgroundColor: theme.color.muted }}>
                    <Text style={{ color: theme.color.mutedForeground, fontWeight: '700' }}>{state.sub.status.toUpperCase()}</Text>
                  </View>
                  <Text style={{ color: theme.color.mutedForeground }}>Renews on {nextRenewal}</Text>
                </View>
              )}
              {subPlan && (
                <View>
                  <PlanCard plan={subPlan} selected onSelect={() => {}} />
                  {queued.sub ? <Text style={{ color: theme.color.warning, marginTop: 6 }}>⏱ Updating subscription…</Text> : null}
                </View>
              )}
            </View>
          </Section>

          <Section title="Usage" right={
            <TouchableOpacity onPress={() => navigation.navigate('UsageLimits', { usage: state.usage })} accessibilityRole="button" accessibilityLabel="Open limits" style={{ paddingHorizontal: 10, paddingVertical: 8, borderRadius: 8, borderWidth: 1, borderColor: theme.color.border }}>
              <Text style={{ color: theme.color.cardForeground, fontWeight: '700' }}>Limits</Text>
            </TouchableOpacity>
          }>
            <View style={{ gap: 12 }}>
              {state.usage.map((u) => (
                <UsageBar key={u.key} meter={u} />
              ))}
            </View>
          </Section>

          <Section title="Payment Method" right={
            <TouchableOpacity onPress={() => navigation.navigate('PaymentMethods', { methods: state.paymentMethods, dunning: state.dunning })} accessibilityRole="button" accessibilityLabel="Manage payment methods" style={{ paddingHorizontal: 10, paddingVertical: 8, borderRadius: 8, backgroundColor: theme.color.secondary }}>
              <Text style={{ color: theme.color.secondaryForeground, fontWeight: '700' }}>Manage</Text>
            </TouchableOpacity>
          }>
            {state.paymentMethods[0] ? (
              <PaymentMethodRow pm={state.paymentMethods[0]} onSetDefault={() => {}} onRemove={() => {}} />
            ) : (
              <Text style={{ color: theme.color.mutedForeground }}>No payment methods</Text>
            )}
          </Section>

          <Section title="Invoices" right={
            <TouchableOpacity onPress={() => navigation.navigate('Invoices', { invoices: state.invoices })} accessibilityRole="button" accessibilityLabel="View all invoices" style={{ paddingHorizontal: 10, paddingVertical: 8, borderRadius: 8, borderWidth: 1, borderColor: theme.color.border }}>
              <Text style={{ color: theme.color.cardForeground, fontWeight: '700' }}>View All</Text>
            </TouchableOpacity>
          }>
            <View style={{ gap: 4 }}>
              {state.invoices.slice(0, 5).map((inv) => (
                <InvoiceRow key={inv.id} inv={inv} onOpen={() => navigation.navigate('InvoiceDetail', { inv, tax: state.tax })} />
              ))}
            </View>
          </Section>

          <Section title="Billing Profile" right={
            <TouchableOpacity onPress={() => navigation.navigate('TaxBillingProfile', { tax: state.tax })} accessibilityRole="button" accessibilityLabel="Edit tax profile" style={{ paddingHorizontal: 10, paddingVertical: 8, borderRadius: 8, borderWidth: 1, borderColor: theme.color.border }}>
              <Text style={{ color: theme.color.cardForeground, fontWeight: '700' }}>Edit</Text>
            </TouchableOpacity>
          }>
            {state.tax ? (
              <View>
                <Text style={{ color: theme.color.cardForeground, fontWeight: '700' }}>{state.tax.invoiceName || '—'}</Text>
                <Text style={{ color: theme.color.mutedForeground }}>{state.tax.address1 || '—'}</Text>
                <Text style={{ color: theme.color.mutedForeground }}>{state.tax.city || '—'} {state.tax.zip || ''}</Text>
                <Text style={{ color: theme.color.mutedForeground }}>Country: {state.tax.country || '—'}{state.tax.vatId ? ` • VAT: ${state.tax.vatId}` : ''}</Text>
              </View>
            ) : (
              <Text style={{ color: theme.color.mutedForeground }}>No billing profile set.</Text>
            )}
          </Section>
        </View>
      </ScrollView>
    </SafeAreaView>
  )
}

export default BillingHome


