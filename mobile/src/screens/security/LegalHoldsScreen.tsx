import React from 'react'
import { SafeAreaView, View, Text, TouchableOpacity, ScrollView, Modal, TextInput, Switch } from 'react-native'
import { useSafeAreaInsets } from 'react-native-safe-area-context'
import { useTheme } from '../../providers/ThemeProvider'
import { useNavigation } from '@react-navigation/native'
import { LegalHold, AuditEvent } from '../../types/security'

const now = () => Date.now()

const gen = (): LegalHold[] => ([{ id: 'lh-1', scope: 'contact', refId: 'c-100', reason: 'Legal review', active: true, createdAt: now() - 86400000, createdBy: 'me' }])

const Chip: React.FC<{ label: string; active?: boolean; onPress: () => void }>=({ label, active, onPress }) => {
  const { theme } = useTheme()
  return (
    <TouchableOpacity onPress={onPress} accessibilityRole="button" accessibilityLabel={label} style={{ paddingHorizontal: 10, paddingVertical: 6, borderRadius: 10, borderWidth: 1, borderColor: active ? theme.color.primary : theme.color.border }}>
      <Text style={{ color: active ? theme.color.primary : theme.color.mutedForeground, fontWeight: '600' }}>{label}</Text>
    </TouchableOpacity>
  )
}

