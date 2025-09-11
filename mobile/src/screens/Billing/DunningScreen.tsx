import React from 'react'
import { SafeAreaView, View, Text, TouchableOpacity, ScrollView, Alert } from 'react-native'
import { useTheme } from '../../providers/ThemeProvider'
import { DunningState } from '../../types/billing'
import DunningBanner from '../../components/billing/DunningBanner'
import { useNavigation, useRoute } from '@react-navigation/native'

type Params = { dunning?: DunningState }

interface AttemptItem { id: string; at: number; result: 'success' | 'failure'; message?: string }

const DunningScreen: React.FC = () => {
  const { theme } = useTheme()
  const navigation = useNavigation<any>()
  const route = useRoute<any>()
  const initial = (route.params as Params | undefined)?.dunning || { cardExpiring: true, lastChargeFailed: true, lastFailureMessage: 'Insufficient funds', graceUntil: Math.floor(Date.now() / 1000) + 3 * 86400 } as DunningState

  const [state, setState] = React.useState<DunningState>(initial)
  const [attempts, setAttempts] = React.useState<AttemptItem[]>([
    { id: 'a1', at: Math.floor(Date.now() / 1000) - 3 * 3600, result: 'failure', message: 'Insufficient funds' },
    { id: 'a0', at: Math.floor(Date.now() / 1000) - 26 * 3600, result: 'failure', message: 'Network error' }
  ])

  const fmt = (ts: number) => new Date(ts * 1000).toLocaleString()

  const retryNow = () => {
    const success = Math.random() < 0.6
    const now = Math.floor(Date.now() / 1000)
    if (success) {
      setAttempts((arr) => [{ id: `a${arr.length + 1}`, at: now, result: 'success' }, ...arr])
      setState((s) => ({ ...s, lastChargeFailed: false, lastFailureMessage: undefined }))
      Alert.alert('Success', 'Charge retried successfully.')
      navigation.navigate('Billing', { dunning: { ...state, lastChargeFailed: false, lastFailureMessage: undefined } })
    } else {
      setAttempts((arr) => [{ id: `a${arr.length + 1}`, at: now, result: 'failure', message: 'Card declined' }, ...arr])
      setState((s) => ({ ...s, lastChargeFailed: true, lastFailureMessage: 'Card declined' }))
      Alert.alert('Failed', 'Retry failed. Please update your card.')
    }
    try { (require('../../lib/analytics') as any).track('billing.dunning.retry', { success }) } catch {}
  }

  const updateCard = () => navigation.navigate('PaymentMethods', { dunning: state })
  const contactSupport = () => Alert.alert('Contact support', 'We will connect you to support (stub).')

  const remainingGrace = state.graceUntil ? Math.max(0, state.graceUntil - Math.floor(Date.now() / 1000)) : 0
  const graceText = state.graceUntil ? `${Math.floor(remainingGrace / 86400)}d ${Math.floor((remainingGrace % 86400) / 3600)}h ${Math.floor((remainingGrace % 3600) / 60)}m` : '—'

  return (
    <SafeAreaView style={{ flex: 1, backgroundColor: theme.color.background }}>
      <ScrollView>
        <View style={{ paddingHorizontal: 24, paddingVertical: 12 }}>
          <Text style={{ color: theme.color.foreground, fontSize: 24, fontWeight: '700', marginBottom: 12 }}>Billing Issue</Text>

          <View style={{ marginBottom: 12 }}>
            <DunningBanner state={state} onUpdateCard={updateCard} />
          </View>

          <View style={{ borderWidth: 1, borderColor: theme.color.border, borderRadius: 12, padding: 12, marginBottom: 12 }}>
            <View style={{ flexDirection: 'row', alignItems: 'center', justifyContent: 'space-between', marginBottom: 8 }}>
              <Text style={{ color: theme.color.cardForeground, fontWeight: '700' }}>Retry charge</Text>
              <TouchableOpacity onPress={retryNow} accessibilityRole="button" accessibilityLabel="Retry now" style={{ paddingHorizontal: 12, paddingVertical: 8, borderRadius: 10, backgroundColor: theme.color.primary }}>
                <Text style={{ color: theme.color.primaryForeground, fontWeight: '700' }}>Retry now</Text>
              </TouchableOpacity>
            </View>
            <Text style={{ color: theme.color.mutedForeground }}>We will attempt to charge your default payment method.</Text>
          </View>

          <View style={{ borderWidth: 1, borderColor: theme.color.border, borderRadius: 12, padding: 12, marginBottom: 12 }}>
            <Text style={{ color: theme.color.cardForeground, fontWeight: '700', marginBottom: 8 }}>Attempts timeline</Text>
            {attempts.map((a) => (
              <View key={a.id} style={{ flexDirection: 'row', alignItems: 'center', justifyContent: 'space-between', borderTopWidth: 1, borderTopColor: theme.color.border, paddingVertical: 8 }}>
                <Text style={{ color: theme.color.cardForeground }}>{fmt(a.at)}</Text>
                <Text style={{ color: a.result === 'success' ? theme.color.success : theme.color.error, fontWeight: '700' }}>{a.result.toUpperCase()}</Text>
              </View>
            ))}
          </View>

          <View style={{ borderWidth: 1, borderColor: theme.color.border, borderRadius: 12, padding: 12, marginBottom: 12 }}>
            <Text style={{ color: theme.color.cardForeground, fontWeight: '700', marginBottom: 8 }}>Grace period</Text>
            <Text style={{ color: theme.color.mutedForeground }}>Time remaining: {graceText}</Text>
            <TouchableOpacity onPress={contactSupport} accessibilityRole="button" accessibilityLabel="Contact support" style={{ alignSelf: 'flex-start', marginTop: 10, paddingHorizontal: 12, paddingVertical: 10, borderRadius: 10, borderWidth: 1, borderColor: theme.color.border }}>
              <Text style={{ color: theme.color.cardForeground, fontWeight: '700' }}>Contact support</Text>
            </TouchableOpacity>
          </View>
        </View>
      </ScrollView>
    </SafeAreaView>
  )
}

export default DunningScreen


