import React from 'react'
import { View, Text } from 'react-native'
import { tokens } from '../../ui/tokens'

export interface NumberTileProps { label: string; value: number | string; delta?: number; helpText?: string; testID?: string }

const NumberTile: React.FC<NumberTileProps> = ({ label, value, delta, helpText, testID }) => {
  const deltaColor = delta == null ? tokens.colors.mutedForeground : delta >= 0 ? tokens.colors.success : tokens.colors.error
  const deltaSign = delta == null ? '' : delta >= 0 ? '+' : ''
  return (
    <View testID={testID} style={{ borderWidth: 1, borderColor: tokens.colors.border, borderRadius: 12, padding: 12, backgroundColor: tokens.colors.card, minHeight: 88 }}>
      <Text style={{ color: tokens.colors.mutedForeground, marginBottom: 6 }}>{label}</Text>
      <Text style={{ color: tokens.colors.cardForeground, fontSize: 22, fontWeight: '700' }}>{String(value)}</Text>
      <View style={{ flexDirection: 'row', alignItems: 'center', gap: 8, marginTop: 4 }}>
        {delta != null && (
          <Text style={{ color: deltaColor }}>{deltaSign}{delta}%</Text>
        )}
        {helpText ? <Text style={{ color: tokens.colors.mutedForeground }}>{helpText}</Text> : null}
      </View>
    </View>
  )
}

export default React.memo(NumberTile)


