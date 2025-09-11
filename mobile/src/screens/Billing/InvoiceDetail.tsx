import React from 'react'
import { SafeAreaView, View, Text, TouchableOpacity, Alert, ScrollView } from 'react-native'
import { useTheme } from '../../providers/ThemeProvider'
import { useRoute } from '@react-navigation/native'
import { Invoice, TaxInfo } from '../../types/billing'
import { track } from '../../lib/analytics'

type Params = { inv: Invoice; tax?: TaxInfo }

const formatCurrency = (amount: number, currency: string) => {
  try {
    return new Intl.NumberFormat(undefined, { style: 'currency', currency }).format(amount / 100)
  } catch {
    return `$${(amount / 100).toFixed(2)}`
  }
}

const InvoiceDetail: React.FC = () => {
  const { theme } = useTheme()
  const route = useRoute<any>()
  const { inv, tax } = route.params as Params
  const lineItems = [
    { id: 'li_1', desc: 'Subscription (Pro, monthly)', qty: 1, unit: inv.amountDue, total: inv.amountDue }
  ]

  const openPdf = () => { try { track('billing.invoice.open_pdf', { id: inv.id }) } catch {}; Alert.alert('Open PDF', 'Opening invoice PDF...') }

  return (
    <SafeAreaView style={{ flex: 1, backgroundColor: theme.color.background }}>
      <ScrollView>
        <View style={{ paddingHorizontal: 24, paddingVertical: 12 }}>
          <View style={{ flexDirection: 'row', alignItems: 'center', justifyContent: 'space-between', marginBottom: 8 }}>
            <Text style={{ color: theme.color.foreground, fontSize: 24, fontWeight: '700' }}>Invoice #{inv.number}</Text>
            <TouchableOpacity onPress={openPdf} accessibilityRole="button" accessibilityLabel="Open PDF" style={{ paddingHorizontal: 12, paddingVertical: 8, borderRadius: 10, borderWidth: 1, borderColor: theme.color.border }}>
              <Text style={{ color: theme.color.cardForeground, fontWeight: '700' }}>Open PDF</Text>
            </TouchableOpacity>
          </View>
          <Text style={{ color: inv.status === 'paid' ? theme.color.success : inv.status === 'open' ? theme.color.warning : theme.color.mutedForeground, fontWeight: '700', marginBottom: 12 }}>{inv.status.toUpperCase()}</Text>

          <View style={{ borderWidth: 1, borderColor: theme.color.border, borderRadius: 12, padding: 12, marginBottom: 12 }}>
            <Text style={{ color: theme.color.cardForeground, fontWeight: '700', marginBottom: 8 }}>Line items</Text>
            {lineItems.map((li) => (
              <View key={li.id} style={{ flexDirection: 'row', alignItems: 'center', justifyContent: 'space-between', marginBottom: 6 }}>
                <View>
                  <Text style={{ color: theme.color.cardForeground }}>{li.desc}</Text>
                  <Text style={{ color: theme.color.mutedForeground }}>Qty {li.qty}</Text>
                </View>
                <Text style={{ color: theme.color.cardForeground }}>{formatCurrency(li.total, inv.currency)}</Text>
              </View>
            ))}
            <View style={{ height: 1, backgroundColor: theme.color.border, marginVertical: 8 }} />
            <View style={{ flexDirection: 'row', alignItems: 'center', justifyContent: 'space-between' }}>
              <Text style={{ color: theme.color.cardForeground, fontWeight: '700' }}>Total</Text>
              <Text style={{ color: theme.color.cardForeground, fontWeight: '700' }}>{formatCurrency(inv.amountDue, inv.currency)}</Text>
            </View>
          </View>

          <View style={{ borderWidth: 1, borderColor: theme.color.border, borderRadius: 12, padding: 12 }}>
            <Text style={{ color: theme.color.cardForeground, fontWeight: '700', marginBottom: 8 }}>Billing info</Text>
            {tax ? (
              <View>
                <Text style={{ color: theme.color.cardForeground, fontWeight: '700' }}>{tax.invoiceName || '—'}</Text>
                <Text style={{ color: theme.color.mutedForeground }}>{tax.address1 || '—'}</Text>
                <Text style={{ color: theme.color.mutedForeground }}>{tax.city || '—'} {tax.zip || ''}</Text>
                <Text style={{ color: theme.color.mutedForeground }}>Country: {tax.country || '—'}{tax.vatId ? ` • VAT: ${tax.vatId}` : ''}</Text>
              </View>
            ) : (
              <Text style={{ color: theme.color.mutedForeground }}>No billing profile set.</Text>
            )}
          </View>
        </View>
      </ScrollView>
    </SafeAreaView>
  )
}

export default InvoiceDetail


