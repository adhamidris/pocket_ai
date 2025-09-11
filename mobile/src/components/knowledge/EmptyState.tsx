import React from 'react'
import { View } from 'react-native'
import { Text } from '../../ui/Text'
import { tokens } from '../../ui/tokens'

export interface EmptyStateProps { message: string; testID?: string }

const EmptyState: React.FC<EmptyStateProps> = ({ message, testID }) => (
  <View testID={testID} style={{ alignItems: 'center', justifyContent: 'center', paddingVertical: 24 }}>
    <View style={{ width: 56, height: 56, borderRadius: 28, backgroundColor: tokens.colors.muted, marginBottom: 12 }} />
    <Text size={13} color={tokens.colors.mutedForeground}>{message}</Text>
  </View>
)

export default React.memo(EmptyState)


