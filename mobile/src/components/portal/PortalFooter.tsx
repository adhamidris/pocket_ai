import React from 'react'
import { View, Text, Image, TouchableOpacity } from 'react-native'
import { useTheme } from '../../providers/ThemeProvider'

export interface PortalFooterProps { logoUrl?: string; brandNote?: string; onPrivacy?: () => void; testID?: string }

const PortalFooter: React.FC<PortalFooterProps> = ({ logoUrl, brandNote, onPrivacy, testID }) => {
  const { theme } = useTheme()
  return (
    <View testID={testID} style={{ paddingHorizontal: 16, paddingVertical: 12, borderTopWidth: 1, borderTopColor: theme.color.border, backgroundColor: theme.color.card, flexDirection: 'row', alignItems: 'center', justifyContent: 'space-between' }}>
      <View style={{ flexDirection: 'row', alignItems: 'center' }}>
        {logoUrl ? <Image source={{ uri: logoUrl }} style={{ width: 20, height: 20, borderRadius: 4, marginRight: 8 }} /> : null}
        {brandNote ? <Text style={{ color: theme.color.mutedForeground, fontSize: 12 }}>{brandNote}</Text> : null}
      </View>
      <TouchableOpacity onPress={onPrivacy} accessibilityRole="button" accessibilityLabel="Privacy policy" style={{ paddingHorizontal: 10, paddingVertical: 6, borderRadius: 8, borderWidth: 1, borderColor: theme.color.border }}>
        <Text style={{ color: theme.color.cardForeground, fontSize: 12, fontWeight: '600' }}>Privacy</Text>
      </TouchableOpacity>
    </View>
  )
}

export default React.memo(PortalFooter)


