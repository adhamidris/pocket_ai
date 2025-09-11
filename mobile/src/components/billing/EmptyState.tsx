import React from 'react'
import { View, Text } from 'react-native'
import { tokens } from '../../ui/tokens'

export interface EmptyStateProps { title: string; subtitle?: string; testID?: string }

const EmptyState: React.FC<EmptyStateProps> = ({ title, subtitle, testID }) => {
  return (
    <View testID={testID} style={{ alignItems: 'center', padding: 12 }}>
      <View style={{ width: 64, height: 64, borderRadius: 32, backgroundColor: tokens.colors.muted, marginBottom: 8 }} />
      <Text style={{ color: tokens.colors.cardForeground, fontWeight: '700' }}>{title}</Text>
      {subtitle ? <Text style={{ color: tokens.colors.mutedForeground, marginTop: 4, textAlign: 'center' }}>{subtitle}</Text> : null}
    </View>
  )
}

export default React.memo(EmptyState)


