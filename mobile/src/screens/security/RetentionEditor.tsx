// @ts-nocheck
import React from 'react'
import { SafeAreaView, View, Text, TouchableOpacity, ScrollView, Switch } from 'react-native'
import { useSafeAreaInsets } from 'react-native-safe-area-context'
import { useTheme } from '../../providers/ThemeProvider'
import { DataRetentionPolicy, AuditEvent } from '../../types/security'
import { track } from '../../lib/analytics'
import RetentionSliders from '../../components/security/RetentionSliders'

export interface RetentionEditorProps {
  value: DataRetentionPolicy
  onSave: (policy: DataRetentionPolicy, audit: AuditEvent) => void
  onClose?: () => void
}

const now = () => Date.now()

const WarningBanner: React.FC<{ show: boolean }>=({ show }) => {
  const { theme } = useTheme()
  if (!show) return null
  return (
    <View style={{ borderWidth: 1, borderColor: theme.color.warning, backgroundColor: theme.color.warning + '20', borderRadius: 12, padding: 10, marginBottom: 12 }}>
      <Text style={{ color: theme.color.cardForeground }}>Warning: Indefinite retention selected. Ensure consent and policies allow this (UI-only).</Text>
    </View>
  )
}

const RetentionEditor: React.FC<RetentionEditorProps> = ({ value, onSave, onClose }) => {
  const { theme } = useTheme()
  const insets = useSafeAreaInsets()
  const [policy, setPolicy] = React.useState<DataRetentionPolicy>(value)

  const save = () => {
    const evt: AuditEvent = {
      id: `a-${now()}`,
      ts: now(),
      actor: 'me',
      action: 'policy.update',
      entityType: 'policy',
      entityId: policy.id,
      details: `Retention updated: conv=${policy.conversationsDays}, msg=${policy.messagesDays}, audit=${policy.auditDays}, piiMask=${policy.piiMasking}, backups=${policy.applyToBackups}`,
      risk: 'low',
    }
    try { track('retention.save') } catch {}
    onSave({ ...policy, updatedAt: now() }, evt)
    onClose && onClose()
  }

  const anyIndef = policy.conversationsDays === -1 || policy.messagesDays === -1 || policy.auditDays === -1

  return (
    <SafeAreaView style={{ flex: 1, backgroundColor: theme.color.background }}>
      <ScrollView contentContainerStyle={{ paddingBottom: 24 }}>
        <View style={{ paddingHorizontal: 24, paddingTop: insets.top + 12, paddingBottom: 12 }}>
          <View style={{ flexDirection: 'row', alignItems: 'center', justifyContent: 'space-between' }}>
            <TouchableOpacity onPress={onClose} accessibilityRole="button" accessibilityLabel="Back" style={{ paddingHorizontal: 12, paddingVertical: 8, borderRadius: 10, borderWidth: 1, borderColor: theme.color.border }}>
              <Text style={{ color: theme.color.primary, fontWeight: '600' }}>{'< Back'}</Text>
            </TouchableOpacity>
            <Text style={{ color: theme.color.foreground, fontSize: 20, fontWeight: '700' }}>Retention Policy</Text>
            <TouchableOpacity onPress={save} accessibilityRole="button" accessibilityLabel="Save" style={{ paddingHorizontal: 12, paddingVertical: 8, borderRadius: 10, backgroundColor: theme.color.primary }}>
              <Text style={{ color: theme.color.primaryForeground, fontWeight: '700' }}>Save</Text>
            </TouchableOpacity>
          </View>
        </View>

        <View style={{ paddingHorizontal: 24 }}>
          <WarningBanner show={anyIndef} />
          <RetentionSliders value={policy} onChange={setPolicy} />

          {/* Toggles */}
          <View style={{ marginTop: 12, gap: 8 }}>
            <View style={{ flexDirection: 'row', alignItems: 'center', justifyContent: 'space-between', borderWidth: 1, borderColor: theme.color.border, borderRadius: 12, paddingHorizontal: 12, paddingVertical: 10 }}>
              <Text style={{ color: theme.color.cardForeground }}>PII masking on export/views</Text>
              <Switch value={policy.piiMasking} onValueChange={(v) => setPolicy((p) => ({ ...p, piiMasking: v }))} accessibilityLabel="PII masking on export and views" />
            </View>
            <View style={{ flexDirection: 'row', alignItems: 'center', justifyContent: 'space-between', borderWidth: 1, borderColor: theme.color.border, borderRadius: 12, paddingHorizontal: 12, paddingVertical: 10 }}>
              <Text style={{ color: theme.color.cardForeground }}>Apply to backups</Text>
              <Switch value={policy.applyToBackups} onValueChange={(v) => setPolicy((p) => ({ ...p, applyToBackups: v }))} accessibilityLabel="Apply to backups" />
            </View>
          </View>
        </View>
      </ScrollView>
    </SafeAreaView>
  )
}

export default RetentionEditor


