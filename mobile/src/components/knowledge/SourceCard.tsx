import React from 'react'
import { TouchableOpacity, View } from 'react-native'
import { KnowledgeSource } from '../../types/knowledge'
import { Text } from '../../ui/Text'
import { tokens } from '../../ui/tokens'
import StatusBadge from './StatusBadge'
import { Clock } from 'lucide-react-native'

export interface SourceCardProps { src: KnowledgeSource; onPress: (id: string) => void; testID?: string; queued?: boolean }

const iconFor = (k: KnowledgeSource['kind']) => k === 'url' ? '🔗' : k === 'upload' ? '📄' : '📝'

const SourceCard: React.FC<SourceCardProps> = ({ src, onPress, testID, queued }) => {
  return (
    <TouchableOpacity
      testID={testID}
      onPress={() => onPress(src.id)}
      accessibilityLabel={`Open source ${src.title}`}
      accessibilityRole="button"
      style={{ paddingVertical: 12, minHeight: 44 }}
    >
      <View style={{ borderWidth: 1, borderColor: tokens.colors.border, borderRadius: 12, padding: 12 }}>
        <View style={{ flexDirection: 'row', alignItems: 'center', justifyContent: 'space-between' }}>
          <View style={{ flexDirection: 'row', alignItems: 'center', gap: 8 }}>
            <Text size={18} weight="700" color={tokens.colors.cardForeground}>{iconFor(src.kind)}</Text>
            <Text size={14} weight="700" color={tokens.colors.cardForeground} numberOfLines={1}>{src.title}</Text>
          </View>
          <View style={{ flexDirection: 'row', alignItems: 'center', gap: 8 }}>
            {queued ? <Clock size={14} color={tokens.colors.mutedForeground as any} /> : null}
            <StatusBadge status={src.status} />
          </View>
        </View>
        <View style={{ flexDirection: 'row', alignItems: 'center', gap: 8, marginTop: 6 }}>
          <View style={{ paddingHorizontal: 8, paddingVertical: 4, borderRadius: 10, borderWidth: 1, borderColor: tokens.colors.border }}>
            <Text size={11} color={tokens.colors.mutedForeground}>{src.scope}</Text>
          </View>
          {src.lastTrainedTs && (
            <Text size={11} color={tokens.colors.mutedForeground}>Trained {new Date(src.lastTrainedTs).toLocaleString()}</Text>
          )}
        </View>
      </View>
    </TouchableOpacity>
  )
}

export default React.memo(SourceCard)


