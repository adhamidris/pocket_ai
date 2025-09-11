import React from 'react'
import { SafeAreaView, View, Text, TouchableOpacity, Alert, ScrollView } from 'react-native'
import { useTheme } from '../../providers/ThemeProvider'
import { TaxInfo } from '../../types/billing'
import TaxForm from '../../components/billing/TaxForm'
import { useNavigation, useRoute } from '@react-navigation/native'
import OfflineBanner from '../../components/dashboard/OfflineBanner'

type Params = { tax?: TaxInfo }

const defaultTax: TaxInfo = { country: '', invoiceName: '', address1: '', city: '', zip: '' }

const euHint = 'EU: Provide a valid VAT ID (e.g., DE123456789). Reverse-charge may apply.'
const menaHint = 'MENA: Provide VAT/TRN (e.g., UAE TRN, KSA VAT). Ensure legal invoice name matches trade license.'

const validate = (t: TaxInfo): string | null => {
  if (!t.country || !t.country.trim()) return 'Country is required.'
  if (t.vatId && !/^[A-Za-z0-9\-]{6,20}$/.test(t.vatId)) return 'VAT/Tax ID looks invalid.'
  return null
}

const TaxBillingProfile: React.FC = () => {
  const { theme } = useTheme()
  const navigation = useNavigation<any>()
  const route = useRoute<any>()
  const initial = ((route.params || {}) as Params).tax || defaultTax
  const [value, setValue] = React.useState<TaxInfo>(initial)
  const [offline, setOffline] = React.useState(false)
  const [queued, setQueued] = React.useState(false)

  const onSave = () => {
    const err = validate(value)
    if (err) {
      Alert.alert('Invalid', err)
      return
    }
    setQueued(true)
    setTimeout(() => setQueued(false), 1200)
    try { (require('../../lib/analytics') as any).track('billing.tax.save') } catch {}
    navigation.navigate('Billing', { tax: value })
  }

  return (
    <SafeAreaView style={{ flex: 1, backgroundColor: theme.color.background }}>
      <ScrollView>
        <View style={{ paddingHorizontal: 24, paddingVertical: 12 }}>
          <View style={{ flexDirection: 'row', alignItems: 'center', justifyContent: 'space-between', marginBottom: 12 }}>
            <Text style={{ color: theme.color.foreground, fontSize: 24, fontWeight: '700' }}>Tax & Billing Profile</Text>
            <TouchableOpacity onPress={onSave} accessibilityRole="button" accessibilityLabel="Save profile" style={{ paddingHorizontal: 12, paddingVertical: 8, borderRadius: 10, backgroundColor: theme.color.primary }}>
              <Text style={{ color: theme.color.primaryForeground, fontWeight: '700' }}>Save</Text>
            </TouchableOpacity>
          </View>
          {offline && <OfflineBanner visible />}

          <View style={{ borderWidth: 1, borderColor: theme.color.border, borderRadius: 12, padding: 12, marginBottom: 12 }}>
            <TaxForm value={value} onChange={setValue} />
          </View>

          <View style={{ borderWidth: 1, borderColor: theme.color.border, borderRadius: 12, padding: 12, gap: 8 }}>
            <Text style={{ color: theme.color.cardForeground, fontWeight: '700' }}>Regional hints</Text>
            <Text style={{ color: theme.color.mutedForeground }}>{euHint}</Text>
            <Text style={{ color: theme.color.mutedForeground }}>{menaHint}</Text>
            {queued ? <Text style={{ color: theme.color.warning }}>⏱ Saving…</Text> : null}
          </View>
        </View>
      </ScrollView>
    </SafeAreaView>
  )
}

export default TaxBillingProfile


