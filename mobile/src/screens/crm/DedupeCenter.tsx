import React from 'react'
import { SafeAreaView, View, TouchableOpacity, Text, Alert, ScrollView } from 'react-native'
import { useNavigation } from '@react-navigation/native'
import { useSafeAreaInsets } from 'react-native-safe-area-context'
import { useTheme } from '../../providers/ThemeProvider'
import { Contact, DedupeCandidate } from '../../types/crm'
import TagChip from '../../components/crm/TagChip'
import ConsentBadge from '../../components/crm/ConsentBadge'
import VipBadge from '../../components/crm/VipBadge'
import { track } from '../../lib/analytics'

type CompareField = 'name' | 'email' | 'phone' | 'tags' | 'vip' | 'consent'

const makeContact = (id: string, overrides: Partial<Contact> = {}): Contact => ({
  id,
  name: 'Sarah Johnson',
  email: 'sarah@example.com',
  phone: '+1-555-123-4567',
  channels: ['whatsapp', 'email'],
  tags: ['Enterprise', 'VIP'],
  vip: true,
  lastInteractionTs: Date.now() - 1000 * 60 * 60 * 5,
  lifetimeValue: 12000,
  consent: 'granted',
  ...overrides,
})

const initialCandidates: Array<{
  candidate: DedupeCandidate
  master: Contact
  dup: Contact
}> = [
  {
    candidate: { masterId: 'ct-1', dupId: 'ct-101', reason: 'same_email', score: 0.96 },
    master: makeContact('ct-1', { name: 'Sarah Johnson', email: 'sarah@example.com' }),
    dup: makeContact('ct-101', { name: 'Sarah J.', email: 'sarah@example.com', tags: ['Billing'] }),
  },
  {
    candidate: { masterId: 'ct-2', dupId: 'ct-102', reason: 'name_similarity', score: 0.82 },
    master: makeContact('ct-2', { name: 'Michael Chen', email: 'mchen@startup.io', vip: false }),
    dup: makeContact('ct-102', { name: 'Mike Chen', phone: '+1-555-777-8888', vip: true }),
  },
]

const FieldRow: React.FC<{
  label: string
  left: React.ReactNode
  right: React.ReactNode
  pickLeft: boolean
  onToggle: () => void
}> = ({ label, left, right, pickLeft, onToggle }) => {
  return (
    <View style={{ flexDirection: 'row', alignItems: 'center', paddingVertical: 8 }}>
      <Text style={{ flex: 1, fontWeight: '600' }}>{label}</Text>
      <View style={{ flex: 3 }}>
        {left}
      </View>
      <TouchableOpacity onPress={onToggle} accessibilityLabel={`Pick ${pickLeft ? 'master' : 'duplicate'}`} accessibilityRole="button" style={{ paddingHorizontal: 12, paddingVertical: 6 }}>
        <Text style={{ fontWeight: '700' }}>{pickLeft ? '◉' : '○'}</Text>
      </TouchableOpacity>
      <View style={{ flex: 3 }}>
        {right}
      </View>
    </View>
  )
}

