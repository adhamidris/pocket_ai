import React from 'react'
import { View, Text, SafeAreaView, ScrollView, TouchableOpacity } from 'react-native'
import { useTranslation } from 'react-i18next'
import { useNavigation } from '@react-navigation/native'
import { useTheme } from '../../providers/ThemeProvider'
import { useSafeAreaInsets } from 'react-native-safe-area-context'
import { Sun, Moon, MoreHorizontal, HelpCircle } from 'lucide-react-native'
import KpiTile from '../../components/dashboard/KpiTile'
import { Anchor } from '../../components/help/Anchor'
import AlertCard from '../../components/dashboard/AlertCard'
import QuickActions from '../../components/dashboard/QuickActions'
import SetupProgressCard from '../../components/dashboard/SetupProgressCard'
import InsightsTopIntents from '../../components/dashboard/InsightsTopIntents'
import InsightsPeakTimes from '../../components/dashboard/InsightsPeakTimes'
import VolumeByChannelMini from '../../components/dashboard/VolumeByChannelMini'
import IndustryTiles from '../../components/dashboard/IndustryTiles'
import { DashboardKpi, AlertItem, SetupStep, IntentItem, PeakHour, IndustryPackId } from '../../types/dashboard'
import { getFlags, selectKpis, selectAlerts, selectQuickActions, selectIndustryPack } from './personalizer'
import ErrorBanner from '../../components/dashboard/ErrorBanner'
import EmptyState from '../../components/dashboard/EmptyState'
import OfflineBanner from '../../components/dashboard/OfflineBanner'
import SyncCenterSheet from '../../components/dashboard/SyncCenterSheet'
import { track } from '../../lib/analytics'
import OnboardingChecklist from '../Help/OnboardingChecklist'
import AskDashboard from '../../components/dashboard/AskDashboard'
import DashboardAskPanel from '../Assistant/DashboardAskPanel'

