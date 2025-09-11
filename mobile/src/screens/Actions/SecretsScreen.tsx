import React from 'react'
import { SafeAreaView, View, Text, TouchableOpacity, ScrollView, Alert } from 'react-native'
import { useSafeAreaInsets } from 'react-native-safe-area-context'
import { useTheme } from '../../providers/ThemeProvider'
import { useNavigation } from '@react-navigation/native'
import { SecretRef } from '../../types/actions'
import SecretRow from '../../components/actions/SecretRow'

const demoSecrets: SecretRef[] = [
  { key: 'smtp', note: 'Email SMTP credentials (notifications)' },
  { key: 'whatsapp_api', note: 'WhatsApp Business API token' },
  { key: 'stripe_api', note: 'Stripe API key (refunds/receipts)' },
]

const SecretsScreen: React.FC = () => {
  const { theme } = useTheme()
  const insets = useSafeAreaInsets()
  const navigation = useNavigation<any>()

  const rotate = (k: string) => {
    Alert.alert('Rotate', `Rotating ${k} (UI-only)`) 
  }

  return (
    <SafeAreaView style={{ flex: 1, backgroundColor: theme.color.background }}>
      <ScrollView contentContainerStyle={{ paddingBottom: 24 }}>
        {/* Header */}
        <View style={{ paddingHorizontal: 24, paddingTop: insets.top + 12, paddingBottom: 12 }}>
          <View style={{ flexDirection: 'row', alignItems: 'center', justifyContent: 'space-between' }}>
            <TouchableOpacity onPress={() => navigation.goBack()} accessibilityLabel="Back" accessibilityRole="button" style={{ paddingHorizontal: 12, paddingVertical: 8, borderRadius: 10, borderWidth: 1, borderColor: theme.color.border }}>
              <Text style={{ color: theme.color.primary, fontWeight: '700' }}>{'< Back'}</Text>
            </TouchableOpacity>
            <Text style={{ color: theme.color.foreground, fontSize: 20, fontWeight: '700' }}>Secrets</Text>
            <View style={{ width: 64 }} />
          </View>
          <View style={{ marginTop: 8, padding: 12, borderRadius: 12, borderWidth: 1, borderColor: theme.color.border, backgroundColor: theme.color.secondary }}>
            <Text style={{ color: theme.color.mutedForeground }}>UI-only placeholder. Secrets are not stored in app; a backend vault will be used later.</Text>
          </View>
        </View>

        {/* List */}
        <View style={{ paddingHorizontal: 24, gap: 12 }}>
          {demoSecrets.map((s) => (
            <SecretRow key={s.key} refItem={s} onRotate={() => rotate(s.key)} />
          ))}
        </View>
      </ScrollView>
    </SafeAreaView>
  )
}

export default SecretsScreen


