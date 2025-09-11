import React from 'react'
import { View, Text, TouchableOpacity } from 'react-native'
import { useNavigation } from '@react-navigation/native'
import { track } from '../../lib/analytics'
import { tokens } from '../../ui/tokens'
import { useEntitlements } from './entitlements'

export interface EntitlementsGateProps {
  require: string
  limitKey?: string
  children: React.ReactNode
  testID?: string
}

const UpsellInline: React.FC<{ title: string; onUpgrade: () => void }> = ({ title, onUpgrade }) => (
  <View style={{ borderWidth: 1, borderColor: tokens.colors.border, borderRadius: 12, padding: 12, backgroundColor: tokens.colors.card }}>
    <Text style={{ color: tokens.colors.cardForeground, fontWeight: '700', marginBottom: 6 }}>{title}</Text>
    <Text style={{ color: tokens.colors.mutedForeground, marginBottom: 8 }}>This feature is available on higher plans. Upgrade to unlock.</Text>
    <TouchableOpacity onPress={onUpgrade} accessibilityRole="button" accessibilityLabel="Upgrade plan" style={{ alignSelf: 'flex-start', paddingHorizontal: 12, paddingVertical: 10, borderRadius: 10, backgroundColor: tokens.colors.primary }}>
      <Text style={{ color: tokens.colors.primaryForeground, fontWeight: '700' }}>Upgrade</Text>
    </TouchableOpacity>
  </View>
)

const EntitlementsGate: React.FC<EntitlementsGateProps> = ({ require, limitKey, children, testID }) => {
  const navigation = useNavigation<any>()
  const ents = useEntitlements()
  const e = ents[require]
  const allowed = e?.enabled
  if (!allowed) {
    try { track('upsell.view') } catch {}
    return <UpsellInline title="Upgrade required" onUpgrade={() => { try { track('upsell.cta', { from: require }) } catch {}; navigation.navigate('PlanMatrix') }} />
  }
  if (limitKey && e?.limit != null && e.limit !== 'unlimited') {
    // limit messaging could be added; for now just pass-through
  }
  return <View testID={testID}>{children}</View>
}

export default EntitlementsGate


