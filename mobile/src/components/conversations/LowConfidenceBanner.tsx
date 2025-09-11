import React from 'react'
import Box from '../../ui/Box'
import { Text } from '../../ui/Text'
import { tokens } from '../../ui/tokens'
import { AlertTriangle } from 'lucide-react-native'

export interface LowConfidenceBannerProps {
  visible: boolean
  testID?: string
}

const LowConfidenceBanner: React.FC<LowConfidenceBannerProps> = ({ visible, testID }) => {
  if (!visible) return null
  return (
    <Box testID={testID} row align="center" gap={8} p={12} radius={12} style={{ backgroundColor: tokens.colors.warning + '20', borderWidth: 1, borderColor: tokens.colors.warning }}>
      <AlertTriangle size={16} color={tokens.colors.warning as any} />
      <Text size={13} color={tokens.colors.cardForeground}>Low confidence detected — consider human review.</Text>
    </Box>
  )
}

export default React.memo(LowConfidenceBanner)


