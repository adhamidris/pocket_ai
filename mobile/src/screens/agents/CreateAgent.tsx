import React, { useState, useRef } from 'react'
import { View, Text, ScrollView, Alert, findNodeHandle } from 'react-native'
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
    role: '',
    tone: '',
    traits: [] as string[],
    escalationRule: '',
    tools: [] as string[]
  })

  // Smart auto-center on selection
  const scrollRef = useRef<ScrollView>(null)
  const sectionRefs: Record<'basic'|'role'|'tone'|'traits'|'escalation'|'tools', React.RefObject<View>> = {
    basic: useRef<View>(null as any),
    role: useRef<View>(null as any),
    tone: useRef<View>(null as any),
    traits: useRef<View>(null as any),
    escalation: useRef<View>(null as any),
    tools: useRef<View>(null as any),
  }

  const scrollSectionToCenter = (ref: React.RefObject<View>) => {
    const scrollView = scrollRef.current
    const node = ref.current
    if (!scrollView || !node) return
    node.measure((x, y, width, height, pageX, pageY) => {
      // Measure ScrollView window box
      // @ts-ignore measureInWindow is available at runtime
      scrollView.measureInWindow?.((svX: number, svY: number, svW: number, svH: number) => {
        const sectionTop = pageY
        const sectionBottom = pageY + height
        const sectionCenter = pageY + height / 2
        const viewTop = svY
        const viewBottom = svY + svH
        const bandTop = viewTop + svH * 0.35
        const bandBottom = viewBottom - svH * 0.35
        const fullyVisible = sectionTop >= viewTop && sectionBottom <= viewBottom
        const centerInBand = sectionCenter >= bandTop && sectionCenter <= bandBottom
        if (fullyVisible && centerInBand) return
        const targetY = Math.max(0, sectionCenter - svH / 2)
        scrollView.scrollTo({ y: targetY, animated: true })
      })
    })
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
      role: formData.role,
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
      tools: []
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
      <ScrollView ref={scrollRef} showsVerticalScrollIndicator={false} contentContainerStyle={{ paddingHorizontal: 12, paddingBottom: 20 }}>
        {/* Agent Name */}
        <View ref={sectionRefs.basic} style={{ marginBottom: 16 }}>
          <Text style={{ color: theme.color.cardForeground, fontSize: 16, fontWeight: '600', marginBottom: 16 }}>Agent Name</Text>
          <Input
            value={formData.name}
            onChangeText={(text) => setFormData(prev => ({ ...prev, name: text }))}
            placeholder="e.g. Nancy Support Agent"
            icon={<Bot size={20} color={theme.color.mutedForeground} />}
          />
          
          {/* Description removed */}
        </View>

        <View style={{ height: 1, backgroundColor: theme.color.border, marginBottom: 16 }} />
        {/* Role */}
        <View ref={sectionRefs.role} style={{ marginBottom: 16 }}>
          <Text style={{ color: theme.color.cardForeground, fontSize: 16, fontWeight: '600', marginBottom: 16 }}>
            Role
          </Text>
          <View style={{ flexDirection: 'row', flexWrap: 'wrap' }}>
            {roles.map((r) => (
              <View key={r} style={{ marginRight: 8, marginBottom: 8 }}>
                <Button
                  title={r}
                  variant={formData.role === r ? 'default' : 'outline'}
                  size="sm"
                  onPress={() => { setFormData(prev => ({ ...prev, role: r })); scrollSectionToCenter(sectionRefs.role) }}
                />
              </View>
            ))}
          </View>
        </View>

        <View style={{ height: 1, backgroundColor: theme.color.border, marginBottom: 16 }} />
        {/* Tone */}
        <View ref={sectionRefs.tone} style={{ marginBottom: 16 }}>
          <Text style={{ color: theme.color.cardForeground, fontSize: 16, fontWeight: '600', marginBottom: 16 }}>
            Tone
          </Text>
          <View style={{ flexDirection: 'row', flexWrap: 'wrap' }}>
            {tones.map((t) => (
              <View key={t} style={{ marginRight: 8, marginBottom: 8 }}>
                <Button
                  title={t}
                  variant={formData.tone === t ? 'default' : 'outline'}
                  size="sm"
                  onPress={() => { setFormData(prev => ({ ...prev, tone: t })); scrollSectionToCenter(sectionRefs.tone) }}
                />
              </View>
            ))}
          </View>
        </View>

        <View style={{ height: 1, backgroundColor: theme.color.border, marginBottom: 16 }} />
        {/* Traits */}
        <View ref={sectionRefs.traits} style={{ marginBottom: 16 }}>
          <Text style={{ color: theme.color.cardForeground, fontSize: 16, fontWeight: '600', marginBottom: 16 }}>
            Traits
          </Text>
          <View style={{ flexDirection: 'row', flexWrap: 'wrap', alignItems: 'center' }}>
            {traitOptions.map((opt) => {
              const selected = formData.traits.includes(opt)
              return (
                <View key={opt} style={{ marginRight: 8, marginBottom: 8 }}>
                  <Button
                    title={opt}
                    variant={selected ? 'default' : 'outline'}
                    size="sm"
                    onPress={() => { setFormData(prev => ({
                      ...prev,
                      traits: selected ? prev.traits.filter(x => x !== opt) : [...prev.traits, opt]
                    })); scrollSectionToCenter(sectionRefs.traits) }}
                  />
                </View>
              )
            })}
          </View>
        </View>

        <View style={{ height: 1, backgroundColor: theme.color.border, marginBottom: 16 }} />
        {/* Escalation Rules */}
        <View ref={sectionRefs.escalation} style={{ marginBottom: 16 }}>
          <Text style={{ color: theme.color.cardForeground, fontSize: 16, fontWeight: '600', marginBottom: 16 }}>
            Escalation rules
          </Text>
          <View style={{ flexDirection: 'row', flexWrap: 'wrap' }}>
            {escalationOptions.map((opt) => (
              <View key={opt} style={{ marginRight: 8, marginBottom: 8 }}>
                <Button
                  title={opt}
                  variant={formData.escalationRule === opt ? 'default' : 'outline'}
                  size="sm"
                  onPress={() => { setFormData(prev => ({ ...prev, escalationRule: opt })); scrollSectionToCenter(sectionRefs.escalation) }}
                />
              </View>
            ))}
          </View>
        </View>

        
        {/* Action Buttons */}
        <View style={{ gap: 12, marginTop: 20 }}>
          <Button
            title="Create Agent"
            onPress={handleSave}
            variant="premium"
            size="lg"
          />
          <Button
            title={t('common.cancel')}
            onPress={onClose}
            variant="ghost"
            size="lg"
          />
        </View>
      </ScrollView>
    </Modal>
  )
}
