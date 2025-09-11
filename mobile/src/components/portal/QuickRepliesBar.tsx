import React from 'react'
import { View } from 'react-native'
import QuickReplyChip from './QuickReplyChip'

export interface QuickRepliesBarProps { replies: string[]; onSelect: (text: string) => void; testID?: string }

const QuickRepliesBar: React.FC<QuickRepliesBarProps> = ({ replies, onSelect, testID }) => {
  if (!replies?.length) return null
  return (
    <View testID={testID} style={{ flexDirection: 'row', flexWrap: 'wrap', gap: 8, paddingHorizontal: 12, paddingVertical: 8 }}>
      {replies.slice(0, 6).map((r, idx) => (
        <QuickReplyChip key={`${r}-${idx}`} label={r} onPress={() => onSelect(r)} />
      ))}
    </View>
  )
}

export default React.memo(QuickRepliesBar)


