import React from 'react'
import { View, Text, TouchableOpacity } from 'react-native'
import { AuditEvent } from '../../types/security'
import RiskBadge from './RiskBadge'

export interface AuditRowProps { evt: AuditEvent; onPress?: () => void; testID?: string }

const AuditRow: React.FC<AuditRowProps> = ({ evt, onPress, testID }) => {
  return (
    <TouchableOpacity onPress={onPress} testID={testID} accessibilityRole="button" accessibilityLabel={`Audit event ${evt.action}`} style={{ paddingVertical: 10, borderBottomWidth: 1, borderBottomColor: '#1f2937' }}>
      <View style={{ flexDirection: 'row', alignItems: 'center', justifyContent: 'space-between' }}>
        <View style={{ flex: 1 }}>
          <Text style={{ color: '#e5e7eb', fontWeight: '600' }}>{evt.action}</Text>
          <Text style={{ color: '#9ca3af', fontSize: 12 }}>{new Date(evt.ts).toLocaleString()} • {evt.actor} • {evt.entityType}{evt.entityId ? `#${evt.entityId}` : ''}</Text>
          {evt.details ? <Text style={{ color: '#9ca3af', marginTop: 2 }}>{evt.details}</Text> : null}
        </View>
        <RiskBadge level={evt.risk} />
      </View>
    </TouchableOpacity>
  )
}

export default React.memo(AuditRow)


