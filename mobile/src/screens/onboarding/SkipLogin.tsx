import React, { useEffect, useRef, useState } from 'react'
import { SafeAreaView, View, Text, TextInput, KeyboardAvoidingView, Platform, TouchableOpacity, ScrollView, Image, Animated, Easing } from 'react-native'
import Svg, { Path } from 'react-native-svg'
import MaskedView from '@react-native-masked-view/masked-view'
import { LinearGradient } from 'expo-linear-gradient'
import { useTheme } from '../../providers/ThemeProvider'
import { Button } from '../../components/ui/Button'

interface Props {
  onBack?: () => void
  onLogin?: () => void
  onRegister?: () => void
}

export const SkipLoginScreen: React.FC<Props> = ({ onBack, onLogin, onRegister }) => {
  const { theme } = useTheme()
  const fullSub = 'Your clients, served better.'
  const [typed, setTyped] = useState('')
  const intervalRef = useRef<any>(null)
  const timeoutRef = useRef<any>(null)
  const [fieldWidth, setFieldWidth] = useState<number | null>(null)
  // Orbs
  const pulse1 = useRef(new Animated.Value(0)).current
  const pulse2 = useRef(new Animated.Value(0)).current
  const drift1 = useRef(new Animated.Value(0)).current
  const drift2 = useRef(new Animated.Value(0)).current

  useEffect(() => {
    const start = () => {
      let i = 0
      intervalRef.current && clearInterval(intervalRef.current)
      timeoutRef.current && clearTimeout(timeoutRef.current)
      intervalRef.current = setInterval(() => {
        i += 1
        setTyped(fullSub.slice(0, i))
        if (i >= fullSub.length) {
          clearInterval(intervalRef.current)
          timeoutRef.current = setTimeout(start, 1400) // pause before looping
        }
      }, 80) // smooth & slow
    }
    start()
    return () => { clearInterval(intervalRef.current); clearTimeout(timeoutRef.current) }
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

  return (
    <SafeAreaView style={{ flex: 1, backgroundColor: theme.color.background }}>
      <KeyboardAvoidingView behavior={Platform.OS === 'ios' ? 'padding' : 'height'} style={{ flex: 1 }}>
        <View style={{ flex: 1 }}>
          {/* Background Orbs */}
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
          {/* Header (consistent with other onboarding pages) */}
          <View style={{ alignItems: 'center', marginTop: 40, marginBottom: 16, paddingHorizontal: 24 }}>
            <LinearGradient
              colors={[theme.color.primary, theme.color.primaryLight, 'hsl(260,100%,80%)']}
              start={{ x: 0, y: 0 }}
              end={{ x: 1, y: 1 }}
              style={{ width: 64, height: 64, borderRadius: 20, alignItems: 'center', justifyContent: 'center', marginBottom: 12 }}
            >
              <Text style={{ color: '#fff', fontSize: 28, fontWeight: '700' }}>A</Text>
            </LinearGradient>
            <View style={{ alignItems: 'center' }}>
              <MaskedView
                maskElement={
                  <Text style={{ fontSize: 38, fontWeight: '700', textAlign: 'center', lineHeight: 42 }}>Pocket</Text>
                }
              >
                <LinearGradient colors={[theme.color.primary, theme.color.primaryLight, 'hsl(260,100%,80%)']} start={{ x: 0, y: 0 }} end={{ x: 1, y: 1 }}>
                  <Text style={{ opacity: 0, fontSize: 38, fontWeight: '700', textAlign: 'center', lineHeight: 42 }}>Pocket</Text>
                </LinearGradient>
              </MaskedView>
              <Text style={{ color: theme.color.foreground, fontSize: 24, fontWeight: '700', textAlign: 'center', lineHeight: 30, marginTop: 2 }}>AI Assistants</Text>
            </View>
            <Text style={{ color: theme.color.mutedForeground, fontSize: 16, textAlign: 'center', marginTop: 8 }}>
              {typed}
            </Text>
          </View>

          {/* Form area */}
          <ScrollView keyboardShouldPersistTaps="handled" contentContainerStyle={{ padding: 24 }}>
            <View style={{ gap: 10 }}>
              <View
                onLayout={(e) => setFieldWidth(e.nativeEvent.layout.width)}
                style={{ backgroundColor: theme.color.accent, borderRadius: 12, paddingHorizontal: 10, paddingVertical: 6, borderWidth: 0, borderColor: 'transparent' }}
              >
                <Text style={{ color: theme.color.cardForeground, fontSize: 12, fontWeight: '600', marginBottom: 6 }}>Email</Text>
                <TextInput
                  placeholder="you@company.com"
                  keyboardType="email-address"
                  placeholderTextColor={theme.color.placeholder}
                  underlineColorAndroid="transparent"
                  style={{ color: theme.color.cardForeground, paddingVertical: 3, fontSize: 14 }}
                />
              </View>
              <View style={{ backgroundColor: theme.color.accent, borderRadius: 12, paddingHorizontal: 10, paddingVertical: 6, borderWidth: 0, borderColor: 'transparent' }}>
                <Text style={{ color: theme.color.cardForeground, fontSize: 12, fontWeight: '600', marginBottom: 6 }}>Password</Text>
                <TextInput
                  placeholder="••••••••"
                  secureTextEntry
                  autoComplete="off"
                  textContentType="none"
                  importantForAutofill="no"
                  placeholderTextColor={theme.color.placeholder}
                  underlineColorAndroid="transparent"
                  style={{ color: theme.color.cardForeground, paddingVertical: 3, fontSize: 14 }}
                />
              </View>
            </View>

            <View style={{ marginTop: 16, gap: 12 }}>
              <View style={{ width: fieldWidth ?? '100%', alignSelf: 'center' }}>
                <Button title="Login" size="lg" variant="hero" onPress={onLogin} />
              </View>
              <View style={{ flexDirection: 'row', alignItems: 'center', justifyContent: 'center', marginVertical: 2 }}>
                <View style={{ width: 64, height: 1, backgroundColor: theme.color.border, marginRight: 10 }} />
                <Text style={{ color: theme.color.mutedForeground, fontWeight: '600' }}>OR</Text>
                <View style={{ width: 64, height: 1, backgroundColor: theme.color.border, marginLeft: 10 }} />
              </View>
              {Platform.OS === 'ios' && (
                <TouchableOpacity
                  activeOpacity={0.8}
                  style={{
                    height: 48,
                    borderRadius: 12,
                    backgroundColor: theme.color.accent,
                    alignItems: 'center',
                    justifyContent: 'center',
                    width: fieldWidth ?? '100%',
                    alignSelf: 'center'
                  }}
                >
                  <View style={{ flexDirection: 'row', alignItems: 'center', gap: 8 }}>
                    <View style={{ width: 24, height: 24, borderRadius: 12, backgroundColor: 'transparent', alignItems: 'center', justifyContent: 'center', overflow: 'hidden' }}>
                      <Svg width={16} height={16} viewBox="0 0 24 24">
                        <Path fill={theme.dark ? '#fff' : '#000'} d="M16.365 1.43c-.977.058-2.128.66-2.8 1.436-.607.703-1.14 1.859-.94 2.938 1.083.083 2.2-.552 2.888-1.337.605-.692 1.083-1.822.852-3.037zM19.5 12.64c-.053-3.086 2.52-4.559 2.631-4.623-1.429-2.084-3.649-2.37-4.429-2.4-1.889-.192-3.684 1.135-4.642 1.135-.976 0-2.436-1.11-4.001-1.08-2.058.03-3.973 1.196-5.032 3.04-2.148 3.723-.548 9.214 1.547 12.236 1.017 1.463 2.223 3.107 3.8 3.052 1.53-.06 2.107-.988 3.963-.988 1.841 0 2.383.988 3.999.958 1.653-.027 2.698-1.492 3.702-2.962 1.165-1.706 1.644-3.36 1.671-3.443-.037-.017-3.211-1.233-3.238-4.825z"/>
                      </Svg>
                    </View>
                    <Text style={{ color: theme.color.cardForeground, fontWeight: '600' }}>Continue with Apple</Text>
                  </View>
                </TouchableOpacity>
              )}
              <TouchableOpacity activeOpacity={0.8} style={{
                height: 48,
                borderRadius: 12,
                backgroundColor: theme.color.accent,
                alignItems: 'center',
                justifyContent: 'center',
                width: fieldWidth ?? '100%',
                alignSelf: 'center'
              }}>
                <View style={{ flexDirection: 'row', alignItems: 'center', gap: 8 }}>
                  <View style={{ width: 24, height: 24, borderRadius: 12, backgroundColor: 'transparent', alignItems: 'center', justifyContent: 'center', borderWidth: 0, borderColor: 'transparent', overflow: 'hidden' }}>
                    <Svg width={16} height={16} viewBox="0 0 24 24">
                      <Path fill="#4285F4" d="M23.49 12.27c0-.86-.07-1.49-.22-2.14H12v3.88h6.52c-.13 1.03-.83 2.58-2.39 3.62l-.02.14 3.47 2.69.24.02c2.2-2.03 3.47-5.01 3.47-8.21z"/>
                      <Path fill="#34A853" d="M12 24c3.15 0 5.8-1.04 7.73-2.83l-3.68-2.85c-.99.68-2.31 1.16-4.05 1.16-3.09 0-5.71-2.03-6.64-4.83l-.14.01-3.6 2.79-.05.13C2.49 21.53 6.92 24 12 24z"/>
                      <Path fill="#FBBC05" d="M5.36 14.65c-.22-.65-.35-1.35-.35-2.06 0-.71.13-1.41.34-2.06l-.01-.14-3.64-2.83-.12.06C.78 9.5 0 10.99 0 12.59c0 1.59.78 3.09 2.14 4.17l3.22-2.11z"/>
                      <Path fill="#EA4335" d="M12 4.73c2.19 0 3.67.95 4.51 1.75l3.29-3.22C17.78 1.2 15.15 0 12 0 6.92 0 2.49 2.47.97 6.03l3.63 2.86C5.53 6.09 8.14 4.73 12 4.73z"/>
                    </Svg>
                  </View>
                  <Text style={{ color: theme.color.cardForeground, fontWeight: '600' }}>Continue with Google</Text>
                </View>
              </TouchableOpacity>
              {/* Register link to avoid CTA confusion */}
              <View style={{ alignItems: 'center', marginTop: 8 }}>
                <Text style={{ color: theme.color.cardForeground }}>
                  Don't have an account?
                  <Text> </Text>
                  <Text onPress={onRegister} style={{ color: theme.color.primary, fontWeight: '700' }}>Sign up</Text>
                </Text>
              </View>
            </View>

            {onBack ? (
              <TouchableOpacity onPress={onBack} style={{ alignSelf: 'center', marginTop: 16 }}>
                <Text style={{ color: theme.color.mutedForeground, fontWeight: '600' }}>Back</Text>
              </TouchableOpacity>
            ) : null}
          </ScrollView>
        </View>
      </KeyboardAvoidingView>
    </SafeAreaView>
  )
}
