import React from 'react'
import { SafeAreaView, View, Text, TouchableOpacity, ScrollView, TextInput, Alert } from 'react-native'
import { useSafeAreaInsets } from 'react-native-safe-area-context'
import { useTheme } from '../../providers/ThemeProvider'
import { useNavigation } from '@react-navigation/native'
import { ResidencySetting, AuditEvent } from '../../types/security'
import { track } from '../../lib/analytics'
import ResidencyPicker from '../../components/security/ResidencyPicker'

export interface DataResidencyScreenProps {
  value: ResidencySetting
  onSave: (v: ResidencySetting, audit: AuditEvent) => void
  onClose?: () => void
}

const nowTs = () => Date.now()

const DataResidencyScreen: React.FC<DataResidencyScreenProps> = ({ value, onSave, onClose }) => {
  const { theme } = useTheme()
  const insets = useSafeAreaInsets()
  const navigation = useNavigation<any>()

  const [val, setVal] = React.useState<ResidencySetting>(value)
  const [effective, setEffective] = React.useState<string>(value.effectiveAt ? new Date(value.effectiveAt).toISOString().slice(0, 16) : '') // ISO local-ish
  const [note, setNote] = React.useState<string>(value.note || '')

  const save = () => {
    const effAt = effective ? new Date(effective).getTime() : undefined
    const next: ResidencySetting = { ...val, effectiveAt: effAt, note: note.trim() || undefined }
    const evt: AuditEvent = { id: `a-${nowTs()}`, ts: nowTs(), actor: 'me', action: 'policy.update', entityType: 'policy', details: `Residency ${value.region} -> ${next.region}; effective ${effAt ? new Date(effAt).toISOString() : 'immediately'}`, risk: 'low' }
    onSave(next, evt)
    try { track('residency.save', { region: next.region }) } catch {}
    Alert.alert('Saved', 'Data residency updated (UI-only).')
    if (onClose) onClose()
    else navigation.goBack()
  }

  return (
    <SafeAreaView style={{ flex: 1, backgroundColor: theme.color.background }}>
      <ScrollView contentContainerStyle={{ paddingBottom: 24 }}>
        {/* Header */}
        <View style={{ paddingHorizontal: 24, paddingTop: insets.top + 12, paddingBottom: 12 }}>
          <View style={{ flexDirection: 'row', alignItems: 'center', justifyContent: 'space-between' }}>
            <TouchableOpacity onPress={() => (onClose ? onClose() : navigation.goBack())} accessibilityRole="button" accessibilityLabel="Back" style={{ paddingHorizontal: 12, paddingVertical: 8, borderRadius: 10, borderWidth: 1, borderColor: theme.color.border }}>
              <Text style={{ color: theme.color.primary, fontWeight: '600' }}>{'< Back'}</Text>
            </TouchableOpacity>
            <Text style={{ color: theme.color.foreground, fontSize: 20, fontWeight: '700' }}>Data Residency</Text>
            <TouchableOpacity onPress={save} accessibilityRole="button" accessibilityLabel="Save" style={{ paddingHorizontal: 12, paddingVertical: 8, borderRadius: 10, backgroundColor: theme.color.primary }}>
              <Text style={{ color: theme.color.primaryForeground, fontWeight: '700' }}>Save</Text>
            </TouchableOpacity>
          </View>
        </View>

        <View style={{ paddingHorizontal: 24, gap: 12 }}>
          <View style={{ borderWidth: 1, borderColor: theme.color.border, borderRadius: 12, padding: 12 }}>
            <Text style={{ color: theme.color.mutedForeground, marginBottom: 6 }}>Region</Text>
            <ResidencyPicker value={val} onChange={setVal} />
            <Text style={{ color: theme.color.mutedForeground, marginTop: 8 }}>Changing the data residency region affects where your data is stored and processed. Some features may be limited in certain regions. This is a UI-only demo.</Text>
          </View>

          <View style={{ borderWidth: 1, borderColor: theme.color.border, borderRadius: 12, padding: 12 }}>
            <Text style={{ color: theme.color.cardForeground, fontWeight: '700', marginBottom: 6 }}>Effective date (optional)</Text>
            <TextInput
              value={effective}
              onChangeText={setEffective}
              placeholder="YYYY-MM-DDTHH:mm"
              placeholderTextColor={theme.color.placeholder}
              accessibilityLabel="Effective date"
              style={{ borderWidth: 1, borderColor: theme.color.border, borderRadius: 10, paddingHorizontal: 10, paddingVertical: 8, color: theme.color.cardForeground }}
            />
            <Text style={{ color: theme.color.mutedForeground, marginTop: 6, fontSize: 12 }}>Enter ISO-like local date/time (UI stub). Leave blank to apply immediately.</Text>
          </View>

          <View style={{ borderWidth: 1, borderColor: theme.color.border, borderRadius: 12, padding: 12 }}>
            <Text style={{ color: theme.color.cardForeground, fontWeight: '700', marginBottom: 6 }}>Note (optional)</Text>
            <TextInput
              value={note}
              onChangeText={setNote}
              placeholder="Internal note"
              placeholderTextColor={theme.color.placeholder}
              accessibilityLabel="Residency note"
              style={{ borderWidth: 1, borderColor: theme.color.border, borderRadius: 10, paddingHorizontal: 10, paddingVertical: 8, color: theme.color.cardForeground }}
            />
          </View>
        </View>
      </ScrollView>
    </SafeAreaView>
  )
}

export default DataResidencyScreen


