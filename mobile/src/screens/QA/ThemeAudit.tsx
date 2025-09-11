import React from 'react'
import { SafeAreaView, View, Text, TouchableOpacity, Switch } from 'react-native'
import { useTheme } from '../../providers/ThemeProvider'
import { useSafeAreaInsets } from 'react-native-safe-area-context'
import KpiTile from '../../components/dashboard/KpiTile'
import QuickActions from '../../components/dashboard/QuickActions'
import VolumeByChannelMini from '../../components/dashboard/VolumeByChannelMini'
import Bubble from '../../components/portal/Bubble'
import Coachmark from '../../components/help/Coachmark'
import ConversationListItem from '../../components/conversations/ConversationListItem'

type Preset = 'neutral' | 'bold' | 'soft' | 'brandA' | 'brandB'

export const ThemeAudit: React.FC = () => {
  const { theme } = useTheme()
  const insets = useSafeAreaInsets()
  const [hc, setHc] = React.useState(false)

  const apply = (preset: Preset) => {
    try {
      const { DeviceEventEmitter } = require('react-native')
      DeviceEventEmitter.emit('theme.apply', { preset })
    } catch {}
  }

  const Overlay = () => hc ? (
    <View pointerEvents="none" style={{ position: 'absolute', left: 0, right: 0, top: 0, bottom: 0, backgroundColor: 'transparent', borderColor: '#000', borderWidth: 0 }}>
      <View style={{ position: 'absolute', right: 12, top: 12, backgroundColor: '#000', paddingHorizontal: 8, paddingVertical: 4, borderRadius: 8 }}>
        <Text style={{ color: '#fff', fontWeight: '800' }}>High Contrast</Text>
      </View>
    </View>
  ) : null

  const kpi = { kind: 'frtP50', label: 'FRT p50', value: 2.4, delta: -0.3 } as any
  const actions = [{ label: 'Urgent', deeplink: 'app://conversations?filter=urgent', testID: 'qa' }] as any
  const byChannel = [{ channel: 'WhatsApp', value: 42 }] as any
  const msg = { id: 'm1', at: Date.now(), sender: 'ai', parts: [{ kind: 'text', text: 'Hello from portal' }] } as any
  const coach = { id: 'hint1', title: 'Tip', body: 'This is a coachmark', anchorTestID: 'kpi-frt' } as any
  const conv = { id: 'c-1', customerName: 'Sarah Johnson', lastMessageSnippet: 'Order status', lastUpdatedTs: Date.now(), channel: 'whatsapp', tags: ['VIP'], assignedTo: 'You', priority: 'high', waitingMinutes: 12, sla: 'risk', lowConfidence: false } as any

  return (
    <SafeAreaView style={{ flex: 1, backgroundColor: theme.color.background }}>
      <View style={{ paddingHorizontal: 16, paddingTop: insets.top + 12, paddingBottom: 12 }}>
        <Text style={{ color: theme.color.foreground, fontSize: 22, fontWeight: '700', marginBottom: 8 }}>Theme Audit</Text>
        <View style={{ flexDirection: 'row', flexWrap: 'wrap', gap: 8, alignItems: 'center' }}>
          {(['neutral', 'bold', 'soft', 'brandA', 'brandB'] as Preset[]).map(p => (
            <TouchableOpacity key={p} onPress={() => apply(p)} accessibilityRole="button" accessibilityLabel={`Apply ${p}`} style={{ paddingHorizontal: 10, paddingVertical: 8, borderRadius: 10, borderWidth: 1, borderColor: theme.color.border }}>
              <Text style={{ color: theme.color.cardForeground, fontWeight: '700' }}>{p}</Text>
            </TouchableOpacity>
          ))}
          <View style={{ flexDirection: 'row', alignItems: 'center', marginLeft: 8 }}>
            <Text style={{ color: theme.color.cardForeground, marginRight: 6 }}>High‑contrast</Text>
            <Switch value={hc} onValueChange={setHc} />
          </View>
        </View>
      </View>

      <View style={{ paddingHorizontal: 16, paddingBottom: 24 }}>
        <View style={{ flexDirection: 'row', flexWrap: 'wrap', gap: 12 }}>
          <View style={{ flexBasis: '48%', flexGrow: 1 }}>
            <KpiTile item={kpi} loading={false} testID="kpi-frt" />
          </View>
          <View style={{ flexBasis: '48%', flexGrow: 1 }}>
            <QuickActions actions={actions} onPress={() => {}} />
          </View>
          <View style={{ flexBasis: '48%', flexGrow: 1 }}>
            <VolumeByChannelMini items={byChannel} />
          </View>
          <View style={{ flexBasis: '48%', flexGrow: 1 }}>
            <Bubble message={msg} />
          </View>
          <View style={{ flexBasis: '48%', flexGrow: 1 }}>
            <ConversationListItem item={conv} onPress={() => {}} onAssignToAgent={() => {}} />
          </View>
        </View>
      </View>

      <Overlay />
      <Coachmark visible anchorTestID={coach.anchorTestID} title={coach.title} body={coach.body} onClose={() => {}} />
    </SafeAreaView>
  )
}

export default ThemeAudit


