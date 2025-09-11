import React from 'react'
import { View, Text, Image, TouchableOpacity } from 'react-native'
import { useTheme } from '../../providers/ThemeProvider'

export const EmptyStateGuide: React.FC<{
  title: string
  lines: string[]
  cta?: { label: string; onPress: () => void }
  illustrationUri?: string
}> = ({ title, lines, cta, illustrationUri }) => {
  const { theme } = useTheme()
  return (
    <View style={{ alignItems: 'center', padding: 24 }}>
      {illustrationUri && (
        <Image source={{ uri: illustrationUri }} style={{ width: 160, height: 120, marginBottom: 16, resizeMode: 'contain' }} />
      )}
      <Text style={{ color: theme.color.cardForeground, fontWeight: '700', fontSize: 18, textAlign: 'center', marginBottom: 8 }}>{title}</Text>
      <View style={{ gap: 6, marginBottom: 16 }}>
        {lines.map((l, idx) => (
          <Text key={idx} style={{ color: theme.color.mutedForeground, textAlign: 'center' }}>{l}</Text>
        ))}
      </View>
      {cta && (
        <TouchableOpacity onPress={cta.onPress} accessibilityLabel={cta.label} style={{ paddingHorizontal: 16, paddingVertical: 12, backgroundColor: theme.color.primary, borderRadius: 12, minWidth: 44, minHeight: 44, justifyContent: 'center' }}>
          <Text style={{ color: '#fff', fontWeight: '600' }}>{cta.label}</Text>
        </TouchableOpacity>
      )}
    </View>
  )
}


