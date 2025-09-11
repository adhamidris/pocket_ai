import React from 'react'
import { View, Text, TextInput, TouchableOpacity } from 'react-native'
import { useNavigation } from '@react-navigation/native'
import { tokens } from '../../ui/tokens'

export interface KnowledgeLinksProps {
  sources?: string[]
  onChange: (sources: string[]) => void
  testID?: string
}

const KnowledgeLinks: React.FC<KnowledgeLinksProps> = ({ sources = [], onChange, testID }) => {
  const navigation = useNavigation<any>()
  const [input, setInput] = React.useState('')

  const add = () => {
    const v = input.trim()
    if (!v) return
    onChange([v, ...sources])
    setInput('')
  }

  const remove = (idx: number) => {
    onChange(sources.filter((_, i) => i !== idx))
  }

  return (
    <View testID={testID}>
      <View style={{ borderWidth: 1, borderColor: tokens.colors.border, borderRadius: 12, backgroundColor: tokens.colors.card, marginBottom: 8 }}>
        <TextInput
          value={input}
          onChangeText={setInput}
          placeholder="Add knowledge source URL or label"
          placeholderTextColor={tokens.colors.placeholder as any}
          style={{ paddingHorizontal: 12, paddingVertical: 10, color: tokens.colors.cardForeground }}
        />
      </View>
      <TouchableOpacity onPress={add} accessibilityLabel="Add knowledge source" accessibilityRole="button" style={{ alignSelf: 'flex-start', paddingHorizontal: 12, paddingVertical: 8, borderRadius: 12, borderWidth: 1, borderColor: tokens.colors.border, marginBottom: 8 }}>
        <Text style={{ color: tokens.colors.primary, fontWeight: '700' }}>Add</Text>
      </TouchableOpacity>
      {sources.length === 0 ? (
        <Text style={{ color: tokens.colors.mutedForeground }}>No sources yet.</Text>
      ) : (
        <View style={{ gap: 8 }}>
          {sources.map((s, idx) => (
            <View key={`${s}-${idx}`} style={{ flexDirection: 'row', alignItems: 'center', justifyContent: 'space-between', padding: 12, borderWidth: 1, borderColor: tokens.colors.border, borderRadius: 12 }}>
              <TouchableOpacity onPress={() => navigation.navigate('Knowledge', { screen: 'KnowledgeHome', params: { filterTitles: [s] } })} accessibilityLabel={`Open Knowledge for ${s}`} accessibilityRole="button" style={{ flex: 1, marginRight: 8 }}>
                <Text style={{ color: tokens.colors.cardForeground }} numberOfLines={1}>{s}</Text>
              </TouchableOpacity>
              <TouchableOpacity onPress={() => remove(idx)} accessibilityLabel={`Remove ${s}`} accessibilityRole="button" style={{ paddingHorizontal: 10, paddingVertical: 6 }}>
                <Text style={{ color: tokens.colors.error, fontWeight: '700' }}>Remove</Text>
              </TouchableOpacity>
            </View>
          ))}
        </View>
      )}
    </View>
  )
}

export default React.memo(KnowledgeLinks)



