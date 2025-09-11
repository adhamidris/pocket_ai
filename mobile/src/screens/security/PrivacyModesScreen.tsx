import React from 'react'
import { SafeAreaView, View, Text, TouchableOpacity, ScrollView, DeviceEventEmitter, Alert } from 'react-native'
import { useSafeAreaInsets } from 'react-native-safe-area-context'
import { useTheme } from '../../providers/ThemeProvider'
import { useNavigation } from '@react-navigation/native'
import { PrivacyMode } from '../../types/security'
import PrivacyModeToggles from '../../components/security/PrivacyModeToggles'
import { track } from '../../lib/analytics'

export interface PrivacyModesScreenProps {
  initial?: PrivacyMode
}

const PrivacyModesScreen: React.FC<PrivacyModesScreenProps> = ({ initial }) => {
  const { theme } = useTheme()
  const insets = useSafeAreaInsets()
  const navigation = useNavigation<any>()

  const [vals, setVals] = React.useState<PrivacyMode>(initial || { anonymizeAnalytics: false, hideContactPII: false, strictLogging: false })

  const save = () => {
    DeviceEventEmitter.emit('privacy.modes', vals)
    try { track('privacy.modes.update') } catch {}
    try { track('privacy.modes.save') } catch {}
    Alert.alert('Saved', 'Privacy modes updated (UI-only).')
    navigation.goBack()
  }

  return (
    <SafeAreaView style={{ flex: 1, backgroundColor: theme.color.background }}>
      <ScrollView contentContainerStyle={{ paddingBottom: 24 }}>
        {/* Header */}
        <View style={{ paddingHorizontal: 24, paddingTop: insets.top + 12, paddingBottom: 12 }}>
          <View style={{ flexDirection: 'row', alignItems: 'center', justifyContent: 'space-between' }}>
            <TouchableOpacity onPress={() => navigation.goBack()} accessibilityRole="button" accessibilityLabel="Back" style={{ paddingHorizontal: 12, paddingVertical: 8, borderRadius: 10, borderWidth: 1, borderColor: theme.color.border }}>
              <Text style={{ color: theme.color.primary, fontWeight: '600' }}>{'< Back'}</Text>
            </TouchableOpacity>
            <Text style={{ color: theme.color.foreground, fontSize: 20, fontWeight: '700' }}>Privacy Modes</Text>
            <TouchableOpacity onPress={save} accessibilityRole="button" accessibilityLabel="Save" style={{ paddingHorizontal: 12, paddingVertical: 8, borderRadius: 10, backgroundColor: theme.color.primary }}>
              <Text style={{ color: theme.color.primaryForeground, fontWeight: '700' }}>Save</Text>
            </TouchableOpacity>
          </View>
        </View>

        <View style={{ paddingHorizontal: 24, gap: 12 }}>
          <PrivacyModeToggles value={vals} onChange={setVals} />
          <View style={{ borderWidth: 1, borderColor: theme.color.border, borderRadius: 12, padding: 12 }}>
            <Text style={{ color: theme.color.cardForeground, fontWeight: '700', marginBottom: 6 }}>Effects (UI-only)</Text>
            <Text style={{ color: theme.color.mutedForeground, marginBottom: 4 }}>• Anonymize analytics: hides PII in analytics and adds "Anonymized" badge.</Text>
            <Text style={{ color: theme.color.mutedForeground, marginBottom: 4 }}>• Hide contact PII: masks emails/phones in CRM lists and details.</Text>
            <Text style={{ color: theme.color.mutedForeground }}>• Strict logging: reduces detail in audit trails (stub).</Text>
          </View>
        </View>
      </ScrollView>
    </SafeAreaView>
  )
}

export default PrivacyModesScreen


