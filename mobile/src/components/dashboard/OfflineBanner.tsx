import React from 'react'
import Box from '../../ui/Box'
import { Text } from '../../ui/Text'
import { tokens } from '../../ui/tokens'
import { WifiOff } from 'lucide-react-native'

export interface OfflineBannerProps {
  visible: boolean
  testID?: string
}

const OfflineBanner: React.FC<OfflineBannerProps> = ({ visible, testID }) => {
  if (!visible) return null
  return (
    <Box
      testID={testID}
      row
      align="center"
      gap={8}
      p={12}
      radius={12}
      style={{ backgroundColor: tokens.colors.warning + '20', borderWidth: 1, borderColor: tokens.colors.warning }}
      accessibilityLabel="Offline banner"
      accessibilityRole="status"
    >
      <WifiOff size={16} color={tokens.colors.warning as any} />
      <Text size={13} color={tokens.colors.cardForeground}>
        Offline — actions will sync automatically when connection is restored.
      </Text>
    </Box>
  )
}

export default OfflineBanner


