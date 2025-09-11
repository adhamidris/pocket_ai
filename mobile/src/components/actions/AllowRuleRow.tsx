import React from 'react'
import { View, Text, TouchableOpacity, Switch } from 'react-native'
import { useTheme } from '../../providers/ThemeProvider'
import { AllowRule } from '../../types/actions'
import ApprovalBadge from './ApprovalBadge'

export const AllowRuleRow: React.FC<{ rule: AllowRule; onEdit: () => void; onToggle: () => void; onDelete: () => void }>
  = ({ rule, onEdit, onToggle, onDelete }) => {
  const { theme } = useTheme()
  const [enabled, setEnabled] = React.useState<boolean>(true)
  return (
    <View style={{ paddingHorizontal: 12, paddingVertical: 10, borderRadius: 12, borderWidth: 1, borderColor: theme.color.border, backgroundColor: theme.color.card }}>
      <View style={{ flexDirection: 'row', alignItems: 'center', justifyContent: 'space-between' }}>
        <View style={{ flex: 1, paddingRight: 12 }}>
          <Text style={{ color: theme.color.cardForeground, fontWeight: '700' }}>{rule.actionId}</Text>
          <Text style={{ color: theme.color.mutedForeground, marginTop: 4, fontSize: 12 }}>Agents: {rule.agentIds?.length || 0} • Intents: {rule.intents?.length || 0}</Text>
          <View style={{ marginTop: 6 }}>
            <ApprovalBadge require={!!rule.requireApproval} role={rule.approverRole} />
          </View>
        </View>
        <Switch value={enabled} onValueChange={(v) => { setEnabled(v); onToggle() }} accessibilityLabel="Toggle rule" />
      </View>
      <View style={{ flexDirection: 'row', gap: 8, marginTop: 8 }}>
        <TouchableOpacity onPress={onEdit} accessibilityRole="button" accessibilityLabel="Edit allow rule" style={{ paddingHorizontal: 10, paddingVertical: 6, borderRadius: 8, borderWidth: 1, borderColor: theme.color.border }}>
          <Text style={{ color: theme.color.cardForeground }}>Edit</Text>
        </TouchableOpacity>
        <TouchableOpacity onPress={onDelete} accessibilityRole="button" accessibilityLabel="Delete allow rule" style={{ paddingHorizontal: 10, paddingVertical: 6, borderRadius: 8, borderWidth: 1, borderColor: theme.color.error }}>
          <Text style={{ color: theme.color.error }}>Delete</Text>
        </TouchableOpacity>
      </View>
    </View>
  )
}

export default AllowRuleRow


