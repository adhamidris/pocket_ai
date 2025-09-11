import React from 'react'
import { SafeAreaView, View, Text, TouchableOpacity } from 'react-native'
import { useTheme } from '../../providers/ThemeProvider'
import { useSafeAreaInsets } from 'react-native-safe-area-context'
import { useNavigation } from '@react-navigation/native'

type Step = { id: string; label: string; run: () => Promise<void> }

export const ScriptedWalks: React.FC = () => {
  const { theme } = useTheme()
  const insets = useSafeAreaInsets()
  const navigation = useNavigation<any>()
  const [log, setLog] = React.useState<Record<string, 'pending'|'ok'|'fail'>>({})

  const setStatus = (id: string, st: 'pending'|'ok'|'fail') => setLog(prev => ({ ...prev, [id]: st }))
  const delay = (ms: number) => new Promise(r => setTimeout(r, ms))

  const day0: Step[] = [
    { id: 'ch-connect', label: 'Connect channel', run: async () => { navigation.navigate('Main', { screen: 'Channels' }); await delay(150) } },
    { id: 'publish-link', label: 'Publish link', run: async () => { navigation.navigate('Main', { screen: 'Channels', params: { screen: 'WidgetSnippet' } }); await delay(150) } },
    { id: 'theme-publish', label: 'Theme publish', run: async () => { navigation.navigate('Main', { screen: 'Settings', params: { screen: 'Settings', params: { deeplink: 'publish' } } }); await delay(150) } },
    { id: 'train-kb', label: 'Train knowledge', run: async () => { navigation.navigate('Main', { screen: 'Knowledge', params: { screen: 'TrainingCenter' } }); await delay(150) } },
    { id: 'set-hours', label: 'Set business hours', run: async () => { navigation.navigate('Main', { screen: 'Automations', params: { screen: 'BusinessHours' } }); await delay(150) } },
    { id: 'create-rule', label: 'Create rule', run: async () => { navigation.navigate('Main', { screen: 'Automations', params: { screen: 'RuleBuilder' } }); await delay(150) } },
    { id: 'test-portal', label: 'Test portal', run: async () => { navigation.navigate('Main', { screen: 'Portal' }); await delay(150); try { (require('react-native') as any).DeviceEventEmitter.emit('portal.tested') } catch {} } },
  ]

  const opsDay: Step[] = [
    { id: 'handle-backlog', label: 'Handle backlog', run: async () => { navigation.navigate('Main', { screen: 'Conversations' }); await delay(150) } },
    { id: 'sla-edit', label: 'Edit SLA', run: async () => { navigation.navigate('Main', { screen: 'Automations', params: { screen: 'SlaEditor' } }); await delay(150) } },
    { id: 'assistant-qa', label: 'Assistant Q&A', run: async () => { (require('react-native') as any).DeviceEventEmitter.emit('assistant.open', { text: 'How did FRT change?', persona: 'analyst' }); await delay(150) } },
    { id: 'rule-from-intent', label: 'Create automation from intent', run: async () => { navigation.navigate('Main', { screen: 'Dashboard' }); await delay(150) } },
    { id: 'verify-trend', label: 'Verify analytics trend link', run: async () => { navigation.navigate('Main', { screen: 'Analytics', params: { screen: 'Trends' } }); await delay(150) } },
    { id: 'approvals', label: 'Approvals in Actions', run: async () => { navigation.navigate('Main', { screen: 'Actions' }); await delay(150) } },
  ]

  const supportExit: Step[] = [
    { id: 'end-chat', label: 'End portal chat', run: async () => { navigation.navigate('Main', { screen: 'Portal' }); await delay(150) } },
    { id: 'csat', label: 'Submit CSAT', run: async () => { try { (require('../../components/portal/CsatPanel') as any) } catch {}; await delay(150) } },
    { id: 'transcript', label: 'View transcript', run: async () => { try { (require('react-native') as any).DeviceEventEmitter.emit('portal.tested') } catch {}; await delay(150) } },
  ]

  const runSteps = async (steps: Step[]) => {
    for (const s of steps) {
      setStatus(s.id, 'pending')
      try { await s.run(); setStatus(s.id, 'ok') } catch { setStatus(s.id, 'fail') }
    }
  }

  const Section: React.FC<{ title: string; steps: Step[] }> = ({ title, steps }) => (
    <View style={{ borderWidth: 1, borderColor: theme.color.border, borderRadius: 12, padding: 12 }}>
      <View style={{ flexDirection: 'row', alignItems: 'center', justifyContent: 'space-between', marginBottom: 8 }}>
        <Text style={{ color: theme.color.mutedForeground, fontWeight: '700' }}>{title}</Text>
        <TouchableOpacity onPress={() => runSteps(steps)} accessibilityRole="button" accessibilityLabel={`Run ${title}`} style={{ paddingHorizontal: 10, paddingVertical: 6, borderRadius: 8, borderWidth: 1, borderColor: theme.color.border }}>
          <Text style={{ color: theme.color.cardForeground, fontWeight: '700' }}>Run</Text>
        </TouchableOpacity>
      </View>
      {steps.map(s => (
        <View key={s.id} style={{ flexDirection: 'row', alignItems: 'center', justifyContent: 'space-between', paddingVertical: 6 }}>
          <Text style={{ color: theme.color.cardForeground }}>{s.label}</Text>
          <Text style={{ color: (log[s.id] === 'ok' ? theme.color.success : log[s.id] === 'fail' ? theme.color.warning : theme.color.mutedForeground), fontWeight: '700' }}>{(log[s.id] || 'idle').toUpperCase()}</Text>
        </View>
      ))}
    </View>
  )

  return (
    <SafeAreaView style={{ flex: 1, backgroundColor: theme.color.background }}>
      <View style={{ paddingHorizontal: 16, paddingTop: insets.top + 12, paddingBottom: 12 }}>
        <Text style={{ color: theme.color.foreground, fontSize: 22, fontWeight: '700' }}>Scripted Walks</Text>
      </View>
      <View style={{ paddingHorizontal: 16, gap: 12 }}>
        <Section title="Day‑0 setup" steps={day0} />
        <Section title="Ops day" steps={opsDay} />
        <Section title="Support exit" steps={supportExit} />
      </View>
    </SafeAreaView>
  )
}

export default ScriptedWalks


