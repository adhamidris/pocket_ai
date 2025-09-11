import React from 'react'
import { SafeAreaView, View, Text, TouchableOpacity, ScrollView, RefreshControl } from 'react-native'
import { useSafeAreaInsets } from 'react-native-safe-area-context'
import { useTheme } from '../../providers/ThemeProvider'
import { useNavigation } from '@react-navigation/native'
import { track } from '../../lib/analytics'
import { DataRetentionPolicy, ConsentTemplate, AuditEvent, IpRule, SessionPolicy, ResidencySetting, ExportJob, DeletionRequest, PrivacyMode } from '../../types/security'
import { PolicyCard } from '../../components/security'
import OfflineBanner from '../../components/dashboard/OfflineBanner'
import SyncCenterSheet from '../../components/dashboard/SyncCenterSheet'
import RiskBadge from '../../components/security/RiskBadge'
import AuditRow from '../../components/security/AuditRow'

const now = () => Date.now()

const genRetention = (): DataRetentionPolicy => ({ id: 'rp-1', name: 'Default', conversationsDays: 365, messagesDays: 180, auditDays: 365 * 2, piiMasking: true, applyToBackups: false, updatedAt: now() })
const genConsent = (): ConsentTemplate[] => ([{ id: 'ct-1', name: 'Default Consent', version: 1, languages: [{ code: 'en', text: 'We collect...' }], channels: ['web','email'], lastPublishedAt: now() - 86400000 }])
const genAudit = (): AuditEvent[] => Array.from({ length: 10 }).map((_, i) => ({ id: `a-${i}`, ts: now() - i * 3600_000, actor: i % 3 === 0 ? 'system' : 'me', action: i % 2 ? 'policy.update' : 'export.create', entityType: i % 2 ? 'policy' : 'export', risk: (i % 4 === 0 ? 'medium' : 'low') }))
const genIpRules = (): IpRule[] => ([{ id: 'ip-1', cidr: '10.0.0.0/8', enabled: true }, { id: 'ip-2', cidr: '192.168.0.0/16', enabled: false }])
const genSession = (): SessionPolicy => ({ enforce2FA: true, sessionHours: 24, idleTimeoutMins: 30, deviceLimit: 3 })
const genResidency = (): ResidencySetting => ({ region: 'auto' })
const genExports = (): ExportJob[] => ([{ id: 'ex-1', scope: 'all', state: 'completed', progress: 100, createdAt: now() - 7200_000, finishedAt: now() - 7100_000, downloadUrl: 'https://example.com/export.zip' }])
const genDeletions = (): DeletionRequest[] => ([{ id: 'del-1', subject: 'contact', status: 'pending', submittedAt: now() - 3600_000 }])
const genPrivacy = (): PrivacyMode => ({ anonymizeAnalytics: true, hideContactPII: false, strictLogging: false })

