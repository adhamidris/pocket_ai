import React from 'react'
import { View, Text, TouchableOpacity, Switch } from 'react-native'
import { AllowedAction } from '../../types/agents'
import { tokens } from '../../ui/tokens'
import EntitlementsGate from '../billing/EntitlementsGate'

export interface AllowlistEditorProps {
  items: AllowedAction[]
  onToggle: (key: string) => void
  testID?: string
}

const RiskBadge: React.FC<{ level?: 'low'|'medium'|'high' }> = ({ level }) => {
  if (!level) return null
  const color = level === 'low' ? tokens.colors.success : level === 'medium' ? tokens.colors.warning : tokens.colors.error
  return (
    <View style={{ paddingHorizontal: 8, paddingVertical: 4, borderRadius: 10, borderWidth: 1, borderColor: color, backgroundColor: (color as string) + '20' }}>
      <Text style={{ color, fontSize: 11, fontWeight: '700' }}>{level.toUpperCase()}</Text>
    </View>
  )
}

const AllowlistEditor: React.FC<AllowlistEditorProps> = ({ items, onToggle, testID }) => {
  return (
    <EntitlementsGate require="agentsAllowlist">
    <View testID={testID}>
      {items.map((a) => (
        <View key={a.key} style={{ flexDirection: 'row', alignItems: 'center', justifyContent: 'space-between', borderWidth: 1, borderColor: tokens.colors.border, borderRadius: 12, padding: 12, marginBottom: 8 }}>
          <View style={{ flex: 1, marginRight: 12 }}>
            <Text style={{ color: tokens.colors.cardForeground, fontWeight: '600' }}>{a.label}</Text>
            {!!a.description && <Text style={{ color: tokens.colors.mutedForeground, fontSize: 12, marginTop: 2 }}>{a.description}</Text>}
          </View>
          <View style={{ flexDirection: 'row', alignItems: 'center', gap: 8 }}>
            <RiskBadge level={a.risk} />
            <Switch value={a.enabled} onValueChange={() => onToggle(a.key)} accessibilityLabel={`Toggle ${a.label}`} />
            <TouchableOpacity onPress={() => { /* info tooltip stub */ }} accessibilityLabel={`Info about ${a.label}`} accessibilityRole="button" style={{ paddingHorizontal: 8, paddingVertical: 6 }}>
              <Text style={{ color: tokens.colors.mutedForeground }}>i</Text>
            </TouchableOpacity>
          </View>
        </View>
      ))}
    </View>
    </EntitlementsGate>
  )
}

export default React.memo(AllowlistEditor)



