import React, { useEffect, useMemo, useRef, useState } from 'react'
import { View, Text, SafeAreaView, Animated, Easing, TouchableOpacity } from 'react-native'
import { ChevronLeft } from 'lucide-react-native'
import { LinearGradient } from 'expo-linear-gradient'
import MaskedView from '@react-native-masked-view/masked-view'
import { useTranslation } from 'react-i18next'
import { useTheme } from '../../providers/ThemeProvider'
import { Button } from '../../components/ui/Button'
import { Card } from '../../components/ui/Card'
import { Input } from '../../components/ui/Input'
import { Check } from 'lucide-react-native'

interface HowItWorksScreenProps {
  onNext: () => void
  onBack: () => void
  onSkipToRegister?: () => void
}

const StepCard: React.FC<{ title: string; bullets: string[] }> = ({ title, bullets }) => {
  const { theme } = useTheme()
  
  return (
    <Card style={{ flex: 1, minHeight: 140 }}>
      <Text style={{
        color: theme.color.cardForeground,
        fontSize: 16,
        fontWeight: '600',
        textAlign: 'center',
        marginBottom: 12
      }}>
        {title}
      </Text>
      <View style={{ gap: 6 }}>
        {bullets.map((bullet, index) => (
          <Text key={index} style={{
            color: theme.color.mutedForeground,
            fontSize: 12,
            textAlign: 'center',
            lineHeight: 16
          }}>
            • {bullet}
          </Text>
        ))}
      </View>
    </Card>
  )
}

const AnimatedConnector: React.FC<{ progress: Animated.Value }> = ({ progress }) => {
  const { theme } = useTheme()
  
  const width = progress.interpolate({
    inputRange: [0, 1],
    outputRange: ['0%', '100%']
  })

  return (
    <View style={{
      flex: 1,
      height: 8,
      backgroundColor: 'rgba(255,255,255,0.1)',
      borderRadius: 4,
      overflow: 'hidden',
      marginHorizontal: 8
    }}>
      <Animated.View style={{
        height: '100%',
        backgroundColor: theme.color.primary,
        width
      }} />
    </View>
  )
}

