import React from 'react'
import { View, TouchableOpacity, Text, Alert } from 'react-native'
import { tokens } from '../../ui/tokens'

export interface ExportBarProps { onExportCsv?: () => void; onExportPdf?: () => void; onSave?: () => void; onSchedule?: () => void; testID?: string }

const ExportBar: React.FC<ExportBarProps> = ({ onExportCsv, onExportPdf, onSave, onSchedule, testID }) => {
  const Btn: React.FC<{ label: string; onPress?: () => void }> = ({ label, onPress }) => (
    <TouchableOpacity onPress={onPress || (() => Alert.alert(label, 'UI-only'))} accessibilityLabel={label} accessibilityRole="button" style={{ paddingHorizontal: 12, paddingVertical: 8, borderRadius: 12, borderWidth: 1, borderColor: tokens.colors.border }}>
      <Text style={{ color: tokens.colors.cardForeground, fontWeight: '600' }}>{label}</Text>
    </TouchableOpacity>
  )
  return (
    <View testID={testID} style={{ flexDirection: 'row', gap: 8 }}>
      <Btn label="Export CSV" onPress={onExportCsv} />
      <Btn label="Export PDF" onPress={onExportPdf} />
      <Btn label="Save Report" onPress={onSave} />
      <Btn label="Schedule" onPress={onSchedule} />
    </View>
  )
}

export default React.memo(ExportBar)


