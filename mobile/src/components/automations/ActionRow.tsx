import React from 'react'
import { View, Text, TextInput } from 'react-native'
import { tokens } from '../../ui/tokens'
import { Action } from '../../types/automations'

export interface ActionRowProps { action: Action; onChange: (a: Action) => void; testID?: string }

const ActionRow: React.FC<ActionRowProps> = ({ action, onChange, testID }) => {
  const [keyStr, setKeyStr] = React.useState(action.key)
  const [params, setParams] = React.useState<string>(JSON.stringify(action.params || {}))

  React.useEffect(() => {
    try {
      const p = JSON.parse(params || '{}')
      onChange({ key: keyStr as any, params: p })
    } catch {
      // ignore parse errors in UI-only
    }
  }, [keyStr, params])

  return (
    <View testID={testID} style={{ padding: 12, borderWidth: 1, borderColor: tokens.colors.border, borderRadius: 12 }}>
      <Text style={{ color: tokens.colors.mutedForeground, marginBottom: 6 }}>Action</Text>
      <View style={{ gap: 8 }}>
        <TextInput value={String(keyStr)} onChangeText={(t) => setKeyStr(t as any)} placeholder="action key" placeholderTextColor={tokens.colors.placeholder as any} style={{ borderWidth: 1, borderColor: tokens.colors.border, borderRadius: 10, paddingHorizontal: 10, paddingVertical: 8, color: tokens.colors.cardForeground }} />
        <TextInput value={params} onChangeText={setParams} placeholder='{"to":"agent:nancy"}' placeholderTextColor={tokens.colors.placeholder as any} style={{ borderWidth: 1, borderColor: tokens.colors.border, borderRadius: 10, paddingHorizontal: 10, paddingVertical: 8, color: tokens.colors.cardForeground }} />
      </View>
    </View>
  )
}

export default React.memo(ActionRow)


