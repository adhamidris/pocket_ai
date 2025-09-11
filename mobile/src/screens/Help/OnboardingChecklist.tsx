import React, { useMemo, useState, useEffect } from 'react'
import { View, Text, TouchableOpacity, DeviceEventEmitter } from 'react-native'
import { useNavigation } from '@react-navigation/native'
import { useTheme } from '../../providers/ThemeProvider'
import { Checklist, ChecklistStep } from '../../types/help'
import AsyncStorage from '@react-native-async-storage/async-storage'
import * as NetInfo from '@react-native-community/netinfo'

const initial: Checklist = {
  id: 'global-onboarding',
  title: 'Quickstart Checklist',
  progress: 0,
  steps: [
    { id: 's1', title: 'Connect channel', done: false, cta: { label: 'Open Channels', route: 'Channels' } },
    { id: 's2', title: 'Publish link', done: false, cta: { label: 'Widget snippet', route: 'Channels', params: { screen: 'WidgetSnippet' } } },
    { id: 's3', title: 'Train FAQs', done: false, cta: { label: 'Training Center', route: 'Knowledge', params: { screen: 'TrainingCenter' } } },
    { id: 's4', title: 'Set hours', done: false, cta: { label: 'Business Hours', route: 'Automations', params: { screen: 'BusinessHours' } } },
    { id: 's5', title: 'Test portal', done: false, cta: { label: 'Open Portal', route: 'Portal' } },
    { id: 's6', title: 'Create rule', done: false, cta: { label: 'Rule Builder', route: 'Automations', params: { screen: 'RuleBuilder' } } },
  ]
}

export const OnboardingChecklist: React.FC<{ onDismiss?: () => void }> = ({ onDismiss }) => {
  const { theme } = useTheme()
  const navigation = useNavigation<any>()
  const [data, setData] = useState<Checklist>(initial)
  const [dismissed, setDismissed] = useState<boolean>(false)
  const [offline, setOffline] = useState<boolean>(false)

  useEffect(() => {
    const sub = (NetInfo as any).addEventListener?.((state: any) => setOffline(!state?.isConnected))
    return () => sub && sub()
  }, [])

  useEffect(() => { (async () => { try { await AsyncStorage.setItem('help.cached.checklist', JSON.stringify(data)) } catch {} })() }, [data])

  const doneCount = data.steps.filter(s => s.done).length
  const progressPct = Math.round((doneCount / Math.max(1, data.steps.length)) * 100)

  const navigateCta = (step: ChecklistStep) => {
    if (!step.cta) return
    const { route, params } = step.cta
    if (route === 'Channels' && params?.screen === 'WidgetSnippet') {
      navigation.navigate('Main', { screen: 'Channels', params: { screen: 'WidgetSnippet' } })
    } else if (route === 'Knowledge' && params?.screen === 'TrainingCenter') {
      navigation.navigate('Main', { screen: 'Knowledge', params: { screen: 'TrainingCenter' } })
    } else if (route === 'Automations' && params?.screen === 'BusinessHours') {
      navigation.navigate('Main', { screen: 'Automations', params: { screen: 'BusinessHours' } })
    } else if (route === 'Automations' && params?.screen === 'RuleBuilder') {
      navigation.navigate('Main', { screen: 'Automations', params: { screen: 'RuleBuilder' } })
    } else if (route === 'Portal') {
      navigation.navigate('Main', { screen: 'Portal' })
    } else {
      navigation.navigate('Main', { screen: route })
    }
  }

  if (dismissed || progressPct === 100) return null

  return (
    <View style={{ borderWidth: 1, borderColor: theme.color.border, borderRadius: 16, padding: 16, backgroundColor: theme.color.card }}>
      <View style={{ flexDirection: 'row', justifyContent: 'space-between', alignItems: 'center', marginBottom: 8 }}>
        <Text style={{ color: theme.color.cardForeground, fontWeight: '700', fontSize: 16 }}>{data.title}</Text>
        <TouchableOpacity onPress={() => { setDismissed(true); onDismiss?.() }} accessibilityLabel="Dismiss checklist" style={{ paddingHorizontal: 12, paddingVertical: 6, borderRadius: 8, borderWidth: 1, borderColor: theme.color.border }}>
          <Text style={{ color: theme.color.mutedForeground, fontWeight: '600' }}>Dismiss</Text>
        </TouchableOpacity>
      </View>
      <View style={{ flexDirection: 'row', gap: 8, marginBottom: 12 }}>
        {offline && (
          <View style={{ paddingHorizontal: 8, paddingVertical: 4, borderRadius: 999, borderWidth: 1, borderColor: theme.color.border }}>
            <Text style={{ color: theme.color.mutedForeground }}>Cached</Text>
          </View>
        )}
        <TouchableOpacity onPress={() => DeviceEventEmitter.emit('assistant.open', { text: 'Generate a step-by-step onboarding plan for my first week. Include channels, knowledge, hours, portal, and one automation rule.', persona: 'agent' })} accessibilityLabel="Generate step-by-step" style={{ paddingHorizontal: 12, paddingVertical: 8, borderRadius: 10, borderWidth: 1, borderColor: theme.color.border }}>
          <Text style={{ color: theme.color.cardForeground, fontWeight: '600' }}>Generate step‑by‑step</Text>
        </TouchableOpacity>
      </View>
      <View style={{ height: 8, backgroundColor: theme.color.secondary, borderRadius: 999, overflow: 'hidden', marginBottom: 12 }}>
        <View style={{ width: `${progressPct}%`, backgroundColor: theme.color.primary, flex: 1 }} />
      </View>
      <View style={{ gap: 10 }}>
        {data.steps.map(step => (
          <View key={step.id} style={{ flexDirection: 'row', alignItems: 'center', justifyContent: 'space-between' }}>
            <TouchableOpacity onPress={() => setData(prev => ({ ...prev, steps: prev.steps.map(s => s.id === step.id ? { ...s, done: !s.done } : s) }))} accessibilityLabel={`Toggle ${step.title}`} style={{ width: 44, height: 44, borderRadius: 12, borderWidth: 1, borderColor: theme.color.border, alignItems: 'center', justifyContent: 'center', backgroundColor: step.done ? theme.color.success : 'transparent' }}>
              <Text style={{ color: step.done ? '#000' : theme.color.mutedForeground }}>{step.done ? '✓' : ''}</Text>
            </TouchableOpacity>
            <View style={{ flex: 1, marginLeft: 12 }}>
              <Text style={{ color: theme.color.cardForeground, fontWeight: '600' }}>{step.title}</Text>
            </View>
            {step.cta && (
              <TouchableOpacity onPress={() => navigateCta(step)} accessibilityLabel={step.cta.label} style={{ paddingHorizontal: 12, paddingVertical: 10, borderRadius: 12, backgroundColor: theme.color.primary, minHeight: 44, justifyContent: 'center' }}>
                <Text style={{ color: '#fff', fontWeight: '700' }}>{step.cta.label}</Text>
              </TouchableOpacity>
            )}
          </View>
        ))}
      </View>
    </View>
  )
}

export default OnboardingChecklist


