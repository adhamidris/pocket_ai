import React from 'react'
import { SafeAreaView, View, Text, TouchableOpacity, ScrollView, TextInput, Alert } from 'react-native'
import { useSafeAreaInsets } from 'react-native-safe-area-context'
import { useTheme } from '../../providers/ThemeProvider'
import { useNavigation, useRoute } from '@react-navigation/native'
import { ActionSpec, AllowRule } from '../../types/actions'
import { track } from '../../lib/analytics'

type ExportBundle = {
  version: number
  exportedAt: string
  actions: ActionSpec[]
  rules: AllowRule[]
  packsApplied?: string[]
}

type Params = { actions?: ActionSpec[]; rules?: AllowRule[]; packsApplied?: string[]; onApply?: (bundle: ExportBundle) => void }

const ImportExportScreen: React.FC = () => {
  const { theme } = useTheme()
  const insets = useSafeAreaInsets()
  const navigation = useNavigation<any>()
  const route = useRoute<any>()
  const { actions = [], rules = [], packsApplied = [], onApply } = (route.params || {}) as Params

  const [text, setText] = React.useState<string>('')
  const [preview, setPreview] = React.useState<ExportBundle | null>(null)
  const [conflicts, setConflicts] = React.useState<string[]>([])

  const makeBundle = (): ExportBundle => ({ version: 1, exportedAt: new Date().toISOString(), actions, rules, packsApplied })

  const doExport = () => {
    const bundle = makeBundle()
    const json = JSON.stringify(bundle, null, 2)
    setText(json)
    try { console.log('[ExportBundle]', bundle) } catch {}
    try { track('actions.export') } catch {}
    Alert.alert('Exported', 'Bundle copied to editor and logged to console.')
  }

  const parseImport = () => {
    try {
      const obj = JSON.parse(text) as ExportBundle
      setPreview(obj)
      try { track('actions.import') } catch {}
      // Conflicts: same action id but different version
      const map = new Map(actions.map(a => [a.id, a.version]))
      const diffs: string[] = []
      obj.actions.forEach(a => {
        const cur = map.get(a.id)
        if (cur != null && cur !== a.version) diffs.push(`Action ${a.id}: version ${cur} → ${a.version}`)
      })
      setConflicts(diffs)
    } catch (e: any) {
      Alert.alert('Invalid JSON', e?.message || 'Failed to parse')
      setPreview(null)
      setConflicts([])
    }
  }

  const applyImport = () => {
    if (!preview) return
    onApply ? onApply(preview) : Alert.alert('Applied', 'Imported rules/actions (UI-only)')
    navigation.goBack()
  }

  return (
    <SafeAreaView style={{ flex: 1, backgroundColor: theme.color.background }}>
      <ScrollView contentContainerStyle={{ paddingBottom: 24 }}>
        {/* Header */}
        <View style={{ paddingHorizontal: 24, paddingTop: insets.top + 12, paddingBottom: 12 }}>
          <View style={{ flexDirection: 'row', alignItems: 'center', justifyContent: 'space-between' }}>
            <TouchableOpacity onPress={() => navigation.goBack()} accessibilityLabel="Back" accessibilityRole="button" style={{ paddingHorizontal: 12, paddingVertical: 8, borderRadius: 10, borderWidth: 1, borderColor: theme.color.border }}>
              <Text style={{ color: theme.color.primary, fontWeight: '700' }}>{'< Back'}</Text>
            </TouchableOpacity>
            <Text style={{ color: theme.color.foreground, fontSize: 20, fontWeight: '700' }}>Import / Export</Text>
            <View style={{ width: 64 }} />
          </View>
        </View>

        {/* Controls */}
        <View style={{ paddingHorizontal: 24, gap: 12 }}>
          <View style={{ flexDirection: 'row', gap: 8 }}>
            <TouchableOpacity onPress={doExport} accessibilityLabel="Export" accessibilityRole="button" style={{ paddingHorizontal: 12, paddingVertical: 10, borderRadius: 12, backgroundColor: theme.color.primary }}>
              <Text style={{ color: '#fff', fontWeight: '700' }}>Export</Text>
            </TouchableOpacity>
            <TouchableOpacity onPress={parseImport} accessibilityLabel="Parse" accessibilityRole="button" style={{ paddingHorizontal: 12, paddingVertical: 10, borderRadius: 12, borderWidth: 1, borderColor: theme.color.border }}>
              <Text style={{ color: theme.color.cardForeground, fontWeight: '600' }}>Parse Import</Text>
            </TouchableOpacity>
            {preview && (
              <TouchableOpacity onPress={applyImport} accessibilityLabel="Apply" accessibilityRole="button" style={{ paddingHorizontal: 12, paddingVertical: 10, borderRadius: 12, backgroundColor: theme.color.primary }}>
                <Text style={{ color: '#fff', fontWeight: '700' }}>Apply</Text>
              </TouchableOpacity>
            )}
          </View>

          <View>
            <Text style={{ color: theme.color.mutedForeground, marginBottom: 6 }}>JSON</Text>
            <View style={{ borderWidth: 1, borderColor: theme.color.border, borderRadius: 12, backgroundColor: theme.color.card }}>
              <TextInput value={text} onChangeText={setText} placeholder="Paste bundle JSON here" placeholderTextColor={theme.color.placeholder} multiline style={{ minHeight: 200, paddingHorizontal: 12, paddingVertical: 10, color: theme.color.cardForeground }} />
            </View>
          </View>

          {!!preview && (
            <View>
              <Text style={{ color: theme.color.cardForeground, fontWeight: '700', marginBottom: 6 }}>Preview</Text>
              <Text style={{ color: theme.color.mutedForeground }}>Actions: {preview.actions.length} • Rules: {preview.rules.length} • Packs: {preview.packsApplied?.length || 0}</Text>
              {conflicts.length > 0 && (
                <View style={{ marginTop: 8 }}>
                  <Text style={{ color: theme.color.warning, fontWeight: '700' }}>Version conflicts</Text>
                  {conflicts.map((c, i) => (
                    <Text key={i} style={{ color: theme.color.mutedForeground }}>• {c}</Text>
                  ))}
                </View>
              )}
            </View>
          )}
        </View>
      </ScrollView>
    </SafeAreaView>
  )
}

export default ImportExportScreen