const LegalHoldsScreen: React.FC = () => {
  const { theme } = useTheme()
  const insets = useSafeAreaInsets()
  const navigation = useNavigation<any>()

  const [items, setItems] = React.useState<LegalHold[]>(() => gen())
  const [editorOpen, setEditorOpen] = React.useState(false)
  const [scope, setScope] = React.useState<LegalHold['scope']>('contact')
  const [refId, setRefId] = React.useState('')
  const [reason, setReason] = React.useState('')

  const appendAudit = (evt: AuditEvent) => { console.log('audit', evt) }

  const add = () => {
    const item: LegalHold = { id: `lh-${now()}`, scope, refId: refId.trim() || undefined, reason: reason.trim() || '—', active: true, createdAt: now(), createdBy: 'me' }
    setItems((arr) => [item, ...arr])
    appendAudit({ id: `a-${now()}`, ts: now(), actor: 'me', action: 'legalhold.create', entityType: 'policy', entityId: item.id, details: `${item.scope} ${item.refId || ''}`, risk: 'low' })
    setEditorOpen(false)
  }

  const toggleActive = (id: string) => {
    setItems((arr) => arr.map((h) => h.id === id ? { ...h, active: !h.active } : h))
    const h = items.find((x) => x.id === id)
    if (h) appendAudit({ id: `a-${now()}`, ts: now(), actor: 'me', action: h.active ? 'legalhold.disable' : 'legalhold.enable', entityType: 'policy', entityId: id, details: `${h.scope} ${h.refId || ''}`, risk: 'low' })
  }

  return (
    <SafeAreaView style={{ flex: 1, backgroundColor: theme.color.background }}>
      <ScrollView contentContainerStyle={{ paddingBottom: 24 }}>
        {/* Header */}
        <View style={{ paddingHorizontal: 24, paddingTop: insets.top + 12, paddingBottom: 12 }}>
          <View style={{ flexDirection: 'row', alignItems: 'center', justifyContent: 'space-between' }}>
            <TouchableOpacity onPress={() => navigation.goBack()} accessibilityRole="button" accessibilityLabel="Back" style={{ paddingHorizontal: 12, paddingVertical: 8, borderRadius: 10, borderWidth: 1, borderColor: theme.color.border }}>
              <Text style={{ color: theme.color.primary, fontWeight: '600' }}>{'< Back'}</Text>
            </TouchableOpacity>
            <Text style={{ color: theme.color.foreground, fontSize: 20, fontWeight: '700' }}>Legal Holds</Text>
            <TouchableOpacity onPress={() => setEditorOpen(true)} accessibilityRole="button" accessibilityLabel="New Hold" style={{ paddingHorizontal: 12, paddingVertical: 8, borderRadius: 10, borderWidth: 1, borderColor: theme.color.border }}>
              <Text style={{ color: theme.color.cardForeground, fontWeight: '600' }}>New Hold</Text>
            </TouchableOpacity>
          </View>
        </View>

        {/* List */}
        <View style={{ paddingHorizontal: 24 }}>
          {items.map((h) => (
            <View key={h.id} style={{ paddingVertical: 10, borderBottomWidth: 1, borderBottomColor: theme.color.border }}>
              <View style={{ flexDirection: 'row', alignItems: 'center', justifyContent: 'space-between' }}>
                <Text style={{ color: theme.color.cardForeground, fontWeight: '600' }}>{h.scope.toUpperCase()} {h.refId || ''}</Text>
                <View style={{ flexDirection: 'row', alignItems: 'center', gap: 8 }}>
                  <Text style={{ color: theme.color.mutedForeground }}>{h.active ? 'Active' : 'Inactive'}</Text>
                  <Switch value={h.active} onValueChange={() => toggleActive(h.id)} accessibilityLabel="Toggle hold" />
                </View>
              </View>
              <Text style={{ color: theme.color.mutedForeground }}>{h.reason}</Text>
              <Text style={{ color: theme.color.mutedForeground, fontSize: 12 }}>Created {new Date(h.createdAt).toLocaleString()}</Text>
            </View>
          ))}
        </View>
      </ScrollView>

      {/* Editor */}
      <Modal visible={editorOpen} transparent animationType="slide" onRequestClose={() => setEditorOpen(false)}>
        <View style={{ flex: 1, backgroundColor: 'rgba(0,0,0,0.4)', justifyContent: 'flex-end' }}>
          <View style={{ backgroundColor: theme.color.card, borderTopLeftRadius: 16, borderTopRightRadius: 16, padding: 16, borderWidth: 1, borderColor: theme.color.border }}>
            <Text style={{ color: theme.color.cardForeground, fontWeight: '700', marginBottom: 8 }}>New Legal Hold</Text>
            <View style={{ flexDirection: 'row', flexWrap: 'wrap', gap: 8, marginBottom: 8 }}>
              {(['contact','conversation','all'] as const).map((s) => (
                <Chip key={s} label={s} active={scope === s} onPress={() => setScope(s)} />
              ))}
            </View>
            {scope !== 'all' && (
              <TextInput value={refId} onChangeText={setRefId} placeholder={`${scope === 'contact' ? 'Contact' : 'Conversation'} ID`} placeholderTextColor={theme.color.placeholder} accessibilityLabel="Reference ID" style={{ borderWidth: 1, borderColor: theme.color.border, borderRadius: 10, paddingHorizontal: 10, paddingVertical: 8, color: theme.color.cardForeground, marginBottom: 8 }} />
            )}
            <TextInput value={reason} onChangeText={setReason} placeholder="Reason" placeholderTextColor={theme.color.placeholder} accessibilityLabel="Reason" style={{ borderWidth: 1, borderColor: theme.color.border, borderRadius: 10, paddingHorizontal: 10, paddingVertical: 8, color: theme.color.cardForeground }} />
            <View style={{ flexDirection: 'row', justifyContent: 'flex-end', gap: 8, marginTop: 12 }}>
              <TouchableOpacity onPress={() => setEditorOpen(false)} accessibilityRole="button" accessibilityLabel="Cancel" style={{ paddingHorizontal: 12, paddingVertical: 8, borderRadius: 10, borderWidth: 1, borderColor: theme.color.border }}>
                <Text style={{ color: theme.color.cardForeground }}>Cancel</Text>
              </TouchableOpacity>
              <TouchableOpacity onPress={add} accessibilityRole="button" accessibilityLabel="Create hold" style={{ paddingHorizontal: 12, paddingVertical: 8, borderRadius: 10, backgroundColor: theme.color.primary }}>
                <Text style={{ color: theme.color.primaryForeground, fontWeight: '700' }}>Create</Text>
              </TouchableOpacity>
            </View>
          </View>
        </View>
      </Modal>
    </SafeAreaView>
  )
}

export default LegalHoldsScreen


