import React from 'react'
import { View, Text, TextInput } from 'react-native'
import { tokens } from '../../ui/tokens'
import { TaxInfo } from '../../types/billing'

export interface TaxFormProps { value: TaxInfo; onChange: (v: TaxInfo) => void; testID?: string }

const Field: React.FC<{ label: string; value?: string; onChangeText: (v: string) => void; placeholder?: string; testID?: string }> = ({ label, value, onChangeText, placeholder, testID }) => (
  <View style={{ marginBottom: 8 }}>
    <Text style={{ color: tokens.colors.mutedForeground, marginBottom: 4 }}>{label}</Text>
    <TextInput
      testID={testID}
      placeholder={placeholder}
      placeholderTextColor={tokens.colors.mutedForeground}
      value={value}
      onChangeText={onChangeText}
      accessibilityLabel={label}
      style={{ borderWidth: 1, borderColor: tokens.colors.border, borderRadius: 8, paddingHorizontal: 10, paddingVertical: 10, color: tokens.colors.cardForeground, minHeight: 44 }}
    />
  </View>
)

const TaxForm: React.FC<TaxFormProps> = ({ value, onChange, testID }) => {
  const update = (patch: Partial<TaxInfo>) => onChange({ ...value, ...patch })
  return (
    <View testID={testID}>
      <Field label="Country" value={value.country} onChangeText={(v) => update({ country: v })} placeholder="Country" />
      <Field label="VAT ID" value={value.vatId} onChangeText={(v) => update({ vatId: v })} placeholder="VAT/Tax ID" />
      <Field label="Invoice Name" value={value.invoiceName} onChangeText={(v) => update({ invoiceName: v })} placeholder="Company or Person" />
      <Field label="Address" value={value.address1} onChangeText={(v) => update({ address1: v })} placeholder="Street address" />
      <View style={{ flexDirection: 'row', gap: 8 }}>
        <View style={{ flex: 1 }}>
          <Field label="City" value={value.city} onChangeText={(v) => update({ city: v })} placeholder="City" />
        </View>
        <View style={{ flex: 1 }}>
          <Field label="ZIP" value={value.zip} onChangeText={(v) => update({ zip: v })} placeholder="ZIP" />
        </View>
      </View>
    </View>
  )
}

export default React.memo(TaxForm)


