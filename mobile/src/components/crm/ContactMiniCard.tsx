import React from 'react'
import Box from '../../ui/Box'
import { Text } from '../../ui/Text'
import { tokens } from '../../ui/tokens'
import { Contact } from '../../types/crm'
import VipBadge from './VipBadge'

export interface ContactMiniCardProps {
  item: Contact
  testID?: string
}

const ContactMiniCard: React.FC<ContactMiniCardProps> = ({ item, testID }) => {
  return (
    <Box testID={testID} p={12} radius={16} style={{ backgroundColor: tokens.colors.card, borderWidth: 1, borderColor: tokens.colors.border, width: 160 }}>
      <Box row align="center" justify="space-between" mb={8}>
        <Text size={14} weight="600" color={tokens.colors.cardForeground} numberOfLines={1}>{item.name}</Text>
        <VipBadge active={!!item.vip} />
      </Box>
      <Text size={12} color={tokens.colors.mutedForeground} numberOfLines={1}>{item.email || item.phone || '—'}</Text>
    </Box>
  )
}

export default React.memo(ContactMiniCard)


