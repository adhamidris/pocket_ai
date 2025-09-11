import React from 'react'
import { View, Text, TouchableOpacity } from 'react-native'
import { tokens } from '../../ui/tokens'
import { Interval } from '../../types/billing'

export interface PriceToggleProps { value: Interval; onChange: (v: Interval) => void; testID?: string }

const ToggleButton: React.FC<{ label: string; active: boolean; onPress: () => void }> = ({ label, active, onPress }) => (
  <TouchableOpacity
    onPress={onPress}
    accessibilityRole="button"
    accessibilityLabel={`Price interval ${label}`}
    style={{
      paddingVertical: 8,
      paddingHorizontal: 12,
      borderRadius: 8,
      borderWidth: 1,
      borderColor: active ? tokens.colors.primary : tokens.colors.border,
      backgroundColor: active ? tokens.colors.primary : tokens.colors.card,
      minHeight: 44
    }}
  >
    <Text style={{ color: active ? tokens.colors.primaryForeground : tokens.colors.cardForeground, fontWeight: '700' }}>{label}</Text>
  </TouchableOpacity>
)

const PriceToggle: React.FC<PriceToggleProps> = ({ value, onChange, testID }) => {
  return (
    <View testID={testID} style={{ flexDirection: 'row', gap: 8 }}>
      <ToggleButton label="Monthly" active={value === 'month'} onPress={() => onChange('month')} />
      <ToggleButton label="Yearly" active={value === 'year'} onPress={() => onChange('year')} />
    </View>
  )
}

export default React.memo(PriceToggle)


