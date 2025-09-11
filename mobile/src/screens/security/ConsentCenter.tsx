import React from 'react'
import { SafeAreaView, View, Text, TouchableOpacity, ScrollView, Modal, TextInput } from 'react-native'
import { useSafeAreaInsets } from 'react-native-safe-area-context'
import { useTheme } from '../../providers/ThemeProvider'
import { useNavigation } from '@react-navigation/native'
import { ConsentTemplate, ConsentRecord, AuditEvent } from '../../types/security'
import ConsentTemplateRow from '../../components/security/ConsentTemplateRow'
import { track } from '../../lib/analytics'

const now = () => Date.now()

const genRecords = (): ConsentRecord[] => Array.from({ length: 20 }).map((_, i) => ({ id: `r-${i}`, contactId: `c-${i}`, templateId: 'ct-1', state: (['granted','denied','withdrawn'] as const)[i % 3], channel: (['web','email','whatsapp'] as const)[i % 3], ts: now() - i * 3600_000 }))

const Chip: React.FC<{ label: string; active?: boolean; onPress: () => void }>=({ label, active, onPress }) => {
  const { theme } = useTheme()
  return (
    <TouchableOpacity onPress={onPress} accessibilityRole="button" accessibilityLabel={label} style={{ paddingHorizontal: 10, paddingVertical: 6, borderRadius: 10, borderWidth: 1, borderColor: active ? theme.color.primary : theme.color.border }}>
      <Text style={{ color: active ? theme.color.primary : theme.color.mutedForeground, fontWeight: '600' }}>{label}</Text>
    </TouchableOpacity>
  )
}

