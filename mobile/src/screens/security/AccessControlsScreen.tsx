import React from 'react'
import { SafeAreaView, View, Text, TouchableOpacity, ScrollView, Modal, TextInput, Alert } from 'react-native'
import { useSafeAreaInsets } from 'react-native-safe-area-context'
import { useTheme } from '../../providers/ThemeProvider'
import { useNavigation } from '@react-navigation/native'
import { IpRule, SessionPolicy, AuditEvent } from '../../types/security'
import IpRuleRow from '../../components/security/IpRuleRow'
import { track } from '../../lib/analytics'
import SessionPolicyEditor from '../../components/security/SessionPolicyEditor'

export interface AccessControlsScreenProps {
  rules: IpRule[]
  session: SessionPolicy
  onSave?: (rules: IpRule[], session: SessionPolicy, audit: AuditEvent[]) => void
}

const now = () => Date.now()

const isValidCidr = (cidr: string): boolean => {
  const parts = cidr.split('/')
  if (parts.length !== 2) return false
  const [ip, maskStr] = parts
  const mask = Number(maskStr)
  if (!Number.isInteger(mask) || mask < 0 || mask > 32) return false
  const octets = ip.split('.')
  if (octets.length !== 4) return false
  return octets.every((o) => {
    if (!/^\d+$/.test(o)) return false
    const n = Number(o)
    return n >= 0 && n <= 255
  })
}

