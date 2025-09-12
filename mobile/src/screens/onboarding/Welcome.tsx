import React, { useEffect, useRef } from 'react'
import { View, Text, SafeAreaView, Animated, Easing } from 'react-native'
import { LinearGradient } from 'expo-linear-gradient'
import MaskedView from '@react-native-masked-view/masked-view'
import { useTranslation } from 'react-i18next'
import { useTheme } from '../../providers/ThemeProvider'
import { Button } from '../../components/ui/Button'

interface WelcomeScreenProps {
  onNext: () => void
  onSkip: () => void
}

export const WelcomeScreen: React.FC<WelcomeScreenProps> = ({ onNext, onSkip }) => {
  const { t } = useTranslation()
  const { theme } = useTheme()
  const title = t('onboarding.welcome.title')
  const [titlePrefix, titleHighlight] = title.split('\n')
  const AnimatedGradient: any = Animated.createAnimatedComponent(LinearGradient)

  const pulse1 = useRef(new Animated.Value(0)).current
  const pulse2 = useRef(new Animated.Value(0)).current
  const drift1 = useRef(new Animated.Value(0)).current
  const drift2 = useRef(new Animated.Value(0)).current

  useEffect(() => {
    const duration = 3600
    const makePulse = (val: Animated.Value) =>
      Animated.sequence([
        Animated.timing(val, { toValue: 1, duration: duration / 2, easing: Easing.inOut(Easing.ease), useNativeDriver: true }),
        Animated.timing(val, { toValue: 0, duration: duration / 2, easing: Easing.inOut(Easing.ease), useNativeDriver: true })
      ])

    const anim1 = Animated.loop(makePulse(pulse1))
    const anim2 = Animated.loop(Animated.sequence([Animated.delay(duration / 2), makePulse(pulse2)]))

    const driftAnim1 = Animated.loop(
      Animated.sequence([
        Animated.timing(drift1, { toValue: 1, duration: 12000, easing: Easing.inOut(Easing.quad), useNativeDriver: true }),
        Animated.timing(drift1, { toValue: 0, duration: 12000, easing: Easing.inOut(Easing.quad), useNativeDriver: true })
      ])
    )
    const driftAnim2 = Animated.loop(
      Animated.sequence([
        Animated.delay(900),
        Animated.timing(drift2, { toValue: 1, duration: 14000, easing: Easing.inOut(Easing.quad), useNativeDriver: true }),
        Animated.timing(drift2, { toValue: 0, duration: 14000, easing: Easing.inOut(Easing.quad), useNativeDriver: true })
      ])
    )

    anim1.start()
    anim2.start()
    driftAnim1.start()
    driftAnim2.start()
    return () => {
      anim1.stop()
      anim2.stop()
      driftAnim1.stop()
      driftAnim2.stop()
      drift1.setValue(0)
      drift2.setValue(0)
    }
  }, [])

  const scale1 = pulse1.interpolate({ inputRange: [0, 1], outputRange: [1, 1.08] })
  const opacity1 = pulse1.interpolate({ inputRange: [0, 1], outputRange: [0.10, 0.22] })
  const scale2 = pulse2.interpolate({ inputRange: [0, 1], outputRange: [1, 1.08] })
  const opacity2 = pulse2.interpolate({ inputRange: [0, 1], outputRange: [0.08, 0.18] })
  const translateX1 = drift1.interpolate({ inputRange: [0, 0.25, 0.5, 0.75, 1], outputRange: [0, 14, 6, -12, 0] })
  const translateY1 = drift1.interpolate({ inputRange: [0, 0.25, 0.5, 0.75, 1], outputRange: [0, -10, 6, -4, 0] })
  const translateX2 = drift2.interpolate({ inputRange: [0, 0.25, 0.5, 0.75, 1], outputRange: [0, -12, -4, 10, 0] })
  const translateY2 = drift2.interpolate({ inputRange: [0, 0.25, 0.5, 0.75, 1], outputRange: [0, 8, -6, 4, 0] })

  return (
    <SafeAreaView style={{ flex: 1, backgroundColor: theme.color.background }}>
      <LinearGradient
        colors={[theme.color.background, theme.color.background, theme.color.background]}
        start={{ x: 0, y: 0 }}
        end={{ x: 1, y: 1 }}
        style={{ flex: 1 }}
      >
        {/* Background decorative elements with gradient heartbeat */}
        <View style={{ position: 'absolute', top: 0, left: 0, right: 0, bottom: 0 }}>
          <Animated.View
            style={{
              position: 'absolute',
              top: 80,
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
            }}
          >
            <LinearGradient
              colors={[theme.color.primary, theme.color.primaryLight, 'hsl(260,100%,80%)']}
              start={{ x: 0, y: 0 }}
              end={{ x: 1, y: 1 }}
              style={{ flex: 1, borderRadius: 100 }}
            />
          </Animated.View>

          <Animated.View
            style={{
              position: 'absolute',
              bottom: 120,
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
            }}
          >
            <LinearGradient
              colors={[theme.color.primary, theme.color.primaryLight, 'hsl(260,100%,80%)']}
              start={{ x: 0, y: 0 }}
              end={{ x: 1, y: 1 }}
              style={{ flex: 1, borderRadius: 75 }}
            />
          </Animated.View>
        </View>

        <View style={{ 
          flex: 1, 
          padding: 24, 
          justifyContent: 'center',
          alignItems: 'center'
        }}>
          {/* Brand Logo - match web gradient tile */}
          <LinearGradient
            colors={[theme.color.primary, theme.color.primaryLight, 'hsl(260,100%,80%)']}
            start={{ x: 0, y: 0 }}
            end={{ x: 1, y: 1 }}
            style={{
              width: 80,
              height: 80,
              borderRadius: 24,
              alignItems: 'center',
              justifyContent: 'center',
              marginBottom: 32,
            }}
          >
            <Text style={{ color: '#fff', fontSize: 36, fontWeight: '700' }}>A</Text>
          </LinearGradient>

          {/* Title */}
          <View style={{ marginBottom: 16, alignItems: 'center' }}>
            {titlePrefix ? (
              <Text style={{
                color: theme.color.foreground,
                fontSize: 32,
                fontWeight: '700',
                textAlign: 'center',
                lineHeight: 38
              }}>
                {titlePrefix}
              </Text>
            ) : null}
            {titleHighlight ? (
              <MaskedView
                maskElement={
                  <Text style={{
                    fontSize: 32,
                    fontWeight: '700',
                    textAlign: 'center',
                    lineHeight: 38
                  }}>
                    {titleHighlight}
                  </Text>
                }
              >
                <LinearGradient
                  colors={[theme.color.primary, theme.color.primaryLight, 'hsl(260,100%,80%)']}
                  start={{ x: 0, y: 0 }}
                  end={{ x: 1, y: 1 }}
                >
                  {/* Invisible text to size the gradient to text bounds */}
                  <Text style={{
                    opacity: 0,
                    fontSize: 32,
                    fontWeight: '700',
                    textAlign: 'center',
                    lineHeight: 38
                  }}>
                    {titleHighlight}
                  </Text>
                </LinearGradient>
              </MaskedView>
            ) : null}
          </View>

          {/* Subtitle */}
          <Text style={{
            color: theme.color.mutedForeground,
            fontSize: 18,
            textAlign: 'center',
            marginBottom: 48,
            lineHeight: 24,
            paddingHorizontal: 20
          }}>
            {t('onboarding.welcome.subtitle')}
          </Text>

          {/* Action Buttons */}
          <View style={{ width: '100%', gap: 16 }}>
            <Button title="Start onboarding" variant="hero" size="lg" onPress={onNext} />
            <View style={{ alignSelf: 'center', paddingVertical: 6 }}>
              <Text onPress={onSkip} style={{ color: theme.color.mutedForeground, fontSize: 16, fontWeight: '600' }}>Skip to login</Text>
            </View>
          </View>
        </View>
      </LinearGradient>
    </SafeAreaView>
  )
}
