import React from 'react'
import { Modal, View, Text, Switch, TouchableOpacity } from 'react-native'
import { useTheme } from '../../providers/ThemeProvider'
import { VoicePrefs } from '../../hooks/useVoiceSettings'

export const VoiceSettingsSheet: React.FC<{ visible: boolean; onClose: () => void; prefs: VoicePrefs; onChange: (p: Partial<VoicePrefs>) => void }>
  = ({ visible, onClose, prefs, onChange }) => {
  const { theme } = useTheme()
  return (
    <Modal visible={visible} onRequestClose={onClose} transparent animationType="slide">
      <View style={{ flex: 1, backgroundColor: 'rgba(0,0,0,0.6)', justifyContent: 'flex-end' }}>
        <View style={{ backgroundColor: theme.color.card, padding: 16, borderTopLeftRadius: theme.radius.xl, borderTopRightRadius: theme.radius.xl }}>
          <View style={{ height: 4, width: 40, borderRadius: 2, backgroundColor: theme.color.muted, alignSelf: 'center', marginBottom: 12 }} />
          <Text style={{ color: theme.color.cardForeground, fontWeight: '700', fontSize: 16, marginBottom: 8 }}>Voice Settings</Text>
          {[
            { key: 'enabled', label: 'Enable voice controls' },
            { key: 'pushToTalk', label: 'Push to talk (hold to record)' },
            { key: 'tapToToggle', label: 'Tap to toggle mic' },
            { key: 'autoplayTts', label: 'Autoplay TTS for long answers' },
          ].map((row) => (
            <View key={row.key} style={{ flexDirection: 'row', alignItems: 'center', justifyContent: 'space-between', borderWidth: 1, borderColor: theme.color.border, borderRadius: 12, paddingHorizontal: 12, paddingVertical: 10, marginTop: 10 }}>
              <Text style={{ color: theme.color.cardForeground }}>{row.label}</Text>
              <Switch value={(prefs as any)[row.key]} onValueChange={(v) => onChange({ [row.key]: v } as any)} />
            </View>
          ))}
          <TouchableOpacity onPress={onClose} accessibilityRole="button" accessibilityLabel="Close" style={{ alignSelf: 'stretch', padding: 12, alignItems: 'center', marginTop: 12 }}>
            <Text style={{ color: theme.color.primary, fontWeight: '700' }}>Close</Text>
          </TouchableOpacity>
        </View>
      </View>
    </Modal>
  )
}

export default VoiceSettingsSheet


