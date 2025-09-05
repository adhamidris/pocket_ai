import React, { useEffect, useMemo, useRef, useState } from 'react'
import { View, Text, ScrollView, TextInput, Pressable, Animated, Easing } from 'react-native'
import { useTheme } from '@/providers/ThemeProvider'
import { useTranslation } from 'react-i18next'
import { navigate } from '@/navigation/navRef'
import { hapticLight, hapticSuccess } from '@/utils/haptics'
import { sharePortalLink } from '@/utils/share'

type RouteParams = { agentId?: string }

type Msg = { id: number; text: string; isBot: boolean }

export const ChatScreen: React.FC<{ route: { params?: RouteParams } }> = ({ route }) => {
  const { theme } = useTheme()
  const { t } = useTranslation()
  const scenarios = t<any>('demo.scenarios', { returnObjects: true }) as any[]
  const agentId = route?.params?.agentId?.toLowerCase()
  const scenario = useMemo(() => {
    if (!Array.isArray(scenarios) || scenarios.length === 0) return { agentName: 'Agent', jobTitle: 'Assistant', conversation: [] }
    if (agentId) {
      const found = scenarios.find(s => String(s.agentName).toLowerCase().includes(agentId))
      if (found) return found
    }
    return scenarios[0]
  }, [agentId, scenarios])

  const ref = useRef<ScrollView | null>(null)
  const [messages, setMessages] = useState<Msg[]>(() => scenario.conversation.map((m: any) => ({ id: m.id, text: m.text, isBot: m.isBot })))
  const [input, setInput] = useState('')
  const [isBotTyping, setIsBotTyping] = useState(false)

  useEffect(() => {
    setTimeout(() => ref.current?.scrollToEnd({ animated: true }), 50)
  }, [messages.length])

  // Handle initial reply from notification
  useEffect(() => {
    const initialReply = (route?.params as any)?.initialReply as string | undefined
    if (initialReply && initialReply.trim()) {
      setMessages(prev => [...prev, { id: prev.length + 1, text: initialReply.trim(), isBot: false }])
      setIsBotTyping(true)
      setTimeout(() => {
        setMessages(prev => [...prev, { id: prev.length + 1, text: 'Thanks — let me take a look.', isBot: true }])
        setIsBotTyping(false)
      }, 900)
    }
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [])

  const onSend = () => {
    const text = input.trim()
    if (!text) return
    hapticLight()
    setInput('')
    setMessages(prev => [...prev, { id: prev.length + 1, text, isBot: false }])
    setIsBotTyping(true)
    setTimeout(() => {
      setMessages(prev => [...prev, { id: prev.length + 1, text: 'Got it. I’ll help with that.', isBot: true }])
      setIsBotTyping(false)
      hapticSuccess()
    }, 900)
  }

  const TypingDots: React.FC = () => {
    const d1 = useRef(new Animated.Value(0)).current
    const d2 = useRef(new Animated.Value(0)).current
    const d3 = useRef(new Animated.Value(0)).current
    useEffect(() => {
      const make = (v: Animated.Value, delay: number) => Animated.loop(Animated.sequence([
        Animated.timing(v, { toValue: 1, duration: 400, easing: Easing.inOut(Easing.ease), useNativeDriver: true, delay }),
        Animated.timing(v, { toValue: 0, duration: 400, easing: Easing.inOut(Easing.ease), useNativeDriver: true }),
      ]))
      const a1 = make(d1, 0); const a2 = make(d2, 150); const a3 = make(d3, 300)
      a1.start(); a2.start(); a3.start()
      return () => { a1.stop(); a2.stop(); a3.stop() }
    }, [d1, d2, d3])
    const Dot = ({ v }: { v: Animated.Value }) => (
      <Animated.View style={{ width: 6, height: 6, borderRadius: 3, backgroundColor: theme.color.mutedForeground, opacity: v }} />
    )
    return (
      <View style={{ flexDirection: 'row', gap: 4 }}>
        <Dot v={d1} />
        <Dot v={d2} />
        <Dot v={d3} />
      </View>
    )
  }

  return (
    <View style={{ flex: 1, backgroundColor: theme.color.background }}>
      {/* Header with back and share */}
      <View style={{ paddingTop: 50, paddingBottom: 12, paddingHorizontal: 12, borderBottomWidth: 1, borderColor: theme.color.border, backgroundColor: theme.color.card, flexDirection: 'row', alignItems: 'center', justifyContent: 'space-between' }}>
        <Pressable accessibilityRole="button" accessibilityLabel="Back" onPress={() => navigate('Home')} style={{ padding: 8, borderRadius: 8 }}>
          <Text style={{ color: theme.color.cardForeground, fontSize: 16 }}>Back</Text>
        </Pressable>
        <View style={{ alignItems: 'center' }}>
          <Text style={{ color: theme.color.cardForeground, fontSize: 18, fontWeight: '700' }}>{scenario.agentName}</Text>
          <Text style={{ color: theme.color.mutedForeground, fontSize: 12 }}>{scenario.jobTitle}</Text>
        </View>
        <Pressable accessibilityRole="button" accessibilityLabel="Share chat link" onPress={() => sharePortalLink(agentId || scenario.agentName)} style={{ padding: 8, borderRadius: 8 }}>
          <Text style={{ color: theme.color.cardForeground, fontSize: 16 }}>Share</Text>
        </Pressable>
      </View>

      {/* Messages */}
      <ScrollView ref={ref} contentContainerStyle={{ padding: 16, gap: 12 }}>
        {messages.map((m) => (
          <View key={m.id} style={{ alignItems: m.isBot ? 'flex-start' : 'flex-end' }}>
            <View style={{ maxWidth: '85%', padding: 10, borderRadius: 16, backgroundColor: m.isBot ? theme.color.card : theme.color.primary, borderWidth: m.isBot ? 1 : 0, borderColor: theme.color.border }}>
              <Text style={{ color: m.isBot ? theme.color.cardForeground : '#fff' }}>{m.text}</Text>
            </View>
          </View>
        ))}
        {isBotTyping && (
          <View style={{ alignItems: 'flex-start' }}>
            <View style={{ maxWidth: '85%', padding: 10, borderRadius: 16, backgroundColor: theme.color.card, borderWidth: 1, borderColor: theme.color.border }}>
              <TypingDots />
            </View>
          </View>
        )}
      </ScrollView>

      {/* Composer */}
      <View style={{ borderTopWidth: 1, borderColor: theme.color.border, padding: 10, flexDirection: 'row', alignItems: 'flex-end', gap: 8, backgroundColor: theme.color.card }}>
        <View style={{ flex: 1, borderRadius: 12, borderWidth: 1, borderColor: theme.color.border, backgroundColor: theme.color.background, paddingHorizontal: 12, paddingVertical: 8, maxHeight: 120 }}>
          <TextInput
            value={input}
            onChangeText={setInput}
            placeholder="Type a message…"
            placeholderTextColor={theme.color.mutedForeground}
            style={{ color: theme.color.foreground, maxHeight: 100 }}
            onSubmitEditing={onSend}
            returnKeyType="send"
            multiline
            accessibilityLabel="Message input"
          />
        </View>
        <Pressable accessibilityRole="button" accessibilityLabel="Send message" onPress={onSend} style={{ padding: 10, borderRadius: 10, backgroundColor: input.trim() ? theme.color.primary : theme.color.border }} disabled={!input.trim()}>
          <Text style={{ color: '#fff', fontWeight: '600' }}>Send</Text>
        </Pressable>
      </View>
    </View>
  )
}
