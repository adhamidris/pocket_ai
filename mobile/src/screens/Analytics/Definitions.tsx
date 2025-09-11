import React from 'react'
import { SafeAreaView, View, Text, TouchableOpacity, ScrollView } from 'react-native'
import { useSafeAreaInsets } from 'react-native-safe-area-context'
import { useNavigation } from '@react-navigation/native'
import { useTheme } from '../../providers/ThemeProvider'

const items: Array<{ key: string; title: string; desc: string }> = [
  { key: 'frtP50', title: 'FRT P50', desc: '50th percentile of first response latency (agent or AI) within each time bucket.' },
  { key: 'resolutionRate', title: 'Resolution %', desc: 'Resolved conversations divided by total conversations in the period.' },
  { key: 'deflection', title: 'Deflection %', desc: 'Conversations resolved by AI with no agent intervention divided by total.' },
  { key: 'repeatContactRate', title: 'Repeat contact %', desc: 'Users who reach out again within 48 hours divided by unique users.' },
  { key: 'aht', title: 'AHT', desc: 'Average handle time per conversation (active agent time).' },
  { key: 'csat', title: 'CSAT', desc: 'Customer satisfaction score average for the period.' },
]

const caveats = [
  'Data may be delayed up to 5 minutes due to processing.',
  'Timezone defaults to device timezone for UI; backend stored in UTC.',
  'Demo data in this app uses randomized samples and does not represent real performance.',
]

const Definitions: React.FC = () => {
  const { theme } = useTheme()
  const insets = useSafeAreaInsets()
  const navigation = useNavigation<any>()

  return (
    <SafeAreaView style={{ flex: 1, backgroundColor: theme.color.background }}>
      {/* Header */}
      <View style={{ paddingHorizontal: 16, paddingTop: insets.top + 8, paddingBottom: 12, borderBottomWidth: 1, borderBottomColor: theme.color.border }}>
        <View style={{ flexDirection: 'row', alignItems: 'center', justifyContent: 'space-between' }}>
          <TouchableOpacity onPress={() => navigation.goBack()} accessibilityLabel="Back" accessibilityRole="button" style={{ padding: 8 }}>
            <Text style={{ color: theme.color.primary, fontWeight: '600' }}>{'< Back'}</Text>
          </TouchableOpacity>
          <Text style={{ color: theme.color.foreground, fontSize: 18, fontWeight: '700' }}>Definitions & Methodology</Text>
          <View style={{ width: 64 }} />
        </View>
      </View>

      <ScrollView style={{ padding: 16 }} contentContainerStyle={{ paddingBottom: 24 }}>
        <Text style={{ color: theme.color.cardForeground, fontWeight: '700', marginBottom: 8 }}>Metrics</Text>
        <View style={{ gap: 10 }}>
          {items.map((it) => (
            <View key={it.key} style={{ borderWidth: 1, borderColor: theme.color.border, borderRadius: 12, padding: 12 }}>
              <Text style={{ color: theme.color.cardForeground, fontWeight: '700', marginBottom: 4 }}>{it.title}</Text>
              <Text style={{ color: theme.color.mutedForeground }}>{it.desc}</Text>
            </View>
          ))}
        </View>

        <Text style={{ color: theme.color.cardForeground, fontWeight: '700', marginVertical: 12 }}>Notes</Text>
        <View style={{ gap: 6 }}>
          {caveats.map((c, i) => (
            <Text key={i} style={{ color: theme.color.mutedForeground }}>• {c}</Text>
          ))}
        </View>
      </ScrollView>
    </SafeAreaView>
  )
}

export default Definitions


