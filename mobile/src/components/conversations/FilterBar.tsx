import React from 'react'
import { TouchableOpacity } from 'react-native'
import Box from '../../ui/Box'
import { Text } from '../../ui/Text'
import { tokens } from '../../ui/tokens'
import { FilterKey } from '../../types/conversations'

export interface FilterBarProps {
  active: Partial<Record<FilterKey, string>>
  onChange: (key: FilterKey, value?: string) => void
  testID?: string
}

const Chip: React.FC<{ label: string; active: boolean; onPress: () => void; testID?: string }> = ({ label, active, onPress, testID }) => (
  <TouchableOpacity
    testID={testID}
    onPress={onPress}
    accessibilityLabel={`Filter ${label}`}
    accessibilityRole="button"
    hitSlop={{ top: 8, bottom: 8, left: 8, right: 8 }}
    style={{
      paddingHorizontal: 12,
      paddingVertical: 8,
      borderRadius: 12,
      borderWidth: 1,
      borderColor: active ? tokens.colors.primary : tokens.colors.border,
      backgroundColor: active ? tokens.colors.card : 'transparent',
      minHeight: 44,
      justifyContent: 'center',
    }}
  >
    <Text size={12} weight="600" color={active ? tokens.colors.primary : tokens.colors.mutedForeground}>{label}</Text>
  </TouchableOpacity>
)

const FilterBar: React.FC<FilterBarProps> = ({ active, onChange, testID }) => {
  return (
    <Box testID={testID} row wrap gap={8}>
      <Chip label="Urgent" active={!!active.urgent} onPress={() => onChange('urgent', active.urgent ? undefined : '1')} testID="conv-filter-urgent" />
      <Chip label="Waiting >30m" active={!!active.waiting30} onPress={() => onChange('waiting30', active.waiting30 ? undefined : '1')} testID="conv-filter-waiting30" />
      <Chip label="Unassigned" active={!!active.unassigned} onPress={() => onChange('unassigned', active.unassigned ? undefined : '1')} testID="conv-filter-unassigned" />
      <Chip label="SLA Risk" active={!!active.slaRisk} onPress={() => onChange('slaRisk', active.slaRisk ? undefined : '1')} testID="conv-filter-slarisk" />
      <Chip label="VIP" active={!!active.vip} onPress={() => onChange('vip', active.vip ? undefined : '1')} testID="conv-filter-vip" />

      {/* Placeholder dropdown chips (non-interactive for now) */}
      <Chip label="Channel" active={!!active.channel} onPress={() => onChange('channel', active.channel ? undefined : 'whatsapp')} testID="conv-filter-channel" />
      <Chip label="Intent" active={!!active.intent} onPress={() => onChange('intent', active.intent ? undefined : 'order_status')} testID="conv-filter-intent" />
      <Chip label="Tag" active={!!active.tag} onPress={() => onChange('tag', active.tag ? undefined : 'billing')} testID="conv-filter-tag" />
    </Box>
  )
}

export default React.memo(FilterBar)


