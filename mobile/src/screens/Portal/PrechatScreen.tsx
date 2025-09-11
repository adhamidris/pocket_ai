import React from 'react'
import { SafeAreaView, View, Text, TextInput, TouchableOpacity, Alert } from 'react-native'
import { useSafeAreaInsets } from 'react-native-safe-area-context'
import { useTheme } from '../../providers/ThemeProvider'
import { useNavigation } from '@react-navigation/native'
import ConsentBanner from '../../components/portal/ConsentBanner'

const emailOk = (e: string) => /^[^\s@]+@[^\s@]+\.[^\s@]+$/.test(e)

const PrechatScreen: React.FC = () => {
  const { theme } = useTheme()
  const insets = useSafeAreaInsets()
  const navigation = useNavigation<any>()

  const [name, setName] = React.useState('')
  const [email, setEmail] = React.useState('')
  const [topic, setTopic] = React.useState('Support')
  const [accepted, setAccepted] = React.useState(false)

  const valid = name.trim().length > 0 && (!email || emailOk(email))

  const start = () => {
    if (!valid || !accepted) return
    // persist locally in memory (UI-only)
    // @ts-ignore
    global.__prechat = { name, email, topic }
    try { (require('../../lib/analytics') as any).track('portal.prechat.submit') } catch {}
    navigation.navigate('Portal', { notice: `Session started for ${name}` })
  }

  return (
    <SafeAreaView style={{ flex: 1, backgroundColor: theme.color.background }}>
      <View style={{ paddingHorizontal: 16, paddingTop: insets.top + 12, paddingBottom: 12 }}>
        <Text style={{ color: theme.color.foreground, fontSize: 24, fontWeight: '700' }}>Pre‑chat</Text>
      </View>
      <View style={{ paddingHorizontal: 16, gap: 12 }}>
        <View>
          <Text style={{ color: theme.color.mutedForeground, marginBottom: 6 }}>Name *</Text>
          <TextInput value={name} onChangeText={setName} placeholder="Your name" placeholderTextColor={theme.color.placeholder} style={{ borderWidth: 1, borderColor: theme.color.border, borderRadius: 10, paddingHorizontal: 10, paddingVertical: 10, color: theme.color.cardForeground }} />
        </View>
        <View>
          <Text style={{ color: theme.color.mutedForeground, marginBottom: 6 }}>Email</Text>
          <TextInput value={email} onChangeText={setEmail} placeholder="you@example.com" keyboardType="email-address" placeholderTextColor={theme.color.placeholder} style={{ borderWidth: 1, borderColor: theme.color.border, borderRadius: 10, paddingHorizontal: 10, paddingVertical: 10, color: theme.color.cardForeground }} />
          {!!email && !emailOk(email) && <Text style={{ color: theme.color.error, marginTop: 4 }}>Invalid email</Text>}
        </View>
        <View>
          <Text style={{ color: theme.color.mutedForeground, marginBottom: 6 }}>Topic</Text>
          <TextInput value={topic} onChangeText={setTopic} placeholder="Support" placeholderTextColor={theme.color.placeholder} style={{ borderWidth: 1, borderColor: theme.color.border, borderRadius: 10, paddingHorizontal: 10, paddingVertical: 10, color: theme.color.cardForeground }} />
        </View>
        <ConsentBanner message="By starting chat, you agree to our privacy policy." onAccept={() => setAccepted(true)} onDecline={() => setAccepted(false)} />
        <TouchableOpacity onPress={start} disabled={!valid || !accepted} accessibilityRole="button" accessibilityLabel="Start chat" style={{ alignSelf: 'flex-start', paddingHorizontal: 12, paddingVertical: 12, borderRadius: 10, backgroundColor: theme.color.primary, opacity: !valid || !accepted ? 0.5 : 1 }}>
          <Text style={{ color: theme.color.primaryForeground, fontWeight: '700' }}>Start Chat</Text>
        </TouchableOpacity>
      </View>
    </SafeAreaView>
  )
}

export default PrechatScreen


