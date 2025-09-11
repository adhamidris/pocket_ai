import React from 'react'
import { SafeAreaView, View, Text, TouchableOpacity } from 'react-native'
import { useTheme } from '../../providers/ThemeProvider'
import { useSafeAreaInsets } from 'react-native-safe-area-context'
import { useNavigation } from '@react-navigation/native'
import ConvSkeleton from '../../components/conversations/ListSkeleton'
import ConvEmpty from '../../components/conversations/EmptyState'
import CrmSkeleton from '../../components/crm/ListSkeleton'
import CrmEmpty from '../../components/crm/EmptyState'
import ErrorBanner from '../../components/dashboard/ErrorBanner'
import OfflineBanner from '../../components/dashboard/OfflineBanner'
import { EmptyStateGuide } from '../../components/help/EmptyStateGuide'

type Mode = 'loading' | 'empty' | 'error'

const Section: React.FC<{ title: string; children: React.ReactNode }> = ({ title, children }) => {
  const { theme } = useTheme()
  return (
    <View style={{ borderWidth: 1, borderColor: theme.color.border, borderRadius: 12, padding: 12, marginBottom: 16 }}>
      <Text style={{ color: theme.color.mutedForeground, fontWeight: '700', marginBottom: 8 }}>{title}</Text>
      {children}
    </View>
  )
}

const ModeSwitch: React.FC<{ mode: Mode; setMode: (m: Mode) => void }> = ({ mode, setMode }) => {
  const { theme } = useTheme()
  const Btn: React.FC<{ m: Mode }> = ({ m }) => (
    <TouchableOpacity onPress={() => setMode(m)} accessibilityRole="button" accessibilityLabel={m} style={{ paddingHorizontal: 10, paddingVertical: 6, borderRadius: 10, borderWidth: 1, borderColor: mode === m ? theme.color.primary : theme.color.border, backgroundColor: mode === m ? theme.color.primary + '22' : 'transparent' }}>
      <Text style={{ color: theme.color.cardForeground, fontWeight: '700' }}>{m}</Text>
    </TouchableOpacity>
  )
  return (
    <View style={{ flexDirection: 'row', gap: 8 }}>
      <Btn m="loading" />
      <Btn m="empty" />
      <Btn m="error" />
    </View>
  )
}

export const StateCoverageAudit: React.FC = () => {
  const { theme } = useTheme()
  const insets = useSafeAreaInsets()
  const navigation = useNavigation<any>()
  const [mode, setMode] = React.useState<Mode>('loading')

  const OpenBtn: React.FC<{ to: string; label: string; params?: any }> = ({ to, label, params }) => (
    <TouchableOpacity onPress={() => navigation.navigate('Main', { screen: to, params })} accessibilityRole="button" accessibilityLabel={`Open ${label}`} style={{ paddingHorizontal: 10, paddingVertical: 8, borderRadius: 8, borderWidth: 1, borderColor: theme.color.border }}>
      <Text style={{ color: theme.color.cardForeground, fontWeight: '700' }}>{label}</Text>
    </TouchableOpacity>
  )

  return (
    <SafeAreaView style={{ flex: 1, backgroundColor: theme.color.background }}>
      <View style={{ paddingHorizontal: 16, paddingTop: insets.top + 12, paddingBottom: 12, flexDirection: 'row', alignItems: 'center', justifyContent: 'space-between' }}>
        <Text style={{ color: theme.color.foreground, fontSize: 22, fontWeight: '700' }}>State Coverage Audit</Text>
        <ModeSwitch mode={mode} setMode={setMode} />
      </View>

      <View style={{ paddingHorizontal: 16 }}>
        <Section title="Conversations">
          {mode === 'loading' && <ConvSkeleton rows={8} testID="conv-skeleton" />}
          {mode === 'empty' && <ConvEmpty message="No conversations match your filters." testID="conv-empty" />}
          {mode === 'error' && <ErrorBanner message="Simulated error — demo only" testID="conv-error" />}
          <View style={{ flexDirection: 'row', gap: 8, marginTop: 8 }}>
            <OpenBtn to="Conversations" label="Open Conversations" />
            <OpenBtn to="Conversations" label="Open Urgent" params={{ screen: 'ConversationsHome', params: { filter: 'urgent' } }} />
          </View>
        </Section>

        <Section title="CRM">
          {mode === 'loading' && <CrmSkeleton rows={12} testID="crm-skeleton" />}
          {mode === 'empty' && <CrmEmpty message="No contacts match your filters." testID="crm-empty" />}
          {mode === 'error' && <ErrorBanner message="Simulated error — demo only" testID="crm-error" />}
          <View style={{ flexDirection: 'row', gap: 8, marginTop: 8 }}>
            <OpenBtn to="CRM" label="Open CRM" />
          </View>
        </Section>

        <Section title="Analytics">
          {mode === 'loading' && <EmptyStateGuide title="Generating analytics…" lines={["Calculating KPIs", "Rendering charts"]} cta={{ label: 'Open Analytics', onPress: () => navigation.navigate('Main', { screen: 'Analytics' }) }} />}
          {mode === 'empty' && <EmptyStateGuide title="Not enough data" lines={["Collect more traffic", "Try a different range"]} cta={{ label: 'Open Analytics', onPress: () => navigation.navigate('Main', { screen: 'Analytics' }) }} />}
          {mode === 'error' && <ErrorBanner message="Simulated error — demo only" testID="an-error" />}
        </Section>

        <Section title="Dashboard & Settings banners">
          {mode !== 'error' ? <OfflineBanner visible testID="offline-demo" /> : <ErrorBanner message="Simulated error — demo only" testID="dash-error" />}
          <View style={{ flexDirection: 'row', gap: 8, marginTop: 8 }}>
            <OpenBtn to="Dashboard" label="Open Dashboard" />
            <OpenBtn to="Settings" label="Open Settings" />
          </View>
        </Section>
      </View>
    </SafeAreaView>
  )
}

export default StateCoverageAudit

