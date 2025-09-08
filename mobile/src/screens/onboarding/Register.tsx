import React, { useRef, useState, useEffect } from 'react'
import { SafeAreaView, View, Text, TextInput, TouchableOpacity, Modal, ScrollView, Pressable, KeyboardAvoidingView, Platform, Dimensions, Animated, Easing } from 'react-native'
import Svg, { Path } from 'react-native-svg'
import { LinearGradient } from 'expo-linear-gradient'
import { useTheme } from '../../providers/ThemeProvider'
import { Button } from '../../components/ui/Button'

export const RegisterScreen: React.FC<{ onBack: () => void, onLogin?: () => void }> = ({ onBack, onLogin }) => {
  const { theme } = useTheme()
  const [firstName, setFirstName] = useState('')
  const [email, setEmail] = useState('')
  // const [phone, setPhone] = useState('') // commented out: phone number state
  const [password, setPassword] = useState('')
  const [confirm, setConfirm] = useState('')
  const [focusFirst, setFocusFirst] = useState(false)
  const [focusEmail, setFocusEmail] = useState(false)
  // const [focusPhone, setFocusPhone] = useState(false) // commented out: phone focus state
  const [focusPass, setFocusPass] = useState(false)
  const [focusConfirm, setFocusConfirm] = useState(false)
  // const [showPicker, setShowPicker] = useState(false) // commented out: country picker visibility for phone
  // const [showRoleTip, setShowRoleTip] = useState(false) // commented out: registering-as tip
  // const [selectedRole, setSelectedRole] = useState<'business' | 'entrepreneur' | 'employee' | null>(null) // commented out: registering-as value
  // const [showRolePicker, setShowRolePicker] = useState(false) // commented out: registering-as picker visibility
  // const [roleMenuPos, setRoleMenuPos] = useState<{ x: number; y: number; width: number; height: number } | null>(null) // commented out: registering-as menu pos
  // const roleAnchorRef = useRef<any>(null) // commented out: registering-as anchor ref
  // const roleTipTimeoutRef = useRef<any>(null) // commented out: registering-as tip timer

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

  // const countries = [
  //   { code: 'EG', dial: '+20', flag: '🇪🇬', label: 'Egypt' },
  //   { code: 'UAE', dial: '+971', flag: '🇦🇪', label: 'UAE' },
  //   { code: 'KSA', dial: '+966', flag: '🇸🇦', label: 'KSA' },
  //   { code: 'US', dial: '+1', flag: '🇺🇸', label: 'United States' },
  //   { code: 'UK', dial: '+44', flag: '🇬🇧', label: 'United Kingdom' },
  //   { code: 'FR', dial: '+33', flag: '🇫🇷', label: 'France' },
  //   { code: 'DE', dial: '+49', flag: '🇩🇪', label: 'Germany' },
  //   { code: 'ES', dial: '+34', flag: '🇪🇸', label: 'Spain' },
  //   { code: 'IT', dial: '+39', flag: '🇮🇹', label: 'Italy' },
  // ]
  // const [selectedCountry, setSelectedCountry] = useState(countries[1])

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

        <ScrollView keyboardShouldPersistTaps="handled" keyboardDismissMode="on-drag" overScrollMode="never" contentContainerStyle={{ padding: 24, flexGrow: 1 }}>
        <View style={{ alignItems: 'center', marginTop: 24, marginBottom: 20 }}>
          <LinearGradient
            colors={[theme.color.primary, theme.color.primaryLight, 'hsl(260,100%,80%)']}
            start={{ x: 0, y: 0 }}
            end={{ x: 1, y: 1 }}
            style={{ width: 64, height: 64, borderRadius: 22, alignItems: 'center', justifyContent: 'center', marginBottom: 10 }}
          >
            <Text style={{ color: '#fff', fontSize: 28, fontWeight: '700' }}>A</Text>
          </LinearGradient>
          <Text style={{ color: theme.color.foreground, fontSize: 24, fontWeight: '700' }}>Create your account</Text>
          <Text style={{ color: theme.color.mutedForeground, marginTop: 4 }}>{typed}</Text>
        </View>

        <View style={{ gap: 8 }}>
          <View style={{ backgroundColor: theme.color.accent, borderRadius: 12, paddingHorizontal: 10, paddingVertical: 6, borderWidth: 1, borderColor: focusFirst ? theme.color.ring : 'transparent', shadowColor: theme.color.primary, shadowOpacity: focusFirst ? 0.12 : 0, shadowRadius: 10, shadowOffset: { width: 0, height: 6 }, elevation: 0 }}>
            <Text style={{ color: theme.color.cardForeground, fontSize: 12, fontWeight: '600', marginBottom: 4 }}>First name</Text>
            <TextInput value={firstName} onChangeText={setFirstName} placeholder="John" placeholderTextColor={theme.color.mutedForeground} underlineColorAndroid="transparent" onFocus={() => setFocusFirst(true)} onBlur={() => setFocusFirst(false)} style={{ color: theme.color.cardForeground, paddingVertical: 2, fontSize: 14, borderWidth: 0, borderColor: 'transparent' }} />
          </View>
          <View style={{ backgroundColor: theme.color.accent, borderRadius: 12, paddingHorizontal: 10, paddingVertical: 6, borderWidth: 1, borderColor: focusEmail ? theme.color.ring : 'transparent', shadowColor: theme.color.primary, shadowOpacity: focusEmail ? 0.12 : 0, shadowRadius: 10, shadowOffset: { width: 0, height: 6 }, elevation: 0 }}>
            <Text style={{ color: theme.color.cardForeground, fontSize: 12, fontWeight: '600', marginBottom: 4 }}>Email</Text>
            <TextInput value={email} onChangeText={setEmail} placeholder="you@company.com" keyboardType="email-address" placeholderTextColor={theme.color.mutedForeground} underlineColorAndroid="transparent" onFocus={() => setFocusEmail(true)} onBlur={() => setFocusEmail(false)} style={{ color: theme.color.cardForeground, paddingVertical: 2, fontSize: 14, borderWidth: 0, borderColor: 'transparent' }} />
          </View>
          {/** Phone number field temporarily commented out */}
          {/**
          <View style={{ backgroundColor: theme.color.accent, borderRadius: 12, paddingHorizontal: 10, paddingVertical: 6, borderWidth: 1, borderColor: focusPhone ? theme.color.ring : 'transparent', shadowColor: theme.color.primary, shadowOpacity: focusPhone ? 0.12 : 0, shadowRadius: 10, shadowOffset: { width: 0, height: 6 }, elevation: 0 }}>
            <Text style={{ color: theme.color.cardForeground, fontSize: 12, fontWeight: '600', marginBottom: 4 }}>Phone (optional)</Text>
            <View style={{ flexDirection: 'row', alignItems: 'center', gap: 8 }}>
              <TouchableOpacity
                activeOpacity={0.8}
                onPress={() => setShowPicker(true)}
                style={{
                  flexDirection: 'row',
                  alignItems: 'center',
                  gap: 6,
                  paddingHorizontal: 8,
                  paddingVertical: 3,
                  backgroundColor: theme.color.background,
                  borderRadius: 8,
                  borderWidth: 0,
                  borderColor: 'transparent',
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
                onFocus={() => setFocusPhone(true)}
                onBlur={() => setFocusPhone(false)}
                style={{ color: theme.color.cardForeground, paddingVertical: 2, fontSize: 14, flex: 1, borderWidth: 0, borderColor: 'transparent' }}
              />
            </View>
          </View>
          */}
          <View style={{ backgroundColor: theme.color.accent, borderRadius: 12, paddingHorizontal: 10, paddingVertical: 6, borderWidth: 1, borderColor: focusPass ? theme.color.ring : 'transparent', shadowColor: theme.color.primary, shadowOpacity: focusPass ? 0.12 : 0, shadowRadius: 10, shadowOffset: { width: 0, height: 6 }, elevation: 0 }}>
            <Text style={{ color: theme.color.cardForeground, fontSize: 12, fontWeight: '600', marginBottom: 4 }}>Password</Text>
            <TextInput value={password} onChangeText={setPassword} placeholder="••••••••" secureTextEntry autoComplete="off" textContentType="none" importantForAutofill="no" placeholderTextColor={theme.color.mutedForeground} underlineColorAndroid="transparent" onFocus={() => setFocusPass(true)} onBlur={() => setFocusPass(false)} style={{ color: theme.color.cardForeground, paddingVertical: 2, fontSize: 14, borderWidth: 0, borderColor: 'transparent' }} />
          </View>
          <View style={{ backgroundColor: theme.color.accent, borderRadius: 12, paddingHorizontal: 10, paddingVertical: 6, borderWidth: 1, borderColor: focusConfirm ? theme.color.ring : 'transparent', shadowColor: theme.color.primary, shadowOpacity: focusConfirm ? 0.12 : 0, shadowRadius: 10, shadowOffset: { width: 0, height: 6 }, elevation: 0 }}>
            <Text style={{ color: theme.color.cardForeground, fontSize: 12, fontWeight: '600', marginBottom: 4 }}>Confirm password</Text>
            <TextInput value={confirm} onChangeText={setConfirm} placeholder="••••••••" secureTextEntry autoComplete="off" textContentType="none" importantForAutofill="no" placeholderTextColor={theme.color.mutedForeground} underlineColorAndroid="transparent" onFocus={() => setFocusConfirm(true)} onBlur={() => setFocusConfirm(false)} style={{ color: theme.color.cardForeground, paddingVertical: 2, fontSize: 14, borderWidth: 0, borderColor: 'transparent' }} />
          </View>

          {/* Registering as (match web labels and behavior) */}
          {/**
          <View style={{ backgroundColor: theme.color.accent, borderRadius: 12, paddingHorizontal: 10, paddingVertical: 8 }}>
            <View style={{ flexDirection: 'row', alignItems: 'center', justifyContent: 'space-between' }}>
              <Text style={{ color: theme.color.cardForeground, fontSize: 12, fontWeight: '600' }}>Registering as</Text>
              <TouchableOpacity
                activeOpacity={0.8}
                onPress={() => {
                  setShowRoleTip(true)
                  if (roleTipTimeoutRef.current) clearTimeout(roleTipTimeoutRef.current)
                  roleTipTimeoutRef.current = setTimeout(() => setShowRoleTip(false), 2200)
                }}
                style={{ paddingHorizontal: 8, paddingVertical: 8 }}
              >
                <View style={{ width: 24, height: 24, borderRadius: 12, backgroundColor: theme.dark ? theme.color.accent : 'hsla(240,75%,48%,0.12)', alignItems: 'center', justifyContent: 'center', borderWidth: 0, borderColor: 'transparent' }}>
                  <Text style={{ color: theme.dark ? theme.color.cardForeground : theme.color.primary, fontSize: 12, fontWeight: '700' }}>i</Text>
                </View>
              </TouchableOpacity>
            </View>
            {showRoleTip ? (
              <Text style={{ color: theme.color.mutedForeground, fontSize: 12, marginTop: 4 }}>
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
              style={{ marginTop: 8, backgroundColor: theme.color.background, borderRadius: 10, paddingHorizontal: 10, paddingVertical: 8, flexDirection: 'row', alignItems: 'center', justifyContent: 'space-between', borderWidth: 0, borderColor: 'transparent' }}
            >
              <Text style={{ color: selectedRole ? theme.color.cardForeground : theme.color.mutedForeground, fontSize: 14 }}>
                {selectedRole === 'business' ? 'Business' : selectedRole === 'entrepreneur' ? 'Entrepreneur' : selectedRole === 'employee' ? 'Employee' : 'Select role'}
              </Text>
              <Text style={{ color: theme.color.mutedForeground, fontSize: 14 }}>▾</Text>
            </TouchableOpacity>
          </View>
          */}
        </View>

        <View style={{ marginTop: 12 }}>
          <Button title="Create account" size="lg" variant="hero" onPress={() => {}} />
        </View>

        {/* OR separator */}
        <View style={{ flexDirection: 'row', alignItems: 'center', justifyContent: 'center', marginVertical: 10 }}>
          <View style={{ flex: 1, height: 1, backgroundColor: theme.color.border }} />
          <Text style={{ color: theme.color.mutedForeground, fontWeight: '600', marginHorizontal: 10 }}>OR</Text>
          <View style={{ flex: 1, height: 1, backgroundColor: theme.color.border }} />
        </View>

        {/* Social logins */}
        {Platform.OS === 'ios' && (
          <TouchableOpacity
            activeOpacity={0.8}
            style={{
              height: 48,
              borderRadius: 12,
              backgroundColor: theme.color.accent,
              alignItems: 'center',
              justifyContent: 'center',
              width: '100%',
              alignSelf: 'center',
              marginBottom: 8,
            }}
          >
            <View style={{ flexDirection: 'row', alignItems: 'center', gap: 8 }}>
              <View style={{ width: 24, height: 24, borderRadius: 12, backgroundColor: 'transparent', alignItems: 'center', justifyContent: 'center', overflow: 'hidden' }}>
                <Svg width={16} height={16} viewBox="0 0 24 24">
                  <Path fill={theme.color.cardForeground as any} d="M16.365 1.43c-.977.058-2.128.66-2.8 1.436-.607.703-1.14 1.859-.94 2.938 1.083.083 2.2-.552 2.888-1.337.605-.692 1.083-1.822.852-3.037zM19.5 12.64c-.053-3.086 2.52-4.559 2.631-4.623-1.429-2.084-3.649-2.37-4.429-2.4-1.889-.192-3.684 1.135-4.642 1.135-.976 0-2.436-1.11-4.001-1.08-2.058.03-3.973 1.196-5.032 3.04-2.148 3.723-.548 9.214 1.547 12.236 1.017 1.463 2.223 3.107 3.8 3.052 1.53-.06 2.107-.988 3.963-.988 1.841 0 2.383.988 3.999.958 1.653-.027 2.698-1.492 3.702-2.962 1.165-1.706 1.644-3.36 1.671-3.443-.037-.017-3.211-1.233-3.238-4.825z"/>
                </Svg>
              </View>
              <Text style={{ color: theme.color.cardForeground, fontWeight: '600' }}>Continue with Apple</Text>
            </View>
          </TouchableOpacity>
        )}
        <TouchableOpacity
          activeOpacity={0.8}
          style={{
            height: 48,
            borderRadius: 12,
            backgroundColor: theme.color.accent,
            alignItems: 'center',
            justifyContent: 'center',
            width: '100%',
            alignSelf: 'center',
            marginBottom: 12,
          }}
        >
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

        <View style={{ alignItems: 'center', marginTop: 12 }}>
          <Text style={{ color: theme.color.cardForeground }}>
            Already have an account?{' '}
            <Text onPress={onLogin} style={{ color: theme.color.primary, fontWeight: '700' }}>Login</Text>
          </Text>
        </View>

        <TouchableOpacity onPress={onBack} style={{ alignSelf: 'center', marginTop: 20 }}>
          <Text style={{ color: theme.color.mutedForeground, fontWeight: '600' }}>Back</Text>
        </TouchableOpacity>

        {/** Country picker modal for phone (temporarily commented out) */}
        {/**
        <Modal visible={showPicker} transparent animationType="fade" onRequestClose={() => setShowPicker(false)}>
          <View style={{ flex: 1, backgroundColor: 'rgba(0,0,0,0.5)', justifyContent: 'center', padding: 24 }}>
            <Pressable style={{ position: 'absolute', top: 0, bottom: 0, left: 0, right: 0 }} onPress={() => setShowPicker(false)} />
            <View style={{ backgroundColor: theme.color.card, borderRadius: 14, paddingVertical: 6, maxHeight: 360, borderWidth: 0, borderColor: 'transparent' }}>
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
        */}

        {/** Role picker modal - commented out */}
        {/**
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
                  borderWidth: 0,
                  borderColor: 'transparent',
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
        */}
        </ScrollView>
      </View>
      </KeyboardAvoidingView>
    </SafeAreaView>
  )
}
