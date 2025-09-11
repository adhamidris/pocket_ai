import React from 'react'
import { View, Text } from 'react-native'
import { AnswerChunk, AskAnswer } from '../../types/assistant'
import { useTheme } from '../../providers/ThemeProvider'
import CitationChip from './CitationChip'
import TtsButton from './TtsButton'
import SafetyBadge from './SafetyBadge'
import ToolSuggestionRow from './ToolSuggestionRow'
import FollowUpChips from './FollowUpChips'

const renderChunk = (c: AnswerChunk, idx: number, theme: any) => {
  switch (c.kind) {
    case 'paragraph':
      return <Text key={idx} style={{ color: theme.color.foreground, lineHeight: 22 }}>{c.text}</Text>
    case 'list':
      return (
        <View key={idx} style={{ gap: 6 }}>
          {(c.items ?? []).map((it, i) => (
            <Text key={i} style={{ color: theme.color.foreground }}>• {it}</Text>
          ))}
        </View>
      )
    case 'kpi':
      return (
        <View key={idx} style={{ flexDirection: 'row', justifyContent: 'space-between', alignItems: 'center', backgroundColor: theme.color.secondary, borderRadius: theme.radius.md, padding: 12 }}>
          <Text style={{ color: theme.color.mutedForeground }}>{c.kpi?.label}</Text>
          <Text style={{ color: theme.color.foreground, fontWeight: '700' }}>{c.kpi?.value} {c.kpi?.delta && <Text style={{ color: theme.color.mutedForeground }}>({c.kpi?.delta})</Text>}</Text>
        </View>
      )
    case 'callout':
      return (
        <View key={idx} style={{ backgroundColor: theme.color.accent, borderRadius: theme.radius.md, padding: 12 }}>
          <Text style={{ color: theme.color.foreground }}>{c.text}</Text>
        </View>
      )
    case 'table':
      return (
        <View key={idx} style={{ borderWidth: 1, borderColor: theme.color.border, borderRadius: theme.radius.md }}>
          <Text style={{ color: theme.color.mutedForeground, padding: 12 }}>Table preview (UI-only)</Text>
        </View>
      )
    case 'chart':
      return (
        <View key={idx} style={{ height: 120, borderRadius: theme.radius.md, backgroundColor: theme.color.secondary, alignItems: 'center', justifyContent: 'center' }}>
          <Text style={{ color: theme.color.mutedForeground }}>Chart: {c.chartKey}</Text>
        </View>
      )
    case 'code':
      return (
        <View key={idx} style={{ backgroundColor: '#111', borderRadius: theme.radius.md, padding: 12 }}>
          <Text style={{ color: '#0f0', fontFamily: 'Menlo' }}>{c.code}</Text>
        </View>
      )
    default:
      return null
  }
}

export const AnswerCard: React.FC<{ answer: AskAnswer; hidePII?: boolean; onTool?: (key: string) => void; onFollowUp?: (s: string) => void; autoplayTts?: boolean }>
  = ({ answer, hidePII, onTool, onFollowUp, autoplayTts }) => {
  const { theme } = useTheme()
  const [playing, setPlaying] = React.useState(false)
  React.useEffect(() => {
    if (autoplayTts && answer.chunks.some(c => c.kind === 'paragraph' || c.kind === 'list')) {
      setPlaying(true)
      const t = setTimeout(() => setPlaying(false), 1500)
      return () => clearTimeout(t)
    }
  }, [])
  return (
    <View style={{ backgroundColor: theme.color.card, borderWidth: 1, borderColor: theme.color.border, borderRadius: theme.radius.lg, padding: 12, gap: 12 }}>
      {answer.safety && <SafetyBadge piiMasked={!!answer.safety.piiMasked} />}
      {answer.chunks.map((c, i) => (
        <View key={i}>
          {renderChunk(c, i, theme)}
          {!!c.citations?.length && (
            <View style={{ marginTop: 8 }}>
              <CitationChip citations={c.citations} hidePII={hidePII} />
            </View>
          )}
        </View>
      ))}
      <View style={{ flexDirection: 'row', gap: 8 }}>
        <TtsButton playing={playing} onPlay={() => { setPlaying(true); setTimeout(() => setPlaying(false), 1500) }} onStop={() => setPlaying(false)} />
      </View>
      {!!answer.toolSuggestions?.length && (
        <ToolSuggestionRow suggestions={answer.toolSuggestions} onPress={(t) => onTool?.(t.key)} />
      )}
      {!!answer.followUps?.length && (
        <FollowUpChips followUps={answer.followUps} onSelect={onFollowUp} />
      )}
      {answer.chunks.length >= 3 && (
        <Text style={{ color: theme.color.mutedForeground, fontSize: 12 }}>
          {answer.safety?.disclaimer || 'This is a preview; verify before acting.'}
        </Text>
      )}
    </View>
  )
}

export default AnswerCard
export const MemoAnswerCard = React.memo(AnswerCard)


