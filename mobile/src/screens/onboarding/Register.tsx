import React, { useRef, useState, useEffect } from 'react'
import { SafeAreaView, View, Text, TextInput, TouchableOpacity, Modal, ScrollView, Pressable, KeyboardAvoidingView, Platform, Dimensions, Animated, Easing, LayoutAnimation, UIManager } from 'react-native'
import Svg, { Path } from 'react-native-svg'
import { LinearGradient } from 'expo-linear-gradient'
import { Paperclip } from 'lucide-react-native'
import { useTheme } from '../../providers/ThemeProvider'
import { Button } from '../../components/ui/Button'
import * as Localization from 'expo-localization'

export const RegisterScreen: React.FC<{ onBack: () => void, onLogin?: () => void, onComplete?: () => void }> = ({ onBack, onLogin, onComplete }) => {
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

  // Flow step: 'form' | 'role' | 'business' | 'agent' | 'uploads'
  const [step, setStep] = useState<'form' | 'role' | 'business' | 'agent' | 'uploads'>('form')

  // Business form state
  const [company, setCompany] = useState('')
  const [industry, setIndustry] = useState('')
  const [industryQuery, setIndustryQuery] = useState('')
  const [industryCustomInput, setIndustryCustomInput] = useState('')
  const [website, setWebsite] = useState('')
  const [focusCompany, setFocusCompany] = useState(false)
  const [focusIndustry, setFocusIndustry] = useState(false)
  
  const [focusWebsite, setFocusWebsite] = useState(false)
  const [showIndustryModal, setShowIndustryModal] = useState(false)
  // Agent setup state
  const [agentName, setAgentName] = useState('')
  const [desiredTitle, setDesiredTitle] = useState('')
  const [titleCustomInput, setTitleCustomInput] = useState('')
  const [agentTone, setAgentTone] = useState('')
  const [selectedTraits, setSelectedTraits] = useState<string[]>([])
  const [escalationRule, setEscalationRule] = useState('')
  const [focusAgentName, setFocusAgentName] = useState(false)
  const [focusTitle, setFocusTitle] = useState(false)
  const [focusTone, setFocusTone] = useState(false)
  const [focusTraits, setFocusTraits] = useState(false)
  const [focusEscalation, setFocusEscalation] = useState(false)
  const [showTitleModal, setShowTitleModal] = useState(false)
  const [showToneModal, setShowToneModal] = useState(false)
  const [showTraitsModal, setShowTraitsModal] = useState(false)
  const [showEscalationModal, setShowEscalationModal] = useState(false)

  // Tooltip state (shared)
  const [showTooltip, setShowTooltip] = useState(false)
  const [tooltipText, setTooltipText] = useState('')
  const [tooltipMenuPos, setTooltipMenuPos] = useState<{ x: number, y: number, width: number, height: number } | null>(null)
  
  // Uploads step state
  const [visionFiles, setVisionFiles] = useState<string[]>([])
  const [missionFiles, setMissionFiles] = useState<string[]>([])
  const [catalogFiles, setCatalogFiles] = useState<string[]>([])
  const [faqsFiles, setFaqsFiles] = useState<string[]>([])
  const [kbFiles, setKbFiles] = useState<string[]>([])
  const [sopsFiles, setSopsFiles] = useState<string[]>([])
  const [tcFiles, setTcFiles] = useState<string[]>([])
  
  // URL inputs for each upload type
  const [visionUrl, setVisionUrl] = useState('')
  const [missionUrl, setMissionUrl] = useState('')
  const [catalogUrl, setCatalogUrl] = useState('')
  const [faqsUrl, setFaqsUrl] = useState('')
  const [kbUrl, setKbUrl] = useState('')
  const [sopsUrl, setSopsUrl] = useState('')
  const [tcUrl, setTcUrl] = useState('')
  const catalogEntity = 'Products & Services'
  const uploadOptions = [
    { key: 'vision', label: 'Vision' },
    { key: 'mission', label: 'Mission' },
    { key: 'catalog', label: `${catalogEntity} Catalog` },
    { key: 'faqs', label: 'FAQs' },
    { key: 'kb', label: 'Knowledge Base' },
    { key: 'sops', label: 'SOPs' },
    { key: 'tc', label: 'T&C' },
  ]
  const [selectedUploadTypes, setSelectedUploadTypes] = useState<string[]>([])
  
  const attachUrl = (value: string, setList: React.Dispatch<React.SetStateAction<string[]>>, clear: () => void) => {
    const v = (value || '').trim()
    if (!v) return
    // naive URL validation
    const looksUrl = /^(https?:\/\/|www\.)/i.test(v)
    setList(prev => prev.includes(v) ? prev : [...prev, v])
    if (looksUrl) clear()
  }
  
  // Updated to mirror web Register industry categories
  const industryOptions = [
    'E‑commerce & Retail',
    'SaaS & Software',
    'Financial Services',
    'Healthcare & Life Sciences',
    'Education',
    'Hospitality & Travel',
    'Manufacturing',
    'Logistics & Transportation',
    'Real Estate',
    'Media & Entertainment',
    'Telecommunications',
    'Energy & Utilities',
    'Nonprofit & NGOs',
    'Professional Services',
    'Consumer Services',
  ]

  // Map industry label to LoB key (mirror web)
  const mapIndustryToLobKey = (label?: string) => {
    if (!label) return 'Other'
    const s = label.toLowerCase()
    if (s.includes('e‑commerce') || s.includes('e-commerce') || s.includes('retail')) return 'E‑commerce'
    if (s.includes('saas') || s.includes('software')) return 'SaaS'
    if (s.includes('financial')) return 'Finance'
    if (s.includes('health')) return 'Healthcare'
    if (s.includes('education')) return 'Education'
    if (s.includes('hospitality') || s.includes('travel')) return 'Hospitality'
    if (s.includes('manufactur')) return 'Manufacturing'
    if (s.includes('logistics') || s.includes('transport')) return 'Logistics'
    if (s.includes('real estate')) return 'Real Estate'
    if (s.includes('media') || s.includes('entertainment')) return 'Media & Entertainment'
    if (s.includes('telecom')) return 'Telecommunications'
    if (s.includes('energy') || s.includes('utilit')) return 'Energy & Utilities'
    if (s.includes('nonprofit') || s.includes('ngo')) return 'Nonprofit & NGOs'
    if (s.includes('professional')) return 'Professional Services'
    if (s.includes('consumer')) return 'Consumer Services'
    return 'Other'
  }
  const lobOptionsByKey: Record<string, string[]> = {
    'E‑commerce': [
      'Apparel',
      'Electronics',
      'Beauty & Personal Care',
      'Home & Kitchen',
      'Sports & Outdoors',
      'Groceries',
      'Digital Goods',
      'Handmade & Crafts',
      'Automotive Accessories',
    ],
    'SaaS': [
      'CRM',
      'Marketing Automation',
      'Analytics',
      'Project Management',
      'Customer Support',
      'Developer Tools',
      'Productivity',
      'Security',
      'Billing/Subscriptions',
      'Auth/Identity',
      'Observability',
      'Data Platform',
    ],
    'Finance': [
      'Banking',
      'Lending',
      'Payments',
      'Wealth Management',
      'Insurance',
      'Accounting',
      'Crypto/Blockchain',
      'Trading Platforms',
    ],
    'Healthcare': [
      'Clinics',
      'Telemedicine',
      'Pharmacy',
      'Diagnostics',
      'Medical Devices',
      'Wellness',
      'Electronic Health Records',
    ],
    'Education': [
      'K‑12',
      'Higher Education',
      'EdTech Platform',
      'Corporate Training',
      'Test Prep',
      'Language Learning',
      'Tutoring & Coaching',
    ],
    'Hospitality': [
      'Hotels',
      'Restaurants',
      'Catering',
      'Travel & Tours',
      'Venues & Events',
      'Short‑Term Rentals',
    ],
    'Manufacturing': [
      'OEM Production',
      'Contract Manufacturing',
      'CNC Machining',
      'Injection Molding',
      '3D Printing',
      'PCB Assembly',
      'Quality Assurance',
      'Procurement & Supply',
      'Packaging',
      'Maintenance (MRO)',
    ],
    'Logistics': [
      'Freight Forwarding',
      'Last‑Mile Delivery',
      'Warehousing & Fulfillment',
      'Cold Chain',
      'Customs Brokerage',
      'Fleet Management',
      'Courier',
      'LTL/FTL Trucking',
      'Air Cargo',
      'Ocean Freight',
    ],
    'Real Estate': [
      'Residential Sales',
      'Commercial Leasing',
      'Property Management',
      'Valuation & Appraisal',
      'Real Estate Development',
      'Facility Management',
      'Co‑working',
      'Mortgage Brokerage',
      'Title & Escrow',
      'Short‑Term Rentals',
    ],
    'Media & Entertainment': [
      'Streaming Subscriptions',
      'OTT Platform',
      'Content Production',
      'Post‑Production',
      'Music Publishing',
      'Game Development',
      'Live Events',
      'Digital Advertising',
      'Influencer Campaigns',
      'Licensing & Syndication',
    ],
    'Telecommunications': [
      'Mobile Voice',
      'Fixed Broadband',
      'VoIP',
      'IoT Connectivity',
      'Cloud PBX',
      'SIP Trunking',
      'Managed Networks',
      '5G Solutions',
      'Fiber to the Home',
      'Data Center Colocation',
    ],
    'Energy & Utilities': [
      'Electricity Supply',
      'Natural Gas Supply',
      'Renewable Generation',
      'Solar Installation',
      'Energy Storage',
      'Smart Metering',
      'Demand Response',
      'Energy Trading',
      'EV Charging',
      'Utility Billing',
    ],
    'Nonprofit & NGOs': [
      'Fundraising',
      'Grant Management',
      'Program Delivery',
      'Volunteer Management',
      'Advocacy & Outreach',
      'Education Programs',
      'Healthcare Missions',
      'Disaster Relief',
      'Community Development',
      'Monitoring & Evaluation',
    ],
    'Professional Services': [
      'Consulting',
      'Legal Advisory',
      'Tax & Audit',
      'Accounting',
      'Architecture',
      'Engineering',
      'Design & Creative',
      'Recruitment',
      'IT Consulting',
      'Managed IT',
    ],
    'Consumer Services': [
      'Home Cleaning',
      'Appliance Repair',
      'Beauty & Wellness',
      'Fitness & Training',
      'Tutoring',
      'Pet Care',
      'Event Planning',
      'Photography',
      'Home Renovation',
      'Moving & Storage',
    ],
    'Other': [
      'Consulting',
      'Custom Development',
      'Training & Enablement',
      'Support & Success',
    ],
  }
  const getLobOptions = (label: string) => {
    const key = mapIndustryToLobKey(label)
    const base = lobOptionsByKey[key] || lobOptionsByKey['Other']
    return base.filter(o => !o.toLowerCase().includes('services'))
  }

  const [selectedLobs, setSelectedLobs] = useState<string[]>([])
  const [customLobs, setCustomLobs] = useState<string[]>([])
  const [lobQuery, setLobQuery] = useState('')
  const [lobCustomInput, setLobCustomInput] = useState('')
  const [showLobModal, setShowLobModal] = useState(false)
  const [focusLob, setFocusLob] = useState(false)
  const lobAnchorRef = useRef<any>(null)
  const [lobMenuPos, setLobMenuPos] = useState<{ x: number, y: number, width: number, height: number } | null>(null)
  const [lobContentH, setLobContentH] = useState(0)
  const [lobContainerH, setLobContainerH] = useState(0)
  const [lobScrollY, setLobScrollY] = useState(0)

  useEffect(() => {
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

  // Agent options
  const titleOptions = [
    'Customer Support Agent',
    'Support Specialist',
    'Customer Success Representative',
    'Sales Support Agent',
    'Front Desk Representative',
    'Account Manager',
    'Helpdesk Agent',
  ]
  const toneOptions = [
    'Friendly',
    'Professional',
    'Empathetic',
    'Casual',
    'Formal',
    'Concise',
  ]
  const traitOptions = [
    'Detail-oriented',
    'Talkative',
    'Proactive',
    'Patient',
    'Analytical',
    'Persuasive',
  ]
  const escalationOptions = [
    'If customer asks for a human',
    'If sentiment is negative',
    'If more than 2 failed attempts',
    'If order/account is high-value',
    'If compliance/security issue detected',
  ]

  // Anchors for dropdown positioning
  const industryAnchorRef = useRef<any>(null)
  const [industryMenuPos, setIndustryMenuPos] = useState<{ x: number, y: number, width: number, height: number } | null>(null)
  const countryAnchorRef = useRef<any>(null)
  const [countryMenuPos, setCountryMenuPos] = useState<{ x: number, y: number, width: number, height: number } | null>(null)
  // Agent anchors
  const titleAnchorRef = useRef<any>(null)
  const [titleMenuPos, setTitleMenuPos] = useState<{ x: number, y: number, width: number, height: number } | null>(null)
  const toneAnchorRef = useRef<any>(null)
  const [toneMenuPos, setToneMenuPos] = useState<{ x: number, y: number, width: number, height: number } | null>(null)
  const traitsAnchorRef = useRef<any>(null)
  const [traitsMenuPos, setTraitsMenuPos] = useState<{ x: number, y: number, width: number, height: number } | null>(null)
  const escalationAnchorRef = useRef<any>(null)
  const [escalationMenuPos, setEscalationMenuPos] = useState<{ x: number, y: number, width: number, height: number } | null>(null)
  const tooltipAnchorRef = useRef<any>(null)
  const escalationTipRef = useRef<any>(null)
  

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
  // Agent dropdown metrics
  const [titleContentH, setTitleContentH] = useState(0)
  const [titleContainerH, setTitleContainerH] = useState(0)
  const [titleScrollY, setTitleScrollY] = useState(0)
  const [toneContentH, setToneContentH] = useState(0)
  const [toneContainerH, setToneContainerH] = useState(0)
  const [toneScrollY, setToneScrollY] = useState(0)
  const [traitsContentH, setTraitsContentH] = useState(0)
  const [traitsContainerH, setTraitsContainerH] = useState(0)
  const [traitsScrollY, setTraitsScrollY] = useState(0)
  const [escalationContentH, setEscalationContentH] = useState(0)
  const [escalationContainerH, setEscalationContainerH] = useState(0)
  const [escalationScrollY, setEscalationScrollY] = useState(0)

  // Enable LayoutAnimation on Android
  useEffect(() => {
    if (Platform.OS === 'android' && UIManager && (UIManager as any).setLayoutAnimationEnabledExperimental) {
      (UIManager as any).setLayoutAnimationEnabledExperimental(true)
    }
  }, [])

  // (Removed Other industry branch to mirror web UX)

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
  const AGENT_SUB = 'Configure your AI agent'
  const UPLOADS_SUB = 'Upload your materials'
  const targetSub = step === 'form' ? FORM_SUB : step === 'role' ? ROLE_SUB : step === 'business' ? BUSINESS_SUB : step === 'agent' ? AGENT_SUB : UPLOADS_SUB
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
    if (step === 'uploads') {
      LayoutAnimation.configureNext(LayoutAnimation.Presets.easeInEaseOut)
      setStep('agent')
      return
    }
    if (step === 'agent') {
      LayoutAnimation.configureNext(LayoutAnimation.Presets.easeInEaseOut)
      setStep('business')
      return
    }
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

  const headerTitle = step === 'form' ? 'Create your account' : step === 'role' ? 'Tell us more about you' : step === 'business' ? 'Business profile setup' : step === 'agent' ? 'Agent setup' : 'Uploads'

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
  const openTitleMenu = () => {
    setFocusTitle(true)
    requestAnimationFrame(() => {
      try {
        titleAnchorRef.current?.measureInWindow?.((x: number, y: number, width: number, height: number) => {
          setTitleMenuPos({ x, y, width, height })
          setShowTitleModal(true)
        })
      } catch {
        setShowTitleModal(true)
      }
    })
  }
  const openToneMenu = () => {
    setFocusTone(true)
    requestAnimationFrame(() => {
      try {
        toneAnchorRef.current?.measureInWindow?.((x: number, y: number, width: number, height: number) => {
          setToneMenuPos({ x, y, width, height })
          setShowToneModal(true)
        })
      } catch {
        setShowToneModal(true)
      }
    })
  }
  const openTraitsMenu = () => {
    setFocusTraits(true)
    requestAnimationFrame(() => {
      try {
        traitsAnchorRef.current?.measureInWindow?.((x: number, y: number, width: number, height: number) => {
          setTraitsMenuPos({ x, y, width, height })
          setShowTraitsModal(true)
        })
      } catch {
        setShowTraitsModal(true)
      }
    })
  }
  const openEscalationMenu = () => {
    setFocusEscalation(true)
    requestAnimationFrame(() => {
      try {
        escalationAnchorRef.current?.measureInWindow?.((x: number, y: number, width: number, height: number) => {
          setEscalationMenuPos({ x, y, width, height })
          setShowEscalationModal(true)
        })
      } catch {
        setShowEscalationModal(true)
      }
    })
  }
  
  const ignoreNextLobOpenRef = useRef(false)
  const openLobMenu = () => {
    if (ignoreNextLobOpenRef.current) { ignoreNextLobOpenRef.current = false; return }
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
  const openTooltip = (targetRef: any, text: string) => {
    setTooltipText(text)
    requestAnimationFrame(() => {
      try {
        targetRef?.current?.measureInWindow?.((x: number, y: number, width: number, height: number) => {
          setTooltipMenuPos({ x, y, width, height })
          setShowTooltip(true)
        })
      } catch {
        setShowTooltip(true)
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

  const UploadInputRow: React.FC<{ 
    url: string; 
    onUrlChange: (url: string) => void; 
    onAttach: () => void; 
    files: string[]; 
    theme: any 
  }> = ({ url, onUrlChange, onAttach, files, theme }) => (
    <View style={{ gap: 10 }}>
      <View style={{ flexDirection: 'row', gap: 10, alignItems: 'center' }}>
        <View style={{
          flex: 1,
          backgroundColor: theme.dark ? theme.color.accent : theme.color.card,
          borderRadius: 12,
          paddingHorizontal: 12,
          paddingVertical: 10,
          borderWidth: 1,
          borderColor: theme.color.border
        }}>
          <TextInput
            value={url}
            onChangeText={onUrlChange}
            placeholder="Insert URL here..."
            placeholderTextColor={theme.color.placeholder}
            style={{ color: theme.color.cardForeground, fontSize: 15, paddingVertical: 4 }}
            autoCapitalize="none"
            keyboardType="url"
            underlineColorAndroid="transparent"
          />
        </View>
        <TouchableOpacity
          activeOpacity={0.85}
          onPress={onAttach}
          accessibilityRole="button"
          accessibilityLabel="Attach URL"
          style={{ 
            width: 48,
            height: 48,
            borderRadius: 12,
            borderWidth: 0,
            borderColor: 'transparent',
            backgroundColor: theme.color.primary,
            alignItems: 'center',
            justifyContent: 'center'
          }}
        >
          <Paperclip size={18} color={'#fff' as any} />
        </TouchableOpacity>
      </View>
      {files && files.length > 0 ? (
        <View style={{ gap: 6 }}>
          {files.map((f, i) => (
            <View key={i} style={{ backgroundColor: theme.dark ? theme.color.secondary : theme.color.card, borderRadius: 10, paddingHorizontal: 12, paddingVertical: 8, borderWidth: 1, borderColor: theme.color.border }}>
              <Text numberOfLines={1} ellipsizeMode="tail" style={{ color: theme.color.mutedForeground, fontSize: 13 }}>{f}</Text>
            </View>
          ))}
        </View>
      ) : null}
    </View>
  )

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
          <Text style={{ color: theme.color.foreground, fontSize: 26, fontWeight: '700' }}>{headerTitle}</Text>
          <Text style={{ color: theme.color.mutedForeground, marginTop: 4, fontSize: 14 }}>{typed}</Text>
        </View>

        {step === 'form' ? (
          <>
            <View style={{ gap: 8 }}>
              <View style={{ backgroundColor: theme.color.accent, borderRadius: 12, paddingHorizontal: 12, paddingVertical: 10, borderWidth: 0, borderColor: 'transparent', shadowColor: theme.color.primary, shadowOpacity: focusFirst ? 0.12 : 0, shadowRadius: 10, shadowOffset: { width: 0, height: 6 }, elevation: 0 }}>
                <Text style={{ color: theme.color.cardForeground, fontSize: 15, fontWeight: '700', marginBottom: 6 }}>First name</Text>
                <TextInput value={firstName} onChangeText={setFirstName} placeholder="John" placeholderTextColor={theme.color.mutedForeground} underlineColorAndroid="transparent" onFocus={() => setFocusFirst(true)} onBlur={() => setFocusFirst(false)} style={{ color: theme.color.cardForeground, paddingVertical: 4, fontSize: 15, borderWidth: 0, borderColor: 'transparent' }} />
              </View>
              <View style={{ backgroundColor: theme.color.accent, borderRadius: 12, paddingHorizontal: 12, paddingVertical: 10, borderWidth: 0, borderColor: 'transparent', shadowColor: theme.color.primary, shadowOpacity: focusEmail ? 0.12 : 0, shadowRadius: 10, shadowOffset: { width: 0, height: 6 }, elevation: 0 }}>
                <Text style={{ color: theme.color.cardForeground, fontSize: 15, fontWeight: '700', marginBottom: 6 }}>Email</Text>
                <TextInput value={email} onChangeText={setEmail} placeholder="you@company.com" keyboardType="email-address" placeholderTextColor={theme.color.mutedForeground} underlineColorAndroid="transparent" onFocus={() => setFocusEmail(true)} onBlur={() => setFocusEmail(false)} style={{ color: theme.color.cardForeground, paddingVertical: 4, fontSize: 15, borderWidth: 0, borderColor: 'transparent' }} />
              </View>
              <View style={{ backgroundColor: theme.color.accent, borderRadius: 12, paddingHorizontal: 12, paddingVertical: 10, borderWidth: 0, borderColor: 'transparent', shadowColor: theme.color.primary, shadowOpacity: focusPass ? 0.12 : 0, shadowRadius: 10, shadowOffset: { width: 0, height: 6 }, elevation: 0 }}>
                <Text style={{ color: theme.color.cardForeground, fontSize: 15, fontWeight: '700', marginBottom: 6 }}>Password</Text>
                <TextInput value={password} onChangeText={setPassword} placeholder="••••••••" secureTextEntry autoComplete="off" textContentType="none" importantForAutofill="no" placeholderTextColor={theme.color.mutedForeground} underlineColorAndroid="transparent" onFocus={() => setFocusPass(true)} onBlur={() => setFocusPass(false)} style={{ color: theme.color.cardForeground, paddingVertical: 4, fontSize: 15, borderWidth: 0, borderColor: 'transparent' }} />
              </View>
              <View style={{ backgroundColor: theme.color.accent, borderRadius: 12, paddingHorizontal: 12, paddingVertical: 10, borderWidth: 0, borderColor: 'transparent', shadowColor: theme.color.primary, shadowOpacity: focusConfirm ? 0.12 : 0, shadowRadius: 10, shadowOffset: { width: 0, height: 6 }, elevation: 0 }}>
                <Text style={{ color: theme.color.cardForeground, fontSize: 15, fontWeight: '700', marginBottom: 6 }}>Confirm password</Text>
                <TextInput value={confirm} onChangeText={setConfirm} placeholder="••••••••" secureTextEntry autoComplete="off" textContentType="none" importantForAutofill="no" placeholderTextColor={theme.color.mutedForeground} underlineColorAndroid="transparent" onFocus={() => setFocusConfirm(true)} onBlur={() => setFocusConfirm(false)} style={{ color: theme.color.cardForeground, paddingVertical: 4, fontSize: 15, borderWidth: 0, borderColor: 'transparent' }} />
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
                  <Text style={{ color: '#fff', fontSize: 18, fontWeight: '700', marginBottom: 4, textAlign: 'center', lineHeight: 24 }}>B2C</Text>
                  <Text style={{ color: '#fff', opacity: 0.95, textAlign: 'center', fontSize: 14, lineHeight: 18 }}>Customer Support</Text>
                  <Text style={{ color: '#fff', opacity: 0.9, textAlign: 'center', fontSize: 12, lineHeight: 16, marginTop: 2 }}>handling inquiries, orders, and complaints</Text>
                </LinearGradient>
              </TouchableOpacity>
              <TouchableOpacity activeOpacity={0.9} style={{ overflow: 'hidden', borderRadius: 12, alignSelf: 'center', width: '96%' }}>
                <LinearGradient colors={[theme.color.primary, theme.color.primaryLight]} start={{ x: 0, y: 0 }} end={{ x: 1, y: 1 }} style={{ paddingVertical: 20, paddingHorizontal: 16, borderRadius: 12, alignItems: 'center', justifyContent: 'center' }}>
                  <Text style={{ color: '#fff', fontSize: 18, fontWeight: '700', marginBottom: 4, textAlign: 'center', lineHeight: 24 }}>B2B</Text>
                  <Text style={{ color: '#fff', opacity: 0.95, textAlign: 'center', fontSize: 14, lineHeight: 18 }}>Business Development</Text>
                  <Text style={{ color: '#fff', opacity: 0.9, textAlign: 'center', fontSize: 12, lineHeight: 16, marginTop: 2 }}>managing suppliers, clients, and partners</Text>
                </LinearGradient>
              </TouchableOpacity>
              <TouchableOpacity activeOpacity={0.9} style={{ overflow: 'hidden', borderRadius: 12, alignSelf: 'center', width: '96%' }}>
                <LinearGradient colors={[theme.color.primary, theme.color.primaryLight]} start={{ x: 0, y: 0 }} end={{ x: 1, y: 1 }} style={{ paddingVertical: 20, paddingHorizontal: 16, borderRadius: 12, alignItems: 'center', justifyContent: 'center' }}>
                  <Text style={{ color: '#fff', fontSize: 18, fontWeight: '700', marginBottom: 4, textAlign: 'center', lineHeight: 24 }}>Employee</Text>
                  <Text style={{ color: '#fff', opacity: 0.9, textAlign: 'center', fontSize: 12, lineHeight: 16 }}>for day‑to‑day frontline communication, internal requests, and task handoffs</Text>
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
        ) : step === 'business' ? (
          // Step: business form
          <View style={{ marginTop: 4 }}>
            <View style={{ gap: 8 }}>
              <View style={{ backgroundColor: theme.color.accent, borderRadius: 12, paddingHorizontal: 12, paddingVertical: 10, borderWidth: 0, borderColor: 'transparent', shadowColor: theme.color.primary, shadowOpacity: focusCompany ? 0.12 : 0, shadowRadius: 10, shadowOffset: { width: 0, height: 6 }, elevation: 0 }}>
                <Text style={{ color: theme.color.cardForeground, fontSize: 15, fontWeight: '700', marginBottom: 6 }}>Business name</Text>
                <TextInput value={company} onChangeText={setCompany} placeholder="Acme Inc" placeholderTextColor={theme.color.mutedForeground} underlineColorAndroid="transparent" onFocus={() => setFocusCompany(true)} onBlur={() => setFocusCompany(false)} style={{ color: theme.color.cardForeground, paddingVertical: 4, fontSize: 15, borderWidth: 0, borderColor: 'transparent' }} />
              </View>
              <View style={{ backgroundColor: theme.color.accent, borderRadius: 12, paddingHorizontal: 12, paddingVertical: 10, borderWidth: 0, borderColor: 'transparent', shadowColor: theme.color.primary, shadowOpacity: focusIndustry ? 0.12 : 0, shadowRadius: 10, shadowOffset: { width: 0, height: 6 }, elevation: 0 }}>
                <Text style={{ color: theme.color.cardForeground, fontSize: 15, fontWeight: '700', marginBottom: 6 }}>Industry</Text>
                <Pressable
                  ref={industryAnchorRef}
                  collapsable={false}
                  onPress={openIndustryMenu}
                  hitSlop={{ top: 8, bottom: 8, left: 8, right: 8 }}
                >
                  <View style={{ flexDirection: 'row', alignItems: 'center', justifyContent: 'space-between' }}>
                    <Text style={{ color: industry ? theme.color.cardForeground : theme.color.mutedForeground, paddingVertical: 2, fontSize: 15 }}>
                      {industry || 'Select industry'}
                    </Text>
                    <Text style={{ color: theme.color.mutedForeground, fontSize: 16 }}>▾</Text>
                  </View>
                </Pressable>
              </View>
              {/* 'Other' free-text branch removed to mirror web flow */}

              {/* Products/Services */}
              <View style={{ backgroundColor: theme.color.accent, borderRadius: 12, paddingHorizontal: 12, paddingVertical: 10, borderWidth: 0, borderColor: 'transparent', shadowColor: theme.color.primary, shadowOpacity: focusLob ? 0.12 : 0, shadowRadius: 10, shadowOffset: { width: 0, height: 6 }, elevation: 0 }}>
                <Text style={{ color: theme.color.cardForeground, fontSize: 15, fontWeight: '700', marginBottom: 8 }}>Industry nichees</Text>
                <Pressable
                  ref={lobAnchorRef}
                  collapsable={false}
                  onPress={openLobMenu}
                  hitSlop={{ top: 8, bottom: 8, left: 8, right: 8 }}
                >
                  <View style={{ flexDirection: 'row', alignItems: 'center', justifyContent: 'space-between' }}>
                    <View style={{ flexDirection: 'row', alignItems: 'center', flex: 1, overflow: 'hidden' }}>
                      {(() => {
                        const merged = Array.from(new Set([...(selectedLobs||[]), ...(customLobs||[])]))
                        const visible = merged.slice(0, 2)
                        const remaining = Math.max(0, merged.length - visible.length)
                        if (merged.length === 0) {
                          return (
                            <Text style={{ color: theme.color.mutedForeground, paddingVertical: 2, fontSize: 15, flexShrink: 1 }} numberOfLines={1}>
                              {industry ? 'Select nichees' : 'Select industry first'}
                            </Text>
                          )
                        }
                        return (
                          <View style={{ flexDirection: 'row', alignItems: 'center', gap: 6, flexShrink: 1 }}>
                            {visible.map((opt) => (
                              <TouchableOpacity
                                key={opt}
                                onPress={(e) => { e.stopPropagation?.(); setSelectedLobs(prev => prev.filter(x => x !== opt)) }}
                                activeOpacity={0.85}
                                style={{ backgroundColor: theme.color.primary, borderRadius: 999, paddingLeft: 12, paddingRight: 12, paddingVertical: 8, position: 'relative' }}
                              >
                                <Text style={{ color: '#fff', fontSize: 13, fontWeight: '700' }}>{opt}</Text>
                              </TouchableOpacity>
                            ))}
                            {remaining > 0 && (
                              <View style={{ backgroundColor: theme.color.primary, borderRadius: 999, paddingHorizontal: 12, paddingVertical: 8, shadowColor: 'transparent', shadowOpacity: 0, shadowRadius: 0, shadowOffset: { width: 0, height: 0 }, elevation: 0, borderWidth: 0, borderColor: 'transparent' }}>
                                <Text style={{ color: '#fff', fontSize: 13, fontWeight: '700' }}>+{remaining}</Text>
                              </View>
              )}
                          </View>
                        )
                      })()}
                    </View>
                    <Text style={{ color: theme.color.mutedForeground, fontSize: 16, marginLeft: 8 }}>▾</Text>
                  </View>
                </Pressable>
              </View>
              {/* Replaced free-text Other with custom LoB chips in modal */}


              {/* Country */}
              <Pressable
                ref={countryAnchorRef}
                collapsable={false}
                onPress={openCountryMenu}
                hitSlop={{ top: 8, bottom: 8, left: 8, right: 8 }}
                style={{ backgroundColor: theme.color.accent, borderRadius: 12, paddingHorizontal: 12, paddingVertical: 10, borderWidth: 0, borderColor: 'transparent', shadowColor: theme.color.primary, shadowOpacity: focusCountry ? 0.12 : 0, shadowRadius: 10, shadowOffset: { width: 0, height: 6 }, elevation: 0 }}
              >
                <Text style={{ color: theme.color.cardForeground, fontSize: 15, fontWeight: '700', marginBottom: 6 }}>Country</Text>
                <View style={{ flexDirection: 'row', alignItems: 'center', justifyContent: 'space-between' }}>
                  <View style={{ flexDirection: 'row', alignItems: 'center' }}>
                    {country ? (
                      <Text style={{ fontSize: 16, marginRight: 6 }}>
                        {(countryOptions.find(o => o.value === country)?.flag) || ''}
                      </Text>
                    ) : null}
                    <Text style={{ color: country ? theme.color.cardForeground : theme.color.mutedForeground, paddingVertical: 2, fontSize: 15 }}>
                      {country || 'Select country'}
                    </Text>
                  </View>
                  <Text style={{ color: theme.color.mutedForeground, fontSize: 16 }}>▾</Text>
                </View>
              </Pressable>

              {/* Website */}
              <View style={{ backgroundColor: theme.color.accent, borderRadius: 12, paddingHorizontal: 12, paddingVertical: 10, borderWidth: 0, borderColor: 'transparent', shadowColor: theme.color.primary, shadowOpacity: focusWebsite ? 0.12 : 0, shadowRadius: 10, shadowOffset: { width: 0, height: 6 }, elevation: 0 }}>
                <Text style={{ color: theme.color.cardForeground, fontSize: 15, fontWeight: '700', marginBottom: 6 }}>Website <Text style={{ color: theme.color.mutedForeground, fontWeight: '400' }}>(optional)</Text></Text>
                <TextInput value={website} onChangeText={setWebsite} placeholder="https://example.com" autoCapitalize="none" keyboardType="url" placeholderTextColor={theme.color.mutedForeground} underlineColorAndroid="transparent" onFocus={() => setFocusWebsite(true)} onBlur={() => setFocusWebsite(false)} style={{ color: theme.color.cardForeground, paddingVertical: 4, fontSize: 15, borderWidth: 0, borderColor: 'transparent' }} />
              </View>
            </View>

            <View style={{ marginTop: 12 }}>
              <Button title="Next" size="lg" variant="hero" onPress={() => { LayoutAnimation.configureNext(LayoutAnimation.Presets.easeInEaseOut); setStep('agent') }} />
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
                      <View style={{ paddingTop: 8, paddingBottom: 0, paddingHorizontal: 12 }}>
                        <Text style={{ color: theme.color.cardForeground, fontSize: 14, fontWeight: '700', marginBottom: 6 }}>Select industry</Text>
                        <View style={{ position: 'relative' }}>
                          {/* Search */}
                          <View style={{ marginBottom: 8 }}>
                            <View style={{ backgroundColor: theme.color.accent, borderRadius: 8, paddingHorizontal: 10, paddingVertical: 6 }}>
                              <TextInput
                                value={industryQuery}
                                onChangeText={setIndustryQuery}
                                placeholder="Search industry"
                                placeholderTextColor={theme.color.mutedForeground}
                                style={{ color: theme.color.cardForeground, fontSize: 14, paddingVertical: 2 }}
                              />
                            </View>
                          </View>
                          {/* Add custom */}
                          <View style={{ marginBottom: 8 }}>
                            <View style={{ flexDirection: 'row', alignItems: 'center', gap: 8 }}>
                              <View style={{ flex: 1, backgroundColor: theme.color.accent, borderRadius: 8, paddingHorizontal: 10, paddingVertical: 6 }}>
                                <TextInput
                                  value={industryCustomInput}
                                  onChangeText={setIndustryCustomInput}
                                  placeholder="Add custom"
                                  placeholderTextColor={theme.color.mutedForeground}
                                  style={{ color: theme.color.cardForeground, fontSize: 14, paddingVertical: 2 }}
                                />
                              </View>
                              <TouchableOpacity
                                activeOpacity={0.85}
                                onPress={() => {
                                  const v = industryCustomInput.trim(); if (!v) return;
                                  setIndustry(v);
                                  setIndustryCustomInput('');
                                  setShowIndustryModal(false); setFocusIndustry(false);
                                }}
                                style={{ backgroundColor: theme.color.primary, borderRadius: 8, paddingHorizontal: 12, paddingVertical: 8 }}
                              >
                                <Text style={{ color: '#fff', fontWeight: '600' }}>Add</Text>
                              </TouchableOpacity>
                            </View>
                          </View>
                          {/* List */}
                          <ScrollView
                            showsVerticalScrollIndicator={true}
                            contentContainerStyle={{ paddingBottom: 0 }}
                            keyboardShouldPersistTaps="handled"
                            scrollEventThrottle={16}
                            style={{ maxHeight: Math.max(140, plc.maxHeight - 160) }}
                          >
                            {(industryOptions.filter(opt => !industryQuery.trim() || opt.toLowerCase().includes(industryQuery.toLowerCase()))).map((opt) => (
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
                            showsVerticalScrollIndicator={true}
                            contentContainerStyle={{ paddingBottom: 0 }}
                            keyboardShouldPersistTaps="handled"
                            scrollEventThrottle={16}
                            style={{ maxHeight: Math.max(140, plc.maxHeight - 160) }}
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
                        <Text style={{ color: theme.color.cardForeground, fontSize: 14, fontWeight: '700', marginBottom: 6 }}>Select nichees</Text>
                        <View style={{ position: 'relative', paddingBottom: 8 }}>
                          {!industry ? (
                            <View style={{ paddingVertical: 8 }}>
                              <Text style={{ color: theme.color.mutedForeground, fontSize: 13 }}>Select industry first</Text>
                            </View>
                          ) : (
                            <>
                          <View style={{ marginBottom: 8 }}>
                            <View style={{ backgroundColor: theme.color.accent, borderRadius: 8, paddingHorizontal: 10, paddingVertical: 6, borderWidth: 0, borderColor: 'transparent' }}>
                              <TextInput
                                value={lobQuery}
                                onChangeText={setLobQuery}
                                    placeholder="Search nichees"
                                placeholderTextColor={theme.color.mutedForeground}
                                style={{ color: theme.color.cardForeground, fontSize: 14, paddingVertical: 2 }}
                              />
                            </View>
                          </View>
                          {/* Add custom moved above the list to avoid clipping */}
                          <View style={{ marginBottom: 8 }}>
                            <View style={{ flexDirection: 'row', alignItems: 'center', gap: 8 }}>
                              <View style={{ flex: 1, backgroundColor: theme.color.accent, borderRadius: 8, paddingHorizontal: 10, paddingVertical: 6 }}>
                                <TextInput
                                  value={lobCustomInput}
                                  onChangeText={setLobCustomInput}
                                  placeholder="Add custom"
                                  placeholderTextColor={theme.color.mutedForeground}
                                  style={{ color: theme.color.cardForeground, fontSize: 14, paddingVertical: 2 }}
                                />
                              </View>
                              <TouchableOpacity
                                activeOpacity={0.85}
                                onPress={() => {
                                  const v = lobCustomInput.trim()
                                  if (!v) return
                                  setCustomLobs(prev => prev.includes(v) ? prev : [...prev, v])
                                  setLobCustomInput('')
                                }}
                                style={{ backgroundColor: theme.color.primary, borderRadius: 8, paddingHorizontal: 12, paddingVertical: 8 }}
                              >
                                <Text style={{ color: '#fff', fontWeight: '600' }}>Add</Text>
                              </TouchableOpacity>
                            </View>
                          </View>
                          <ScrollView
                            showsVerticalScrollIndicator={true}
                            contentContainerStyle={{ paddingBottom: 0 }}
                            keyboardShouldPersistTaps="handled"
                            onContentSizeChange={(w, h) => setLobContentH(h)}
                            onScroll={(e) => setLobScrollY(e.nativeEvent.contentOffset.y)}
                            scrollEventThrottle={16}
                            style={{ maxHeight: Math.max(140, plc.maxHeight - 160) }}
                          >
                            {options.filter(o => !lobQuery.trim() || o.toLowerCase().includes(lobQuery.toLowerCase())).map((opt) => {
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
                            {/* End of list */}
                          </ScrollView>
                            </>
                          )}
                        </View>
                      </View>
                    </View>
              </View>
                  ) })()
            ) : null}
              </Pressable>
            </Modal>
          </View>
        ) : step === 'agent' ? (
          // Step: agent setup
          <View style={{ marginTop: 4 }}>
            <View style={{ gap: 8 }}>
              {/* Agent name */}
              <View style={{ backgroundColor: theme.color.accent, borderRadius: 12, paddingHorizontal: 12, paddingVertical: 10, borderWidth: 0, borderColor: 'transparent', shadowColor: theme.color.primary, shadowOpacity: focusCompany ? 0.12 : 0, shadowRadius: 10, shadowOffset: { width: 0, height: 6 }, elevation: 0 }}>
                <View style={{ flexDirection: 'row', alignItems: 'center', marginBottom: 6 }}>
                  <Text style={{ color: theme.color.cardForeground, fontSize: 15, fontWeight: '700', marginRight: 6 }}>Agent name</Text>
                </View>
                <TextInput value={agentName} onChangeText={setAgentName} placeholder="e.g., Nancy" placeholderTextColor={theme.color.mutedForeground} underlineColorAndroid="transparent" onFocus={() => setFocusAgentName(true)} onBlur={() => setFocusAgentName(false)} style={{ color: theme.color.cardForeground, paddingVertical: 4, fontSize: 15, borderWidth: 0, borderColor: 'transparent' }} />
              </View>

              {/* Desired Title */}
              <View style={{ backgroundColor: theme.color.accent, borderRadius: 12, paddingHorizontal: 12, paddingVertical: 10, borderWidth: 0, borderColor: 'transparent', shadowColor: theme.color.primary, shadowOpacity: focusIndustry ? 0.12 : 0, shadowRadius: 10, shadowOffset: { width: 0, height: 6 }, elevation: 0 }}>
                <View style={{ flexDirection: 'row', alignItems: 'center', marginBottom: 6 }}>
                  <Text style={{ color: theme.color.cardForeground, fontSize: 15, fontWeight: '700', marginRight: 6 }}>Role</Text>
                </View>
                <Pressable
                  ref={titleAnchorRef}
                  collapsable={false}
                  onPress={openTitleMenu}
                >
                  <View style={{ flexDirection: 'row', alignItems: 'center', justifyContent: 'space-between' }}>
                    <Text style={{ color: desiredTitle ? theme.color.cardForeground : theme.color.mutedForeground, paddingVertical: 2, fontSize: 15 }}>
                      {desiredTitle || 'Select title'}
                    </Text>
                    <Text style={{ color: theme.color.mutedForeground, fontSize: 16 }}>▾</Text>
                  </View>
                </Pressable>
              </View>

              {/* Agent tone */}
              <View style={{ backgroundColor: theme.color.accent, borderRadius: 12, paddingHorizontal: 12, paddingVertical: 10, borderWidth: 0, borderColor: 'transparent', shadowColor: theme.color.primary, shadowOpacity: 0, shadowRadius: 10, shadowOffset: { width: 0, height: 6 }, elevation: 0 }}>
                <View style={{ flexDirection: 'row', alignItems: 'center', marginBottom: 6 }}>
                  <Text style={{ color: theme.color.cardForeground, fontSize: 15, fontWeight: '700', marginRight: 6 }}>Tone</Text>
                </View>
                <Pressable ref={toneAnchorRef} collapsable={false} onPress={openToneMenu}>
                  <View style={{ flexDirection: 'row', alignItems: 'center', justifyContent: 'space-between' }}>
                    <Text style={{ color: agentTone ? theme.color.cardForeground : theme.color.mutedForeground, paddingVertical: 2, fontSize: 15 }}>
                      {agentTone || 'Select tone'}
                    </Text>
                    <Text style={{ color: theme.color.mutedForeground, fontSize: 16 }}>▾</Text>
                  </View>
                </Pressable>
              </View>

              {/* Agent traits (inline toggle chips) */}
              <View style={{ backgroundColor: theme.color.accent, borderRadius: 12, paddingHorizontal: 12, paddingVertical: 10, borderWidth: 0, borderColor: 'transparent', shadowColor: theme.color.primary, shadowOpacity: 0, shadowRadius: 10, shadowOffset: { width: 0, height: 6 }, elevation: 0 }}>
                <View style={{ flexDirection: 'row', alignItems: 'center', marginBottom: 8 }}>
                  <Text style={{ color: theme.color.cardForeground, fontSize: 15, fontWeight: '700', marginRight: 6 }}>Traits</Text>
                </View>
                <View style={{ flexDirection: 'row', flexWrap: 'wrap', alignItems: 'center' }}>
                  {traitOptions.map((opt) => {
                    const selected = selectedTraits.includes(opt)
                    return (
                      <TouchableOpacity
                        key={opt}
                        onPress={() => setSelectedTraits(prev => selected ? prev.filter(x => x !== opt) : [...prev, opt])}
                        activeOpacity={0.85}
                        style={{
                          paddingHorizontal: 12,
                          paddingVertical: 8,
                          borderRadius: 999,
                          marginRight: 6,
                          marginBottom: 6,
                          backgroundColor: selected ? theme.color.primary : (theme.dark ? theme.color.secondary : theme.color.card),
                        }}
                      >
                        <Text style={{ color: selected ? '#fff' : theme.color.mutedForeground, fontSize: 13, fontWeight: '700' }}>{opt}</Text>
                      </TouchableOpacity>
                    )
                  })}
                </View>
              </View>

              {/* Escalation rules */}
              <View style={{ backgroundColor: theme.color.accent, borderRadius: 12, paddingHorizontal: 12, paddingVertical: 10, borderWidth: 0, borderColor: 'transparent', shadowColor: theme.color.primary, shadowOpacity: 0, shadowRadius: 10, shadowOffset: { width: 0, height: 6 }, elevation: 0 }}>
                <View style={{ flexDirection: 'row', alignItems: 'center', marginBottom: 6 }}>
                  <Text style={{ color: theme.color.cardForeground, fontSize: 15, fontWeight: '700', marginRight: 6 }}>Escalation rules</Text>
                  <Pressable ref={escalationTipRef} hitSlop={{ top: 6, bottom: 6, left: 6, right: 6 }} onPress={() => openTooltip(escalationTipRef, 'Tell the agent when to hand off to a human to protect CX and SLAs.') }>
                    <View style={{ width: 16, height: 16, borderRadius: 8, borderWidth: 0, borderColor: 'transparent', alignItems: 'center', justifyContent: 'center', backgroundColor: theme.dark ? '#000' : '#fff' }}>
                      <Text style={{ color: theme.color.primary, fontSize: 11, fontWeight: '500' }}>i</Text>
                    </View>
                  </Pressable>
                </View>
                <Pressable ref={escalationAnchorRef} collapsable={false} onPress={openEscalationMenu}>
                  <View style={{ flexDirection: 'row', alignItems: 'center', justifyContent: 'space-between' }}>
                    <Text style={{ color: escalationRule ? theme.color.cardForeground : theme.color.mutedForeground, paddingVertical: 2, fontSize: 15 }}>
                      {escalationRule || 'Select rules'}
                    </Text>
                    <Text style={{ color: theme.color.mutedForeground, fontSize: 16 }}>▾</Text>
                  </View>
                </Pressable>
              </View>
            </View>

            <View style={{ marginTop: 12 }}>
              <Button title="Next" size="lg" variant="hero" onPress={() => { LayoutAnimation.configureNext(LayoutAnimation.Presets.easeInEaseOut); setStep('uploads') }} />
            </View>

            <TouchableOpacity onPress={handleBack} style={{ alignSelf: 'center', marginTop: 20 }}>
              <Text style={{ color: theme.color.mutedForeground, fontWeight: '600' }}>Back</Text>
            </TouchableOpacity>
          </View>
        ) : step === 'uploads' ? (
          // Step: uploads
          <View style={{ marginTop: 4 }}>
            <View style={{ gap: 12 }}>
              {/* Choose applicable materials */}
              <View style={{ backgroundColor: theme.color.accent, borderRadius: 12, paddingHorizontal: 12, paddingVertical: 12 }}>
                <Text style={{ color: theme.color.cardForeground, fontSize: 15, fontWeight: '700', marginBottom: 8 }}>Which materials do you want to upload?</Text>
                <View style={{ flexDirection: 'row', flexWrap: 'wrap', alignItems: 'center' }}>
                  {uploadOptions.map((opt) => {
                    const selected = selectedUploadTypes.includes(opt.key)
                    return (
                      <TouchableOpacity
                        key={opt.key}
                        onPress={() => setSelectedUploadTypes(prev => selected ? prev.filter(x => x !== opt.key) : [...prev, opt.key])}
                        activeOpacity={0.85}
                        style={{
                          paddingHorizontal: 12,
                          paddingVertical: 8,
                          borderRadius: 999,
                          marginRight: 6,
                          marginBottom: 6,
                          backgroundColor: selected ? theme.color.primary : (theme.dark ? theme.color.secondary : theme.color.card),
                        }}
                      >
                        <Text style={{ color: selected ? '#fff' : theme.color.mutedForeground, fontSize: 13, fontWeight: '700' }}>{opt.label}</Text>
                      </TouchableOpacity>
                    )
                  })}
                </View>
              </View>
 
               {/* Vision */}
               {selectedUploadTypes.includes('vision') && (
               <View style={{ backgroundColor: theme.color.accent, borderRadius: 12, paddingHorizontal: 12, paddingVertical: 12 }}>
                 <Text style={{ color: theme.color.cardForeground, fontSize: 15, fontWeight: '700', marginBottom: 8 }}>Vision</Text>
                 <UploadInputRow 
                   url={visionUrl} 
                   onUrlChange={setVisionUrl} 
                   onAttach={() => attachUrl(visionUrl, setVisionFiles, () => setVisionUrl(''))} 
                   files={visionFiles} 
                   theme={theme} 
                 />
              </View>
              )}
 
               {/* Mission */}
               {selectedUploadTypes.includes('mission') && (
               <View style={{ backgroundColor: theme.color.accent, borderRadius: 12, paddingHorizontal: 12, paddingVertical: 12 }}>
                 <Text style={{ color: theme.color.cardForeground, fontSize: 15, fontWeight: '700', marginBottom: 8 }}>Mission</Text>
                 <UploadInputRow 
                   url={missionUrl} 
                   onUrlChange={setMissionUrl} 
                   onAttach={() => attachUrl(missionUrl, setMissionFiles, () => setMissionUrl(''))} 
                   files={missionFiles} 
                   theme={theme} 
                 />
               </View>
               )}
 
               {/* Products/Services Catalog */}
               {selectedUploadTypes.includes('catalog') && (
               <View style={{ backgroundColor: theme.color.accent, borderRadius: 12, paddingHorizontal: 12, paddingVertical: 12 }}>
                 <Text style={{ color: theme.color.cardForeground, fontSize: 15, fontWeight: '700', marginBottom: 8 }}>{catalogEntity} Catalog</Text>
                 <UploadInputRow 
                   url={catalogUrl} 
                   onUrlChange={setCatalogUrl} 
                   onAttach={() => attachUrl(catalogUrl, setCatalogFiles, () => setCatalogUrl(''))} 
                   files={catalogFiles} 
                   theme={theme} 
                 />
               </View>
               )}
 
               {/* FAQs */}
               {selectedUploadTypes.includes('faqs') && (
               <View style={{ backgroundColor: theme.color.accent, borderRadius: 12, paddingHorizontal: 12, paddingVertical: 12 }}>
                 <Text style={{ color: theme.color.cardForeground, fontSize: 15, fontWeight: '700', marginBottom: 8 }}>FAQs</Text>
                 <UploadInputRow 
                   url={faqsUrl} 
                   onUrlChange={setFaqsUrl} 
                   onAttach={() => attachUrl(faqsUrl, setFaqsFiles, () => setFaqsUrl(''))} 
                   files={faqsFiles} 
                   theme={theme} 
                 />
               </View>
               )}
 
               {/* Knowledge Base */}
               {selectedUploadTypes.includes('kb') && (
               <View style={{ backgroundColor: theme.color.accent, borderRadius: 12, paddingHorizontal: 12, paddingVertical: 12 }}>
                 <Text style={{ color: theme.color.cardForeground, fontSize: 15, fontWeight: '700', marginBottom: 8 }}>Knowledge Base</Text>
                 <UploadInputRow 
                   url={kbUrl} 
                   onUrlChange={setKbUrl} 
                   onAttach={() => attachUrl(kbUrl, setKbFiles, () => setKbUrl(''))} 
                   files={kbFiles} 
                   theme={theme} 
                 />
               </View>
               )}
 
               {/* SOPs */}
               {selectedUploadTypes.includes('sops') && (
               <View style={{ backgroundColor: theme.color.accent, borderRadius: 12, paddingHorizontal: 12, paddingVertical: 12 }}>
                 <Text style={{ color: theme.color.cardForeground, fontSize: 15, fontWeight: '700', marginBottom: 8 }}>SOPs</Text>
                 <UploadInputRow 
                   url={sopsUrl} 
                   onUrlChange={setSopsUrl} 
                   onAttach={() => attachUrl(sopsUrl, setSopsFiles, () => setSopsUrl(''))} 
                   files={sopsFiles} 
                   theme={theme} 
                 />
               </View>
               )}
 
               {/* Terms & Conditions */}
               {selectedUploadTypes.includes('tc') && (
               <View style={{ backgroundColor: theme.color.accent, borderRadius: 12, paddingHorizontal: 12, paddingVertical: 12 }}>
                 <Text style={{ color: theme.color.cardForeground, fontSize: 15, fontWeight: '700', marginBottom: 8 }}>T&C</Text>
                 <UploadInputRow 
                   url={tcUrl} 
                   onUrlChange={setTcUrl} 
                   onAttach={() => attachUrl(tcUrl, setTcFiles, () => setTcUrl(''))} 
                   files={tcFiles} 
                   theme={theme} 
                 />
               </View>
               )}
             </View>
 
             <View style={{ marginTop: 12 }}>
               <Button title="Finish" size="lg" variant="hero" onPress={onComplete} />
             </View>
 
             <TouchableOpacity onPress={handleBack} style={{ alignSelf: 'center', marginTop: 20 }}>
               <Text style={{ color: theme.color.mutedForeground, fontWeight: '600' }}>Back</Text>
             </TouchableOpacity>
          </View>
        ) : null}
        </ScrollView>
      </View>
      </KeyboardAvoidingView>
      {/* Agent: Title Modal */}
      <Modal
        visible={showTitleModal}
        transparent
        animationType="fade"
        onRequestClose={() => { setShowTitleModal(false); setFocusTitle(false) }}
      >
        <Pressable style={{ flex: 1, backgroundColor: 'rgba(0,0,0,0.25)' }} onPress={() => { setShowTitleModal(false); setFocusTitle(false) }}>
          {titleMenuPos ? (() => { const plc = getMenuPlacement(titleMenuPos); return (
            <View style={{ position: 'absolute', left: titleMenuPos.x, top: plc.top, width: titleMenuPos.width }}>
              <View onLayout={(e) => setTitleContainerH(e.nativeEvent.layout.height)} style={{ backgroundColor: theme.color.card, borderRadius: 12, borderWidth: 0, borderColor: 'transparent', maxHeight: plc.maxHeight, shadowColor: '#000', shadowOpacity: 0.2, shadowRadius: 16, shadowOffset: { width: 0, height: 10 }, elevation: 10, overflow: 'hidden' }}>
                <View style={{ paddingVertical: 10, paddingHorizontal: 12 }}>
                  <Text style={{ color: theme.color.cardForeground, fontSize: 15, fontWeight: '700', marginBottom: 8 }}>Select title</Text>
                  {/* Add custom */}
                  <View style={{ marginBottom: 8 }}>
                    <View style={{ flexDirection: 'row', alignItems: 'center', gap: 8 }}>
                      <View style={{ flex: 1, backgroundColor: theme.color.accent, borderRadius: 8, paddingHorizontal: 10, paddingVertical: 8 }}>
                        <TextInput
                          value={titleCustomInput}
                          onChangeText={setTitleCustomInput}
                          placeholder="Add custom"
                          placeholderTextColor={theme.color.mutedForeground}
                          style={{ color: theme.color.cardForeground, fontSize: 15, paddingVertical: 4 }}
                        />
                      </View>
                      <TouchableOpacity
                        activeOpacity={0.85}
                        onPress={() => {
                          const v = titleCustomInput.trim(); if (!v) return;
                          setDesiredTitle(v);
                          setTitleCustomInput('');
                          setShowTitleModal(false); setFocusTitle(false);
                        }}
                        style={{ backgroundColor: theme.color.primary, borderRadius: 8, paddingHorizontal: 12, paddingVertical: 10 }}
                      >
                        <Text style={{ color: '#fff', fontWeight: '600' }}>Add</Text>
                      </TouchableOpacity>
                    </View>
                  </View>
                  <ScrollView
                    showsVerticalScrollIndicator={true}
                    contentContainerStyle={{ paddingTop: 8, paddingBottom: 12 }}
                    keyboardShouldPersistTaps="handled"
                    onContentSizeChange={(w,h)=>setTitleContentH(h)}
                    onScroll={(e)=>setTitleScrollY(e.nativeEvent.contentOffset.y)}
                    scrollEventThrottle={16}
                    style={{ maxHeight: Math.max(140, plc.maxHeight - 160) }}
                  >
                    {titleOptions.map((opt) => (
                      <TouchableOpacity key={opt} onPress={() => { setDesiredTitle(opt); setShowTitleModal(false); setFocusTitle(false) }} hitSlop={{ top: 8, bottom: 8, left: 0, right: 0 }} style={{ paddingVertical: 10, paddingHorizontal: 10, borderRadius: 8, backgroundColor: opt === desiredTitle ? theme.color.accent : 'transparent', marginBottom: 4 }} activeOpacity={0.8}>
                        <Text numberOfLines={1} ellipsizeMode="tail" style={{ color: theme.color.cardForeground, fontSize: 15 }}>{opt}</Text>
                      </TouchableOpacity>
                    ))}
                  </ScrollView>
                </View>
              </View>
            </View>
          )})() : null}
        </Pressable>
      </Modal>

      {/* Agent: Tone Modal */}
      <Modal
        visible={showToneModal}
        transparent
        animationType="fade"
        onRequestClose={() => { setShowToneModal(false); setFocusTone(false) }}
      >
        <Pressable style={{ flex: 1, backgroundColor: 'rgba(0,0,0,0.25)' }} onPress={() => { setShowToneModal(false); setFocusTone(false) }}>
          {toneMenuPos ? (() => { const plc = getMenuPlacement(toneMenuPos); return (
            <View style={{ position: 'absolute', left: toneMenuPos.x, top: plc.top, width: toneMenuPos.width }}>
              <View onLayout={(e) => setToneContainerH(e.nativeEvent.layout.height)} style={{ backgroundColor: theme.color.card, borderRadius: 12, borderWidth: 0, borderColor: 'transparent', maxHeight: plc.maxHeight, shadowColor: '#000', shadowOpacity: 0.2, shadowRadius: 16, shadowOffset: { width: 0, height: 10 }, elevation: 10, overflow: 'hidden' }}>
                <View style={{ paddingVertical: 10, paddingHorizontal: 12 }}>
                  <Text style={{ color: theme.color.cardForeground, fontSize: 15, fontWeight: '700', marginBottom: 8 }}>Select tone</Text>
                  <ScrollView
                    showsVerticalScrollIndicator={true}
                    contentContainerStyle={{ paddingTop: 8, paddingBottom: 12 }}
                    keyboardShouldPersistTaps="handled"
                    onContentSizeChange={(w,h)=>setToneContentH(h)}
                    onScroll={(e)=>setToneScrollY(e.nativeEvent.contentOffset.y)}
                    scrollEventThrottle={16}
                    style={{ maxHeight: Math.max(140, plc.maxHeight - 160) }}
                  >
                    {toneOptions.map((opt) => (
                      <TouchableOpacity key={opt} onPress={() => { setAgentTone(opt); setShowToneModal(false); setFocusTone(false) }} hitSlop={{ top: 8, bottom: 8, left: 0, right: 0 }} style={{ paddingVertical: 10, paddingHorizontal: 10, borderRadius: 8, backgroundColor: opt === agentTone ? theme.color.accent : 'transparent', marginBottom: 4 }} activeOpacity={0.8}>
                        <Text numberOfLines={1} ellipsizeMode="tail" style={{ color: theme.color.cardForeground, fontSize: 15 }}>{opt}</Text>
                      </TouchableOpacity>
                    ))}
                  </ScrollView>
                </View>
              </View>
            </View>
          )})() : null}
        </Pressable>
      </Modal>

      {/* Agent: Traits Modal removed - using inline toggle chips */}

      {/* Agent: Escalation Modal */}
      <Modal
        visible={showEscalationModal}
        transparent
        animationType="fade"
        onRequestClose={() => { setShowEscalationModal(false); setFocusEscalation(false) }}
      >
        <Pressable style={{ flex: 1, backgroundColor: 'rgba(0,0,0,0.25)' }} onPress={() => { setShowEscalationModal(false); setFocusEscalation(false) }}>
          {escalationMenuPos ? (() => { const plc = getMenuPlacement(escalationMenuPos); return (
            <View style={{ position: 'absolute', left: escalationMenuPos.x, top: plc.top, width: escalationMenuPos.width }}>
              <View onLayout={(e) => setEscalationContainerH(e.nativeEvent.layout.height)} style={{ backgroundColor: theme.color.card, borderRadius: 12, borderWidth: 0, borderColor: 'transparent', maxHeight: plc.maxHeight, shadowColor: '#000', shadowOpacity: 0.2, shadowRadius: 16, shadowOffset: { width: 0, height: 10 }, elevation: 10, overflow: 'hidden' }}>
                <View style={{ paddingVertical: 10, paddingHorizontal: 12 }}>
                  <Text style={{ color: theme.color.cardForeground, fontSize: 15, fontWeight: '700', marginBottom: 8 }}>Escalation rules</Text>
                  <ScrollView
                    showsVerticalScrollIndicator={true}
                    contentContainerStyle={{ paddingTop: 8, paddingBottom: 12 }}
                    keyboardShouldPersistTaps="handled"
                    onContentSizeChange={(w,h)=>setEscalationContentH(h)}
                    onScroll={(e)=>setEscalationScrollY(e.nativeEvent.contentOffset.y)}
                    scrollEventThrottle={16}
                    style={{ maxHeight: Math.max(140, plc.maxHeight - 160) }}
                  >
                    {escalationOptions.map((opt) => (
                      <TouchableOpacity key={opt} onPress={() => { setEscalationRule(opt); setShowEscalationModal(false); setFocusEscalation(false) }} hitSlop={{ top: 8, bottom: 8, left: 0, right: 0 }} style={{ paddingVertical: 10, paddingHorizontal: 10, borderRadius: 8, backgroundColor: opt === escalationRule ? theme.color.accent : 'transparent', marginBottom: 4 }} activeOpacity={0.8}>
                        <Text numberOfLines={1} ellipsizeMode="tail" style={{ color: theme.color.cardForeground, fontSize: 15 }}>{opt}</Text>
                      </TouchableOpacity>
                    ))}
                  </ScrollView>
                </View>
              </View>
            </View>
          )})() : null}
        </Pressable>
      </Modal>
      
      {/* Shared Tooltip */}
      <Modal
        visible={showTooltip}
        transparent
        animationType="fade"
        onRequestClose={() => setShowTooltip(false)}
      >
        <Pressable style={{ flex: 1, backgroundColor: 'transparent' }} onPress={() => setShowTooltip(false)}>
          {tooltipMenuPos ? (() => {
            const screen = Dimensions.get('window')
            const gap = 8
            const width = Math.max(220, Math.min(320, tooltipMenuPos.width))
            let left = tooltipMenuPos.x + tooltipMenuPos.width + gap
            if (left + width + 8 > screen.width) {
              left = Math.max(8, tooltipMenuPos.x - width - gap)
            }
            const top = Math.max(8, Math.min(tooltipMenuPos.y, screen.height - 200))
            return (
              <View style={{ position: 'absolute', left, top, width, backgroundColor: theme.color.card, borderRadius: 10, padding: 10, shadowColor: '#000', shadowOpacity: 0.2, shadowRadius: 12, shadowOffset: { width: 0, height: 8 }, elevation: 8 }}>
                <Text style={{ color: theme.color.cardForeground, fontSize: 12, lineHeight: 16 }}>{tooltipText}</Text>
              </View>
            )
          })() : null}
        </Pressable>
      </Modal>
    </SafeAreaView>
  )
}

// Mount shared tooltip overlay
export const RegisterScreenWithTooltips = (props: any) => {
  const { theme } = useTheme()
  // We can't access inner state from here, so this is a placeholder export in case of reuse.
  return null
}

// Tooltip Modal (shared)
// Rendered as a sibling overlay in the same file scope for simplicity
export const RegisterTooltipsOverlay: React.FC<{
  visible: boolean,
  text: string,
  pos: { x: number, y: number, width: number, height: number } | null,
  theme: any,
  onClose: () => void
}> = ({ visible, text, pos, theme, onClose }) => {
  if (!visible || !pos) return null as any
  const plc = ((p: { x: number, y: number, width: number, height: number }) => {
    const screenH = Dimensions.get('window').height
    const margin = 24
    const gap = 6
    const below = Math.max(0, screenH - (p.y + p.height + gap) - margin)
    const above = Math.max(0, p.y - margin)
    const openBelow = below >= Math.max(100, above)
    const maxHeight = Math.min(200, openBelow ? below : above)
    const top = openBelow ? (p.y + p.height + gap) : Math.max(margin, p.y - maxHeight - gap)
    return { top, maxHeight }
  })(pos)
  return (
    <Modal visible={visible} transparent animationType="fade" onRequestClose={onClose}>
      <Pressable style={{ flex: 1, backgroundColor: 'rgba(0,0,0,0.15)' }} onPress={onClose}>
        <View style={{ position: 'absolute', left: pos.x, top: plc.top, width: Math.max(220, pos.width), backgroundColor: theme.color.card, borderRadius: 10, padding: 10, shadowColor: '#000', shadowOpacity: 0.2, shadowRadius: 12, shadowOffset: { width: 0, height: 8 }, elevation: 8 }}>
          <Text style={{ color: theme.color.cardForeground, fontSize: 12, lineHeight: 16 }}>{text}</Text>
        </View>
      </Pressable>
    </Modal>
  )
}
