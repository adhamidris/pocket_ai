import React from 'react'
import { View, Text } from 'react-native'
import { useTheme } from '../../providers/ThemeProvider'
import { SimResult } from '../../types/actions'

export const SimResultCard: React.FC<{ res?: SimResult }>=({ res }) => {
  const { theme } = useTheme()
  if (!res) return null
  return (
    <View style={{ padding: 12, borderRadius: 12, borderWidth: 1, borderColor: theme.color.border, backgroundColor: theme.color.card }}>
      <Text style={{ color: res.ok ? theme.color.success : theme.color.error, fontWeight: '700' }}>{res.ok ? 'OK' : 'Failed'}</Text>
      <Text style={{ color: theme.color.cardForeground, marginTop: 6 }}>{res.preview}</Text>
      {!!res.warnings?.length && (
        <View style={{ marginTop: 8 }}>
          <Text style={{ color: theme.color.warning, fontWeight: '600' }}>Warnings</Text>
          {res.warnings.map((w, i) => (
            <Text key={i} style={{ color: theme.color.mutedForeground }}>• {w}</Text>
          ))}
        </View>
      )}
      {!!res.errors?.length && (
        <View style={{ marginTop: 8 }}>
          <Text style={{ color: theme.color.error, fontWeight: '600' }}>Errors</Text>
          {res.errors.map((e, i) => (
            <Text key={i} style={{ color: theme.color.mutedForeground }}>• {e}</Text>
          ))}
        </View>
      )}
    </View>
  )
}

export default SimResultCard


