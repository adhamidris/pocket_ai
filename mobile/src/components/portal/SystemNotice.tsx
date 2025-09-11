import React from 'react'
import { View, Text } from 'react-native'
import { useTheme } from '../../providers/ThemeProvider'

export interface SystemNoticeProps { text: string; tone?: 'info'|'warn'|'success'|'error'; testID?: string }

const SystemNotice: React.FC<SystemNoticeProps> = ({ text, tone = 'info', testID }) => {
  const { theme } = useTheme()
  const color = tone === 'success' ? theme.color.success : tone === 'error' ? theme.color.error : tone === 'warn' ? theme.color.warning : theme.color.mutedForeground
  return (
    <View testID={testID} style={{ paddingHorizontal: 12, paddingVertical: 8 }}>
      <Text style={{ color }}>{text}</Text>
    </View>
  )
}

export default React.memo(SystemNotice)


