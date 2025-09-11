import React from 'react'
import { View, Text, TouchableOpacity, Switch } from 'react-native'
import { IpRule } from '../../types/security'

export interface IpRuleRowProps { rule: IpRule; onToggle: () => void; onEdit: () => void; onDelete: () => void; testID?: string }

const IpRuleRow: React.FC<IpRuleRowProps> = ({ rule, onToggle, onEdit, onDelete, testID }) => {
  return (
    <View testID={testID} style={{ paddingVertical: 10, borderBottomWidth: 1, borderBottomColor: '#1f2937', flexDirection: 'row', alignItems: 'center', justifyContent: 'space-between' }}>
      <View style={{ flex: 1 }}>
        <Text style={{ color: '#e5e7eb', fontWeight: '600' }}>{rule.cidr}</Text>
        {rule.note ? <Text style={{ color: '#9ca3af', fontSize: 12 }}>{rule.note}</Text> : null}
      </View>
      <View style={{ flexDirection: 'row', alignItems: 'center', gap: 8 }}>
        <Switch value={rule.enabled} onValueChange={onToggle} accessibilityLabel={`Enable ${rule.cidr}`} />
        <TouchableOpacity onPress={onEdit} accessibilityRole="button" accessibilityLabel={`Edit ${rule.cidr}`} style={{ paddingHorizontal: 10, paddingVertical: 6, borderRadius: 8, borderWidth: 1, borderColor: '#374151' }}>
          <Text style={{ color: '#e5e7eb' }}>Edit</Text>
        </TouchableOpacity>
        <TouchableOpacity onPress={onDelete} accessibilityRole="button" accessibilityLabel={`Delete ${rule.cidr}`} style={{ paddingHorizontal: 10, paddingVertical: 6, borderRadius: 8, borderWidth: 1, borderColor: '#374151' }}>
          <Text style={{ color: '#ef4444' }}>Delete</Text>
        </TouchableOpacity>
      </View>
    </View>
  )
}

export default React.memo(IpRuleRow)


