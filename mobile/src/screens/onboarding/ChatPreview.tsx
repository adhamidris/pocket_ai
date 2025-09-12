import React, { useMemo, useRef, useEffect, useState } from 'react'
import { View, Text, SafeAreaView, ScrollView, Animated, Easing, TextInput, TouchableOpacity, Image } from 'react-native'
import { ChevronLeft } from 'lucide-react-native'
import { useTranslation } from 'react-i18next'
import { useTheme } from '../../providers/ThemeProvider'
import { Button } from '../../components/ui/Button'
import { Card } from '../../components/ui/Card'
import MaskedView from '@react-native-masked-view/masked-view'
import { LinearGradient } from 'expo-linear-gradient'
import Svg, { Rect as SvgRect, Circle as SvgCircle, Path as SvgPath, G as SvgG } from 'react-native-svg'
import { Bot, User, Send } from 'lucide-react-native'

interface ChatPreviewScreenProps {
  onComplete: () => void
  onBack: () => void
  onRegister?: () => void
}

export const ChatPreviewScreen: React.FC<ChatPreviewScreenProps> = ({ onComplete, onBack, onRegister }) => {
  const { t } = useTranslation()
  const { theme } = useTheme()
  const subtitleFade = useRef(new Animated.Value(0)).current
  const subtitleSlide = useRef(new Animated.Value(0)).current
  const scrollRef = useRef<ScrollView | null>(null)
  type DemoMessage = { text: string; isBot: boolean; images?: string[] }
  const [messages, setMessages] = useState<DemoMessage[]>([])
  const [currentIndex, setCurrentIndex] = useState(0)
  const [phase, setPhase] = useState<'idle' | 'pre-bot' | 'streaming'>('idle')
  const dot = useRef(new Animated.Value(0)).current
  const [inputValue, setInputValue] = useState('')
  const onlinePulse = useRef(new Animated.Value(0)).current
  const [brokenImages, setBrokenImages] = useState<Record<string, boolean>>({})
  // Background orbs (match HowItWorks / ShareAnywhere)
  const pulse1 = useRef(new Animated.Value(0)).current
  const pulse2 = useRef(new Animated.Value(0)).current
  const drift1 = useRef(new Animated.Value(0)).current
  const drift2 = useRef(new Animated.Value(0)).current
  
  const scenarios = t('demo.scenarios', { returnObjects: true }) as Array<{
    agentName: string
    jobTitle: string
    conversation: Array<{ text: string; isBot: boolean; images?: string[] }>
  }>
  
  const [scenarioIndex, setScenarioIndex] = useState(() => Math.floor(Math.random() * Math.max(1, (scenarios?.length || 1))))
  const scenario = useMemo(() => (scenarios?.[scenarioIndex] ?? scenarios?.[0]), [scenarios, scenarioIndex])
  // Force first run to a scenario that shares images (to mirror web demo)
  useEffect(() => {
    const idx = (scenarios || []).findIndex(s => (s.conversation || []).some(m => (m as any).images && (m as any).images.length > 0))
    if (idx >= 0 && idx !== scenarioIndex) setScenarioIndex(idx)
  }, [])

  // Minimal Nancy avatar (scaled) matching ShareAnywhere style
  const MiniNancy: React.FC = () => (
    <Svg width={28} height={28} viewBox="0 0 200 200">
      {/* Head */}
      <SvgRect x="30" y="35" width="140" height="130" rx="36" fill="#f2c7b9" />
      {/* Hair cap */}
      <SvgRect x="30" y="35" width="140" height="44" rx="26" fill="#2a2a2a" opacity="0.95" />
      {/* Eyes */}
      <SvgRect x="52" y="72" width="36" height="26" rx="8" fill="#ffffff" />
      <SvgRect x="112" y="72" width="36" height="26" rx="8" fill="#ffffff" />
      <SvgCircle cx="70" cy="85" r="7" fill="#6db1ff" />
      <SvgCircle cx="70" cy="85" r="3" fill="#0a0a0a" />
      <SvgCircle cx="130" cy="85" r="7" fill="#6db1ff" />
      <SvgCircle cx="130" cy="85" r="3" fill="#0a0a0a" />
      {/* Nose */}
      <SvgPath d="M97 112 Q100 116 103 112" stroke="#b97c70" strokeWidth="2" fill="none" strokeLinecap="round" />
      {/* Mouth */}
      <SvgRect x="88" y="134" width="24" height="6" rx="3" fill="#0a0a0a" />
    </Svg>
  )

  const TinyNancy: React.FC = () => (
    <Svg width={20} height={20} viewBox="0 0 200 200">
      <SvgRect x="30" y="35" width="140" height="130" rx="36" fill="#f2c7b9" />
      <SvgRect x="30" y="35" width="140" height="44" rx="26" fill="#2a2a2a" opacity="0.95" />
      <SvgRect x="52" y="72" width="36" height="26" rx="8" fill="#ffffff" />
      <SvgRect x="112" y="72" width="36" height="26" rx="8" fill="#ffffff" />
      <SvgCircle cx="70" cy="85" r="7" fill="#6db1ff" />
      <SvgCircle cx="70" cy="85" r="3" fill="#0a0a0a" />
      <SvgCircle cx="130" cy="85" r="7" fill="#6db1ff" />
      <SvgCircle cx="130" cy="85" r="3" fill="#0a0a0a" />
      <SvgRect x="88" y="134" width="24" height="6" rx="3" fill="#0a0a0a" />
    </Svg>
  )

  useEffect(() => {
    Animated.parallel([
      Animated.timing(subtitleFade, { toValue: 1, duration: 240, useNativeDriver: true }),
      Animated.timing(subtitleSlide, { toValue: 1, duration: 180, easing: Easing.out(Easing.cubic), useNativeDriver: true })
    ]).start()
  }, [])

  // Typing dots animation
  useEffect(() => {
    const loop = Animated.loop(
      Animated.sequence([
        Animated.timing(dot, { toValue: 1, duration: 500, useNativeDriver: true }),
        Animated.timing(dot, { toValue: 0, duration: 500, useNativeDriver: true })
      ])
    )
    loop.start()
    return () => loop.stop()
  }, [])

  // Online pulse dot
  useEffect(() => {
    const loop = Animated.loop(
      Animated.sequence([
        Animated.timing(onlinePulse, { toValue: 1, duration: 900, easing: Easing.inOut(Easing.quad), useNativeDriver: false }),
        Animated.timing(onlinePulse, { toValue: 0, duration: 900, easing: Easing.inOut(Easing.quad), useNativeDriver: false })
      ])
    )
    loop.start()
    return () => loop.stop()
  }, [])

  // Orbs animations
  useEffect(() => {
    const makePulse = (val: Animated.Value, duration: number) =>
      Animated.loop(
        Animated.sequence([
          Animated.timing(val, { toValue: 1, duration: duration / 2, easing: Easing.inOut(Easing.ease), useNativeDriver: true }),
          Animated.timing(val, { toValue: 0, duration: duration / 2, easing: Easing.inOut(Easing.ease), useNativeDriver: true })
        ])
      )
    const p1 = makePulse(pulse1, 3600)
    const p2 = makePulse(pulse2, 4200)
    const d1 = Animated.loop(
      Animated.sequence([
        Animated.timing(drift1, { toValue: 1, duration: 12000, easing: Easing.inOut(Easing.quad), useNativeDriver: true }),
        Animated.timing(drift1, { toValue: 0, duration: 12000, easing: Easing.inOut(Easing.quad), useNativeDriver: true })
      ])
    )
    const d2 = Animated.loop(
      Animated.sequence([
        Animated.delay(900),
        Animated.timing(drift2, { toValue: 1, duration: 14000, easing: Easing.inOut(Easing.quad), useNativeDriver: true }),
        Animated.timing(drift2, { toValue: 0, duration: 14000, easing: Easing.inOut(Easing.quad), useNativeDriver: true })
      ])
    )
    p1.start(); p2.start(); d1.start(); d2.start()
    return () => { p1.stop(); p2.stop(); d1.stop(); d2.stop() }
  }, [])

  const scale1 = pulse1.interpolate({ inputRange: [0, 1], outputRange: [1, 1.08] })
  const opacity1 = pulse1.interpolate({ inputRange: [0, 1], outputRange: [0.06, 0.14] })
  const scale2 = pulse2.interpolate({ inputRange: [0, 1], outputRange: [1, 1.08] })
  const opacity2 = pulse2.interpolate({ inputRange: [0, 1], outputRange: [0.05, 0.12] })
  const translateX1 = drift1.interpolate({ inputRange: [0, 0.25, 0.5, 0.75, 1], outputRange: [0, 28, 12, -24, 0] })
  const translateY1 = drift1.interpolate({ inputRange: [0, 0.25, 0.5, 0.75, 1], outputRange: [0, -20, 10, -8, 0] })
  const translateX2 = drift2.interpolate({ inputRange: [0, 0.25, 0.5, 0.75, 1], outputRange: [0, -26, 10, 22, 0] })
  const translateY2 = drift2.interpolate({ inputRange: [0, 0.25, 0.5, 0.75, 1], outputRange: [0, 18, 0, -16, 0] })

  // Scroll to bottom on new content/phase
  useEffect(() => {
    if (scrollRef.current) {
      requestAnimationFrame(() => scrollRef.current?.scrollToEnd({ animated: true }))
    }
  }, [messages, phase])

  // Conversation playback with streaming for bot messages
  useEffect(() => {
    if (!scenario || !scenario.conversation) return
    if (currentIndex >= scenario.conversation.length) {
      const resetTimer = setTimeout(() => {
        setMessages([])
        setCurrentIndex(0)
        setPhase('idle')
        // pick different scenario if available
        setScenarioIndex((prev) => {
          const len = scenarios?.length || 1
          if (len <= 1) return prev
          let next = Math.floor(Math.random() * len)
          if (next === prev) next = (prev + 1) % len
          return next
        })
      }, 2600)
      return () => clearTimeout(resetTimer)
    }

    const msg = scenario.conversation[currentIndex]
    const baseDelay = msg.isBot ? 600 : 400
    const typingLead = msg.isBot ? 500 : 0
    const delay = baseDelay + (msg.text?.length || 0) * 6

    const timer = setTimeout(() => {
      if (msg.isBot) {
        // If this bot message contains images, render immediately without streaming
        if (msg.images && msg.images.length > 0) {
          setPhase('idle')
          setMessages(prev => [...prev, { isBot: true, text: msg.text || '', images: msg.images }])
          setCurrentIndex(i => i + 1)
          return
        }
        setPhase('pre-bot')
        // small lead for typing dots
        const pre = setTimeout(() => {
          setPhase('streaming')
          const fullText = msg.text || ''
          const perCharMs = 50
          const total = Math.min(2000, Math.max(700, Math.ceil(fullText.length * perCharMs)))
          const stepMs = Math.max(16, Math.floor(total / Math.max(1, fullText.length)))
          setMessages(prev => [...prev, { isBot: true, text: '' }])
          let pos = 0
          const stream = setInterval(() => {
            pos += 1
            const nextText = fullText.slice(0, pos)
            setMessages(prev => {
              if (prev.length === 0) return prev
              const copy = prev.slice()
              copy[copy.length - 1] = { isBot: true, text: nextText }
              return copy
            })
            if (pos >= fullText.length) {
              clearInterval(stream)
              setPhase('idle')
              setCurrentIndex(i => i + 1)
            }
          }, stepMs)
        }, typingLead)
        return () => clearTimeout(pre)
      }
      // user message
      setPhase('idle')
      setMessages(prev => [...prev, { isBot: false, text: msg.text || '' }])
      setCurrentIndex(i => i + 1)
    }, delay)

    return () => clearTimeout(timer)
  }, [currentIndex, scenario, scenarios])

  return (
    <SafeAreaView style={{ flex: 1, backgroundColor: theme.color.background }}>
      <View style={{ flex: 1, padding: 24 }}>
        {/* Back arrow */}
        <TouchableOpacity onPress={onBack} activeOpacity={0.8} style={{ position: 'absolute', top: 12, left: 16, padding: 8, zIndex: 10 }}>
          <ChevronLeft color={theme.color.primary as any} size={22} />
        </TouchableOpacity>
        {/* Background Orbs */}
        <View style={{ position: 'absolute', top: 0, left: 0, right: 0, bottom: 0 }} pointerEvents="none">
          <Animated.View style={{
            position: 'absolute',
            top: 8,
            left: 40,
            width: 200,
            height: 200,
            borderRadius: 100,
            borderWidth: 0,
            borderColor: 'transparent',
            opacity: opacity1 as unknown as number,
            transform: [
              { translateX: translateX1 as unknown as number },
              { translateY: translateY1 as unknown as number },
              { scale: scale1 as unknown as number }
            ]
          }}>
            <LinearGradient
              colors={[theme.color.primary, theme.color.primaryLight, 'hsl(260,100%,80%)']}
              start={{ x: 0, y: 0 }}
              end={{ x: 1, y: 1 }}
              style={{ flex: 1, borderRadius: 100 }}
            />
          </Animated.View>
          <Animated.View style={{
            position: 'absolute',
            bottom: 32,
            right: 40,
            width: 150,
            height: 150,
            borderRadius: 75,
            borderWidth: 0,
            borderColor: 'transparent',
            opacity: opacity2 as unknown as number,
            transform: [
              { translateX: translateX2 as unknown as number },
              { translateY: translateY2 as unknown as number },
              { scale: scale2 as unknown as number }
            ]
          }}>
            <LinearGradient
              colors={[theme.color.primary, theme.color.primaryLight, 'hsl(260,100%,80%)']}
              start={{ x: 0, y: 0 }}
              end={{ x: 1, y: 1 }}
              style={{ flex: 1, borderRadius: 75 }}
            />
          </Animated.View>
        </View>
        {/* Header */}
        <View style={{ alignItems: 'center', marginBottom: 24, marginTop: 40 }}>
          <MaskedView
            maskElement={
              <Text style={{ fontSize: 32, fontWeight: '700', textAlign: 'center', marginBottom: 8 }}>
                {t('onboarding.chatPreview.title')}
              </Text>
            }
          >
            <LinearGradient
              colors={[theme.color.primary, theme.color.primaryLight, 'hsl(260,100%,80%)']}
              start={{ x: 0, y: 0 }}
              end={{ x: 1, y: 1 }}
            >
              <Text style={{ opacity: 0, fontSize: 32, fontWeight: '700', textAlign: 'center', marginBottom: 8 }}>
            {t('onboarding.chatPreview.title')}
          </Text>
            </LinearGradient>
          </MaskedView>
          <View style={{ transform: [{ translateY: 10 }] }}>
            <Animated.Text style={{
              color: theme.color.foreground,
              fontSize: 18,
              textAlign: 'center',
              opacity: subtitleFade,
              transform: [{ translateY: subtitleSlide.interpolate({ inputRange: [0, 1], outputRange: [0, 6] }) }]
          }}>
            {t('onboarding.chatPreview.subtitle')}
            </Animated.Text>
          </View>
        </View>

        {/* Chat Preview */}
        <View style={{ flex: 1, justifyContent: 'center' }}>
          <Card variant="default" style={{ padding: 14, shadowOpacity: 0, shadowRadius: 0, elevation: 0, shadowOffset: { width: 0, height: 0 } }}>
            {/* Chat Header (distinct background like web) */}
            <View style={{ flexDirection: 'row', alignItems: 'center', backgroundColor: theme.color.secondary, borderWidth: 0, borderRadius: 8, paddingHorizontal: 10, paddingVertical: 8, marginBottom: 14, marginHorizontal: -14 }}>
              <View style={{ width: 40, height: 40, alignItems: 'center', justifyContent: 'center', marginRight: 12 }}>
                <MiniNancy />
              </View>
              <View style={{ flex: 1 }}>
                <Text style={{ color: theme.color.cardForeground, fontSize: 16, fontWeight: '600', marginBottom: 2 }}>
                  {scenario?.agentName || 'AI Agent'}
                </Text>
                <Text style={{ color: theme.color.mutedForeground, fontSize: 12, marginTop: 2 }}>
                  {scenario?.jobTitle || 'Support Agent'}
                </Text>
                <View style={{ flexDirection: 'row', alignItems: 'center', gap: 6, marginTop: 6 }}>
                  <Animated.View style={{ width: 8, height: 8, borderRadius: 4, backgroundColor: theme.color.success, opacity: onlinePulse.interpolate({ inputRange: [0, 1], outputRange: [0.5, 1] }) as any }} />
                  <Text style={{ color: theme.color.mutedForeground, fontSize: 11 }}>{t('demo.online') as string}</Text>
                </View>
              </View>
            </View>

            {/* Messages */}
            <View style={{ height: 280, overflow: 'hidden' }}>
              <ScrollView ref={scrollRef} style={{ flex: 1 }} contentContainerStyle={{ paddingBottom: 8 }}>
              {messages.map((message, index) => (
                  <View key={index} style={{ alignSelf: message.isBot ? 'flex-start' : 'flex-end', marginBottom: 12, width: '100%' }}>
                    <View style={{ flexDirection: message.isBot ? 'row' : 'row-reverse', alignItems: 'flex-end', maxWidth: '100%' }}>
                      {/* Avatar bubble */}
                      <View style={{
                        width: 24,
                        height: 24,
                        borderRadius: 12,
                        alignItems: 'center',
                        justifyContent: 'center',
                        backgroundColor: message.isBot ? 'transparent' : '#6b7280',
                        marginTop: 4,
                        marginRight: message.isBot ? 8 : 0,
                        marginLeft: message.isBot ? 0 : 8
                      }}>
                        {message.isBot ? (
                          <TinyNancy />
                        ) : (
                          <User size={16} color={'#ffffff' as any} />
                        )}
                      </View>
                      {/* Message bubble */}
                      {message.isBot ? (
                  <View style={{
                          flexShrink: 1,
                          maxWidth: '78%',
                          paddingVertical: 8,
                          paddingHorizontal: 12,
                          backgroundColor: theme.color.card,
                          borderWidth: 0,
                          borderTopLeftRadius: 12,
                          borderTopRightRadius: 16,
                          borderBottomRightRadius: 16,
                          borderBottomLeftRadius: 6
                        }}>
                          <Text style={{ color: theme.color.cardForeground, fontSize: 13, lineHeight: 17 }}>
                            {message.text}
                          </Text>
                          {message.images && message.images.length > 0 && (
                            <View style={{ marginTop: 8, flexDirection: 'row', flexWrap: 'wrap', gap: 6 }}>
                              {message.images.slice(0, 2).map((src, idx) => {
                                const imgKey = `${currentIndex}-${idx}`
                                if (brokenImages[imgKey]) return null
                                return (
                                  <Image
                                    key={idx}
                                    source={{ uri: src }}
                                    resizeMode="cover"
                                    style={{ width: 80, height: 56, borderRadius: 8, backgroundColor: theme.color.accent }}
                                    onError={() => setBrokenImages(prev => ({ ...prev, [imgKey]: true }))}
                                  />
                                )
                              })}
                            </View>
                          )}
                        </View>
                      ) : (
                        <LinearGradient colors={[theme.color.primary, theme.color.primaryLight]} start={{ x: 0, y: 0 }} end={{ x: 1, y: 1 }} style={{
                          flexShrink: 1,
                          maxWidth: '78%',
                          paddingVertical: 8,
                          paddingHorizontal: 12,
                          borderTopLeftRadius: 16,
                          borderTopRightRadius: 12,
                          borderBottomRightRadius: 6,
                          borderBottomLeftRadius: 16
                        }}>
                          <Text style={{ color: '#fff', fontSize: 13, lineHeight: 17 }}>
                      {message.text}
                    </Text>
                        </LinearGradient>
                      )}
                    </View>
                  </View>
                ))}
                {/* Typing indicator */}
                {phase === 'pre-bot' && (
                  <View style={{ alignItems: 'flex-start', marginBottom: 12 }}>
                    <View style={{ flexDirection: 'row', alignItems: 'center', maxWidth: '88%' }}>
                      <View style={{
                        width: 24,
                        height: 24,
                        borderRadius: 12,
                        alignItems: 'center',
                        justifyContent: 'center',
                        backgroundColor: 'transparent',
                        marginTop: 4,
                        marginRight: 8
                      }}>
                        <TinyNancy />
                      </View>
                      <View style={{
                        paddingVertical: 10,
                        paddingHorizontal: 12,
                        backgroundColor: theme.color.card,
                        borderWidth: 0,
                        borderTopLeftRadius: 12,
                        borderTopRightRadius: 16,
                        borderBottomRightRadius: 16,
                        borderBottomLeftRadius: 6
                      }}>
                        <View style={{ flexDirection: 'row', alignItems: 'center', gap: 4 }}>
                          {[0,1,2].map((i) => (
                            <Animated.View key={i} style={{
                              width: 6,
                              height: 6,
                              borderRadius: 3,
                              backgroundColor: '#9ca3af',
                              transform: [{ translateY: Animated.multiply(dot, i % 2 === 0 ? -2 : -1) as any }],
                              opacity: dot.interpolate({ inputRange: [0, 1], outputRange: [0.5, 1] }) as any,
                              marginRight: i < 2 ? 4 : 0
                            }} />
                          ))}
                        </View>
                      </View>
                    </View>
                  </View>
                )}
              </ScrollView>
            </View>
            {/* Embedded input inside chat container */}
            <View style={{ paddingTop: 10, borderTopWidth: 1, borderTopColor: theme.color.border, opacity: 0.85 }}>
              <View style={{ flexDirection: 'row', alignItems: 'center', gap: 6 }}>
                <View style={{ flex: 1, backgroundColor: theme.color.accent, borderWidth: 0, borderRadius: 10, paddingHorizontal: 10 }}>
                  <TextInput
                    value={inputValue}
                    onChangeText={setInputValue}
                    editable={false}
                    placeholder={'Type your message…'}
                    placeholderTextColor={theme.color.placeholder}
                    style={{ color: theme.color.cardForeground, paddingVertical: 8, fontSize: 14 }}
                  />
                </View>
                <TouchableOpacity activeOpacity={0.7} style={{ width: 34, height: 32, borderRadius: 8, alignItems: 'center', justifyContent: 'center', backgroundColor: 'hsla(240,75%,48%,0.12)', borderWidth: 0 }}>
                  <Send size={16} color={'#ffffff' as any} />
                </TouchableOpacity>
              </View>
            </View>
          </Card>
        </View>

        {/* Navigation */}
        <View style={{ gap: 16, marginTop: 16, marginBottom: 32 }}>
          <Button
            title={t('common.getStarted')}
            onPress={onRegister || onComplete}
            size="lg"
            variant="premium"
          />
        </View>
      </View>
    </SafeAreaView>
  )
}
