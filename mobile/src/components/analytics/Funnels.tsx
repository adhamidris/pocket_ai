import React from 'react'
import { View, Text } from 'react-native'
import { tokens } from '../../ui/tokens'

export interface FunnelsProps { testID?: string }

type Step = { label: string; count: number; note?: string }

const Bar: React.FC<{ pct: number; color?: string }>=({ pct, color }) => (
  <View style={{ height: 14, borderRadius: 7, backgroundColor: tokens.colors.muted, overflow: 'hidden' }}>
    <View style={{ width: `${Math.max(0, Math.min(100, pct))}%`, height: '100%', backgroundColor: color || tokens.colors.primary }} />
  </View>
)

const FunnelCard: React.FC<{ title: string; steps: Step[]; color?: string }>=({ title, steps, color }) => {
  const max = Math.max(...steps.map((s) => s.count)) || 1
  return (
    <View style={{ borderWidth: 1, borderColor: tokens.colors.border, borderRadius: 12, padding: 12, flex: 1 }}>
      <Text style={{ color: tokens.colors.cardForeground, fontWeight: '700', marginBottom: 8 }}>{title}</Text>
      <View style={{ gap: 10 }}>
        {steps.map((s, idx) => {
          const prev = idx === 0 ? s.count : steps[idx - 1].count
          const drop = prev > 0 ? Math.round(((prev - s.count) / prev) * 100) : 0
          const pct = Math.round((s.count / max) * 100)
          return (
            <View key={idx}>
              <View style={{ flexDirection: 'row', alignItems: 'center', justifyContent: 'space-between' }}>
                <Text style={{ color: tokens.colors.cardForeground }}>{s.label}</Text>
                <Text style={{ color: tokens.colors.mutedForeground }}>{s.count.toLocaleString()}</Text>
              </View>
              <Bar pct={pct} color={color} />
              <View style={{ flexDirection: 'row', justifyContent: 'space-between', marginTop: 2 }}>
                <Text style={{ color: tokens.colors.mutedForeground, fontSize: 12 }}>{s.note || ''}</Text>
                {idx > 0 && <Text style={{ color: tokens.colors.error, fontSize: 12 }}>▼ {drop}%</Text>}
              </View>
            </View>
          )
        })}
      </View>
    </View>
  )
}

const rand = (min: number, max: number) => Math.floor(Math.random() * (max - min + 1)) + min

const Funnels: React.FC<FunnelsProps> = ({ testID }) => {
  const setup: Step[] = [
    { label: 'Signed up', count: 1000 },
    { label: 'Connected channel', count: 780, note: 'Add WhatsApp or Instagram' },
    { label: 'Published link', count: 610, note: 'Embed or share link' },
    { label: 'Trained knowledge', count: 420, note: 'Added ≥1 source' },
    { label: 'First resolution', count: 280 },
  ]

  const deflection: Step[] = [
    { label: 'New conversations', count: 2000 },
    { label: 'Auto-answer', count: 1200, note: 'Answer from knowledge' },
    { label: 'No agent needed', count: 800 },
  ]

  const resolution: Step[] = [
    { label: 'New conversations', count: 2000 },
    { label: 'Assigned', count: 1400 },
    { label: 'Resolved', count: 1200, note: `Reopened ${rand(5, 15)}%` },
  ]

  return (
    <View testID={testID} style={{ borderWidth: 1, borderColor: tokens.colors.border, borderRadius: 12, overflow: 'hidden' }}>
      <View style={{ padding: 12, backgroundColor: tokens.colors.card, borderBottomWidth: 1, borderBottomColor: tokens.colors.border }}>
        <Text style={{ color: tokens.colors.cardForeground, fontWeight: '700' }}>Funnels</Text>
      </View>
      <View style={{ padding: 12, gap: 12 }}>
        <View style={{ flexDirection: 'row', gap: 12 }}>
          <FunnelCard title="Setup" steps={setup} color={tokens.colors.primary} />
          <FunnelCard title="Deflection" steps={deflection} color={tokens.colors.success} />
        </View>
        <View>
          <FunnelCard title="Resolution" steps={resolution} color={tokens.colors.warning} />
        </View>
      </View>
    </View>
  )
}

export default React.memo(Funnels)


