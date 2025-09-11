import React from 'react'
import { SafeAreaView, View, Text, TextInput, TouchableOpacity, ScrollView, Alert } from 'react-native'
import { useSafeAreaInsets } from 'react-native-safe-area-context'
import { useTheme } from '../../providers/ThemeProvider'
import { BusinessProfile } from '../../types/settings'
import { track } from '../../lib/analytics'
import LogoUploader from '../../components/settings/LogoUploader'
import { useNavigation } from '@react-navigation/native'

export interface BusinessProfileScreenProps {
  initial: BusinessProfile
  onSave: (profile: BusinessProfile) => void
}

const Field: React.FC<{ label: string; value?: string; onChange: (v: string) => void; placeholder?: string }>=({ label, value, onChange, placeholder }) => {
  const { theme } = useTheme()
  return (
    <View style={{ marginBottom: 12 }}>
      <Text style={{ color: theme.color.mutedForeground, marginBottom: 6 }}>{label}</Text>
      <View style={{ borderWidth: 1, borderColor: theme.color.border, borderRadius: 12, backgroundColor: theme.color.card }}>
        <TextInput
          value={value}
          onChangeText={onChange}
          placeholder={placeholder}
          placeholderTextColor={theme.color.placeholder}
          style={{ paddingHorizontal: 12, paddingVertical: 10, color: theme.color.cardForeground }}
        />
      </View>
    </View>
  )
}

const BusinessProfileScreen: React.FC<BusinessProfileScreenProps> = ({ initial, onSave }) => {
  const { theme } = useTheme()
  const insets = useSafeAreaInsets()
  const navigation = useNavigation<any>()

  const [profile, setProfile] = React.useState<BusinessProfile>(initial)

  const save = () => {
    if (!profile.name?.trim()) {
      Alert.alert('Required', 'Please enter a business name.')
      return
    }
    onSave({ ...profile })
    Alert.alert('Saved', 'Business profile updated.')
    try { track('settings.profile.save') } catch {}
  }

  return (
    <SafeAreaView style={{ flex: 1, backgroundColor: theme.color.background }}>
      <ScrollView contentContainerStyle={{ paddingBottom: 24 }}>
        <View style={{ paddingHorizontal: 24, paddingTop: insets.top + 12, paddingBottom: 12 }}>
          <View style={{ flexDirection: 'row', alignItems: 'center', justifyContent: 'space-between' }}>
            <TouchableOpacity onPress={() => navigation.goBack?.()} accessibilityRole="button" accessibilityLabel="Back" style={{ paddingHorizontal: 12, paddingVertical: 8, borderRadius: 10, borderWidth: 1, borderColor: theme.color.border }}>
              <Text style={{ color: theme.color.primary, fontWeight: '600' }}>{'< Back'}</Text>
            </TouchableOpacity>
            <Text style={{ color: theme.color.foreground, fontSize: 20, fontWeight: '700' }}>Business Profile</Text>
            <View style={{ width: 64 }} />
          </View>
        </View>

        <View style={{ paddingHorizontal: 24 }}>
          {/* Logo & Favicon */}
          <View style={{ flexDirection: 'row', gap: 12, marginBottom: 12 }}>
            <View style={{ flex: 1 }}>
              <Text style={{ color: theme.color.mutedForeground, marginBottom: 6 }}>Logo</Text>
              <LogoUploader url={profile.logoUrl} onPick={(url) => setProfile((p) => ({ ...p, logoUrl: url }))} />
            </View>
            <View style={{ flex: 1 }}>
              <Text style={{ color: theme.color.mutedForeground, marginBottom: 6 }}>Favicon (optional)</Text>
              <LogoUploader url={profile.faviconUrl} onPick={(url) => setProfile((p) => ({ ...p, faviconUrl: url }))} />
            </View>
          </View>

          {/* Fields */}
          <Field label="Name" value={profile.name} onChange={(v) => setProfile((p) => ({ ...p, name: v }))} placeholder="e.g., Acme Inc." />
          <Field label="Website" value={profile.website} onChange={(v) => setProfile((p) => ({ ...p, website: v }))} placeholder="https://example.com" />
          <Field label="Country" value={profile.country} onChange={(v) => setProfile((p) => ({ ...p, country: v }))} placeholder="e.g., USA" />
          <Field label="Industry" value={profile.industry} onChange={(v) => setProfile((p) => ({ ...p, industry: v }))} placeholder="e.g., E-commerce" />
          <Field label="Description" value={profile.description} onChange={(v) => setProfile((p) => ({ ...p, description: v }))} placeholder="Short description" />

          {/* Actions */}
          <View style={{ flexDirection: 'row', gap: 8, marginTop: 8 }}>
            <TouchableOpacity onPress={save} accessibilityRole="button" accessibilityLabel="Save profile" style={{ paddingHorizontal: 12, paddingVertical: 10, borderRadius: 12, borderWidth: 1, borderColor: theme.color.border }}>
              <Text style={{ color: theme.color.cardForeground, fontWeight: '600' }}>Save</Text>
            </TouchableOpacity>
            <TouchableOpacity onPress={() => navigation.navigate('Channels', { screen: 'ChannelsHome' })} accessibilityRole="button" accessibilityLabel="Publish link" style={{ paddingHorizontal: 12, paddingVertical: 10, borderRadius: 12, backgroundColor: theme.color.primary }}>
              <Text style={{ color: theme.color.primaryForeground, fontWeight: '700' }}>Publish link</Text>
            </TouchableOpacity>
          </View>
        </View>
      </ScrollView>
    </SafeAreaView>
  )
}

export default BusinessProfileScreen


