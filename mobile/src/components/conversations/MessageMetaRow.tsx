import React from 'react'
import Box from '../../ui/Box'
import { Text } from '../../ui/Text'
import { tokens } from '../../ui/tokens'
import { Message } from '../../types/conversations'

export interface MessageMetaRowProps {
  msg: Message
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

const senderLabel = (sender: Message['sender']) => sender === 'ai' ? 'AI' : sender === 'agent' ? 'Agent' : 'Customer'

const MessageMetaRow: React.FC<MessageMetaRowProps> = ({ msg, testID }) => {
  return (
    <Box testID={testID} row gap={8} align="center" justify="flex-start">
      <Box px={8} py={4} radius={8} style={{ backgroundColor: tokens.colors.muted }}>
        <Text size={11} weight="600" color={tokens.colors.mutedForeground}>{senderLabel(msg.sender)}</Text>
      </Box>
      <Text size={11} color={tokens.colors.mutedForeground}>{timeAgo(msg.ts)}</Text>
      <Text size={11} color={tokens.colors.mutedForeground}>•</Text>
      <Text size={11} color={msg.status === 'queued' ? tokens.colors.warning : tokens.colors.mutedForeground}>
        {msg.status === 'queued' ? 'queued' : 'sent'}
      </Text>
    </Box>
  )
}

export default React.memo(MessageMetaRow)


