import React from 'react'
import { TouchableOpacity, View } from 'react-native'
import { FailureItem } from '../../types/knowledge'
import { Text } from '../../ui/Text'
import { tokens } from '../../ui/tokens'

export interface FailureRowProps { item: FailureItem; onPress: (id: string) => void; testID?: string }

const FailureRow: React.FC<FailureRowProps> = ({ item, onPress, testID }) => {
  return (
    <TouchableOpacity
      testID={testID}
      onPress={() => onPress(item.id)}
      accessibilityLabel={`Failure ${item.question}`}
      accessibilityRole="button"
      style={{ paddingVertical: 12, minHeight: 44 }}
    >
      <View style={{ borderWidth: 1, borderColor: tokens.colors.border, borderRadius: 12, padding: 12 }}>
        <Text size={14} weight="600" color={tokens.colors.cardForeground} numberOfLines={1}>{item.question}</Text>
        <Text size={12} color={tokens.colors.mutedForeground}>Conf {Math.round(item.confidence * 100)}% • {new Date(item.occurredAt).toLocaleString()}</Text>
      </View>
    </TouchableOpacity>
  )
}

export default React.memo(FailureRow)


