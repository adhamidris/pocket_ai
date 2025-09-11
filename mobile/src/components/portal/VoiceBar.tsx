import React from 'react'
import { View, Text, TouchableOpacity, Modal } from 'react-native'
import { useTheme } from '../../providers/ThemeProvider'

export interface VoiceBarProps {
  enabled: boolean
  onTranscript: (text: string) => void
  testID?: string
}

const VoiceBar: React.FC<VoiceBarProps> = ({ enabled, onTranscript, testID }) => {
  const { theme } = useTheme()
  const [recording, setRecording] = React.useState(false)
  const [seconds, setSeconds] = React.useState(0)
  const [levels, setLevels] = React.useState<number[]>([2, 6, 3, 5])
  const [showPerm, setShowPerm] = React.useState(false)

  React.useEffect(() => {
    if (!recording) return
    const t = setInterval(() => {
      setSeconds((s) => s + 1)
      setLevels((prev) => prev.map(() => Math.max(2, Math.min(8, Math.floor(Math.random() * 8)))))
    }, 250)
    return () => clearInterval(t)
  }, [recording])

  const start = () => {
    if (!enabled) { setShowPerm(true); return }
    setSeconds(0)
    setRecording(true)
    try { (require('../../lib/analytics') as any).track('portal.voice.record') } catch {}
  }
  const stop = () => {
    setRecording(false)
    const text = `Voice note ${Math.ceil(seconds)}s`
    onTranscript(text)
  }

  return (
    <View testID={testID} style={{ paddingHorizontal: 12, paddingVertical: 8, flexDirection: 'row', alignItems: 'center', justifyContent: 'space-between' }}>
      <TouchableOpacity
        onPressIn={start}
        onPressOut={stop}
        accessibilityRole="button"
        accessibilityLabel="Hold to record"
        style={{ paddingHorizontal: 14, paddingVertical: 10, borderRadius: 999, backgroundColor: recording ? theme.color.error : theme.color.card, borderWidth: 1, borderColor: theme.color.border, minHeight: 44 }}
      >
        <Text style={{ color: recording ? theme.color.background : theme.color.cardForeground, fontWeight: '700' }}>Mic</Text>
      </TouchableOpacity>
      <View style={{ flexDirection: 'row', alignItems: 'flex-end', gap: 3, height: 16 }}>
        {levels.map((h, idx) => (
          <View key={idx} style={{ width: 4, height: recording ? h * 2 : 4, backgroundColor: theme.color.muted, borderRadius: 2 }} />
        ))}
      </View>
      <Text style={{ color: theme.color.mutedForeground }}>{recording ? `${seconds}s` : 'Hold to talk'}</Text>

      <Modal visible={showPerm} transparent animationType="fade" onRequestClose={() => setShowPerm(false)}>
        <View style={{ flex: 1, backgroundColor: 'rgba(0,0,0,0.4)', alignItems: 'center', justifyContent: 'center', padding: 16 }}>
          <View style={{ backgroundColor: theme.color.card, borderRadius: 12, borderWidth: 1, borderColor: theme.color.border, padding: 16, width: '85%' }}>
            <Text style={{ color: theme.color.cardForeground, fontWeight: '700', marginBottom: 6 }}>Microphone disabled</Text>
            <Text style={{ color: theme.color.mutedForeground, marginBottom: 12 }}>Grant permission to record voice notes.</Text>
            <View style={{ flexDirection: 'row', justifyContent: 'flex-end', gap: 8 }}>
              <TouchableOpacity onPress={() => setShowPerm(false)} style={{ paddingHorizontal: 12, paddingVertical: 8 }}>
                <Text style={{ color: theme.color.mutedForeground }}>Cancel</Text>
              </TouchableOpacity>
              <TouchableOpacity onPress={() => setShowPerm(false)} style={{ paddingHorizontal: 12, paddingVertical: 8, borderRadius: 10, backgroundColor: theme.color.primary }}>
                <Text style={{ color: theme.color.primaryForeground, fontWeight: '700' }}>OK</Text>
              </TouchableOpacity>
            </View>
          </View>
        </View>
      </Modal>
    </View>
  )
}

export default React.memo(VoiceBar)


