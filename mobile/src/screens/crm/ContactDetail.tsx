import React from 'react'
import { SafeAreaView, View, TouchableOpacity, Text, TextInput, FlatList, Alert, Linking, I18nManager } from 'react-native'
import { useNavigation, useRoute } from '@react-navigation/native'
import { useSafeAreaInsets } from 'react-native-safe-area-context'
import { useTheme } from '../../providers/ThemeProvider'
import { Contact, ConsentState, InteractionSummary } from '../../types/crm'
import VipBadge from '../../components/crm/VipBadge'
import ConsentBadge from '../../components/crm/ConsentBadge'
import RedactionToggle from '../../components/crm/RedactionToggle'
import TagChip from '../../components/crm/TagChip'
import ValuePill from '../../components/crm/ValuePill'
import ChannelDot from '../../components/conversations/ChannelDot'
import { track } from '../../lib/analytics'

const mask = (s?: string, enabled?: boolean) => {
  if (!s) return '—'
  if (!enabled) return s
  return s.replace(/./g, '•')
}

const demoContact = (id: string): Contact => ({
  id,
  name: 'Sarah Johnson',
  email: 'sarah.johnson@example.com',
  phone: '+1-555-123-4567',
  channels: ['whatsapp', 'email'],
  tags: ['Enterprise', 'VIP', 'Billing'],
  vip: true,
  lastInteractionTs: Date.now() - 1000 * 60 * 60 * 5,
  lifetimeValue: 15750,
  consent: 'granted',
})

const demoActivity: InteractionSummary[] = [
  { conversationId: 'c-1', lastMessageSnippet: 'Refund processed, confirmation sent.', lastUpdatedTs: Date.now() - 1000 * 60 * 15, channel: 'email', intentTags: ['billing'] },
  { conversationId: 'c-2', lastMessageSnippet: 'Tracking shared via WhatsApp.', lastUpdatedTs: Date.now() - 1000 * 60 * 60 * 26, channel: 'whatsapp', intentTags: ['order'] },
]

