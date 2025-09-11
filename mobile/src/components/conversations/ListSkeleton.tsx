import React from 'react'
import Box from '../../ui/Box'
import { tokens } from '../../ui/tokens'

export interface ListSkeletonProps {
  rows?: number
  testID?: string
}

const Row = () => (
  <Box row align="center" gap={12} py={12}>
    <Box w={36} h={36} radius={18} style={{ backgroundColor: tokens.colors.muted }} />
    <Box style={{ flex: 1 }}>
      <Box w={'70%'} h={12} radius={6} style={{ backgroundColor: tokens.colors.muted, marginBottom: 6 }} />
      <Box w={'40%'} h={10} radius={5} style={{ backgroundColor: tokens.colors.muted }} />
    </Box>
    <Box w={48} h={20} radius={10} style={{ backgroundColor: tokens.colors.muted }} />
  </Box>
)

const ListSkeleton: React.FC<ListSkeletonProps> = ({ rows = 6, testID }) => {
  return (
    <Box testID={testID}>
      {Array.from({ length: rows }).map((_, i) => (
        <Row key={i} />
      ))}
    </Box>
  )
}

export default React.memo(ListSkeleton)


