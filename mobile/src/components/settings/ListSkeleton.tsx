import React from 'react'
import { View } from 'react-native'

export interface ListSkeletonProps { rows?: number; testID?: string }

const ListSkeleton: React.FC<ListSkeletonProps> = ({ rows = 5, testID }) => {
  return (
    <View testID={testID}>
      {Array.from({ length: rows }).map((_, i) => (
        <View key={i} style={{ height: 44, borderRadius: 10, backgroundColor: '#111827', opacity: 0.15, marginBottom: 8 }} />
      ))}
    </View>
  )
}

export default React.memo(ListSkeleton)


