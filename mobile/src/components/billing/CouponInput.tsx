import React from 'react'
import { View, Text, TextInput, TouchableOpacity } from 'react-native'
import { tokens } from '../../ui/tokens'
import { Coupon } from '../../types/billing'

export interface CouponInputProps { onApply: (code: string) => void; onRemove: () => void; applied?: Coupon; testID?: string }

const CouponInput: React.FC<CouponInputProps> = ({ onApply, onRemove, applied, testID }) => {
  const [code, setCode] = React.useState('')

  return (
    <View testID={testID}>
      {applied ? (
        <View style={{ flexDirection: 'row', alignItems: 'center', justifyContent: 'space-between' }}>
          <Text style={{ color: tokens.colors.cardForeground, fontWeight: '700' }}>Coupon: {applied.code}</Text>
          <TouchableOpacity onPress={onRemove} accessibilityRole="button" accessibilityLabel="Remove coupon" style={{ paddingHorizontal: 10, paddingVertical: 8, borderRadius: 8, borderWidth: 1, borderColor: tokens.colors.border, minHeight: 44 }}>
            <Text style={{ color: tokens.colors.error, fontWeight: '700' }}>Remove</Text>
          </TouchableOpacity>
        </View>
      ) : (
        <View style={{ flexDirection: 'row', alignItems: 'center', gap: 8 }}>
          <TextInput
            placeholder="Enter coupon"
            placeholderTextColor={tokens.colors.mutedForeground}
            value={code}
            onChangeText={setCode}
            accessibilityLabel="Coupon code"
            style={{ flex: 1, borderWidth: 1, borderColor: tokens.colors.border, borderRadius: 8, paddingHorizontal: 10, paddingVertical: 10, color: tokens.colors.cardForeground, minHeight: 44 }}
          />
          <TouchableOpacity onPress={() => onApply(code.trim())} disabled={!code.trim()} accessibilityRole="button" accessibilityLabel="Apply coupon" style={{ backgroundColor: tokens.colors.primary, paddingHorizontal: 12, paddingVertical: 10, borderRadius: 8, opacity: code.trim() ? 1 : 0.6, minHeight: 44 }}>
            <Text style={{ color: tokens.colors.primaryForeground, fontWeight: '700' }}>Apply</Text>
          </TouchableOpacity>
        </View>
      )}
    </View>
  )
}

export default React.memo(CouponInput)


