import React from 'react'
import { View } from 'react-native'
import { tokens } from '../../ui/tokens'

export interface ListSkeletonProps { rows?: number; testID?: string }

const ListSkeleton: React.FC<ListSkeletonProps> = ({ rows = 6, testID }) => {
  return (
    <View testID={testID}>
      {Array.from({ length: rows }).map((_, i) => (
        <View key={i} style={{ height: 52, borderRadius: 12, backgroundColor: tokens.colors.muted, opacity: 0.4, marginBottom: 8 }} />
      ))}
    </View>
  )
}

export default React.memo(ListSkeleton)


