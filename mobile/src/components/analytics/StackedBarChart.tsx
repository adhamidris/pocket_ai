import React from 'react'
import { View, Text } from 'react-native'
import { tokens } from '../../ui/tokens'

export interface StackedBarChartProps { stacks: { label: string; parts: { name: string; value: number }[] }[]; testID?: string }

const colors = [tokens.colors.primary, tokens.colors.success, tokens.colors.warning, tokens.colors.error, tokens.colors.accent]

const StackedBarChart: React.FC<StackedBarChartProps> = ({ stacks, testID }) => {
  const totals = stacks.map((s) => s.parts.reduce((a, b) => a + (b.value || 0), 0))
  const max = Math.max(1, ...totals)
  return (
    <View testID={testID} accessibilityRole="image" accessibilityLabel="Stacked bar chart" accessible>
      {stacks.map((s, idx) => {
        const total = totals[idx] || 1
        return (
          <View key={idx} style={{ marginBottom: 8 }}>
            <View style={{ flexDirection: 'row', alignItems: 'center', justifyContent: 'space-between' }}>
              <Text style={{ color: tokens.colors.cardForeground }}>{s.label}</Text>
              <Text style={{ color: tokens.colors.mutedForeground }}>{total}</Text>
            </View>
            <View style={{ height: 10, borderRadius: 6, backgroundColor: tokens.colors.muted, flexDirection: 'row', overflow: 'hidden' }}>
              {s.parts.map((p, i) => (
                <View key={i} style={{ width: `${(p.value / total) * 100}%`, height: '100%', backgroundColor: colors[i % colors.length], opacity: 0.9 }} />
              ))}
            </View>
            <Text style={{ color: tokens.colors.mutedForeground, fontSize: 12, marginTop: 2 }}>Total {total} across {s.parts.length} categories</Text>
          </View>
        )
      })}
    </View>
  )
}

export default React.memo(StackedBarChart)


