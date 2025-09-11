import React from 'react'
import { View, Text, TouchableOpacity } from 'react-native'
import { tokens } from '../../ui/tokens'
import { PaymentMethod } from '../../types/billing'

export interface PaymentMethodRowProps { pm: PaymentMethod; onSetDefault: () => void; onRemove: () => void; testID?: string; queued?: boolean }

const brandLabel: Record<string, string> = {
  visa: 'Visa',
  mastercard: 'Mastercard',
  amex: 'Amex',
  mada: 'Mada',
  paypal: 'PayPal',
  applepay: 'Apple Pay',
  googlepay: 'Google Pay'
}

const PaymentMethodRow: React.FC<PaymentMethodRowProps> = ({ pm, onSetDefault, onRemove, testID, queued }) => {
  return (
    <View testID={testID} style={{ flexDirection: 'row', alignItems: 'center', justifyContent: 'space-between', paddingVertical: 10 }}>
      <View style={{ flexDirection: 'row', alignItems: 'center', gap: 10 }}>
        <View style={{ width: 36, height: 24, borderRadius: 4, backgroundColor: tokens.colors.muted, alignItems: 'center', justifyContent: 'center' }}>
          <Text style={{ color: tokens.colors.mutedForeground, fontSize: 10 }}>{brandLabel[pm.brand] || pm.brand}</Text>
        </View>
        <Text style={{ color: tokens.colors.cardForeground }}>
          {brandLabel[pm.brand] || pm.brand}{pm.last4 ? ` •••• ${pm.last4}` : ''}{pm.expMonth && pm.expYear ? ` • ${pm.expMonth}/${pm.expYear}` : ''}
        </Text>
        {queued ? <Text style={{ color: tokens.colors.warning, marginLeft: 6 }}>⏱</Text> : null}
      </View>
      <View style={{ flexDirection: 'row', alignItems: 'center', gap: 8 }}>
        {pm.isDefault ? (
          <View style={{ borderWidth: 1, borderColor: tokens.colors.border, paddingHorizontal: 8, paddingVertical: 6, borderRadius: 8 }}>
            <Text style={{ color: tokens.colors.mutedForeground }}>Default</Text>
          </View>
        ) : (
          <TouchableOpacity onPress={onSetDefault} accessibilityRole="button" accessibilityLabel="Set default payment method" style={{ backgroundColor: tokens.colors.secondary, paddingHorizontal: 8, paddingVertical: 6, borderRadius: 8, minHeight: 44 }}>
            <Text style={{ color: tokens.colors.secondaryForeground, fontWeight: '700' }}>Make Default</Text>
          </TouchableOpacity>
        )}
        <TouchableOpacity onPress={onRemove} accessibilityRole="button" accessibilityLabel="Remove payment method" style={{ paddingHorizontal: 8, paddingVertical: 6, borderRadius: 8, borderWidth: 1, borderColor: tokens.colors.border, minHeight: 44 }}>
          <Text style={{ color: tokens.colors.error, fontWeight: '700' }}>Remove</Text>
        </TouchableOpacity>
      </View>
    </View>
  )
}

export default React.memo(PaymentMethodRow)


