import React from 'react'
import { SafeAreaView, View, Text, TextInput, TouchableOpacity, Switch, ScrollView } from 'react-native'
import { useSafeAreaInsets } from 'react-native-safe-area-context'
import { useTheme } from '../../providers/ThemeProvider'
import { useNavigation, useRoute } from '@react-navigation/native'
import { NoteSource, SourceScope } from '../../types/knowledge'
import ScopePicker from '../../components/knowledge/ScopePicker'
import { track } from '../../lib/analytics'

export interface NoteEditorParams {
  note?: NoteSource
  onSave?: (note: NoteSource) => void
}

const NoteEditor: React.FC = () => {
  const { theme } = useTheme()
  const insets = useSafeAreaInsets()
  const navigation = useNavigation<any>()
  const route = useRoute<any>()
  const params: NoteEditorParams = route.params || {}

  const [title, setTitle] = React.useState(params.note?.title || '')
  const [tags, setTags] = React.useState((params.note?.tags || []).join(', '))
  const [content, setContent] = React.useState(params.note?.content || '')
  const [enabled, setEnabled] = React.useState<boolean>(params.note?.enabled ?? true)
  const [scope, setScope] = React.useState<SourceScope>(params.note?.scope || 'global')

  const save = () => {
    const id = params.note?.id || `note-${Date.now()}`
    const note: NoteSource = {
      id,
      kind: 'note',
      title: title.trim() || 'Untitled Note',
      enabled,
      scope,
      status: params.note?.status || 'idle',
      content,
      tags: tags.split(',').map(t => t.trim()).filter(Boolean),
      lastTrainedTs: params.note?.lastTrainedTs,
    }
    params.onSave && params.onSave(note)
    track('knowledge.source_add', { kind: params.note ? 'note_edit' : 'note' })
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
          <Text style={{ color: theme.color.foreground, fontSize: 18, fontWeight: '700' }}>{params.note ? 'Edit Note' : 'New Note'}</Text>
          <View style={{ width: 64 }} />
        </View>
      </View>

      <ScrollView style={{ padding: 16 }} contentContainerStyle={{ paddingBottom: 24 }}>
        {/* Title */}
        <View style={{ marginBottom: 12 }}>
          <Text style={{ color: theme.color.mutedForeground, marginBottom: 6 }}>Title</Text>
          <TextInput value={title} onChangeText={setTitle} placeholder="e.g., Refund Policy" placeholderTextColor={theme.color.placeholder} accessibilityLabel="Title" style={{ borderWidth: 1, borderColor: theme.color.border, borderRadius: 12, paddingHorizontal: 12, paddingVertical: 10, color: theme.color.cardForeground }} />
        </View>
        {/* Tags */}
        <View style={{ marginBottom: 12 }}>
          <Text style={{ color: theme.color.mutedForeground, marginBottom: 6 }}>Tags (comma-separated)</Text>
          <TextInput value={tags} onChangeText={setTags} placeholder="internal,policy" placeholderTextColor={theme.color.placeholder} accessibilityLabel="Tags" style={{ borderWidth: 1, borderColor: theme.color.border, borderRadius: 12, paddingHorizontal: 12, paddingVertical: 10, color: theme.color.cardForeground }} />
        </View>
        {/* Content */}
        <View style={{ marginBottom: 12 }}>
          <Text style={{ color: theme.color.mutedForeground, marginBottom: 6 }}>Content</Text>
          <TextInput value={content} onChangeText={setContent} placeholder="Type the note content here" placeholderTextColor={theme.color.placeholder} accessibilityLabel="Content" multiline style={{ minHeight: 160, textAlignVertical: 'top', borderWidth: 1, borderColor: theme.color.border, borderRadius: 12, paddingHorizontal: 12, paddingVertical: 10, color: theme.color.cardForeground }} />
        </View>
        {/* Enabled */}
        <View style={{ flexDirection: 'row', alignItems: 'center', justifyContent: 'space-between', marginBottom: 12 }}>
          <Text style={{ color: theme.color.mutedForeground }}>Enabled</Text>
          <Switch value={enabled} onValueChange={setEnabled} accessibilityLabel="Enabled" />
        </View>
        {/* Scope */}
        <View style={{ marginBottom: 16 }}>
          <Text style={{ color: theme.color.mutedForeground, marginBottom: 6 }}>Scope</Text>
          <ScopePicker value={scope} onChange={setScope} />
        </View>

        {/* Save */}
        <TouchableOpacity onPress={save} accessibilityLabel="Save Note" accessibilityRole="button" style={{ alignSelf: 'flex-start', paddingHorizontal: 12, paddingVertical: 10, borderWidth: 1, borderColor: theme.color.border, borderRadius: 12 }}>
          <Text style={{ color: theme.color.primary, fontWeight: '700' }}>Save</Text>
        </TouchableOpacity>
      </ScrollView>
    </SafeAreaView>
  )
}

export default NoteEditor


