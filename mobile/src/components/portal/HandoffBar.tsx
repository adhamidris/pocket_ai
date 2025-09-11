import React from 'react'
import { View, Text, TouchableOpacity } from 'react-native'
import { useTheme } from '../../providers/ThemeProvider'
import { QueueInfo, SessionState } from '../../types/portal'

export interface HandoffBarProps {
  session: SessionState
  queue?: QueueInfo
  onRequestHuman: () => void
  onCancelRequest: () => void
  onEndChat: () => void
  testID?: string
}

const HandoffBar: React.FC<HandoffBarProps> = ({ session, queue, onRequestHuman, onCancelRequest, onEndChat, testID }) => {
  const { theme } = useTheme()
  const isQueued = session === 'queued'
  const isEnded = session === 'ended'
  return (
    <View testID={testID} style={{ borderTopWidth: 1, borderTopColor: theme.color.border, padding: 12, backgroundColor: theme.color.card, gap: 8 }}>
      {isQueued && queue ? (
        <Text style={{ color: theme.color.mutedForeground }}>Queued • Position {queue.position}{queue.etaMins != null ? ` • ETA ${queue.etaMins}m` : ''}</Text>
      ) : null}
      <View style={{ flexDirection: 'row', gap: 8 }}>
        {isQueued ? (
          <TouchableOpacity onPress={onCancelRequest} accessibilityRole="button" accessibilityLabel="Cancel request" style={{ paddingHorizontal: 12, paddingVertical: 10, borderRadius: 10, borderWidth: 1, borderColor: theme.color.border, minHeight: 44 }}>
            <Text style={{ color: theme.color.cardForeground, fontWeight: '700' }}>Cancel request</Text>
          </TouchableOpacity>
        ) : !isEnded ? (
          <TouchableOpacity onPress={onRequestHuman} accessibilityRole="button" accessibilityLabel="Request human" style={{ paddingHorizontal: 12, paddingVertical: 10, borderRadius: 10, backgroundColor: theme.color.primary, minHeight: 44 }}>
            <Text style={{ color: theme.color.primaryForeground, fontWeight: '700' }}>Request human</Text>
          </TouchableOpacity>
        ) : null}
        <TouchableOpacity onPress={onEndChat} accessibilityRole="button" accessibilityLabel="End chat" style={{ paddingHorizontal: 12, paddingVertical: 10, borderRadius: 10, borderWidth: 1, borderColor: theme.color.border, minHeight: 44 }}>
          <Text style={{ color: theme.color.cardForeground, fontWeight: '700' }}>End chat</Text>
        </TouchableOpacity>
      </View>
    </View>
  )
}

export default React.memo(HandoffBar)


