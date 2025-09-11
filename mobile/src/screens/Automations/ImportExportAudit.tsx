import React from 'react'
import { SafeAreaView, View, Text, TouchableOpacity, TextInput, ScrollView, Alert } from 'react-native'
import { useSafeAreaInsets } from 'react-native-safe-area-context'
import { useTheme } from '../../providers/ThemeProvider'
import { useNavigation, useRoute } from '@react-navigation/native'
import { AutoResponder, BusinessCalendar, Rule, SlaPolicy } from '../../types/automations'
import { track } from '../../lib/analytics'

export interface ImportExportParams {
  rules: Rule[]
  calendar: BusinessCalendar
  policy: SlaPolicy
  responders: AutoResponder[]
  audit?: { ts: number; user: string; action: string }[]
  tab?: 'export' | 'import' | 'audit'
  onApply?: (data: { rules: Rule[]; calendar: BusinessCalendar; policy: SlaPolicy; responders: AutoResponder[]; auditAction?: string }) => void
}

const ImportExportAudit: React.FC = () => {
  const { theme } = useTheme()
  const insets = useSafeAreaInsets()
  const navigation = useNavigation<any>()
  const route = useRoute<any>()
  const params: ImportExportParams = route.params || {}

  const [tab, setTab] = React.useState<NonNullable<ImportExportParams['tab']>>(params.tab || 'export')
  const [json, setJson] = React.useState<string>(() => JSON.stringify({ rules: params.rules, calendar: params.calendar, policy: params.policy, responders: params.responders }, null, 2))
  const [importPreview, setImportPreview] = React.useState<any | null>(null)

  const doExport = () => {
    try {
      console.log('[export]\n', json)
      track('automations.export')
      Alert.alert('Exported', 'JSON printed to console (UI-only).')
      params.onApply && params.onApply({ rules: params.rules, calendar: params.calendar, policy: params.policy, responders: params.responders, auditAction: 'export' })
    } catch (e) {
      Alert.alert('Error', 'Could not export.')
    }
  }

  const parseImport = () => {
    try {
      const obj = JSON.parse(json)
      if (!obj || typeof obj !== 'object') throw new Error('invalid')
      setImportPreview({
        rulesCount: Array.isArray(obj.rules) ? obj.rules.length : 0,
        respondersCount: Array.isArray(obj.responders) ? obj.responders.length : 0,
        hasCalendar: !!obj.calendar,
        hasPolicy: !!obj.policy,
        obj,
      })
      Alert.alert('Parsed', 'Import JSON parsed. Review preview below.')
    } catch (e) {
      Alert.alert('Invalid JSON', 'Please paste a valid JSON export.')
      setImportPreview(null)
    }
  }

  const applyImport = () => {
    if (!importPreview?.obj) { Alert.alert('Nothing to import'); return }
    try {
      const obj = importPreview.obj
      params.onApply && params.onApply({ rules: obj.rules || [], calendar: obj.calendar || params.calendar, policy: obj.policy || params.policy, responders: obj.responders || [], auditAction: 'import' })
      track('automations.import')
      navigation.goBack()
    } catch (e) {
      Alert.alert('Error', 'Could not apply import.')
    }
  }

  const audits = (params.audit || []).slice().sort((a, b) => b.ts - a.ts)

  return (
    <SafeAreaView style={{ flex: 1, backgroundColor: theme.color.background }}>
      {/* Header */}
      <View style={{ paddingHorizontal: 16, paddingTop: insets.top + 8, paddingBottom: 12, borderBottomWidth: 1, borderBottomColor: theme.color.border }}>
        <View style={{ flexDirection: 'row', alignItems: 'center', justifyContent: 'space-between' }}>
          <TouchableOpacity onPress={() => navigation.goBack()} accessibilityLabel="Back" accessibilityRole="button" style={{ padding: 8 }}>
            <Text style={{ color: theme.color.primary, fontWeight: '600' }}>{'< Back'}</Text>
          </TouchableOpacity>
          <Text style={{ color: theme.color.foreground, fontSize: 18, fontWeight: '700' }}>Import / Export / Audit</Text>
          <View style={{ width: 64 }} />
        </View>
      </View>

      {/* Tabs */}
      <View style={{ paddingHorizontal: 16, paddingTop: 8, paddingBottom: 8, flexDirection: 'row', gap: 8 }}>
        {(['export', 'import', 'audit'] as const).map((t) => (
          <TouchableOpacity key={t} onPress={() => setTab(t)} accessibilityLabel={`Tab ${t}`} accessibilityRole="button" style={{ paddingHorizontal: 12, paddingVertical: 8, borderRadius: 10, borderWidth: 1, borderColor: tab === t ? theme.color.primary : theme.color.border }}>
            <Text style={{ color: tab === t ? theme.color.primary : theme.color.mutedForeground, fontWeight: '700' }}>{t.toUpperCase()}</Text>
          </TouchableOpacity>
        ))}
      </View>

      <ScrollView style={{ padding: 16 }} contentContainerStyle={{ paddingBottom: 24 }}>
        {tab !== 'audit' ? (
          <View>
            <Text style={{ color: theme.color.mutedForeground, marginBottom: 6 }}>{tab === 'export' ? 'JSON export' : 'Paste JSON to import'}</Text>
            <TextInput value={json} onChangeText={setJson} multiline placeholder="{...}"
              placeholderTextColor={theme.color.placeholder} accessibilityLabel="JSON" style={{ minHeight: 220, textAlignVertical: 'top', borderWidth: 1, borderColor: theme.color.border, borderRadius: 12, padding: 12, color: theme.color.cardForeground, marginBottom: 12 }} />
            <View style={{ flexDirection: 'row', gap: 8 }}>
              {tab === 'export' ? (
                <TouchableOpacity onPress={doExport} accessibilityLabel="Export" accessibilityRole="button" style={{ paddingHorizontal: 12, paddingVertical: 10, borderWidth: 1, borderColor: theme.color.border, borderRadius: 12 }}>
                  <Text style={{ color: theme.color.primary, fontWeight: '700' }}>Export</Text>
                </TouchableOpacity>
              ) : (
                <>
                  <TouchableOpacity onPress={parseImport} accessibilityLabel="Preview Import" accessibilityRole="button" style={{ paddingHorizontal: 12, paddingVertical: 10, borderWidth: 1, borderColor: theme.color.border, borderRadius: 12 }}>
                    <Text style={{ color: theme.color.cardForeground, fontWeight: '600' }}>Preview</Text>
                  </TouchableOpacity>
                  <TouchableOpacity onPress={applyImport} accessibilityLabel="Apply Import" accessibilityRole="button" style={{ paddingHorizontal: 12, paddingVertical: 10, borderWidth: 1, borderColor: theme.color.border, borderRadius: 12 }}>
                    <Text style={{ color: theme.color.primary, fontWeight: '700' }}>Apply</Text>
                  </TouchableOpacity>
                </>
              )}
            </View>

            {importPreview && (
              <View style={{ marginTop: 12, borderWidth: 1, borderColor: theme.color.border, borderRadius: 12, padding: 12 }}>
                <Text style={{ color: theme.color.cardForeground, fontWeight: '700', marginBottom: 6 }}>Preview</Text>
                <Text style={{ color: theme.color.mutedForeground }}>Rules: {importPreview.rulesCount}</Text>
                <Text style={{ color: theme.color.mutedForeground }}>Responders: {importPreview.respondersCount}</Text>
                <Text style={{ color: theme.color.mutedForeground }}>Calendar: {importPreview.hasCalendar ? 'Yes' : 'No'}</Text>
                <Text style={{ color: theme.color.mutedForeground }}>Policy: {importPreview.hasPolicy ? 'Yes' : 'No'}</Text>
              </View>
            )}
          </View>
        ) : (
          <View>
            {audits.length === 0 ? (
              <Text style={{ color: theme.color.mutedForeground }}>No audit entries yet.</Text>
            ) : (
              <View style={{ gap: 8 }}>
                {audits.map((a, idx) => (
                  <View key={idx} style={{ padding: 12, borderWidth: 1, borderColor: theme.color.border, borderRadius: 12 }}>
                    <Text style={{ color: theme.color.cardForeground, fontWeight: '600' }}>{a.action}</Text>
                    <Text style={{ color: theme.color.mutedForeground }}>{new Date(a.ts).toLocaleString()} • {a.user}</Text>
                  </View>
                ))}
              </View>
            )}
          </View>
        )}
      </ScrollView>
    </SafeAreaView>
  )
}

export default ImportExportAudit


