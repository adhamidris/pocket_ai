import React, { useRef, useState, useEffect } from 'react'
import { SafeAreaView, View, Text, TextInput, TouchableOpacity, Modal, ScrollView, Pressable, KeyboardAvoidingView, Platform, Dimensions, Animated, Easing, LayoutAnimation, UIManager } from 'react-native'
import Svg, { Path } from 'react-native-svg'
import { LinearGradient } from 'expo-linear-gradient'
import { useTheme } from '../../providers/ThemeProvider'
import { Button } from '../../components/ui/Button'
import * as Localization from 'expo-localization'

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

  // Flow step: 'form' | 'role' | 'business'
  const [step, setStep] = useState<'form' | 'role' | 'business'>('form')

  // Business form state
  const [company, setCompany] = useState('')
  const [industry, setIndustry] = useState('')
  const [employees, setEmployees] = useState('')
  const [website, setWebsite] = useState('')
  const [focusCompany, setFocusCompany] = useState(false)
  const [focusIndustry, setFocusIndustry] = useState(false)
  const [focusEmployees, setFocusEmployees] = useState(false)
  const [focusWebsite, setFocusWebsite] = useState(false)
  const [showIndustryModal, setShowIndustryModal] = useState(false)
  const [showSizeModal, setShowSizeModal] = useState(false)
  const sizeOptions = ['1-10','11-50','51-200','201-1000','1000+']
  const industryOptions = [
    'E-Commerce',
    'Retail',
    'SaaS',
    'Healthcare',
    'Finance',
    'Education',
    'Hospitality',
    'Real Estate',
    'Marketing / Advertising',
    'Manufacturing',
    'Logistics / Transportation',
    'Media / Entertainment',
    'Non-Profit',
    'Other',
  ]
  const [otherIndustry, setOtherIndustry] = useState('')
  const [focusOtherIndustry, setFocusOtherIndustry] = useState(false)

  // LOB options per industry (mirrors web, with sensible fallbacks)
  const lobOptionsByIndustry: Record<string, string[]> = {
    'E-Commerce': ['Apparel','Electronics','Beauty & Personal Care','Home & Kitchen','Sports & Outdoors','Groceries','Digital Goods','Handmade & Crafts','Automotive Accessories'],
    'SaaS': ['CRM','Marketing Automation','Analytics','Project Management','Customer Support','Developer Tools','Fintech','Productivity','Security'],
    'Finance': ['Banking','Lending','Payments','Wealth Management','Insurance','Accounting','Crypto / Blockchain'],
    'Healthcare': ['Clinics','Telemedicine','Pharmacy','Diagnostics','Medical Devices','Wellness'],
    'Education': ['K-12','Higher Education','EdTech Platform','Corporate Training','Test Prep','Language Learning'],
    'Hospitality': ['Hotels','Restaurants','Catering','Travel & Tours','Venues & Events'],
    'Retail': ['Apparel','Electronics','Home & Kitchen','Beauty & Personal Care','Groceries','Sports & Outdoors'],
    'Real Estate': ['Residential','Commercial','Property Management','Brokerage','Vacation Rentals'],
    'Marketing / Advertising': ['Agency Services','Performance Marketing','Branding','SEO/SEM','Content Marketing','Social Media'],
    'Manufacturing': ['Automotive','Consumer Goods','Industrial Equipment','Electronics','Food & Beverage'],
    'Logistics / Transportation': ['Freight','Last-mile Delivery','Warehousing','Fleet Management','Courier Services'],
    'Media / Entertainment': ['Streaming','Gaming','Publishing','Music','Film/TV','Events'],
    'Non-Profit': ['Education','Healthcare','Community Services','Environmental','Religious','Arts & Culture'],
    'Other': ['Consulting','Retail','Manufacturing','Services','Non-profit']
  }
  const getLobOptions = (ind: string) => lobOptionsByIndustry[ind] || lobOptionsByIndustry['Other']

  const [selectedLobs, setSelectedLobs] = useState<string[]>([])
  const [showLobModal, setShowLobModal] = useState(false)
  const [focusLob, setFocusLob] = useState(false)
  const lobAnchorRef = useRef<any>(null)
  const [lobMenuPos, setLobMenuPos] = useState<{ x: number, y: number, width: number, height: number } | null>(null)
  const [lobContentH, setLobContentH] = useState(0)
  const [lobContainerH, setLobContainerH] = useState(0)
  const [lobScrollY, setLobScrollY] = useState(0)
  const [specifyProducts, setSpecifyProducts] = useState('')
  const [focusSpecifyProducts, setFocusSpecifyProducts] = useState(false)

  useEffect(() => {
    // Filter out LOBs not valid for the new industry
    const options = getLobOptions(industry)
    setSelectedLobs((prev) => prev.filter((x) => options.includes(x)))
  }, [industry])

  // Country state (dropdown)
  const [country, setCountry] = useState('')
  const [focusCountry, setFocusCountry] = useState(false)
  const [showCountryModal, setShowCountryModal] = useState(false)
  const countryOptions = [
    { value: 'United States', flag: '🇺🇸' },
    { value: 'United Kingdom', flag: '🇬🇧' },
    { value: 'United Arab Emirates', flag: '🇦🇪' },
    { value: 'Saudi Arabia', flag: '🇸🇦' },
    { value: 'Egypt', flag: '🇪🇬' },
    { value: 'France', flag: '🇫🇷' },
    { value: 'Germany', flag: '🇩🇪' },
    { value: 'Spain', flag: '🇪🇸' },
    { value: 'Italy', flag: '🇮🇹' },
    { value: 'Other', flag: '🌐' },
  ]

  // Anchors for dropdown positioning
  const industryAnchorRef = useRef<any>(null)
  const [industryMenuPos, setIndustryMenuPos] = useState<{ x: number, y: number, width: number, height: number } | null>(null)
  const countryAnchorRef = useRef<any>(null)
  const [countryMenuPos, setCountryMenuPos] = useState<{ x: number, y: number, width: number, height: number } | null>(null)
  const sizeAnchorRef = useRef<any>(null)
  const [sizeMenuPos, setSizeMenuPos] = useState<{ x: number, y: number, width: number, height: number } | null>(null)

  // Scrollbar metrics for dropdowns
  const [industryContentH, setIndustryContentH] = useState(0)
  const [industryContainerH, setIndustryContainerH] = useState(0)
  const [industryScrollY, setIndustryScrollY] = useState(0)
  const [countryContentH, setCountryContentH] = useState(0)
  const [countryContainerH, setCountryContainerH] = useState(0)
  const [countryScrollY, setCountryScrollY] = useState(0)
  const [sizeContentH, setSizeContentH] = useState(0)
  const [sizeContainerH, setSizeContainerH] = useState(0)
  const [sizeScrollY, setSizeScrollY] = useState(0)

  // Enable LayoutAnimation on Android
  useEffect(() => {
    if (Platform.OS === 'android' && UIManager && (UIManager as any).setLayoutAnimationEnabledExperimental) {
      (UIManager as any).setLayoutAnimationEnabledExperimental(true)
    }
  }, [])

  useEffect(() => {
    if (industry !== 'Other') {
      setOtherIndustry('')
      setFocusOtherIndustry(false)
    }
  }, [industry])

  // Auto-detect country on business step (timezone first, then region/locale)
  useEffect(() => {
    if (step !== 'business') return
    if (country) return
    try {
      const regionToCountry: Record<string, string> = {
        US: 'United States',
        GB: 'United Kingdom',
        AE: 'United Arab Emirates',
        SA: 'Saudi Arabia',
        EG: 'Egypt',
        FR: 'France',
        DE: 'Germany',
        ES: 'Spain',
        IT: 'Italy',
      }

      let region: string | undefined
      const tz = (Intl && Intl.DateTimeFormat && Intl.DateTimeFormat().resolvedOptions().timeZone) || (Localization as any).timezone
      const tzToRegion: Record<string, string> = {
        'Europe/London': 'GB',
        'Europe/Paris': 'FR',
        'Europe/Berlin': 'DE',
        'Europe/Madrid': 'ES',
        'Europe/Rome': 'IT',
        'America/New_York': 'US',
        'America/Chicago': 'US',
        'America/Denver': 'US',
        'America/Los_Angeles': 'US',
        'Asia/Riyadh': 'SA',
        'Asia/Dubai': 'AE',
        'Africa/Cairo': 'EG',
      }
      if (tz && tzToRegion[tz]) region = tzToRegion[tz]

      if (!region) {
        const locRegion = (Localization as any).region
        if (locRegion) region = String(locRegion).toUpperCase()
      }

      if (!region) {
        try {
          const locales = (Localization as any).getLocales?.() || []
          for (const l of locales) {
            if (l?.regionCode || l?.region) { region = String((l.regionCode || l.region)).toUpperCase(); break }
            if (l?.languageTag) {
              const m = String(l.languageTag).match(/[-_](\w{2})/)
              if (m && m[1]) { region = m[1].toUpperCase(); break }
            }
          }
        } catch {}
      }

      // Last resort: if primary language is Arabic, prefer Egypt
      if (!region) {
        try {
          const locales = (Localization as any).getLocales?.() || []
          const primary = locales[0]?.languageCode || locales[0]?.languageTag || ''
          if (primary && /^ar/i.test(String(primary))) region = 'EG'
        } catch {}
      }

      const mapped = region ? regionToCountry[region] : undefined
      if (mapped) setCountry(mapped)
    } catch {}
  }, [step, country])

  // Streaming subtitle (depends on step)
  const FORM_SUB = 'Join and get started in minutes'
  const ROLE_SUB = "Just few more steps and you're ready.."
  const BUSINESS_SUB = 'Help us understand your business'
  const targetSub = step === 'form' ? FORM_SUB : step === 'role' ? ROLE_SUB : BUSINESS_SUB
  const [typed, setTyped] = useState('')
  const subIntervalRef = useRef<any>(null)
  const subTimeoutRef = useRef<any>(null)

  useEffect(() => {
    const start = () => {
      let i = 0
      if (subIntervalRef.current) clearInterval(subIntervalRef.current)
      if (subTimeoutRef.current) clearTimeout(subTimeoutRef.current)
      setTyped('')
      subIntervalRef.current = setInterval(() => {
        i += 1
        setTyped(targetSub.slice(0, i))
        if (i >= targetSub.length) {
          clearInterval(subIntervalRef.current)
          subTimeoutRef.current = setTimeout(start, 1500)
        }
      }, 85)
    }
    start()
    return () => { clearInterval(subIntervalRef.current); clearTimeout(subTimeoutRef.current) }
  }, [targetSub])

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

  const goToRoleStep = () => {
    LayoutAnimation.configureNext(LayoutAnimation.Presets.easeInEaseOut)
    setStep('role')
  }
  const goToBusinessStep = () => {
    LayoutAnimation.configureNext(LayoutAnimation.Presets.easeInEaseOut)
    setStep('business')
  }

  const handleBack = () => {
    if (step === 'business') {
      LayoutAnimation.configureNext(LayoutAnimation.Presets.easeInEaseOut)
      setStep('role')
      return
    }
    if (step === 'role') {
      LayoutAnimation.configureNext(LayoutAnimation.Presets.easeInEaseOut)
      setStep('form')
      return
    }
    onBack()
  }

  const headerTitle = step === 'form' ? 'Create your account' : step === 'role' ? 'Tell us more about you' : 'Business profile setup'

  const openIndustryMenu = () => {
    setFocusIndustry(true)
    requestAnimationFrame(() => {
      try {
        industryAnchorRef.current?.measureInWindow?.((x: number, y: number, width: number, height: number) => {
          setIndustryMenuPos({ x, y, width, height })
          setShowIndustryModal(true)
        })
      } catch {
        setShowIndustryModal(true)
      }
    })
  }
  const openCountryMenu = () => {
    setFocusCountry(true)
    requestAnimationFrame(() => {
      try {
        countryAnchorRef.current?.measureInWindow?.((x: number, y: number, width: number, height: number) => {
          setCountryMenuPos({ x, y, width, height })
          setShowCountryModal(true)
        })
      } catch {
        setShowCountryModal(true)
      }
    })
  }
  const openSizeMenu = () => {
    setFocusEmployees(true)
    requestAnimationFrame(() => {
      try {
        sizeAnchorRef.current?.measureInWindow?.((x: number, y: number, width: number, height: number) => {
          setSizeMenuPos({ x, y, width, height })
          setShowSizeModal(true)
        })
      } catch {
        setShowSizeModal(true)
      }
    })
  }
  const openLobMenu = () => {
    setFocusLob(true)
    requestAnimationFrame(() => {
      try {
        lobAnchorRef.current?.measureInWindow?.((x: number, y: number, width: number, height: number) => {
          setLobMenuPos({ x, y, width, height })
          setShowLobModal(true)
        })
      } catch {
        setShowLobModal(true)
      }
    })
  }

  const getMenuPlacement = (pos: { x: number; y: number; width: number; height: number }) => {
    const screenH = Dimensions.get('window').height
    const margin = 24
    const gap = 6
    const below = Math.max(0, screenH - (pos.y + pos.height + gap) - margin)
    const above = Math.max(0, pos.y - margin)
    const openBelow = below >= Math.max(180, above)
    const maxHeight = Math.min(320, openBelow ? below : above)
    const top = openBelow ? (pos.y + pos.height + gap) : Math.max(margin, pos.y - maxHeight - gap)
    return { top, maxHeight }
  }

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
          <Text style={{ color: theme.color.foreground, fontSize: 24, fontWeight: '700' }}>{headerTitle}</Text>
          <Text style={{ color: theme.color.mutedForeground, marginTop: 4 }}>{typed}</Text>
        </View>

        {step === 'form' ? (
          <>
            <View style={{ gap: 8 }}>
              <View style={{ backgroundColor: theme.color.accent, borderRadius: 12, paddingHorizontal: 10, paddingVertical: 6, borderWidth: 0, borderColor: 'transparent', shadowColor: theme.color.primary, shadowOpacity: focusFirst ? 0.12 : 0, shadowRadius: 10, shadowOffset: { width: 0, height: 6 }, elevation: 0 }}>
                <Text style={{ color: theme.color.cardForeground, fontSize: 12, fontWeight: '600', marginBottom: 4 }}>First name</Text>
                <TextInput value={firstName} onChangeText={setFirstName} placeholder="John" placeholderTextColor={theme.color.mutedForeground} underlineColorAndroid="transparent" onFocus={() => setFocusFirst(true)} onBlur={() => setFocusFirst(false)} style={{ color: theme.color.cardForeground, paddingVertical: 2, fontSize: 14, borderWidth: 0, borderColor: 'transparent' }} />
              </View>
              <View style={{ backgroundColor: theme.color.accent, borderRadius: 12, paddingHorizontal: 10, paddingVertical: 6, borderWidth: 0, borderColor: 'transparent', shadowColor: theme.color.primary, shadowOpacity: focusEmail ? 0.12 : 0, shadowRadius: 10, shadowOffset: { width: 0, height: 6 }, elevation: 0 }}>
                <Text style={{ color: theme.color.cardForeground, fontSize: 12, fontWeight: '600', marginBottom: 4 }}>Email</Text>
                <TextInput value={email} onChangeText={setEmail} placeholder="you@company.com" keyboardType="email-address" placeholderTextColor={theme.color.mutedForeground} underlineColorAndroid="transparent" onFocus={() => setFocusEmail(true)} onBlur={() => setFocusEmail(false)} style={{ color: theme.color.cardForeground, paddingVertical: 2, fontSize: 14, borderWidth: 0, borderColor: 'transparent' }} />
              </View>
              <View style={{ backgroundColor: theme.color.accent, borderRadius: 12, paddingHorizontal: 10, paddingVertical: 6, borderWidth: 0, borderColor: 'transparent', shadowColor: theme.color.primary, shadowOpacity: focusPass ? 0.12 : 0, shadowRadius: 10, shadowOffset: { width: 0, height: 6 }, elevation: 0 }}>
                <Text style={{ color: theme.color.cardForeground, fontSize: 12, fontWeight: '600', marginBottom: 4 }}>Password</Text>
                <TextInput value={password} onChangeText={setPassword} placeholder="••••••••" secureTextEntry autoComplete="off" textContentType="none" importantForAutofill="no" placeholderTextColor={theme.color.mutedForeground} underlineColorAndroid="transparent" onFocus={() => setFocusPass(true)} onBlur={() => setFocusPass(false)} style={{ color: theme.color.cardForeground, paddingVertical: 2, fontSize: 14, borderWidth: 0, borderColor: 'transparent' }} />
              </View>
              <View style={{ backgroundColor: theme.color.accent, borderRadius: 12, paddingHorizontal: 10, paddingVertical: 6, borderWidth: 0, borderColor: 'transparent', shadowColor: theme.color.primary, shadowOpacity: focusConfirm ? 0.12 : 0, shadowRadius: 10, shadowOffset: { width: 0, height: 6 }, elevation: 0 }}>
                <Text style={{ color: theme.color.cardForeground, fontSize: 12, fontWeight: '600', marginBottom: 4 }}>Confirm password</Text>
                <TextInput value={confirm} onChangeText={setConfirm} placeholder="••••••••" secureTextEntry autoComplete="off" textContentType="none" importantForAutofill="no" placeholderTextColor={theme.color.mutedForeground} underlineColorAndroid="transparent" onFocus={() => setFocusConfirm(true)} onBlur={() => setFocusConfirm(false)} style={{ color: theme.color.cardForeground, paddingVertical: 2, fontSize: 14, borderWidth: 0, borderColor: 'transparent' }} />
              </View>
            </View>

            <View style={{ marginTop: 12 }}>
              <Button title="Create account" size="lg" variant="hero" onPress={goToRoleStep} />
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
                onPress={goToRoleStep}
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
              onPress={goToRoleStep}
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

            <TouchableOpacity onPress={handleBack} style={{ alignSelf: 'center', marginTop: 20 }}>
              <Text style={{ color: theme.color.mutedForeground, fontWeight: '600' }}>Back</Text>
            </TouchableOpacity>
          </>
        ) : step === 'role' ? (
          // Step: role selection
          <View style={{ flexGrow: 1, justifyContent: 'center', paddingTop: 8, marginTop: -24 }}>
            <View style={{ alignItems: 'center', marginBottom: 16 }}>
              <Text style={{ color: theme.color.foreground, fontSize: 18, fontWeight: '700' }}>I'm registering as a</Text>
            </View>
            <View style={{ gap: 16, alignItems: 'center', marginTop: 8 }}>
              <TouchableOpacity activeOpacity={0.9} onPress={goToBusinessStep} style={{ overflow: 'hidden', borderRadius: 12, alignSelf: 'center', width: '96%' }}>
                <LinearGradient colors={[theme.color.primary, theme.color.primaryLight]} start={{ x: 0, y: 0 }} end={{ x: 1, y: 1 }} style={{ paddingVertical: 20, paddingHorizontal: 16, borderRadius: 12, alignItems: 'center', justifyContent: 'center' }}>
                  <Text style={{ color: '#fff', fontSize: 16, fontWeight: '700', marginBottom: 4, textAlign: 'center', lineHeight: 22 }}>Business</Text>
                  <Text style={{ color: '#fff', opacity: 0.9, textAlign: 'center', fontSize: 13, lineHeight: 18 }}>to handle basic customer service tasks</Text>
                </LinearGradient>
              </TouchableOpacity>
              <TouchableOpacity activeOpacity={0.9} style={{ overflow: 'hidden', borderRadius: 12, alignSelf: 'center', width: '96%' }}>
                <LinearGradient colors={[theme.color.primary, theme.color.primaryLight]} start={{ x: 0, y: 0 }} end={{ x: 1, y: 1 }} style={{ paddingVertical: 20, paddingHorizontal: 16, borderRadius: 12, alignItems: 'center', justifyContent: 'center' }}>
                  <Text style={{ color: '#fff', fontSize: 16, fontWeight: '700', marginBottom: 4, textAlign: 'center', lineHeight: 22 }}>Entrepreneur</Text>
                  <Text style={{ color: '#fff', opacity: 0.9, textAlign: 'center', fontSize: 13, lineHeight: 18 }}>to handle my clients' communications</Text>
                </LinearGradient>
              </TouchableOpacity>
              <TouchableOpacity activeOpacity={0.9} style={{ overflow: 'hidden', borderRadius: 12, alignSelf: 'center', width: '96%' }}>
                <LinearGradient colors={[theme.color.primary, theme.color.primaryLight]} start={{ x: 0, y: 0 }} end={{ x: 1, y: 1 }} style={{ paddingVertical: 20, paddingHorizontal: 16, borderRadius: 12, alignItems: 'center', justifyContent: 'center' }}>
                  <Text style={{ color: '#fff', fontSize: 16, fontWeight: '700', marginBottom: 4, textAlign: 'center', lineHeight: 22 }}>Employee</Text>
                  <Text style={{ color: '#fff', opacity: 0.9, textAlign: 'center', fontSize: 13, lineHeight: 18 }}>to handle my frontlining daily tasks</Text>
                </LinearGradient>
              </TouchableOpacity>
            </View>

            <View style={{ marginTop: 16, paddingHorizontal: 8 }}>
              <Text style={{ color: theme.color.mutedForeground, fontSize: 12, textAlign: 'center' }}>
                To help us tailor your setup, please choose what best fits your intended use.
              </Text>
            </View>

            <TouchableOpacity onPress={handleBack} style={{ alignSelf: 'center', marginTop: 24 }}>
              <Text style={{ color: theme.color.mutedForeground, fontWeight: '600' }}>Back</Text>
            </TouchableOpacity>
          </View>
        ) : (
          // Step: business form
          <View style={{ marginTop: 4 }}>
            <View style={{ gap: 8 }}>
              <View style={{ backgroundColor: theme.color.accent, borderRadius: 12, paddingHorizontal: 10, paddingVertical: 6, borderWidth: 0, borderColor: 'transparent', shadowColor: theme.color.primary, shadowOpacity: focusCompany ? 0.12 : 0, shadowRadius: 10, shadowOffset: { width: 0, height: 6 }, elevation: 0 }}>
                <Text style={{ color: theme.color.cardForeground, fontSize: 12, fontWeight: '600', marginBottom: 4 }}>Company name</Text>
                <TextInput value={company} onChangeText={setCompany} placeholder="Acme Inc" placeholderTextColor={theme.color.mutedForeground} underlineColorAndroid="transparent" onFocus={() => setFocusCompany(true)} onBlur={() => setFocusCompany(false)} style={{ color: theme.color.cardForeground, paddingVertical: 2, fontSize: 14, borderWidth: 0, borderColor: 'transparent' }} />
              </View>
              <View style={{ backgroundColor: theme.color.accent, borderRadius: 12, paddingHorizontal: 10, paddingVertical: 6, borderWidth: 0, borderColor: 'transparent', shadowColor: theme.color.primary, shadowOpacity: focusIndustry ? 0.12 : 0, shadowRadius: 10, shadowOffset: { width: 0, height: 6 }, elevation: 0 }}>
                <Text style={{ color: theme.color.cardForeground, fontSize: 12, fontWeight: '600', marginBottom: 4 }}>Industry</Text>
                <Pressable
                  ref={industryAnchorRef}
                  collapsable={false}
                  onPress={openIndustryMenu}
                  hitSlop={{ top: 8, bottom: 8, left: 8, right: 8 }}
                >
                  <View style={{ flexDirection: 'row', alignItems: 'center', justifyContent: 'space-between' }}>
                    <Text style={{ color: industry ? theme.color.cardForeground : theme.color.mutedForeground, paddingVertical: 2, fontSize: 14 }}>
                      {industry || 'Select industry'}
                    </Text>
                    <Text style={{ color: theme.color.mutedForeground, fontSize: 16 }}>▾</Text>
                  </View>
                </Pressable>
              </View>
              {industry === 'Other' && (
                <View style={{ backgroundColor: theme.color.accent, borderRadius: 12, paddingHorizontal: 10, paddingVertical: 6, borderWidth: 0, borderColor: 'transparent', shadowColor: theme.color.primary, shadowOpacity: focusOtherIndustry ? 0.12 : 0, shadowRadius: 10, shadowOffset: { width: 0, height: 6 }, elevation: 0 }}>
                  <Text style={{ color: theme.color.cardForeground, fontSize: 12, fontWeight: '600', marginBottom: 4 }}>Industry (please specify)</Text>
                  <TextInput value={otherIndustry} onChangeText={setOtherIndustry} placeholder="Type your industry" placeholderTextColor={theme.color.mutedForeground} underlineColorAndroid="transparent" onFocus={() => setFocusOtherIndustry(true)} onBlur={() => setFocusOtherIndustry(false)} style={{ color: theme.color.cardForeground, paddingVertical: 2, fontSize: 14, borderWidth: 0, borderColor: 'transparent' }} />
                </View>
              )}

              {/* Line of Products/Services */}
              <View style={{ backgroundColor: theme.color.accent, borderRadius: 12, paddingHorizontal: 10, paddingVertical: 6, borderWidth: 0, borderColor: 'transparent', shadowColor: theme.color.primary, shadowOpacity: focusLob ? 0.12 : 0, shadowRadius: 10, shadowOffset: { width: 0, height: 6 }, elevation: 0 }}>
                <Text style={{ color: theme.color.cardForeground, fontSize: 12, fontWeight: '600', marginBottom: 4 }}>Line of Products/Services</Text>
                <Pressable
                  ref={lobAnchorRef}
                  collapsable={false}
                  onPress={openLobMenu}
                  hitSlop={{ top: 8, bottom: 8, left: 8, right: 8 }}
                >
                  <View style={{ flexDirection: 'row', alignItems: 'center', justifyContent: 'space-between' }}>
                    <Text style={{ color: selectedLobs.length ? theme.color.cardForeground : theme.color.mutedForeground, paddingVertical: 2, fontSize: 14 }}>
                      {selectedLobs.length ? (selectedLobs.length <= 2 ? selectedLobs.join(', ') : `${selectedLobs.slice(0,2).join(', ')} +${selectedLobs.length - 2}`) : 'Select Products/Services'}
                    </Text>
                    <Text style={{ color: theme.color.mutedForeground, fontSize: 16 }}>▾</Text>
                  </View>
                </Pressable>
              </View>
              {selectedLobs.includes('Other') && (
                <View style={{ backgroundColor: theme.color.accent, borderRadius: 12, paddingHorizontal: 10, paddingVertical: 6, borderWidth: 0, borderColor: 'transparent', shadowColor: theme.color.primary, shadowOpacity: focusSpecifyProducts ? 0.12 : 0, shadowRadius: 10, shadowOffset: { width: 0, height: 6 }, elevation: 0 }}>
                  <Text style={{ color: theme.color.cardForeground, fontSize: 12, fontWeight: '600', marginBottom: 4 }}>Specify your Services/Products</Text>
                  <TextInput value={specifyProducts} onChangeText={setSpecifyProducts} placeholder="Type your services/products" placeholderTextColor={theme.color.mutedForeground} underlineColorAndroid="transparent" onFocus={() => setFocusSpecifyProducts(true)} onBlur={() => setFocusSpecifyProducts(false)} style={{ color: theme.color.cardForeground, paddingVertical: 2, fontSize: 14, borderWidth: 0, borderColor: 'transparent' }} />
                </View>
              )}

              {/* Company size */}
              <View style={{ backgroundColor: theme.color.accent, borderRadius: 12, paddingHorizontal: 10, paddingVertical: 6, borderWidth: 0, borderColor: 'transparent', shadowColor: theme.color.primary, shadowOpacity: focusEmployees ? 0.12 : 0, shadowRadius: 10, shadowOffset: { width: 0, height: 6 }, elevation: 0 }}>
                <Text style={{ color: theme.color.cardForeground, fontSize: 12, fontWeight: '600', marginBottom: 4 }}>Company size</Text>
                <Pressable
                  ref={sizeAnchorRef}
                  collapsable={false}
                  onPress={openSizeMenu}
                  hitSlop={{ top: 8, bottom: 8, left: 8, right: 8 }}
                >
                  <View style={{ flexDirection: 'row', alignItems: 'center', justifyContent: 'space-between' }}>
                    <Text style={{ color: employees ? theme.color.cardForeground : theme.color.mutedForeground, paddingVertical: 2, fontSize: 14 }}>
                      {employees || 'Select size'}
                    </Text>
                    <Text style={{ color: theme.color.mutedForeground, fontSize: 16 }}>▾</Text>
                  </View>
                </Pressable>
              </View>

              {/* Country */}
              <Pressable
                ref={countryAnchorRef}
                collapsable={false}
                onPress={openCountryMenu}
                hitSlop={{ top: 8, bottom: 8, left: 8, right: 8 }}
                style={{ backgroundColor: theme.color.accent, borderRadius: 12, paddingHorizontal: 10, paddingVertical: 6, borderWidth: 0, borderColor: 'transparent', shadowColor: theme.color.primary, shadowOpacity: focusCountry ? 0.12 : 0, shadowRadius: 10, shadowOffset: { width: 0, height: 6 }, elevation: 0 }}
              >
                <Text style={{ color: theme.color.cardForeground, fontSize: 12, fontWeight: '600', marginBottom: 4 }}>Country</Text>
                <View style={{ flexDirection: 'row', alignItems: 'center', justifyContent: 'space-between' }}>
                  <View style={{ flexDirection: 'row', alignItems: 'center' }}>
                    {country ? (
                      <Text style={{ fontSize: 16, marginRight: 6 }}>
                        {(countryOptions.find(o => o.value === country)?.flag) || ''}
                      </Text>
                    ) : null}
                    <Text style={{ color: country ? theme.color.cardForeground : theme.color.mutedForeground, paddingVertical: 2, fontSize: 14 }}>
                      {country || 'Select country'}
                    </Text>
                  </View>
                  <Text style={{ color: theme.color.mutedForeground, fontSize: 16 }}>▾</Text>
                </View>
              </Pressable>

              {/* Website */}
              <View style={{ backgroundColor: theme.color.accent, borderRadius: 12, paddingHorizontal: 10, paddingVertical: 6, borderWidth: 0, borderColor: 'transparent', shadowColor: theme.color.primary, shadowOpacity: focusWebsite ? 0.12 : 0, shadowRadius: 10, shadowOffset: { width: 0, height: 6 }, elevation: 0 }}>
                <Text style={{ color: theme.color.cardForeground, fontSize: 12, fontWeight: '600', marginBottom: 4 }}>Website <Text style={{ color: theme.color.mutedForeground }}>(optional)</Text></Text>
                <TextInput value={website} onChangeText={setWebsite} placeholder="https://example.com" autoCapitalize="none" keyboardType="url" placeholderTextColor={theme.color.mutedForeground} underlineColorAndroid="transparent" onFocus={() => setFocusWebsite(true)} onBlur={() => setFocusWebsite(false)} style={{ color: theme.color.cardForeground, paddingVertical: 2, fontSize: 14, borderWidth: 0, borderColor: 'transparent' }} />
              </View>
            </View>

            <View style={{ marginTop: 12 }}>
              <Button title="Next" size="lg" variant="hero" onPress={() => {}} />
            </View>

            <TouchableOpacity onPress={handleBack} style={{ alignSelf: 'center', marginTop: 20 }}>
          <Text style={{ color: theme.color.mutedForeground, fontWeight: '600' }}>Back</Text>
        </TouchableOpacity>

            {/* Industry Modal */}
            <Modal
              visible={showIndustryModal}
              transparent
              animationType="fade"
              onRequestClose={() => { setShowIndustryModal(false); setFocusIndustry(false) }}
            >
              <Pressable
                style={{ flex: 1, backgroundColor: 'rgba(0,0,0,0.25)' }}
                onPress={() => { setShowIndustryModal(false); setFocusIndustry(false) }}
              >
                {industryMenuPos ? (
                  (() => { const plc = getMenuPlacement(industryMenuPos); return (
                  <View style={{ position: 'absolute', left: industryMenuPos.x, top: plc.top, width: industryMenuPos.width }}>
                    <View onLayout={(e) => setIndustryContainerH(e.nativeEvent.layout.height)} style={{ backgroundColor: theme.color.card, borderRadius: 12, borderWidth: 0, borderColor: 'transparent', maxHeight: plc.maxHeight, shadowColor: '#000', shadowOpacity: 0.2, shadowRadius: 16, shadowOffset: { width: 0, height: 10 }, elevation: 10, overflow: 'hidden' }}>
                      <View style={{ paddingVertical: 8, paddingHorizontal: 12 }}>
                        <Text style={{ color: theme.color.cardForeground, fontSize: 14, fontWeight: '700', marginBottom: 6 }}>Select industry</Text>
                        <View style={{ position: 'relative', paddingBottom: 8 }}>
                          <ScrollView
                            showsVerticalScrollIndicator={false}
                            contentContainerStyle={{ paddingBottom: 20 }}
                            contentInset={{ bottom: Platform.OS === 'ios' ? 10 : 0 }}
                            scrollIndicatorInsets={{ bottom: 8 }}
                            keyboardShouldPersistTaps="handled"
                            onContentSizeChange={(w, h) => setIndustryContentH(h)}
                            onScroll={(e) => setIndustryScrollY(e.nativeEvent.contentOffset.y)}
                            scrollEventThrottle={16}
                          >
                            {industryOptions.map((opt) => (
                              <TouchableOpacity
                                key={opt}
                                onPress={() => { setIndustry(opt); setShowIndustryModal(false); setFocusIndustry(false) }}
                                style={{ paddingVertical: 8, paddingHorizontal: 8, borderRadius: 8, backgroundColor: opt === industry ? theme.color.accent : 'transparent', marginBottom: 3 }}
                                activeOpacity={0.8}
                              >
                                <Text numberOfLines={1} ellipsizeMode="tail" style={{ color: theme.color.cardForeground, fontSize: 14 }}>{opt}</Text>
                              </TouchableOpacity>
                            ))}
                          </ScrollView>
                          {/* scrollbar removed intentionally */}
                        </View>
                      </View>
                    </View>
                  </View>
                  ) })()
                ) : null}
              </Pressable>
            </Modal>

            {/* Country Modal */}
            <Modal
              visible={showCountryModal}
              transparent
              animationType="fade"
              onRequestClose={() => { setShowCountryModal(false); setFocusCountry(false) }}
            >
              <Pressable
                style={{ flex: 1, backgroundColor: 'rgba(0,0,0,0.25)' }}
                onPress={() => { setShowCountryModal(false); setFocusCountry(false) }}
              >
                {countryMenuPos ? (
                  (() => { const plc = getMenuPlacement(countryMenuPos); return (
                  <View style={{ position: 'absolute', left: countryMenuPos.x, top: plc.top, width: countryMenuPos.width }}>
                    <View onLayout={(e) => setCountryContainerH(e.nativeEvent.layout.height)} style={{ backgroundColor: theme.color.card, borderRadius: 12, borderWidth: 0, borderColor: 'transparent', maxHeight: plc.maxHeight, shadowColor: '#000', shadowOpacity: 0.2, shadowRadius: 16, shadowOffset: { width: 0, height: 10 }, elevation: 10, overflow: 'hidden' }}>
                      <View style={{ paddingVertical: 8, paddingHorizontal: 12 }}>
                        <Text style={{ color: theme.color.cardForeground, fontSize: 14, fontWeight: '700', marginBottom: 6 }}>Select country</Text>
                        <View style={{ position: 'relative', paddingBottom: 8 }}>
                          <ScrollView
                            showsVerticalScrollIndicator={false}
                            contentContainerStyle={{ paddingBottom: 20 }}
                            contentInset={{ bottom: Platform.OS === 'ios' ? 10 : 0 }}
                            scrollIndicatorInsets={{ bottom: 8 }}
                            keyboardShouldPersistTaps="handled"
                            onContentSizeChange={(w, h) => setCountryContentH(h)}
                            onScroll={(e) => setCountryScrollY(e.nativeEvent.contentOffset.y)}
                            scrollEventThrottle={16}
                          >
                            {countryOptions.map((opt) => (
                  <TouchableOpacity
                                key={opt.value}
                                onPress={() => { setCountry(opt.value); setShowCountryModal(false); setFocusCountry(false) }}
                                style={{ paddingVertical: 8, paddingHorizontal: 8, borderRadius: 8, backgroundColor: opt.value === country ? theme.color.accent : 'transparent', marginBottom: 3, flexDirection: 'row', alignItems: 'center', gap: 8 }}
                    activeOpacity={0.8}
                  >
                                <Text style={{ fontSize: 16 }}>{opt.flag}</Text>
                                <Text numberOfLines={1} ellipsizeMode="tail" style={{ color: theme.color.cardForeground, fontSize: 14, flexShrink: 1 }}>{opt.value}</Text>
                  </TouchableOpacity>
                ))}
              </ScrollView>
                          {/* scrollbar removed intentionally */}
                        </View>
                      </View>
            </View>
          </View>
                  ) })()
                ) : null}
              </Pressable>
        </Modal>

            {/* Size Modal */}
            <Modal
              visible={showSizeModal}
              transparent
              animationType="fade"
              onRequestClose={() => { setShowSizeModal(false); setFocusEmployees(false) }}
            >
              <Pressable
                style={{ flex: 1, backgroundColor: 'rgba(0,0,0,0.25)' }}
                onPress={() => { setShowSizeModal(false); setFocusEmployees(false) }}
              >
                {sizeMenuPos ? (
                  (() => { const plc = getMenuPlacement(sizeMenuPos); return (
                  <View style={{ position: 'absolute', left: sizeMenuPos.x, top: plc.top, width: sizeMenuPos.width }}>
                    <View onLayout={(e) => setSizeContainerH(e.nativeEvent.layout.height)} style={{ backgroundColor: theme.color.card, borderRadius: 12, borderWidth: 0, borderColor: 'transparent', maxHeight: plc.maxHeight, shadowColor: '#000', shadowOpacity: 0.2, shadowRadius: 16, shadowOffset: { width: 0, height: 10 }, elevation: 10, overflow: 'hidden' }}>
                      <View style={{ paddingVertical: 8, paddingHorizontal: 12 }}>
                        <Text style={{ color: theme.color.cardForeground, fontSize: 14, fontWeight: '700', marginBottom: 6 }}>Select company size</Text>
                        <View style={{ position: 'relative' }}>
                          <ScrollView
                            showsVerticalScrollIndicator={false}
                            onContentSizeChange={(w, h) => setSizeContentH(h)}
                            onScroll={(e) => setSizeScrollY(e.nativeEvent.contentOffset.y)}
                            scrollEventThrottle={16}
                          >
                            {sizeOptions.map((opt) => (
                    <TouchableOpacity
                                key={opt}
                                onPress={() => { setEmployees(opt); setShowSizeModal(false); setFocusEmployees(false) }}
                                style={{ paddingVertical: 8, paddingHorizontal: 8, borderRadius: 8, backgroundColor: opt === employees ? theme.color.accent : 'transparent', marginBottom: 3 }}
                      activeOpacity={0.8}
                    >
                                <Text numberOfLines={1} ellipsizeMode="tail" style={{ color: theme.color.cardForeground, fontSize: 14 }}>{opt}</Text>
                    </TouchableOpacity>
                  ))}
                </ScrollView>
                          {/* scrollbar removed intentionally */}
                        </View>
                      </View>
                    </View>
                  </View>
                  ) })()
                ) : null}
              </Pressable>
            </Modal>

            {/* LOB Modal (match industry/country modal styling) */}
            <Modal
              visible={showLobModal}
              transparent
              animationType="fade"
              onRequestClose={() => { setShowLobModal(false); setFocusLob(false) }}
            >
              <Pressable
                style={{ flex: 1, backgroundColor: 'rgba(0,0,0,0.25)' }}
                onPress={() => { setShowLobModal(false); setFocusLob(false) }}
              >
                {lobMenuPos ? (
                  (() => { const plc = getMenuPlacement(lobMenuPos); const options = getLobOptions(industry); return (
                  <View style={{ position: 'absolute', left: lobMenuPos.x, top: plc.top, width: lobMenuPos.width }}>
                    <View onLayout={(e) => setLobContainerH(e.nativeEvent.layout.height)} style={{ backgroundColor: theme.color.card, borderRadius: 12, borderWidth: 0, borderColor: 'transparent', maxHeight: plc.maxHeight, shadowColor: '#000', shadowOpacity: 0.2, shadowRadius: 16, shadowOffset: { width: 0, height: 10 }, elevation: 10, overflow: 'hidden' }}>
                      <View style={{ paddingVertical: 8, paddingHorizontal: 12 }}>
                        <Text style={{ color: theme.color.cardForeground, fontSize: 14, fontWeight: '700', marginBottom: 6 }}>Line of Products/Services</Text>
                        <View style={{ position: 'relative', paddingBottom: 8 }}>
                          <ScrollView
                            showsVerticalScrollIndicator={false}
                            contentContainerStyle={{ paddingBottom: 20 }}
                            contentInset={{ bottom: Platform.OS === 'ios' ? 10 : 0 }}
                            scrollIndicatorInsets={{ bottom: 8 }}
                            keyboardShouldPersistTaps="handled"
                            onContentSizeChange={(w, h) => setLobContentH(h)}
                            onScroll={(e) => setLobScrollY(e.nativeEvent.contentOffset.y)}
                            scrollEventThrottle={16}
                          >
                            {options.map((opt) => {
                              const selected = selectedLobs.includes(opt)
                              return (
                                <TouchableOpacity
                                  key={opt}
                                  onPress={() => {
                                    setSelectedLobs((prev) => selected ? prev.filter(x => x !== opt) : [...prev, opt])
                                  }}
                                  style={{ paddingVertical: 8, paddingHorizontal: 8, borderRadius: 8, backgroundColor: 'transparent', marginBottom: 3, flexDirection: 'row', alignItems: 'center', justifyContent: 'space-between' }}
                                  activeOpacity={0.8}
                                >
                                  <Text numberOfLines={1} ellipsizeMode="tail" style={{ color: theme.color.cardForeground, fontSize: 14, flex: 1, marginRight: 8 }}>{opt}</Text>
                                  <View style={{ width: 18, alignItems: 'flex-end' }}>
                                    {selected ? (
                                      <Text style={{ color: theme.color.primary, fontWeight: '700' }}>✓</Text>
                                    ) : null}
                                  </View>
                                </TouchableOpacity>
                              )
                            })}
                          </ScrollView>
                          {/* scrollbar removed intentionally */}
                        </View>
                      </View>
                    </View>
              </View>
                  ) })()
            ) : null}
              </Pressable>
            </Modal>
          </View>
        )}
        </ScrollView>
      </View>
      </KeyboardAvoidingView>
    </SafeAreaView>
  )
}
