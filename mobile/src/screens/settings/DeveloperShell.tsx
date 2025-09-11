import React from 'react'
import { View, Text, TouchableOpacity } from 'react-native'
import { useTheme } from '../../providers/ThemeProvider'

export interface DeveloperShellProps {
  onOpenApi: () => void
  onOpenWebhooks: () => void
}

const DeveloperShell: React.FC<DeveloperShellProps> = ({ onOpenApi, onOpenWebhooks }) => {
  const { theme } = useTheme()
  const Btn: React.FC<{ label: string; onPress: () => void }>=({ label, onPress }) => (
    <TouchableOpacity onPress={onPress} accessibilityRole="button" accessibilityLabel={label} style={{ paddingHorizontal: 12, paddingVertical: 10, borderRadius: 12, borderWidth: 1, borderColor: theme.color.border }}>
      <Text style={{ color: theme.color.cardForeground, fontWeight: '600' }}>{label}</Text>
    </TouchableOpacity>
  )
  return (
    <View style={{ gap: 12 }}>
      <Text style={{ color: theme.color.cardForeground, fontWeight: '700' }}>Developer Tools</Text>
      <Btn label="API Keys" onPress={onOpenApi} />
      <Btn label="Webhooks" onPress={onOpenWebhooks} />
    </View>
  )
}

export default React.memo(DeveloperShell)


