import React from 'react'
import { SafeAreaView, View, Text, TouchableOpacity, ScrollView } from 'react-native'
import { useSafeAreaInsets } from 'react-native-safe-area-context'
import { useNavigation } from '@react-navigation/native'
import { useTheme } from '../../providers/ThemeProvider'

const longLicenses = `Open-source licenses:\n\n- React Native (MIT)\n- React (MIT)\n- Reanimated (MIT)\n- react-navigation (MIT)\n- victory-native (MIT)\n- and others...`;

const WebViewPlaceholder: React.FC<{ title: string }>=({ title }) => {
  const { theme } = useTheme()
  return (
    <View style={{ borderWidth: 1, borderColor: theme.color.border, borderRadius: 12, padding: 12 }}>
      <Text style={{ color: theme.color.mutedForeground }}>WebView placeholder for {title}.</Text>
    </View>
  )
}

const AboutLegal: React.FC = () => {
  const { theme } = useTheme()
  const insets = useSafeAreaInsets()
  const navigation = useNavigation<any>()

  return (
    <SafeAreaView style={{ flex: 1, backgroundColor: theme.color.background }}>
      <ScrollView contentContainerStyle={{ paddingBottom: 24 }}>
        <View style={{ paddingHorizontal: 24, paddingTop: insets.top + 12, paddingBottom: 12 }}>
          <View style={{ flexDirection: 'row', alignItems: 'center', justifyContent: 'space-between' }}>
            <TouchableOpacity onPress={() => navigation.goBack()} accessibilityRole="button" accessibilityLabel="Back" style={{ paddingHorizontal: 12, paddingVertical: 8, borderRadius: 10, borderWidth: 1, borderColor: theme.color.border }}>
              <Text style={{ color: theme.color.primary, fontWeight: '600' }}>{'< Back'}</Text>
            </TouchableOpacity>
            <Text style={{ color: theme.color.foreground, fontSize: 20, fontWeight: '700' }}>About & Legal</Text>
            <View style={{ width: 64 }} />
          </View>
        </View>

        <View style={{ paddingHorizontal: 24, gap: 12 }}>
          <TouchableOpacity onPress={() => navigation.navigate('WhatsNew')} accessibilityRole="button" accessibilityLabel="What’s New" style={{ alignSelf: 'flex-start', paddingHorizontal: 12, paddingVertical: 10, borderRadius: 12, borderWidth: 1, borderColor: theme.color.border }}>
            <Text style={{ color: theme.color.cardForeground, fontWeight: '600' }}>What’s New</Text>
          </TouchableOpacity>
          <View style={{ borderWidth: 1, borderColor: theme.color.border, borderRadius: 12, padding: 12 }}>
            <Text style={{ color: theme.color.cardForeground, fontWeight: '700' }}>Version</Text>
            <Text style={{ color: theme.color.mutedForeground, marginTop: 4 }}>v1.0.0 (build 1)</Text>
          </View>

          <TouchableOpacity onPress={() => navigation.navigate('Feedback')} accessibilityRole="button" accessibilityLabel="Send feedback" style={{ alignSelf: 'flex-start', paddingHorizontal: 12, paddingVertical: 10, borderRadius: 12, borderWidth: 1, borderColor: theme.color.border }}>
            <Text style={{ color: theme.color.cardForeground, fontWeight: '600' }}>Send feedback</Text>
          </TouchableOpacity>

          <View style={{ borderWidth: 1, borderColor: theme.color.border, borderRadius: 12, padding: 12 }}>
            <Text style={{ color: theme.color.cardForeground, fontWeight: '700', marginBottom: 8 }}>Terms</Text>
            <WebViewPlaceholder title="Terms" />
          </View>

          <View style={{ borderWidth: 1, borderColor: theme.color.border, borderRadius: 12, padding: 12 }}>
            <Text style={{ color: theme.color.cardForeground, fontWeight: '700', marginBottom: 8 }}>Privacy Policy</Text>
            <WebViewPlaceholder title="Privacy Policy" />
          </View>

          <View style={{ borderWidth: 1, borderColor: theme.color.border, borderRadius: 12, padding: 12 }}>
            <Text style={{ color: theme.color.cardForeground, fontWeight: '700', marginBottom: 8 }}>Open-source licenses</Text>
            <Text style={{ color: theme.color.mutedForeground, lineHeight: 20 }}>{longLicenses}</Text>
          </View>

          <TouchableOpacity onPress={() => navigation.navigate('Security', { screen: 'PrivacyCenter' })} accessibilityRole="button" accessibilityLabel="Open Security & Privacy Center" style={{ alignSelf: 'flex-start', paddingHorizontal: 12, paddingVertical: 10, borderRadius: 12, borderWidth: 1, borderColor: theme.color.border }}>
            <Text style={{ color: theme.color.cardForeground, fontWeight: '600' }}>Open Security & Privacy Center</Text>
          </TouchableOpacity>
        </View>
      </ScrollView>
    </SafeAreaView>
  )
}

export default AboutLegal


