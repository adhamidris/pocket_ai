import React from 'react'
import { SafeAreaView, View, TouchableOpacity, Text } from 'react-native'
import { useSafeAreaInsets } from 'react-native-safe-area-context'
import { useNavigation } from '@react-navigation/native'
import { useTheme } from '../../providers/ThemeProvider'

const PrivacyCenter: React.FC = () => {
  const insets = useSafeAreaInsets()
  const navigation = useNavigation<any>()
  const { theme } = useTheme()

  return (
    <SafeAreaView style={{ flex: 1, backgroundColor: theme.color.background }}>
      <View style={{ paddingHorizontal: 16, paddingTop: insets.top + 8, paddingBottom: 12, borderBottomWidth: 1, borderBottomColor: theme.color.border }}>
        <View style={{ flexDirection: 'row', alignItems: 'center', justifyContent: 'space-between' }}>
          <TouchableOpacity onPress={() => navigation.goBack()} accessibilityLabel="Back" accessibilityRole="button" style={{ padding: 8 }}>
            <Text style={{ color: theme.color.primary, fontWeight: '600' }}>{'< Back'}</Text>
          </TouchableOpacity>
          <Text style={{ color: theme.color.foreground, fontSize: 18, fontWeight: '700' }}>Security & Privacy Center</Text>
          <TouchableOpacity onPress={() => navigation.navigate('Security', { screen: 'PrivacyModes' })} accessibilityLabel="Privacy Modes" accessibilityRole="button" style={{ padding: 8 }}>
            <Text style={{ color: theme.color.cardForeground, fontWeight: '600' }}>Modes</Text>
          </TouchableOpacity>
        </View>
      </View>
      <View style={{ padding: 16 }}>
        <Text style={{ color: theme.color.cardForeground, fontSize: 16, fontWeight: '600', marginBottom: 8 }}>Retention controls</Text>
        <Text style={{ color: theme.color.mutedForeground, marginBottom: 16 }}>UI shell placeholder for retention sliders and consent templates.</Text>
        <Text style={{ color: theme.color.cardForeground, fontSize: 16, fontWeight: '600', marginBottom: 8 }}>Consent & Disclosures</Text>
        <Text style={{ color: theme.color.mutedForeground }}>Configure consent banners, templates, and audit viewer (stub).</Text>
      </View>
    </SafeAreaView>
  )
}

export default PrivacyCenter


