import React from 'react'
import { View, TouchableOpacity } from 'react-native'
import { Text } from '../../ui/Text'
import { tokens } from '../../ui/tokens'
import { SourceScope } from '../../types/knowledge'

export interface ScopePickerProps {
  value: SourceScope
  onChange: (v: SourceScope) => void
  testID?: string
}

const ScopePicker: React.FC<ScopePickerProps> = ({ value, onChange, testID }) => {
  const scopes: SourceScope[] = ['global', 'agent:demo']
  return (
    <View testID={testID} style={{ flexDirection: 'row', gap: 8 }}>
      {scopes.map((s) => (
        <TouchableOpacity key={s} onPress={() => onChange(s)} accessibilityLabel={`Scope ${s}`} accessibilityRole="button" style={{ paddingHorizontal: 12, paddingVertical: 8, borderRadius: 12, borderWidth: 1, borderColor: value === s ? tokens.colors.primary : tokens.colors.border, minHeight: 44, justifyContent: 'center' }}>
          <Text size={12} weight="600" color={value === s ? tokens.colors.primary : tokens.colors.mutedForeground}>{s}</Text>
        </TouchableOpacity>
      ))}
    </View>
  )
}

export default React.memo(ScopePicker)


