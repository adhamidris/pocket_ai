import React from 'react'
import { View, Text, TextInput } from 'react-native'
import { CapacityHint } from '../../types/agents'
import { tokens } from '../../ui/tokens'

export interface CapacityEditorProps {
  value?: CapacityHint
  onChange: (v?: CapacityHint) => void
  testID?: string
}

const CapacityEditor: React.FC<CapacityEditorProps> = ({ value, onChange, testID }) => {
  const [concurrent, setConcurrent] = React.useState<string>(String(value?.concurrent ?? ''))
  const [backlog, setBacklog] = React.useState<string>(value?.backlogMax != null ? String(value.backlogMax) : '')

  React.useEffect(() => {
    const v: CapacityHint = { concurrent: Number(concurrent || 0) }
    if (backlog !== '') v.backlogMax = Number(backlog)
    onChange(v)
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [concurrent, backlog])

  return (
    <View testID={testID}>
      <View style={{ flexDirection: 'row', alignItems: 'center', gap: 8, marginBottom: 8 }}>
        <Text style={{ color: tokens.colors.mutedForeground, width: 120 }}>Concurrent</Text>
        <TextInput value={concurrent} onChangeText={setConcurrent} keyboardType="number-pad" placeholder="3" placeholderTextColor={tokens.colors.placeholder as any} style={{ flex: 1, color: tokens.colors.cardForeground, borderBottomWidth: 1, borderBottomColor: tokens.colors.border }} />
      </View>
      <View style={{ flexDirection: 'row', alignItems: 'center', gap: 8 }}>
        <Text style={{ color: tokens.colors.mutedForeground, width: 120 }}>Backlog Max</Text>
        <TextInput value={backlog} onChangeText={setBacklog} keyboardType="number-pad" placeholder="20" placeholderTextColor={tokens.colors.placeholder as any} style={{ flex: 1, color: tokens.colors.cardForeground, borderBottomWidth: 1, borderBottomColor: tokens.colors.border }} />
      </View>
    </View>
  )
}

export default React.memo(CapacityEditor)



