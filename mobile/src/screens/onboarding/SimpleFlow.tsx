import React, { useEffect, useRef } from 'react'
import { View, Text, Animated, Easing } from 'react-native'
import { useTheme } from '@/providers/ThemeProvider'
import { useTranslation } from 'react-i18next'
import { Button } from '@/components/ui/Button'
import { hapticLight } from '@/utils/haptics'

const StepCard: React.FC<{ title: string; bullets: string[] }> = ({ title, bullets }) => {
  const { theme } = useTheme()
  return (
    <View style={{ flex: 1, backgroundColor: theme.color.card, borderColor: theme.color.border, borderWidth: 1, borderRadius: theme.radius.xl, padding: theme.spacing.lg }}>
      <Text style={{ color: theme.color.cardForeground, fontSize: 18, fontWeight: '600', textAlign: 'center' }}>{title}</Text>
      <View style={{ marginTop: 8, gap: 6 }}>
        {bullets.map((b, i) => (
          <Text key={i} style={{ color: theme.color.mutedForeground, textAlign: 'center' }}>• {b}</Text>
        ))}
      </View>
    </View>
  )
}

export const SimpleFlow: React.FC<{ onNext?: () => void }> = ({ onNext }) => {
  const { t } = useTranslation()
  const steps = t<any>('howItWorks.steps', { returnObjects: true }) as { title: string; bullets: string[] }[]
  const { theme } = useTheme()

  const cycle = 4800
  const w1 = useRef(new Animated.Value(0)).current
  const w2 = useRef(new Animated.Value(0)).current
  useEffect(() => {
    const anim1 = Animated.loop(
      Animated.timing(w1, { toValue: 1, duration: cycle / 2, easing: Easing.linear, useNativeDriver: false })
    )
    const anim2 = Animated.loop(
      Animated.sequence([
        Animated.delay(cycle / 2),
        Animated.timing(w2, { toValue: 1, duration: cycle / 2, easing: Easing.linear, useNativeDriver: false })
      ])
    )
    anim1.start()
    anim2.start()
    return () => { anim1.stop(); anim2.stop(); w1.setValue(0); w2.setValue(0) }
  }, [w1, w2])

  const s1 = { width: w1.interpolate({ inputRange: [0, 1], outputRange: ['0%', '100%'] }) }
  const s2 = { width: w2.interpolate({ inputRange: [0, 1], outputRange: ['0%', '100%'] }) }

  return (
    <View style={{ flex: 1, padding: 16, justifyContent: 'center' }}>
      <View style={{ flexDirection: 'row', alignItems: 'center', gap: 16 }}>
        <StepCard title={steps[0]?.title} bullets={steps[0]?.bullets || []} />
        <View style={{ flex: 1, height: 8, borderRadius: 9999, backgroundColor: 'rgba(255,255,255,0.08)', overflow: 'hidden' }}>
          <Animated.View style={[{ height: '100%', backgroundColor: theme.color.primary }, s1]} />
        </View>
        <StepCard title={steps[1]?.title} bullets={steps[1]?.bullets || []} />
        <View style={{ flex: 1, height: 8, borderRadius: 9999, backgroundColor: 'rgba(255,255,255,0.08)', overflow: 'hidden' }}>
          <Animated.View style={[{ height: '100%', backgroundColor: theme.color.primary }, s2]} />
        </View>
        <StepCard title={steps[2]?.title} bullets={steps[2]?.bullets || []} />
      </View>
      {onNext ? (
        <View style={{ marginTop: 24, alignItems: 'center' }}>
          <Button title={t('common.continue') || 'Continue'} variant="outline" onPress={() => { hapticLight(); onNext() }} />
        </View>
      ) : null}
    </View>
  )
}
