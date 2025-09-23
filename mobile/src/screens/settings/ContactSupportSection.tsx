import React, { useMemo, useState } from 'react'
import { View, Text, TouchableOpacity, Platform, Linking, TextInput } from 'react-native'
import { useTheme } from '../../providers/ThemeProvider'
import { Card } from '../../components/ui/Card'
import { Input } from '../../components/ui/Input'
import { Button } from '../../components/ui/Button'
import { Modal } from '../../components/ui/Modal'
import { Tag, Send, X, Paperclip } from 'lucide-react-native'

const categories = [
  'Account',
  'Billing',
  'Technical',
  'Agents',
  'Conversations/CRM',
  'Other'
] as const

export const ContactSupportSection: React.FC = () => {
  const { theme } = useTheme()

  const [form, setForm] = useState({
    category: 'Technical' as typeof categories[number],
    subject: '',
    message: '',
  })
  const [showCatModal, setShowCatModal] = useState(false)
  const [attachments, setAttachments] = useState<{ name: string; uri?: string }[]>([])

  const appVersion = useMemo(() => {
    try {
      const appConfig = require('../../../app.json')
      return appConfig?.expo?.version || '1.0.0'
    } catch {
      return '1.0.0'
    }
  }, [])

  const buildDiagnostics = () => {
    const parts = [
      `Platform: ${Platform.OS}`,
      `App Version: ${appVersion}`,
    ]
    return parts.join('\n')
  }

  const openEmailSimple = (subject?: string, body?: string) => {
    const subj = encodeURIComponent(subject || 'Support Request')
    const attachLines = attachments.length > 0
      ? `\n\nAttachments (URIs):\n${attachments.map(a => `• ${a.name}${a.uri ? ` — ${a.uri}` : ''}`).join('\n')}`
      : ''
    const diag = `\n\n---\n${buildDiagnostics()}`
    const fullBody = encodeURIComponent((body || 'Describe your issue here.') + attachLines + diag)
    const url = `mailto:support@pocket.ai?subject=${subj}&body=${fullBody}`
    Linking.openURL(url).catch(() => {})
  }

  const submitForm = async () => {
    const subj = `[${form.category}] ${form.subject || 'No subject'}`
    const body = form.message || 'No message'
    // Try to use Expo Mail Composer with attachments if available
    let MailComposer: any = null
    try { MailComposer = require('expo-mail-composer') } catch {}
    try {
      if (MailComposer?.isAvailableAsync && (await MailComposer.isAvailableAsync())) {
        const files = attachments.map(a => a.uri).filter(Boolean)
        await MailComposer.composeAsync({
          recipients: ['support@pocket.ai'],
          subject: subj,
          body: `${body}\n\n---\n${buildDiagnostics()}`,
          attachments: files,
        })
        return
      }
    } catch {}
    // Fallback to simple mailto with inline listing
    openEmailSimple(subj, body)
  }

  const pickAttachments = async () => {
    let DocumentPicker: any = null
    try { DocumentPicker = require('expo-document-picker') } catch {}
    if (!DocumentPicker?.getDocumentAsync) {
      // Silent no-op if module not present; could alert user here
      return
    }
    try {
      const res = await DocumentPicker.getDocumentAsync({ multiple: true, copyToCacheDirectory: true })
      // Handle both old and new API shapes
      const assets = (res?.assets || (res?.type !== 'cancel' ? [res] : [])) as any[]
      const picked = (assets || []).map(a => ({ name: a.name || 'attachment', uri: a.uri }))
      if (picked.length) setAttachments(prev => [...prev, ...picked])
    } catch {}
  }

  //

  return (
    <View>
      {/* Communication Form */}
      <Card variant="flat" style={{ backgroundColor: theme.dark ? (theme.color.secondary as any) : (theme.color.accent as any), paddingHorizontal: 14, paddingVertical: 12 }}>

        <Text style={{ color: theme.color.foreground, fontSize: 14, fontWeight: '600', marginBottom: 8 }}>Category</Text>
        <TouchableOpacity
          onPress={() => setShowCatModal(true)}
          style={{
            flexDirection: 'row', alignItems: 'center', justifyContent: 'space-between',
            backgroundColor: theme.color.card,
            borderRadius: theme.radius.md, paddingHorizontal: 12, paddingVertical: 10, marginBottom: 12
          }}
        >
          <View style={{ flexDirection: 'row', alignItems: 'center', gap: 10 }}>
            <Tag size={16} color={theme.color.mutedForeground as any} />
            <Text style={{ color: theme.color.cardForeground, fontSize: 14, fontWeight: '600' }}>{form.category}</Text>
          </View>
          <Text style={{ color: theme.color.mutedForeground, fontSize: 12 }}>Change</Text>
        </TouchableOpacity>

        <Input
          label="Subject"
          value={form.subject}
          onChangeText={(t) => setForm(prev => ({ ...prev, subject: t }))}
          borderless
          surface="secondary"
          placeholder="Short description"
        />

        

        {/* Simple text area using a TextInput for message */}
        <View style={{ marginBottom: 16 }}>
          <Text style={{ color: theme.color.foreground, fontSize: 14, fontWeight: '600', marginBottom: 8 }}>Message</Text>
          <View style={{
            backgroundColor: theme.color.card,
            borderRadius: theme.radius.md,
            borderWidth: 0,
            borderColor: 'transparent',
            paddingHorizontal: 12,
            paddingVertical: 10,
          }}>
            <TextInput
              value={form.message}
              onChangeText={(t) => setForm(prev => ({ ...prev, message: t }))}
              placeholder="Describe your issue…"
              placeholderTextColor={theme.color.mutedForeground as any}
              style={{ color: theme.color.foreground, fontSize: 16, minHeight: 120, textAlignVertical: 'top' as any }}
              multiline
              numberOfLines={6}
            />
          </View>
        </View>

        {/* Attachments */}
        <View style={{ marginBottom: 12 }}>
          <View style={{ flexDirection: 'row', justifyContent: 'space-between', alignItems: 'center', marginBottom: 8 }}>
            <Text style={{ color: theme.color.cardForeground, fontSize: 14, fontWeight: '500' }}>Attachments</Text>
            <TouchableOpacity
              onPress={pickAttachments}
              style={{
                width: 36,
                height: 36,
                borderRadius: 18,
                backgroundColor: theme.dark ? theme.color.secondary : theme.color.card,
                alignItems: 'center',
                justifyContent: 'center',
              }}
            >
              <Paperclip size={16} color={theme.color.mutedForeground as any} />
            </TouchableOpacity>
          </View>
          {attachments.length > 0 && (
            <View style={{ gap: 6 }}>
              {attachments.map((a, idx) => (
                <View key={`${a.name}-${idx}`} style={{ flexDirection: 'row', alignItems: 'center', justifyContent: 'space-between', backgroundColor: theme.color.card, borderRadius: theme.radius.md, paddingHorizontal: 10, paddingVertical: 8, borderWidth: 1, borderColor: theme.color.border }}>
                  <Text style={{ color: theme.color.cardForeground, fontSize: 13, flex: 1 }} numberOfLines={1}>
                    {a.name}
                  </Text>
                  <TouchableOpacity onPress={() => setAttachments(prev => prev.filter((_, i) => i !== idx))} style={{ padding: 6 }}>
                    <X size={14} color={theme.color.mutedForeground as any} />
                  </TouchableOpacity>
                </View>
              ))}
            </View>
          )}
        </View>

        <Button
          title="Submit"
          variant="default"
          size="md"
          fullWidth
          onPress={submitForm}
          iconLeft={<Send size={16} color={'#fff'} />}
        />
      </Card>

      {/* Category Modal */}
      <Modal visible={showCatModal} onClose={() => setShowCatModal(false)} title="Select category" size="sm" autoHeight>
        <View style={{ gap: 6 }}>
          {categories.map((c) => {
            const selected = form.category === c
            return (
              <TouchableOpacity
                key={c}
                onPress={() => { setForm(prev => ({ ...prev, category: c })); setShowCatModal(false) }}
                style={{ flexDirection: 'row', alignItems: 'center', justifyContent: 'space-between', paddingVertical: 10, paddingHorizontal: 12, borderRadius: theme.radius.md, backgroundColor: selected ? (theme.dark ? theme.color.secondary : theme.color.card) : 'transparent' }}
              >
                <Text style={{ color: theme.color.cardForeground, fontSize: 14, fontWeight: '600' }}>{c}</Text>
                {selected && <Text style={{ color: theme.color.primary, fontSize: 12, fontWeight: '700' }}>Selected</Text>}
              </TouchableOpacity>
            )
          })}
        </View>
      </Modal>
    </View>
  )
}
