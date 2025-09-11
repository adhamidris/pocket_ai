import React from 'react'
import { SafeAreaView, View, Text, TouchableOpacity, ScrollView } from 'react-native'
import { useTheme } from '../../providers/ThemeProvider'
import { Coupon } from '../../types/billing'
import CouponInput from '../../components/billing/CouponInput'
import { useNavigation, useRoute } from '@react-navigation/native'

type Params = { coupon?: Coupon; priceCents?: number; currency?: string; quantity?: number; returnTo?: string }

const formatCurrency = (amount: number, currency: string) => {
  try {
    return new Intl.NumberFormat(undefined, { style: 'currency', currency }).format(amount / 100)
  } catch {
    return `$${(amount / 100).toFixed(2)}`
  }
}

const describeCoupon = (c: Coupon) => {
  if (c.percentOff) return `${c.percentOff}% off`
  if (c.amountOffCents && c.currency) return `${formatCurrency(c.amountOffCents, c.currency)} off`
  return 'Promotion applied'
}

const CouponsScreen: React.FC = () => {
  const { theme } = useTheme()
  const navigation = useNavigation<any>()
  const route = useRoute<any>()
  const params = (route.params || {}) as Params
  const [coupon, setCoupon] = React.useState<Coupon | undefined>(params.coupon)

  const base = (params.priceCents || 0) * (params.quantity || 1)
  const discount = coupon
    ? (coupon.percentOff ? Math.round(base * coupon.percentOff / 100) : Math.min(base, coupon.amountOffCents || 0))
    : 0
  const total = base - discount

  const onApply = (code: string) => {
    const codeUp = code.trim().toUpperCase()
    // simple demo logic: map codes to demo coupons
    if (codeUp === 'SAVE20') setCoupon({ id: 'c_save20', code: 'SAVE20', percentOff: 20, duration: 'once' })
    else if (codeUp === 'LESS500') setCoupon({ id: 'c_less500', code: 'LESS500', amountOffCents: 500, currency: (params.currency as any) || 'USD', duration: 'once' })
    else setCoupon({ id: `c_${codeUp}`, code: codeUp, percentOff: 10, duration: 'once' })
  }

  const onSave = () => {
    const target = params.returnTo || 'Billing'
    if (target === 'Checkout') {
      navigation.navigate('Checkout', { coupon })
    } else if (target === 'ManagePlan') {
      navigation.navigate('ManagePlan', { coupon })
    } else {
      navigation.navigate('Billing', { coupon })
    }
  }

  return (
    <SafeAreaView style={{ flex: 1, backgroundColor: theme.color.background }}>
      <ScrollView>
        <View style={{ paddingHorizontal: 24, paddingVertical: 12 }}>
          <View style={{ flexDirection: 'row', alignItems: 'center', justifyContent: 'space-between', marginBottom: 12 }}>
            <Text style={{ color: theme.color.foreground, fontSize: 24, fontWeight: '700' }}>Coupons & Promotions</Text>
            <TouchableOpacity onPress={onSave} accessibilityRole="button" accessibilityLabel="Save" style={{ paddingHorizontal: 12, paddingVertical: 8, borderRadius: 10, backgroundColor: theme.color.primary }}>
              <Text style={{ color: theme.color.primaryForeground, fontWeight: '700' }}>Save</Text>
            </TouchableOpacity>
          </View>

          <View style={{ borderWidth: 1, borderColor: theme.color.border, borderRadius: 12, padding: 12, marginBottom: 12 }}>
            <Text style={{ color: theme.color.cardForeground, fontWeight: '700', marginBottom: 8 }}>Apply coupon</Text>
            <CouponInput applied={coupon} onApply={onApply} onRemove={() => setCoupon(undefined)} />
          </View>

          {(params.priceCents && params.currency) ? (
            <View style={{ borderWidth: 1, borderColor: theme.color.border, borderRadius: 12, padding: 12 }}>
              <Text style={{ color: theme.color.cardForeground, fontWeight: '700', marginBottom: 8 }}>Preview</Text>
              <Text style={{ color: theme.color.mutedForeground, marginBottom: 4 }}>Base: {formatCurrency(base, params.currency)}</Text>
              {coupon ? (
                <Text style={{ color: theme.color.success, marginBottom: 4 }}>Discount ({describeCoupon(coupon)}): -{formatCurrency(discount, params.currency)}</Text>
              ) : null}
              <Text style={{ color: theme.color.cardForeground, fontWeight: '700' }}>Total: {formatCurrency(total, params.currency)}</Text>
            </View>
          ) : null}
        </View>
      </ScrollView>
    </SafeAreaView>
  )
}

export default CouponsScreen


