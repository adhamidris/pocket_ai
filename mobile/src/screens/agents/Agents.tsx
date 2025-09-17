import React, { useState } from 'react'
import { View, Text, SafeAreaView, ScrollView, TouchableOpacity, Alert } from 'react-native'
import { useSafeAreaInsets } from 'react-native-safe-area-context'
import { useTranslation } from 'react-i18next'
import { useTheme } from '../../providers/ThemeProvider'
import { Card } from '../../components/ui/Card'
import { Button } from '../../components/ui/Button'
import { Bot, Plus } from 'lucide-react-native'
import { CreateAgent } from './CreateAgent'
import { AgentCard } from './AgentCard'

interface Agent {
  id: string
  name: string
  description: string
  status: 'active' | 'inactive'
  persona: string
  department: string
  conversations: number
  responseTime: string
  tools: string[]
  createdAt: string
}

export const AgentsScreen: React.FC = () => {
  const { t } = useTranslation()
  const { theme } = useTheme()
  const insets = useSafeAreaInsets()
  const [activeTab, setActiveTab] = useState<'active' | 'inactive'>('active')
  const [showCreateModal, setShowCreateModal] = useState(false)
  const [agents, setAgents] = useState<Agent[]>([])

  const tabs = [
    { key: 'active' as const, label: t('agents.active') },
    { key: 'inactive' as const, label: t('agents.inactive') },
  ]

  const filteredAgents = agents.filter(agent => agent.status === activeTab)

  const handleCreateAgent = (newAgent: Agent) => {
    setAgents(prev => [...prev, newAgent])
  }

  const handleToggleStatus = (agentId: string) => {
    setAgents(prev => prev.map(agent => 
      agent.id === agentId 
        ? { ...agent, status: agent.status === 'active' ? 'inactive' : 'active' as 'active' | 'inactive' }
        : agent
    ))
  }

  const handleEditAgent = (agent: Agent) => {
    // TODO: Implement edit functionality
    Alert.alert('Edit Agent', `Edit functionality for ${agent.name} coming soon!`)
  }

  const handleDeleteAgent = (agentId: string) => {
    Alert.alert(
      'Delete Agent',
      'Are you sure you want to delete this agent? This action cannot be undone.',
      [
        { text: 'Cancel', style: 'cancel' },
        { 
          text: 'Delete', 
          style: 'destructive',
          onPress: () => setAgents(prev => prev.filter(a => a.id !== agentId))
        }
      ]
    )
  }

  return (
    <SafeAreaView style={{ flex: 1, backgroundColor: theme.color.background }}>
      <View style={{ flex: 1 }}>
        {/* Header */}
        <View style={{ paddingHorizontal: 24, paddingTop: 12, paddingBottom: 16 }}>
          <View style={{ flexDirection: 'row', justifyContent: 'space-between', alignItems: 'center', marginBottom: 16 }}>
            <Text style={{
              color: theme.color.foreground,
              fontSize: 32,
              fontWeight: '700'
            }}>
              {t('agents.title')}
            </Text>
            <TouchableOpacity 
              onPress={() => setShowCreateModal(true)}
              style={{
                width: 44,
                height: 44,
                backgroundColor: theme.color.card,
                borderRadius: 22,
                alignItems: 'center',
                justifyContent: 'center'
              }}
            >
              <Plus color={theme.color.cardForeground as any} size={20} />
            </TouchableOpacity>
          </View>

          {/* Stats */}
          <View style={{ flexDirection: 'row', gap: 12 }}>
            <View style={{
              backgroundColor: theme.color.card,
              borderRadius: theme.radius.md,
              paddingHorizontal: 14,
              paddingVertical: 10,
              flex: 1
            }}>
              <Text style={{
                color: theme.color.cardForeground,
                fontSize: 20,
                fontWeight: '700',
                textAlign: 'center'
              }} numberOfLines={1} ellipsizeMode="clip" allowFontScaling={false}>
                {agents.filter(a => a.status === 'active').length}
              </Text>
              <Text style={{
                color: theme.color.mutedForeground,
                fontSize: 12,
                textAlign: 'center',
                flexShrink: 1
              }} numberOfLines={1} ellipsizeMode="clip" allowFontScaling={false}>
                Active
              </Text>
            </View>
            <View style={{
              backgroundColor: theme.color.card,
              borderRadius: theme.radius.md,
              paddingHorizontal: 14,
              paddingVertical: 10,
              flex: 1
            }}>
              <Text style={{
                color: theme.color.cardForeground,
                fontSize: 20,
                fontWeight: '700',
                textAlign: 'center'
              }} numberOfLines={1} ellipsizeMode="clip" allowFontScaling={false}>
                {agents.filter(a => a.status === 'inactive').length}
              </Text>
              <Text style={{
                color: theme.color.mutedForeground,
                fontSize: 12,
                textAlign: 'center',
                flexShrink: 1
              }} numberOfLines={1} ellipsizeMode="clip" allowFontScaling={false}>
                Inactive
              </Text>
            </View>
          </View>
        </View>

        {/* Tabs */}
        <View style={{
          flexDirection: 'row',
          paddingHorizontal: 24,
          marginBottom: 12
        }}>
          {tabs.map((tab) => (
            <TouchableOpacity
              key={tab.key}
              onPress={() => setActiveTab(tab.key)}
              style={{
                flex: 1,
                paddingVertical: 12,
                paddingHorizontal: 16,
                borderRadius: theme.radius.md,
                backgroundColor: activeTab === tab.key
                  ? (theme.color.primary as any)
                  : (theme.dark ? theme.color.secondary : theme.color.accent),
                borderWidth: 0,
                borderColor: 'transparent',
                marginRight: tab.key !== 'inactive' ? 8 : 0
              }}
            >
              <Text style={{
                color: activeTab === tab.key
                  ? ('#ffffff' as any)
                  : (theme.color.mutedForeground as any),
                textAlign: 'center',
                fontWeight: '700',
                fontSize: 13
              }}>
                {tab.label}
              </Text>
            </TouchableOpacity>
          ))}
        </View>

        {/* Content */}
        <ScrollView style={{ flex: 1, paddingHorizontal: 24 }}>
          {filteredAgents.length > 0 ? (
            filteredAgents.map((agent) => (
              <AgentCard
                key={agent.id}
                agent={agent}
                onToggleStatus={handleToggleStatus}
                onEdit={handleEditAgent}
                onDelete={handleDeleteAgent}
              />
            ))
          ) : (
            <Card variant="flat" style={{ backgroundColor: theme.dark ? theme.color.secondary : theme.color.accent }}>
              <View style={{ alignItems: 'center', padding: 20 }}>
                <View style={{
                  width: 60,
                  height: 60,
                  backgroundColor: theme.color.muted,
                  borderRadius: 30,
                  alignItems: 'center',
                  justifyContent: 'center',
                  marginBottom: 16
                }}>
                  <Bot color={theme.color.mutedForeground} size={28} />
                </View>
                <Text style={{
                  color: theme.color.mutedForeground,
                  textAlign: 'center',
                  fontStyle: 'italic',
                  marginBottom: 20
                }}>
                  {agents.length === 0 ? t('agents.noAgents') : `No ${activeTab} agents`}
                </Text>
                <Button
                  title={t('agents.createAgent')}
                  onPress={() => setShowCreateModal(true)}
                  variant="default"
                />
              </View>
            </Card>
          )}
        </ScrollView>

        {/* Create Agent Modal */}
        <CreateAgent
          visible={showCreateModal}
          onClose={() => setShowCreateModal(false)}
          onSave={handleCreateAgent}
        />
      </View>
    </SafeAreaView>
  )
}
