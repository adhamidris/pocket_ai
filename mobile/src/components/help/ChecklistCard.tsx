import React from 'react'
import { View, Text, TouchableOpacity } from 'react-native'
import { Check } from 'lucide-react-native'
import { useTheme } from '../../providers/ThemeProvider'
import { Checklist, ChecklistStep } from '../../types/help'
import * as Localization from 'expo-localization'
import { helpStrings } from '../../content/help/strings'
import { track } from '../../lib/analytics'

export const ChecklistCard: React.FC<{
  checklist: Checklist
  onToggleStep?: (stepId: string) => void
  onCtaPress?: (step: ChecklistStep) => void
}> = ({ checklist, onToggleStep, onCtaPress }) => {
  const { theme } = useTheme()
  const doneCount = checklist.steps.filter(s => s.done).length
  const progressPct = Math.round((doneCount / Math.max(1, checklist.steps.length)) * 100)
  const lang = Localization.getLocales()[0]?.languageCode === 'ar' ? 'ar' : 'en'

  return (
    <View style={{ backgroundColor: theme.color.card, borderRadius: theme.radius.xl, padding: 16, borderWidth: 1, borderColor: theme.color.border }}>
      <Text style={{ color: theme.color.cardForeground, fontWeight: '700', fontSize: 16, marginBottom: 8 }}>{checklist.title || helpStrings[lang].quickstartTitle}</Text>
      <Text style={{ color: theme.color.mutedForeground, fontSize: 12, marginBottom: 12 }}>{helpStrings[lang].progress(progressPct)}</Text>
      <View style={{ gap: 10 }}>
        {checklist.steps.map(step => (
          <View key={step.id} style={{ flexDirection: 'row', alignItems: 'center', justifyContent: 'space-between' }}>
            <TouchableOpacity
              onPress={() => onToggleStep && onToggleStep(step.id)}
              accessibilityLabel={`Toggle ${step.title}`}
              style={{ width: 44, height: 44, borderRadius: 12, borderWidth: 1, borderColor: theme.color.border, alignItems: 'center', justifyContent: 'center', backgroundColor: step.done ? theme.color.success : 'transparent' }}
            >
              {step.done && <Check size={18} color={'#fff'} />}
            </TouchableOpacity>
            <View style={{ flex: 1, marginLeft: 12 }}>
              <Text style={{ color: theme.color.cardForeground, fontWeight: '600' }}>{step.title}</Text>
            </View>
            {step.cta && (
              <TouchableOpacity
                onPress={() => { try { if (!step.done) track('help.checklist.step_complete', { id: step.id }) } catch {}; onCtaPress && onCtaPress(step) }}
                accessibilityLabel={`Go to ${step.cta.label}`}
                style={{ paddingHorizontal: 12, paddingVertical: 10, backgroundColor: theme.color.primary, borderRadius: 12, minWidth: 44, minHeight: 44, justifyContent: 'center' }}
              >
                <Text style={{ color: '#fff', fontWeight: '600' }}>{step.cta.label}</Text>
              </TouchableOpacity>
            )}
          </View>
        ))}
      </View>
    </View>
  )
}


