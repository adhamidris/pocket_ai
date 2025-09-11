import React from 'react'
import { View, Text, TouchableOpacity } from 'react-native'
import { tokens } from '../../ui/tokens'
import { DunningState } from '../../types/billing'

export interface DunningBannerProps { state?: DunningState; onUpdateCard: () => void; testID?: string }

const DunningBanner: React.FC<DunningBannerProps> = ({ state, onUpdateCard, testID }) => {
  if (!state) return null
  const message = state.lastChargeFailed
    ? state.lastFailureMessage || 'Last charge failed. Please update your card.'
    : state.cardExpiring
      ? 'Your card is expiring soon. Please update your card.'
      : 'Action required on your payment method.'

  return (
    <View testID={testID} style={{ borderWidth: 1, borderColor: tokens.colors.warning, backgroundColor: tokens.colors.warningMuted, padding: 10, borderRadius: 10 }}>
      <Text style={{ color: tokens.colors.warningForeground, marginBottom: 6 }}>{message}</Text>
      {state.graceUntil && (
        <Text style={{ color: tokens.colors.mutedForeground, marginBottom: 6 }}>Grace period until {new Date(state.graceUntil * 1000).toLocaleString()}</Text>
      )}
      <TouchableOpacity onPress={onUpdateCard} accessibilityRole="button" accessibilityLabel="Update card" style={{ alignSelf: 'flex-start', backgroundColor: tokens.colors.primary, paddingHorizontal: 12, paddingVertical: 8, borderRadius: 8, minHeight: 44 }}>
        <Text style={{ color: tokens.colors.primaryForeground, fontWeight: '700' }}>Update Card</Text>
      </TouchableOpacity>
    </View>
  )
}

export default React.memo(DunningBanner)


