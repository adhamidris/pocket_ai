import React from 'react'
import { View, TouchableOpacity, Text } from 'react-native'
import { ToolSuggestion } from '../../types/assistant'
import { useTheme } from '../../providers/ThemeProvider'
import { useNavigation } from '@react-navigation/native'
import openToolSuggestion from '../../navigation/openToolSuggestion'
import { track } from '../../lib/analytics'

export const ToolSuggestionRow: React.FC<{ suggestions: ToolSuggestion[]; onPress?: (t: ToolSuggestion) => void }> = ({ suggestions, onPress }) => {
  const { theme } = useTheme()
  const navigation = useNavigation<any>()
  return (
    <View style={{ flexDirection: 'row', flexWrap: 'wrap', gap: 8 }}>
      {suggestions.map((s, idx) => (
        <TouchableOpacity key={s.key + idx} onPress={() => {
          if (onPress) onPress(s)
          else { try { track('assistant.tool', { key: s.key }) } catch {}; openToolSuggestion(navigation.navigate as any, s) }
        }} accessibilityLabel={`tool ${s.label}`}>
          <View style={{ paddingHorizontal: 12, paddingVertical: 8, backgroundColor: theme.color.secondary, borderRadius: theme.radius.md, borderWidth: 1, borderColor: theme.color.border }}>
            <Text style={{ color: theme.color.foreground, fontWeight: '600' }}>{s.label}</Text>
          </View>
        </TouchableOpacity>
      ))}
    </View>
  )
}

export default ToolSuggestionRow


