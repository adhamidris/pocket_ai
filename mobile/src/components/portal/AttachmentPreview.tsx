import React from 'react'
import { View, Text, TouchableOpacity } from 'react-native'
import { useTheme } from '../../providers/ThemeProvider'
import { MsgPartFile } from '../../types/portal'

export interface AttachmentPreviewProps { files: MsgPartFile[]; onRemove: (name: string) => void; testID?: string }

const AttachmentPreview: React.FC<AttachmentPreviewProps> = ({ files, onRemove, testID }) => {
  const { theme } = useTheme()
  if (!files.length) return null
  return (
    <View testID={testID} style={{ flexDirection: 'row', flexWrap: 'wrap', gap: 8 }}>
      {files.map((f) => (
        <View key={f.name} style={{ borderWidth: 1, borderColor: theme.color.border, borderRadius: 10, paddingHorizontal: 10, paddingVertical: 8, backgroundColor: theme.color.card, flexDirection: 'row', alignItems: 'center', gap: 8 }}>
          <Text style={{ color: theme.color.cardForeground }}>{f.name} ({f.sizeKB}kb)</Text>
          <TouchableOpacity onPress={() => onRemove(f.name)} accessibilityRole="button" accessibilityLabel={`Remove ${f.name}`} style={{ paddingHorizontal: 8, paddingVertical: 6, borderRadius: 8, borderWidth: 1, borderColor: theme.color.border }}>
            <Text style={{ color: theme.color.error, fontWeight: '700' }}>Remove</Text>
          </TouchableOpacity>
        </View>
      ))}
    </View>
  )
}

export default React.memo(AttachmentPreview)


