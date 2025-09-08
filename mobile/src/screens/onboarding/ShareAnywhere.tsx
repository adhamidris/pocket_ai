import React, { useEffect, useMemo, useRef, useState } from 'react'
import { View, Text, SafeAreaView, Animated, Easing, TouchableOpacity } from 'react-native'
import { ChevronLeft } from 'lucide-react-native'
import { Copy } from 'lucide-react-native'
import { useTheme } from '../../providers/ThemeProvider'
import { LinearGradient } from 'expo-linear-gradient'
import MaskedView from '@react-native-masked-view/masked-view'
import { Button } from '../../components/ui/Button'
import Svg, { Circle, Rect, G, Path, Defs, RadialGradient, Stop, Ellipse } from 'react-native-svg'

interface Props {
  onNext: () => void
  onBack: () => void
}

const AnimatedCircle: any = Animated.createAnimatedComponent(Circle)
const AnimatedRect: any = Animated.createAnimatedComponent(Rect)
const AnimatedPath: any = Animated.createAnimatedComponent(Path)

export const ShareAnywhereScreen: React.FC<Props> = ({ onNext, onBack }) => {
  const { theme } = useTheme()
  const fullMessage = "Hello there! I'm your AI-Powered assistant, thank you for creating me. I'm ready to serve your clients anywhere you'll place me.\nJust pin my link anywhere!"
  const [typedMsg, setTypedMsg] = useState('')
  const subtitleFade = useRef(new Animated.Value(0)).current
  const subtitleSlide = useRef(new Animated.Value(0)).current
  // Background orbs animation values (match HowItWorks)
  const pulse1 = useRef(new Animated.Value(0)).current
  const pulse2 = useRef(new Animated.Value(0)).current
  const drift1 = useRef(new Animated.Value(0)).current
  const drift2 = useRef(new Animated.Value(0)).current

  // Robot animation: gentle pupils drift; subtle mouth talk; soft blink
  const pupil = useRef(new Animated.Value(0)).current
  const mouth = useRef(new Animated.Value(0)).current
  const blink = useRef(new Animated.Value(0)).current
  const mouthLoopRef = useRef<any>(null)
  const mouthLoopRunningRef = useRef(false)

  useEffect(() => {
    const eyeLoop = Animated.loop(
      Animated.sequence([
        Animated.timing(pupil, { toValue: 1, duration: 2200, useNativeDriver: true }),
        Animated.timing(pupil, { toValue: -1, duration: 2200, useNativeDriver: true }),
      ])
    )
    mouthLoopRef.current = Animated.loop(
      Animated.sequence([
        Animated.timing(mouth, { toValue: 1, duration: 520, useNativeDriver: true }),
        Animated.timing(mouth, { toValue: 0, duration: 640, useNativeDriver: true }),
      ])
    )
    const blinkLoop = Animated.loop(
      Animated.sequence([
        Animated.delay(2800),
        Animated.timing(blink, { toValue: 1, duration: 120, useNativeDriver: false }),
        Animated.delay(80),
        Animated.timing(blink, { toValue: 0, duration: 140, useNativeDriver: false })
      ])
    )
    eyeLoop.start(); blinkLoop.start()
    return () => { eyeLoop.stop(); mouthLoopRef.current?.stop(); blinkLoop.stop() }
  }, [])

  // Subtitle entrance animation (match other onboarding screens)
  useEffect(() => {
    Animated.parallel([
      Animated.timing(subtitleFade, { toValue: 1, duration: 240, useNativeDriver: true }),
      Animated.timing(subtitleSlide, { toValue: 1, duration: 180, easing: Easing.out(Easing.cubic), useNativeDriver: true })
    ]).start()
  }, [])

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

  // Typewriter streaming for message
  useEffect(() => {
    let index = 0
    const interval = setInterval(() => {
      index += 1
      setTypedMsg(fullMessage.slice(0, index))
      if (index >= fullMessage.length) {
        clearInterval(interval)
      }
    }, 28)
    return () => clearInterval(interval)
  }, [])

  // Start/stop mouth animation in sync with streaming
  useEffect(() => {
    const isTyping = typedMsg.length < fullMessage.length
    if (isTyping) {
      if (!mouthLoopRunningRef.current) {
        mouthLoopRef.current?.start()
        mouthLoopRunningRef.current = true
      }
    } else {
      if (mouthLoopRunningRef.current) {
        mouthLoopRef.current?.stop()
        mouthLoopRunningRef.current = false
        Animated.timing(mouth, { toValue: 0, duration: 180, useNativeDriver: true }).start()
      }
    }
  }, [typedMsg, fullMessage])

  const pupilOffset = pupil.interpolate({ inputRange: [-1, 0, 1], outputRange: [-1.5, 0, 1.5] })
  const mouthHeight = mouth.interpolate({ inputRange: [0, 1], outputRange: [4, 9] })
  const lipDelta = mouth.interpolate({ inputRange: [0, 1], outputRange: [0, 3] })
  const innerMouthH = mouth.interpolate({ inputRange: [0, 1], outputRange: [3, 12] })
  const teethH = mouth.interpolate({ inputRange: [0, 1], outputRange: [4, 6] })
  const tongueH = mouth.interpolate({ inputRange: [0, 1], outputRange: [2, 5] })
  const eyelidHeight = blink.interpolate({ inputRange: [0, 1], outputRange: [0, 28] })

  const link = useMemo(() => 'pocket.com/yourbusiness/jaber-ai', [])

  return (
    <SafeAreaView style={{ flex: 1, backgroundColor: theme.color.background }}>
      <View style={{ flex: 1, padding: 24 }}>
        {/* Back arrow */}
        <TouchableOpacity onPress={onBack} activeOpacity={0.8} style={{ position: 'absolute', top: 12, left: 16, padding: 8, zIndex: 10 }}>
          <ChevronLeft color={theme.color.primary as any} size={22} />
        </TouchableOpacity>
        {/* Title */}
        <View style={{ alignItems: 'center', marginBottom: 24, marginTop: 40 }}>
          <MaskedView
            maskElement={
              <Text style={{ fontSize: 32, fontWeight: '700', textAlign: 'center', marginBottom: 8 }}>
                Share Me Anywhere!
              </Text>
            }
          >
            <LinearGradient colors={[theme.color.primary, theme.color.primaryLight, 'hsl(260,100%,80%)']} start={{ x: 0, y: 0 }} end={{ x: 1, y: 1 }}>
              <Text style={{ opacity: 0, fontSize: 32, fontWeight: '700', textAlign: 'center', marginBottom: 8 }}>
                Share Me Anywhere!
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
              Agent is ready when you are
            </Animated.Text>
          </View>
        </View>

        {/* Robot + message */}
        <View style={{ flex: 1, justifyContent: 'center', alignItems: 'center', gap: 16 }}>
          {/* Background Orbs (match HowItWorks) */}
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
          <View style={{ width: 200, height: 200 }}>
            <Svg width="200" height="200" viewBox="0 0 200 200">
              <Defs>
                <RadialGradient id="cheekGrad" cx="50%" cy="50%" r="50%">
                  <Stop offset="0%" stopColor="#f0a9a0" stopOpacity="0.25" />
                  <Stop offset="100%" stopColor="#f0a9a0" stopOpacity="0" />
                </RadialGradient>
              </Defs>
              {/* Ears behind head (smaller) */}
              <Circle cx="28" cy="100" r="10" fill="#f2c7b9" stroke={theme.color.border} strokeWidth={1} />
              <Circle cx="172" cy="100" r="10" fill="#f2c7b9" stroke={theme.color.border} strokeWidth={1} />
              {/* Head: more rounded human-robotic face */}
              <Rect x="30" y="35" width="140" height="130" rx="60" fill="#f2c7b9" stroke={theme.color.border} strokeWidth={1} />
              {/* Creative hair cap with smoother curvature */}
              <Rect x="30" y="35" width="140" height="44" rx="34" fill="#2a2a2a" opacity={0.95} />
              {/* Hair strands */}
              <Path d="M40 52 C70 44 130 44 160 52" stroke="#4a4a4a" strokeWidth={1.6} strokeLinecap="round" fill="none" opacity={0.5} />
              <Path d="M40 56 C72 48 128 48 160 56" stroke="#5a5a5a" strokeWidth={1.4} strokeLinecap="round" fill="none" opacity={0.45} />
              <Path d="M40 60 C74 52 126 52 160 60" stroke="#6a6a6a" strokeWidth={1.2} strokeLinecap="round" fill="none" opacity={0.35} />
              <Path d="M40 64 C76 56 124 56 160 64" stroke="#7a7a7a" strokeWidth={1.1} strokeLinecap="round" fill="none" opacity={0.3} />
              {/* Center part */}
              <Path d="M100 36 L100 58" stroke="#3a3a3a" strokeWidth={1.2} opacity={0.4} />
              {/* Subtle chin contour (minimized and narrower) */}
              <Path d="M60 150 Q100 160 140 150" stroke={theme.color.border} strokeWidth={0.6} fill="none" opacity={0.15} />
              {/* Soft cheek blush (subtle shaping) */}
              <Ellipse cx="76" cy="116" rx="14" ry="10" fill="url(#cheekGrad)" />
              <Ellipse cx="124" cy="116" rx="14" ry="10" fill="url(#cheekGrad)" />
              {/* Skin contour lines */}
              {/* Forehead soft highlight */}
              <Path d="M68 68 Q100 58 132 68" stroke="#ffffff" strokeWidth={1} opacity={0.08} fill="none" strokeLinecap="round" />
              {/* Cheek contours */}
              <Path d="M58 118 Q72 126 86 120" stroke="#e6b0a0" strokeWidth={1.4} opacity={0.22} fill="none" strokeLinecap="round" />
              <Path d="M142 118 Q128 126 114 120" stroke="#e6b0a0" strokeWidth={1.4} opacity={0.22} fill="none" strokeLinecap="round" />
              {/* Temple micro-lines */}
              <Path d="M46 108 L54 106" stroke="#d9a697" strokeWidth={1.1} opacity={0.18} strokeLinecap="round" />
              <Path d="M154 108 L146 106" stroke="#d9a697" strokeWidth={1.1} opacity={0.18} strokeLinecap="round" />
              {/* Eyes sockets */}
              <G>
                <Rect x="52" y="72" width="36" height="26" rx="8" fill="#ffffff" />
                <Rect x="112" y="72" width="36" height="26" rx="8" fill="#ffffff" />
              </G>
              {/* Eyebrows */}
              <Path d={`M 58 70 Q 70 64 82 70`} stroke={theme.color.cardForeground} strokeWidth={2.5} strokeLinecap="round" fill="none" opacity={0.65} />
              <Path d={`M 118 70 Q 130 64 142 70`} stroke={theme.color.cardForeground} strokeWidth={2.5} strokeLinecap="round" fill="none" opacity={0.65} />
              {/* Friendly iris + small pupil */}
              <AnimatedCircle cx={Animated.add(new Animated.Value(70), pupilOffset)} cy="85" r="7" fill="#6db1ff" />
              <AnimatedCircle cx={Animated.add(new Animated.Value(70), pupilOffset)} cy="85" r="3" fill="#0a0a0a" />
              <AnimatedCircle cx={Animated.add(new Animated.Value(130), pupilOffset)} cy="85" r="7" fill="#6db1ff" />
              <AnimatedCircle cx={Animated.add(new Animated.Value(130), pupilOffset)} cy="85" r="3" fill="#0a0a0a" />
              {/* Catchlights */}
              <Circle cx="66" cy="81" r="1.5" fill="#fff" opacity={0.8} />
              <Circle cx="126" cy="81" r="1.5" fill="#fff" opacity={0.8} />
              {/* Subtle eyelids */}
              <Rect x="52" y="72" width="36" height="10" rx="6" fill={theme.color.secondary} opacity={0.18} />
              <Rect x="112" y="72" width="36" height="10" rx="6" fill={theme.color.secondary} opacity={0.18} />
              {/* Blink overlay (animated from top) - opaque to fully hide pupils */}
              <AnimatedRect x="52" y="72" width="36" height={eyelidHeight as any} rx="8" fill="#f2c7b9" opacity={1} />
              <AnimatedRect x="112" y="72" width="36" height={eyelidHeight as any} rx="8" fill="#f2c7b9" opacity={1} />
              {/* Glasses frame */}
              <G opacity={0.95}>
                {/* Left lens */}
                <Rect x="46" y="66" width="48" height="38" rx="12" fill="#ffffff" opacity={0.06} />
                <Rect x="46" y="66" width="48" height="38" rx="12" fill="none" stroke={theme.color.cardForeground} strokeWidth={2} />
                {/* Right lens */}
                <Rect x="106" y="66" width="48" height="38" rx="12" fill="#ffffff" opacity={0.06} />
                <Rect x="106" y="66" width="48" height="38" rx="12" fill="none" stroke={theme.color.cardForeground} strokeWidth={2} />
                {/* Bridge */}
                <Path d="M94 84 C98 80 104 80 108 84" stroke={theme.color.cardForeground} strokeWidth={2} fill="none" strokeLinecap="round" />
                {/* Arms */}
                <Path d="M46 82 L34 86" stroke={theme.color.cardForeground} strokeWidth={2} strokeLinecap="round" />
                <Path d="M154 82 L166 86" stroke={theme.color.cardForeground} strokeWidth={2} strokeLinecap="round" />
                {/* Lens highlights */}
                <Path d="M50 72 L78 98" stroke="#ffffff" strokeWidth={1.2} opacity={0.15} />
                <Path d="M110 72 L138 98" stroke="#ffffff" strokeWidth={1.2} opacity={0.15} />
              </G>
              {/* Small nose */}
              <Path d="M97 112 Q100 116 103 112" stroke="#b97c70" strokeWidth={1.6} fill="none" strokeLinecap="round" opacity={0.85} />
              <Circle cx="100" cy="118" r="1.2" fill="#b97c70" opacity={0.6} />
              {/* Human mouth: inner mouth (dark), teeth and tongue */}
              {/* Inner mouth */}
              <AnimatedRect x="88" y="131" width="24" height={innerMouthH as any} rx="6" fill="#0a0a0a" opacity={0.9} />
              {/* Teeth (top bar) */}
              <AnimatedRect x="89" y="131" width="22" height={teethH as any} rx="2" fill="#ffffff" opacity={0.95} />
              {/* Teeth separators */}
              <Rect x="96" y="131" width="1.5" height={6} fill="#0a0a0a" opacity={0.45} />
              <Rect x="103" y="131" width="1.5" height={6} fill="#0a0a0a" opacity={0.45} />
              <Rect x="110" y="131" width="1.5" height={6} fill="#0a0a0a" opacity={0.45} />
              {/* Tongue */}
              <AnimatedRect
                x="90"
                y={Animated.add(new Animated.Value(131), Animated.subtract(innerMouthH as any, tongueH as any))}
                width="20"
                height={tongueH as any}
                rx="6"
                fill="#c96b7c"
                opacity={0.9}
              />
              {/* Antenna (smaller) */}
              <Rect x="98.5" y="24" width="3" height="16" rx="1.5" fill={theme.color.primaryLight} />
              <Circle cx="100" cy="20" r="5" fill={theme.color.primaryLight} />
            </Svg>
          </View>

          <Text style={{
            color: theme.color.foreground,
            fontSize: 16,
            textAlign: 'center',
            lineHeight: 22,
            paddingHorizontal: 8
          }}>
            {typedMsg}
          </Text>

          {/* Link mock */}
          <View style={{
            marginTop: 8,
            paddingVertical: 12,
            paddingHorizontal: 16,
            borderRadius: 12,
            backgroundColor: theme.color.accent,
            borderWidth: 1,
            borderColor: theme.color.border,
            flexDirection: 'row',
            alignItems: 'center',
            gap: 10
          }}>
            <Text style={{ color: theme.color.cardForeground, fontWeight: '600' }}>{link}</Text>
            <Copy size={18} color={theme.color.cardForeground} />
          </View>
        </View>

        {/* Navigation */}
        <View style={{ gap: 16, marginTop: 24 }}>
          <Button title={'See me in action'} size="lg" variant="hero" onPress={onNext} />
          <View style={{ alignItems: 'center' }}>
            <Text onPress={onBack} style={{ color: theme.color.mutedForeground, fontSize: 16, fontWeight: '600', paddingVertical: 6 }}>Skip to register</Text>
          </View>
        </View>
      </View>
    </SafeAreaView>
  )
}
