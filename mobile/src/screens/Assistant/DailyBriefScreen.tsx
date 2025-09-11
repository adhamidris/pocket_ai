import React from 'react'
import { SafeAreaView, View, Text, ScrollView, TouchableOpacity, Alert } from 'react-native'
import { useSafeAreaInsets } from 'react-native-safe-area-context'
import { useNavigation } from '@react-navigation/native'
import { useTheme } from '../../providers/ThemeProvider'
import { AnswerCard } from '../../components/assistant/AnswerCard'
import { AskAnswer, AnswerChunk } from '../../types/assistant'

const buildBrief = (): AskAnswer => {
  const kpiFrt: AnswerChunk = { kind: 'kpi', kpi: { label: 'FRT P50', value: '02:10', delta: '-8s' } }
  const kpiRes: AnswerChunk = { kind: 'kpi', kpi: { label: 'Resolution Rate', value: '84%', delta: '+2%' } }
  const summary: AnswerChunk = { kind: 'paragraph', text: 'Compared to yesterday: response time improved, resolution rate up slightly.' }
  const incidents: AnswerChunk = { kind: 'list', items: ['2 predicted breaches in Instagram DM', 'VIP queue stable', 'Top intent: Order status (+3%)'] }
  const staffing: AnswerChunk = { kind: 'callout', text: 'Staffing hint: Shift one agent to 10–12am to cover spike.' }
  const rules: AnswerChunk = { kind: 'list', items: ['WHEN intent=Order status → auto-reply FAQ', 'WHEN VIP AND waiting>20m → priority escalate'] }
  return {
    id: 'daily-brief',
    queryId: 'daily-brief',
    createdAt: Date.now(),
    chunks: [kpiFrt, kpiRes, summary, incidents, staffing, rules],
    followUps: ['Open breaches today', 'Show last 7 days trend'],
    toolSuggestions: [ { key: 'open_analytics', label: 'Open Trends (Day)' } ],
    safety: { piiMasked: true, disclaimer: 'This is a preview; verify before acting.' },
  }
}

const DailyBriefScreen: React.FC = () => {
  const { theme } = useTheme()
  const insets = useSafeAreaInsets()
  const navigation = useNavigation<any>()
  const answer = React.useMemo(buildBrief, [])
  const [scheduled, setScheduled] = React.useState(false)

  return (
    <SafeAreaView style={{ flex: 1, backgroundColor: theme.color.background }}>
      <ScrollView contentContainerStyle={{ paddingTop: insets.top + 12, paddingBottom: 24 }}>
        <View style={{ paddingHorizontal: 24, marginBottom: 12, flexDirection: 'row', alignItems: 'center', justifyContent: 'space-between' }}>
          <Text style={{ color: theme.color.foreground, fontSize: 24, fontWeight: '700' }}>Daily Brief</Text>
          <TouchableOpacity onPress={() => navigation.goBack()} accessibilityLabel="Back" accessibilityRole="button" style={{ paddingHorizontal: 12, paddingVertical: 8, borderWidth: 1, borderColor: theme.color.border, borderRadius: 10 }}>
            <Text style={{ color: theme.color.cardForeground }}>Close</Text>
          </TouchableOpacity>
        </View>
        <View style={{ paddingHorizontal: 24, gap: 12 }}>
          <AnswerCard answer={answer} hidePII autoplayTts />
          <View style={{ flexDirection: 'row', gap: 12 }}>
            <TouchableOpacity onPress={() => Alert.alert('Share', 'Sharing daily brief…')} accessibilityLabel="Share" accessibilityRole="button" style={{ paddingHorizontal: 12, paddingVertical: 10, borderRadius: 10, backgroundColor: theme.color.primary }}>
              <Text style={{ color: '#fff', fontWeight: '700' }}>Share</Text>
            </TouchableOpacity>
            <TouchableOpacity onPress={() => setScheduled((v) => !v)} accessibilityLabel="Schedule" accessibilityRole="button" style={{ paddingHorizontal: 12, paddingVertical: 10, borderRadius: 10, borderWidth: 1, borderColor: theme.color.border }}>
              <Text style={{ color: theme.color.cardForeground, fontWeight: '600' }}>{scheduled ? 'Scheduled: 9am daily' : 'Schedule: 9am'}</Text>
            </TouchableOpacity>
            <TouchableOpacity onPress={() => navigation.navigate('Analytics', { screen: 'Trends' })} accessibilityLabel="Open Analytics" accessibilityRole="button" style={{ paddingHorizontal: 12, paddingVertical: 10, borderRadius: 10, borderWidth: 1, borderColor: theme.color.border }}>
              <Text style={{ color: theme.color.cardForeground, fontWeight: '600' }}>Open Analytics</Text>
            </TouchableOpacity>
          </View>
        </View>
      </ScrollView>
    </SafeAreaView>
  )
}

export default DailyBriefScreen


