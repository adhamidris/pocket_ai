import React, { useState } from 'react'
import { TouchableOpacity, View, Text } from 'react-native'
import { useTheme } from '../../providers/ThemeProvider'
import { track } from '../../lib/analytics'

export const TtsButton: React.FC<{ playing?: boolean; onPlay?: () => void; onStop?: () => void }>
  = ({ playing, onPlay, onStop }) => {
  const { theme } = useTheme()
  const [isPlaying, setIsPlaying] = useState(!!playing)

  const toggle = () => {
    if (isPlaying) { onStop?.(); setIsPlaying(false); try { track('assistant.voice', { action: 'tts' }) } catch {} }
    else { onPlay?.(); setIsPlaying(true); try { track('assistant.voice', { action: 'tts' }) } catch {} }
  }

  return (
    <TouchableOpacity onPress={toggle} accessibilityLabel="Toggle TTS" testID="asst-tts">
      <View style={{ minWidth: 56, height: 44, paddingHorizontal: 12, alignItems: 'center', justifyContent: 'center', backgroundColor: theme.color.secondary, borderRadius: theme.radius.md, borderWidth: 1, borderColor: theme.color.border }}>
        <Text style={{ color: theme.color.foreground, fontWeight: '600' }}>{isPlaying ? 'Stop' : 'Play'}</Text>
      </View>
    </TouchableOpacity>
  )
}

export default TtsButton


