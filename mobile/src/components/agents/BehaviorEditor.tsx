import React from 'react'
import { View, TouchableOpacity, TextInput, Text } from 'react-native'
import { tokens } from '../../ui/tokens'
import { AiBehavior } from '../../types/agents'
import TagChip from '../crm/TagChip'

export interface BehaviorEditorProps {
  value: AiBehavior
  onChange: (v: AiBehavior) => void
  testID?: string
}

const clamp01 = (n: number) => Math.max(0, Math.min(1, n))

const BehaviorEditor: React.FC<BehaviorEditorProps> = ({ value, onChange, testID }) => {
  const [temp, setTemp] = React.useState<number>(value.temperature ?? 0.7)
  const [tone, setTone] = React.useState<AiBehavior['tone']>(value.tone ?? 'friendly')
  const [prompt, setPrompt] = React.useState<string>(value.systemPrompt ?? '')

  React.useEffect(() => {
    onChange({ temperature: temp, tone, systemPrompt: prompt })
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [temp, tone, prompt])

  const adjustTemp = (delta: number) => setTemp((t) => clamp01(parseFloat((t + delta).toFixed(2))))

  const tones: Array<AiBehavior['tone']> = ['neutral', 'friendly', 'formal']

  const progressPct = `${Math.round((temp) * 100)}%`

  return (
    <View testID={testID}>
      {/* Temperature */}
      <Text style={{ color: tokens.colors.cardForeground, fontWeight: '700', marginBottom: 6 }}>Temperature</Text>
      <View style={{ flexDirection: 'row', alignItems: 'center', gap: 8, marginBottom: 8 }}>
        <TouchableOpacity onPress={() => adjustTemp(-0.1)} accessibilityLabel="Decrease temperature" accessibilityRole="button" style={{ paddingHorizontal: 10, paddingVertical: 6, borderWidth: 1, borderColor: tokens.colors.border, borderRadius: 8 }}>
          <Text style={{ color: tokens.colors.cardForeground }}>−</Text>
        </TouchableOpacity>
        <View style={{ flex: 1, height: 8, backgroundColor: tokens.colors.muted, borderRadius: 4 }}>
          <View style={{ width: progressPct, height: '100%', backgroundColor: tokens.colors.primary, borderRadius: 4 }} />
        </View>
        <TouchableOpacity onPress={() => adjustTemp(0.1)} accessibilityLabel="Increase temperature" accessibilityRole="button" style={{ paddingHorizontal: 10, paddingVertical: 6, borderWidth: 1, borderColor: tokens.colors.border, borderRadius: 8 }}>
          <Text style={{ color: tokens.colors.cardForeground }}>+</Text>
        </TouchableOpacity>
        <Text style={{ color: tokens.colors.mutedForeground, width: 48, textAlign: 'right' }}>{temp.toFixed(2)}</Text>
      </View>

      {/* Tone */}
      <Text style={{ color: tokens.colors.cardForeground, fontWeight: '700', marginBottom: 6, marginTop: 4 }}>Tone</Text>
      <View style={{ flexDirection: 'row', gap: 8, marginBottom: 8 }}>
        {tones.map((t) => (
          <TagChip key={t} label={t} selected={tone === t} onPress={() => setTone(t)} />
        ))}
      </View>

      {/* System Prompt */}
      <Text style={{ color: tokens.colors.cardForeground, fontWeight: '700', marginBottom: 6, marginTop: 4 }}>System Prompt</Text>
      <View style={{ borderWidth: 1, borderColor: tokens.colors.border, borderRadius: 12, backgroundColor: tokens.colors.card }}>
        <TextInput
          value={prompt}
          onChangeText={setPrompt}
          placeholder="You are a helpful assistant..."
          placeholderTextColor={tokens.colors.placeholder as any}
          style={{ paddingHorizontal: 12, paddingVertical: 10, color: tokens.colors.cardForeground, minHeight: 96, textAlignVertical: 'top' }}
          multiline
        />
      </View>
    </View>
  )
}

export default React.memo(BehaviorEditor)



