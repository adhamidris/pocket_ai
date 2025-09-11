import React from 'react'
import { SafeAreaView, View, TextInput, TouchableOpacity, Text, ScrollView, FlatList } from 'react-native'
import { useNavigation } from '@react-navigation/native'
import { useSafeAreaInsets } from 'react-native-safe-area-context'
import { useTheme } from '../../providers/ThemeProvider'
import { Contact, Segment, SegmentRule, RuleOp, ConsentState } from '../../types/crm'
import { Channel } from '../../types/conversations'
import TagChip from '../../components/crm/TagChip'
import { track } from '../../lib/analytics'

const rand = (min: number, max: number) => Math.floor(Math.random() * (max - min + 1)) + min
const pick = <T,>(arr: T[]): T => arr[rand(0, arr.length - 1)]

const genContacts = (n = 200): Contact[] => {
  const names = ['Sarah Johnson', 'Michael Chen', 'Emma Rodriguez', 'David Park', 'Aisha Khan', 'Omar Ali', 'Liam Smith', 'Noah Brown', 'Ava Davis', 'Sophia Wilson']
  const tagsPool = ['Enterprise', 'Startup', 'VIP', 'Technical', 'Billing', 'Returns', 'Onboarding']
  const channels: Channel[] = ['whatsapp', 'instagram', 'facebook', 'web', 'email']
  const consents: ConsentState[] = ['granted', 'denied', 'unknown', 'withdrawn']
  const items: Contact[] = []
  for (let i = 0; i < n; i++) {
    const name = `${pick(names)} ${i + 1}`
    const email = Math.random() > 0.5 ? `${name.toLowerCase().replace(/\s+/g, '.')}@example.com` : undefined
    const phone = Math.random() > 0.5 ? `+1-555-${rand(100, 999)}-${rand(1000, 9999)}` : undefined
    const chs = Array.from(new Set([pick(channels), pick(channels), pick(channels)])).slice(0, rand(1, 3))
    const tags = Array.from(new Set([pick(tagsPool), pick(tagsPool), pick(tagsPool)])).slice(0, rand(1, 3))
    const vip = Math.random() > 0.85
    const last = Date.now() - rand(0, 1000 * 60 * 60 * 24 * 30)
    const ltv = Math.random() > 0.5 ? rand(100, 20000) : undefined
    const consent = pick(consents)
    items.push({ id: `ct-${i + 1}`, name, email, phone, channels: chs, tags, vip, lastInteractionTs: last, lifetimeValue: ltv, consent })
  }
  return items
}

const evalRule = (c: Contact, r: SegmentRule): boolean => {
  const v = r.value
  switch (r.field) {
    case 'tag':
      return r.op === 'contains' ? c.tags.includes(v) : r.op === 'is' ? c.tags.includes(v) : true
    case 'vip':
      return r.op === 'is' ? (!!c.vip) === !!v : r.op === 'is_not' ? (!!c.vip) !== !!v : true
    case 'consent':
      return r.op === 'is' ? c.consent === v : r.op === 'is_not' ? c.consent !== v : true
    case 'channel':
      return r.op === 'in' ? c.channels.includes(v) : r.op === 'is' ? c.channels.includes(v) : true
    case 'lastInteractionTs':
      if (!c.lastInteractionTs) return false
      return r.op === 'gt' ? c.lastInteractionTs > v : r.op === 'lt' ? c.lastInteractionTs < v : true
    case 'lifetimeValue':
      return r.op === 'gt' ? (c.lifetimeValue || 0) > v : r.op === 'lt' ? (c.lifetimeValue || 0) < v : true
    default:
      return true
  }
}

const evalSegment = (c: Contact, rules: SegmentRule[]): boolean => rules.every((r) => evalRule(c, r))

