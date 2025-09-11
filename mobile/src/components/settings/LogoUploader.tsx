import React from 'react'
import { View, Text, Image, TouchableOpacity } from 'react-native'
import { useTheme } from '../../providers/ThemeProvider'

export interface LogoUploaderProps { url?: string; onPick: (url: string) => void; testID?: string }

const LogoUploader: React.FC<LogoUploaderProps> = ({ url, onPick, testID }) => {
  const { theme } = useTheme()
  const pick = () => {
    const demo = 'https://placehold.co/120x120/png'
    onPick(demo)
  }
  return (
    <View testID={testID} style={{ borderWidth: 1, borderColor: theme.color.border, borderRadius: 12, padding: 12, alignItems: 'center', gap: 8 }}>
      {url ? (
        <Image source={{ uri: url }} style={{ width: 80, height: 80, borderRadius: 12, backgroundColor: theme.color.muted }} />
      ) : (
        <View style={{ width: 80, height: 80, borderRadius: 12, backgroundColor: theme.color.muted }} />
      )}
      <TouchableOpacity onPress={pick} accessibilityRole="button" accessibilityLabel="Pick logo" style={{ paddingHorizontal: 12, paddingVertical: 8, borderRadius: 10, borderWidth: 1, borderColor: theme.color.border }}>
        <Text style={{ color: theme.color.cardForeground, fontWeight: '600' }}>{url ? 'Replace' : 'Upload'} Logo</Text>
      </TouchableOpacity>
    </View>
  )
}

export default React.memo(LogoUploader)


