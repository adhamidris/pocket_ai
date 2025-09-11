import React from 'react'
import { View, Text, TextInput, TouchableOpacity } from 'react-native'
import { useTheme } from '../../providers/ThemeProvider'

export interface TranscriptFormProps { email: string; onChange: (v: string) => void; onSend: () => void; testID?: string }

const TranscriptForm: React.FC<TranscriptFormProps> = ({ email, onChange, onSend, testID }) => {
  const { theme } = useTheme()
  return (
    <View testID={testID} style={{ borderWidth: 1, borderColor: theme.color.border, borderRadius: 12, padding: 12, backgroundColor: theme.color.card }}>
      <Text style={{ color: theme.color.cardForeground, fontWeight: '700', marginBottom: 8 }}>Send transcript</Text>
      <TextInput value={email} onChangeText={onChange} placeholder="you@example.com" placeholderTextColor={theme.color.placeholder} keyboardType="email-address" accessibilityLabel="Email" style={{ borderWidth: 1, borderColor: theme.color.border, borderRadius: 10, paddingHorizontal: 10, paddingVertical: 8, color: theme.color.cardForeground, minHeight: 44, marginBottom: 8 }} />
      <TouchableOpacity onPress={onSend} accessibilityRole="button" accessibilityLabel="Send transcript" style={{ alignSelf: 'flex-start', paddingHorizontal: 12, paddingVertical: 10, borderRadius: 10, backgroundColor: theme.color.primary }}>
        <Text style={{ color: theme.color.primaryForeground, fontWeight: '700' }}>Send</Text>
      </TouchableOpacity>
    </View>
  )
}

export default React.memo(TranscriptForm)