export const HowItWorksScreen: React.FC<HowItWorksScreenProps> = ({ onNext, onBack, onSkipToRegister }) => {
  const { t } = useTranslation()
  const { theme } = useTheme()

  const titles = t('onboarding.howItWorks.carousel.titles', { returnObjects: true }) as string[]

  const [stepIndex, setStepIndex] = useState(0)
  const fade = useRef(new Animated.Value(1)).current
  const slide = useRef(new Animated.Value(0)).current
  const pulse1 = useRef(new Animated.Value(0)).current
  const pulse2 = useRef(new Animated.Value(0)).current
  const drift1 = useRef(new Animated.Value(0)).current
  const drift2 = useRef(new Animated.Value(0)).current

  // Step transition helper
  const goToNextStep = () => {
    const next = (stepIndex + 1) % 3
    Animated.sequence([
      Animated.timing(fade, { toValue: 0, duration: 240, useNativeDriver: true }),
      Animated.timing(slide, { toValue: 1, duration: 240, easing: Easing.out(Easing.cubic), useNativeDriver: true }),
    ]).start(() => {
      slide.setValue(0)
      setStepIndex(next)
      Animated.timing(fade, { toValue: 1, duration: 240, useNativeDriver: true }).start()
    })
  }

  const currentTitle = titles?.[stepIndex] || ''
  const demoMinHeight = stepIndex === 2 ? 240 : 360

  // Background orbs animations
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
  const DemoRegister: React.FC<{ onComplete: () => void }> = ({ onComplete }) => {
    const [name, setName] = useState('')
    const [email, setEmail] = useState('')
    const [submitting, setSubmitting] = useState(false)

    useEffect(() => {
      let t1: any, t2: any, t3: any
      // Simulate typing
      const type = (text: string, setter: (s: string) => void, delay = 80) => {
        let i = 0
        return setInterval(() => {
          i++
          setter(text.slice(0, i))
          if (i >= text.length) clearInterval(timer)
        }, delay) as any
        var timer: any
      }
      t1 = type('John Doe', setName)
      t2 = setTimeout(() => {
        t3 = type('john@example.com', setEmail)
      }, 900)
      const submitTimer = setTimeout(() => {
        setSubmitting(true)
        setTimeout(() => { setSubmitting(false); onComplete() }, 900)
      }, 2500)
      return () => { clearInterval(t1); clearTimeout(t2); clearInterval(t3); clearTimeout(submitTimer) }
    }, [])

    return (
      <View>
        <Text style={{ color: theme.color.mutedForeground, fontSize: 12, marginBottom: 8 }}>Create your account</Text>
        <Input label="Full name" value={name} editable={false} />
        <Input label="Email" value={email} editable={false} keyboardType="email-address" />
        <View style={{ flexDirection: 'row', gap: 8 }}>
          <View style={{ flex: 1 }}>
            <Input label="Password" value={'••••••••'} secureTextEntry editable={false} />
          </View>
          <View style={{ flex: 1 }}>
            <Input label="Confirm" value={'••••••••'} secureTextEntry editable={false} />
          </View>
        </View>
        <View style={{ marginTop: 8 }}>
          <Button title={'Next'} size="md" variant="hero" loading={submitting} />
        </View>
      </View>
    )
  }

  const DemoProfile: React.FC<{ onComplete: () => void }> = ({ onComplete }) => {
    const [company, setCompany] = useState('')
    const [industry, setIndustry] = useState('')
    const [address, setAddress] = useState('')
    const [nextLoading, setNextLoading] = useState(false)

    useEffect(() => {
      let t1: any, t2: any, t3: any, done: any
      const type = (text: string, setter: (s: string) => void, delay = 70) => {
        let i = 0
        const timer = setInterval(() => {
          i++; setter(text.slice(0, i)); if (i >= text.length) clearInterval(timer)
        }, delay)
        return timer
      }
      t1 = type('Acme Corp', setCompany)
      t2 = setTimeout(() => { t3 = type('E-Commerce', setIndustry) }, 700)
      done = setTimeout(() => {
        setAddress('123 Market St, SF')
        setNextLoading(true)
        setTimeout(() => { setNextLoading(false); onComplete() }, 900)
      }, 1800)
      return () => { clearInterval(t1); clearTimeout(t2); clearInterval(t3); clearTimeout(done) }
    }, [])

    return (
      <View>
        <Text style={{ color: theme.color.mutedForeground, fontSize: 12, marginBottom: 8 }}>Business profile</Text>
        <Input label={'Company name'} value={company} editable={false} />
        <View style={{ flexDirection: 'row', gap: 8 }}>
          <View style={{ flex: 1 }}>
            <Input label={'Industry'} value={industry} editable={false} />
          </View>
          
        </View>
        <Input label={'Address'} value={address} editable={false} />
        <View style={{ marginTop: 8 }}>
          <Button title={'Next'} size="md" variant="hero" loading={nextLoading} />
        </View>
      </View>
    )
  }

  const DemoUpload: React.FC<{ onComplete: () => void }> = ({ onComplete }) => {
    const p1 = useRef(new Animated.Value(0)).current
    const p2 = useRef(new Animated.Value(0)).current
    const p3 = useRef(new Animated.Value(0)).current
    const [uploadLoading, setUploadLoading] = useState(false)

    useEffect(() => {
      const run = async () => {
        Animated.timing(p1, { toValue: 1, duration: 900, useNativeDriver: false }).start(() => {
          Animated.timing(p2, { toValue: 1, duration: 900, useNativeDriver: false }).start(() => {
            Animated.timing(p3, { toValue: 1, duration: 900, useNativeDriver: false }).start(() => {
              setUploadLoading(true)
              setTimeout(() => { setUploadLoading(false); onComplete() }, 600)
            })
          })
        })
      }
      run()
    }, [])

    const Bar: React.FC<{ label: string; v: Animated.Value }> = ({ label, v }) => (
      <View style={{ marginBottom: 12 }}>
        <Text style={{ color: theme.color.mutedForeground, fontSize: 12, marginBottom: 6 }}>{label}</Text>
        <View style={{ height: 10, borderRadius: 6, backgroundColor: theme.color.muted, overflow: 'hidden' }}>
          <Animated.View style={{ height: '100%', borderRadius: 6, backgroundColor: theme.color.primary, width: v.interpolate({ inputRange: [0, 1], outputRange: ['0%', '100%'] }) as any }} />
        </View>
      </View>
    )

    return (
      <View>
        <Text style={{ color: theme.color.mutedForeground, fontSize: 12, marginBottom: 8 }}>Upload knowledge base</Text>
        <Bar label={'knowledgebase.pdf'} v={p1} />
        <Bar label={'sop.docx'} v={p2} />
        <Bar label={'faq.csv'} v={p3} />
        <View style={{ flexDirection: 'row', alignItems: 'center', gap: 8, marginTop: 6, marginBottom: 12 }}>
          <Check size={16} color={theme.color.success} />
          <Text style={{ color: theme.color.mutedForeground, fontSize: 12 }}>Processing & indexing</Text>
        </View>
        <View style={{ marginTop: 12 }}>
          <Button title={'Upload'} size="md" variant="hero" loading={uploadLoading} />
        </View>
      </View>
    )
  }

  return (
    <SafeAreaView style={{ flex: 1, backgroundColor: theme.color.background }}>
      <View style={{ flex: 1, padding: 24, backgroundColor: theme.color.background }}>
        {/* Back arrow */}
        <TouchableOpacity onPress={onBack} activeOpacity={0.8} style={{ position: 'absolute', top: 12, left: 16, padding: 8, zIndex: 10 }}>
          <ChevronLeft color={theme.color.primary as any} size={22} />
        </TouchableOpacity>
        {/* Header */}
        <View style={{ alignItems: 'center', marginBottom: 24, marginTop: 40 }}>
          <MaskedView
            maskElement={
              <Text style={{
                fontSize: 32,
                fontWeight: '700',
                textAlign: 'center',
                marginBottom: 8
              }}>
                {t('onboarding.howItWorks.title')}
              </Text>
            }
          >
            <LinearGradient
              colors={[theme.color.primary, theme.color.primaryLight, 'hsl(260,100%,80%)']}
              start={{ x: 0, y: 0 }}
              end={{ x: 1, y: 1 }}
            >
              <Text style={{
                opacity: 0,
                fontSize: 32,
                fontWeight: '700',
                textAlign: 'center',
                marginBottom: 8
              }}>
                {t('onboarding.howItWorks.title')}
              </Text>
            </LinearGradient>
          </MaskedView>
          {/* Animated sub-title (visually nudged without affecting layout) */}
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

        {/* Demo Simulator */}
        <View style={{ flex: 1, justifyContent: 'center', paddingTop: 8 }}>
          <Card variant="premium" style={{ padding: 16 }}>
            <Animated.View style={{ backgroundColor: theme.color.secondary, borderRadius: 12, padding: 16, minHeight: demoMinHeight, justifyContent: 'flex-start', opacity: fade, transform: [{ translateY: slide.interpolate({ inputRange: [0, 1], outputRange: [0, 8] }) }] }}>
              {stepIndex === 0 && <DemoRegister onComplete={goToNextStep} />}
              {stepIndex === 1 && <DemoProfile onComplete={goToNextStep} />}
              {stepIndex === 2 && <DemoUpload onComplete={goToNextStep} />}
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
            onPress={onNext}
            size="lg"
            variant="hero"
          />
          <TouchableOpacity onPress={onSkipToRegister || onBack} activeOpacity={0.7} style={{ alignSelf: 'center', paddingVertical: 6 }}>
            <Text style={{ color: theme.color.mutedForeground, fontSize: 16, fontWeight: '600' }}>Skip to register</Text>
          </TouchableOpacity>
        </View>
      </View>
    </SafeAreaView>
  )
}
