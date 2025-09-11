import React from 'react'
import { SafeAreaView, View, Text, TouchableOpacity, ScrollView, Alert } from 'react-native'
import { useSafeAreaInsets } from 'react-native-safe-area-context'
import { useTheme } from '../../providers/ThemeProvider'
import { useNavigation, useRoute } from '@react-navigation/native'
import { ActionRun } from '../../types/actions'
import SimResultCard from '../../components/actions/SimResultCard'

type Params = { run: ActionRun }

const RunDetail: React.FC = () => {
  const { theme } = useTheme()
  const insets = useSafeAreaInsets()
  const navigation = useNavigation<any>()
  const route = useRoute<any>()
  const { run } = (route.params || {}) as Params

  const rollback = () => {
    Alert.alert('Rollback', 'UI-only: if action supports rollback, it would be queued.')
  }

  const timeline = [
    { k: 'requested', at: new Date(run.createdAt).toLocaleString() },
    ...(run.approvedBy ? [{ k: 'approved', at: new Date(run.createdAt + 1000).toLocaleString() }] : []),
    { k: 'running', at: new Date(run.createdAt + 3000).toLocaleString() },
    { k: run.state, at: new Date(run.createdAt + 7000).toLocaleString() },
  ]

  return (
    <SafeAreaView style={{ flex: 1, backgroundColor: theme.color.background }}>
      <ScrollView contentContainerStyle={{ paddingBottom: 24 }}>
        {/* Header */}
        <View style={{ paddingHorizontal: 24, paddingTop: insets.top + 12, paddingBottom: 12 }}>
          <View style={{ flexDirection: 'row', alignItems: 'center', justifyContent: 'space-between' }}>
            <TouchableOpacity onPress={() => navigation.goBack()} accessibilityLabel="Back" accessibilityRole="button" style={{ paddingHorizontal: 12, paddingVertical: 8, borderRadius: 10, borderWidth: 1, borderColor: theme.color.border }}>
              <Text style={{ color: theme.color.primary, fontWeight: '700' }}>{'< Back'}</Text>
            </TouchableOpacity>
            <Text style={{ color: theme.color.foreground, fontSize: 20, fontWeight: '700' }}>Run {run.id}</Text>
            <TouchableOpacity onPress={rollback} accessibilityLabel="Rollback" accessibilityRole="button" style={{ paddingHorizontal: 12, paddingVertical: 8, borderRadius: 10, borderWidth: 1, borderColor: theme.color.border }}>
              <Text style={{ color: theme.color.cardForeground, fontWeight: '600' }}>Rollback</Text>
            </TouchableOpacity>
          </View>
        </View>

        {/* Timeline */}
        <View style={{ paddingHorizontal: 24, marginBottom: 12 }}>
          <Text style={{ color: theme.color.cardForeground, fontWeight: '700', marginBottom: 6 }}>Timeline</Text>
          <View style={{ gap: 4 }}>
            {timeline.map((t, i) => (
              <Text key={i} style={{ color: theme.color.mutedForeground }}>• {t.k} — {t.at}</Text>
            ))}
          </View>
        </View>

        {/* Params */}
        <View style={{ paddingHorizontal: 24, marginBottom: 12 }}>
          <Text style={{ color: theme.color.cardForeground, fontWeight: '700', marginBottom: 6 }}>Params</Text>
          <Text style={{ color: theme.color.mutedForeground }}>{JSON.stringify(run.params)}</Text>
        </View>

        {/* Result */}
        <View style={{ paddingHorizontal: 24, marginBottom: 12 }}>
          <Text style={{ color: theme.color.cardForeground, fontWeight: '700', marginBottom: 6 }}>Result</Text>
          <SimResultCard res={run.result} />
        </View>
      </ScrollView>
    </SafeAreaView>
  )
}

export default RunDetail