export const DashboardScreen: React.FC = () => {
  const { t } = useTranslation()
  const { theme, toggle } = useTheme()
  const navigation = useNavigation<any>()
  const insets = useSafeAreaInsets()
  // Demo data for scaffolding
  const [kpis, setKpis] = React.useState<DashboardKpi[]>([])
  const [alerts, setAlerts] = React.useState<AlertItem[]>([])
  const [actions, setActions] = React.useState<{ label: string; deeplink: string; testID: string }[]>([])

  const steps: SetupStep[] = [
    { id: 's1', title: 'Connect Channels', status: 'todo', deeplink: 'app://channels' },
    { id: 's2', title: 'Publish Widget/Link', status: 'todo', deeplink: 'app://channels/publish' },
    { id: 's3', title: 'Train from Uploads', status: 'done', deeplink: 'app://knowledge' },
  ]

  const intents: IntentItem[] = [
    { name: 'Order status', sharePct: 34, deflectionPct: 68, trendDelta: 5 },
    { name: 'Billing', sharePct: 22, deflectionPct: 40, trendDelta: -2 },
    { name: 'Product availability', sharePct: 12, trendDelta: 1 },
  ]

  const peakHours: PeakHour[] = Array.from({ length: 24 }, (_, hour) => ({ hour, value: Math.round(Math.abs(Math.sin(hour / 3)) * 10) + (hour % 6 === 0 ? 8 : 0) }))

  const byChannel = [
    { channel: 'WhatsApp', value: 42 },
    { channel: 'Instagram', value: 28 },
    { channel: 'Facebook', value: 16 },
    { channel: 'Web', value: 35 },
  ]

  const [pack, setPack] = React.useState<IndustryPackId>('neutral')
  const loadingKpis = false
  const loadingIntents = false
  const [loadingPeak, setLoadingPeak] = React.useState(false)
  const [showError, setShowError] = React.useState(false)
  const [offline, setOffline] = React.useState(false)
  const [syncOpen, setSyncOpen] = React.useState(false)

  const handleActionPress = (deeplink: string) => {
    // Very light deeplink parser for demo → navigate with params
    if (deeplink.startsWith('app://conversations')) {
      const filter = deeplink.split('filter=')[1] || 'all'
      navigation.navigate('Conversations', { filter })
    }
    if (deeplink.startsWith('app://channels')) {
      navigation.navigate('Channels')
    }
    // infer kind from deeplink for analytics
    if (deeplink.includes('urgent')) track('quick_action.used', { kind: 'urgent' })
    else if (deeplink.includes('waiting30')) track('quick_action.used', { kind: 'waiting30' })
    else if (deeplink.includes('unassigned')) track('quick_action.used', { kind: 'unassigned' })
    else if (deeplink.includes('slaRisk')) track('quick_action.used', { kind: 'slaRisk' })
  }

  React.useEffect(() => {
    (async () => {
      const flags = await getFlags()
      setKpis(selectKpis(flags))
      setAlerts(selectAlerts(flags))
      setActions(selectQuickActions(flags))
      setPack(selectIndustryPack(flags))
    })()
  }, [])

  React.useEffect(() => {
    track('dashboard.view')
  }, [])

  React.useEffect(() => {
    if (kpis.length > 0) {
      track('dashboard.kpi_viewed', { kpis: kpis.map(k => k.kind) })
    }
  }, [kpis])

  return (
    <SafeAreaView style={{ flex: 1, backgroundColor: theme.color.background }}>
      <ScrollView style={{ flex: 1 }}>
        {/* Header */}
        <View style={{ paddingHorizontal: 24, paddingTop: insets.top + 12, paddingBottom: 16 }}>
          <View style={{ flexDirection: 'row', alignItems: 'center', justifyContent: 'space-between' }}>
            <Text style={{
              color: theme.color.foreground,
              fontSize: 32,
              fontWeight: '700',
              marginBottom: 8
            }}>
              {t('dashboard.title')}
            </Text>
            <TouchableOpacity
              onPress={toggle}
              activeOpacity={0.85}
              style={{ padding: 8, borderRadius: 16, minHeight: 44, minWidth: 44, alignItems: 'center', justifyContent: 'center' }}
              hitSlop={{ top: 8, bottom: 8, left: 8, right: 8 }}
              accessibilityLabel="Toggle theme"
              accessibilityRole="button"
            >
              {theme.dark ? <Sun size={18} color={theme.color.cardForeground as any} /> : <Moon size={18} color={theme.color.cardForeground as any} />}
            </TouchableOpacity>
            {__DEV__ ? (
              <TouchableOpacity
                onPress={() => setSyncOpen(true)}
                activeOpacity={0.85}
                style={{ padding: 8, borderRadius: 16, minHeight: 44, minWidth: 44, alignItems: 'center', justifyContent: 'center' }}
                hitSlop={{ top: 8, bottom: 8, left: 8, right: 8 }}
                accessibilityLabel="Open Sync Center"
                accessibilityRole="button"
              >
                <MoreHorizontal size={18} color={theme.color.cardForeground as any} />
              </TouchableOpacity>
            ) : null}
            <TouchableOpacity
              onPress={() => navigation.navigate('HelpCenter', { initialTab: 'search', initialTag: 'Dashboard' })}
              activeOpacity={0.85}
              style={{ padding: 8, borderRadius: 16, minHeight: 44, minWidth: 44, alignItems: 'center', justifyContent: 'center' }}
              hitSlop={{ top: 8, bottom: 8, left: 8, right: 8 }}
              accessibilityLabel="Open help"
              accessibilityRole="button"
            >
              <HelpCircle size={18} color={theme.color.cardForeground as any} />
            </TouchableOpacity>
          </View>
          <Text style={{
            color: theme.color.mutedForeground,
            fontSize: 16
          }}>
            {t('dashboard.welcome')} 👋
          </Text>
        </View>
        {/* Dev toggles (only in dev) */}
        {__DEV__ && (
          <View style={{ paddingHorizontal: 24, marginBottom: 8, flexDirection: 'row', gap: 12 }}>
            <TouchableOpacity onPress={() => setShowError((v) => !v)} accessibilityLabel="Toggle error banner" accessibilityRole="button" style={{ paddingHorizontal: 12, paddingVertical: 8, borderRadius: 8, backgroundColor: theme.color.card, borderWidth: 1, borderColor: theme.color.border, minHeight: 44, justifyContent: 'center' }}>
              <Text style={{ color: theme.color.cardForeground, fontWeight: '600' }}>{showError ? 'Hide Error' : 'Show Error'}</Text>
            </TouchableOpacity>
            <TouchableOpacity onPress={() => setLoadingPeak((v) => !v)} accessibilityLabel="Toggle loading for Peak Times" accessibilityRole="button" style={{ paddingHorizontal: 12, paddingVertical: 8, borderRadius: 8, backgroundColor: theme.color.card, borderWidth: 1, borderColor: theme.color.border, minHeight: 44, justifyContent: 'center' }}>
              <Text style={{ color: theme.color.cardForeground, fontWeight: '600' }}>{loadingPeak ? 'Stop Loading' : 'Load PeakTimes'}</Text>
            </TouchableOpacity>
            <TouchableOpacity onPress={() => setOffline((v) => !v)} accessibilityLabel="Toggle offline" accessibilityRole="button" style={{ paddingHorizontal: 12, paddingVertical: 8, borderRadius: 8, backgroundColor: theme.color.card, borderWidth: 1, borderColor: theme.color.border, minHeight: 44, justifyContent: 'center' }}>
              <Text style={{ color: theme.color.cardForeground, fontWeight: '600' }}>{offline ? 'Go Online' : 'Go Offline'}</Text>
            </TouchableOpacity>
          </View>
        )}

        {/* Error banner */}
        {showError && (
          <View style={{ paddingHorizontal: 24, marginBottom: 12 }}>
            <ErrorBanner message="Simulated error — demo only" testID="err-banner" />
          </View>
        )}

        {/* Offline banner */}
        {offline && (
          <View style={{ paddingHorizontal: 24, marginBottom: 12 }}>
            <OfflineBanner visible={offline} testID="offline-banner" />
          </View>
        )}

        {/* Onboarding Checklist (global) */}
        <View style={{ paddingHorizontal: 24, marginBottom: 16 }}>
          <OnboardingChecklist />
        </View>

        {/* KPIs */}
        <View style={{ paddingHorizontal: 24, marginBottom: 20, flexDirection: 'row', flexWrap: 'wrap', gap: 12 }}>
          {kpis.map((kpi) => (
            <Anchor key={kpi.kind} testID={kpi.kind === 'frtP50' ? 'kpi-frt' : `kpi-${kpi.kind}`}>
              <View style={{ flexBasis: '47%', flexGrow: 1 }}>
                <KpiTile item={kpi} loading={loadingKpis} testID={`kpi-${kpi.kind}`} />
              </View>
            </Anchor>
          ))}
        </View>

        {/* Alerts */}
        <View style={{ paddingHorizontal: 24, marginBottom: 24 }}>
          <ScrollView horizontal showsHorizontalScrollIndicator={false}>
            <View style={{ flexDirection: 'row' }}>
              {alerts.map((a) => (
                <AlertCard key={a.id} alert={a} testID={`alert-${a.kind}`} onPress={(deeplink) => {
                  if (a.kind === 'slaRisk') {
                    // Open Automations -> SlaEditor
                    // @ts-ignore
                    navigation.navigate('Automations', { screen: 'SlaEditor', params: { policy: { id: 'sla-1', name: 'Default SLA', pauseOutsideHours: true, targets: [ { priority: 'vip', frtP50Sec: 60, frtP90Sec: 180 }, { priority: 'high', frtP50Sec: 120, frtP90Sec: 300 }, { priority: 'normal', frtP50Sec: 300, frtP90Sec: 1200 } ] }, onApply: () => {} } })
                  } else {
                    handleActionPress(deeplink)
                  }
                }} />
              ))}
            </View>
          </ScrollView>
        </View>

        {/* Ask the Assistant (new compact panel) */}
        <View style={{ paddingHorizontal: 24, marginBottom: 24 }}>
          <DashboardAskPanel testID="dashboard-ask-panel" />
        </View>

        {/* Quick Actions */}
        <View style={{ paddingHorizontal: 24, marginBottom: 24 }}>
          <Anchor testID="quick-actions">
            <QuickActions actions={actions} onPress={handleActionPress} testID="quick-actions" />
          </Anchor>
        </View>

        {/* Setup Progress (only if any todo) */}
        {steps.some(s => s.status === 'todo') && (
          <View style={{ paddingHorizontal: 24, marginBottom: 24 }}>
            <SetupProgressCard steps={steps} testID="setup-progress" onStepPress={(id: string) => {
              if (id === 's2') {
                // Deep-link to PublishThemeScreen when widget not themed
                // @ts-ignore
                navigation.navigate('Settings', { screen: 'Settings', params: { deeplink: 'publish' } })
              }
            }} />
          </View>
        )}

        {/* Insights */}
        <View style={{ paddingHorizontal: 24, gap: 16, marginBottom: 24 }}>
          <InsightsTopIntents items={intents} loading={loadingIntents} testID="insights-intents" />
          <InsightsPeakTimes hours={peakHours} loading={loadingPeak} testID="insights-peak" />
          <VolumeByChannelMini items={byChannel} testID="insights-volume" />
        </View>

        {/* Ask the Dashboard */}
        <View style={{ paddingHorizontal: 24, marginBottom: 24 }}>
          <AskDashboard testID="ask-dashboard" />
        </View>

        {/* Industry Tiles */}
        <View style={{ paddingHorizontal: 24, marginBottom: 32 }}>
          <Anchor testID="industry-tiles">
          <IndustryTiles
            pack={pack}
            onTilePress={(title) => {
              // Basic contextual routing stubs
              if (title.includes('Orders') || title.includes('Pending') || title.includes('Returns')) {
                navigation.navigate('Conversations', { filter: 'urgent' })
              } else if (title.includes('Bookings') || title.includes('Callback')) {
                navigation.navigate('Conversations', { filter: 'waiting30' })
              } else if (title.includes('Trials') || title.includes('At‑Risk')) {
                navigation.navigate('Conversations', { filter: 'vip' })
              }
            }}
            testID="industry-tiles"
          />
          </Anchor>
        </View>

        {/* Spacer bottom */}
        <View style={{ height: 24 }} />

        {/* Sync Center Sheet */}
        <SyncCenterSheet
          visible={syncOpen}
          onClose={() => setSyncOpen(false)}
          lastSyncAt={new Date().toLocaleString()}
          queuedCount={offline ? 3 : 0}
          onRetryAll={() => {}}
          testID="sync-center"
        />
      </ScrollView>
    </SafeAreaView>
  )
}
