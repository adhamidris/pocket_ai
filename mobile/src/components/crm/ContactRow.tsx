import React from 'react'
import { TouchableOpacity, I18nManager } from 'react-native'
import Box from '../../ui/Box'
import { Text } from '../../ui/Text'
import { tokens } from '../../ui/tokens'
import { Contact, ConsentState, InteractionSummary } from '../../types/crm'
import ConsentBadge from './ConsentBadge'
import VipBadge from './VipBadge'
import TagChip from './TagChip'
import ValuePill from './ValuePill'
import ChannelDot from '../conversations/ChannelDot'

export interface ContactRowProps {
  item: Contact
  interaction?: InteractionSummary
  onPress: (id: string) => void
  onLongPress?: (id: string) => void
  onToggleVip?: (id: string) => void
  onAddRemoveTag?: (id: string) => void
  onSetConsent?: (id: string, state: ConsentState) => void
  queuedVip?: boolean
  queuedTag?: boolean
  queuedConsent?: boolean
  hidePII?: boolean
  testID?: string
}

const timeAgo = (ts?: number) => {
  if (!ts) return '—'
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

const ContactRow: React.FC<ContactRowProps> = ({ item, interaction, onPress, onLongPress, onToggleVip, onAddRemoveTag, onSetConsent, queuedVip, queuedTag, queuedConsent, hidePII, testID }) => {
  const extraTags = Math.max(0, item.tags.length - 3)
  const [open, setOpen] = React.useState(false)

  const a11y = `Contact ${item.name}${item.vip ? ', VIP' : ''}, consent ${item.consent}`

  return (
    <TouchableOpacity
      testID={testID}
      onPress={() => onPress(item.id)}
      onLongPress={() => { setOpen(true); onLongPress?.(item.id) }}
      accessibilityLabel={a11y}
      accessibilityRole="button"
      hitSlop={{ top: 8, bottom: 8, left: 8, right: 8 }}
      style={{ paddingVertical: 12, minHeight: 44 }}
    >
      <Box row align="flex-start" gap={12}>
        {/* Channels */}
        <Box row align="center" gap={4} mt={4}>
          {item.channels.map((ch, idx) => (
            <ChannelDot key={`${ch}-${idx}`} channel={ch} size={8} />
          ))}
        </Box>

        {/* Main */}
        <Box style={{ flex: 1 }}>
          {/* Header line: name + consent + VIP + value */}
          <Box row align="center" justify="space-between" mb={4}>
            <Box row align="center" gap={8} style={{ flex: 1 }}>
              <Text size={14} weight="600" color={tokens.colors.cardForeground} numberOfLines={1} style={{ textAlign: I18nManager.isRTL ? 'right' : 'left' }}>
                {item.name}
              </Text>
              <ConsentBadge state={item.consent} />
              {queuedConsent && <Text size={11} color={tokens.colors.warning}>⏳</Text>}
              <VipBadge active={!!item.vip} />
              {queuedVip && <Text size={11} color={tokens.colors.warning}>⏳</Text>}
            </Box>
            <ValuePill value={item.lifetimeValue} />
          </Box>

          {/* Interaction snippet */}
          <Box row align="center" gap={8} mb={6}>
            <Text size={11} color={tokens.colors.mutedForeground} style={{ textAlign: I18nManager.isRTL ? 'right' : 'left' }}>{timeAgo(interaction?.lastUpdatedTs || item.lastInteractionTs)}</Text>
            {interaction?.lastMessageSnippet && (
              <Text size={12} color={tokens.colors.mutedForeground} numberOfLines={1} style={{ textAlign: I18nManager.isRTL ? 'right' : 'left' }}>
                {interaction.lastMessageSnippet}
              </Text>
            )}
          </Box>
          {/* PII lines */}
          <Box>
            {item.email ? (
              <Text size={11} color={tokens.colors.mutedForeground}>{hidePII ? '••••••••@••••' : item.email}</Text>
            ) : null}
            {item.phone ? (
              <Text size={11} color={tokens.colors.mutedForeground}>{hidePII ? '+•••-•••-••••' : item.phone}</Text>
            ) : null}
          </Box>

          {/* Tags */}
          <Box row wrap gap={6}>
            {item.tags.slice(0, 3).map((t) => (
              <TagChip key={t} label={t} />
            ))}
            {extraTags > 0 && (
              <Box px={8} py={4} radius={8} style={{ backgroundColor: tokens.colors.muted }}>
                <Text size={11} color={tokens.colors.mutedForeground}>+{extraTags}</Text>
              </Box>
            )}
            {queuedTag && <Text size={11} color={tokens.colors.warning}>⏳</Text>}
          </Box>
        </Box>
      </Box>
      <RowActionsSheet
        id={item.id}
        visible={open}
        onClose={() => setOpen(false)}
        onToggleVip={(id) => onToggleVip?.(id)}
        onAddRemoveTag={(id) => onAddRemoveTag?.(id)}
        onSetConsent={(id, state) => onSetConsent?.(id, state)}
        onStartConversation={(id) => console.log('start convo', id)}
        onMerge={(id) => console.log('merge', id)}
        onDelete={(id) => console.log('delete', id)}
      />
    </TouchableOpacity>
  )
}

export default React.memo(ContactRow)


