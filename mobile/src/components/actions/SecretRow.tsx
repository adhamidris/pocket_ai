import React from 'react'
import { View, Text, TouchableOpacity } from 'react-native'
import { useTheme } from '../../providers/ThemeProvider'
import { SecretRef } from '../../types/actions'

export const SecretRow: React.FC<{ refItem: SecretRef; onRotate: () => void }>=({ refItem, onRotate }) => {
  const { theme } = useTheme()
  return (
    <View style={{ paddingHorizontal: 12, paddingVertical: 10, borderRadius: 12, borderWidth: 1, borderColor: theme.color.border, backgroundColor: theme.color.card }}>
      <Text style={{ color: theme.color.cardForeground, fontWeight: '700' }}>{refItem.key}</Text>
      {!!refItem.note && <Text style={{ color: theme.color.mutedForeground, marginTop: 4 }}>{refItem.note}</Text>}
      <View style={{ flexDirection: 'row', justifyContent: 'flex-end', marginTop: 8 }}>
        <TouchableOpacity onPress={onRotate} accessibilityRole="button" accessibilityLabel={`Rotate ${refItem.key}`} style={{ paddingHorizontal: 10, paddingVertical: 6, borderRadius: 8, borderWidth: 1, borderColor: theme.color.border }}>
          <Text style={{ color: theme.color.cardForeground, fontWeight: '600' }}>Rotate</Text>
        </TouchableOpacity>
      </View>
    </View>
  )
}

export default SecretRow


