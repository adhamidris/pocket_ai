import React from 'react'
import { SafeAreaView, View, Text, TouchableOpacity, TextInput, FlatList, Alert } from 'react-native'
import { useTheme } from '../../providers/ThemeProvider'
import { Invoice } from '../../types/billing'
import InvoiceRow from '../../components/billing/InvoiceRow'
import { useNavigation, useRoute } from '@react-navigation/native'
import { track } from '../../lib/analytics'

type Params = { invoices?: Invoice[] }

const sampleInvoices: Invoice[] = Array.from({ length: 30 }).map((_, i) => ({
  id: `inv_${i}`,
  number: `000${i + 1}`,
  amountDue: 4900 + (i % 5) * 500,
  currency: 'USD',
  created: Math.floor(Date.now() / 1000) - i * 15 * 86400,
  status: i % 7 === 0 ? 'void' : (i % 6 === 0 ? 'uncollectible' : (i % 4 === 0 ? 'open' : 'paid'))
}))

const statusOptions: Array<Invoice['status'] | 'all'> = ['all', 'paid', 'open', 'void', 'uncollectible']

const InvoicesScreen: React.FC = () => {
  const { theme } = useTheme()
  const navigation = useNavigation<any>()
  const route = useRoute<any>()
  const initial = (route.params as Params | undefined)?.invoices || sampleInvoices

  const [status, setStatus] = React.useState<Invoice['status'] | 'all'>('all')
  const [dateRange, setDateRange] = React.useState<'30d' | '90d' | 'all'>('30d')
  const [minAmount, setMinAmount] = React.useState<string>('')
  const [maxAmount, setMaxAmount] = React.useState<string>('')

  const filtered = React.useMemo(() => {
    const now = Math.floor(Date.now() / 1000)
    const start = dateRange === '30d' ? now - 30 * 86400 : dateRange === '90d' ? now - 90 * 86400 : 0
    const minA = minAmount ? parseInt(minAmount, 10) : undefined
    const maxA = maxAmount ? parseInt(maxAmount, 10) : undefined
    return initial.filter((inv) => {
      if (status !== 'all' && inv.status !== status) return false
      if (start && inv.created < start) return false
      if (minA != null && inv.amountDue < minA) return false
      if (maxA != null && inv.amountDue > maxA) return false
      return true
    })
  }, [initial, status, dateRange, minAmount, maxAmount])

  const openDetail = (inv: Invoice) => { try { track('billing.invoice.open', { id: inv.id }) } catch {}; navigation.navigate('InvoiceDetail', { inv }) }
  const downloadAll = () => { try { track('billing.invoices.download_all') } catch {}; Alert.alert('Download', 'Downloading all invoices...') }

  const StatusChip: React.FC<{ value: typeof status; onPress: () => void; active: boolean }>=({ value, onPress, active }) => (
    <TouchableOpacity onPress={onPress} accessibilityRole="button" accessibilityLabel={`Filter ${value}`} style={{ paddingHorizontal: 10, paddingVertical: 8, borderRadius: 999, borderWidth: 1, borderColor: active ? theme.color.primary : theme.color.border, backgroundColor: active ? theme.color.primary : theme.color.card }}>
      <Text style={{ color: active ? theme.color.primaryForeground : theme.color.cardForeground, fontWeight: '700' }}>{String(value).toUpperCase()}</Text>
    </TouchableOpacity>
  )

  const RangeChip: React.FC<{ label: string; active: boolean; onPress: () => void }>=({ label, active, onPress }) => (
    <TouchableOpacity onPress={onPress} accessibilityRole="button" accessibilityLabel={`Range ${label}`} style={{ paddingHorizontal: 10, paddingVertical: 8, borderRadius: 999, borderWidth: 1, borderColor: active ? theme.color.primary : theme.color.border, backgroundColor: active ? theme.color.primary : theme.color.card }}>
      <Text style={{ color: active ? theme.color.primaryForeground : theme.color.cardForeground, fontWeight: '700' }}>{label}</Text>
    </TouchableOpacity>
  )

  return (
    <SafeAreaView style={{ flex: 1, backgroundColor: theme.color.background }}>
      <View style={{ flexDirection: 'row', alignItems: 'center', justifyContent: 'space-between', paddingHorizontal: 24, paddingVertical: 12 }}>
        <Text style={{ color: theme.color.foreground, fontSize: 24, fontWeight: '700' }}>Invoices</Text>
        <TouchableOpacity onPress={downloadAll} accessibilityRole="button" accessibilityLabel="Download all" style={{ paddingHorizontal: 12, paddingVertical: 8, borderRadius: 10, borderWidth: 1, borderColor: theme.color.border }}>
          <Text style={{ color: theme.color.cardForeground, fontWeight: '700' }}>Download all</Text>
        </TouchableOpacity>
      </View>

      <View style={{ paddingHorizontal: 24, gap: 8 }}>
        <View style={{ flexDirection: 'row', flexWrap: 'wrap', gap: 8 }}>
          {statusOptions.map((opt) => (
            <StatusChip key={opt} value={opt} active={status === opt} onPress={() => setStatus(opt)} />
          ))}
        </View>
        <View style={{ flexDirection: 'row', flexWrap: 'wrap', gap: 8 }}>
          <RangeChip label="Last 30d" active={dateRange === '30d'} onPress={() => setDateRange('30d')} />
          <RangeChip label="Last 90d" active={dateRange === '90d'} onPress={() => setDateRange('90d')} />
          <RangeChip label="All time" active={dateRange === 'all'} onPress={() => setDateRange('all')} />
        </View>
        <View style={{ flexDirection: 'row', gap: 8, marginBottom: 8 }}>
          <View style={{ flex: 1 }}>
            <Text style={{ color: theme.color.mutedForeground, marginBottom: 4 }}>Min amount (cents)</Text>
            <TextInput value={minAmount} onChangeText={setMinAmount} keyboardType="number-pad" placeholder="0" placeholderTextColor={theme.color.mutedForeground} style={{ borderWidth: 1, borderColor: theme.color.border, borderRadius: 8, paddingHorizontal: 10, paddingVertical: 10, color: theme.color.cardForeground }} />
          </View>
          <View style={{ flex: 1 }}>
            <Text style={{ color: theme.color.mutedForeground, marginBottom: 4 }}>Max amount (cents)</Text>
            <TextInput value={maxAmount} onChangeText={setMaxAmount} keyboardType="number-pad" placeholder="999999" placeholderTextColor={theme.color.mutedForeground} style={{ borderWidth: 1, borderColor: theme.color.border, borderRadius: 8, paddingHorizontal: 10, paddingVertical: 10, color: theme.color.cardForeground }} />
          </View>
        </View>
      </View>

      <View style={{ flex: 1, paddingHorizontal: 12, marginTop: 8 }}>
        <FlatList
          data={filtered}
          keyExtractor={(item) => item.id}
          renderItem={({ item }) => (
            <View style={{ marginHorizontal: 12, borderBottomWidth: 1, borderBottomColor: theme.color.border }}>
              <InvoiceRow inv={item} onOpen={() => openDetail(item)} />
            </View>
          )}
          initialNumToRender={12}
          windowSize={10}
          getItemLayout={(data, index) => ({ length: 64, offset: 64 * index, index })}
          removeClippedSubviews
        />
      </View>
    </SafeAreaView>
  )
}

export default InvoicesScreen


