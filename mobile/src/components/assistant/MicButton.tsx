import React, { useEffect, useRef, useState } from 'react'
import { TouchableOpacity, View, Text, Animated } from 'react-native'
import * as Haptics from 'expo-haptics'
import { useTheme } from '../../providers/ThemeProvider'
import { track } from '../../lib/analytics'

export interface MicButtonProps {
  recording?: boolean
  onRecordStart?: () => void
  onRecordStop?: () => void
  durationMs?: number
  pressBehavior?: 'hold' | 'toggle'
}

export const MicButton: React.FC<MicButtonProps> = ({ recording, onRecordStart, onRecordStop, durationMs, pressBehavior = 'hold' }) => {
  const { theme } = useTheme()
  const [isRecording, setIsRecording] = useState(!!recording)
  const [duration, setDuration] = useState(durationMs ?? 0)
  const pulse = useRef(new Animated.Value(1)).current

  useEffect(() => { setIsRecording(!!recording) }, [recording])
  useEffect(() => { if (typeof durationMs === 'number') setDuration(durationMs) }, [durationMs])

  useEffect(() => {
    let timer: any
    if (isRecording) {
      timer = setInterval(() => setDuration(d => d + 1000), 1000)
      Animated.loop(Animated.sequence([
        Animated.timing(pulse, { toValue: 1.1, duration: 600, useNativeDriver: true }),
        Animated.timing(pulse, { toValue: 1.0, duration: 600, useNativeDriver: true }),
      ])).start()
    } else {
      setDuration(0)
      pulse.setValue(1)
    }
    return () => { if (timer) clearInterval(timer) }
  }, [isRecording])

  const toggle = () => {
    if (pressBehavior === 'toggle') {
      if (isRecording) { onRecordStop?.(); setIsRecording(false); try { track('assistant.voice', { action: 'record' }) } catch {} }
      else { Haptics.impactAsync(Haptics.ImpactFeedbackStyle.Medium); onRecordStart?.(); setIsRecording(true); try { track('assistant.voice', { action: 'record' }) } catch {} }
    }
  }

  const mm = Math.floor(duration / 60000).toString().padStart(2, '0')
  const ss = Math.floor((duration % 60000) / 1000).toString().padStart(2, '0')

  return (
    <TouchableOpacity onPress={toggle} accessibilityLabel="Record voice" testID="asst-mic">
      <Animated.View style={{ transform: [{ scale: pulse }], width: 56, height: 56, alignItems: 'center', justifyContent: 'center', backgroundColor: isRecording ? theme.color.error : theme.color.primary, borderRadius: 28, ...(theme.shadow.md as any) }}>
        <Text style={{ color: '#fff', fontWeight: '700' }}>{isRecording ? 'Stop' : 'Mic'}</Text>
      </Animated.View>
      <View style={{ alignItems: 'center', marginTop: 6 }}>
        <Text style={{ color: theme.color.mutedForeground, fontSize: 12 }}>{mm}:{ss}</Text>
      </View>
    </TouchableOpacity>
  )
}

export default MicButton


