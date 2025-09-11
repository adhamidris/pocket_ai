import React, { useMemo, useState } from 'react'
import { View, Text, TouchableOpacity } from 'react-native'
import { Modal } from '../ui/Modal'
import { useTheme } from '../../providers/ThemeProvider'
import { Survey } from '../../types/help'

export const SurveyModal: React.FC<{ visible: boolean; survey: Survey; onClose: () => void; onSubmit?: (value: number | string) => void }> = ({ visible, survey, onClose, onSubmit }) => {
  const { theme } = useTheme()
  const [value, setValue] = useState<number | string | undefined>()
  const options = useMemo(() => {
    if (survey.scale === 'nps') return Array.from({ length: 11 }, (_, i) => String(i))
    if (survey.scale === 'csat') return ['1','2','3','4','5']
    if (survey.scale === 'likert' && survey.options) return survey.options
    return survey.options ?? []
  }, [survey])

  return (
    <Modal visible={visible} onClose={onClose} title={survey.name}>
      <Text style={{ color: theme.color.cardForeground, marginBottom: 12 }}>{survey.question}</Text>
      <View style={{ flexDirection: 'row', flexWrap: 'wrap', gap: 8 }}>
        {options.map(opt => (
          <TouchableOpacity key={opt} onPress={() => setValue(opt)} accessibilityLabel={`Select ${opt}`} style={{ paddingHorizontal: 12, paddingVertical: 10, borderRadius: 12, borderWidth: 1, borderColor: theme.color.border, backgroundColor: value === opt ? theme.color.primary : 'transparent' }}>
            <Text style={{ color: value === opt ? '#fff' : theme.color.cardForeground, fontWeight: '600' }}>{opt}</Text>
          </TouchableOpacity>
        ))}
      </View>
      <TouchableOpacity onPress={() => { if (value !== undefined) onSubmit?.(value as any); onClose() }} accessibilityLabel="Submit survey" style={{ marginTop: 16, height: 44, borderRadius: 12, backgroundColor: theme.color.primary, alignItems: 'center', justifyContent: 'center' }}>
        <Text style={{ color: '#fff', fontWeight: '700' }}>Submit</Text>
      </TouchableOpacity>
    </Modal>
  )
}


