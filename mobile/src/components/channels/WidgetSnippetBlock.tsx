import React from 'react'
import { View, TouchableOpacity } from 'react-native'
import { Text } from '../../ui/Text'
import { tokens } from '../../ui/tokens'
import { WidgetSnippet } from '../../types/channels'

export interface WidgetSnippetBlockProps {
  snippet: WidgetSnippet
  onCopy: () => void
  testID?: string
}

const WidgetSnippetBlock: React.FC<WidgetSnippetBlockProps> = ({ snippet, onCopy, testID }) => {
  return (
    <View testID={testID} style={{ borderWidth: 1, borderColor: tokens.colors.border, borderRadius: 16, backgroundColor: tokens.colors.card, padding: 12 }}>
      <Text size={12} color={tokens.colors.mutedForeground} style={{ marginBottom: 6 }}>Framework: {snippet.framework}</Text>
      <View style={{ borderWidth: 1, borderColor: tokens.colors.border, borderRadius: 12, backgroundColor: tokens.colors.muted, padding: 10 }}>
        <Text size={12} color={tokens.colors.cardForeground}>{snippet.code}</Text>
      </View>
      <TouchableOpacity onPress={onCopy} accessibilityLabel="Copy snippet" accessibilityRole="button" style={{ marginTop: 10, alignSelf: 'flex-start', paddingHorizontal: 12, paddingVertical: 8, borderWidth: 1, borderColor: tokens.colors.border, borderRadius: 12 }}>
        <Text size={13} weight="600" color={tokens.colors.primary}>Copy</Text>
      </TouchableOpacity>
    </View>
  )
}

export default React.memo(WidgetSnippetBlock)



