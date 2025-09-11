import React from 'react'
import { View, Text, TouchableOpacity } from 'react-native'
import { ConsentTemplate } from '../../types/security'

export interface ConsentTemplateRowProps { tpl: ConsentTemplate; onEdit: () => void; onPublish: () => void; testID?: string }

const ConsentTemplateRow: React.FC<ConsentTemplateRowProps> = ({ tpl, onEdit, onPublish, testID }) => {
  return (
    <View testID={testID} style={{ paddingVertical: 10, borderBottomWidth: 1, borderBottomColor: '#1f2937', flexDirection: 'row', alignItems: 'center', justifyContent: 'space-between' }}>
      <View style={{ flex: 1 }}>
        <Text style={{ color: '#e5e7eb', fontWeight: '600' }}>{tpl.name} v{tpl.version}</Text>
        <Text style={{ color: '#9ca3af', fontSize: 12 }}>{tpl.languages.map(l => l.code).join(', ')} • {tpl.channels.join(', ')}</Text>
      </View>
      <View style={{ flexDirection: 'row', gap: 8 }}>
        <TouchableOpacity onPress={onEdit} accessibilityRole="button" accessibilityLabel={`Edit ${tpl.name}`} style={{ paddingHorizontal: 10, paddingVertical: 6, borderRadius: 8, borderWidth: 1, borderColor: '#374151' }}>
          <Text style={{ color: '#e5e7eb' }}>Edit</Text>
        </TouchableOpacity>
        <TouchableOpacity onPress={onPublish} accessibilityRole="button" accessibilityLabel={`Publish ${tpl.name}`} style={{ paddingHorizontal: 10, paddingVertical: 6, borderRadius: 8, borderWidth: 1, borderColor: '#374151' }}>
          <Text style={{ color: '#22c55e' }}>Publish</Text>
        </TouchableOpacity>
      </View>
    </View>
  )
}

export default React.memo(ConsentTemplateRow)


