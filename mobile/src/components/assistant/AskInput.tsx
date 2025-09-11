import React, { useMemo, useRef, useState } from 'react'
import { TextInput, View, TouchableOpacity, Text } from 'react-native'
import { useTheme } from '../../providers/ThemeProvider'
import { Persona, Tone } from '../../types/assistant'
import { Button } from '../ui/Button'
import { Badge } from '../ui/Badge'
import Box from '../../ui/Box'
import Txt from '../../ui/Text'
import { PersonaSwitcher } from './PersonaSwitcher'
import { TonePicker } from './TonePicker'
import MicButton from './MicButton'
import Waveform from './Waveform'
import VoiceSettingsSheet from './VoiceSettingsSheet'
import { useVoiceSettings } from '../../hooks/useVoiceSettings'

export interface AskInputProps {
  value?: string
  onChangeText?: (text: string) => void
  onSend?: (text: string) => void
  persona?: Persona
  onPersonaChange?: (p: Persona) => void
  tone?: Tone
  onToneChange?: (t: Tone) => void
  rangeLabel?: string
  disabled?: boolean
}

export const AskInput: React.FC<AskInputProps> = ({
  value,
  onChangeText,
  onSend,
  persona = 'owner',
  onPersonaChange,
  tone = 'neutral',
  onToneChange,
  rangeLabel,
  disabled,
}) => {
  const { theme } = useTheme()
  const { prefs, update } = useVoiceSettings()
  const [openVoiceSheet, setOpenVoiceSheet] = useState(false)
  const [text, setText] = useState(value ?? '')
  const inputRef = useRef<TextInput | null>(null)

  const canSend = useMemo(() => text.trim().length > 0, [text])

  const handleSend = () => {
    if (!canSend || disabled) return
    onSend?.(text.trim())
    setText('')
    onChangeText?.('')
  }

  const handleRecordStart = () => {
    // animate handled by MicButton + Waveform
  }
  const handleRecordStop = () => {
    const stub = `Voice note ${Math.ceil(Math.random()*5)}s`
    setText(stub)
    onChangeText?.(stub)
    setTimeout(() => handleSend(), 50)
  }

  return (
    <View testID="asst-ask-input" style={{ backgroundColor: theme.color.card, borderWidth: 1, borderColor: theme.color.border, borderRadius: theme.radius.lg }}>
      <Box px={12} py={12} gap={12}>
        <Box row align="center" justify="space-between" gap={12}>
          <PersonaSwitcher value={persona} onChange={onPersonaChange} />
          <TonePicker value={tone} onChange={onToneChange} />
        </Box>

        {rangeLabel && (
          <Badge variant="secondary" size="sm">{rangeLabel}</Badge>
        )}

        <Box row align="flex-end" gap={12}>
          {prefs.enabled && (
            <Box row align="center" gap={8}>
              <MicButton pressBehavior={prefs.pushToTalk ? 'hold' : 'toggle'} onRecordStart={handleRecordStart} onRecordStop={handleRecordStop} />
              <Waveform />
            </Box>
          )}
          <View style={{ flex: 1, minHeight: 80, maxHeight: 160, borderWidth: 1, borderColor: theme.color.border, borderRadius: theme.radius.md, paddingHorizontal: 12, paddingVertical: 8, backgroundColor: theme.color.background, opacity: disabled ? 0.6 : 1 }}>
            <TextInput
              ref={inputRef}
              value={text}
              onChangeText={(t) => { setText(t); onChangeText?.(t) }}
              placeholder="Ask anything..."
              placeholderTextColor={theme.color.placeholder}
              multiline
              style={{ color: theme.color.foreground, fontSize: 16, lineHeight: 22 }}
              accessibilityLabel="Assistant question input"
              returnKeyType="send"
              onSubmitEditing={handleSend}
              editable={!disabled}
            />
          </View>
          <Button title="Send" onPress={handleSend} disabled={disabled || !canSend} variant="default" size="lg" accessibilityLabel="Send question" testID="asst-send" />
        </Box>

        <Box row align="center" justify="space-between">
          <Txt size={12} color={theme.color.mutedForeground}>Tip: Include a time range like "last 7 days"</Txt>
          <TouchableOpacity onPress={() => setOpenVoiceSheet(true)} accessibilityLabel="Voice settings" style={{ paddingHorizontal: 8, paddingVertical: 6 }}>
            <Txt size={12} color={theme.color.mutedForeground}>Voice Settings</Txt>
          </TouchableOpacity>
        </Box>
      </Box>
      <VoiceSettingsSheet visible={openVoiceSheet} onClose={() => setOpenVoiceSheet(false)} prefs={prefs} onChange={update} />
    </View>
  )
}

export default AskInput


