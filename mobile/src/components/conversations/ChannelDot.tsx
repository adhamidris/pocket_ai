import React from 'react'
import Box from '../../ui/Box'
import { Channel } from '../../types/conversations'

export interface ChannelDotProps {
  channel: Channel
  size?: number
  testID?: string
}

const channelColor = (channel: Channel): string => {
  switch (channel) {
    case 'whatsapp': return '#25D366'
    case 'instagram': return '#C13584'
    case 'facebook': return '#1877F2'
    case 'web': return '#6B7280'
    case 'email': return '#F59E0B'
    default: return '#9CA3AF'
  }
}

const ChannelDot: React.FC<ChannelDotProps> = ({ channel, size = 8, testID }) => {
  const color = channelColor(channel)
  return (
    <Box
      testID={testID}
      style={{ width: size, height: size, borderRadius: size / 2, backgroundColor: color }}
      accessibilityLabel={`Channel ${channel}`}
      accessibilityRole="image"
    />
  )
}

export default React.memo(ChannelDot)


