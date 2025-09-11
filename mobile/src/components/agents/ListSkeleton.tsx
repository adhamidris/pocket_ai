import React from 'react'
import { View } from 'react-native'
import { tokens } from '../../ui/tokens'

export interface ListSkeletonProps {
  rows?: number
  testID?: string
}

const Row = () => (
  <View style={{ height: 64, borderRadius: 12, backgroundColor: tokens.colors.muted, marginBottom: 12 }} />
)

const ListSkeleton: React.FC<ListSkeletonProps> = ({ rows = 8, testID }) => {
  return (
    <View testID={testID}>
      {Array.from({ length: rows }).map((_, i) => <Row key={i} />)}
    </View>
  )
}

export default React.memo(ListSkeleton)



