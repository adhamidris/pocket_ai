import React from 'react'
import { View, Text, TouchableOpacity, Switch } from 'react-native'
import { tokens } from '../../ui/tokens'
import { AutoResponder } from '../../types/automations'
import { Clock } from 'lucide-react-native'

export interface ResponderCardProps { res: AutoResponder; onToggle: () => void; onEdit: () => void; testID?: string; queued?: boolean }

const ResponderCard: React.FC<ResponderCardProps> = ({ res, onToggle, onEdit, testID, queued }) => {
  return (
    <View testID={testID} style={{ borderWidth: 1, borderColor: tokens.colors.border, borderRadius: 12, padding: 12 }}>
      <View style={{ flexDirection: 'row', alignItems: 'center', justifyContent: 'space-between' }}>
        <View style={{ flexDirection: 'row', alignItems: 'center', gap: 6 }}>
          <Text style={{ color: tokens.colors.cardForeground, fontWeight: '700' }}>{res.name}</Text>
          {queued ? <Clock size={14} color={tokens.colors.mutedForeground as any} /> : null}
        </View>
        <Switch value={res.active} onValueChange={onToggle} accessibilityLabel={`Toggle ${res.name}`} />
      </View>
      <Text style={{ color: tokens.colors.mutedForeground, marginTop: 6 }} numberOfLines={2}>{res.message}</Text>
      <View style={{ flexDirection: 'row', gap: 8, marginTop: 8 }}>
        <TouchableOpacity onPress={onEdit} accessibilityLabel="Edit autoresponder" accessibilityRole="button" style={{ paddingHorizontal: 12, paddingVertical: 8, borderRadius: 10, borderWidth: 1, borderColor: tokens.colors.border }}>
          <Text style={{ color: tokens.colors.cardForeground, fontWeight: '600' }}>Edit</Text>
        </TouchableOpacity>
      </View>
    </View>
  )
}

export default React.memo(ResponderCard)


