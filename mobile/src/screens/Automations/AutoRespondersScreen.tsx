import React from 'react'
import { SafeAreaView, View, Text, TouchableOpacity, TextInput, ScrollView, Switch, Alert } from 'react-native'
import { useSafeAreaInsets } from 'react-native-safe-area-context'
import { useTheme } from '../../providers/ThemeProvider'
import { useNavigation, useRoute } from '@react-navigation/native'
import { AutoResponder } from '../../types/automations'
import ResponderCard from '../../components/automations/ResponderCard'
import OfflineBanner from '../../components/dashboard/OfflineBanner'
import TagChip from '../../components/crm/TagChip'
import { track } from '../../lib/analytics'

export interface AutoRespondersParams {
  responders?: AutoResponder[]
  onApply?: (arr: AutoResponder[]) => void
}

const channelsAll = ['whatsapp', 'instagram', 'facebook', 'web', 'email'] as const

const AutoRespondersScreen: React.FC = () => {
  const { theme } = useTheme()
  const insets = useSafeAreaInsets()
  const navigation = useNavigation<any>()
  const route = useRoute<any>()
  const params: AutoRespondersParams = route.params || {}

  const [list, setList] = React.useState<AutoResponder[]>(params.responders || [])
  const [offline, setOffline] = React.useState(false)
  const [queued, setQueued] = React.useState<Record<string, boolean>>({})

  // Editor state
  const [editing, setEditing] = React.useState<AutoResponder | null>(null)
  const [name, setName] = React.useState('')
  const [active, setActive] = React.useState(true)
  const [message, setMessage] = React.useState('')
  const [messageDraft, setMessageDraft] = React.useState('')
  const [channels, setChannels] = React.useState<string[]>(['web'])
  const [outsideOnly, setOutsideOnly] = React.useState<boolean>(false)
  const [intents, setIntents] = React.useState<string>('')

  const openNew = () => {
    setEditing(null)
    setName('')
    setActive(true)
    setMessage(''); setMessageDraft('')
    setChannels(['web'])
    setOutsideOnly(false)
    setIntents('')
  }

  const openEdit = (res: AutoResponder) => {
    setEditing(res)
    setName(res.name)
    setActive(res.active)
    setMessage(res.message); setMessageDraft(res.message)
  React.useEffect(() => {
    const t = setTimeout(() => setMessage(messageDraft), 300)
    return () => clearTimeout(t)
  }, [messageDraft])
    setChannels(res.channels as any)
    setOutsideOnly(!!res.onlyOutsideHours)
    setIntents((res.intentFilter || []).join(', '))
  }

  const toggleChannel = (ch: string) => {
    setChannels((arr) => arr.includes(ch) ? arr.filter((c) => c !== ch) : [...arr, ch])
  }

  const save = () => {
    if (!message.trim()) { Alert.alert('Message required'); return }
    if (!name.trim()) { Alert.alert('Name required'); return }
    const base: AutoResponder = {
      id: editing?.id || `ar-${Date.now()}`,
      name: name.trim(),
      active,
      message: message.trim(),
      channels: channels as any,
      onlyOutsideHours: outsideOnly || undefined,
      intentFilter: intents.split(',').map(s => s.trim()).filter(Boolean),
    }
    setList((arr) => {
      const exists = arr.find((x) => x.id === base.id)
      if (exists) return arr.map((x) => x.id === base.id ? base : x)
      return [base, ...arr]
    })
    setEditing(null)
    setName(''); setMessage(''); setIntents(''); setChannels(['web']); setOutsideOnly(false); setActive(true)
  }

  const done = () => {
    try { params.onApply && params.onApply(list) } catch {}
    navigation.goBack()
  }

  return (
    <SafeAreaView style={{ flex: 1, backgroundColor: theme.color.background }}>
      {/* Header */}
      <View style={{ paddingHorizontal: 16, paddingTop: insets.top + 8, paddingBottom: 12, borderBottomWidth: 1, borderBottomColor: theme.color.border }}>
        <View style={{ flexDirection: 'row', alignItems: 'center', justifyContent: 'space-between' }}>
          <TouchableOpacity onPress={() => navigation.goBack()} accessibilityLabel="Back" accessibilityRole="button" style={{ padding: 8 }}>
            <Text style={{ color: theme.color.primary, fontWeight: '600' }}>{'< Back'}</Text>
          </TouchableOpacity>
          <Text style={{ color: theme.color.foreground, fontSize: 18, fontWeight: '700' }}>Auto-responders</Text>
          <TouchableOpacity onPress={done} accessibilityLabel="Done" accessibilityRole="button" style={{ padding: 8 }}>
            <Text style={{ color: theme.color.primary, fontWeight: '700' }}>Done</Text>
          </TouchableOpacity>
        </View>
      </View>

      <ScrollView style={{ padding: 16 }} contentContainerStyle={{ paddingBottom: 28 }}>
        {offline && <OfflineBanner visible />}
        {/* List */}
        <View style={{ flexDirection: 'row', alignItems: 'center', justifyContent: 'space-between', marginBottom: 8 }}>
          <Text style={{ color: theme.color.cardForeground, fontWeight: '700' }}>Responders</Text>
          <TouchableOpacity onPress={openNew} accessibilityLabel="New" accessibilityRole="button" style={{ paddingHorizontal: 12, paddingVertical: 8, borderRadius: 10, borderWidth: 1, borderColor: theme.color.border }}>
            <Text style={{ color: theme.color.primary, fontWeight: '700' }}>New</Text>
          </TouchableOpacity>
        </View>
        <View style={{ gap: 8, marginBottom: 16 }}>
          {list.map((r) => (
            <ResponderCard key={r.id} res={r} queued={!!queued[r.id]} onToggle={() => {
              setList((arr) => arr.map((x) => x.id === r.id ? { ...x, active: !x.active } : x))
              track('responder.toggle', { active: !r.active })
              if (offline) {
                setQueued((q) => ({ ...q, [r.id]: true }))
                setTimeout(() => setQueued((q) => ({ ...q, [r.id]: false })), 1500)
              }
            }} onEdit={() => openEdit(r)} />
          ))}
        </View>

        {/* Editor */}
        <View style={{ borderWidth: 1, borderColor: theme.color.border, borderRadius: 12, padding: 12 }}>
          <Text style={{ color: theme.color.cardForeground, fontWeight: '700', marginBottom: 8 }}>{editing ? 'Edit Responder' : 'New Responder'}</Text>
          <Text style={{ color: theme.color.mutedForeground, marginBottom: 6 }}>Name</Text>
          <TextInput value={name} onChangeText={setName} placeholder="e.g., After-hours reply" placeholderTextColor={theme.color.placeholder} accessibilityLabel="Responder name" style={{ borderWidth: 1, borderColor: theme.color.border, borderRadius: 12, paddingHorizontal: 12, paddingVertical: 10, color: theme.color.cardForeground, marginBottom: 10 }} />

          <View style={{ flexDirection: 'row', alignItems: 'center', justifyContent: 'space-between', marginBottom: 10 }}>
            <Text style={{ color: theme.color.mutedForeground }}>Active</Text>
            <Switch value={active} onValueChange={setActive} accessibilityLabel="Active" />
          </View>

          <Text style={{ color: theme.color.mutedForeground, marginBottom: 6 }}>Message</Text>
          <TextInput value={messageDraft} onChangeText={setMessageDraft} placeholder="Type the auto-response" placeholderTextColor={theme.color.placeholder} accessibilityLabel="Message" multiline style={{ minHeight: 100, textAlignVertical: 'top', borderWidth: 1, borderColor: theme.color.border, borderRadius: 12, paddingHorizontal: 12, paddingVertical: 10, color: theme.color.cardForeground, marginBottom: 10 }} />

          <Text style={{ color: theme.color.mutedForeground, marginBottom: 6 }}>Channels</Text>
          <View style={{ flexDirection: 'row', flexWrap: 'wrap', gap: 8, marginBottom: 10 }}>
            {channelsAll.map((ch) => (
              <TagChip key={ch} label={ch} selected={channels.includes(ch)} onPress={() => toggleChannel(ch)} />
            ))}
          </View>

          <View style={{ flexDirection: 'row', alignItems: 'center', justifyContent: 'space-between', marginBottom: 10 }}>
            <Text style={{ color: theme.color.mutedForeground }}>Only outside business hours</Text>
            <Switch value={outsideOnly} onValueChange={setOutsideOnly} accessibilityLabel="Only outside hours" />
          </View>

          <Text style={{ color: theme.color.mutedForeground, marginBottom: 6 }}>Intent filters (comma-separated)</Text>
          <TextInput value={intents} onChangeText={setIntents} placeholder="billing, order" placeholderTextColor={theme.color.placeholder} accessibilityLabel="Intent filters" style={{ borderWidth: 1, borderColor: theme.color.border, borderRadius: 12, paddingHorizontal: 12, paddingVertical: 10, color: theme.color.cardForeground, marginBottom: 12 }} />

          <View style={{ flexDirection: 'row', justifyContent: 'flex-end' }}>
            <TouchableOpacity onPress={save} accessibilityLabel="Save responder" accessibilityRole="button" style={{ paddingHorizontal: 12, paddingVertical: 10, borderWidth: 1, borderColor: theme.color.border, borderRadius: 12 }}>
              <Text style={{ color: theme.color.primary, fontWeight: '700' }}>{editing ? 'Update' : 'Save'}</Text>
            </TouchableOpacity>
          </View>
        </View>
      </ScrollView>
    </SafeAreaView>
  )
}

export default AutoRespondersScreen


