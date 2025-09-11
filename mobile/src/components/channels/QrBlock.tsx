import React from 'react'
import { View, TouchableOpacity } from 'react-native'
import { Text } from '../../ui/Text'
import { tokens } from '../../ui/tokens'

export interface QrBlockProps {
  url: string
  onSave: () => void
  testID?: string
}

const QrPlaceholder: React.FC = () => {
  const cells = 9
  const grid = Array.from({ length: cells * cells }, (_, i) => i)
  return (
    <View style={{ width: 160, height: 160, flexDirection: 'row', flexWrap: 'wrap', borderWidth: 1, borderColor: tokens.colors.border, borderRadius: 12, overflow: 'hidden' }}>
      {grid.map((i) => (
        <View key={i} style={{ width: 160 / cells, height: 160 / cells, backgroundColor: (i + Math.floor(i / cells)) % 3 === 0 ? tokens.colors.cardForeground : tokens.colors.card }} />
      ))}
    </View>
  )
}

const QrBlock: React.FC<QrBlockProps> = ({ url, onSave, testID }) => {
  return (
    <View testID={testID}>
      <QrPlaceholder />
      <Text size={12} color={tokens.colors.mutedForeground} style={{ marginTop: 8 }} numberOfLines={1}>{url}</Text>
      <TouchableOpacity onPress={onSave} accessibilityLabel="Save QR" accessibilityRole="button" style={{ marginTop: 10, alignSelf: 'flex-start', paddingHorizontal: 12, paddingVertical: 8, borderWidth: 1, borderColor: tokens.colors.border, borderRadius: 12 }}>
        <Text size={13} weight="600" color={tokens.colors.primary}>Save</Text>
      </TouchableOpacity>
    </View>
  )
}

export default React.memo(QrBlock)



