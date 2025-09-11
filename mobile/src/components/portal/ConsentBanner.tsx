import React from 'react'
import { View, Text, TouchableOpacity } from 'react-native'
import { useTheme } from '../../providers/ThemeProvider'

export interface ConsentBannerProps { message: string; onAccept: () => void; onDecline: () => void; testID?: string }

const ConsentBanner: React.FC<ConsentBannerProps> = ({ message, onAccept, onDecline, testID }) => {
  const { theme } = useTheme()
  return (
    <View testID={testID} style={{ padding: 12, borderWidth: 1, borderColor: theme.color.border, backgroundColor: theme.color.card, borderRadius: 12 }}>
      <Text style={{ color: theme.color.cardForeground, marginBottom: 8 }}>{message}</Text>
      <View style={{ flexDirection: 'row', gap: 8 }}>
        <TouchableOpacity onPress={onDecline} accessibilityRole="button" accessibilityLabel="Decline" style={{ paddingHorizontal: 12, paddingVertical: 10, borderRadius: 10, borderWidth: 1, borderColor: theme.color.border, minHeight: 44 }}>
          <Text style={{ color: theme.color.cardForeground, fontWeight: '700' }}>Decline</Text>
        </TouchableOpacity>
        <TouchableOpacity onPress={onAccept} accessibilityRole="button" accessibilityLabel="Accept" style={{ paddingHorizontal: 12, paddingVertical: 10, borderRadius: 10, backgroundColor: theme.color.primary, minHeight: 44 }}>
          <Text style={{ color: theme.color.primaryForeground, fontWeight: '700' }}>Accept</Text>
        </TouchableOpacity>
      </View>
    </View>
  )
}

export default React.memo(ConsentBanner)


