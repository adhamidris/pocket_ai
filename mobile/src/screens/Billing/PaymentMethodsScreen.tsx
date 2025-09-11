import React from 'react'
import { SafeAreaView, View, Text, TouchableOpacity, ScrollView, Modal, TextInput, Switch } from 'react-native'
import { useTheme } from '../../providers/ThemeProvider'
import { PaymentMethod, PaymentBrand, DunningState } from '../../types/billing'
import PaymentMethodRow from '../../components/billing/PaymentMethodRow'
import DunningBanner from '../../components/billing/DunningBanner'
import { useNavigation, useRoute } from '@react-navigation/native'

type Params = { methods?: PaymentMethod[]; dunning?: DunningState }

const brands: PaymentBrand[] = ['visa', 'mastercard', 'amex', 'mada', 'paypal']

const PaymentMethodsScreen: React.FC = () => {
  const { theme } = useTheme()
  const navigation = useNavigation<any>()
  const route = useRoute<any>()
  const params = (route.params || {}) as Params

  const [methods, setMethods] = React.useState<PaymentMethod[]>(params.methods && params.methods.length ? params.methods : [{ id: 'pm_1', brand: 'visa', last4: '4242', expMonth: 12, expYear: 2030, isDefault: true }])
  const [dunning, setDunning] = React.useState<DunningState | undefined>(params.dunning)

  const [applePay, setApplePay] = React.useState<boolean>(false)
  const [googlePay, setGooglePay] = React.useState<boolean>(false)
  const [paypalConnected, setPaypalConnected] = React.useState<boolean>(false)

  const [addOpen, setAddOpen] = React.useState(false)
  const [newBrand, setNewBrand] = React.useState<PaymentBrand>('visa')
  const [newLast4, setNewLast4] = React.useState('4242')
  const [newExpMonth, setNewExpMonth] = React.useState('12')
  const [newExpYear, setNewExpYear] = React.useState('2030')

  const [confirmRemoveId, setConfirmRemoveId] = React.useState<string | null>(null)

  const setDefault = (id: string) => {
    setMethods((list) => list.map((m) => ({ ...m, isDefault: m.id === id })))
    if (dunning) {
      setDunning(undefined)
    }
    setTimeout(() => {}, 500)
    try { (require('../../lib/analytics') as any).track('billing.card.default') } catch {}
  }

  const removeMethod = (id: string) => {
    setMethods((list) => {
      const next = list.filter((m) => m.id !== id)
      if (!next.some((m) => m.isDefault) && next[0]) {
        next[0].isDefault = true
      }
      return [...next]
    })
    setConfirmRemoveId(null)
    setTimeout(() => {}, 500)
    try { (require('../../lib/analytics') as any).track('billing.card.remove') } catch {}
  }

  const addMethod = () => {
    const id = `pm_${Math.random().toString(36).slice(2, 8)}`
    setMethods((list) => {
      const next: PaymentMethod = {
        id,
        brand: newBrand,
        last4: newLast4.slice(-4),
        expMonth: Number(newExpMonth) || undefined,
        expYear: Number(newExpYear) || undefined,
        isDefault: list.length === 0
      }
      const updated = [...list, next]
      // If becomes default and there was dunning, clear it
      if (next.isDefault && dunning) setDunning(undefined)
      return updated
    })
    setAddOpen(false)
    setTimeout(() => {}, 500)
    try { (require('../../lib/analytics') as any).track('billing.card.add') } catch {}
  }

  const done = () => {
    navigation.navigate('Billing', { updatedMethods: methods, dunningCleared: !dunning })
  }

  return (
    <SafeAreaView style={{ flex: 1, backgroundColor: theme.color.background }}>
      <ScrollView>
        <View style={{ paddingHorizontal: 24, paddingVertical: 12 }}>
          <View style={{ flexDirection: 'row', alignItems: 'center', justifyContent: 'space-between', marginBottom: 12 }}>
            <Text style={{ color: theme.color.foreground, fontSize: 24, fontWeight: '700' }}>Payment Methods</Text>
            <TouchableOpacity onPress={done} accessibilityRole="button" accessibilityLabel="Done" style={{ paddingHorizontal: 12, paddingVertical: 8, borderRadius: 10, borderWidth: 1, borderColor: theme.color.border }}>
              <Text style={{ color: theme.color.cardForeground, fontWeight: '700' }}>Done</Text>
            </TouchableOpacity>
          </View>

          {!!dunning && (
            <View style={{ marginBottom: 12 }}>
              <DunningBanner state={dunning} onUpdateCard={() => setAddOpen(true)} />
            </View>
          )}

          <View style={{ borderWidth: 1, borderColor: theme.color.border, borderRadius: 12, padding: 12, marginBottom: 12 }}>
            <View style={{ flexDirection: 'row', alignItems: 'center', justifyContent: 'space-between', marginBottom: 8 }}>
              <Text style={{ color: theme.color.cardForeground, fontWeight: '700' }}>Cards & Wallets</Text>
              <TouchableOpacity onPress={() => setAddOpen(true)} accessibilityRole="button" accessibilityLabel="Add method" style={{ paddingHorizontal: 10, paddingVertical: 8, borderRadius: 8, backgroundColor: theme.color.primary }}>
                <Text style={{ color: theme.color.primaryForeground, fontWeight: '700' }}>Add method</Text>
              </TouchableOpacity>
            </View>
            <View>
              {methods.map((m) => (
                <View key={m.id} style={{ borderTopWidth: 1, borderTopColor: theme.color.border }}>
                  <PaymentMethodRow
                    pm={m}
                    onSetDefault={() => setDefault(m.id)}
                    onRemove={() => setConfirmRemoveId(m.id)}
                    queued={confirmRemoveId === m.id}
                  />
                  {confirmRemoveId === m.id && (
                    <View style={{ flexDirection: 'row', justifyContent: 'flex-end', gap: 8, marginBottom: 8 }}>
                      <TouchableOpacity onPress={() => setConfirmRemoveId(null)} accessibilityRole="button" accessibilityLabel="Cancel remove" style={{ paddingHorizontal: 10, paddingVertical: 8, borderRadius: 8, borderWidth: 1, borderColor: theme.color.border }}>
                        <Text style={{ color: theme.color.cardForeground }}>Cancel</Text>
                      </TouchableOpacity>
                      <TouchableOpacity onPress={() => removeMethod(m.id)} accessibilityRole="button" accessibilityLabel="Confirm remove" style={{ paddingHorizontal: 10, paddingVertical: 8, borderRadius: 8, backgroundColor: theme.color.error }}>
                        <Text style={{ color: theme.color.background, fontWeight: '700' }}>Remove</Text>
                      </TouchableOpacity>
                    </View>
                  )}
                </View>
              ))}
              {methods.length === 0 && (
                <Text style={{ color: theme.color.mutedForeground }}>No payment methods added.</Text>
              )}
            </View>
          </View>

          <View style={{ borderWidth: 1, borderColor: theme.color.border, borderRadius: 12, padding: 12, marginBottom: 12 }}>
            <Text style={{ color: theme.color.cardForeground, fontWeight: '700', marginBottom: 8 }}>Wallets</Text>
            <View style={{ flexDirection: 'row', alignItems: 'center', justifyContent: 'space-between', marginBottom: 8 }}>
              <Text style={{ color: theme.color.cardForeground }}>Apple Pay</Text>
              <Switch value={applePay} onValueChange={setApplePay} accessibilityLabel="Apple Pay" />
            </View>
            <View style={{ flexDirection: 'row', alignItems: 'center', justifyContent: 'space-between' }}>
              <Text style={{ color: theme.color.cardForeground }}>Google Pay</Text>
              <Switch value={googlePay} onValueChange={setGooglePay} accessibilityLabel="Google Pay" />
            </View>
          </View>

          <View style={{ borderWidth: 1, borderColor: theme.color.border, borderRadius: 12, padding: 12 }}>
            <Text style={{ color: theme.color.cardForeground, fontWeight: '700', marginBottom: 8 }}>PayPal</Text>
            {paypalConnected ? (
              <View style={{ flexDirection: 'row', alignItems: 'center', justifyContent: 'space-between' }}>
                <Text style={{ color: theme.color.cardForeground }}>Connected</Text>
                <TouchableOpacity onPress={() => setPaypalConnected(false)} accessibilityRole="button" accessibilityLabel="Disconnect PayPal" style={{ paddingHorizontal: 10, paddingVertical: 8, borderRadius: 8, borderWidth: 1, borderColor: theme.color.border }}>
                  <Text style={{ color: theme.color.cardForeground, fontWeight: '700' }}>Disconnect</Text>
                </TouchableOpacity>
              </View>
            ) : (
              <TouchableOpacity onPress={() => setPaypalConnected(true)} accessibilityRole="button" accessibilityLabel="Connect PayPal" style={{ alignSelf: 'flex-start', paddingHorizontal: 10, paddingVertical: 8, borderRadius: 8, backgroundColor: theme.color.secondary }}>
                <Text style={{ color: theme.color.secondaryForeground, fontWeight: '700' }}>Connect PayPal</Text>
              </TouchableOpacity>
            )}
          </View>
        </View>
      </ScrollView>

      <Modal visible={addOpen} transparent animationType="slide" onRequestClose={() => setAddOpen(false)}>
        <View style={{ flex: 1, backgroundColor: 'rgba(0,0,0,0.4)', justifyContent: 'center', alignItems: 'center', padding: 16 }}>
          <View style={{ backgroundColor: theme.color.card, borderRadius: 12, borderWidth: 1, borderColor: theme.color.border, width: '100%', padding: 12 }}>
            <Text style={{ color: theme.color.cardForeground, fontSize: 18, fontWeight: '700', marginBottom: 8 }}>Add Payment Method</Text>
            <View style={{ flexDirection: 'row', flexWrap: 'wrap', gap: 8, marginBottom: 8 }}>
              {brands.map((b) => (
                <TouchableOpacity key={b} onPress={() => setNewBrand(b)} accessibilityRole="button" accessibilityLabel={`Brand ${b}`} style={{ paddingHorizontal: 10, paddingVertical: 8, borderRadius: 8, borderWidth: 1, borderColor: newBrand === b ? theme.color.primary : theme.color.border, backgroundColor: newBrand === b ? theme.color.primary : theme.color.card }}>
                  <Text style={{ color: newBrand === b ? theme.color.primaryForeground : theme.color.cardForeground, fontWeight: '700' }}>{b.toUpperCase()}</Text>
                </TouchableOpacity>
              ))}
            </View>
            <View style={{ flexDirection: 'row', gap: 8 }}>
              <View style={{ flex: 1 }}>
                <Text style={{ color: theme.color.mutedForeground, marginBottom: 4 }}>Last 4</Text>
                <TextInput value={newLast4} onChangeText={setNewLast4} keyboardType="number-pad" maxLength={4} style={{ borderWidth: 1, borderColor: theme.color.border, borderRadius: 8, paddingHorizontal: 10, paddingVertical: 10, color: theme.color.cardForeground, minHeight: 44 }} />
              </View>
              <View style={{ width: 100 }}>
                <Text style={{ color: theme.color.mutedForeground, marginBottom: 4 }}>Exp MM</Text>
                <TextInput value={newExpMonth} onChangeText={setNewExpMonth} keyboardType="number-pad" maxLength={2} style={{ borderWidth: 1, borderColor: theme.color.border, borderRadius: 8, paddingHorizontal: 10, paddingVertical: 10, color: theme.color.cardForeground, minHeight: 44 }} />
              </View>
              <View style={{ width: 120 }}>
                <Text style={{ color: theme.color.mutedForeground, marginBottom: 4 }}>Exp YYYY</Text>
                <TextInput value={newExpYear} onChangeText={setNewExpYear} keyboardType="number-pad" maxLength={4} style={{ borderWidth: 1, borderColor: theme.color.border, borderRadius: 8, paddingHorizontal: 10, paddingVertical: 10, color: theme.color.cardForeground, minHeight: 44 }} />
              </View>
            </View>
            <View style={{ flexDirection: 'row', justifyContent: 'flex-end', gap: 8, marginTop: 12 }}>
              <TouchableOpacity onPress={() => setAddOpen(false)} accessibilityRole="button" accessibilityLabel="Cancel add" style={{ paddingHorizontal: 12, paddingVertical: 10, borderRadius: 10, borderWidth: 1, borderColor: theme.color.border }}>
                <Text style={{ color: theme.color.cardForeground }}>Cancel</Text>
              </TouchableOpacity>
              <TouchableOpacity onPress={addMethod} accessibilityRole="button" accessibilityLabel="Save method" style={{ paddingHorizontal: 12, paddingVertical: 10, borderRadius: 10, backgroundColor: theme.color.primary }}>
                <Text style={{ color: theme.color.primaryForeground, fontWeight: '700' }}>Save</Text>
              </TouchableOpacity>
            </View>
          </View>
        </View>
      </Modal>
    </SafeAreaView>
  )
}

export default PaymentMethodsScreen


