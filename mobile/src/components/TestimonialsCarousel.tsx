import React, { useEffect, useRef, useState } from 'react'
import { View, Text, ScrollView, Dimensions } from 'react-native'
import { useTheme } from '@/providers/ThemeProvider'

type Item = { quote: string; author: string; role: string }

export const TestimonialsCarousel: React.FC<{ items: Item[] }> = ({ items }) => {
  const { width } = Dimensions.get('window')
  const cardWidth = Math.min(320, width * 0.8)
  const { theme } = useTheme()
  const scRef = useRef<ScrollView | null>(null)
  const [index, setIndex] = useState(0)

  useEffect(() => {
    const id = setInterval(() => {
      const next = (index + 1) % items.length
      setIndex(next)
      scRef.current?.scrollTo({ x: next * (cardWidth + 12), animated: true })
    }, 3500)
    return () => clearInterval(id)
  }, [index, items.length, cardWidth])

  return (
    <View>
      <ScrollView
        ref={scRef}
        horizontal
        showsHorizontalScrollIndicator={false}
        snapToInterval={cardWidth + 12}
        decelerationRate="fast"
        contentContainerStyle={{ paddingHorizontal: 16 }}
      >
        {items.map((item, idx) => (
          <View key={idx} style={{ width: cardWidth, marginRight: 12, backgroundColor: theme.color.card, borderColor: theme.color.border, borderWidth: 1, borderRadius: theme.radius.xl, padding: theme.spacing.lg }}>
            <Text style={{ color: theme.color.mutedForeground, fontStyle: 'italic' }}>“{item.quote}”</Text>
            <View style={{ marginTop: 12 }}>
              <Text style={{ color: theme.color.cardForeground, fontWeight: '600' }}>{item.author}</Text>
              <Text style={{ color: theme.color.mutedForeground, fontSize: 12 }}>{item.role}</Text>
            </View>
          </View>
        ))}
      </ScrollView>
      <View style={{ flexDirection: 'row', justifyContent: 'center', marginTop: 8, gap: 6 }}>
        {items.map((_, i) => (
          <View key={i} style={{ width: 6, height: 6, borderRadius: 3, backgroundColor: i === index ? theme.color.primary : theme.color.border }} />
        ))}
      </View>
    </View>
  )
}

