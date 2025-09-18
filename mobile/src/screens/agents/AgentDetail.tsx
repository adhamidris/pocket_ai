import React, { useState } from 'react'
import { View, Text, ScrollView, TouchableOpacity } from 'react-native'
import { Modal } from '../../components/ui/Modal'
import { useTheme } from '../../providers/ThemeProvider'
import { Button } from '../../components/ui/Button'
import { Bot, MessageCircle, ClipboardList, CheckCircle2, Star, Settings, BarChart3 } from 'lucide-react-native'

interface AgentDetailProps {
  visible: boolean
  agent: {
    id: string
    name: string
    description?: string
    status: 'active' | 'inactive'
    role?: string
    roles?: string[]
    conversations?: number
    openCases?: number
    resolvedCases?: number
    satisfaction?: number
    createdAt?: string
  } | null
  onClose: () => void
  onToggleStatus?: (id: string) => void
  onEdit?: (agentId: string) => void
}

export const AgentDetail: React.FC<AgentDetailProps> = ({ visible, agent, onClose, onToggleStatus, onEdit }) => {
  const { theme } = useTheme()
  const [activeTab, setActiveTab] = useState<'overview'|'performance'|'settings'>('overview')
  if (!agent) return null

  const name = agent.name
  const roleText = agent.role || (agent.roles || []).slice(0, 2).join(' • ')
  const stats = [
    { key: 'chats', label: 'Chats', value: agent.conversations ?? 0, icon: MessageCircle, color: theme.color.primary },
    { key: 'open', label: 'Open', value: agent.openCases ?? 0, icon: ClipboardList, color: theme.color.warning },
    { key: 'resolved', label: 'Resolved', value: agent.resolvedCases ?? 0, icon: CheckCircle2, color: theme.color.success },
    { key: 'csat', label: 'Satisfaction', value: (agent.satisfaction ?? 0) + '%', icon: Star, color: theme.color.warning },
  ]

  const tabs = [
    { key: 'overview' as const, label: 'Overview', icon: Bot },
    { key: 'performance' as const, label: 'Performance', icon: BarChart3 },
    { key: 'settings' as const, label: 'Settings', icon: Settings },
  ]

  return (
    <Modal visible={visible} onClose={onClose} title={name} size="lg" autoHeight={false}>
      <View style={{ gap: 12, flex: 1, minHeight: 0 }}>
        {/* Header: Avatar + name/role + status */}
        <View style={{ flexDirection: 'row', alignItems: 'center' }}>
          <View style={{ width: 44, height: 44, borderRadius: 22, backgroundColor: theme.dark ? theme.color.secondary : theme.color.card, alignItems: 'center', justifyContent: 'center', marginRight: 12 }}>
            <Bot size={22} color={theme.color.primary as any} />
          </View>
          <View style={{ flex: 1, minWidth: 0 }}>
            <Text style={{ color: theme.color.cardForeground, fontSize: 18, fontWeight: '700' }} numberOfLines={1}>{name}</Text>
            {roleText ? (
              <Text style={{ color: theme.color.mutedForeground, fontSize: 12 }} numberOfLines={1}>{roleText}</Text>
            ) : null}
          </View>
          <View style={{ flexDirection: 'row', alignItems: 'center', gap: 6, backgroundColor: theme.dark ? theme.color.secondary : theme.color.card, borderRadius: theme.radius.sm, paddingHorizontal: 8, paddingVertical: 4 }}>
            <View style={{ width: 6, height: 6, borderRadius: 3, backgroundColor: agent.status === 'active' ? theme.color.success : theme.color.mutedForeground }} />
            <Text style={{ color: theme.color.mutedForeground, fontSize: 12, fontWeight: '600' }}>{agent.status}</Text>
          </View>
        </View>

        {/* Tabs bar */}
        <View style={{ flexDirection: 'row', backgroundColor: theme.color.muted, borderRadius: theme.radius.md, padding: 6 }}>
          {tabs.map((tab) => (
            <TouchableOpacity
              key={tab.key}
              onPress={() => setActiveTab(tab.key)}
              style={{
                flex: 1,
                paddingVertical: 10,
                borderRadius: theme.radius.sm,
                backgroundColor: activeTab === tab.key ? theme.color.card : 'transparent'
              }}
            >
              <View style={{ alignItems: 'center', gap: 4 }}>
                <tab.icon size={18} color={activeTab === tab.key ? (theme.color.primary as any) : (theme.color.mutedForeground as any)} />
                <Text style={{ color: activeTab === tab.key ? (theme.color.primary as any) : (theme.color.mutedForeground as any), fontSize: 12, fontWeight: '700' }}>{tab.label}</Text>
              </View>
            </TouchableOpacity>
          ))}
        </View>

        {/* Scrollable content under tabs (does not overlap buttons) */}
        <View style={{ flex: 1, minHeight: 0 }}>
          <ScrollView showsVerticalScrollIndicator={false} contentContainerStyle={{ paddingBottom: 8 }}>
            {activeTab === 'overview' && (
              <View style={{ gap: 12 }}>
                {/* Analytics */}
                <View style={{ flexDirection: 'row', justifyContent: 'space-between', backgroundColor: theme.dark ? theme.color.secondary : theme.color.accent, borderRadius: theme.radius.md, padding: 12 }}>
                  {stats.map((s) => (
                    <View key={s.key} style={{ flex: 1, alignItems: 'center', paddingHorizontal: 6 }}>
                      <s.icon size={16} color={s.color as any} />
                      <Text style={{ color: theme.color.cardForeground, fontSize: 16, fontWeight: '700', marginTop: 4 }}>
                        {s.value}
                      </Text>
                      <Text style={{ color: theme.color.mutedForeground, fontSize: 12 }}>{s.label}</Text>
                    </View>
                  ))}
                </View>

                {/* About */}
                {agent.description ? (
                  <View>
                    <Text style={{ color: theme.color.cardForeground, fontSize: 14, fontWeight: '600', marginBottom: 6 }}>About</Text>
                    <Text style={{ color: theme.color.mutedForeground, fontSize: 14, lineHeight: 20 }}>{agent.description}</Text>
                  </View>
                ) : null}
              </View>
            )}

            {activeTab === 'performance' && (
              <View style={{ gap: 10 }}>
                <Text style={{ color: theme.color.cardForeground, fontSize: 14, fontWeight: '600' }}>Performance</Text>
                <Text style={{ color: theme.color.mutedForeground, fontSize: 13 }}>
                  Detailed KPIs coming soon. Current overview reflects core metrics.
                </Text>
              </View>
            )}

            {activeTab === 'settings' && (
              <View style={{ gap: 10 }}>
                <Text style={{ color: theme.color.cardForeground, fontSize: 14, fontWeight: '600' }}>Configuration</Text>
                <Text style={{ color: theme.color.mutedForeground, fontSize: 13 }}>
                  Edit agent settings from the main Agents page.
                </Text>
              </View>
            )}
          </ScrollView>
        </View>

        {/* Actions (sticky at bottom) */}
        <View style={{ flexDirection: 'row', gap: 12 }}>
          <View style={{ flex: 1 }}>
            <Button
              title={agent.status === 'active' ? 'Deactivate' : 'Activate'}
              variant={agent.status === 'active' ? 'danger' : 'default'}
              size="lg"
              fullWidth
              onPress={() => onToggleStatus?.(agent.id)}
            />
          </View>
          <View style={{ flex: 1 }}>
            <Button title="Edit" variant="secondary" size="lg" fullWidth onPress={() => onEdit?.(agent.id)} />
          </View>
        </View>

        <Button title="Close" variant="default" size="lg" fullWidth onPress={onClose} />
      </View>
    </Modal>
  )
}
