import React from 'react'
import { Switch } from 'react-native'
import Box from '../../ui/Box'
import { Text } from '../../ui/Text'
import { tokens } from '../../ui/tokens'

export interface RedactionToggleProps {
  enabled: boolean
  onToggle: () => void
  testID?: string
}

const RedactionToggle: React.FC<RedactionToggleProps> = ({ enabled, onToggle, testID }) => {
  return (
    <Box testID={testID} row align="center" justify="space-between" style={{ paddingVertical: 8 }}>
      <Text size={13} color={tokens.colors.cardForeground}>PII Redaction</Text>
      <Switch value={enabled} onValueChange={onToggle} accessibilityLabel="Toggle PII Redaction" />
    </Box>
  )
}

export default React.memo(RedactionToggle)


