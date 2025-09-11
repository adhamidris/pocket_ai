import React from 'react'
import { SafeAreaView, View, Text, ScrollView, TouchableOpacity } from 'react-native'
import { useTheme } from '../../providers/ThemeProvider'
import { useSafeAreaInsets } from 'react-native-safe-area-context'
import KpiTile from '../../components/dashboard/KpiTile'
import AlertCard from '../../components/dashboard/AlertCard'
import QuickActions from '../../components/dashboard/QuickActions'
import InsightsTopIntents from '../../components/dashboard/InsightsTopIntents'
import InsightsPeakTimes from '../../components/dashboard/InsightsPeakTimes'
import VolumeByChannelMini from '../../components/dashboard/VolumeByChannelMini'
import ConversationListItem from '../../components/conversations/ConversationListItem'
import ContactRow from '../../components/crm/ContactRow'
import PolicyCard from '../../components/security/PolicyCard'
import ArticleViewer from '../../components/help/ArticleViewer'
import Bubble from '../../components/portal/Bubble'
import EmptyState from '../../components/ui/EmptyState'
import Button from '../../components/ui/Button'

export const ComponentGallery: React.FC = () => {
  const { theme, toggle } = useTheme()
  const insets = useSafeAreaInsets()
  const [scale, setScale] = React.useState<number>(1)

  const kpi = { kind: 'frtP50', label: 'FRT p50', value: 2.4, delta: -0.3 }
  const alert = { id: 'a1', kind: 'slaRisk', title: 'SLA at risk', message: 'High wait for VIP', deeplink: 'app://conversations?filter=slaRisk' }
  const quick = [{ label: 'Urgent', deeplink: 'app://conversations?filter=urgent', testID: 'qa' }]
  const intents = [{ name: 'Billing', sharePct: 22, deflectionPct: 40, trendDelta: -2 }]
  const peak = Array.from({ length: 24 }, (_, hour) => ({ hour, value: Math.round(Math.abs(Math.sin(hour / 3)) * 10) }))
  const byChannel = [ { channel: 'WhatsApp', value: 42 } ]
  const conv = { id: 'c-1', customerName: 'Sarah Johnson', lastMessageSnippet: 'Order status', lastUpdatedTs: Date.now(), channel: 'whatsapp', tags: ['VIP'], assignedTo: 'You', priority: 'high', waitingMinutes: 12, sla: 'risk', lowConfidence: false } as any
  const contact = { id: 'ct-1', name: 'Michael Chen', email: 'm.chen@example.com', channels: ['web'], tags: ['Enterprise'], vip: true, lastInteractionTs: Date.now() } as any
  const policy = { id: 'pol-1', name: 'Session Length', description: 'Max session 12h', enabled: true } as any
  const article = { id: 'a1', kind: 'howto', title: 'Connect a channel', bodyMd: 'Open Channels → WhatsApp', tags: ['Channels'], updatedAt: Date.now() } as any
  const bubbleMsg = { id: 'm1', at: Date.now(), sender: 'customer', parts: [{ kind: 'text', text: 'Hello!' }] } as any

  const Section: React.FC<{ title: string; children: React.ReactNode }> = ({ title, children }) => (
    <View style={{ marginBottom: 20 }}>
      <Text style={{ color: theme.color.mutedForeground, fontWeight: '700', marginBottom: 8 }}>{title}</Text>
      <View style={{ transform: [{ scale }] }}>
        {children}
      </View>
    </View>
  )

  return (
    <SafeAreaView style={{ flex: 1, backgroundColor: theme.color.background }}>
      <ScrollView contentContainerStyle={{ paddingHorizontal: 16, paddingTop: insets.top + 12, paddingBottom: 24 }}>
        <View style={{ flexDirection: 'row', alignItems: 'center', justifyContent: 'space-between', marginBottom: 12 }}>
          <Text style={{ color: theme.color.foreground, fontSize: 24, fontWeight: '700' }}>Component Gallery</Text>
          <View style={{ flexDirection: 'row', gap: 8 }}>
            <TouchableOpacity onPress={() => setScale(s => Math.max(0.75, s - 0.1))} accessibilityRole="button" accessibilityLabel="Smaller" style={{ paddingHorizontal: 10, paddingVertical: 8, borderRadius: 8, borderWidth: 1, borderColor: theme.color.border }}>
              <Text style={{ color: theme.color.cardForeground }}>−</Text>
            </TouchableOpacity>
            <TouchableOpacity onPress={() => setScale(s => Math.min(1.5, s + 0.1))} accessibilityRole="button" accessibilityLabel="Larger" style={{ paddingHorizontal: 10, paddingVertical: 8, borderRadius: 8, borderWidth: 1, borderColor: theme.color.border }}>
              <Text style={{ color: theme.color.cardForeground }}>+</Text>
            </TouchableOpacity>
            <TouchableOpacity onPress={toggle} accessibilityRole="button" accessibilityLabel="Toggle theme" style={{ paddingHorizontal: 10, paddingVertical: 8, borderRadius: 8, borderWidth: 1, borderColor: theme.color.border }}>
              <Text style={{ color: theme.color.cardForeground }}>Theme</Text>
            </TouchableOpacity>
          </View>
        </View>

        <View style={{ flexDirection: 'row', flexWrap: 'wrap', gap: 12 }}>
          <View style={{ flexBasis: '48%', flexGrow: 1 }}>
            <Section title="KPI Tile">
              <KpiTile item={kpi as any} loading={false} testID="qa-kpi" />
            </Section>
          </View>
          <View style={{ flexBasis: '48%', flexGrow: 1 }}>
            <Section title="Alert Card">
              <AlertCard alert={alert as any} onPress={() => {}} testID="qa-alert" />
            </Section>
          </View>
          <View style={{ flexBasis: '48%', flexGrow: 1 }}>
            <Section title="Quick Actions">
              <QuickActions actions={quick as any} onPress={() => {}} testID="qa-quick" />
            </Section>
          </View>
          <View style={{ flexBasis: '48%', flexGrow: 1 }}>
            <Section title="Intents Chart">
              <InsightsTopIntents items={intents as any} loading={false} testID="qa-intents" />
            </Section>
          </View>
          <View style={{ flexBasis: '48%', flexGrow: 1 }}>
            <Section title="Peak Times">
              <InsightsPeakTimes hours={peak as any} loading={false} testID="qa-peak" />
            </Section>
          </View>
          <View style={{ flexBasis: '48%', flexGrow: 1 }}>
            <Section title="Volume by Channel">
              <VolumeByChannelMini items={byChannel as any} testID="qa-channel" />
            </Section>
          </View>
          <View style={{ flexBasis: '48%', flexGrow: 1 }}>
            <Section title="Conversation Row">
              <ConversationListItem item={conv} onPress={() => {}} onAssignToAgent={() => {}} testID="qa-conv" />
            </Section>
          </View>
          <View style={{ flexBasis: '48%', flexGrow: 1 }}>
            <Section title="Contact Row">
              <ContactRow item={contact} onPress={() => {}} testID="qa-contact" />
            </Section>
          </View>
          <View style={{ flexBasis: '48%', flexGrow: 1 }}>
            <Section title="Policy Card">
              <PolicyCard policy={policy} />
            </Section>
          </View>
          <View style={{ flexBasis: '48%', flexGrow: 1 }}>
            <Section title="Portal Bubble">
              <Bubble message={bubbleMsg} />
            </Section>
          </View>
          <View style={{ flexBasis: '48%', flexGrow: 1 }}>
            <Section title="Help Article">
              <ArticleViewer article={article} />
            </Section>
          </View>
          <View style={{ flexBasis: '48%', flexGrow: 1 }}>
            <Section title="UI Primitives">
              <View style={{ gap: 8 }}>
                <Button label="Primary" onPress={() => {}} />
                <EmptyState message="Empty state sample" />
              </View>
            </Section>
          </View>
        </View>
      </ScrollView>
    </SafeAreaView>
  )
}

export default ComponentGallery


