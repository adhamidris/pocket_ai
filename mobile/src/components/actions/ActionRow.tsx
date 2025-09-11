import React from 'react'
import { TouchableOpacity, View, Text } from 'react-native'
import { useTheme } from '../../providers/ThemeProvider'
import { ActionSpec } from '../../types/actions'
import RiskBadge from './RiskBadge'

export const ActionRow: React.FC<{ spec: ActionSpec; onOpen: () => void; testID?: string }>
  = ({ spec, onOpen, testID }) => {
  const { theme } = useTheme()
  return (
    <TouchableOpacity onPress={onOpen} accessibilityRole="button" accessibilityLabel={`Open action ${spec.name}`} testID={testID ?? `act-catalog-row-${spec.id}`}
      style={{ paddingHorizontal: 12, paddingVertical: 10, borderRadius: 12, borderWidth: 1, borderColor: theme.color.border, backgroundColor: theme.color.card }}
    >
      <View style={{ flexDirection: 'row', alignItems: 'center', justifyContent: 'space-between' }}>
        <View style={{ flex: 1, paddingRight: 12 }}>
          <Text style={{ color: theme.color.cardForeground, fontWeight: '700' }}>{spec.name}</Text>
          <Text style={{ color: theme.color.mutedForeground, marginTop: 4 }}>{spec.summary}</Text>
          <Text style={{ color: theme.color.mutedForeground, marginTop: 4, fontSize: 12 }}>{spec.category} • v{spec.version} • {spec.kind}</Text>
        </View>
        <RiskBadge risk={spec.riskLevel} />
      </View>
    </TouchableOpacity>
  )
}

export default ActionRow


