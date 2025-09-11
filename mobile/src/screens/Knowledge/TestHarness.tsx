import React from 'react'
import { SafeAreaView, View, Text, TextInput, TouchableOpacity, FlatList } from 'react-native'
import { useSafeAreaInsets } from 'react-native-safe-area-context'
import { useTheme } from '../../providers/ThemeProvider'
import { useNavigation } from '@react-navigation/native'
import { TestCase } from '../../types/knowledge'
import TagChip from '../../components/crm/TagChip'
import { track } from '../../lib/analytics'

const cannedActual = (q: string) => q ? `Demo answer for: ${q}` : '—'

const TestHarness: React.FC = () => {
  const { theme } = useTheme()
  const insets = useSafeAreaInsets()
  const navigation = useNavigation<any>()

  const [question, setQuestion] = React.useState('')
  const [expected, setExpected] = React.useState('')
  const [tag, setTag] = React.useState('general')
  const [tests, setTests] = React.useState<TestCase[]>([])
  const [filterTag, setFilterTag] = React.useState<string | undefined>()

  const actual = cannedActual(question)
  const pass = expected.trim().length > 0 && expected.trim() === actual.trim()

  const addTest = () => {
    if (!question.trim() || !expected.trim()) return
    const id = `t-${Date.now()}`
    const t: TestCase = { id, question: question.trim(), expectedAnswer: expected.trim(), tags: [tag], lastRunAt: Date.now(), lastPass: pass }
    setTests((arr) => [t, ...arr])
    track('knowledge.test_case', { action: 'create' })
    setQuestion('')
    setExpected('')
  }

  const runAll = () => {
    const now = Date.now()
    setTests((arr) => arr.map((t) => ({
      ...t,
      lastRunAt: now,
      lastPass: (t.expectedAnswer || '').trim().length > 0 && (t.expectedAnswer || '').trim() === cannedActual(t.question).trim(),
    })))
    const numPass = tests.filter((t) => (t.expectedAnswer || '').trim().length > 0 && (t.expectedAnswer || '').trim() === cannedActual(t.question).trim()).length
    track('knowledge.test_case', { action: 'run_all', pass: numPass })
  }

  const visible = React.useMemo(() => tests.filter((t) => !filterTag || (t.tags || []).includes(filterTag)), [tests, filterTag])

  return (
    <SafeAreaView style={{ flex: 1, backgroundColor: theme.color.background }}>
      {/* Header */}
      <View style={{ paddingHorizontal: 16, paddingTop: insets.top + 8, paddingBottom: 12, borderBottomWidth: 1, borderBottomColor: theme.color.border }}>
        <View style={{ flexDirection: 'row', alignItems: 'center', justifyContent: 'space-between' }}>
          <TouchableOpacity onPress={() => navigation.goBack()} accessibilityLabel="Back" accessibilityRole="button" style={{ padding: 8 }}>
            <Text style={{ color: theme.color.primary, fontWeight: '600' }}>{'< Back'}</Text>
          </TouchableOpacity>
          <Text style={{ color: theme.color.foreground, fontSize: 18, fontWeight: '700' }}>Test Harness</Text>
          <View style={{ width: 64 }} />
        </View>
      </View>

      <View style={{ padding: 16 }}>
        {/* Input */}
        <Text style={{ color: theme.color.mutedForeground, marginBottom: 6 }}>Question</Text>
        <TextInput value={question} onChangeText={setQuestion} placeholder="Enter question" placeholderTextColor={theme.color.placeholder} accessibilityLabel="Question" style={{ borderWidth: 1, borderColor: theme.color.border, borderRadius: 12, paddingHorizontal: 12, paddingVertical: 10, color: theme.color.cardForeground, marginBottom: 12 }} />
        <Text style={{ color: theme.color.mutedForeground, marginBottom: 6 }}>Expected</Text>
        <TextInput value={expected} onChangeText={setExpected} placeholder="Enter expected answer" placeholderTextColor={theme.color.placeholder} accessibilityLabel="Expected" multiline style={{ minHeight: 80, textAlignVertical: 'top', borderWidth: 1, borderColor: theme.color.border, borderRadius: 12, paddingHorizontal: 12, paddingVertical: 10, color: theme.color.cardForeground, marginBottom: 12 }} />
        <Text style={{ color: theme.color.mutedForeground, marginBottom: 6 }}>Actual (demo)</Text>
        <View style={{ borderWidth: 1, borderColor: theme.color.border, borderRadius: 12, padding: 10, marginBottom: 12 }}>
          <Text style={{ color: theme.color.cardForeground }}>{actual}</Text>
        </View>

        {/* Tag */}
        <View style={{ flexDirection: 'row', alignItems: 'center', gap: 8, marginBottom: 12 }}>
          {(['general', 'billing', 'order', 'shipping'] as const).map((t) => (
            <TagChip key={t} label={t} selected={tag === t} onPress={() => setTag(t)} />
          ))}
        </View>

        {/* Pass/Fail */}
        <View style={{ flexDirection: 'row', alignItems: 'center', gap: 8, marginBottom: 12 }}>
          <Text style={{ color: pass ? theme.color.success : theme.color.error, fontWeight: '700' }}>{pass ? 'PASS' : 'FAIL'}</Text>
          <TouchableOpacity onPress={addTest} accessibilityLabel="Add Test" accessibilityRole="button" style={{ paddingHorizontal: 12, paddingVertical: 8, borderWidth: 1, borderColor: theme.color.border, borderRadius: 12 }}>
            <Text style={{ color: theme.color.cardForeground, fontWeight: '600' }}>Add Test</Text>
          </TouchableOpacity>
          <TouchableOpacity onPress={runAll} accessibilityLabel="Run All" accessibilityRole="button" style={{ paddingHorizontal: 12, paddingVertical: 8, borderWidth: 1, borderColor: theme.color.border, borderRadius: 12 }}>
            <Text style={{ color: theme.color.primary, fontWeight: '700' }}>Run All</Text>
          </TouchableOpacity>
        </View>

        {/* Filter by tag */}
        <View style={{ flexDirection: 'row', alignItems: 'center', gap: 8, marginBottom: 8 }}>
          <Text style={{ color: theme.color.mutedForeground }}>Filter:</Text>
          {(['all', 'general', 'billing', 'order', 'shipping'] as const).map((t) => (
            <TagChip key={t} label={t} selected={(!filterTag && t === 'all') || filterTag === t} onPress={() => setFilterTag(t === 'all' ? undefined : t)} />
          ))}
        </View>
      </View>

      {/* Tests list */}
      <FlatList
        style={{ paddingHorizontal: 16 }}
        data={visible}
        keyExtractor={(t) => t.id}
        renderItem={({ item }) => (
          <View style={{ padding: 12, borderWidth: 1, borderColor: theme.color.border, borderRadius: 12, marginBottom: 8 }}>
            <Text style={{ color: theme.color.cardForeground, fontWeight: '600' }}>{item.question}</Text>
            <Text style={{ color: theme.color.mutedForeground }}>Expected: {item.expectedAnswer}</Text>
            <Text style={{ color: (item.lastPass ? theme.color.success : theme.color.error) }}>{item.lastPass ? 'PASS' : 'FAIL'} {item.lastRunAt ? `• ${new Date(item.lastRunAt).toLocaleString()}` : ''}</Text>
            {!!(item.tags || []).length && <Text style={{ color: theme.color.mutedForeground }}>Tags: {(item.tags || []).join(', ')}</Text>}
          </View>
        )}
      />
    </SafeAreaView>
  )
}

export default TestHarness


