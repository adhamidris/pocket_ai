import React from 'react'
import Box from '../../ui/Box'
import { Text } from '../../ui/Text'
import { tokens } from '../../ui/tokens'

export interface VipBadgeProps {
  active: boolean
  testID?: string
}

const VipBadge: React.FC<VipBadgeProps> = ({ active, testID }) => {
  const c = active ? tokens.colors.warning : tokens.colors.mutedForeground
  return (
    <Box
      testID={testID}
      px={8}
      py={4}
      radius={12}
      style={{ backgroundColor: active ? tokens.colors.warning + '20' : tokens.colors.muted, borderWidth: 1, borderColor: c, minHeight: 32, justifyContent: 'center' }}
      accessibilityLabel={active ? 'VIP' : 'Not VIP'}
      accessibilityRole="text"
    >
      <Text size={11} weight="700" color={c}>VIP</Text>
    </Box>
  )
}

export default React.memo(VipBadge)


