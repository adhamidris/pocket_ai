import React from 'react'
import { View, Text, TouchableOpacity } from 'react-native'
import { tokens } from '../../ui/tokens'
import { Invoice } from '../../types/billing'

export interface InvoiceRowProps { inv: Invoice; onOpen: () => void; testID?: string }

const formatCurrency = (amount: number, currency: string) => {
  try {
    return new Intl.NumberFormat(undefined, { style: 'currency', currency }).format(amount / 100)
  } catch {
    return `$${(amount / 100).toFixed(2)}`
  }
}

const InvoiceRow: React.FC<InvoiceRowProps> = ({ inv, onOpen, testID }) => {
  const created = new Date(inv.created * 1000).toLocaleDateString()
  return (
    <TouchableOpacity onPress={onOpen} accessibilityRole="button" accessibilityLabel={`Open invoice ${inv.number}`} testID={testID} style={{ paddingVertical: 10, flexDirection: 'row', alignItems: 'center', justifyContent: 'space-between' }}>
      <View style={{ gap: 2 }}>
        <Text style={{ color: tokens.colors.cardForeground, fontWeight: '700' }}>#{inv.number}</Text>
        <Text style={{ color: tokens.colors.mutedForeground }}>{created}</Text>
      </View>
      <View style={{ alignItems: 'flex-end' }}>
        <Text style={{ color: tokens.colors.cardForeground }}>{formatCurrency(inv.amountDue, inv.currency)}</Text>
        <Text style={{ color: inv.status === 'paid' ? tokens.colors.success : inv.status === 'open' ? tokens.colors.warning : tokens.colors.mutedForeground, fontWeight: '700' }}>{inv.status.toUpperCase()}</Text>
      </View>
    </TouchableOpacity>
  )
}

export default React.memo(InvoiceRow)