export const ContactDetail: React.FC = () => {
  const navigation = useNavigation<any>()
  const route = useRoute<any>()
  const { theme } = useTheme()
  const insets = useSafeAreaInsets()

  const id: string = route.params?.id || 'ct-1'
  const [contact, setContact] = React.useState<Contact>(() => demoContact(id))
  const [redact, setRedact] = React.useState<boolean>(!!contact.piiRedacted)
  const [tab, setTab] = React.useState<'profile' | 'activity' | 'notes'>('profile')
  const [noteInput, setNoteInput] = React.useState('')
  const [notes, setNotes] = React.useState<string[]>([])

  const call = () => { if (contact.phone) Linking.openURL(`tel:${contact.phone}`) }
  const email = () => { if (contact.email) Linking.openURL(`mailto:${contact.email}`) }
  const startConversation = () => {
    const existingId = demoActivity[0]?.conversationId
    if (existingId) {
      navigation.navigate('Conversations', { screen: 'ConversationThread', params: { id: existingId } })
    } else {
      navigation.navigate('Conversations', { screen: 'Conversations', params: { prefill: contact.name } })
    }
  }

  return (
    <SafeAreaView style={{ flex: 1, backgroundColor: theme.color.background }}>
      {/* Header */}
      <View style={{ paddingHorizontal: 16, paddingTop: insets.top + 8, paddingBottom: 12, borderBottomWidth: 1, borderBottomColor: theme.color.border }}>
        <View style={{ flexDirection: 'row', alignItems: 'center', justifyContent: 'space-between' }}>
          <TouchableOpacity onPress={() => navigation.goBack()} accessibilityLabel="Back" accessibilityRole="button" style={{ padding: 8 }}>
            <Text style={{ color: theme.color.primary, fontWeight: '600' }}>{'< Back'}</Text>
          </TouchableOpacity>
          <View style={{ flexDirection: 'row', gap: 8 }}>
            <TouchableOpacity onPress={() => Alert.alert('Edit')} accessibilityLabel="Edit" accessibilityRole="button" style={{ padding: 8 }}>
              <Text style={{ color: theme.color.primary, fontWeight: '600' }}>Edit</Text>
            </TouchableOpacity>
            <TouchableOpacity onPress={() => Alert.alert('Merge')} accessibilityLabel="Merge" accessibilityRole="button" style={{ padding: 8 }}>
              <Text style={{ color: theme.color.primary, fontWeight: '600' }}>Merge</Text>
            </TouchableOpacity>
            <TouchableOpacity onPress={() => Alert.alert('Delete', 'Are you sure?', [{ text: 'Cancel' }, { text: 'Delete', style: 'destructive' }])} accessibilityLabel="Delete" accessibilityRole="button" style={{ padding: 8 }}>
              <Text style={{ color: theme.color.error, fontWeight: '600' }}>Delete</Text>
            </TouchableOpacity>
            <TouchableOpacity onPress={() => { track('crm.delete_request', { id: contact.id }); navigation.navigate('Security', { screen: 'DeletionRequests' , params: { prefill: { subject: 'contact', refId: contact.id } } }) }} accessibilityLabel="Request Deletion" accessibilityRole="button" style={{ padding: 8 }}>
              <Text style={{ color: theme.color.mutedForeground, fontWeight: '600' }}>Request Deletion</Text>
            </TouchableOpacity>
            <TouchableOpacity onPress={() => navigation.navigate('CRM', { screen: 'PrivacyCenter' })} accessibilityLabel="Privacy Center" accessibilityRole="button" style={{ padding: 8 }}>
              <Text style={{ color: theme.color.cardForeground, fontWeight: '600' }}>Privacy Center</Text>
            </TouchableOpacity>
          </View>
        </View>
        <View style={{ flexDirection: 'row', alignItems: 'center', gap: 8, marginTop: 8 }}>
          <Text style={{ color: theme.color.foreground, fontSize: 18, fontWeight: '700', textAlign: I18nManager.isRTL ? 'right' : 'left' }}>{contact.name}</Text>
          <VipBadge active={!!contact.vip} />
          <ConsentBadge state={contact.consent} />
          <RedactionToggle enabled={redact} onToggle={() => setRedact((v) => !v)} />
        </View>
      </View>

      {/* Tabs */}
      <View style={{ flexDirection: 'row', gap: 8, paddingHorizontal: 16, paddingVertical: 8 }}>
        {(['profile', 'activity', 'notes'] as const).map((t) => (
          <TouchableOpacity key={t} onPress={() => setTab(t)} style={{ paddingHorizontal: 12, paddingVertical: 8, borderRadius: 12, borderWidth: 1, borderColor: tab === t ? theme.color.primary : theme.color.border }}>
            <Text style={{ color: tab === t ? theme.color.primary : theme.color.mutedForeground, fontWeight: '600' }}>{t.toUpperCase()}</Text>
          </TouchableOpacity>
        ))}
      </View>

      {/* Body */}
      {tab === 'profile' && (
        <View style={{ paddingHorizontal: 16, paddingVertical: 8, gap: 12 }}>
          <View style={{ flexDirection: 'row', alignItems: 'center', gap: 8 }}>
            {contact.channels.map((ch, idx) => <ChannelDot key={`${ch}-${idx}`} channel={ch} />)}
          </View>
          <Text style={{ color: theme.color.mutedForeground }}>Email: {mask(contact.email, redact)}</Text>
          <Text style={{ color: theme.color.mutedForeground }}>Phone: {mask(contact.phone, redact)}</Text>
          <ValuePill value={contact.lifetimeValue} />
          <View style={{ flexDirection: 'row', flexWrap: 'wrap', gap: 8 }}>
            {contact.tags.map((t) => <TagChip key={t} label={t} />)}
          </View>
          <View style={{ flexDirection: 'row', gap: 12, marginTop: 8 }}>
            <TouchableOpacity onPress={call} disabled={!contact.phone} style={{ paddingHorizontal: 12, paddingVertical: 8, borderRadius: 12, borderWidth: 1, borderColor: theme.color.border }}>
              <Text style={{ color: contact.phone ? theme.color.cardForeground : theme.color.mutedForeground }}>Call</Text>
            </TouchableOpacity>
            <TouchableOpacity onPress={email} disabled={!contact.email} style={{ paddingHorizontal: 12, paddingVertical: 8, borderRadius: 12, borderWidth: 1, borderColor: theme.color.border }}>
              <Text style={{ color: contact.email ? theme.color.cardForeground : theme.color.mutedForeground }}>Email</Text>
            </TouchableOpacity>
            <TouchableOpacity onPress={startConversation} style={{ paddingHorizontal: 12, paddingVertical: 8, borderRadius: 12, borderWidth: 1, borderColor: theme.color.border }}>
              <Text style={{ color: theme.color.primary, fontWeight: '600' }}>Start Conversation</Text>
            </TouchableOpacity>
          </View>
        </View>
      )}

      {tab === 'activity' && (
        <FlatList
          data={demoActivity}
          keyExtractor={(i) => i.conversationId}
          renderItem={({ item }) => (
            <TouchableOpacity onPress={() => navigation.navigate('Conversations', { selectedId: item.conversationId })} style={{ padding: 16, borderBottomWidth: 1, borderBottomColor: theme.color.border }} accessibilityLabel={`Open conversation ${item.conversationId}`} accessibilityRole="button">
              <Text style={{ color: theme.color.cardForeground, fontWeight: '600', marginBottom: 4 }}>{item.channel.toUpperCase()}</Text>
              <Text style={{ color: theme.color.mutedForeground }}>{item.lastMessageSnippet}</Text>
            </TouchableOpacity>
          )}
          contentContainerStyle={{ paddingBottom: 12 }}
        />
      )}

      {/* Consent history */}
      <View style={{ paddingHorizontal: 16, paddingVertical: 8 }}>
        <Text style={{ color: theme.color.cardForeground, fontWeight: '700', marginBottom: 6 }}>Consent History</Text>
        {[{ ts: Date.now() - 1000 * 60 * 60 * 24 * 10, state: 'granted' as ConsentState }, { ts: Date.now() - 1000 * 60 * 60 * 24 * 2, state: 'withdrawn' as ConsentState }].map((e, idx) => (
          <View key={idx} style={{ flexDirection: 'row', alignItems: 'center', gap: 8, marginBottom: 6 }}>
            <ConsentBadge state={e.state} />
            <Text style={{ color: theme.color.mutedForeground }}>{new Date(e.ts).toLocaleString()}</Text>
          </View>
        ))}
      </View>

      {tab === 'notes' && (
        <View style={{ paddingHorizontal: 16, paddingVertical: 8, gap: 12 }}>
          <View style={{ borderWidth: 1, borderColor: theme.color.border, borderRadius: 12, backgroundColor: theme.color.card }}>
            <TextInput
              value={noteInput}
              onChangeText={setNoteInput}
              placeholder="Add a note"
              placeholderTextColor={theme.color.placeholder}
              style={{ paddingHorizontal: 12, paddingVertical: 10, color: theme.color.cardForeground }}
              multiline
            />
          </View>
          <TouchableOpacity onPress={() => { if (noteInput.trim()) { setNotes((n) => [noteInput.trim(), ...n]); setNoteInput('') } }} style={{ alignSelf: 'flex-end', paddingHorizontal: 12, paddingVertical: 8, borderRadius: 12, borderWidth: 1, borderColor: theme.color.border }}>
            <Text style={{ color: theme.color.primary, fontWeight: '600' }}>Add Note</Text>
          </TouchableOpacity>
          {notes.map((n, idx) => (
            <View key={idx} style={{ padding: 12, borderWidth: 1, borderColor: theme.color.border, borderRadius: 12 }}>
              <Text style={{ color: theme.color.cardForeground }}>{n}</Text>
            </View>
          ))}
        </View>
      )}
    </SafeAreaView>
  )
}

export default ContactDetail


