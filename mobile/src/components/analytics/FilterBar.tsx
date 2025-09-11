import React from 'react'
import { View, Text, TouchableOpacity } from 'react-native'
import { tokens } from '../../ui/tokens'

export interface AnalyticsFilterBarProps { filters: Record<string, any>; onChange: (key: string, val: any) => void; testID?: string }

const AnalyticsFilterBar: React.FC<AnalyticsFilterBarProps> = ({ filters, onChange, testID }) => {
  const mk = (label: string, key: string, val: any) => (
    <TouchableOpacity key={key} onPress={() => onChange(key, filters[key] ? undefined : val)} accessibilityLabel={`Filter ${label}`} accessibilityRole="button" style={{ paddingHorizontal: 12, paddingVertical: 8, borderRadius: 12, borderWidth: 1, borderColor: filters[key] ? tokens.colors.primary : tokens.colors.border }}>
      <Text style={{ color: filters[key] ? tokens.colors.primary : tokens.colors.mutedForeground }}>{label}: {filters[key] || 'any'}</Text>
    </TouchableOpacity>
  )
  return (
    <View testID={testID} style={{ flexDirection: 'row', flexWrap: 'wrap', gap: 8 }}>
      {mk('Channel', 'channel', 'whatsapp')}
      {mk('Intent', 'intent', 'order')}
      {mk('Agent', 'agent', 'Nancy')}
      {mk('Segment', 'segment', 'VIP')}
      {mk('Priority', 'priority', 'high')}
    </View>
  )
}

export default React.memo(AnalyticsFilterBar)


