import React from 'react'
import { View, Text, TouchableOpacity, TextInput } from 'react-native'
import { useTheme } from '../../providers/ThemeProvider'
import { CsatForm } from '../../types/portal'

export interface CsatPanelProps { value: CsatForm; onChange: (v: CsatForm) => void; onSubmit: () => void; testID?: string }

const CsatPanel: React.FC<CsatPanelProps> = ({ value, onChange, onSubmit, testID }) => {
  const { theme } = useTheme()
  return (
    <View testID={testID} style={{ borderWidth: 1, borderColor: theme.color.border, borderRadius: 12, padding: 12, backgroundColor: theme.color.card }}>
      <Text style={{ color: theme.color.cardForeground, fontWeight: '700', marginBottom: 8 }}>Rate your chat</Text>
      <View style={{ flexDirection: 'row', gap: 8, marginBottom: 8 }}>
        {[1,2,3,4,5].map((r) => (
          <TouchableOpacity key={r} onPress={() => onChange({ ...value, rating: r as any })} accessibilityRole="button" accessibilityLabel={`Rate ${r}`} style={{ paddingHorizontal: 10, paddingVertical: 8, borderRadius: 10, borderWidth: 1, borderColor: value.rating === r ? theme.color.primary : theme.color.border, backgroundColor: value.rating === r ? theme.color.primary : theme.color.card, minHeight: 44 }}>
            <Text style={{ color: value.rating === r ? theme.color.primaryForeground : theme.color.cardForeground, fontWeight: '700' }}>{r}</Text>
          </TouchableOpacity>
        ))}
      </View>
      <TextInput value={value.comment} onChangeText={(t) => onChange({ ...value, comment: t })} placeholder="Leave a comment (optional)" placeholderTextColor={theme.color.placeholder} style={{ borderWidth: 1, borderColor: theme.color.border, borderRadius: 10, paddingHorizontal: 10, paddingVertical: 8, color: theme.color.cardForeground, minHeight: 44, marginBottom: 8 }} />
      <TouchableOpacity onPress={onSubmit} accessibilityRole="button" accessibilityLabel="Submit rating" style={{ alignSelf: 'flex-start', paddingHorizontal: 12, paddingVertical: 10, borderRadius: 10, backgroundColor: theme.color.primary }}>
        <Text style={{ color: theme.color.primaryForeground, fontWeight: '700' }}>Submit</Text>
      </TouchableOpacity>
    </View>
  )
}

export default React.memo(CsatPanel)


