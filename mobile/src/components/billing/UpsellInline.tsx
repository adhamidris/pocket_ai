import React from 'react'
import { View, Text, TouchableOpacity } from 'react-native'
import { useNavigation } from '@react-navigation/native'
import { tokens } from '../../ui/tokens'

export interface UpsellInlineProps { message: string; highlightPlanId?: string }

const UpsellInline: React.FC<UpsellInlineProps> = ({ message, highlightPlanId }) => {
  const navigation = useNavigation<any>()
  return (
    <View style={{ borderWidth: 1, borderColor: tokens.colors.border, borderRadius: 12, padding: 12, backgroundColor: tokens.colors.card }}>
      <Text style={{ color: tokens.colors.cardForeground, marginBottom: 6 }}>{message}</Text>
      <TouchableOpacity onPress={() => navigation.navigate('PlanMatrix', { highlightPlanId })} accessibilityRole="button" accessibilityLabel="Upgrade plan" style={{ alignSelf: 'flex-start', paddingHorizontal: 12, paddingVertical: 10, borderRadius: 10, backgroundColor: tokens.colors.primary }}>
        <Text style={{ color: tokens.colors.primaryForeground, fontWeight: '700' }}>Upgrade</Text>
      </TouchableOpacity>
    </View>
  )
}

export default UpsellInline


