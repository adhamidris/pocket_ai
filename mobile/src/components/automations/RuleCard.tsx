import React from 'react'
import { View, Text, TouchableOpacity, Switch } from 'react-native'
import { tokens } from '../../ui/tokens'
import { Rule } from '../../types/automations'
import { Clock } from 'lucide-react-native'

export interface RuleCardProps {
  rule: Rule
  onToggle: () => void
  onEdit: () => void
  onReorder: (dir: 'up'|'down') => void
  testID?: string
  queued?: boolean
}

const RuleCard: React.FC<RuleCardProps> = ({ rule, onToggle, onEdit, onReorder, testID, queued }) => {
  return (
    <View testID={testID} style={{ borderWidth: 1, borderColor: tokens.colors.border, borderRadius: 12, padding: 12 }}>
      <View style={{ flexDirection: 'row', alignItems: 'center', justifyContent: 'space-between' }}>
        <View style={{ flexDirection: 'row', alignItems: 'center', gap: 6, flex: 1, marginRight: 12 }}>
          <Text style={{ color: tokens.colors.cardForeground, fontWeight: '700', flexShrink: 1 }} numberOfLines={1}>{rule.name}</Text>
          {queued ? <Clock size={14} color={tokens.colors.mutedForeground as any} /> : null}
        </View>
        <Switch value={rule.enabled} onValueChange={onToggle} accessibilityLabel={`Toggle ${rule.name}`} />
      </View>
      <View style={{ flexDirection: 'row', alignItems: 'center', gap: 8, marginTop: 8 }}>
        <TouchableOpacity onPress={() => onReorder('up')} accessibilityLabel="Move up" accessibilityRole="button" style={{ paddingHorizontal: 12, paddingVertical: 8, borderRadius: 10, borderWidth: 1, borderColor: tokens.colors.border }}>
          <Text style={{ color: tokens.colors.mutedForeground, fontWeight: '600' }}>↑</Text>
        </TouchableOpacity>
        <TouchableOpacity onPress={() => onReorder('down')} accessibilityLabel="Move down" accessibilityRole="button" style={{ paddingHorizontal: 12, paddingVertical: 8, borderRadius: 10, borderWidth: 1, borderColor: tokens.colors.border }}>
          <Text style={{ color: tokens.colors.mutedForeground, fontWeight: '600' }}>↓</Text>
        </TouchableOpacity>
        <TouchableOpacity onPress={onEdit} accessibilityLabel="Edit rule" accessibilityRole="button" style={{ paddingHorizontal: 12, paddingVertical: 8, borderRadius: 10, borderWidth: 1, borderColor: tokens.colors.border }}>
          <Text style={{ color: tokens.colors.cardForeground, fontWeight: '600' }}>Edit</Text>
        </TouchableOpacity>
      </View>
    </View>
  )
}

export default React.memo(RuleCard)


