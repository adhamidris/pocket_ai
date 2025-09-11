import React from 'react'
import { View, Text, TouchableOpacity, ScrollView, TextInput } from 'react-native'
import { useTheme } from '../../providers/ThemeProvider'
import { PromptTemplate } from '../../types/assistant'

export interface PromptLibraryProps {
  templates: PromptTemplate[]
  onUse: (tpl: PromptTemplate) => void
  onUpdate: (tpl: PromptTemplate) => void
  onDuplicate: (tpl: PromptTemplate) => void
  onPinToDashboard?: (tpl: PromptTemplate) => void
}

export const PromptLibrary: React.FC<PromptLibraryProps> = ({ templates, onUse, onUpdate, onDuplicate, onPinToDashboard }) => {
  const { theme } = useTheme()
  const [editingId, setEditingId] = React.useState<string | null>(null)
  const [draft, setDraft] = React.useState<PromptTemplate | null>(null)

  const startEdit = (tpl: PromptTemplate) => { setEditingId(tpl.id); setDraft(tpl) }
  const saveEdit = () => { if (draft) onUpdate(draft); setEditingId(null); setDraft(null) }

  return (
    <ScrollView>
      <View style={{ gap: 12 }}>
        {templates.map((tpl) => (
          <View key={tpl.id} style={{ borderWidth: 1, borderColor: theme.color.border, borderRadius: 12, padding: 12, backgroundColor: theme.color.card }}>
            {editingId === tpl.id ? (
              <View style={{ gap: 8 }}>
                <TextInput value={draft?.name} onChangeText={(v) => setDraft(d => ({ ...(d as any), name: v }))} placeholder="Name" placeholderTextColor={theme.color.placeholder} style={{ color: theme.color.cardForeground, borderWidth: 1, borderColor: theme.color.border, borderRadius: 8, paddingHorizontal: 8, paddingVertical: 6 }} />
                <TextInput value={draft?.text} onChangeText={(v) => setDraft(d => ({ ...(d as any), text: v }))} placeholder="Prompt" placeholderTextColor={theme.color.placeholder} multiline style={{ color: theme.color.cardForeground, borderWidth: 1, borderColor: theme.color.border, borderRadius: 8, paddingHorizontal: 8, paddingVertical: 8, minHeight: 60 }} />
                <View style={{ flexDirection: 'row', gap: 8 }}>
                  <TouchableOpacity onPress={saveEdit} accessibilityRole="button" accessibilityLabel="Save template" style={{ paddingHorizontal: 10, paddingVertical: 8, borderRadius: 8, backgroundColor: theme.color.primary }}>
                    <Text style={{ color: '#fff', fontWeight: '700' }}>Save</Text>
                  </TouchableOpacity>
                  <TouchableOpacity onPress={() => { setEditingId(null); setDraft(null) }} accessibilityRole="button" accessibilityLabel="Cancel edit" style={{ paddingHorizontal: 10, paddingVertical: 8, borderRadius: 8, borderWidth: 1, borderColor: theme.color.border }}>
                    <Text style={{ color: theme.color.cardForeground, fontWeight: '600' }}>Cancel</Text>
                  </TouchableOpacity>
                </View>
              </View>
            ) : (
              <>
                <Text style={{ color: theme.color.cardForeground, fontWeight: '700' }}>{tpl.name}</Text>
                <Text style={{ color: theme.color.mutedForeground, marginTop: 4 }}>{tpl.text}</Text>
                <View style={{ flexDirection: 'row', gap: 8, marginTop: 8, flexWrap: 'wrap' }}>
                  <TouchableOpacity onPress={() => onUse(tpl)} accessibilityRole="button" accessibilityLabel="Use template" style={{ paddingHorizontal: 10, paddingVertical: 8, borderRadius: 8, backgroundColor: theme.color.primary }}>
                    <Text style={{ color: '#fff', fontWeight: '700' }}>Use</Text>
                  </TouchableOpacity>
                  <TouchableOpacity onPress={() => onPinToDashboard?.(tpl)} accessibilityRole="button" accessibilityLabel="Pin to Dashboard" style={{ paddingHorizontal: 10, paddingVertical: 8, borderRadius: 8, borderWidth: 1, borderColor: theme.color.border }}>
                    <Text style={{ color: theme.color.cardForeground, fontWeight: '600' }}>Pin to Dashboard</Text>
                  </TouchableOpacity>
                  <TouchableOpacity onPress={() => startEdit(tpl)} accessibilityRole="button" accessibilityLabel="Edit template" style={{ paddingHorizontal: 10, paddingVertical: 8, borderRadius: 8, borderWidth: 1, borderColor: theme.color.border }}>
                    <Text style={{ color: theme.color.cardForeground, fontWeight: '600' }}>Edit</Text>
                  </TouchableOpacity>
                  <TouchableOpacity onPress={() => onDuplicate(tpl)} accessibilityRole="button" accessibilityLabel="Duplicate template" style={{ paddingHorizontal: 10, paddingVertical: 8, borderRadius: 8, borderWidth: 1, borderColor: theme.color.border }}>
                    <Text style={{ color: theme.color.cardForeground, fontWeight: '600' }}>Duplicate</Text>
                  </TouchableOpacity>
                </View>
              </>
            )}
          </View>
        ))}
      </View>
    </ScrollView>
  )
}

export default PromptLibrary