const SegmentsScreen: React.FC = () => {
  const navigation = useNavigation<any>()
  const { theme } = useTheme()
  const insets = useSafeAreaInsets()

  const [contacts] = React.useState<Contact[]>(() => genContacts())
  const [segments, setSegments] = React.useState<Segment[]>([])
  const [name, setName] = React.useState('')
  const [color, setColor] = React.useState<string>('#6B7280')
  const [rules, setRules] = React.useState<SegmentRule[]>([])

  const addRule = () => setRules((r) => [...r, { field: 'tag', op: 'is', value: 'VIP' }])
  const updateRule = (idx: number, patch: Partial<SegmentRule>) => setRules((rs) => rs.map((r, i) => (i === idx ? { ...r, ...patch } : r)))
  const removeRule = (idx: number) => setRules((rs) => rs.filter((_, i) => i !== idx))

  const preview = React.useMemo(() => contacts.filter((c) => evalSegment(c, rules)).length, [contacts, rules])

  const save = () => {
    if (!name.trim()) return
    const seg: Segment = { id: `seg-${Date.now()}`, name: name.trim(), rules: rules.slice(), color }
    setSegments((s) => [seg, ...s])
    setName('')
    setRules([])
    track('crm.segment', { action: 'create', id: seg.id })
  }

  const applySegment = (seg: Segment) => {
    track('crm.segment', { action: 'apply', id: seg.id })
    navigation.navigate('CRM', { screen: 'CRMList', params: { segmentRules: seg.rules } })
  }

  const fields: SegmentRule['field'][] = ['tag', 'vip', 'consent', 'channel', 'lastInteractionTs', 'lifetimeValue']
  const ops: RuleOp[] = ['is', 'is_not', 'contains', 'gt', 'lt', 'in']

  return (
    <SafeAreaView style={{ flex: 1, backgroundColor: theme.color.background }}>
      <View style={{ paddingHorizontal: 16, paddingTop: insets.top + 8, paddingBottom: 12, borderBottomWidth: 1, borderBottomColor: theme.color.border }}>
        <View style={{ flexDirection: 'row', alignItems: 'center', justifyContent: 'space-between' }}>
          <TouchableOpacity onPress={() => navigation.goBack()} accessibilityLabel="Back" accessibilityRole="button" style={{ padding: 8 }}>
            <Text style={{ color: theme.color.primary, fontWeight: '600' }}>{'< Back'}</Text>
          </TouchableOpacity>
          <Text style={{ color: theme.color.foreground, fontSize: 18, fontWeight: '700' }}>Segments</Text>
          <View style={{ width: 64 }} />
        </View>
      </View>

      <ScrollView contentContainerStyle={{ padding: 16 }}>
        {/* Builder */}
        <Text style={{ color: theme.color.cardForeground, fontWeight: '700', marginBottom: 8 }}>Create Segment</Text>
        <View style={{ borderWidth: 1, borderColor: theme.color.border, borderRadius: 12, padding: 12, marginBottom: 12 }}>
          <TextInput value={name} onChangeText={setName} placeholder="Segment name" placeholderTextColor={theme.color.placeholder} style={{ color: theme.color.cardForeground, marginBottom: 8 }} />
          <View style={{ flexDirection: 'row', gap: 8, alignItems: 'center', marginBottom: 8 }}>
            <Text style={{ color: theme.color.mutedForeground }}>Color:</Text>
            {['#EF4444','#F59E0B','#10B981','#3B82F6','#6B7280'].map((c) => (
              <TouchableOpacity key={c} onPress={() => setColor(c)} style={{ width: 20, height: 20, borderRadius: 10, backgroundColor: c, borderWidth: color === c ? 2 : 1, borderColor: color === c ? theme.color.cardForeground : theme.color.border }} />
            ))}
          </View>
          {rules.map((r, idx) => (
            <View key={idx} style={{ flexDirection: 'row', alignItems: 'center', gap: 8, marginBottom: 8 }}>
              <TouchableOpacity onPress={() => updateRule(idx, { field: fields[(fields.indexOf(r.field) + 1) % fields.length] })} style={{ paddingHorizontal: 10, paddingVertical: 6, borderWidth: 1, borderColor: theme.color.border, borderRadius: 8 }}>
                <Text style={{ color: theme.color.cardForeground }}>{r.field}</Text>
              </TouchableOpacity>
              <TouchableOpacity onPress={() => updateRule(idx, { op: ops[(ops.indexOf(r.op) + 1) % ops.length] })} style={{ paddingHorizontal: 10, paddingVertical: 6, borderWidth: 1, borderColor: theme.color.border, borderRadius: 8 }}>
                <Text style={{ color: theme.color.cardForeground }}>{r.op}</Text>
              </TouchableOpacity>
              <TextInput value={String(r.value ?? '')} onChangeText={(t) => updateRule(idx, { value: t })} placeholder="value" placeholderTextColor={theme.color.placeholder} style={{ flex: 1, color: theme.color.cardForeground, borderBottomWidth: 1, borderBottomColor: theme.color.border }} />
              <TouchableOpacity onPress={() => removeRule(idx)} style={{ paddingHorizontal: 10, paddingVertical: 6, borderWidth: 1, borderColor: theme.color.border, borderRadius: 8 }}>
                <Text style={{ color: theme.color.error }}>Remove</Text>
              </TouchableOpacity>
            </View>
          ))}
          <View style={{ flexDirection: 'row', justifyContent: 'space-between', marginTop: 8 }}>
            <TouchableOpacity onPress={addRule} style={{ paddingHorizontal: 12, paddingVertical: 8, borderRadius: 12, borderWidth: 1, borderColor: theme.color.border }}>
              <Text style={{ color: theme.color.cardForeground, fontWeight: '600' }}>Add Rule</Text>
            </TouchableOpacity>
            <Text style={{ color: theme.color.mutedForeground }}>Preview: {preview}</Text>
            <TouchableOpacity onPress={save} style={{ paddingHorizontal: 12, paddingVertical: 8, borderRadius: 12, borderWidth: 1, borderColor: theme.color.border }}>
              <Text style={{ color: theme.color.primary, fontWeight: '700' }}>Save</Text>
            </TouchableOpacity>
          </View>
        </View>

        {/* Segments List */}
        <Text style={{ color: theme.color.cardForeground, fontWeight: '700', marginBottom: 8 }}>Saved Segments</Text>
        {segments.length === 0 ? (
          <Text style={{ color: theme.color.mutedForeground }}>No segments yet.</Text>
        ) : (
          <FlatList
            data={segments}
            keyExtractor={(s) => s.id}
            renderItem={({ item }) => (
              <View style={{ padding: 12, borderWidth: 1, borderColor: theme.color.border, borderRadius: 12, marginBottom: 8 }}>
                <View style={{ flexDirection: 'row', alignItems: 'center', justifyContent: 'space-between' }}>
                  <View style={{ flexDirection: 'row', alignItems: 'center', gap: 8 }}>
                    <View style={{ width: 10, height: 10, borderRadius: 5, backgroundColor: item.color || '#6B7280' }} />
                    <Text style={{ color: theme.color.cardForeground, fontWeight: '600' }}>{item.name}</Text>
                  </View>
                  <TouchableOpacity onPress={() => applySegment(item)} style={{ paddingHorizontal: 10, paddingVertical: 6, borderWidth: 1, borderColor: theme.color.border, borderRadius: 8 }}>
                    <Text style={{ color: theme.color.primary, fontWeight: '700' }}>Apply</Text>
                  </TouchableOpacity>
                </View>
              </View>
            )}
          />
        )}
      </ScrollView>
    </SafeAreaView>
  )
}

export default SegmentsScreen


