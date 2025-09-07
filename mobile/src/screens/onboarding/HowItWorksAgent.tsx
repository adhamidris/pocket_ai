import React, { useEffect, useMemo, useRef, useState } from 'react'
import { View, Text, SafeAreaView, Animated, Easing, TouchableOpacity, Switch } from 'react-native'
import { ChevronLeft } from 'lucide-react-native'
import { useTranslation } from 'react-i18next'
import { useTheme } from '../../providers/ThemeProvider'
import { Button } from '../../components/ui/Button'
import { Card } from '../../components/ui/Card'
import { Input } from '../../components/ui/Input'
import MaskedView from '@react-native-masked-view/masked-view'
import { LinearGradient } from 'expo-linear-gradient'

interface HowItWorksAgentProps {
  onNext: () => void
  onBack: () => void
}

const Chip: React.FC<{ label: string; selected?: boolean }> = ({ label, selected }) => {
  const { theme } = useTheme()
  return (
    <View style={{
      paddingVertical: 7,
      paddingHorizontal: 14,
      borderRadius: 18,
      backgroundColor: selected ? theme.color.primary : theme.color.accent,
      borderWidth: 1,
      borderColor: selected ? theme.color.primary : theme.color.border,
      marginRight: 8,
      marginBottom: 8
    }}>
      <Text style={{ color: selected ? '#fff' : theme.color.cardForeground, fontSize: 12, fontWeight: '600' }}>{label}</Text>
    </View>
  )
}

