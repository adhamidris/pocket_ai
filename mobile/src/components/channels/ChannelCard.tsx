import React from 'react'
import { View, TouchableOpacity } from 'react-native'
import { ChannelStatus } from '../../types/channels'
import { Text } from '../../ui/Text'
import { tokens } from '../../ui/tokens'

export interface ChannelCardProps {
  status: ChannelStatus
  onOpenRecipe: () => void
  testID?: string
}

const iconFor = (ch: ChannelStatus['channel']) => {
  switch (ch) {
    case 'whatsapp': return '🟢'
    case 'instagram': return '📸'
    case 'facebook': return '📘'
    default: return '🌐'
  }
}

const ChannelCard: React.FC<ChannelCardProps> = ({ status, onOpenRecipe, testID }) => {
  return (
    <View testID={testID} style={{ borderWidth: 1, borderColor: tokens.colors.border, borderRadius: 16, backgroundColor: tokens.colors.card, padding: 12 }}>
      <View style={{ flexDirection: 'row', alignItems: 'center', justifyContent: 'space-between' }}>
        <View style={{ flexDirection: 'row', alignItems: 'center', gap: 8 }}>
          <Text size={18} weight="700" color={tokens.colors.cardForeground}>{iconFor(status.channel)}</Text>
          <Text size={14} weight="700" color={tokens.colors.cardForeground}>{status.channel.toUpperCase()}</Text>
        </View>
        <Text size={12} color={status.connected ? tokens.colors.success : tokens.colors.mutedForeground}>{status.connected ? 'Connected' : 'Not connected'}</Text>
      </View>
      {status.lastVerifiedAt && (
        <Text size={11} color={tokens.colors.mutedForeground} style={{ marginTop: 6 }}>Last verified: {new Date(status.lastVerifiedAt).toLocaleString()}</Text>
      )}
      {status.notes && (
        <Text size={12} color={tokens.colors.mutedForeground} style={{ marginTop: 4 }}>{status.notes}</Text>
      )}
      <TouchableOpacity onPress={onOpenRecipe} accessibilityLabel={`Open recipe for ${status.channel}`} accessibilityRole="button" style={{ marginTop: 10, alignSelf: 'flex-start', paddingHorizontal: 12, paddingVertical: 8, borderWidth: 1, borderColor: tokens.colors.border, borderRadius: 12 }}>
        <Text size={13} weight="600" color={tokens.colors.primary}>Open Recipe</Text>
      </TouchableOpacity>
    </View>
  )
}

export default React.memo(ChannelCard)



