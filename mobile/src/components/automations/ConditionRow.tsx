import React from 'react'
import { View, Text, TextInput, TouchableOpacity } from 'react-native'
import { tokens } from '../../ui/tokens'
import { Condition } from '../../types/automations'

export interface ConditionRowProps { cond: Condition; onChange: (c: Condition) => void; testID?: string }

const ConditionRow: React.FC<ConditionRowProps> = ({ cond, onChange, testID }) => {
  const [keyStr, setKeyStr] = React.useState(cond.key)
  const [opStr, setOpStr] = React.useState(cond.op)
  const [val, setVal] = React.useState<any>(cond.value ?? '')

  React.useEffect(() => { onChange({ key: keyStr as any, op: opStr as any, value: val }) }, [keyStr, opStr, val])

  return (
    <View testID={testID} style={{ padding: 12, borderWidth: 1, borderColor: tokens.colors.border, borderRadius: 12 }}>
      <Text style={{ color: tokens.colors.mutedForeground, marginBottom: 6 }}>Condition</Text>
      <View style={{ flexDirection: 'row', gap: 8 }}>
        <TextInput value={String(keyStr)} onChangeText={(t) => setKeyStr(t as any)} placeholder="key" placeholderTextColor={tokens.colors.placeholder as any} style={{ flex: 1, borderWidth: 1, borderColor: tokens.colors.border, borderRadius: 10, paddingHorizontal: 10, paddingVertical: 8, color: tokens.colors.cardForeground }} />
        <TextInput value={String(opStr)} onChangeText={(t) => setOpStr(t as any)} placeholder="op" placeholderTextColor={tokens.colors.placeholder as any} style={{ width: 90, borderWidth: 1, borderColor: tokens.colors.border, borderRadius: 10, paddingHorizontal: 10, paddingVertical: 8, color: tokens.colors.cardForeground }} />
        <TextInput value={String(val ?? '')} onChangeText={setVal} placeholder="value" placeholderTextColor={tokens.colors.placeholder as any} style={{ flex: 1, borderWidth: 1, borderColor: tokens.colors.border, borderRadius: 10, paddingHorizontal: 10, paddingVertical: 8, color: tokens.colors.cardForeground }} />
      </View>
    </View>
  )
}

export default React.memo(ConditionRow)


