import React from 'react'
import Box from '../../ui/Box'
import { Text } from '../../ui/Text'
import { tokens } from '../../ui/tokens'
import { Message } from '../../types/conversations'

export interface MessageBubbleProps {
  msg: Message
  testID?: string
}

const formatTime = (ts: number) => new Date(ts).toLocaleTimeString('en-US', { hour: 'numeric', minute: '2-digit' })

const MessageBubble: React.FC<MessageBubbleProps> = ({ msg, testID }) => {
  const isRight = msg.sender !== 'customer'
  const bubbleBg = msg.sender === 'customer' ? tokens.colors.muted : tokens.colors.primary
  const textColor = msg.sender === 'customer' ? tokens.colors.cardForeground : '#ffffff'

  return (
    <Box testID={testID} align={isRight ? 'flex-end' : 'flex-start'} accessibilityLabel={`${msg.sender} message: ${msg.text}`}> 
      <Box
        p={12}
        radius={16}
        style={{
          maxWidth: '80%',
          backgroundColor: bubbleBg,
          borderWidth: msg.sender === 'customer' ? 1 : 0,
          borderColor: msg.sender === 'customer' ? tokens.colors.border : 'transparent',
        }}
      >
        <Text size={14} color={textColor} style={{ lineHeight: 20 }}>{msg.text}</Text>
      </Box>
      <Text size={10} color={tokens.colors.mutedForeground} style={{ marginTop: 4 }}>{formatTime(msg.ts)}</Text>
    </Box>
  )
}

export default React.memo(MessageBubble)


