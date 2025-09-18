import React from 'react'
import { View, Text } from 'react-native'
import { Modal } from '../../components/ui/Modal'
import { useTheme } from '../../providers/ThemeProvider'
import { Button } from '../../components/ui/Button'
import { Bot, MessageCircle, ClipboardList, CheckCircle2, Star } from 'lucide-react-native'

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
  if (!agent) return null

  const name = agent.name
  const roleText = agent.role || (agent.roles || []).slice(0, 2).join(' • ')
  const stats = [
    { key: 'chats', label: 'Chats', value: agent.conversations ?? 0, icon: MessageCircle, color: theme.color.primary },
    { key: 'open', label: 'Open', value: agent.openCases ?? 0, icon: ClipboardList, color: theme.color.warning },
    { key: 'resolved', label: 'Resolved', value: agent.resolvedCases ?? 0, icon: CheckCircle2, color: theme.color.success },
    { key: 'csat', label: 'Satisfaction', value: (agent.satisfaction ?? 0) + '%', icon: Star, color: theme.color.warning },
  ]

  return (
    <Modal visible={visible} onClose={onClose} title={name} size="lg" autoHeight>
      <View style={{ gap: 16 }}>
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

        {/* Description */}
        {agent.description ? (
          <View>
            <Text style={{ color: theme.color.cardForeground, fontSize: 14, fontWeight: '600', marginBottom: 6 }}>About</Text>
            <Text style={{ color: theme.color.mutedForeground, fontSize: 14, lineHeight: 20 }}>{agent.description}</Text>
          </View>
        ) : null}

        {/* Actions */}
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

