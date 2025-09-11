import React from 'react'
import { View, Switch } from 'react-native'
import { RedactionRule } from '../../types/knowledge'
import { Text } from '../../ui/Text'
import { tokens } from '../../ui/tokens'

export interface RedactionRuleRowProps { rule: RedactionRule; onToggle: (id: string, v: boolean) => void; testID?: string }

const RedactionRuleRow: React.FC<RedactionRuleRowProps> = ({ rule, onToggle, testID }) => {
  return (
    <View testID={testID} style={{ borderWidth: 1, borderColor: tokens.colors.border, borderRadius: 12, padding: 12 }}>
      <View style={{ flexDirection: 'row', alignItems: 'center', justifyContent: 'space-between' }}>
        <View style={{ flex: 1, marginRight: 12 }}>
          <Text size={14} weight="600" color={tokens.colors.cardForeground}>{rule.pattern}</Text>
          {rule.description && <Text size={12} color={tokens.colors.mutedForeground}>{rule.description}</Text>}
          {rule.sample && <Text size={12} color={tokens.colors.mutedForeground}>e.g., {rule.sample}</Text>}
        </View>
        <Switch value={rule.enabled} onValueChange={(v) => onToggle(rule.id, v)} accessibilityLabel={`Toggle redaction ${rule.pattern}`} />
      </View>
    </View>
  )
}

export default React.memo(RedactionRuleRow)


