import React from 'react'
import { View, Text, TextInput, Switch } from 'react-native'
import { useTheme } from '../../providers/ThemeProvider'
import { AllowRule } from '../../types/actions'

export const GuardEditor: React.FC<{ value?: AllowRule['guard']; onChange: (v: AllowRule['guard']) => void }>
  = ({ value, onChange }) => {
  const { theme } = useTheme()
  const [maxRecords, setMaxRecords] = React.useState<string>(value?.maxRecords ? String(value?.maxRecords) : '')
  const [maxAmount, setMaxAmount] = React.useState<string>(value?.maxAmount ? String(value?.maxAmount) : '')
  const [bhOnly, setBhOnly] = React.useState<boolean>(!!value?.businessHoursOnly)
  React.useEffect(() => onChange({ maxRecords: Number(maxRecords) || undefined, maxAmount: Number(maxAmount) || undefined, businessHoursOnly: bhOnly }), [maxRecords, maxAmount, bhOnly])
  return (
    <View style={{ gap: 8 }}>
      <Text style={{ color: theme.color.cardForeground, fontWeight: '700' }}>Guards</Text>
      <View style={{ flexDirection: 'row', gap: 8 }}>
        <View style={{ flex: 1, borderWidth: 1, borderColor: theme.color.border, borderRadius: 10, backgroundColor: theme.color.card }}>
          <TextInput value={maxRecords} onChangeText={setMaxRecords} keyboardType="numeric" placeholder="Max records" placeholderTextColor={theme.color.placeholder} style={{ paddingHorizontal: 10, paddingVertical: 8, color: theme.color.cardForeground }} />
        </View>
        <View style={{ flex: 1, borderWidth: 1, borderColor: theme.color.border, borderRadius: 10, backgroundColor: theme.color.card }}>
          <TextInput value={maxAmount} onChangeText={setMaxAmount} keyboardType="numeric" placeholder="Max amount" placeholderTextColor={theme.color.placeholder} style={{ paddingHorizontal: 10, paddingVertical: 8, color: theme.color.cardForeground }} />
        </View>
      </View>
      <View style={{ flexDirection: 'row', alignItems: 'center', gap: 8 }}>
        <Text style={{ color: theme.color.mutedForeground }}>Business hours only</Text>
        <Switch value={bhOnly} onValueChange={setBhOnly} accessibilityLabel="Business hours only" />
      </View>
    </View>
  )
}

export default GuardEditor


