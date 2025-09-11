import React from 'react'
import { View, Text, TouchableOpacity, ScrollView } from 'react-native'
import { tokens } from '../../ui/tokens'
import { Currency } from '../../types/billing'
import { useCurrency } from './currency'

const options: Currency[] = ['USD','EUR','GBP','EGP','AED','SAR']

export interface CurrencySelectorProps { testID?: string }

const CurrencySelector: React.FC<CurrencySelectorProps> = ({ testID }) => {
  const [cur, setCur] = useCurrency()

  return (
    <View testID={testID} style={{ flexDirection: 'row', alignItems: 'center', gap: 8 }}>
      <Text style={{ color: tokens.colors.mutedForeground }}>Currency:</Text>
      <ScrollView horizontal showsHorizontalScrollIndicator={false}>
        <View style={{ flexDirection: 'row', gap: 6 }}>
          {options.map((c) => (
            <TouchableOpacity key={c} onPress={() => setCur(c)} accessibilityRole="button" accessibilityLabel={`Set ${c}`} style={{ paddingHorizontal: 10, paddingVertical: 6, borderRadius: 10, borderWidth: 1, borderColor: cur === c ? tokens.colors.primary : tokens.colors.border, backgroundColor: cur === c ? tokens.colors.primary : tokens.colors.card }}>
              <Text style={{ color: cur === c ? tokens.colors.primaryForeground : tokens.colors.cardForeground, fontWeight: '700' }}>{c}</Text>
            </TouchableOpacity>
          ))}
        </View>
      </ScrollView>
    </View>
  )
}

export default CurrencySelector