const SecurityHome: React.FC = () => {
  const { theme } = useTheme()
  const insets = useSafeAreaInsets()
  const navigation = useNavigation<any>()

  const [retention, setRetention] = React.useState<DataRetentionPolicy>(() => genRetention())
  const [templates, setTemplates] = React.useState<ConsentTemplate[]>(() => genConsent())
  const [audit, setAudit] = React.useState<AuditEvent[]>(() => genAudit())
  const [ipRules, setIpRules] = React.useState<IpRule[]>(() => genIpRules())
  const [session, setSession] = React.useState<SessionPolicy>(() => genSession())
  const [residency, setResidency] = React.useState<ResidencySetting>(() => genResidency())
  const [exportsList, setExportsList] = React.useState<ExportJob[]>(() => genExports())
  const [deletions, setDeletions] = React.useState<DeletionRequest[]>(() => genDeletions())
  const [privacy, setPrivacy] = React.useState<PrivacyMode>(() => genPrivacy())
  const [refreshing, setRefreshing] = React.useState(false)
  const [offline, setOffline] = React.useState(false)
  const [syncOpen, setSyncOpen] = React.useState(false)

  React.useEffect(() => { track('security.view') }, [])

  const onRefresh = () => {
    setRefreshing(true)
    setTimeout(() => {
      setAudit(genAudit())
      setExportsList(genExports())
      setDeletions(genDeletions())
      setRefreshing(false)
    }, 500)
  }

  const RowBtn: React.FC<{ label: string; onPress: () => void }>=({ label, onPress }) => (
    <TouchableOpacity onPress={onPress} accessibilityRole="button" accessibilityLabel={label} style={{ paddingHorizontal: 10, paddingVertical: 6, borderRadius: 8, borderWidth: 1, borderColor: theme.color.border }}>
      <Text style={{ color: theme.color.cardForeground, fontWeight: '600' }}>{label}</Text>
    </TouchableOpacity>
  )

  return (
    <SafeAreaView style={{ flex: 1, backgroundColor: theme.color.background }}>
      <ScrollView refreshControl={<RefreshControl refreshing={refreshing} onRefresh={onRefresh} />}> 
        {/* Header */}
        <View style={{ paddingHorizontal: 24, paddingTop: insets.top + 12, paddingBottom: 12 }}>
          {offline && (
            <View style={{ marginBottom: 8 }}>
              <OfflineBanner visible={offline} testID="sec-offline" />
            </View>
          )}
          <View style={{ flexDirection: 'row', alignItems: 'center', justifyContent: 'space-between' }}>
            <Text style={{ color: theme.color.foreground, fontSize: 28, fontWeight: '700' }}>Security & Privacy Center</Text>
            <View style={{ flexDirection: 'row', gap: 8 }}>
              <TouchableOpacity onPress={() => setOffline((v) => !v)} accessibilityRole="button" accessibilityLabel="Toggle offline" style={{ paddingHorizontal: 10, paddingVertical: 6, borderRadius: 8, borderWidth: 1, borderColor: offline ? theme.color.primary : theme.color.border }}>
                <Text style={{ color: offline ? theme.color.primary : theme.color.cardForeground }}>{offline ? 'Cached' : 'Online'}</Text>
              </TouchableOpacity>
              <TouchableOpacity onPress={() => setSyncOpen(true)} accessibilityRole="button" accessibilityLabel="Sync Center" style={{ paddingHorizontal: 10, paddingVertical: 6, borderRadius: 8, borderWidth: 1, borderColor: theme.color.border }}>
                <Text style={{ color: theme.color.cardForeground }}>Sync</Text>
              </TouchableOpacity>
              <RowBtn label="Download audit" onPress={() => { /* stub */ }} />
              <RowBtn label="Policies JSON" onPress={() => { /* stub */ }} />
            </View>
          </View>
        </View>

        <View style={{ paddingHorizontal: 24, gap: 12 }}>
          <PolicyCard title="Data Retention" subtitle={`${retention.conversationsDays === -1 ? 'Indefinite' : retention.conversationsDays + ' days'} conversations • ${retention.messagesDays} messages • ${retention.auditDays} audit`}>
            <View style={{ flexDirection: 'row', justifyContent: 'flex-end' }}>
              <RowBtn label="Edit" onPress={() => navigation.navigate('Security', { screen: 'PrivacyCenter' })} />
            </View>
          </PolicyCard>

          <PolicyCard title="Consent" subtitle={`${templates.length} templates • Last publish ${templates[0]?.lastPublishedAt ? new Date(templates[0].lastPublishedAt!).toLocaleDateString() : '—'}`}>
            <View style={{ flexDirection: 'row', justifyContent: 'flex-end' }}>
              <RowBtn label="Manage" onPress={() => navigation.navigate('Security', { screen: 'PrivacyCenter' })} />
            </View>
          </PolicyCard>

          <PolicyCard title="Audit Log" subtitle={`Showing last 5`}> 
            <View>
              {audit.slice(0, 5).map((e) => (
                <AuditRow key={e.id} evt={e} />
              ))}
              <View style={{ flexDirection: 'row', justifyContent: 'flex-end' }}>
                <RowBtn label="View all" onPress={() => navigation.navigate('Security', { screen: 'PrivacyCenter' })} />
              </View>
            </View>
          </PolicyCard>

          <PolicyCard title="Access Controls" subtitle={`${ipRules.length} IP rules • ${session.enforce2FA ? '2FA On' : '2FA Off'}, session ${session.sessionHours}h`}>
            <View style={{ flexDirection: 'row', justifyContent: 'flex-end' }}>
              <RowBtn label="Edit" onPress={() => navigation.navigate('Security', { screen: 'PrivacyCenter' })} />
            </View>
          </PolicyCard>

          <PolicyCard title="Data Residency" subtitle={`Region: ${residency.region.toUpperCase()}`}>
            <View style={{ flexDirection: 'row', justifyContent: 'flex-end' }}>
              <RowBtn label="Change" onPress={() => navigation.navigate('Security', { screen: 'PrivacyCenter' })} />
            </View>
          </PolicyCard>

          <PolicyCard title="Exports & Deletion Requests" subtitle={`${exportsList.length} exports • ${deletions.length} deletions`}>
            <View style={{ flexDirection: 'row', justifyContent: 'flex-end' }}>
              <RowBtn label="Open" onPress={() => navigation.navigate('Security', { screen: 'PrivacyCenter' })} />
            </View>
          </PolicyCard>

          <PolicyCard title="Privacy Modes" subtitle={`${privacy.anonymizeAnalytics ? 'Anonymize' : ''} ${privacy.hideContactPII ? '• Hide PII' : ''} ${privacy.strictLogging ? '• Strict logs' : ''}`.trim() || 'Default'}>
            <View style={{ flexDirection: 'row', justifyContent: 'flex-end' }}>
              <RowBtn label="Edit" onPress={() => navigation.navigate('Security', { screen: 'PrivacyCenter' })} />
            </View>
          </PolicyCard>
        </View>
      </ScrollView>
      <SyncCenterSheet visible={syncOpen} onClose={() => setSyncOpen(false)} lastSyncAt={new Date().toLocaleString()} queuedCount={offline ? 5 : 0} onRetryAll={() => setSyncOpen(false)} />
    </SafeAreaView>
  )
}

export default SecurityHome


