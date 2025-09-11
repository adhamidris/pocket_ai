import React from 'react'
import Box from '../../ui/Box'
import { tokens } from '../../ui/tokens'

export interface ThreadSkeletonProps {
  bubbles?: number
  testID?: string
}

const Bubble = ({ right }: { right?: boolean }) => (
  <Box align={right ? 'flex-end' : 'flex-start'}>
    <Box w={'80%'} h={48} radius={16} style={{ backgroundColor: tokens.colors.muted }} />
  </Box>
)

const ThreadSkeleton: React.FC<ThreadSkeletonProps> = ({ bubbles = 6, testID }) => {
  return (
    <Box testID={testID} gap={12}>
      {Array.from({ length: bubbles }).map((_, i) => (
        <Bubble key={i} right={i % 2 === 1} />
      ))}
    </Box>
  )
}

export default React.memo(ThreadSkeleton)


