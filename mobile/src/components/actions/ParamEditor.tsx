import React from 'react'
import { View, Text, TextInput, Switch } from 'react-native'
import { useTheme } from '../../providers/ThemeProvider'
import { ActionSpec, ParamSpec } from '../../types/actions'

export const ParamEditor: React.FC<{ spec: ActionSpec; value: Record<string, any>; onChange: (v: Record<string, any>) => void }>
  = ({ spec, value, onChange }) => {
  const { theme } = useTheme()
  const setVal = (k: string, v: any) => onChange({ ...value, [k]: v })

  const Field: React.FC<{ p: ParamSpec }>=({ p }) => {
    const v = value[p.key]
    if (p.type === 'boolean') return (
      <View style={{ flexDirection: 'row', alignItems: 'center', justifyContent: 'space-between', borderWidth: 1, borderColor: theme.color.border, borderRadius: 10, paddingHorizontal: 12, paddingVertical: 10 }}>
        <Text style={{ color: theme.color.cardForeground }}>{p.label}</Text>
        <Switch value={!!v} onValueChange={(nv) => setVal(p.key, nv)} accessibilityLabel={p.label} />
      </View>
    )
    return (
      <View>
        <Text style={{ color: theme.color.mutedForeground, marginBottom: 4 }}>{p.label}</Text>
        <View style={{ borderWidth: 1, borderColor: theme.color.border, borderRadius: 10, backgroundColor: theme.color.card }}>
          <TextInput
            value={v == null ? '' : String(v)}
            onChangeText={(t) => setVal(p.key, p.type === 'number' ? Number(t) : t)}
            placeholder={p.help || p.label}
            placeholderTextColor={theme.color.placeholder}
            keyboardType={p.type === 'number' ? 'numeric' : 'default'}
            style={{ paddingHorizontal: 12, paddingVertical: 10, color: theme.color.cardForeground }}
          />
        </View>
      </View>
    )
  }

  return (
    <View style={{ gap: 12 }}>
      {spec.params.map((p) => (
        <Field key={p.key} p={p} />
      ))}
    </View>
  )
}

export default ParamEditor


