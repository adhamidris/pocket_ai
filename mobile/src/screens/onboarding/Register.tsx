import React, { useRef, useState, useEffect } from 'react'
import { SafeAreaView, View, Text, TextInput, TouchableOpacity, Modal, ScrollView, Pressable, KeyboardAvoidingView, Platform, Dimensions, Animated, Easing } from 'react-native'
import { LinearGradient } from 'expo-linear-gradient'
import { useTheme } from '../../providers/ThemeProvider'
import { Button } from '../../components/ui/Button'

export const RegisterScreen: React.FC<{ onBack: () => void, onLogin?: () => void }> = ({ onBack, onLogin }) => {
  const { theme } = useTheme()
  const [firstName, setFirstName] = useState('')
  const [email, setEmail] = useState('')
  const [phone, setPhone] = useState('')
  const [password, setPassword] = useState('')
  const [confirm, setConfirm] = useState('')
  const [showPicker, setShowPicker] = useState(false)
  const [showRoleTip, setShowRoleTip] = useState(false)
  const [selectedRole, setSelectedRole] = useState<'business' | 'entrepreneur' | 'employee' | null>(null)
  const [showRolePicker, setShowRolePicker] = useState(false)
  const [roleMenuPos, setRoleMenuPos] = useState<{ x: number; y: number; width: number; height: number } | null>(null)
  const roleAnchorRef = useRef<any>(null)
  const roleTipTimeoutRef = useRef<any>(null)

  // Streaming subtitle
  const fullSub = 'Join and get started in minutes'
  const [typed, setTyped] = useState('')
  const subIntervalRef = useRef<any>(null)
  const subTimeoutRef = useRef<any>(null)

  useEffect(() => {
    const start = () => {
      let i = 0
      if (subIntervalRef.current) clearInterval(subIntervalRef.current)
      if (subTimeoutRef.current) clearTimeout(subTimeoutRef.current)
      subIntervalRef.current = setInterval(() => {
        i += 1
        setTyped(fullSub.slice(0, i))
        if (i >= fullSub.length) {
          clearInterval(subIntervalRef.current)
          subTimeoutRef.current = setTimeout(start, 1500)
        }
      }, 85)
    }
    start()
    return () => { clearInterval(subIntervalRef.current); clearTimeout(subTimeoutRef.current) }
  }, [])

  // Orbs
  const pulse1 = useRef(new Animated.Value(0)).current
  const pulse2 = useRef(new Animated.Value(0)).current
  const drift1 = useRef(new Animated.Value(0)).current
  const drift2 = useRef(new Animated.Value(0)).current

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

  const countries = [
    { code: 'EG', dial: '+20', flag: '🇪🇬', label: 'Egypt' },
    { code: 'UAE', dial: '+971', flag: '🇦🇪', label: 'UAE' },
    { code: 'KSA', dial: '+966', flag: '🇸🇦', label: 'KSA' },
    { code: 'US', dial: '+1', flag: '🇺🇸', label: 'United States' },
    { code: 'UK', dial: '+44', flag: '🇬🇧', label: 'United Kingdom' },
    { code: 'FR', dial: '+33', flag: '🇫🇷', label: 'France' },
    { code: 'DE', dial: '+49', flag: '🇩🇪', label: 'Germany' },
    { code: 'ES', dial: '+34', flag: '🇪🇸', label: 'Spain' },
    { code: 'IT', dial: '+39', flag: '🇮🇹', label: 'Italy' },
  ]
  const [selectedCountry, setSelectedCountry] = useState(countries[1])

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

        <ScrollView keyboardShouldPersistTaps="handled" keyboardDismissMode="on-drag" overScrollMode="never" contentContainerStyle={{ padding: 24, flexGrow: 1 }}>
        <View style={{ alignItems: 'center', marginTop: 24, marginBottom: 20 }}>
          <LinearGradient
            colors={[theme.color.primary, theme.color.primaryLight, 'hsl(260,100%,80%)']}
            start={{ x: 0, y: 0 }}
            end={{ x: 1, y: 1 }}
            style={{ width: 64, height: 64, borderRadius: 22, alignItems: 'center', justifyContent: 'center', marginBottom: 14 }}
          >
            <Text style={{ color: '#fff', fontSize: 28, fontWeight: '700' }}>A</Text>
          </LinearGradient>
          <Text style={{ color: theme.color.foreground, fontSize: 24, fontWeight: '700' }}>Create your account</Text>
          <Text style={{ color: theme.color.mutedForeground, marginTop: 6 }}>{typed}</Text>
        </View>

        <View style={{ gap: 12 }}>
          <View style={{ backgroundColor: theme.color.accent, borderRadius: 12, paddingHorizontal: 10, paddingVertical: 6, borderWidth: 0, borderColor: 'transparent' }}>
            <Text style={{ color: theme.color.cardForeground, fontSize: 12, fontWeight: '600', marginBottom: 6 }}>First name</Text>
            <TextInput value={firstName} onChangeText={setFirstName} placeholder="John" placeholderTextColor={theme.color.mutedForeground} underlineColorAndroid="transparent" style={{ color: theme.color.cardForeground, paddingVertical: 3, fontSize: 14 }} />
          </View>
          <View style={{ backgroundColor: theme.color.accent, borderRadius: 12, paddingHorizontal: 10, paddingVertical: 6, borderWidth: 0, borderColor: 'transparent' }}>
            <Text style={{ color: theme.color.cardForeground, fontSize: 12, fontWeight: '600', marginBottom: 6 }}>Email</Text>
            <TextInput value={email} onChangeText={setEmail} placeholder="you@company.com" keyboardType="email-address" placeholderTextColor={theme.color.mutedForeground} underlineColorAndroid="transparent" style={{ color: theme.color.cardForeground, paddingVertical: 3, fontSize: 14 }} />
          </View>
          <View style={{ backgroundColor: theme.color.accent, borderRadius: 12, paddingHorizontal: 10, paddingVertical: 6, borderWidth: 0, borderColor: 'transparent' }}>
            <Text style={{ color: theme.color.cardForeground, fontSize: 12, fontWeight: '600', marginBottom: 6 }}>Phone (optional)</Text>
            <View style={{ flexDirection: 'row', alignItems: 'center', gap: 8 }}>
              <TouchableOpacity
                activeOpacity={0.8}
                onPress={() => setShowPicker(true)}
                style={{
                  flexDirection: 'row',
                  alignItems: 'center',
                  gap: 6,
                  paddingHorizontal: 8,
                  paddingVertical: 4,
                  backgroundColor: theme.color.background,
                  borderRadius: 8,
                }}
              >
                <Text style={{ fontSize: 13 }}>{selectedCountry.flag}</Text>
                <Text style={{ color: theme.color.cardForeground, fontWeight: '600', fontSize: 12 }}>{selectedCountry.dial}</Text>
              </TouchableOpacity>
              <TextInput
                value={phone}
                onChangeText={setPhone}
                placeholder="555 000 111"
                keyboardType="phone-pad"
                placeholderTextColor={theme.color.mutedForeground}
                underlineColorAndroid="transparent"
                style={{ color: theme.color.cardForeground, paddingVertical: 3, fontSize: 14, flex: 1 }}
              />
            </View>
          </View>
          <View style={{ backgroundColor: theme.color.accent, borderRadius: 12, paddingHorizontal: 10, paddingVertical: 6, borderWidth: 0, borderColor: 'transparent' }}>
            <Text style={{ color: theme.color.cardForeground, fontSize: 12, fontWeight: '600', marginBottom: 6 }}>Password</Text>
            <TextInput value={password} onChangeText={setPassword} placeholder="••••••••" secureTextEntry autoComplete="off" textContentType="none" importantForAutofill="no" placeholderTextColor={theme.color.mutedForeground} underlineColorAndroid="transparent" style={{ color: theme.color.cardForeground, paddingVertical: 3, fontSize: 14 }} />
          </View>
          <View style={{ backgroundColor: theme.color.accent, borderRadius: 12, paddingHorizontal: 10, paddingVertical: 6, borderWidth: 0, borderColor: 'transparent' }}>
            <Text style={{ color: theme.color.cardForeground, fontSize: 12, fontWeight: '600', marginBottom: 6 }}>Confirm password</Text>
            <TextInput value={confirm} onChangeText={setConfirm} placeholder="••••••••" secureTextEntry autoComplete="off" textContentType="none" importantForAutofill="no" placeholderTextColor={theme.color.mutedForeground} underlineColorAndroid="transparent" style={{ color: theme.color.cardForeground, paddingVertical: 3, fontSize: 14 }} />
          </View>

          {/* Registering as (match web labels and behavior) */}
          <View style={{ backgroundColor: theme.color.accent, borderRadius: 12, paddingHorizontal: 10, paddingVertical: 10 }}>
            <View style={{ flexDirection: 'row', alignItems: 'center', justifyContent: 'space-between' }}>
              <Text style={{ color: theme.color.cardForeground, fontSize: 12, fontWeight: '600' }}>Registering as</Text>
              <TouchableOpacity
                activeOpacity={0.8}
                onPress={() => {
                  setShowRoleTip(true)
                  if (roleTipTimeoutRef.current) clearTimeout(roleTipTimeoutRef.current)
                  roleTipTimeoutRef.current = setTimeout(() => setShowRoleTip(false), 2200)
                }}
                style={{ paddingHorizontal: 8, paddingVertical: 4 }}
              >
                <View style={{ width: 18, height: 18, borderRadius: 9, backgroundColor: theme.color.background, alignItems: 'center', justifyContent: 'center' }}>
                  <Text style={{ color: theme.color.cardForeground, fontSize: 12, fontWeight: '700' }}>i</Text>
                </View>
              </TouchableOpacity>
            </View>
            {showRoleTip ? (
              <Text style={{ color: theme.color.mutedForeground, fontSize: 12, marginTop: 6 }}>
                Choose how you’re registering. This helps tailor your setup and defaults.
              </Text>
            ) : null}
            <TouchableOpacity
              activeOpacity={0.8}
              ref={roleAnchorRef}
              onPress={() => {
                setShowRoleTip(false)
                if (roleAnchorRef.current && roleAnchorRef.current.measureInWindow) {
                  roleAnchorRef.current.measureInWindow((x: number, y: number, width: number, height: number) => {
                    setRoleMenuPos({ x, y, width, height })
                    setShowRolePicker(true)
                  })
                } else {
                  setShowRolePicker(true)
                }
              }}
              style={{ marginTop: 10, backgroundColor: theme.color.background, borderRadius: 10, paddingHorizontal: 10, paddingVertical: 10, flexDirection: 'row', alignItems: 'center', justifyContent: 'space-between' }}
            >
              <Text style={{ color: selectedRole ? theme.color.cardForeground : theme.color.mutedForeground, fontSize: 14 }}>
                {selectedRole === 'business' ? 'Business' : selectedRole === 'entrepreneur' ? 'Entrepreneur' : selectedRole === 'employee' ? 'Employee' : 'Select role'}
              </Text>
              <Text style={{ color: theme.color.mutedForeground, fontSize: 14 }}>▾</Text>
            </TouchableOpacity>
          </View>
        </View>

        <View style={{ marginTop: 20 }}>
          <Button title="Create account" size="lg" variant="hero" onPress={() => {}} />
        </View>

        <View style={{ alignItems: 'center', marginTop: 12 }}>
          <Text style={{ color: theme.color.cardForeground }}>
            Already have an account?{' '}
            <Text onPress={onLogin} style={{ color: theme.color.primary, fontWeight: '700' }}>Login</Text>
          </Text>
        </View>

        <TouchableOpacity onPress={onBack} style={{ alignSelf: 'center', marginTop: 24 }}>
          <Text style={{ color: theme.color.mutedForeground, fontWeight: '600' }}>Back</Text>
        </TouchableOpacity>

        {/* Country picker modal */}
        <Modal visible={showPicker} transparent animationType="fade" onRequestClose={() => setShowPicker(false)}>
          <View style={{ flex: 1, backgroundColor: 'rgba(0,0,0,0.5)', justifyContent: 'center', padding: 24 }}>
            <Pressable style={{ position: 'absolute', top: 0, bottom: 0, left: 0, right: 0 }} onPress={() => setShowPicker(false)} />
            <View style={{ backgroundColor: theme.color.card, borderRadius: 14, paddingVertical: 6, maxHeight: 360 }}>
              <ScrollView>
                {countries.map((c) => (
                  <TouchableOpacity
                    key={c.code}
                    activeOpacity={0.8}
                    onPress={() => { setSelectedCountry(c); setShowPicker(false) }}
                    style={{ flexDirection: 'row', alignItems: 'center', paddingHorizontal: 14, paddingVertical: 8, gap: 10 }}
                  >
                    <Text style={{ fontSize: 16 }}>{c.flag}</Text>
                    <Text style={{ color: theme.color.cardForeground, flex: 1 }}>{c.label}</Text>
                    <Text style={{ color: theme.color.mutedForeground }}>{c.dial}</Text>
                  </TouchableOpacity>
                ))}
              </ScrollView>
            </View>
          </View>
        </Modal>

        {/* Role picker modal - anchored above selector */}
        <Modal visible={showRolePicker} transparent animationType="fade" onRequestClose={() => setShowRolePicker(false)}>
          <View style={{ flex: 1 }}>
            <Pressable style={{ position: 'absolute', top: 0, bottom: 0, left: 0, right: 0 }} onPress={() => setShowRolePicker(false)} />
            {roleMenuPos ? (
              <View
                style={{
                  position: 'absolute',
                  left: roleMenuPos.x,
                  width: roleMenuPos.width,
                  bottom: Dimensions.get('window').height - roleMenuPos.y + 6,
                  backgroundColor: theme.color.card,
                  borderRadius: 10,
                  paddingVertical: 6,
                  maxHeight: 300,
                  overflow: 'hidden',
                  ...(theme.shadow ? (theme.shadow.md as any) : {}),
                }}
              >
                <ScrollView>
                  {[
                    { key: 'business', title: 'Business', desc: 'to handle basic customer service tasks' },
                    { key: 'entrepreneur', title: 'Entrepreneur', desc: "to handle my clients' day-to-day communications" },
                    { key: 'employee', title: 'Employee', desc: 'to handle my frontlining daily tasks' },
                  ].map((r) => (
                    <TouchableOpacity
                      key={r.key}
                      activeOpacity={0.8}
                      onPress={() => { setSelectedRole(r.key as any); setShowRolePicker(false) }}
                      style={{ paddingHorizontal: 14, paddingVertical: 8 }}
                    >
                      <Text style={{ color: theme.color.cardForeground, fontWeight: '600' }}>{r.title}</Text>
                      <Text style={{ color: theme.color.mutedForeground, marginTop: 2, fontSize: 12 }}>{r.desc}</Text>
                    </TouchableOpacity>
                  ))}
                </ScrollView>
              </View>
            ) : null}
          </View>
        </Modal>
        </ScrollView>
      </View>
      </KeyboardAvoidingView>
    </SafeAreaView>
  )
}
