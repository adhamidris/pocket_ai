import React from 'react'
import { View, Text, TouchableOpacity, ScrollView } from 'react-native'
import { tokens } from '../../ui/tokens'
import { SimulationInput, SimulationResult, Condition, Action } from '../../types/automations'

export interface SimulatorPanelProps { input: SimulationInput; result?: SimulationResult; onRun: () => void; testID?: string }

const pretty = (obj: any) => JSON.stringify(obj, null, 2)

const SimulatorPanel: React.FC<SimulatorPanelProps> = ({ input, result, onRun, testID }) => {
  return (
    <View testID={testID} style={{ borderWidth: 1, borderColor: tokens.colors.border, borderRadius: 12, padding: 12 }}>
      <View style={{ flexDirection: 'row', alignItems: 'center', justifyContent: 'space-between' }}>
        <Text style={{ color: tokens.colors.cardForeground, fontWeight: '700' }}>Simulator</Text>
        <TouchableOpacity onPress={onRun} accessibilityLabel="Run simulation" accessibilityRole="button" style={{ paddingHorizontal: 12, paddingVertical: 8, borderRadius: 10, borderWidth: 1, borderColor: tokens.colors.border }}>
          <Text style={{ color: tokens.colors.primary, fontWeight: '700' }}>Run</Text>
        </TouchableOpacity>
      </View>
      <ScrollView style={{ maxHeight: 200, marginTop: 8 }}>
        <Text style={{ color: tokens.colors.mutedForeground }}>Input</Text>
        <Text style={{ color: tokens.colors.cardForeground }}>{pretty(input)}</Text>
        {result && (
          <View style={{ marginTop: 12 }}>
            <Text style={{ color: tokens.colors.mutedForeground }}>Result</Text>
            <Text style={{ color: tokens.colors.cardForeground }}>{pretty(result)}</Text>
          </View>
        )}
      </ScrollView>
    </View>
  )
}

export default React.memo(SimulatorPanel)


