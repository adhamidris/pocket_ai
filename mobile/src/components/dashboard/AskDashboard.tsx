import React from 'react'
import { TextInput, TouchableOpacity, ActivityIndicator } from 'react-native'
import Box from '../../ui/Box'
import { Text } from '../../ui/Text'
import { tokens } from '../../ui/tokens'

export interface AskDashboardProps {
  testID?: string
}

const AskDashboard: React.FC<AskDashboardProps> = ({ testID }) => {
  const [question, setQuestion] = React.useState('')
  const [loading, setLoading] = React.useState(false)
  const [answer, setAnswer] = React.useState<string | null>(null)

  const handleSubmit = () => {
    if (!question.trim()) return
    setLoading(true)
    setAnswer(null)
    setTimeout(() => {
      // Canned summary
      setAnswer(
        'Today: 43 msgs (+12% WoW). Live: 2; Urgent backlog: 2; FRT P50: 1.2s vs 2s target; Resolution: 78%; Top intents: order status (34%), billing (22%). Suggested: review urgent queue and connect channels.'
      )
      setLoading(false)
    }, 900)
  }

  return (
    <Box testID={testID} p={16} radius={16} style={{ backgroundColor: tokens.colors.card, borderWidth: 1, borderColor: tokens.colors.border }}>
      <Text size={14} weight="600" color={tokens.colors.cardForeground}>Ask the Dashboard</Text>
      <Box row align="center" gap={8} mt={12}>
        <Box style={{ flex: 1, borderWidth: 1, borderColor: tokens.colors.border, borderRadius: 12, backgroundColor: tokens.colors.background }}>
          <TextInput
            value={question}
            onChangeText={setQuestion}
            placeholder="What happened today?"
            placeholderTextColor={tokens.colors.placeholder}
            style={{ paddingHorizontal: 12, paddingVertical: 10, color: tokens.colors.cardForeground, fontSize: 14 }}
            editable={!loading}
            accessibilityLabel="Ask question"
          />
        </Box>
        <TouchableOpacity
          onPress={handleSubmit}
          disabled={loading || !question.trim()}
          accessibilityLabel="Submit question"
          accessibilityRole="button"
          style={{
            paddingHorizontal: 14,
            paddingVertical: 10,
            borderRadius: 12,
            backgroundColor: loading || !question.trim() ? tokens.colors.muted : tokens.colors.primary,
            minHeight: 44,
            alignItems: 'center',
            justifyContent: 'center',
          }}
        >
          {loading ? (
            <ActivityIndicator color="#fff" />
          ) : (
            <Text size={13} weight="600" color={'#ffffff'}>Ask</Text>
          )}
        </TouchableOpacity>
      </Box>

      {answer && (
        <Box mt={12} p={12} radius={12} style={{ backgroundColor: tokens.colors.accent, borderWidth: 1, borderColor: tokens.colors.border }}>
          <Text size={13} color={tokens.colors.cardForeground}>{answer}</Text>
        </Box>
      )}
    </Box>
  )
}

export default AskDashboard


