import React from 'react'
import { View, TextInput, TouchableOpacity, Text } from 'react-native'
import { useTheme } from '../../providers/ThemeProvider'
import { ComposerState } from '../../types/portal'
import AttachmentPreview from './AttachmentPreview'
import { pickFakeFile, validateFile } from './AttachmentFlow'

export interface ComposerProps { state: ComposerState; onChange: (next: ComposerState) => void; onSend: () => void; onAttach?: (error?: string) => void; onMic?: () => void; allowVoice?: boolean; testID?: string }

const Composer: React.FC<ComposerProps> = ({ state, onChange, onSend, onAttach, onMic, allowVoice, testID }) => {
  const { theme } = useTheme()
  const disabled = !!state.disabled
  const remaining = Math.max(0, 500 - (state.text?.length || 0))
  return (
    <View testID={testID} style={{ borderTopWidth: 1, borderTopColor: theme.color.border, padding: 12, backgroundColor: theme.color.card }}>
      <AttachmentPreview files={state.attachments} onRemove={(name) => onChange({ ...state, attachments: state.attachments.filter(f => f.name !== name) })} />
      <View style={{ flexDirection: 'row', alignItems: 'center', gap: 8, marginTop: 8 }}>
        <TouchableOpacity onPress={() => {
          if (disabled) return
          const file = pickFakeFile()
          const res = validateFile(file)
          if ((res as any).ok) {
            onChange({ ...state, attachments: [...state.attachments, file] })
            onAttach && onAttach()
          } else {
            onAttach && onAttach((res as any).error)
          }
        }} disabled={disabled} accessibilityRole="button" accessibilityLabel="Attach" style={{ paddingHorizontal: 12, paddingVertical: 10, borderRadius: 10, borderWidth: 1, borderColor: theme.color.border, opacity: disabled ? 0.5 : 1, minHeight: 44 }}>
          <Text style={{ color: theme.color.cardForeground, fontWeight: '700' }}>＋</Text>
        </TouchableOpacity>
        <View style={{ flex: 1, borderWidth: 1, borderColor: theme.color.border, borderRadius: 12, backgroundColor: theme.color.background }}>
          <TextInput value={state.text} onChangeText={(t) => onChange({ ...state, text: t })} placeholder={state.placeholder || 'Type a message'} placeholderTextColor={theme.color.placeholder} editable={!disabled} multiline accessibilityLabel="Message input" style={{ paddingHorizontal: 12, paddingVertical: 10, color: theme.color.cardForeground, minHeight: 44 }} />
        </View>
        {allowVoice && (
          <TouchableOpacity onPress={onMic} disabled={disabled} accessibilityRole="button" accessibilityLabel="Voice" style={{ paddingHorizontal: 12, paddingVertical: 10, borderRadius: 10, borderWidth: 1, borderColor: theme.color.border, opacity: disabled ? 0.5 : 1, minHeight: 44 }}>
            <Text style={{ color: theme.color.cardForeground, fontWeight: '700' }}>🎤</Text>
          </TouchableOpacity>
        )}
        <TouchableOpacity onPress={onSend} disabled={disabled || !state.text?.trim()} accessibilityRole="button" accessibilityLabel="Send" style={{ paddingHorizontal: 12, paddingVertical: 10, borderRadius: 10, backgroundColor: theme.color.primary, opacity: disabled || !state.text?.trim() ? 0.5 : 1, minHeight: 44 }}>
          <Text style={{ color: theme.color.primaryForeground, fontWeight: '700' }}>Send</Text>
        </TouchableOpacity>
      </View>
      <Text style={{ color: theme.color.mutedForeground, fontSize: 12, marginTop: 4 }}>{remaining} chars</Text>
    </View>
  )
}

export default React.memo(Composer)


