import React from 'react'
import { View, Text, TouchableOpacity } from 'react-native'
import { useTheme } from '../../providers/ThemeProvider'
import { DataRetentionPolicy } from '../../types/security'

export interface RetentionSlidersProps { value: DataRetentionPolicy; onChange: (v: DataRetentionPolicy) => void; testID?: string }

const Row: React.FC<{ label: string; days: number; onSet: (v: number) => void; a11y: string }>=({ label, days, onSet, a11y }) => {
  const { theme } = useTheme()
  const dec = () => onSet(days === -1 ? 0 : Math.max(-1, days - 30))
  const inc = () => onSet(days === -1 ? 30 : days + 30)
  return (
    <View accessibilityLabel={a11y} style={{ flexDirection: 'row', alignItems: 'center', justifyContent: 'space-between', borderWidth: 1, borderColor: theme.color.border, borderRadius: 12, paddingHorizontal: 12, paddingVertical: 10, marginBottom: 8 }}>
      <Text style={{ color: theme.color.cardForeground }}>{label}</Text>
      <View style={{ flexDirection: 'row', alignItems: 'center', gap: 8 }}>
        <TouchableOpacity onPress={dec} accessibilityRole="button" accessibilityLabel={`Decrease ${label}`} style={{ paddingHorizontal: 10, paddingVertical: 8, borderRadius: 8, borderWidth: 1, borderColor: theme.color.border, minHeight: 44 }}>
          <Text style={{ color: theme.color.cardForeground }}>-</Text>
        </TouchableOpacity>
        <Text style={{ color: theme.color.mutedForeground, minWidth: 80, textAlign: 'center' }}>{days === -1 ? 'Indefinite' : `${days} days`}</Text>
        <TouchableOpacity onPress={inc} accessibilityRole="button" accessibilityLabel={`Increase ${label}`} style={{ paddingHorizontal: 10, paddingVertical: 8, borderRadius: 8, borderWidth: 1, borderColor: theme.color.border, minHeight: 44 }}>
          <Text style={{ color: theme.color.cardForeground }}>+</Text>
        </TouchableOpacity>
        <TouchableOpacity onPress={() => onSet(-1)} accessibilityRole="button" accessibilityLabel={`Set ${label} to indefinite`} style={{ paddingHorizontal: 10, paddingVertical: 8, borderRadius: 8, borderWidth: 1, borderColor: theme.color.border, minHeight: 44 }}>
          <Text style={{ color: theme.color.cardForeground }}>-1</Text>
        </TouchableOpacity>
      </View>
    </View>
  )
}

const Warning: React.FC<{ show: boolean }>=({ show }) => {
  if (!show) return null
  return <Text style={{ color: '#f59e0b', marginBottom: 8 }}>Warning: Indefinite retention may require additional consent/policy (UI-only).</Text>
}

const RetentionSliders: React.FC<RetentionSlidersProps> = ({ value, onChange, testID }) => {
  const set = (k: keyof DataRetentionPolicy, v: number|boolean) => onChange({ ...value, [k]: v } as any)
  return (
    <View testID={testID}>
      <Row label="Conversations" days={value.conversationsDays} onSet={(v) => set('conversationsDays', v)} a11y="Conversations retention" />
      <Warning show={value.conversationsDays === -1} />
      <Row label="Messages" days={value.messagesDays} onSet={(v) => set('messagesDays', v)} a11y="Messages retention" />
      <Row label="Audit events" days={value.auditDays} onSet={(v) => set('auditDays', v)} a11y="Audit retention" />
    </View>
  )
}

export default React.memo(RetentionSliders)


