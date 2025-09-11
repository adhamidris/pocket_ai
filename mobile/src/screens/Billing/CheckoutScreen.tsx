import React from 'react'
import { SafeAreaView, View, Text, TouchableOpacity, ScrollView } from 'react-native'
import { useTheme } from '../../providers/ThemeProvider'
import { Coupon, Interval, Plan } from '../../types/billing'
import CouponInput from '../../components/billing/CouponInput'
import { useNavigation, useRoute } from '@react-navigation/native'
import { track } from '../../lib/analytics'
import { useCurrency, convertAmount } from '../../components/billing/currency'
import CurrencySelector from '../../components/billing/CurrencySelector'

type RouteParams = { plan: Plan; interval: Interval }

const formatCurrency = (amount: number, currency: string) => {
  try {
    return new Intl.NumberFormat(undefined, { style: 'currency', currency }).format(amount / 100)
  } catch {
    return `$${(amount / 100).toFixed(2)}`
  }
}

const CheckoutScreen: React.FC = () => {
  const { theme } = useTheme()
  const navigation = useNavigation<any>()
  const route = useRoute<any>()
  const { plan, interval } = route.params as RouteParams
  const price = plan.prices.find((p) => p.interval === interval) || plan.prices[0]
  const [currency] = useCurrency()
  React.useEffect(() => { try { (require('../../lib/analytics') as any).track('billing.checkout.view') } catch {} }, [])

  const [quantity, setQuantity] = React.useState<number>(1)
  const [coupon, setCoupon] = React.useState<Coupon | undefined>(route.params?.coupon)

  const unit = convertAmount(price.unitAmount, price.currency, currency)
  const subtotal = unit * quantity
  const discount = coupon ? (coupon.percentOff ? Math.round(subtotal * coupon.percentOff / 100) : Math.min(subtotal, convertAmount(coupon.amountOffCents || 0, (coupon.currency as any) || currency, currency))) : 0
  const total = subtotal - discount

  const startTrial = () => {
    try { track('billing.trial.start', { plan: plan.id, interval }) } catch {}
    navigation.navigate('Billing', { subscribed: true, planId: plan.id, interval, quantity })
  }

  const subscribe = () => {
    try { track('billing.subscribe', { plan: plan.id, interval, quantity }) } catch {}
    // Queue visual for offline-first demo
    navigation.navigate('Billing', { subscribed: true, planId: plan.id, interval, quantity, queuedSub: true })
  }

  return (
    <SafeAreaView style={{ flex: 1, backgroundColor: theme.color.background }}>
      <ScrollView>
        <View style={{ paddingHorizontal: 24, paddingVertical: 12 }}>
          <Text style={{ color: theme.color.foreground, fontSize: 24, fontWeight: '700', marginBottom: 8 }}>Checkout</Text>
          <View style={{ borderWidth: 1, borderColor: theme.color.border, borderRadius: 12, padding: 12, marginBottom: 12 }}>
            <Text style={{ color: theme.color.cardForeground, fontWeight: '700' }}>{plan.name}</Text>
            <Text style={{ color: theme.color.mutedForeground }}>{plan.blurb}</Text>
            <View style={{ flexDirection: 'row', alignItems: 'center', justifyContent: 'space-between' }}>
              <Text style={{ color: theme.color.cardForeground, fontSize: 18, fontWeight: '700', marginTop: 4 }}>{formatCurrency(unit, currency)} / {interval}</Text>
              <CurrencySelector />
            </View>
          </View>

          <View style={{ borderWidth: 1, borderColor: theme.color.border, borderRadius: 12, padding: 12, marginBottom: 12 }}>
            <Text style={{ color: theme.color.cardForeground, fontWeight: '700', marginBottom: 8 }}>Seats</Text>
            <View style={{ flexDirection: 'row', alignItems: 'center', gap: 8 }}>
              <TouchableOpacity onPress={() => setQuantity((q) => Math.max(1, q - 1))} accessibilityRole="button" accessibilityLabel="Decrease quantity" style={{ paddingHorizontal: 12, paddingVertical: 8, borderRadius: 8, borderWidth: 1, borderColor: theme.color.border, minHeight: 44 }}>
                <Text style={{ color: theme.color.cardForeground, fontWeight: '700' }}>-</Text>
              </TouchableOpacity>
              <Text style={{ color: theme.color.cardForeground, fontWeight: '700', minWidth: 40, textAlign: 'center' }}>{quantity}</Text>
              <TouchableOpacity onPress={() => setQuantity((q) => q + 1)} accessibilityRole="button" accessibilityLabel="Increase quantity" style={{ paddingHorizontal: 12, paddingVertical: 8, borderRadius: 8, borderWidth: 1, borderColor: theme.color.border, minHeight: 44 }}>
                <Text style={{ color: theme.color.cardForeground, fontWeight: '700' }}>+</Text>
              </TouchableOpacity>
            </View>
          </View>

          <View style={{ borderWidth: 1, borderColor: theme.color.border, borderRadius: 12, padding: 12, marginBottom: 12 }}>
            <Text style={{ color: theme.color.cardForeground, fontWeight: '700', marginBottom: 8 }}>Coupon</Text>
            <CouponInput
              applied={coupon}
              onApply={(code) => { try { (require('../../lib/analytics') as any).track('billing.checkout.apply_coupon', { code }) } catch {}; setCoupon({ id: code.toLowerCase(), code, duration: 'once' }) }}
              onRemove={() => setCoupon(undefined)}
            />
            <TouchableOpacity onPress={() => navigation.navigate('Coupons', { coupon, priceCents: price.unitAmount, currency: price.currency, quantity, returnTo: 'Checkout' })} accessibilityRole="button" accessibilityLabel="Open coupons" style={{ marginTop: 8, alignSelf: 'flex-start', paddingHorizontal: 10, paddingVertical: 8, borderRadius: 8, borderWidth: 1, borderColor: theme.color.border }}>
              <Text style={{ color: theme.color.cardForeground, fontWeight: '700' }}>Browse promotions</Text>
            </TouchableOpacity>
          </View>

          <View style={{ borderWidth: 1, borderColor: theme.color.border, borderRadius: 12, padding: 12, marginBottom: 12 }}>
            <Text style={{ color: theme.color.cardForeground, fontWeight: '700', marginBottom: 8 }}>Summary</Text>
            <View style={{ flexDirection: 'row', justifyContent: 'space-between', marginBottom: 4 }}>
              <Text style={{ color: theme.color.mutedForeground }}>Subtotal</Text>
              <Text style={{ color: theme.color.cardForeground }}>{formatCurrency(subtotal, currency)}</Text>
            </View>
            {discount > 0 && (
              <View style={{ flexDirection: 'row', justifyContent: 'space-between', marginBottom: 4 }}>
                <Text style={{ color: theme.color.mutedForeground }}>Discount</Text>
                <Text style={{ color: theme.color.success }}>- {formatCurrency(discount, currency)}</Text>
              </View>
            )}
            <View style={{ flexDirection: 'row', justifyContent: 'space-between', marginTop: 8 }}>
              <Text style={{ color: theme.color.cardForeground, fontWeight: '700' }}>Total</Text>
              <Text style={{ color: theme.color.cardForeground, fontWeight: '700' }}>{formatCurrency(total, currency)}</Text>
            </View>
          </View>

          <View style={{ flexDirection: 'row', gap: 8 }}>
            {price.trialDays ? (
              <TouchableOpacity onPress={startTrial} accessibilityRole="button" accessibilityLabel="Start trial" style={{ flex: 1, paddingHorizontal: 12, paddingVertical: 12, borderRadius: 10, backgroundColor: theme.color.secondary }}>
                <Text style={{ color: theme.color.secondaryForeground, fontWeight: '700', textAlign: 'center' }}>Start {price.trialDays}-day trial</Text>
              </TouchableOpacity>
            ) : null}
            <TouchableOpacity onPress={subscribe} accessibilityRole="button" accessibilityLabel="Subscribe" style={{ flex: 1, paddingHorizontal: 12, paddingVertical: 12, borderRadius: 10, backgroundColor: theme.color.primary }}>
              <Text style={{ color: theme.color.primaryForeground, fontWeight: '700', textAlign: 'center' }}>Subscribe</Text>
            </TouchableOpacity>
          </View>
        </View>
      </ScrollView>
    </SafeAreaView>
  )
}

export default CheckoutScreen


