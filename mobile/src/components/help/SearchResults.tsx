import React, { useEffect } from 'react'
import { View, Text, TouchableOpacity, FlatList } from 'react-native'
import { useTheme } from '../../providers/ThemeProvider'
import { Article } from '../../types/help'
import AsyncStorage from '@react-native-async-storage/async-storage'

const STORAGE_KEY = 'help.cached.articles'

export const SearchResults: React.FC<{ results: Article[]; onSelect: (a: Article) => void }> = ({ results, onSelect }) => {
  const { theme } = useTheme()
  useEffect(() => {
    (async () => {
      try {
        const existing = await AsyncStorage.getItem(STORAGE_KEY)
        const arr: Article[] = existing ? JSON.parse(existing) : []
        const next = [...results, ...arr].slice(0, 10)
        await AsyncStorage.setItem(STORAGE_KEY, JSON.stringify(next))
      } catch {}
    })()
  }, [results])
  if (!results.length) return <Text style={{ color: theme.color.mutedForeground, padding: 12 }}>No results</Text>
  return (
    <FlatList
      data={results}
      keyExtractor={a => a.id}
      renderItem={({ item }) => (
        <TouchableOpacity onPress={() => onSelect(item)} accessibilityLabel={item.title} style={{ padding: 12, minHeight: 44 }}>
          <Text style={{ color: theme.color.cardForeground, fontWeight: '600' }}>{item.title}</Text>
          <Text style={{ color: theme.color.mutedForeground, fontSize: 12 }}>{item.tags.join(' • ')}</Text>
        </TouchableOpacity>
      )}
      ItemSeparatorComponent={() => <View style={{ height: 1, backgroundColor: theme.color.border }} />}
    />
  )
}


