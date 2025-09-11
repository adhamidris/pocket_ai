import React from 'react'
import { View, Text } from 'react-native'

export interface EmptyStateProps { message: string; testID?: string }

const EmptyState: React.FC<EmptyStateProps> = ({ message, testID }) => {
  return (
    <View testID={testID} style={{ alignItems: 'center', padding: 16 }}>
      <Text style={{ color: '#9CA3AF' }}>{message}</Text>
    </View>
  )
}

export default React.memo(EmptyState)


