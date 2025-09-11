import React from 'react'
import { SafeAreaView, View, Text, TouchableOpacity, Alert, Switch, TextInput } from 'react-native'
import { useTheme } from '../../providers/ThemeProvider'
import { useSafeAreaInsets } from 'react-native-safe-area-context'
import AsyncStorage from '@react-native-async-storage/async-storage'
import { useNavigation } from '@react-navigation/native'

type Stage = 'internal' | 'pilot' | 'ga'

type Persisted = {
  stage: Stage
  notes?: string
  checks: { eventsBaseline: boolean; funnelsOk: boolean; errorRateOk: boolean }
}

const STORAGE_KEY = 'qa.launch.rollout.v1'

export const LaunchRollout: React.FC = () => {
  const { theme } = useTheme()
  const insets = useSafeAreaInsets()
  const navigation = useNavigation<any>()
  const [state, setState] = React.useState<Persisted>({ stage: 'internal', notes: '', checks: { eventsBaseline: false, funnelsOk: false, errorRateOk: false } })
  const [loaded, setLoaded] = React.useState<boolean>(false)

  React.useEffect(() => { (async () => {
    try { const raw = await AsyncStorage.getItem(STORAGE_KEY); if (raw) setState(JSON.parse(raw)) } catch {}
    setLoaded(true)
  })() }, [])

  React.useEffect(() => { if (!loaded) return; (async () => { try { await AsyncStorage.setItem(STORAGE_KEY, JSON.stringify(state)) } catch {} })() }, [state, loaded])

  const setStage = (stage: Stage) => setState((s) => ({ ...s, stage }))
  const setNote = (notes: string) => setState((s) => ({ ...s, notes }))
  const setCheck = (k: keyof Persisted['checks'], v: boolean) => setState((s) => ({ ...s, checks: { ...s.checks, [k]: v } }))

  const Chip: React.FC<{ label: string; active?: boolean; onPress: () => void }> = ({ label, active, onPress }) => (
    <TouchableOpacity onPress={onPress} accessibilityRole="button" accessibilityLabel={label} style={{ paddingHorizontal: 10, paddingVertical: 8, borderRadius: 10, borderWidth: 1, borderColor: active ? theme.color.primary : theme.color.border, backgroundColor: active ? (theme.color.primary + '22') : 'transparent' }}>
      <Text style={{ color: theme.color.cardForeground, fontWeight: '700' }}>{label}</Text>
    </TouchableOpacity>
  )

  const Row: React.FC<{ left: React.ReactNode; right?: React.ReactNode }> = ({ left, right }) => (
    <View style={{ flexDirection: 'row', alignItems: 'center', justifyContent: 'space-between', paddingHorizontal: 12, paddingVertical: 10, borderBottomWidth: 1, borderBottomColor: theme.color.border }}>
      <View style={{ flex: 1 }}>{typeof left === 'string' ? <Text style={{ color: theme.color.cardForeground }}>{left}</Text> : left}</View>
      {right}
    </View>
  )

  return (
    <SafeAreaView style={{ flex: 1, backgroundColor: theme.color.background }}>
      <View style={{ paddingHorizontal: 16, paddingTop: insets.top + 12, paddingBottom: 12 }}>
        <Text style={{ color: theme.color.foreground, fontSize: 22, fontWeight: '700', marginBottom: 8 }}>Launch & Rollout</Text>
        <View style={{ flexDirection: 'row', gap: 8, flexWrap: 'wrap' }}>
          <Chip label="Internal" active={state.stage === 'internal'} onPress={() => setStage('internal')} />
          <Chip label="Pilot" active={state.stage === 'pilot'} onPress={() => setStage('pilot')} />
          <Chip label="GA" active={state.stage === 'ga'} onPress={() => setStage('ga')} />
        </View>
      </View>

      <View style={{ paddingHorizontal: 16 }}>
        <View style={{ borderWidth: 1, borderColor: theme.color.border, borderRadius: 12, overflow: 'hidden' }}>
          <Row left="Support runbook (stub)" right={<Chip label="Open" onPress={() => Alert.alert('Runbook', 'Open support runbook (stub)')} />} />
          <Row left="Incident response (Security Audit)" right={<Chip label="Open" onPress={() => navigation.navigate('SecPrivacyAudit')} />} />
        </View>
      </View>

      <View style={{ paddingHorizontal: 16, marginTop: 12 }}>
        <View style={{ borderWidth: 1, borderColor: theme.color.border, borderRadius: 12 }}>
          <Row left="Post‑launch: Event volume baseline">
            <Switch value={state.checks.eventsBaseline} onValueChange={(v) => setCheck('eventsBaseline', v)} />
          </Row>
          <Row left="Post‑launch: Funnel conversions healthy">
            <Switch value={state.checks.funnelsOk} onValueChange={(v) => setCheck('funnelsOk', v)} />
          </Row>
          <Row left="Post‑launch: Error rate proxies OK">
            <Switch value={state.checks.errorRateOk} onValueChange={(v) => setCheck('errorRateOk', v)} />
          </Row>
        </View>
      </View>

      <View style={{ paddingHorizontal: 16, marginTop: 12 }}>
        <Text style={{ color: theme.color.mutedForeground, marginBottom: 6 }}>Notes</Text>
        <View style={{ borderWidth: 1, borderColor: theme.color.border, borderRadius: 12, backgroundColor: theme.color.card }}>
          <TextInput
            value={state.notes}
            onChangeText={setNote}
            placeholder="Rollout notes…"
            placeholderTextColor={theme.color.placeholder}
            multiline
            style={{ minHeight: 96, paddingHorizontal: 12, paddingVertical: 10, color: theme.color.cardForeground }}
          />
        </View>
      </View>

      <View style={{ paddingHorizontal: 16, marginTop: 12 }}>
        <View style={{ flexDirection: 'row', gap: 8, flexWrap: 'wrap' }}>
          <Chip label="Telemetry Audit" onPress={() => navigation.navigate('TelemetryAudit')} />
          <Chip label="RC Preflight" onPress={() => navigation.navigate('RC_Preflight')} />
        </View>
      </View>
    </SafeAreaView>
  )
}

export default LaunchRollout


