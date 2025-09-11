import React from 'react'
import { View, Text, TouchableOpacity } from 'react-native'
import { DeletionRequest } from '../../types/security'

export interface DeletionRowProps { req: DeletionRequest; onApprove: () => void; onReject: () => void; onView?: () => void; testID?: string }

const DeletionRow: React.FC<DeletionRowProps> = ({ req, onApprove, onReject, onView, testID }) => {
  return (
    <View testID={testID} style={{ paddingVertical: 10, borderBottomWidth: 1, borderBottomColor: '#1f2937' }}>
      <Text style={{ color: '#e5e7eb', fontWeight: '600' }}>{req.subject} • {req.status}</Text>
      <Text style={{ color: '#9ca3af', fontSize: 12 }}>{new Date(req.submittedAt).toLocaleString()} {req.refId ? `• ${req.refId}` : ''}</Text>
      <View style={{ flexDirection: 'row', gap: 8, marginTop: 6 }}>
        <TouchableOpacity onPress={onApprove} accessibilityRole="button" accessibilityLabel="Approve deletion" style={{ paddingHorizontal: 10, paddingVertical: 6, borderRadius: 8, borderWidth: 1, borderColor: '#374151' }}>
          <Text style={{ color: '#22c55e' }}>Approve</Text>
        </TouchableOpacity>
        <TouchableOpacity onPress={onReject} accessibilityRole="button" accessibilityLabel="Reject deletion" style={{ paddingHorizontal: 10, paddingVertical: 6, borderRadius: 8, borderWidth: 1, borderColor: '#374151' }}>
          <Text style={{ color: '#ef4444' }}>Reject</Text>
        </TouchableOpacity>
        {onView ? (
          <TouchableOpacity onPress={onView} accessibilityRole="button" accessibilityLabel="View details" style={{ paddingHorizontal: 10, paddingVertical: 6, borderRadius: 8, borderWidth: 1, borderColor: '#374151' }}>
            <Text style={{ color: '#e5e7eb' }}>View</Text>
          </TouchableOpacity>
        ) : null}
      </View>
    </View>
  )
}

export default React.memo(DeletionRow)


