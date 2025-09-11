import React from 'react'
import { Image, TouchableOpacity } from 'react-native'
import Box from '../../ui/Box'
import { Text } from '../../ui/Text'
import { tokens } from '../../ui/tokens'
import { AnyAgent } from '../../types/agents'
import StatusBadge from './StatusBadge'

export interface AgentMiniCardProps {
  item: AnyAgent
  onPress?: (id: string) => void
  testID?: string
}

const initials = (name: string) => name.split(' ').map((n) => n[0]).slice(0, 2).join('').toUpperCase()

const AgentMiniCard: React.FC<AgentMiniCardProps> = ({ item, onPress, testID }) => {
  return (
    <TouchableOpacity
      disabled={!onPress}
      onPress={() => onPress?.(item.id)}
      accessibilityLabel={`Agent ${item.name}`}
      accessibilityRole={onPress ? 'button' : 'text'}
      hitSlop={{ top: 8, bottom: 8, left: 8, right: 8 }}
      style={{ width: 180 }}
      testID={testID}
    >
      <Box p={12} radius={16} style={{ backgroundColor: tokens.colors.card, borderWidth: 1, borderColor: tokens.colors.border }}>
        <Box row align="center" gap={8} mb={8}>
          {item.avatarUrl ? (
            <Image source={{ uri: item.avatarUrl }} style={{ width: 36, height: 36, borderRadius: 18, backgroundColor: tokens.colors.muted }} />
          ) : (
            <Box style={{ width: 36, height: 36, borderRadius: 18, backgroundColor: tokens.colors.muted, alignItems: 'center', justifyContent: 'center' }}>
              <Text size={12} weight="700" color={tokens.colors.mutedForeground}>{initials(item.name)}</Text>
            </Box>
          )}
          <Text size={14} weight="700" color={tokens.colors.cardForeground} numberOfLines={1} style={{ flex: 1 }}>{item.name}</Text>
        </Box>
        <StatusBadge status={item.status} />
      </Box>
    </TouchableOpacity>
  )
}

export default React.memo(AgentMiniCard)



