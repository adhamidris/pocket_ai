import React from 'react'
import { View, Text } from 'react-native'
import { tokens } from '../../ui/tokens'

export interface EmptyStateProps { message?: string; testID?: string }

const EmptyState: React.FC<EmptyStateProps> = ({ message = 'Nothing here yet.', testID }) => {
  return (
    <View testID={testID} style={{ alignItems: 'center', justifyContent: 'center', padding: 24 }}>
      <Text style={{ color: tokens.colors.mutedForeground }}>{message}</Text>
    </View>
  )
}

export default React.memo(EmptyState)


