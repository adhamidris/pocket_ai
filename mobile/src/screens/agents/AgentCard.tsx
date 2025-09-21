import React from 'react'
import { View, Text, TouchableOpacity, Alert } from 'react-native'
import { useTheme } from '../../providers/ThemeProvider'
import { Card } from '../../components/ui/Card'
import { AnimatedCard } from '../../components/ui/AnimatedCard'
import { Badge } from '../../components/ui/Badge'
import { Button } from '../../components/ui/Button'
import { 
  Bot, 
  Power, 
  Settings,
  Trash2
} from 'lucide-react-native'

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
  role?: string
  roles?: string[]
  openCases?: number
  resolvedCases?: number
  satisfaction?: number
}

interface AgentCardProps {
  agent: Agent
  onToggleStatus: (id: string) => void
  onEdit: (agent: Agent) => void
  onDelete: (id: string) => void
  onPress?: (agent: Agent) => void
}

export const AgentCard: React.FC<AgentCardProps> = ({ 
  agent, 
  onToggleStatus, 
  onEdit, 
  onDelete,
  onPress,
}) => {
  const { theme } = useTheme()

  const getPersonaIcon = (persona: string) => {
    switch (persona) {
      case 'friendly': return '😊'
      case 'technical': return '🔧'
      case 'sales': return '💼'
      default: return '👔'
    }
  }

  // Department color no longer used after removing department/tools row

  const withAlpha = (c: string, a: number) =>
    c.startsWith('hsl(')
      ? c.replace('hsl(', 'hsla(').replace(')', `,${a})`)
      : c
  const dangerBg = withAlpha(theme.color.error, theme.dark ? 0.22 : 0.12)
  const hasRole = Boolean(agent.role || (agent.roles && agent.roles.length > 0))
  const hasDescription = Boolean((agent.description || '').trim().length > 0)

  const CardContainer: React.FC<{ children: React.ReactNode }> = ({ children }) => (
    <AnimatedCard
      variant="flat"
      onPress={onPress ? () => onPress(agent) : undefined}
      animationType="fadeIn"
      style={{ marginBottom: 12, backgroundColor: theme.dark ? (theme.color.secondary as any) : (theme.color.accent as any), paddingHorizontal: 16, paddingVertical: 14, borderWidth: 0, borderColor: 'transparent' }}
    >
      {children}
    </AnimatedCard>
  )

  return (
    <CardContainer>
      {/* Header */}
      <View style={{
        flexDirection: 'row',
        justifyContent: 'flex-start',
        alignItems: 'flex-start',
        marginBottom: (hasRole || hasDescription) ? 8 : 4
      }}>
        <View style={{ flex: 1, marginRight: 12 }}>
          <View style={{ flexDirection: 'row', alignItems: 'center', gap: 8, marginBottom: 2 }}>
            <View style={{
              width: 32,
              height: 32,
              backgroundColor: theme.dark ? theme.color.secondary : theme.color.card,
              borderRadius: 16,
              alignItems: 'center',
              justifyContent: 'center'
            }}>
              <Bot size={18} color={theme.color.primary as any} />
            </View>
            <View style={{ flex: 1, minWidth: 0 }}>
              <View style={{ flexDirection: 'row', alignItems: 'center', gap: 6 }}>
                <Text style={{
                  color: theme.color.cardForeground,
                  fontSize: 18,
                  fontWeight: '600',
                  flex: 1
                }} numberOfLines={1}>
                  {agent.name}
                </Text>
                <View style={{
                  flexDirection: 'row',
                  alignItems: 'center',
                  gap: 6,
                  backgroundColor: theme.dark ? theme.color.secondary : theme.color.card,
                  borderRadius: theme.radius.sm,
                  paddingHorizontal: 8,
                  paddingVertical: 3
                }}>
                  <View style={{
                    width: 6,
                    height: 6,
                    borderRadius: 3,
                    backgroundColor: agent.status === 'active' ? theme.color.success : theme.color.mutedForeground
                  }} />
                  <Text style={{ color: theme.color.mutedForeground, fontSize: 12, fontWeight: '600' }}>
                    {agent.status}
                  </Text>
                </View>
              </View>
              {hasRole && (
                <Text style={{ color: theme.color.mutedForeground, fontSize: 12, marginTop: 2 }} numberOfLines={1}>
                  {agent.role || (agent.roles || []).slice(0, 2).join(' • ')}
                </Text>
              )}
            </View>
          </View>

        {hasDescription && (
          <Text style={{
            color: theme.color.mutedForeground,
            fontSize: 14,
            marginTop: hasRole ? 2 : 0,
            marginBottom: 8
          }}>
              {agent.description}
            </Text>
        )}

        </View>
      </View>

      {/* Analytics (compact mini-summary; only for active agents) */}
      {agent.status === 'active' && (
        <View style={{
          flexDirection: 'row',
          justifyContent: 'space-between',
          alignItems: 'center',
          marginBottom: 8,
          paddingTop: 6,
        }}>
          {/* Chats */}
          <View style={{ alignItems: 'center', flex: 1 }}>
            <View style={{ alignItems: 'center', marginBottom: 2 }}>
              <Text style={{ color: theme.color.cardForeground, fontSize: 15, fontWeight: '600' }}>
                {agent.conversations}
              </Text>
            </View>
            <Text style={{ color: theme.color.mutedForeground, fontSize: 11 }}>Chats</Text>
          </View>

          {/* CSAT */}
          <View style={{ alignItems: 'center', flex: 1 }}>
            <View style={{ alignItems: 'center', marginBottom: 2 }}>
              <Text style={{ color: theme.color.cardForeground, fontSize: 15, fontWeight: '600' }}>
                {(agent.satisfaction ?? 0)}%
              </Text>
            </View>
            <Text style={{ color: theme.color.mutedForeground, fontSize: 11 }}>CSAT</Text>
          </View>
        </View>
      )}

      {/* Actions */}
      <View style={{ flexDirection: 'row', alignItems: 'center', gap: 8 }}>
        {/* Left actions */}
        <Button
          title={agent.status === 'active' ? 'Deactivate' : 'Activate'}
          variant={agent.status === 'active' ? 'dangerSoft' : 'default'}
          size="xs"
          iconLeft={
            <Power size={14} color={agent.status === 'active' ? (theme.color.error as any) : '#fff'} />
          }
          accessibilityLabel={`${agent.status === 'active' ? 'Deactivate' : 'Activate'} agent ${agent.name}`}
          onPress={() => {
            if (agent.status === 'active') {
              Alert.alert(
                'Deactivate agent?',
                `This agent can be re-activated later.`,
                [
                  { text: 'Cancel', style: 'cancel' },
                  { text: 'Deactivate', style: 'destructive', onPress: () => onToggleStatus(agent.id) }
                ]
              )
            } else {
              onToggleStatus(agent.id)
            }
          }}
        />
        <Button
          title="Edit"
          variant="secondary"
          size="xs"
          iconLeft={<Settings size={14} color={theme.color.foreground as any} />}
          onPress={() => onEdit(agent)}
        />
        {/* Spacer to push delete to the far right */}
        <View style={{ flex: 1 }} />
        {/* Delete on the very right */}
        <TouchableOpacity
          onPress={() => onDelete(agent.id)}
          hitSlop={{ top: 6, bottom: 6, left: 6, right: 6 }}
          style={{
            width: 32,
            height: 32,
            borderRadius: 16,
            backgroundColor: dangerBg as any,
            alignItems: 'center',
            justifyContent: 'center'
          }}
          accessibilityLabel={`Delete agent ${agent.name}`}
        >
          <Trash2 size={14} color={theme.color.error as any} />
        </TouchableOpacity>
      </View>
    </CardContainer>
  )
}