const AccessControlsScreen: React.FC<AccessControlsScreenProps> = ({ rules, session, onSave }) => {
  const { theme } = useTheme()
  const insets = useSafeAreaInsets()
  const navigation = useNavigation<any>()

  const [items, setItems] = React.useState<IpRule[]>(rules)
  const [sess, setSess] = React.useState<SessionPolicy>(session)
  const [ops, setOps] = React.useState<AuditEvent[]>([])

  const [editorOpen, setEditorOpen] = React.useState(false)
  const [editIdx, setEditIdx] = React.useState<number | null>(null)
  const [cidr, setCidr] = React.useState('')
  const [note, setNote] = React.useState('')
  const debouncer = React.useRef<{ t?: any; run: (fn: () => void) => void }>({
    run(fn) { if (this.t) clearTimeout(this.t); this.t = setTimeout(fn, 200) as any }
  }).current
  const [error, setError] = React.useState<string | undefined>()

  const openNew = () => { setEditIdx(null); setCidr(''); setNote(''); setError(undefined); setEditorOpen(true) }
  const openEdit = (idx: number) => { const r = items[idx]; setEditIdx(idx); setCidr(r.cidr); setNote(r.note || ''); setError(undefined); setEditorOpen(true) }

  const saveRule = () => {
    if (!isValidCidr(cidr.trim())) { setError('Invalid CIDR (e.g., 192.168.0.0/16)'); return }
    if (editIdx == null) {
      const item: IpRule = { id: `ip-${now()}`, cidr: cidr.trim(), note: note.trim() || undefined, enabled: true }
      setItems((arr) => [item, ...arr])
      setOps((arr) => [...arr, { id: `a-${now()}`, ts: now(), actor: 'me', action: 'iprule.create', entityType: 'policy', entityId: item.id, details: `Allow ${item.cidr}`, risk: 'low' }])
      try { track('access.iprule', { action: 'add' }) } catch {}
    } else {
      const prev = items[editIdx]
      const next = { ...prev, cidr: cidr.trim(), note: note.trim() || undefined }
      setItems((arr) => arr.map((r, i) => i === editIdx ? next : r))
      setOps((arr) => [...arr, { id: `a-${now()}`, ts: now(), actor: 'me', action: 'iprule.update', entityType: 'policy', entityId: prev.id, details: `Edit ${prev.cidr} -> ${next.cidr}`, risk: 'low' }])
    }
    setEditorOpen(false)
  }

  const onToggle = (idx: number) => {
    setItems((arr) => arr.map((r, i) => i === idx ? { ...r, enabled: !r.enabled } : r))
    const r = items[idx]
    setOps((arr) => [...arr, { id: `a-${now()}`, ts: now(), actor: 'me', action: r.enabled ? 'iprule.disable' : 'iprule.enable', entityType: 'policy', entityId: r.id, details: r.cidr, risk: 'low' }])
    try { track('access.iprule', { action: 'toggle' }) } catch {}
  }

  const onDelete = (idx: number) => {
    const r = items[idx]
    setItems((arr) => arr.filter((_, i) => i !== idx))
    setOps((arr) => [...arr, { id: `a-${now()}`, ts: now(), actor: 'me', action: 'iprule.delete', entityType: 'policy', entityId: r.id, details: r.cidr, risk: 'low' }])
    try { track('access.iprule', { action: 'delete' }) } catch {}
  }

  const saveAll = () => {
    onSave ? onSave(items, sess, ops) : console.log('access.save', { items, sess, ops })
    try { track('access.session.save') } catch {}
    Alert.alert('Saved', 'Access controls updated (UI-only).')
    navigation.goBack()
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
            <Text style={{ color: theme.color.foreground, fontSize: 20, fontWeight: '700' }}>Access Controls</Text>
            <TouchableOpacity onPress={saveAll} accessibilityRole="button" accessibilityLabel="Save" style={{ paddingHorizontal: 12, paddingVertical: 8, borderRadius: 10, backgroundColor: theme.color.primary }}>
              <Text style={{ color: theme.color.primaryForeground, fontWeight: '700' }}>Save</Text>
            </TouchableOpacity>
          </View>
        </View>

        {/* IP Allowlist */}
        <View style={{ paddingHorizontal: 24, marginBottom: 16 }}>
          <View style={{ flexDirection: 'row', alignItems: 'center', justifyContent: 'space-between', marginBottom: 8 }}>
            <Text style={{ color: theme.color.cardForeground, fontWeight: '700' }}>IP Allowlist</Text>
            <TouchableOpacity onPress={openNew} accessibilityRole="button" accessibilityLabel="Add IP" style={{ paddingHorizontal: 12, paddingVertical: 8, borderRadius: 10, borderWidth: 1, borderColor: theme.color.border }}>
              <Text style={{ color: theme.color.cardForeground, fontWeight: '600' }}>Add</Text>
            </TouchableOpacity>
          </View>
          <View>
            {items.map((r, idx) => (
              <IpRuleRow key={r.id} rule={r} onToggle={() => onToggle(idx)} onEdit={() => openEdit(idx)} onDelete={() => onDelete(idx)} />
            ))}
          </View>
        </View>

        {/* Session & 2FA */}
        <View style={{ paddingHorizontal: 24 }}>
          <Text style={{ color: theme.color.cardForeground, fontWeight: '700', marginBottom: 8 }}>Session & 2FA</Text>
          <SessionPolicyEditor value={sess} onChange={setSess} />
        </View>
      </ScrollView>

      {/* Add/Edit Modal */}
      <Modal visible={editorOpen} transparent animationType="slide" onRequestClose={() => setEditorOpen(false)}>
        <View style={{ flex: 1, backgroundColor: 'rgba(0,0,0,0.4)', justifyContent: 'flex-end' }}>
          <View style={{ backgroundColor: theme.color.card, borderTopLeftRadius: 16, borderTopRightRadius: 16, padding: 16, borderWidth: 1, borderColor: theme.color.border }}>
            <Text style={{ color: theme.color.cardForeground, fontWeight: '700', marginBottom: 8 }}>{editIdx == null ? 'Add IP Rule' : 'Edit IP Rule'}</Text>
            <View style={{ gap: 8 }}>
              <TextInput value={cidr} onChangeText={(v) => debouncer.run(() => setCidr(v))} placeholder="CIDR (e.g., 192.168.0.0/16)" placeholderTextColor={theme.color.placeholder} accessibilityLabel="CIDR" style={{ borderWidth: 1, borderColor: theme.color.border, borderRadius: 10, paddingHorizontal: 10, paddingVertical: 8, color: theme.color.cardForeground, minHeight: 44 }} />
              <TextInput value={note} onChangeText={(v) => debouncer.run(() => setNote(v))} placeholder="Note (optional)" placeholderTextColor={theme.color.placeholder} accessibilityLabel="Note" style={{ borderWidth: 1, borderColor: theme.color.border, borderRadius: 10, paddingHorizontal: 10, paddingVertical: 8, color: theme.color.cardForeground, minHeight: 44 }} />
              {error ? <Text style={{ color: '#ef4444' }}>{error}</Text> : null}
              <View style={{ flexDirection: 'row', justifyContent: 'flex-end', gap: 8 }}>
                <TouchableOpacity onPress={() => setEditorOpen(false)} accessibilityRole="button" accessibilityLabel="Cancel" style={{ paddingHorizontal: 12, paddingVertical: 8, borderRadius: 10, borderWidth: 1, borderColor: theme.color.border }}>
                  <Text style={{ color: theme.color.cardForeground }}>Cancel</Text>
                </TouchableOpacity>
                <TouchableOpacity onPress={saveRule} accessibilityRole="button" accessibilityLabel="Save rule" style={{ paddingHorizontal: 12, paddingVertical: 8, borderRadius: 10, backgroundColor: theme.color.primary }}>
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

export default AccessControlsScreen


