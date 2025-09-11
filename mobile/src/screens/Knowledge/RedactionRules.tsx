import React from 'react'
import { SafeAreaView, View, Text, TouchableOpacity, FlatList, Modal, TextInput, Switch } from 'react-native'
import { useSafeAreaInsets } from 'react-native-safe-area-context'
import { useTheme } from '../../providers/ThemeProvider'
import { useNavigation } from '@react-navigation/native'
import { RedactionRule } from '../../types/knowledge'
import RedactionRuleRow from '../../components/knowledge/RedactionRuleRow'
import { track } from '../../lib/analytics'

const maskSample = (pattern: string, sample: string) => {
  try {
    const re = new RegExp(pattern, 'g')
    return sample.replace(re, (m) => '*'.repeat(Math.max(3, m.length)))
  } catch {
    return sample
  }
}

const RedactionRules: React.FC = () => {
  const { theme } = useTheme()
  const insets = useSafeAreaInsets()
  const navigation = useNavigation<any>()

  const [applyGlobally, setApplyGlobally] = React.useState(true)
  const [rules, setRules] = React.useState<RedactionRule[]>([
    { id: 'r-1', pattern: '\\b\\d{16}\\b', enabled: true, description: 'Credit card number', sample: '4111111111111111' },
    { id: 'r-2', pattern: '[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\\.[A-Za-z]{2,}', enabled: true, description: 'Email address', sample: 'user@example.com' },
  ])
  const [offline, setOffline] = React.useState(false)
  const [queued, setQueued] = React.useState<Record<string, boolean>>({})

  const [open, setOpen] = React.useState(false)
  const [editing, setEditing] = React.useState<RedactionRule | null>(null)
  const [draftPattern, setDraftPattern] = React.useState('')
  const [draftDesc, setDraftDesc] = React.useState('')
  const [draftSample, setDraftSample] = React.useState('')

  const editRule = (rule?: RedactionRule) => {
    if (rule) {
      setEditing(rule)
      setDraftPattern(rule.pattern)
      setDraftDesc(rule.description || '')
      setDraftSample(rule.sample || '')
    } else {
      setEditing(null)
      setDraftPattern('')
      setDraftDesc('')
      setDraftSample('')
    }
    setOpen(true)
  }

  const saveRule = () => {
    if (!draftPattern.trim()) return
    if (editing) {
      setRules((arr) => arr.map((r) => (r.id === editing.id ? { ...r, pattern: draftPattern, description: draftDesc || undefined, sample: draftSample || undefined } : r)))
      track('knowledge.redaction', { action: 'edit' })
    } else {
      const id = `r-${Date.now()}`
      setRules((arr) => [{ id, pattern: draftPattern, enabled: true, description: draftDesc || undefined, sample: draftSample || undefined }, ...arr])
      track('knowledge.redaction', { action: 'add' })
    }
    if (offline) {
      const id = editing?.id || `r-${Date.now()}`
      setQueued((q) => ({ ...q, [id]: true }))
      setTimeout(() => setQueued((q) => ({ ...q, [id]: false })), 1500)
    }
    setOpen(false)
  }

  const toggle = (id: string, v: boolean) => {
    setRules((arr) => arr.map((r) => (r.id === id ? { ...r, enabled: v } : r)))
    track('knowledge.redaction', { action: 'toggle' })
    if (offline) {
      setQueued((q) => ({ ...q, [id]: true }))
      setTimeout(() => setQueued((q) => ({ ...q, [id]: false })), 1500)
    }
  }

  return (
    <SafeAreaView style={{ flex: 1, backgroundColor: theme.color.background }}>
      {/* Header */}
      <View style={{ paddingHorizontal: 16, paddingTop: insets.top + 8, paddingBottom: 12, borderBottomWidth: 1, borderBottomColor: theme.color.border }}>
        <View style={{ flexDirection: 'row', alignItems: 'center', justifyContent: 'space-between' }}>
          <TouchableOpacity onPress={() => navigation.goBack()} accessibilityLabel="Back" accessibilityRole="button" hitSlop={{ top: 8, bottom: 8, left: 8, right: 8 }} style={{ padding: 8 }}>
            <Text style={{ color: theme.color.primary, fontWeight: '600' }}>{'< Back'}</Text>
          </TouchableOpacity>
          <Text style={{ color: theme.color.foreground, fontSize: 18, fontWeight: '700' }}>Redaction Rules</Text>
          <TouchableOpacity onPress={() => editRule()} accessibilityLabel="Add rule" accessibilityRole="button" hitSlop={{ top: 8, bottom: 8, left: 8, right: 8 }} style={{ padding: 8 }}>
            <Text style={{ color: theme.color.primary, fontWeight: '700' }}>+ Rule</Text>
          </TouchableOpacity>
        </View>
      </View>

      {/* Global toggle */}
      <View style={{ padding: 16, borderBottomWidth: 1, borderBottomColor: theme.color.border }}>
        <View style={{ flexDirection: 'row', alignItems: 'center', justifyContent: 'space-between' }}>
          <Text style={{ color: theme.color.mutedForeground }}>Apply redaction in answers</Text>
          <Switch value={applyGlobally} onValueChange={(v) => { setApplyGlobally(v); if (offline) { setQueued((q) => ({ ...q, global: true })); setTimeout(() => setQueued((q) => ({ ...q, global: false })), 1500) } }} accessibilityLabel="Apply redaction globally" />
        </View>
        <View style={{ flexDirection: 'row', alignItems: 'center', gap: 8, marginTop: 8 }}>
          <TouchableOpacity onPress={() => setOffline((o) => !o)} accessibilityLabel="Toggle Offline" accessibilityRole="button" style={{ paddingHorizontal: 10, paddingVertical: 6, borderWidth: 1, borderColor: theme.color.border, borderRadius: 8 }}>
            <Text style={{ color: theme.color.mutedForeground }}>{offline ? 'Online' : 'Offline'}</Text>
          </TouchableOpacity>
          {queued['global'] ? <Text style={{ color: theme.color.mutedForeground, fontSize: 12 }}>⏱</Text> : null}
        </View>
        <TouchableOpacity onPress={() => navigation.navigate('CRM', { screen: 'PrivacyCenter' })} accessibilityLabel="Open Privacy Center" accessibilityRole="button" style={{ marginTop: 12, alignSelf: 'flex-start', paddingHorizontal: 12, paddingVertical: 8, borderWidth: 1, borderColor: theme.color.border, borderRadius: 12 }}>
          <Text style={{ color: theme.color.cardForeground, fontWeight: '600' }}>Open Security & Privacy Center</Text>
        </TouchableOpacity>
      </View>

      {/* Rules list */}
      <FlatList
        style={{ padding: 16 }}
        data={rules}
        keyExtractor={(r) => r.id}
        renderItem={({ item }) => (
          <View style={{ marginBottom: 8 }}>
            <RedactionRuleRow rule={item} onToggle={toggle} />
            <View style={{ flexDirection: 'row', gap: 8, marginTop: 6 }}>
              <TouchableOpacity onPress={() => editRule(item)} accessibilityLabel={`Edit ${item.pattern}`} accessibilityRole="button" style={{ paddingHorizontal: 10, paddingVertical: 6, borderWidth: 1, borderColor: theme.color.border, borderRadius: 8 }}>
                <Text style={{ color: theme.color.cardForeground }}>Edit</Text>
              </TouchableOpacity>
              {queued[item.id] ? <Text style={{ color: theme.color.mutedForeground, fontSize: 12 }}>⏱</Text> : null}
            </View>
          </View>
        )}
      />

      {/* Add/Edit modal */}
      <Modal visible={open} transparent animationType="fade" onRequestClose={() => setOpen(false)}>
        <View style={{ flex: 1, backgroundColor: 'rgba(0,0,0,0.4)', alignItems: 'center', justifyContent: 'center', padding: 16 }}>
          <View style={{ backgroundColor: theme.color.card, borderRadius: 16, padding: 16, borderWidth: 1, borderColor: theme.color.border, width: '90%' }}>
            <Text style={{ color: theme.color.cardForeground, fontWeight: '700', marginBottom: 8 }}>{editing ? 'Edit Rule' : 'New Rule'}</Text>
            <Text style={{ color: theme.color.mutedForeground, marginBottom: 6 }}>Pattern (regex)</Text>
            <TextInput value={draftPattern} onChangeText={setDraftPattern} placeholder="e.g., \\b\\d{16}\\b" placeholderTextColor={theme.color.placeholder} style={{ borderWidth: 1, borderColor: theme.color.border, borderRadius: 8, paddingHorizontal: 10, paddingVertical: 8, color: theme.color.cardForeground, marginBottom: 8 }} />
            <Text style={{ color: theme.color.mutedForeground, marginBottom: 6 }}>Description</Text>
            <TextInput value={draftDesc} onChangeText={setDraftDesc} placeholder="What does this mask?" placeholderTextColor={theme.color.placeholder} style={{ borderWidth: 1, borderColor: theme.color.border, borderRadius: 8, paddingHorizontal: 10, paddingVertical: 8, color: theme.color.cardForeground, marginBottom: 8 }} />
            <Text style={{ color: theme.color.mutedForeground, marginBottom: 6 }}>Sample</Text>
            <TextInput value={draftSample} onChangeText={setDraftSample} placeholder="4111111111111111" placeholderTextColor={theme.color.placeholder} style={{ borderWidth: 1, borderColor: theme.color.border, borderRadius: 8, paddingHorizontal: 10, paddingVertical: 8, color: theme.color.cardForeground, marginBottom: 8 }} />
            {!!draftSample && !!draftPattern && (
              <View style={{ borderWidth: 1, borderColor: theme.color.border, borderRadius: 8, padding: 10, marginBottom: 8 }}>
                <Text style={{ color: theme.color.mutedForeground }}>Preview:</Text>
                <Text style={{ color: theme.color.cardForeground }}>{maskSample(draftPattern, draftSample)}</Text>
              </View>
            )}
            <View style={{ flexDirection: 'row', justifyContent: 'flex-end', gap: 12 }}>
              <TouchableOpacity onPress={() => setOpen(false)} style={{ paddingHorizontal: 12, paddingVertical: 8 }}>
                <Text style={{ color: theme.color.mutedForeground }}>Cancel</Text>
              </TouchableOpacity>
              <TouchableOpacity onPress={saveRule} style={{ paddingHorizontal: 12, paddingVertical: 8 }}>
                <Text style={{ color: theme.color.primary, fontWeight: '700' }}>Save</Text>
              </TouchableOpacity>
            </View>
          </View>
        </View>
      </Modal>
    </SafeAreaView>
  )
}

export default RedactionRules


