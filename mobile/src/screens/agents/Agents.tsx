import React, { useState } from 'react'
import { View, Text, ScrollView, TouchableOpacity, Alert } from 'react-native'
import { SafeAreaView, useSafeAreaInsets } from 'react-native-safe-area-context'
import { useTranslation } from 'react-i18next'
import { useTheme } from '../../providers/ThemeProvider'
import { Card } from '../../components/ui/Card'
import { Button } from '../../components/ui/Button'
import { Plus } from 'lucide-react-native'
import { CreateAgent } from './CreateAgent'
import { AgentCard } from './AgentCard'
import { AgentDetail } from './AgentDetail'

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
  const [selectedAgent, setSelectedAgent] = useState<Agent | null>(null)
  const [showAgentDetail, setShowAgentDetail] = useState(false)
  const [analyticsAgentId, setAnalyticsAgentId] = useState<string | 'all'>('all')
  

  const tabs = [
    { key: 'active' as const, label: 'Agents' },
    { key: 'inactive' as const, label: 'Analytics' },
  ]
  const tabLabelMap: Record<'active'|'inactive', string> = {
    active: 'Agents',
    inactive: 'Analytics',
  }

  // Agents tab shows all agents; Analytics tab is separate
  const filteredAgents = activeTab === 'active'
    ? agents
    : agents.filter(agent => agent.status === 'inactive')

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
    Alert.alert('Edit Agent', `Edit functionality for ${agent.name} coming soon!`)
  }

  const handleUpdateAgent = (id: string, patch: Partial<Agent & { roles: string[]; role?: string; tone?: string; traits?: string[]; escalationRule?: string }>) => {
    setAgents(prev => prev.map(a => a.id === id ? { ...a, ...patch } as any : a))
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

          {/* Removed top stats per request */}
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
          {activeTab === 'inactive' ? (
            // Analytics view (aggregated across agents)
            (() => {
              const sourceAgents = analyticsAgentId === 'all' ? agents : agents.filter(a => a.id === analyticsAgentId)
              const sum = (arr: any[], key: string) => arr.reduce((acc: number, a: any) => acc + (typeof a?.[key] === 'number' ? a[key] : 0), 0)
              const totalConversations = sum(sourceAgents, 'conversations')
              const totalResolved = sum(sourceAgents as any, 'resolvedCases')
              const totalOpen = sum(sourceAgents as any, 'openCases')
              // average satisfaction across agents that have it
              const sats: number[] = (sourceAgents as any).map((a: any) => typeof a?.satisfaction === 'number' ? a.satisfaction : null).filter((x: any) => typeof x === 'number')
              const avgSatisfaction = sats.length > 0 ? Math.round(sats.reduce((a, b) => a + b, 0) / sats.length) : null
              // avg response time in seconds (parseFloat of numbers in the string)
              const times = sourceAgents.map(a => {
                const m = (a.responseTime || '').toString().match(/\d+(?:\.\d+)?/)
                return m ? parseFloat(m[0]) : null
              }).filter(x => typeof x === 'number') as number[]
              const avgResp = times.length > 0 ? `${(times.reduce((a, b) => a + b, 0) / times.length).toFixed(1)}s` : '—'

              return (
                <Card variant="flat" style={{ paddingHorizontal: 16, paddingBottom: 16, paddingTop: 8, marginBottom: 12 }}>
                  <View style={{ gap: 12 }}>
                    <Text style={{ color: theme.color.cardForeground, fontSize: 16, fontWeight: '700' }}>Analytics</Text>
                    {agents.length > 1 && (
                      <ScrollView
                        horizontal
                        showsHorizontalScrollIndicator={false}
                        contentContainerStyle={{ gap: 6 }}
                        style={{ marginBottom: 4 }}
                      >
                        <TouchableOpacity
                          onPress={() => setAnalyticsAgentId('all')}
                          activeOpacity={0.85}
                          style={{ backgroundColor: analyticsAgentId === 'all' ? (theme.color.card as any) : (theme.dark ? theme.color.secondary : theme.color.accent), borderRadius: theme.radius.sm, paddingHorizontal: 10, paddingVertical: 6 }}
                        >
                          <Text style={{ color: analyticsAgentId === 'all' ? (theme.color.primary as any) : (theme.color.mutedForeground as any), fontSize: 12, fontWeight: '700' }}>All Agents</Text>
                        </TouchableOpacity>
                        {agents.map(a => (
                          <TouchableOpacity
                            key={`flt-${a.id}`}
                            onPress={() => setAnalyticsAgentId(a.id)}
                            activeOpacity={0.85}
                            style={{ backgroundColor: analyticsAgentId === a.id ? (theme.color.card as any) : (theme.dark ? theme.color.secondary : theme.color.accent), borderRadius: theme.radius.sm, paddingHorizontal: 10, paddingVertical: 6 }}
                          >
                            <Text style={{ color: analyticsAgentId === a.id ? (theme.color.primary as any) : (theme.color.mutedForeground as any), fontSize: 12, fontWeight: '700' }}>{a.name}</Text>
                          </TouchableOpacity>
                        ))}
                      </ScrollView>
                    )}
                    <View style={{ flexDirection: 'row', flexWrap: 'wrap', gap: 8 }}>
                      {/* Conversations */}
                      <View style={{ flexBasis: '48%', flexGrow: 1 }}>
                        <View style={{ backgroundColor: theme.dark ? theme.color.secondary : theme.color.accent, borderRadius: theme.radius.md, paddingHorizontal: 14, paddingVertical: 12 }}>
                          <Text style={{ color: theme.color.mutedForeground, fontSize: 11, fontWeight: '600', marginBottom: 6 }}>Conversations</Text>
                          <Text style={{ color: theme.color.cardForeground, fontSize: 20, fontWeight: '700' }}>{totalConversations}</Text>
                        </View>
                      </View>
                      {/* Resolved */}
                      <View style={{ flexBasis: '48%', flexGrow: 1 }}>
                        <View style={{ backgroundColor: theme.dark ? theme.color.secondary : theme.color.accent, borderRadius: theme.radius.md, paddingHorizontal: 14, paddingVertical: 12 }}>
                          <Text style={{ color: theme.color.mutedForeground, fontSize: 11, fontWeight: '600', marginBottom: 6 }}>Resolved Cases</Text>
                          <Text style={{ color: theme.color.cardForeground, fontSize: 20, fontWeight: '700' }}>{totalResolved}</Text>
                        </View>
                      </View>
                      {/* Unresolved */}
                      <View style={{ flexBasis: '48%', flexGrow: 1 }}>
                        <View style={{ backgroundColor: theme.dark ? theme.color.secondary : theme.color.accent, borderRadius: theme.radius.md, paddingHorizontal: 14, paddingVertical: 12 }}>
                          <Text style={{ color: theme.color.mutedForeground, fontSize: 11, fontWeight: '600', marginBottom: 6 }}>Unresolved Cases</Text>
                          <Text style={{ color: theme.color.cardForeground, fontSize: 20, fontWeight: '700' }}>{totalOpen}</Text>
                        </View>
                      </View>
                      {/* Satisfaction */}
                      <View style={{ flexBasis: '48%', flexGrow: 1 }}>
                        <View style={{ backgroundColor: theme.dark ? theme.color.secondary : theme.color.accent, borderRadius: theme.radius.md, paddingHorizontal: 14, paddingVertical: 12 }}>
                          <Text style={{ color: theme.color.mutedForeground, fontSize: 11, fontWeight: '600', marginBottom: 6 }}>Satisfaction</Text>
                          <Text style={{ color: theme.color.cardForeground, fontSize: 20, fontWeight: '700', marginBottom: 8 }}>{avgSatisfaction !== null ? `${avgSatisfaction}%` : '—'}</Text>
                          <View style={{ height: 6, backgroundColor: theme.color.muted, borderRadius: 999, overflow: 'hidden' }}>
                            <View style={{ width: `${Math.max(0, Math.min(100, avgSatisfaction ?? 0))}%`, height: '100%', backgroundColor: theme.color.success }} />
                          </View>
                        </View>
                      </View>
                      {/* Avg response time */}
                      <View style={{ flexBasis: '100%', flexGrow: 1 }}>
                        <View style={{ backgroundColor: theme.dark ? theme.color.secondary : theme.color.accent, borderRadius: theme.radius.md, paddingHorizontal: 14, paddingVertical: 12 }}>
                          <Text style={{ color: theme.color.mutedForeground, fontSize: 11, fontWeight: '600', marginBottom: 6 }}>Avg Response Time</Text>
                          <Text style={{ color: theme.color.cardForeground, fontSize: 20, fontWeight: '700' }}>{avgResp}</Text>
                        </View>
                      </View>
                    </View>
                  </View>
                </Card>
              )
            })()
          ) : (
            // Agents list (active)
            filteredAgents.length > 0 ? (
              filteredAgents.map((agent) => (
                <AgentCard
                  key={agent.id}
                  agent={agent}
                  onToggleStatus={handleToggleStatus}
                  onEdit={handleEditAgent}
                  onDelete={handleDeleteAgent}
                  onPress={(a) => { setSelectedAgent(a as any); setShowAgentDetail(true) }}
                />
              ))
            ) : agents.some(a => a.status === 'inactive') ? (
              // Preview the first inactive agent as if active so users can quickly activate it here
              (() => {
                const inactive = agents.find(a => a.status === 'inactive')!
                const preview = { ...inactive, status: 'active' as const }
                return (
                  <AgentCard
                    key={`preview-${inactive.id}`}
                    agent={preview}
                    onToggleStatus={handleToggleStatus}
                    onEdit={handleEditAgent}
                    onDelete={handleDeleteAgent}
                    onPress={(a) => { setSelectedAgent(a as any); setShowAgentDetail(true) }}
                  />
                )
              })()
            ) : (
              <Card variant="flat" style={{ backgroundColor: theme.dark ? theme.color.secondary : theme.color.accent }}>
                <View style={{ alignItems: 'center', padding: 12 }}>
                  <Text style={{
                    color: theme.color.mutedForeground,
                    textAlign: 'center',
                    fontSize: 12,
                    marginBottom: 12,
                    marginTop: 2
                  }}>
                    {agents.length === 0 ? t('agents.noAgents') : `No ${tabLabelMap[activeTab]} data`}
                  </Text>
                  <Button
                    title={t('agents.createAgent')}
                    onPress={() => setShowCreateModal(true)}
                    variant="default"
                    size="sm"
                  />
                </View>
              </Card>
            )
          )}
        </ScrollView>

        {/* Create Agent Modal */}
        <CreateAgent
          visible={showCreateModal}
          onClose={() => setShowCreateModal(false)}
          onSave={handleCreateAgent}
        />

        {/* Agent Detail Modal */}
        <AgentDetail
          visible={showAgentDetail}
          agent={selectedAgent as any}
          onClose={() => { setShowAgentDetail(false); setSelectedAgent(null) }}
          onToggleStatus={(id) => { handleToggleStatus(id); setShowAgentDetail(false) }}
          onEdit={(id) => { const ag = agents.find(a => a.id === id); if (ag) handleEditAgent(ag) }}
          onUpdateAgent={handleUpdateAgent}
        />
      </View>
    </SafeAreaView>
  )
}
