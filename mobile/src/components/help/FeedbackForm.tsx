import React, { useState } from 'react'
import { View, Text, TextInput, TouchableOpacity } from 'react-native'
import { useTheme } from '../../providers/ThemeProvider'

export const FeedbackForm: React.FC<{ onSubmit?: (data: { text: string; rating?: number; contact?: string }) => void }> = ({ onSubmit }) => {
  const { theme } = useTheme()
  const [text, setText] = useState('')
  const [contact, setContact] = useState('')
  const [rating, setRating] = useState<number | undefined>()

  return (
    <View style={{ gap: 12 }}>
      <Text style={{ color: theme.color.cardForeground, fontWeight: '600' }}>How can we improve?</Text>
      <View style={{ flexDirection: 'row', gap: 8 }}>
        {[1,2,3,4,5].map(v => (
          <TouchableOpacity key={v} onPress={() => setRating(v)} accessibilityLabel={`Rate ${v}`} style={{ width: 44, height: 44, borderRadius: 12, backgroundColor: rating === v ? theme.color.primary : theme.color.secondary, alignItems: 'center', justifyContent: 'center' }}>
            <Text style={{ color: rating === v ? '#fff' : theme.color.cardForeground, fontWeight: '700' }}>{v}</Text>
          </TouchableOpacity>
        ))}
      </View>
      <TextInput
        value={text}
        onChangeText={setText}
        placeholder="Your feedback"
        placeholderTextColor={theme.color.placeholder}
        style={{ minHeight: 100, textAlignVertical: 'top', borderWidth: 1, borderColor: theme.color.border, borderRadius: 12, color: theme.color.cardForeground, padding: 12 }}
        multiline
        accessibilityLabel="Feedback text"
      />
      <TextInput
        value={contact}
        onChangeText={setContact}
        placeholder="Contact (optional)"
        placeholderTextColor={theme.color.placeholder}
        style={{ height: 44, borderWidth: 1, borderColor: theme.color.border, borderRadius: 12, color: theme.color.cardForeground, paddingHorizontal: 12 }}
        accessibilityLabel="Contact"
      />
      <TouchableOpacity
        onPress={() => onSubmit?.({ text, rating, contact })}
        accessibilityLabel="Send feedback"
        style={{ height: 44, borderRadius: 12, backgroundColor: theme.color.primary, alignItems: 'center', justifyContent: 'center' }}
      >
        <Text style={{ color: '#fff', fontWeight: '700' }}>Send</Text>
      </TouchableOpacity>
    </View>
  )
}


