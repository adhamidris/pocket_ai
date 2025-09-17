import React, { useState, useRef } from 'react'
import { View, Text, ScrollView, Alert, findNodeHandle, TouchableOpacity } from 'react-native'
import { useTranslation } from 'react-i18next'
import { useTheme } from '../../providers/ThemeProvider'
import { Input } from '../../components/ui/Input'
import { Button } from '../../components/ui/Button'
import { Modal } from '../../components/ui/Modal'
import { Bot, User, Settings, BookOpen, Zap } from 'lucide-react-native'

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

  // Smart auto-center on selection
  const scrollRef = useRef<ScrollView>(null)
  type SectionKey = 'basic' | 'role' | 'tone' | 'traits' | 'escalation' | 'tools'
  const sectionRefs: Record<SectionKey, React.RefObject<View>> = {
    basic: useRef<View>(null as any),
    role: useRef<View>(null as any),
    tone: useRef<View>(null as any),
    traits: useRef<View>(null as any),
    escalation: useRef<View>(null as any),
    tools: useRef<View>(null as any),
  }
  const [viewportHeight, setViewportHeight] = useState(0)
  const [sectionLayouts, setSectionLayouts] = useState<Record<SectionKey, { y: number; height: number }>>({} as any)

  const setSectionLayout = (key: SectionKey, y: number, height: number) => {
    setSectionLayouts(prev => ({ ...prev, [key]: { y, height } }))
  }

  const scrollSectionToCenter = (key: SectionKey) => {
    const layout = sectionLayouts[key]
    if (!scrollRef.current || !layout || viewportHeight === 0) return
    const targetY = Math.max(0, layout.y + layout.height / 2 - viewportHeight / 2)
    scrollRef.current.scrollTo({ y: targetY, animated: true })
  }

  const centerAfterLayout = (key: SectionKey) => {
    // Defer to next frames to capture any reflow from chip wrapping
    requestAnimationFrame(() => requestAnimationFrame(() => scrollSectionToCenter(key)))
  }

  const roles = [
    'Support Agent',
    'Sales Associate',
    'Technical Specialist',
    'Billing Assistant'
  ]

  const tones = [
    'Friendly',
    'Professional',
    'Empathetic',
    'Concise',
    'Playful',
    'Formal'
  ]

  const traitOptions = [
    'Patient',
    'Proactive',
    'Detail-oriented',
    'Persuasive',
    'Analytical',
    'Creative'
  ]

  const escalationOptions = [
    'Never escalate',
    'Escalate on negative sentiment',
    'Escalate on SLA risk',
    'Always escalate complex'
  ]

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
      status: 'inactive',
      createdAt: new Date().toISOString(),
      conversations: 0,
      responseTime: '0s'
    }

    onSave(newAgent)
    onClose()
    
    // Reset form
    setFormData({
      name: '',
      role: '',
      tone: '',
      traits: [],
      escalationRule: '',
      tools: [],
      roles: []
    })
  }

  const toggleTool = (toolKey: string) => {
    setFormData(prev => ({
      ...prev,
      tools: prev.tools.includes(toolKey)
        ? prev.tools.filter(t => t !== toolKey)
        : [...prev.tools, toolKey]
    }))
  }

  return (
    <Modal visible={visible} onClose={onClose} title={t('agents.createAgent')} size="lg">
      <ScrollView
        ref={scrollRef}
        showsVerticalScrollIndicator={false}
        contentContainerStyle={{ paddingHorizontal: 12, paddingBottom: 20 }}
        onLayout={(e) => setViewportHeight(e.nativeEvent.layout.height)}
      >
        {/* Agent Name */}
        <View
          ref={sectionRefs.basic}
          style={{ marginBottom: 16 }}
          onLayout={(e) => setSectionLayout('basic', e.nativeEvent.layout.y, e.nativeEvent.layout.height)}
        >
          <Text style={{ color: theme.color.cardForeground, fontSize: 16, fontWeight: '600', marginBottom: 16 }}>Agent Name</Text>
          <Input
            value={formData.name}
            onChangeText={(text) => setFormData(prev => ({ ...prev, name: text }))}
            placeholder="e.g. Nancy"
            icon={<Bot size={20} color={theme.color.mutedForeground} />}
            borderless
            surface="accent"
          />
          <Text style={{ color: theme.color.mutedForeground, fontSize: 12, marginTop: 6 }}>
            Use a friendly, recognizable name.
          </Text>
          
          {/* Description removed */}
        </View>

        <View style={{ height: 1, backgroundColor: theme.color.border, marginBottom: 16 }} />
        {/* Role */}
        <View
          ref={sectionRefs.role}
          style={{ marginBottom: 16 }}
          onLayout={(e) => setSectionLayout('role', e.nativeEvent.layout.y, e.nativeEvent.layout.height)}
        >
          <Text style={{ color: theme.color.cardForeground, fontSize: 16, fontWeight: '600', marginBottom: 16 }}>
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
          <Text style={{ color: theme.color.mutedForeground, fontSize: 12, marginTop: 6 }}>
            You can select multiple titles for this agent, or create separate agents for different roles if needed.
          </Text>
        </View>

        <View style={{ height: 1, backgroundColor: theme.color.border, marginBottom: 16 }} />
        {/* Tone */}
        <View 
          ref={sectionRefs.tone} 
          style={{ marginBottom: 16 }}
          onLayout={(e) => setSectionLayout('tone', e.nativeEvent.layout.y, e.nativeEvent.layout.height)}
        >
          <Text style={{ color: theme.color.cardForeground, fontSize: 16, fontWeight: '600', marginBottom: 16 }}>
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
          <Text style={{ color: theme.color.mutedForeground, fontSize: 12, marginTop: 6 }}>
            Choose one tone for now — you can adjust this later.
          </Text>
        </View>

        <View style={{ height: 1, backgroundColor: theme.color.border, marginBottom: 16 }} />
        {/* Traits */}
        <View 
          ref={sectionRefs.traits} 
          style={{ marginBottom: 16 }}
          onLayout={(e) => setSectionLayout('traits', e.nativeEvent.layout.y, e.nativeEvent.layout.height)}
        >
          <Text style={{ color: theme.color.cardForeground, fontSize: 16, fontWeight: '600', marginBottom: 16 }}>
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
          <Text style={{ color: theme.color.mutedForeground, fontSize: 12, marginTop: 6 }}>
            Pick a few traits to guide the agent’s personality and style.
          </Text>
        </View>

        <View style={{ height: 1, backgroundColor: theme.color.border, marginBottom: 16 }} />
        {/* Escalation Rules */}
        <View 
          ref={sectionRefs.escalation} 
          style={{ marginBottom: 16 }}
          onLayout={(e) => setSectionLayout('escalation', e.nativeEvent.layout.y, e.nativeEvent.layout.height)}
        >
          <Text style={{ color: theme.color.cardForeground, fontSize: 16, fontWeight: '600', marginBottom: 16 }}>
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
          <Text style={{ color: theme.color.mutedForeground, fontSize: 12, marginTop: 6 }}>
            Set when to hand off to a human. You can refine this later.
          </Text>
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
      </ScrollView>
    </Modal>
  )
}