const DedupeCenter: React.FC = () => {
  const { theme } = useTheme()
  const insets = useSafeAreaInsets()
  const navigation = useNavigation<any>()

  const [items, setItems] = React.useState(initialCandidates)
  const [selected, setSelected] = React.useState(0)
  const [picks, setPicks] = React.useState<Record<CompareField, 'master' | 'dup'>>({
    name: 'master',
    email: 'master',
    phone: 'master',
    tags: 'master',
    vip: 'master',
    consent: 'master',
  })

  const current = items[selected]

  const toggle = (field: CompareField) => {
    setPicks((p) => ({ ...p, [field]: p[field] === 'master' ? 'dup' : 'master' }))
  }

  const merge = () => {
    // Produce merged contact (UI-only)
    const merged: Contact = {
      ...current.master,
      name: picks.name === 'master' ? current.master.name : current.dup.name,
      email: picks.email === 'master' ? current.master.email : current.dup.email,
      phone: picks.phone === 'master' ? current.master.phone : current.dup.phone,
      tags: picks.tags === 'master' ? current.master.tags : current.dup.tags,
      vip: picks.vip === 'master' ? current.master.vip : current.dup.vip,
      consent: picks.consent === 'master' ? current.master.consent : current.dup.consent,
    }
    console.log('Merged contact', merged)
    track('crm.merge', { masterId: current.candidate.masterId, dupId: current.candidate.dupId })
    Alert.alert('Merge completed', 'Duplicate removed and contact updated (UI-only).')
    // Remove candidate from list
    setItems((arr) => arr.filter((_, idx) => idx !== selected))
    setSelected(0)
  }

  return (
    <SafeAreaView style={{ flex: 1, backgroundColor: theme.color.background }}>
      {/* Header */}
      <View style={{ paddingHorizontal: 16, paddingTop: insets.top + 8, paddingBottom: 12, borderBottomWidth: 1, borderBottomColor: theme.color.border }}>
        <View style={{ flexDirection: 'row', alignItems: 'center', justifyContent: 'space-between' }}>
          <TouchableOpacity onPress={() => navigation.goBack()} accessibilityLabel="Back" accessibilityRole="button" style={{ padding: 8 }}>
            <Text style={{ color: theme.color.primary, fontWeight: '600' }}>{'< Back'}</Text>
          </TouchableOpacity>
          <Text style={{ color: theme.color.foreground, fontSize: 18, fontWeight: '700' }}>Dedupe Center</Text>
          <View style={{ width: 64 }} />
        </View>
      </View>

      <ScrollView contentContainerStyle={{ padding: 16 }}>
        {/* Candidate list */}
        <Text style={{ color: theme.color.mutedForeground, marginBottom: 8 }}>Candidates</Text>
        <View style={{ flexDirection: 'row', flexWrap: 'wrap', gap: 8, marginBottom: 12 }}>
          {items.map((it, idx) => (
            <TouchableOpacity key={`${it.candidate.masterId}-${it.candidate.dupId}`} onPress={() => setSelected(idx)} style={{ paddingHorizontal: 12, paddingVertical: 8, borderRadius: 12, borderWidth: 1, borderColor: idx === selected ? theme.color.primary : theme.color.border }}>
              <Text style={{ color: idx === selected ? theme.color.primary : theme.color.mutedForeground, fontWeight: '600' }}>{it.candidate.reason} ({Math.round(it.candidate.score * 100)}%)</Text>
            </TouchableOpacity>
          ))}
        </View>

        {current && (
          <View>
            <Text style={{ color: theme.color.foreground, fontSize: 16, fontWeight: '700', marginBottom: 8 }}>Review</Text>
            <FieldRow
              label="Name"
              left={<Text>{current.master.name}</Text>}
              right={<Text>{current.dup.name}</Text>}
              pickLeft={picks.name === 'master'}
              onToggle={() => toggle('name')}
            />
            <FieldRow
              label="Email"
              left={<Text>{current.master.email || '—'}</Text>}
              right={<Text>{current.dup.email || '—'}</Text>}
              pickLeft={picks.email === 'master'}
              onToggle={() => toggle('email')}
            />
            <FieldRow
              label="Phone"
              left={<Text>{current.master.phone || '—'}</Text>}
              right={<Text>{current.dup.phone || '—'}</Text>}
              pickLeft={picks.phone === 'master'}
              onToggle={() => toggle('phone')}
            />
            <FieldRow
              label="VIP"
              left={<VipBadge active={!!current.master.vip} />}
              right={<VipBadge active={!!current.dup.vip} />}
              pickLeft={picks.vip === 'master'}
              onToggle={() => toggle('vip')}
            />
            <FieldRow
              label="Consent"
              left={<ConsentBadge state={current.master.consent} />}
              right={<ConsentBadge state={current.dup.consent} />}
              pickLeft={picks.consent === 'master'}
              onToggle={() => toggle('consent')}
            />
            <View style={{ marginTop: 8 }}>
              <Text style={{ fontWeight: '600', marginBottom: 6 }}>Tags</Text>
              <View style={{ flexDirection: 'row', justifyContent: 'space-between' }}>
                <View style={{ flex: 1, marginRight: 8 }}>
                  <View style={{ flexDirection: 'row', flexWrap: 'wrap', gap: 6 }}>
                    {current.master.tags.map((t) => <TagChip key={`m-${t}`} label={t} />)}
                  </View>
                </View>
                <TouchableOpacity onPress={() => toggle('tags')} style={{ paddingHorizontal: 12, paddingVertical: 6 }}>
                  <Text style={{ fontWeight: '700' }}>{picks.tags === 'master' ? '◉' : '○'}</Text>
                </TouchableOpacity>
                <View style={{ flex: 1, marginLeft: 8 }}>
                  <View style={{ flexDirection: 'row', flexWrap: 'wrap', gap: 6 }}>
                    {current.dup.tags.map((t) => <TagChip key={`d-${t}`} label={t} />)}
                  </View>
                </View>
              </View>
            </View>

            <View style={{ alignItems: 'flex-end', marginTop: 16 }}>
              <TouchableOpacity onPress={merge} style={{ paddingHorizontal: 12, paddingVertical: 10, borderRadius: 12, borderWidth: 1, borderColor: theme.color.border }}>
                <Text style={{ color: theme.color.primary, fontWeight: '700' }}>Confirm Merge</Text>
              </TouchableOpacity>
            </View>
          </View>
        )}
      </ScrollView>
    </SafeAreaView>
  )
}

export default DedupeCenter


