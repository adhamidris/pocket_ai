import React from 'react'
import Box from '../../ui/Box'
import { Text } from '../../ui/Text'
import { tokens } from '../../ui/tokens'

export interface ErrorBannerProps {
  message: string
  testID?: string
}

const ErrorBanner: React.FC<ErrorBannerProps> = ({ message, testID }) => {
  return (
    <Box
      testID={testID}
      p={12}
      radius={12}
      style={{ backgroundColor: tokens.colors.error + '20', borderWidth: 1, borderColor: tokens.colors.error }}
    >
      <Text size={13} color={tokens.colors.error}>
        {message}
      </Text>
    </Box>
  )
}

export default ErrorBanner