const ConsentCenter: React.FC = () => {
  const { theme } = useTheme()
  const insets = useSafeAreaInsets()
  const navigation = useNavigation<any>()

  const [tab, setTab] = React.useState<'tpl'|'rec'>('tpl')
  const [templates, setTemplates] = React.useState<ConsentTemplate[]>([{ id: 'ct-1', name: 'Default', version: 1, languages: [{ code: 'en', text: 'We collect...' }], channels: ['web'], lastPublishedAt: now() - 86400000 }])
  const [records, setRecords] = React.useState<ConsentRecord[]>(() => genRecords())
  const [filterState, setFilterState] = React.useState<string | undefined>()
  const [filterChannel, setFilterChannel] = React.useState<string | undefined>()

  const [editorOpen, setEditorOpen] = React.useState(false)
  const [tplName, setTplName] = React.useState('New Template')
  const [tplLang, setTplLang] = React.useState('en')
  const [tplText, setTplText] = React.useState('Consent text...')
  const debouncer = React.useRef<{ t?: any; run: (fn: () => void) => void }>({ run(fn) { if (this.t) clearTimeout(this.t); this.t = setTimeout(fn, 200) as any } }).current
  const [tplChannels, setTplChannels] = React.useState<string[]>(['web'])

  const addTemplate = () => {
    const id = `ct-${now()}`
    const item: ConsentTemplate = { id, name: tplName.trim() || 'Untitled', version: 1, languages: [{ code: tplLang, text: tplText }], channels: tplChannels as any }
    setTemplates((arr) => [item, ...arr])
    setEditorOpen(false)
  }

  const publish = (id: string) => {
    setTemplates((arr) => arr.map((t) => t.id === id ? { ...t, lastPublishedAt: now(), version: t.version + 1 } : t))
    // Append audit (UI stub): in a full app, push to a shared audit store
    const evt: AuditEvent = { id: `a-${now()}`, ts: now(), actor: 'me', action: 'policy.update', entityType: 'policy', entityId: id, details: 'Consent template published', risk: 'low' }
    console.log('audit', evt)
    try { track('consent.publish') } catch {}
  }

  const filtered = records.filter((r) => (!filterState || r.state === filterState) && (!filterChannel || r.channel === filterChannel))

  return (
    <SafeAreaView style={{ flex: 1, backgroundColor: theme.color.background }}>
      <ScrollView contentContainerStyle={{ paddingBottom: 24 }}>
        <View style={{ paddingHorizontal: 24, paddingTop: insets.top + 12, paddingBottom: 12 }}>
          <View style={{ flexDirection: 'row', alignItems: 'center', justifyContent: 'space-between' }}>
            <TouchableOpacity onPress={() => navigation.goBack()} accessibilityRole="button" accessibilityLabel="Back" style={{ paddingHorizontal: 12, paddingVertical: 8, borderRadius: 10, borderWidth: 1, borderColor: theme.color.border }}>
              <Text style={{ color: theme.color.primary, fontWeight: '600' }}>{'< Back'}</Text>
            </TouchableOpacity>
            <Text style={{ color: theme.color.foreground, fontSize: 20, fontWeight: '700' }}>Consent Center</Text>
            <View style={{ width: 64 }} />
          </View>
        </View>

        <View style={{ paddingHorizontal: 24, marginBottom: 12 }}>
          <View style={{ flexDirection: 'row', gap: 8 }}>
            <Chip label="Templates" active={tab === 'tpl'} onPress={() => setTab('tpl')} />
            <Chip label="Records" active={tab === 'rec'} onPress={() => setTab('rec')} />
          </View>
        </View>

        {tab === 'tpl' ? (
          <View style={{ paddingHorizontal: 24 }}>
            <View style={{ flexDirection: 'row', justifyContent: 'flex-end', marginBottom: 8 }}>
              <TouchableOpacity onPress={() => setEditorOpen(true)} accessibilityRole="button" accessibilityLabel="New Template" style={{ paddingHorizontal: 12, paddingVertical: 8, borderRadius: 10, borderWidth: 1, borderColor: theme.color.border }}>
                <Text style={{ color: theme.color.cardForeground, fontWeight: '600' }}>New Template</Text>
              </TouchableOpacity>
            </View>
            <View>
              {templates.map((t) => (
                <ConsentTemplateRow key={t.id} tpl={t} onEdit={() => { setEditorOpen(true); setTplName(t.name); setTplLang(t.languages[0]?.code || 'en'); setTplText(t.languages[0]?.text || ''); setTplChannels(t.channels as any) }} onPublish={() => publish(t.id)} />
              ))}
            </View>
          </View>
        ) : (
          <View style={{ paddingHorizontal: 24 }}>
            <View style={{ flexDirection: 'row', gap: 8, marginBottom: 8 }}>
              {(['granted','denied','withdrawn'] as const).map((s) => (
                <Chip key={s} label={s} active={filterState === s} onPress={() => setFilterState(filterState === s ? undefined : s)} />
              ))}
              {(['web','email','whatsapp'] as const).map((ch) => (
                <Chip key={ch} label={ch} active={filterChannel === ch} onPress={() => setFilterChannel(filterChannel === ch ? undefined : ch)} />
              ))}
            </View>
            <View>
              {filtered.map((r) => (
                <TouchableOpacity key={r.id} onPress={() => navigation.navigate('CRM', { screen: 'ContactDetail', params: { id: r.contactId } })} accessibilityRole="button" accessibilityLabel={`Open contact ${r.contactId}`} style={{ paddingVertical: 10, borderBottomWidth: 1, borderBottomColor: theme.color.border }}>
                  <Text style={{ color: theme.color.cardForeground }}>{r.contactId} • {r.state} • {r.channel}</Text>
                  <Text style={{ color: theme.color.mutedForeground, fontSize: 12 }}>{new Date(r.ts).toLocaleString()}</Text>
                </TouchableOpacity>
              ))}
            </View>
          </View>
        )}
      </ScrollView>

      {/* Template Editor */}
      <Modal visible={editorOpen} transparent animationType="slide" onRequestClose={() => setEditorOpen(false)}>
        <View style={{ flex: 1, backgroundColor: 'rgba(0,0,0,0.4)', justifyContent: 'flex-end' }}>
          <View style={{ backgroundColor: theme.color.card, borderTopLeftRadius: 16, borderTopRightRadius: 16, padding: 16, borderWidth: 1, borderColor: theme.color.border }}>
            <Text style={{ color: theme.color.cardForeground, fontWeight: '700', marginBottom: 8 }}>New Template</Text>
            <View style={{ gap: 8 }}>
              <TextInput value={tplName} onChangeText={(v) => debouncer.run(() => setTplName(v))} placeholder="Name" placeholderTextColor={theme.color.placeholder} accessibilityLabel="Template name" style={{ borderWidth: 1, borderColor: theme.color.border, borderRadius: 10, paddingHorizontal: 10, paddingVertical: 8, color: theme.color.cardForeground, minHeight: 44 }} />
              <TextInput value={tplLang} onChangeText={(v) => debouncer.run(() => setTplLang(v))} placeholder="Language code (e.g., en)" placeholderTextColor={theme.color.placeholder} accessibilityLabel="Language code" style={{ borderWidth: 1, borderColor: theme.color.border, borderRadius: 10, paddingHorizontal: 10, paddingVertical: 8, color: theme.color.cardForeground, minHeight: 44 }} />
              <TextInput value={tplText} onChangeText={(v) => debouncer.run(() => setTplText(v))} placeholder="Consent text" placeholderTextColor={theme.color.placeholder} accessibilityLabel="Consent text" multiline style={{ borderWidth: 1, borderColor: theme.color.border, borderRadius: 10, paddingHorizontal: 10, paddingVertical: 8, color: theme.color.cardForeground, minHeight: 80 }} />
              <View style={{ flexDirection: 'row', flexWrap: 'wrap', gap: 8 }}>
                {(['web','email','whatsapp','instagram','facebook'] as const).map((ch) => (
                  <Chip key={ch} label={ch} active={tplChannels.includes(ch)} onPress={() => setTplChannels((arr) => arr.includes(ch) ? arr.filter(x => x !== ch) : [...arr, ch])} />
                ))}
              </View>
              <View style={{ flexDirection: 'row', justifyContent: 'flex-end', gap: 8 }}>
                <TouchableOpacity onPress={() => setEditorOpen(false)} accessibilityRole="button" accessibilityLabel="Cancel" style={{ paddingHorizontal: 12, paddingVertical: 8, borderRadius: 10, borderWidth: 1, borderColor: theme.color.border }}>
                  <Text style={{ color: theme.color.cardForeground }}>Cancel</Text>
                </TouchableOpacity>
                <TouchableOpacity onPress={addTemplate} accessibilityRole="button" accessibilityLabel="Save template" style={{ paddingHorizontal: 12, paddingVertical: 8, borderRadius: 10, backgroundColor: theme.color.primary }}>
                  <Text style={{ color: theme.color.primaryForeground, fontWeight: '700' }}>Save</Text>
                </TouchableOpacity>
              </View>
            </View>
          </View>
        </View>
      </Modal>
    </SafeAreaView>
  )
}

export default ConsentCenter


