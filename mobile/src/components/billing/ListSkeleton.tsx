import React from 'react'
import { View } from 'react-native'
import { tokens } from '../../ui/tokens'

export interface ListSkeletonProps { rows?: number; testID?: string }

const ListSkeleton: React.FC<ListSkeletonProps> = ({ rows = 5, testID }) => {
  return (
    <View testID={testID}>
      {Array.from({ length: rows }).map((_, i) => (
        <View key={i} style={{ height: 20, backgroundColor: tokens.colors.muted, borderRadius: 6, marginVertical: 6 }} />
      ))}
    </View>
  )
}

export default React.memo(ListSkeleton)


