import React from 'react'
import { View, Text, TouchableOpacity } from 'react-native'
import { useTheme } from '../../providers/ThemeProvider'
import { ActionRun } from '../../types/actions'

export const RunRow: React.FC<{ run: ActionRun; onOpen?: () => void }>=({ run, onOpen }) => {
  const { theme } = useTheme()
  return (
    <TouchableOpacity onPress={onOpen} accessibilityRole="button" accessibilityLabel={`Open run ${run.id}`} style={{ paddingHorizontal: 12, paddingVertical: 10, borderRadius: 12, borderWidth: 1, borderColor: theme.color.border, backgroundColor: theme.color.card }}>
      <Text style={{ color: theme.color.cardForeground, fontWeight: '700' }}>{run.actionId}</Text>
      <Text style={{ color: theme.color.mutedForeground, marginTop: 4, fontSize: 12 }}>{run.state} • {new Date(run.createdAt).toLocaleString()}</Text>
      {!!run.result?.preview && (
        <Text style={{ color: theme.color.mutedForeground, marginTop: 6 }} numberOfLines={2}>{run.result.preview}</Text>
      )}
    </TouchableOpacity>
  )
}

export default RunRow