export const HowItWorksAgentScreen: React.FC<HowItWorksAgentProps> = ({ onNext, onBack }) => {
  const { t } = useTranslation()
  const { theme } = useTheme()

  const titles = useMemo(() => [
    'Create your agent',
    'Configure your agent',
    'Agent CRM actions'
  ], [])

  const [stepIndex, setStepIndex] = useState(0)
  const fade = useRef(new Animated.Value(1)).current
  const slide = useRef(new Animated.Value(0)).current
  const demoMinHeight = stepIndex === 2 ? 280 : 360
  const [pageNextLoading, setPageNextLoading] = useState(false)
  // Background orbs animation values (match HowItWorks)
  const pulse1 = useRef(new Animated.Value(0)).current
  const pulse2 = useRef(new Animated.Value(0)).current
  const drift1 = useRef(new Animated.Value(0)).current
  const drift2 = useRef(new Animated.Value(0)).current

  const goToNextSubStep = () => {
    const next = (stepIndex + 1) % 3
    Animated.sequence([
      Animated.timing(fade, { toValue: 0, duration: 160, useNativeDriver: true }),
      Animated.timing(slide, { toValue: 1, duration: 180, easing: Easing.out(Easing.cubic), useNativeDriver: true })
    ]).start(() => {
      slide.setValue(0)
      setStepIndex(next)
      Animated.timing(fade, { toValue: 1, duration: 160, useNativeDriver: true }).start()
    })
  }

  // Background orbs animations (match HowItWorks timing/curves)
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

  // --- Mini demo components ---
  const DemoCreateAgent: React.FC<{ onComplete: () => void }> = ({ onComplete }) => {
    const [name, setName] = useState('')
    const [title, setTitle] = useState('')
    const [langs, setLangs] = useState<string[]>([])
    const [nextLoading, setNextLoading] = useState(false)

    useEffect(() => {
      // Simulate input and language selections
      const timers: any[] = []
      const type = (text: string, setter: (s: string) => void, delay = 60) => {
        let i = 0
        const t = setInterval(() => { i++; setter(text.slice(0, i)); if (i >= text.length) clearInterval(t) }, delay)
        timers.push(t)
      }
      type('Nancy', setName)
      timers.push(setTimeout(() => type('E-Commerce Support Agent', setTitle), 800))
      timers.push(setTimeout(() => setLangs(['English']), 1600))
      timers.push(setTimeout(() => setLangs(['English', 'Arabic']), 2400))
      // Show simulated loading on Next, then advance
      timers.push(setTimeout(() => {
        setNextLoading(true)
        timers.push(setTimeout(() => { setNextLoading(false); onComplete() }, 700))
      }, 3500))
      return () => timers.forEach((t) => { clearTimeout(t); clearInterval(t) })
    }, [])

    return (
      <View>
        <Text style={{ color: theme.color.mutedForeground, fontSize: 12, marginBottom: 16 }}>Agent basics</Text>
        <Input label={'Agent name'} value={name} editable={false} />
        <Input label={'Job title'} value={title} editable={false} />
        <Text style={{ color: theme.color.foreground, fontSize: 12, marginBottom: 10 }}>Spoken languages</Text>
        <View style={{ flexDirection: 'row', flexWrap: 'wrap' }}>
          <Chip label={'English'} selected={langs.includes('English')} />
          <Chip label={'Arabic'} selected={langs.includes('Arabic')} />
          <Chip label={'Spanish'} selected={langs.includes('Spanish')} />
          <Chip label={'French'} selected={langs.includes('French')} />
        </View>
        <View style={{ marginTop: 8 }}>
          <Button title={'Next'} size="md" variant="hero" loading={nextLoading} disabled />
        </View>
      </View>
    )
  }

  const DemoConfigureAgent: React.FC<{ onComplete: () => void }> = ({ onComplete }) => {
    const [tone, setTone] = useState('Friendly')
    const [traits, setTraits] = useState<string[]>([])
    const [nextLoading, setNextLoading] = useState(false)

    useEffect(() => {
      const timers: any[] = []
      timers.push(setTimeout(() => setTone('Professional'), 900))
      timers.push(setTimeout(() => setTraits(['Detail oriented']), 1800))
      timers.push(setTimeout(() => setTraits(['Detail oriented', 'Negotiator']), 2700))
      timers.push(setTimeout(() => {
        setNextLoading(true)
        timers.push(setTimeout(() => { setNextLoading(false); onComplete() }, 700))
      }, 3500))
      return () => timers.forEach(clearTimeout)
    }, [])

    const Trait: React.FC<{ label: string; selected?: boolean }> = ({ label, selected }) => (
      <Chip label={label} selected={selected} />
    )

    return (
      <View style={{ minHeight: 320, justifyContent: 'space-between' }}>
        <View>
          <Text style={{ color: theme.color.mutedForeground, fontSize: 12, marginBottom: 16 }}>Personality & style</Text>
          <Text style={{ color: theme.color.foreground, fontSize: 12, marginBottom: 10 }}>Tone</Text>
          <View style={{ flexDirection: 'row', flexWrap: 'wrap', marginBottom: 8 }}>
            {['Friendly', 'Professional', 'Warm', 'Concise'].map((t) => (
              <Chip key={t} label={t} selected={tone === t} />
            ))}
          </View>
          <Text style={{ color: theme.color.foreground, fontSize: 12, marginBottom: 10 }}>Traits</Text>
          <View style={{ flexDirection: 'row', flexWrap: 'wrap' }}>
            {['Detail oriented', 'Negotiator', 'Straight to the point', 'Talkative'].map((tr) => (
              <Trait key={tr} label={tr} selected={traits.includes(tr)} />
            ))}
          </View>
        </View>
        <View style={{ marginTop: 8 }}>
          <Button title={'Next'} size="md" variant="hero" loading={nextLoading} disabled />
        </View>
      </View>
    )
  }

  const DemoCrmActions: React.FC<{ onComplete: () => void }> = ({ onComplete }) => {
    const [actions, setActions] = useState<{ key: string; label: string; value: boolean }[]>([
      { key: 'create', label: 'Create contacts', value: false },
      { key: 'update', label: 'Update tickets', value: false },
      { key: 'notes', label: 'Add notes', value: false },
    ])
    const [fields, setFields] = useState<string[]>([])
    const [nextLoading, setNextLoading] = useState(false)

    useEffect(() => {
      const timers: any[] = []
      timers.push(setTimeout(() => setActions(a => a.map(x => x.key === 'create' ? { ...x, value: true } : x)), 900))
      timers.push(setTimeout(() => setActions(a => a.map(x => x.key === 'notes' ? { ...x, value: true } : x)), 1800))
      timers.push(setTimeout(() => setFields(['Name', 'Email']), 2400))
      timers.push(setTimeout(() => setFields(['Name', 'Email', 'Order ID']), 3000))
      timers.push(setTimeout(() => {
        setNextLoading(true)
        timers.push(setTimeout(() => { setNextLoading(false); onComplete() }, 700))
      }, 3800))
      return () => timers.forEach(clearTimeout)
    }, [])

    return (
      <View>
        <Text style={{ color: theme.color.mutedForeground, fontSize: 12, marginBottom: 8 }}>Allowed CRM actions</Text>
        {actions.map((a) => (
          <View key={a.key} style={{ flexDirection: 'row', alignItems: 'center', justifyContent: 'space-between', paddingVertical: 6 }}>
            <Text style={{ color: theme.color.cardForeground, fontSize: 14 }}>{a.label}</Text>
            <Switch
              value={a.value}
              onValueChange={() => {}}
              trackColor={{ false: theme.color.muted as any, true: (theme.color.primary + '99') as any }}
              thumbColor={a.value ? '#ffffff' : (theme.color.border as any)}
              ios_backgroundColor={theme.color.muted as any}
            />
          </View>
        ))}
        <Text style={{ color: theme.color.foreground, fontSize: 12, marginTop: 12, marginBottom: 10 }}>Collect fields</Text>
        <View style={{ flexDirection: 'row', flexWrap: 'wrap' }}>
          {['Name', 'Email', 'Phone', 'Order ID', 'Company'].map((f) => (
            <Chip key={f} label={f} selected={fields.includes(f)} />
          ))}
        </View>
        <View style={{ marginTop: 8 }}>
          <Button title={'Create agent'} size="md" variant="hero" loading={nextLoading} disabled />
        </View>
      </View>
    )
  }

  const currentTitle = titles[stepIndex]

  return (
    <SafeAreaView style={{ flex: 1, backgroundColor: theme.color.background }}>
      <View style={{ flex: 1, padding: 24 }}>
        {/* Back arrow */}
        <TouchableOpacity onPress={onBack} activeOpacity={0.8} style={{ position: 'absolute', top: 12, left: 16, padding: 8, zIndex: 10 }}>
          <ChevronLeft color={theme.color.primary as any} size={22} />
        </TouchableOpacity>
        {/* Header */}
        <View style={{ alignItems: 'center', marginBottom: 24, marginTop: 40 }}>
          <MaskedView
            maskElement={
              <Text style={{ fontSize: 32, fontWeight: '700', textAlign: 'center', marginBottom: 8 }}>
                Set up your AI Agent
              </Text>
            }
          >
            <LinearGradient colors={[theme.color.primary, theme.color.primaryLight, 'hsl(260,100%,80%)']} start={{ x: 0, y: 0 }} end={{ x: 1, y: 1 }}>
              <Text style={{ opacity: 0, fontSize: 32, fontWeight: '700', textAlign: 'center', marginBottom: 8 }}>
                Set up your AI Agent
              </Text>
            </LinearGradient>
          </MaskedView>
          <View style={{ transform: [{ translateY: 10 }] }}>
            <Animated.Text style={{
              color: theme.color.foreground,
              fontSize: 18,
              textAlign: 'center',
              opacity: fade,
              transform: [{ translateY: slide.interpolate({ inputRange: [0, 1], outputRange: [0, 6] }) }]
            }}>
              {currentTitle}
            </Animated.Text>
          </View>
        </View>

        {/* Background Orbs (match HowItWorks) */}
        <View style={{ position: 'absolute', top: 0, left: 0, right: 0, bottom: 0 }} pointerEvents="none">
          <Animated.View style={{
            position: 'absolute',
            top: 8,
            left: 40,
            width: 200,
            height: 200,
            borderRadius: 100,
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

        {/* Demo Simulator */}
        <View style={{ flex: 1, justifyContent: 'center', paddingTop: 8 }}>
          <Card variant="premium" style={{ padding: 16 }}>
            <Animated.View style={{ backgroundColor: theme.color.secondary, borderRadius: 12, padding: 16, minHeight: demoMinHeight, justifyContent: 'flex-start', opacity: fade, transform: [{ translateY: slide.interpolate({ inputRange: [0, 1], outputRange: [0, 8] }) }] }}>
              {stepIndex === 0 && <DemoCreateAgent onComplete={goToNextSubStep} />}
              {stepIndex === 1 && <DemoConfigureAgent onComplete={goToNextSubStep} />}
              {stepIndex === 2 && <DemoCrmActions onComplete={goToNextSubStep} />}
            </Animated.View>
          </Card>
        </View>

        {/* Indicators */}
        <View style={{ flexDirection: 'row', justifyContent: 'center', gap: 8, marginTop: 12 }}>
          {[0,1,2].map((i) => (
            <View key={i} style={{
              width: i === stepIndex ? 12 : 8,
              height: i === stepIndex ? 12 : 8,
              borderRadius: 6,
              backgroundColor: i === stepIndex ? theme.color.primary : theme.color.border
            }} />
          ))}
        </View>

        {/* Navigation */}
        <View style={{ gap: 16, marginTop: 24 }}>
          <Button
            title={t('common.next')}
            size="lg"
            variant="hero"
            loading={pageNextLoading}
            onPress={() => {
              setPageNextLoading(true)
              setTimeout(() => { setPageNextLoading(false); onNext() }, 550)
            }}
          />
          <TouchableOpacity onPress={onBack} activeOpacity={0.7} style={{ alignSelf: 'center', paddingVertical: 6 }}>
            <Text style={{ color: theme.color.mutedForeground, fontSize: 16, fontWeight: '600' }}>Skip to register</Text>
          </TouchableOpacity>
        </View>
      </View>
    </SafeAreaView>
  )
}
