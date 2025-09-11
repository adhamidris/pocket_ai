import React from 'react'
import { TextInput, TouchableOpacity, ActivityIndicator } from 'react-native'
import Box from '../../ui/Box'
import { Text } from '../../ui/Text'
import { tokens } from '../../ui/tokens'

export interface ComposerProps {
  value: string
  onChange: (v: string) => void
  onSend: () => void
  sending?: boolean
  testID?: string
}

const Composer: React.FC<ComposerProps> = ({ value, onChange, onSend, sending, testID }) => {
  const disabled = sending || !value.trim()
  return (
    <Box testID={testID} row align="flex-end" gap={8}>
      <Box style={{ flex: 1, borderWidth: 1, borderColor: tokens.colors.border, borderRadius: 12, backgroundColor: tokens.colors.background }}>
        <TextInput
          value={value}
          onChangeText={onChange}
          placeholder="Type a message"
          placeholderTextColor={tokens.colors.placeholder}
          multiline
          maxLength={2000}
          accessibilityLabel="Message input"
          style={{ paddingHorizontal: 12, paddingVertical: 10, color: tokens.colors.cardForeground, fontSize: 14, maxHeight: 120, minHeight: 44 }}
        />
      </Box>
      <TouchableOpacity
        onPress={onSend}
        disabled={disabled}
        accessibilityLabel="Send message"
        accessibilityRole="button"
        style={{
          paddingHorizontal: 14,
          paddingVertical: 10,
          borderRadius: 12,
          backgroundColor: disabled ? tokens.colors.muted : tokens.colors.primary,
          minHeight: 44,
          alignItems: 'center',
          justifyContent: 'center',
        }}
      >
        {sending ? (
          <ActivityIndicator color="#fff" />
        ) : (
          <Text size={13} weight="600" color={'#ffffff'}>Send</Text>
        )}
      </TouchableOpacity>
    </Box>
  )
}

export default React.memo(Composer)


