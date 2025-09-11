import React from 'react'
import { TouchableOpacity } from 'react-native'
import Box from '../../ui/Box'
import { Text } from '../../ui/Text'
import { tokens } from '../../ui/tokens'
import { ConversationSummary } from '../../types/conversations'
import ChannelDot from './ChannelDot'
import SlaBadge from './SlaBadge'

export interface ConversationListItemProps {
  item: ConversationSummary
  onPress: (id: string) => void
  onAssignToAgent?: (id: string) => void
  testID?: string
}

const timeAgo = (ts: number) => {
  const d = Math.max(0, Date.now() - ts)
  const m = Math.floor(d / 60000)
  if (m < 1) return 'now'
  if (m < 60) return `${m}m`
  const h = Math.floor(m / 60)
  if (h < 24) return `${h}h`
  const days = Math.floor(h / 24)
  return `${days}d`
}

import RowActionsSheet from './RowActionsSheet'

const ConversationListItem: React.FC<ConversationListItemProps> = ({ item, onPress, onAssignToAgent, testID }) => {
  const waitingChipColor = item.waitingMinutes >= 30 ? tokens.colors.warning : tokens.colors.mutedForeground
  const priorityLabel = item.priority === 'vip' ? 'VIP' : item.priority === 'high' ? 'HIGH' : item.priority.toUpperCase()
  const [actionsOpen, setActionsOpen] = React.useState(false)

  return (
    <TouchableOpacity
      testID={testID}
      onPress={() => onPress(item.id)}
      onLongPress={() => setActionsOpen(true)}
      accessibilityLabel={`Open conversation with ${item.customerName}`}
      accessibilityRole="button"
      hitSlop={{ top: 8, bottom: 8, left: 8, right: 8 }}
      style={{ paddingVertical: 12, minHeight: 44 }}
    >
      <Box row align="center" gap={12}>
        {/* Channel dot */}
        <ChannelDot channel={item.channel} size={10} />

        {/* Main info */}
        <Box style={{ flex: 1 }}>
          <Box row align="center" justify="space-between" mb={4}>
            <Box row align="center" gap={6}>
              <Text size={14} weight="600" color={tokens.colors.cardForeground}>{item.customerName}</Text>
              {item.lowConfidence && (
                <Text size={12} weight="700" color={tokens.colors.warning}>⚠</Text>
              )}
            </Box>
            <Text size={11} color={tokens.colors.mutedForeground}>{timeAgo(item.lastUpdatedTs)}</Text>
          </Box>
          <Text size={12} color={tokens.colors.mutedForeground} numberOfLines={1}>{item.lastMessageSnippet}</Text>
        </Box>

        {/* Right side: SLA + waiting + priority */}
        <Box align="flex-end" gap={6}>
          <SlaBadge state={item.sla} />
          <Box px={8} py={4} radius={12} style={{ backgroundColor: tokens.colors.accent }}>
            <Text size={11} weight="600" color={waitingChipColor}>{item.waitingMinutes}m</Text>
          </Box>
          <Text size={10} weight="700" color={tokens.colors.mutedForeground}>{priorityLabel}</Text>
        </Box>
      </Box>

      {/* Tags & assignee */}
      <Box row wrap gap={6} mt={8}>
        {item.tags.slice(0, 3).map((t) => (
          <Box key={t} px={8} py={4} radius={8} style={{ backgroundColor: tokens.colors.muted }}>
            <Text size={11} color={tokens.colors.mutedForeground}>{t}</Text>
          </Box>
        ))}
        {item.assignedTo && (
          <Box px={8} py={4} radius={8} style={{ backgroundColor: tokens.colors.card, borderWidth: 1, borderColor: tokens.colors.border }}>
            <Text size={11} color={tokens.colors.mutedForeground}>@{item.assignedTo}</Text>
          </Box>
        )}
      </Box>
      <RowActionsSheet
        id={item.id}
        visible={actionsOpen}
        onClose={() => setActionsOpen(false)}
        onAssign={(id) => console.log('assign', id)}
        onAssignToAgent={(id) => onAssignToAgent?.(id)}
        onTag={(id) => console.log('tag', id)}
        onSetPriority={(id) => console.log('priority', id)}
        onResolve={(id) => console.log('resolve', id)}
        onEscalate={(id) => console.log('escalate', id)}
      />
    </TouchableOpacity>
  )
}

export default React.memo(ConversationListItem)


