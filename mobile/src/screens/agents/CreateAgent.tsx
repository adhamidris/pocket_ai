import React, { useState } from 'react'
import { View, Text, ScrollView, Alert } from 'react-native'
import { useTranslation } from 'react-i18next'
import { useTheme } from '../../providers/ThemeProvider'
import { Input } from '../../components/ui/Input'
import { Button } from '../../components/ui/Button'
import { Card } from '../../components/ui/Card'
import { Badge } from '../../components/ui/Badge'
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
    description: '',
    persona: 'professional',
    department: 'support',
    knowledgeBase: 'basic',
    tools: [] as string[]
  })

  const personas = [
    { key: 'professional', label: 'Professional', icon: User },
    { key: 'friendly', label: 'Friendly', icon: User },
    { key: 'technical', label: 'Technical Expert', icon: Settings },
    { key: 'sales', label: 'Sales Focused', icon: Zap }
  ]

  const departments = [
    { key: 'support', label: 'Customer Support' },
    { key: 'sales', label: 'Sales' },
    { key: 'technical', label: 'Technical Support' },
    { key: 'billing', label: 'Billing' }
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
      ...formData,
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
      description: '',
      persona: 'professional',
      department: 'support',
      knowledgeBase: 'basic',
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
      <ScrollView showsVerticalScrollIndicator={false}>
        {/* Basic Info */}
        <Card style={{ marginBottom: 20 }}>
          <Text style={{
            color: theme.color.cardForeground,
            fontSize: 16,
            fontWeight: '600',
            marginBottom: 16
          }}>
            Basic Information
          </Text>
          
          <Input
            label="Agent Name"
            value={formData.name}
            onChangeText={(text) => setFormData(prev => ({ ...prev, name: text }))}
            placeholder="e.g. Nancy Support Agent"
            icon={<Bot size={20} color={theme.color.mutedForeground} />}
          />
          
          <Input
            label="Description"
            value={formData.description}
            onChangeText={(text) => setFormData(prev => ({ ...prev, description: text }))}
            placeholder="Brief description of the agent's role"
            multiline
            numberOfLines={3}
            style={{ height: 80, textAlignVertical: 'top' }}
          />
        </Card>

        {/* Persona Selection */}
        <Card style={{ marginBottom: 20 }}>
          <Text style={{
            color: theme.color.cardForeground,
            fontSize: 16,
            fontWeight: '600',
            marginBottom: 16
          }}>
            Persona & Style
          </Text>
          
          <View style={{ flexDirection: 'row', flexWrap: 'wrap', gap: 8 }}>
            {personas.map((persona) => (
              <Button
                key={persona.key}
                title={persona.label}
                variant={formData.persona === persona.key ? 'default' : 'outline'}
                size="sm"
                onPress={() => setFormData(prev => ({ ...prev, persona: persona.key }))}
              />
            ))}
          </View>
        </Card>

        {/* Department */}
        <Card style={{ marginBottom: 20 }}>
          <Text style={{
            color: theme.color.cardForeground,
            fontSize: 16,
            fontWeight: '600',
            marginBottom: 16
          }}>
            Department
          </Text>
          
          <View style={{ flexDirection: 'row', flexWrap: 'wrap', gap: 8 }}>
            {departments.map((dept) => (
              <Button
                key={dept.key}
                title={dept.label}
                variant={formData.department === dept.key ? 'default' : 'outline'}
                size="sm"
                onPress={() => setFormData(prev => ({ ...prev, department: dept.key }))}
              />
            ))}
          </View>
        </Card>

        {/* Tools & Capabilities */}
        <Card style={{ marginBottom: 20 }}>
          <Text style={{
            color: theme.color.cardForeground,
            fontSize: 16,
            fontWeight: '600',
            marginBottom: 16
          }}>
            Tools & Capabilities
          </Text>
          
          <View style={{ gap: 12 }}>
            {availableTools.map((tool) => (
              <View
                key={tool.key}
                style={{
                  flexDirection: 'row',
                  justifyContent: 'space-between',
                  alignItems: 'center',
                  paddingVertical: 8
                }}
              >
                <Text style={{
                  color: theme.color.cardForeground,
                  fontSize: 14,
                  flex: 1
                }}>
                  {tool.label}
                </Text>
                <Button
                  title={formData.tools.includes(tool.key) ? 'Enabled' : 'Enable'}
                  variant={formData.tools.includes(tool.key) ? 'default' : 'outline'}
                  size="sm"
                  onPress={() => toggleTool(tool.key)}
                />
              </View>
            ))}
          </View>
        </Card>

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
