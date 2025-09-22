import React, { useState, useRef, useEffect, useMemo } from 'react'
import { View, Text, ScrollView, Alert, findNodeHandle, TouchableOpacity, Animated, Share, Platform } from 'react-native'
import { useTranslation } from 'react-i18next'
import { useTheme } from '../../providers/ThemeProvider'
import { Input } from '../../components/ui/Input'
import { Button } from '../../components/ui/Button'
import { Modal } from '../../components/ui/Modal'
import { Bot, User, Settings, BookOpen, Zap, Copy } from 'lucide-react-native'
import { AGENT_ROLES, AGENT_TONES, AGENT_TRAITS, ESCALATION_OPTIONS } from '../../config/agentOptions'
import Svg, { Circle, Rect, G, Path, Defs, RadialGradient, Stop, Ellipse } from 'react-native-svg'

interface CreateAgentProps {
  visible: boolean
  onClose: () => void
  onSave: (agent: any) => void
}

export const CreateAgent: React.FC<CreateAgentProps> = ({ visible, onClose, onSave }) => {
  const { t } = useTranslation()
  const { theme } = useTheme()
  
  const [formData, setFormData] = useState({
    name: '',
    roles: [] as string[],
    tone: '',
    traits: [] as string[],
    escalationRule: '',
    tools: [] as string[]
  })
  const [step, setStep] = useState<'form' | 'share'>('form')
  const [createdName, setCreatedName] = useState<string>('')

  // Smart auto-center on selection
  const scrollRef = useRef<ScrollView>(null)
  type SectionKey = 'basic' | 'role' | 'tone' | 'traits' | 'escalation' | 'tools' | 'dataAccess'
  const sectionRefs: Record<SectionKey, React.RefObject<View>> = {
    basic: useRef<View>(null as any),
    role: useRef<View>(null as any),
    tone: useRef<View>(null as any),
    traits: useRef<View>(null as any),
    escalation: useRef<View>(null as any),
    tools: useRef<View>(null as any),
    dataAccess: useRef<View>(null as any),
  }
  const [viewportHeight, setViewportHeight] = useState(0)
  const [contentHeight, setContentHeight] = useState(0)
  const [sectionLayouts, setSectionLayouts] = useState<Record<SectionKey, { y: number; height: number }>>({} as any)

  const setSectionLayout = (key: SectionKey, y: number, height: number) => {
    setSectionLayouts(prev => ({ ...prev, [key]: { y, height } }))
  }

  // Slight downward bias so section hints (below options) are in view
  const SCROLL_BIAS: Record<SectionKey, number> = {
    basic: 0,
    role: 56,
    tone: 56,
    traits: 56,
    escalation: 56,
    tools: 56,
    dataAccess: 56,
  }

  const scrollSectionToCenter = (key: SectionKey) => {
    const layout = sectionLayouts[key]
    if (!scrollRef.current || !layout || viewportHeight === 0) return
    const bias = SCROLL_BIAS[key] || 0
    let targetY = layout.y + layout.height / 2 - viewportHeight / 2 + bias
    if (contentHeight > 0) {
      const maxY = Math.max(0, contentHeight - viewportHeight)
      targetY = Math.min(maxY, Math.max(0, targetY))
    } else {
      targetY = Math.max(0, targetY)
    }
    scrollRef.current.scrollTo({ y: targetY, animated: true })
  }

  const centerAfterLayout = (key: SectionKey) => {
    // Defer to next frames to capture any reflow from chip wrapping
    requestAnimationFrame(() => requestAnimationFrame(() => scrollSectionToCenter(key)))
  }

  const roles = AGENT_ROLES
  const tones = AGENT_TONES
  const traitOptions = AGENT_TRAITS
  const escalationOptions = ESCALATION_OPTIONS

  const availableTools = [
    { key: 'email', label: 'Email Integration' },
    { key: 'calendar', label: 'Calendar Booking' },
    { key: 'crm', label: 'CRM Access' },
    { key: 'knowledge', label: 'Knowledge Search' },
    { key: 'escalation', label: 'Human Escalation' }
  ]

  const handleSave = () => {
    if (!formData.name.trim()) {
      Alert.alert('Error', 'Agent name is required')
      return
    }

    const newAgent = {
      id: Date.now().toString(),
      name: formData.name,
      persona: (formData.tone || 'professional').toLowerCase(),
      department: 'support',
      role: formData.roles[0] || '',
      roles: formData.roles,
      tone: formData.tone,
      traits: formData.traits,
      escalationRule: formData.escalationRule,
      tools: formData.tools,
      dataAccess: {
        mode: dataAccessMode,
        selectedCollections: dataAccessSelected,
      },
      status: 'active',
      createdAt: new Date().toISOString(),
      conversations: 0,
      responseTime: '0s'
    }

    onSave(newAgent)
    setCreatedName(formData.name)
    setStep('share')
    
    // Reset form
    setFormData({
      name: '',
      tone: '',
      traits: [],
      escalationRule: '',
      tools: [],
      roles: []
    })
    setDataAccessMode('all')
    setDataAccessSelected([])
  }

  const toggleTool = (toolKey: string) => {
    setFormData(prev => ({
      ...prev,
      tools: prev.tools.includes(toolKey)
        ? prev.tools.filter(t => t !== toolKey)
        : [...prev.tools, toolKey]
    }))
  }

  // Data Access (quick setup)
  type DataAccessMode = 'all' | 'select'
  const [dataAccessMode, setDataAccessMode] = useState<DataAccessMode>('all')
  const [dataAccessSelected, setDataAccessSelected] = useState<string[]>([])
  const availableCollections = [
    'Company Vision',
    'Mission Statement',
    'Support SOPs',
    'Product Knowledge Base',
    'Security Policies',
    'HR Handbook',
  ]
  const toggleCollection = (c: string) => {
    setDataAccessSelected(prev => prev.includes(c) ? prev.filter(x => x !== c) : [...prev, c])
  }

  // Share step animations and typed message
  const pupil = useRef(new Animated.Value(0)).current
  const mouth = useRef(new Animated.Value(0)).current
  const blink = useRef(new Animated.Value(0)).current
  const mouthLoopRef = useRef<any>(null)
  const mouthLoopRunningRef = useRef(false)
  const [typedMsg, setTypedMsg] = useState('')

  useEffect(() => {
    if (!(visible && step === 'share')) return
    const eyeLoop = Animated.loop(
      Animated.sequence([
        Animated.timing(pupil, { toValue: 1, duration: 2000, useNativeDriver: true }),
        Animated.timing(pupil, { toValue: -1, duration: 2000, useNativeDriver: true }),
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
        Animated.delay(2600),
        Animated.timing(blink, { toValue: 1, duration: 120, useNativeDriver: false }),
        Animated.delay(80),
        Animated.timing(blink, { toValue: 0, duration: 140, useNativeDriver: false })
      ])
    )
    eyeLoop.start(); blinkLoop.start()
    return () => { eyeLoop.stop(); mouthLoopRef.current?.stop(); mouthLoopRunningRef.current = false; blinkLoop.stop() }
  }, [visible, step, pupil, mouth, blink])

  useEffect(() => {
    if (!(visible && step === 'share')) return
    const full = (t('onboarding.shareAnywhere.message') as string) || "Hello there! I'm your AI assistant. I'm ready to serve your clients anywhere you place me.\nJust pin my link anywhere!"
    setTypedMsg('')
    let i = 0
    const timer = setInterval(() => {
      i++
      setTypedMsg(full.slice(0, i))
      if (i >= full.length) clearInterval(timer)
    }, 28)
    return () => clearInterval(timer)
  }, [visible, step, t])

  // Start/stop mouth animation in sync with streaming
  useEffect(() => {
    if (!(visible && step === 'share')) return
    const full = (t('onboarding.shareAnywhere.message') as string) || ''
    const isTyping = typedMsg.length < full.length
    if (isTyping) {
      if (!mouthLoopRunningRef.current) {
        mouthLoopRef.current?.start()
        mouthLoopRunningRef.current = true
      }
    } else if (mouthLoopRunningRef.current) {
      mouthLoopRef.current?.stop()
      mouthLoopRunningRef.current = false
      Animated.timing(mouth, { toValue: 0, duration: 180, useNativeDriver: true }).start()
    }
  }, [typedMsg, visible, step, t])

  const pupilOffset = pupil.interpolate({ inputRange: [-1, 1], outputRange: [-3, 3] })
  const innerMouthH = mouth.interpolate({ inputRange: [0, 1], outputRange: [8, 16] })
  const teethH = mouth.interpolate({ inputRange: [0, 1], outputRange: [4, 7] })
  const tongueH = mouth.interpolate({ inputRange: [0, 1], outputRange: [3, 6] })
  const eyelidHeight = blink.interpolate({ inputRange: [0, 1], outputRange: [0, 24] })
  const AnimatedCircle: any = Animated.createAnimatedComponent(Circle)
  const AnimatedRect: any = Animated.createAnimatedComponent(Rect)

  const slugify = (s?: string) => (s || 'nancy-ai')
    .toLowerCase()
    .replace(/[^a-z0-9\s-]/g, '')
    .trim()
    .replace(/\s+/g, '-')
  const link = useMemo(() => `pocket.com/yourbusiness/${slugify(createdName)}`, [createdName])

  const handleShareLink = async () => {
    const toShare = link.startsWith('http') ? link : `https://${link}`
    try {
      await Share.share({ message: toShare })
    } catch {}
  }

  return (
    <Modal visible={visible} onClose={() => { setStep('form'); onClose() }} title={step === 'form' ? t('agents.createAgent') : 'Share Me Anywhere'} size="lg" autoHeight={step === 'share'}>
      <ScrollView
        ref={scrollRef}
        showsVerticalScrollIndicator={false}
        contentContainerStyle={{ paddingHorizontal: 24, paddingBottom: 20 }}
        onLayout={(e) => setViewportHeight(e.nativeEvent.layout.height)}
        onContentSizeChange={(_, h) => setContentHeight(h)}
      >
        {step === 'form' && (
        <>
        {/* Agent Name */}
        <View
          ref={sectionRefs.basic}
          style={{ marginBottom: 20 }}
          onLayout={(e) => setSectionLayout('basic', e.nativeEvent.layout.y, e.nativeEvent.layout.height)}
        >
          <Text style={{ color: theme.color.cardForeground, fontSize: 16, fontWeight: '600', marginBottom: 12 }}>Agent Name</Text>
          <Input
            value={formData.name}
            onChangeText={(text) => setFormData(prev => ({ ...prev, name: text }))}
            placeholder="e.g. Nancy"
            icon={<Bot size={20} color={theme.color.mutedForeground} />}
            borderless
            surface="accent"
          />
          <Text style={{ color: theme.color.mutedForeground, fontSize: 12, marginTop: 8 }}>
            Use a friendly, recognizable name.
          </Text>
          
          {/* Description removed */}
        </View>

        <View style={{ height: 1, backgroundColor: theme.color.border, marginBottom: 16 }} />
        {/* Role */}
        <View
          ref={sectionRefs.role}
          style={{ marginBottom: 20 }}
          onLayout={(e) => setSectionLayout('role', e.nativeEvent.layout.y, e.nativeEvent.layout.height)}
        >
          <Text style={{ color: theme.color.cardForeground, fontSize: 16, fontWeight: '600', marginBottom: 12 }}>
            Role
          </Text>
          <View style={{ flexDirection: 'row', flexWrap: 'wrap' }}>
            {roles.map((r) => {
              const selected = formData.roles.includes(r)
              return (
                <TouchableOpacity
                  key={r}
                  onPress={() => {
                    setFormData(prev => ({
                      ...prev,
                      roles: selected ? prev.roles.filter(x => x !== r) : [...prev.roles, r]
                    }))
                    centerAfterLayout('role')
                  }}
                  activeOpacity={0.8}
                  style={{
                    marginRight: 8,
                    marginBottom: 8,
                    paddingVertical: 12,
                    paddingHorizontal: 16,
                    borderRadius: theme.radius.md,
                    backgroundColor: selected ? (theme.color.primary as any) : (theme.dark ? theme.color.secondary : theme.color.accent),
                    borderWidth: 0,
                    borderColor: 'transparent'
                  }}
                >
                  <Text style={{
                    color: selected ? ('#ffffff' as any) : (theme.color.mutedForeground as any),
                    fontWeight: '700',
                    fontSize: 13
                  }}>
                    {r}
                  </Text>
                </TouchableOpacity>
              )
            })}
          </View>
          <Text style={{ color: theme.color.mutedForeground, fontSize: 12, marginTop: 8 }}>
            You can select multiple titles for this agent, or create separate agents for different roles if needed.
          </Text>
        </View>

        <View style={{ height: 1, backgroundColor: theme.color.border, marginBottom: 16 }} />
        {/* Tone */}
        <View 
          ref={sectionRefs.tone} 
          style={{ marginBottom: 20 }}
          onLayout={(e) => setSectionLayout('tone', e.nativeEvent.layout.y, e.nativeEvent.layout.height)}
        >
          <Text style={{ color: theme.color.cardForeground, fontSize: 16, fontWeight: '600', marginBottom: 12 }}>
            Tone
          </Text>
          <View style={{ flexDirection: 'row', flexWrap: 'wrap' }}>
            {tones.map((t) => {
              const selected = formData.tone === t
              return (
                <TouchableOpacity
                  key={t}
                  onPress={() => { setFormData(prev => ({ ...prev, tone: t })); centerAfterLayout('tone') }}
                  activeOpacity={0.8}
                  style={{
                    marginRight: 8,
                    marginBottom: 8,
                    paddingVertical: 12,
                    paddingHorizontal: 16,
                    borderRadius: theme.radius.md,
                    backgroundColor: selected ? (theme.color.primary as any) : (theme.dark ? theme.color.secondary : theme.color.accent),
                    borderWidth: 0,
                    borderColor: 'transparent'
                  }}
                >
                  <Text style={{
                    color: selected ? ('#ffffff' as any) : (theme.color.mutedForeground as any),
                    fontWeight: '700',
                    fontSize: 13
                  }}>
                    {t}
                  </Text>
                </TouchableOpacity>
              )
            })}
          </View>
          <Text style={{ color: theme.color.mutedForeground, fontSize: 12, marginTop: 8 }}>
            Choose one tone for now — you can adjust this later.
          </Text>
        </View>

        <View style={{ height: 1, backgroundColor: theme.color.border, marginBottom: 16 }} />
        {/* Traits */}
        <View 
          ref={sectionRefs.traits} 
          style={{ marginBottom: 20 }}
          onLayout={(e) => setSectionLayout('traits', e.nativeEvent.layout.y, e.nativeEvent.layout.height)}
        >
          <Text style={{ color: theme.color.cardForeground, fontSize: 16, fontWeight: '600', marginBottom: 12 }}>
            Traits
          </Text>
          <View style={{ flexDirection: 'row', flexWrap: 'wrap', alignItems: 'center' }}>
            {traitOptions.map((opt) => {
              const selected = formData.traits.includes(opt)
              return (
                <TouchableOpacity
                  key={opt}
                  onPress={() => { setFormData(prev => ({
                    ...prev,
                    traits: selected ? prev.traits.filter(x => x !== opt) : [...prev.traits, opt]
                  })); centerAfterLayout('traits') }}
                  activeOpacity={0.8}
                  style={{
                    marginRight: 8,
                    marginBottom: 8,
                    paddingVertical: 12,
                    paddingHorizontal: 16,
                    borderRadius: theme.radius.md,
                    backgroundColor: selected ? (theme.color.primary as any) : (theme.dark ? theme.color.secondary : theme.color.accent),
                    borderWidth: 0,
                    borderColor: 'transparent'
                  }}
                >
                  <Text style={{
                    color: selected ? ('#ffffff' as any) : (theme.color.mutedForeground as any),
                    fontWeight: '700',
                    fontSize: 13
                  }}>
                    {opt}
                  </Text>
                </TouchableOpacity>
              )
            })}
          </View>
          <Text style={{ color: theme.color.mutedForeground, fontSize: 12, marginTop: 8 }}>
            Pick a few traits to guide the agent’s personality and style.
          </Text>
        </View>

        <View style={{ height: 1, backgroundColor: theme.color.border, marginBottom: 16 }} />
        {/* Escalation Rules */}
        <View 
          ref={sectionRefs.escalation} 
          style={{ marginBottom: 20 }}
          onLayout={(e) => setSectionLayout('escalation', e.nativeEvent.layout.y, e.nativeEvent.layout.height)}
        >
          <Text style={{ color: theme.color.cardForeground, fontSize: 16, fontWeight: '600', marginBottom: 12 }}>
            Escalation rules
          </Text>
          <View style={{ flexDirection: 'row', flexWrap: 'wrap' }}>
            {escalationOptions.map((opt) => {
              const selected = formData.escalationRule === opt
              return (
                <TouchableOpacity
                  key={opt}
                  onPress={() => { setFormData(prev => ({ ...prev, escalationRule: opt })); centerAfterLayout('escalation') }}
                  activeOpacity={0.8}
                  style={{
                    marginRight: 8,
                    marginBottom: 8,
                    paddingVertical: 12,
                    paddingHorizontal: 16,
                    borderRadius: theme.radius.md,
                    backgroundColor: selected ? (theme.color.primary as any) : (theme.dark ? theme.color.secondary : theme.color.accent),
                    borderWidth: 0,
                    borderColor: 'transparent'
                  }}
                >
                  <Text style={{
                    color: selected ? ('#ffffff' as any) : (theme.color.mutedForeground as any),
                    fontWeight: '700',
                    fontSize: 13
                  }}>
                    {opt}
                  </Text>
                </TouchableOpacity>
              )
            })}
          </View>
          <Text style={{ color: theme.color.mutedForeground, fontSize: 12, marginTop: 8 }}>
            Set when to hand off to a human. You can refine this later.
          </Text>
        </View>

        <View style={{ height: 1, backgroundColor: theme.color.border, marginBottom: 16 }} />
        {/* Data Access (quick setup) */}
        <View
          ref={sectionRefs.dataAccess}
          style={{ marginBottom: 20 }}
          onLayout={(e) => setSectionLayout('dataAccess', e.nativeEvent.layout.y, e.nativeEvent.layout.height)}
        >
          <Text style={{ color: theme.color.cardForeground, fontSize: 16, fontWeight: '600', marginBottom: 12 }}>
            Data Access
          </Text>
          {/* Segmented control: All vs Select */}
          <View style={{ backgroundColor: theme.color.muted, borderRadius: theme.radius.md, padding: 6, flexDirection: 'row', marginBottom: 12 }}>
            {(['all','select'] as DataAccessMode[]).map(mode => (
              <TouchableOpacity
                key={mode}
                onPress={() => { setDataAccessMode(mode); centerAfterLayout('dataAccess') }}
                activeOpacity={0.85}
                style={{ flex: 1, paddingVertical: 10, borderRadius: theme.radius.sm, backgroundColor: dataAccessMode === mode ? theme.color.card : 'transparent' }}
              >
                <Text style={{ textAlign: 'center', color: dataAccessMode === mode ? (theme.color.primary as any) : (theme.color.mutedForeground as any), fontSize: 12, fontWeight: '700' }}>
                  {mode === 'all' ? 'Grant all access' : 'Select collections'}
                </Text>
              </TouchableOpacity>
            ))}
          </View>

          {/* Collections multi-select */}
          {dataAccessMode === 'select' && (
            <View>
              <View style={{ flexDirection: 'row', flexWrap: 'wrap' }}>
                {availableCollections.map(col => {
                  const selected = dataAccessSelected.includes(col)
                  return (
                    <TouchableOpacity
                      key={col}
                      onPress={() => { toggleCollection(col); centerAfterLayout('dataAccess') }}
                      activeOpacity={0.85}
                      style={{
                        marginRight: 8,
                        marginBottom: 8,
                        paddingVertical: 10,
                        paddingHorizontal: 14,
                        borderRadius: theme.radius.md,
                        backgroundColor: selected ? (theme.color.primary as any) : (theme.dark ? theme.color.secondary : theme.color.accent),
                      }}
                    >
                      <Text style={{ color: selected ? ('#ffffff' as any) : (theme.color.mutedForeground as any), fontWeight: '700', fontSize: 13 }}>
                        {col}
                      </Text>
                    </TouchableOpacity>
                  )
                })}
              </View>
              <Text style={{ color: theme.color.mutedForeground, fontSize: 12, marginTop: 4 }}>
                Selected: {dataAccessSelected.length} {dataAccessSelected.length === 1 ? 'collection' : 'collections'}
              </Text>
            </View>
          )}

          {dataAccessMode === 'all' && (
            <Text style={{ color: theme.color.mutedForeground, fontSize: 12 }}>
              This agent will have access to all current and future files.
            </Text>
          )}
        </View>

        
        {/* Action Buttons */}
        <View style={{ gap: 12, marginTop: 20 }}>
          <Button
            title="Create Agent"
            onPress={handleSave}
            variant="premium"
            size="lg"
          />
        </View>
        </>
        )}

        {step === 'share' && (
          <View style={{ alignItems: 'center', alignSelf: 'stretch', paddingTop: 12 }}>
          <View style={{ width: 200, height: 200, marginBottom: 8 }}>
              <Svg width="200" height="200" viewBox="0 0 200 200">
                <Defs>
                  <RadialGradient id="cheekGrad" cx="50%" cy="50%" r="50%">
                    <Stop offset="0%" stopColor="#f0a9a0" stopOpacity="0.25" />
                    <Stop offset="100%" stopColor="#f0a9a0" stopOpacity="0" />
                  </RadialGradient>
                </Defs>
                {/* Ears behind head (smaller) */}
                <Circle cx="28" cy="100" r="10" fill="#f2c7b9" stroke={theme.color.border as any} strokeWidth={1} />
                <Circle cx="172" cy="100" r="10" fill="#f2c7b9" stroke={theme.color.border as any} strokeWidth={1} />
                {/* Head: more rounded human-robotic face */}
                <Rect x="30" y="35" width="140" height="130" rx="60" fill="#f2c7b9" stroke={theme.color.border as any} strokeWidth={1} />
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
                <Path d="M60 150 Q100 160 140 150" stroke={theme.color.border as any} strokeWidth={0.6} fill="none" opacity={0.15} />
                {/* Soft cheek blush (subtle shaping) */}
                <Ellipse cx="76" cy="116" rx="14" ry="10" fill="url(#cheekGrad)" />
                <Ellipse cx="124" cy="116" rx="14" ry="10" fill="url(#cheekGrad)" />
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
                <Path d={`M 58 70 Q 70 64 82 70`} stroke={theme.color.cardForeground as any} strokeWidth={2.5} strokeLinecap="round" fill="none" opacity={0.65} />
                <Path d={`M 118 70 Q 130 64 142 70`} stroke={theme.color.cardForeground as any} strokeWidth={2.5} strokeLinecap="round" fill="none" opacity={0.65} />
                {/* Friendly iris + small pupil */}
                <AnimatedCircle cx={Animated.add(new Animated.Value(70), pupilOffset) as any} cy={85 as any} r={7 as any} fill="#6db1ff" />
                <AnimatedCircle cx={Animated.add(new Animated.Value(70), pupilOffset) as any} cy={85 as any} r={3 as any} fill="#0a0a0a" />
                <AnimatedCircle cx={Animated.add(new Animated.Value(130), pupilOffset) as any} cy={85 as any} r={7 as any} fill="#6db1ff" />
                <AnimatedCircle cx={Animated.add(new Animated.Value(130), pupilOffset) as any} cy={85 as any} r={3 as any} fill="#0a0a0a" />
                {/* Catchlights */}
                <Circle cx="66" cy="81" r="1.5" fill="#fff" opacity={0.8} />
                <Circle cx="126" cy="81" r="1.5" fill="#fff" opacity={0.8} />
                {/* Subtle eyelids */}
                <Rect x="52" y="72" width="36" height="10" rx="6" fill={theme.color.secondary as any} opacity={0.18} />
                <Rect x="112" y="72" width="36" height="10" rx="6" fill={theme.color.secondary as any} opacity={0.18} />
                {/* Blink overlay (animated from top) - opaque to fully hide pupils */}
                <AnimatedRect x={52 as any} y={72 as any} width={36 as any} height={eyelidHeight as any} rx={8 as any} fill="#f2c7b9" opacity={1} />
                <AnimatedRect x={112 as any} y={72 as any} width={36 as any} height={eyelidHeight as any} rx={8 as any} fill="#f2c7b9" opacity={1} />
                {/* Glasses frame */}
                <G opacity={0.95}>
                  {/* Left lens */}
                  <Rect x="46" y="66" width="48" height="38" rx="12" fill="#ffffff" opacity={0.06} />
                  <Rect x="46" y="66" width="48" height="38" rx="12" fill="none" stroke={theme.color.cardForeground as any} strokeWidth={2} />
                  {/* Right lens */}
                  <Rect x="106" y="66" width="48" height="38" rx="12" fill="#ffffff" opacity={0.06} />
                  <Rect x="106" y="66" width="48" height="38" rx="12" fill="none" stroke={theme.color.cardForeground as any} strokeWidth={2} />
                  {/* Bridge */}
                  <Path d="M94 84 C98 80 104 80 108 84" stroke={theme.color.cardForeground as any} strokeWidth={2} fill="none" strokeLinecap="round" />
                  {/* Arms */}
                  <Path d="M46 82 L34 86" stroke={theme.color.cardForeground as any} strokeWidth={2} strokeLinecap="round" />
                  <Path d="M154 82 L166 86" stroke={theme.color.cardForeground as any} strokeWidth={2} strokeLinecap="round" />
                  {/* Lens highlights */}
                  <Path d="M50 72 L78 98" stroke="#ffffff" strokeWidth={1.2} opacity={0.15} />
                  <Path d="M110 72 L138 98" stroke="#ffffff" strokeWidth={1.2} opacity={0.15} />
                </G>
                {/* Small nose */}
                <Path d="M97 112 Q100 116 103 112" stroke="#b97c70" strokeWidth={1.6} fill="none" strokeLinecap="round" opacity={0.85} />
                <Circle cx="100" cy="118" r="1.2" fill="#b97c70" opacity={0.6} />
                {/* Human mouth: inner mouth (dark), teeth and tongue */}
                <AnimatedRect x={88 as any} y={131 as any} width={24 as any} height={innerMouthH as any} rx={6 as any} fill="#0a0a0a" opacity={0.9} />
                <AnimatedRect x={89 as any} y={131 as any} width={22 as any} height={teethH as any} rx={2 as any} fill="#ffffff" opacity={0.95} />
                <Rect x="96" y="131" width="1.5" height={6} fill="#0a0a0a" opacity={0.45} />
                <Rect x="103" y="131" width="1.5" height={6} fill="#0a0a0a" opacity={0.45} />
                <Rect x="110" y="131" width="1.5" height={6} fill="#0a0a0a" opacity={0.45} />
                <AnimatedRect x={90 as any} y={Animated.add(new Animated.Value(131), Animated.subtract(innerMouthH as any, tongueH as any)) as any} width={20 as any} height={tongueH as any} rx={6 as any} fill="#c96b7c" opacity={0.9} />
                {/* Antenna (smaller) */}
                <Rect x="98.5" y="24" width="3" height="16" rx="1.5" fill={theme.color.primaryLight as any} />
                <Circle cx="100" cy="20" r="5" fill={theme.color.primaryLight as any} />
              </Svg>
            </View>

            <Text style={{
              color: theme.color.foreground,
              fontSize: 16,
              textAlign: 'center',
              lineHeight: 22,
              marginTop: 4,
              marginBottom: 16
            }}>
              {typedMsg}
            </Text>

            {/* Link mock (match ShareAnywhere) */}
            <TouchableOpacity onPress={handleShareLink} activeOpacity={0.8} style={{
              marginTop: 0,
              paddingVertical: 12,
              paddingHorizontal: 16,
              borderRadius: 12,
              backgroundColor: theme.color.accent,
              borderWidth: 1,
              borderColor: theme.color.border,
              flexDirection: 'row',
              alignItems: 'center',
              gap: 10,
              ...(Platform.OS === 'ios'
                ? { shadowColor: (theme.shadow.md.ios.color as any), shadowOpacity: theme.shadow.md.ios.opacity, shadowRadius: theme.shadow.md.ios.radius, shadowOffset: { width: 0, height: theme.shadow.md.ios.offsetY } }
                : { elevation: theme.shadow.md.androidElevation }
              )
            }}>
              <Text style={{ color: theme.color.cardForeground, fontWeight: '600' }} numberOfLines={1}>{link}</Text>
              <Copy size={18} color={theme.color.cardForeground as any} />
            </TouchableOpacity>

            <View style={{ marginTop: 20, alignSelf: 'stretch', marginHorizontal: -24 }}>
              <Button title={t('onboarding.shareAnywhere.cta')} size="lg" variant="hero" onPress={() => { setStep('form'); onClose() }} fullWidth />
            </View>
          </View>
        )}
      </ScrollView>
    </Modal>
  )
}
