import React from 'react'
import { View, Text, Switch, TouchableOpacity, TextInput } from 'react-native'
import { useTheme } from '../../providers/ThemeProvider'
import { SessionPolicy } from '../../types/security'

export interface SessionPolicyEditorProps { value: SessionPolicy; onChange: (v: SessionPolicy) => void; testID?: string }

const NumInput: React.FC<{ label: string; value: number; onChange: (v: number) => void }>=({ label, value, onChange }) => {
  const { theme } = useTheme()
  return (
    <View style={{ flexDirection: 'row', alignItems: 'center', justifyContent: 'space-between', marginBottom: 8 }}>
      <Text style={{ color: theme.color.cardForeground }}>{label}</Text>
      <TextInput value={String(value)} onChangeText={(t) => onChange(Number(t) || 0)} keyboardType="numeric" accessibilityLabel={label} style={{ minWidth: 72, borderWidth: 1, borderColor: theme.color.border, borderRadius: 8, paddingHorizontal: 8, paddingVertical: 6, color: theme.color.cardForeground }} />
    </View>
  )
}

const SessionPolicyEditor: React.FC<SessionPolicyEditorProps> = ({ value, onChange, testID }) => {
  const { theme } = useTheme()
  return (
    <View testID={testID}>
      <View style={{ flexDirection: 'row', alignItems: 'center', justifyContent: 'space-between', marginBottom: 8 }}>
        <Text style={{ color: theme.color.cardForeground }}>Require 2FA</Text>
        <Switch value={value.enforce2FA} onValueChange={(v) => onChange({ ...value, enforce2FA: v })} accessibilityLabel="Require 2FA" />
      </View>
      <NumInput label="Session hours" value={value.sessionHours} onChange={(n) => onChange({ ...value, sessionHours: n })} />
      <NumInput label="Idle timeout (mins)" value={value.idleTimeoutMins} onChange={(n) => onChange({ ...value, idleTimeoutMins: n })} />
      <NumInput label="Device limit" value={value.deviceLimit || 0} onChange={(n) => onChange({ ...value, deviceLimit: n })} />
    </View>
  )
}

export default React.memo(SessionPolicyEditor)


