import React from 'react'
import { SafeAreaView, View, Text, TouchableOpacity, TextInput, Alert, Switch, ScrollView } from 'react-native'
import { useSafeAreaInsets } from 'react-native-safe-area-context'
import { useTheme } from '../../providers/ThemeProvider'
import { useNavigation, useRoute } from '@react-navigation/native'
import { SourceScope, UploadSource } from '../../types/knowledge'
import ScopePicker from '../../components/knowledge/ScopePicker'
import { track } from '../../lib/analytics'

const fakePick = () => {
  const names = ['Product Handbook.pdf', 'FAQ.docx', 'Release Notes.pdf', 'Support SOP.pdf']
  const name = names[Math.floor(Math.random() * names.length)]
  const mime = name.endsWith('.pdf') ? 'application/pdf' : 'application/vnd.openxmlformats-officedocument.wordprocessingml.document'
  const sizeKB = Math.floor(200 + Math.random() * 1500)
  const pages = Math.floor(3 + Math.random() * 48)
  return { filename: name, mime, sizeKB, pages }
}

const AddUploadSource: React.FC = () => {
  const { theme } = useTheme()
  const insets = useSafeAreaInsets()
  const navigation = useNavigation<any>()
  const route = useRoute<any>()

  const onSaveCb: undefined | ((s: UploadSource) => void) = route.params?.onSave

  const [title, setTitle] = React.useState('')
  const [meta, setMeta] = React.useState<{ filename: string; mime: string; sizeKB: number; pages: number } | null>(null)
  const [splitByHeadings, setSplit] = React.useState(true)
  const [auto, setAuto] = React.useState(true)
  const [scope, setScope] = React.useState<SourceScope>('global')

  const pick = () => {
    const m = fakePick()
    setMeta(m)
    if (!title) setTitle(m.filename.replace(/\.[^.]+$/, ''))
  }

  const save = () => {
    if (!meta) { Alert.alert('Pick a file first'); return }
    const id = `up-${Date.now()}`
    const src: UploadSource = {
      id,
      kind: 'upload',
      title: title.trim() || meta.filename,
      enabled: true,
      scope,
      status: 'idle',
      filename: meta.filename,
      mime: meta.mime,
      sizeKB: meta.sizeKB,
      pages: meta.pages,
    }
    try { onSaveCb && onSaveCb(src) } catch {}
    track('knowledge.source_add', { kind: 'upload' })
    Alert.alert('Saved', 'Upload source added (UI-only).')
    navigation.goBack()
  }

  return (
    <SafeAreaView style={{ flex: 1, backgroundColor: theme.color.background }}>
      {/* Header */}
      <View style={{ paddingHorizontal: 16, paddingTop: insets.top + 8, paddingBottom: 12, borderBottomWidth: 1, borderBottomColor: theme.color.border }}>
        <View style={{ flexDirection: 'row', alignItems: 'center', justifyContent: 'space-between' }}>
          <TouchableOpacity onPress={() => navigation.goBack()} accessibilityLabel="Back" accessibilityRole="button" style={{ padding: 8 }}>
            <Text style={{ color: theme.color.primary, fontWeight: '600' }}>{'< Back'}</Text>
          </TouchableOpacity>
          <Text style={{ color: theme.color.foreground, fontSize: 18, fontWeight: '700' }}>Add Upload Source</Text>
          <View style={{ width: 64 }} />
        </View>
      </View>

      <ScrollView style={{ padding: 16 }} contentContainerStyle={{ paddingBottom: 24 }}>
        {/* Pick */}
        <TouchableOpacity onPress={pick} accessibilityLabel="Pick file" accessibilityRole="button" style={{ alignSelf: 'flex-start', paddingHorizontal: 12, paddingVertical: 10, borderWidth: 1, borderColor: theme.color.border, borderRadius: 12, marginBottom: 12 }}>
          <Text style={{ color: theme.color.cardForeground, fontWeight: '600' }}>{meta ? 'Re-pick File' : 'Pick File'}</Text>
        </TouchableOpacity>
        {meta && (
          <View style={{ borderWidth: 1, borderColor: theme.color.border, borderRadius: 12, padding: 12, marginBottom: 12 }}>
            <Text style={{ color: theme.color.cardForeground, fontWeight: '600' }}>{meta.filename}</Text>
            <Text style={{ color: theme.color.mutedForeground }}>MIME: {meta.mime}</Text>
            <Text style={{ color: theme.color.mutedForeground }}>Size: {meta.sizeKB} KB</Text>
            <Text style={{ color: theme.color.mutedForeground }}>Pages: {meta.pages}</Text>
            <Text style={{ color: theme.color.mutedForeground, marginTop: 6 }}>Chunking: {splitByHeadings ? 'Split by headings' : (auto ? 'Auto' : 'None')}</Text>
          </View>
        )}

        {/* Options */}
        <View style={{ flexDirection: 'row', alignItems: 'center', justifyContent: 'space-between', marginBottom: 12 }}>
          <Text style={{ color: theme.color.mutedForeground }}>Split by headings</Text>
          <Switch value={splitByHeadings} onValueChange={setSplit} accessibilityLabel="Split by headings" />
        </View>
        <View style={{ flexDirection: 'row', alignItems: 'center', justifyContent: 'space-between', marginBottom: 12 }}>
          <Text style={{ color: theme.color.mutedForeground }}>Auto</Text>
          <Switch value={auto} onValueChange={setAuto} accessibilityLabel="Auto" />
        </View>

        {/* Title */}
        <View style={{ marginBottom: 12 }}>
          <Text style={{ color: theme.color.mutedForeground, marginBottom: 6 }}>Title</Text>
          <TextInput value={title} onChangeText={setTitle} placeholder="e.g., Product Handbook" placeholderTextColor={theme.color.placeholder} accessibilityLabel="Title" style={{ borderWidth: 1, borderColor: theme.color.border, borderRadius: 12, paddingHorizontal: 12, paddingVertical: 10, color: theme.color.cardForeground }} />
        </View>

        {/* Scope */}
        <View style={{ marginBottom: 16 }}>
          <Text style={{ color: theme.color.mutedForeground, marginBottom: 6 }}>Scope</Text>
          <ScopePicker value={scope} onChange={setScope} />
        </View>

        {/* Save */}
        <TouchableOpacity onPress={save} accessibilityLabel="Save Upload Source" accessibilityRole="button" style={{ alignSelf: 'flex-start', paddingHorizontal: 12, paddingVertical: 10, borderWidth: 1, borderColor: theme.color.border, borderRadius: 12 }}>
          <Text style={{ color: theme.color.primary, fontWeight: '700' }}>Save</Text>
        </TouchableOpacity>
      </ScrollView>
    </SafeAreaView>
  )
}

export default AddUploadSource


