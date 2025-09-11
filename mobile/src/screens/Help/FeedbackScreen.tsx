import React from 'react'
import { SafeAreaView, View, Text, TouchableOpacity, FlatList } from 'react-native'
import { useSafeAreaInsets } from 'react-native-safe-area-context'
import { useTheme } from '../../providers/ThemeProvider'
import { useNavigation } from '@react-navigation/native'
import { FeedbackForm } from '../../components/help/FeedbackForm'
import { track } from '../../lib/analytics'

type Item = { id: string; text: string; contact?: string; rating?: number; at: number }

const STORAGE_KEY = 'help.feedback'

const loadItems = async (): Promise<Item[]> => {
  try {
    const raw = await (require('@react-native-async-storage/async-storage') as any).default.getItem(STORAGE_KEY)
    if (raw) return JSON.parse(raw)
  } catch {}
  return []
}

const saveItems = async (arr: Item[]) => {
  try { await (require('@react-native-async-storage/async-storage') as any).default.setItem(STORAGE_KEY, JSON.stringify(arr)) } catch {}
}

const FeedbackScreen: React.FC = () => {
  const { theme } = useTheme()
  const insets = useSafeAreaInsets()
  const navigation = useNavigation<any>()
  const [items, setItems] = React.useState<Item[]>([])

  React.useEffect(() => { (async () => setItems(await loadItems()))() }, [])

  const onSubmit = async ({ text, rating, contact }: { text: string; rating?: number; contact?: string }) => {
    const it: Item = { id: `f-${Date.now()}`, text, rating, contact, at: Date.now() }
    const next = [it, ...items]
    setItems(next)
    await saveItems(next)
    try { track('help.feedback.submit') } catch {}
  }

  return (
    <SafeAreaView style={{ flex: 1, backgroundColor: theme.color.background }}>
      <View style={{ paddingHorizontal: 24, paddingTop: insets.top + 12, paddingBottom: 12 }}>
        <View style={{ flexDirection: 'row', alignItems: 'center', justifyContent: 'space-between' }}>
          <TouchableOpacity onPress={() => navigation.goBack()} accessibilityLabel="Back" accessibilityRole="button" style={{ paddingHorizontal: 12, paddingVertical: 8, borderRadius: 10, borderWidth: 1, borderColor: theme.color.border }}>
            <Text style={{ color: theme.color.primary, fontWeight: '600' }}>{'< Back'}</Text>
          </TouchableOpacity>
          <Text style={{ color: theme.color.foreground, fontSize: 20, fontWeight: '700' }}>Send Feedback</Text>
          <View style={{ width: 64 }} />
        </View>
      </View>

      <View style={{ paddingHorizontal: 24, gap: 16 }}>
        <FeedbackForm onSubmit={onSubmit} />
        <View style={{ height: 1, backgroundColor: theme.color.border }} />
        <Text style={{ color: theme.color.cardForeground, fontWeight: '700' }}>Inbox</Text>
        <FlatList
          data={items}
          keyExtractor={(i) => i.id}
          renderItem={({ item }) => (
            <View style={{ paddingVertical: 8 }}>
              <Text style={{ color: theme.color.cardForeground, fontWeight: '600' }}>{item.rating ? `Rating: ${item.rating}` : 'Feedback'}</Text>
              <Text style={{ color: theme.color.mutedForeground }}>{item.text}</Text>
              {item.contact ? <Text style={{ color: theme.color.mutedForeground, fontSize: 12 }}>Contact: {item.contact}</Text> : null}
            </View>
          )}
          ItemSeparatorComponent={() => <View style={{ height: 1, backgroundColor: theme.color.border }} />}
        />
      </View>
    </SafeAreaView>
  )
}

export default FeedbackScreen


