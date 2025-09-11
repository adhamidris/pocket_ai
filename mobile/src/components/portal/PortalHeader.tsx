import React from 'react'
import { View, Text, Image } from 'react-native'
import { useTheme } from '../../providers/ThemeProvider'

export interface PortalHeaderProps {
  businessName: string
  logoUrl?: string
  status: 'online' | 'away' | 'offline'
  subtitle?: string
  testID?: string
}

const statusColor = (theme: any, status: PortalHeaderProps['status']) => {
  if (status === 'online') return theme.color.success
  if (status === 'away') return theme.color.warning
  return theme.color.mutedForeground
}

const PortalHeader: React.FC<PortalHeaderProps> = ({ businessName, logoUrl, status, subtitle, testID }) => {
  const { theme } = useTheme()
  return (
    <View testID={testID} style={{ paddingHorizontal: 16, paddingVertical: 12, flexDirection: 'row', alignItems: 'center', backgroundColor: theme.color.card, borderBottomWidth: 1, borderBottomColor: theme.color.border }}>
      {logoUrl ? (
        <Image source={{ uri: logoUrl }} style={{ width: 36, height: 36, borderRadius: 8, marginRight: 10 }} />
      ) : (
        <View style={{ width: 36, height: 36, borderRadius: 8, marginRight: 10, backgroundColor: theme.color.muted }} />
      )}
      <View style={{ flex: 1 }}>
        <Text style={{ color: theme.color.cardForeground, fontSize: 16, fontWeight: '700' }}>{businessName}</Text>
        {subtitle ? <Text style={{ color: theme.color.mutedForeground, fontSize: 12 }} numberOfLines={1}>{subtitle}</Text> : null}
        <View style={{ flexDirection: 'row', alignItems: 'center', marginTop: 2 }}>
          <View style={{ width: 8, height: 8, borderRadius: 4, backgroundColor: statusColor(theme, status), marginRight: 6 }} />
          <Text style={{ color: theme.color.mutedForeground, fontSize: 12 }}>{status.toUpperCase()}</Text>
        </View>
      </View>
    </View>
  )
}

export default React.memo(PortalHeader)


