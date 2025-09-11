import React from 'react'
import { View, Text, TouchableOpacity } from 'react-native'
import { ExportJob } from '../../types/security'

export interface ExportJobRowProps { job: ExportJob; onOpen?: () => void; testID?: string }

const ExportJobRow: React.FC<ExportJobRowProps> = ({ job, onOpen, testID }) => {
  return (
    <View testID={testID} style={{ paddingVertical: 10, borderBottomWidth: 1, borderBottomColor: '#1f2937', flexDirection: 'row', alignItems: 'center', justifyContent: 'space-between' }}>
      <View style={{ flex: 1 }}>
        <Text style={{ color: '#e5e7eb', fontWeight: '600' }}>{job.scope} export • {job.state}</Text>
        <Text style={{ color: '#9ca3af', fontSize: 12 }}>{Math.round(job.progress)}% • {new Date(job.createdAt).toLocaleString()}</Text>
      </View>
      {job.downloadUrl ? (
        <TouchableOpacity onPress={onOpen} accessibilityRole="button" accessibilityLabel="Open export" style={{ paddingHorizontal: 10, paddingVertical: 6, borderRadius: 8, borderWidth: 1, borderColor: '#374151' }}>
          <Text style={{ color: '#e5e7eb' }}>Open</Text>
        </TouchableOpacity>
      ) : null}
    </View>
  )
}

export default React.memo(ExportJobRow)


