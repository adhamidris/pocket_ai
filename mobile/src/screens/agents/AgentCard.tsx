import React from 'react'
import { View, Text, TouchableOpacity } from 'react-native'
import { useTheme } from '../../providers/ThemeProvider'
import { Card } from '../../components/ui/Card'
import { Badge } from '../../components/ui/Badge'
import { Button } from '../../components/ui/Button'
import { 
  Bot, 
  Power, 
  MoreHorizontal, 
  MessageCircle, 
  Clock,
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
}

interface AgentCardProps {
  agent: Agent
  onToggleStatus: (id: string) => void
  onEdit: (agent: Agent) => void
  onDelete: (id: string) => void
}

export const AgentCard: React.FC<AgentCardProps> = ({ 
  agent, 
  onToggleStatus, 
  onEdit, 
  onDelete 
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

  const getDepartmentColor = (dept: string) => {
    switch (dept) {
      case 'sales': return theme.color.success
      case 'technical': return theme.color.warning
      case 'billing': return theme.color.primary
      default: return theme.color.mutedForeground
    }
  }

  return (
    <Card style={{ marginBottom: 16 }}>
      {/* Header */}
      <View style={{
        flexDirection: 'row',
        justifyContent: 'space-between',
        alignItems: 'flex-start',
        marginBottom: 12
      }}>
        <View style={{ flex: 1, marginRight: 12 }}>
          <View style={{ flexDirection: 'row', alignItems: 'center', gap: 8, marginBottom: 4 }}>
            <View style={{
              width: 32,
              height: 32,
              backgroundColor: agent.status === 'active' 
                ? theme.color.primary + '20' 
                : theme.color.muted,
              borderRadius: 16,
              alignItems: 'center',
              justifyContent: 'center'
            }}>
              <Text style={{ fontSize: 16 }}>
                {getPersonaIcon(agent.persona)}
              </Text>
            </View>
            <Text style={{
              color: theme.color.cardForeground,
              fontSize: 18,
              fontWeight: '600',
              flex: 1
            }}>
              {agent.name}
            </Text>
            <Badge 
              variant={agent.status === 'active' ? 'success' : 'secondary'} 
              size="sm"
            >
              {agent.status}
            </Badge>
          </View>
          
          <Text style={{
            color: theme.color.mutedForeground,
            fontSize: 14,
            marginBottom: 8
          }}>
            {agent.description}
          </Text>
          
          <View style={{ flexDirection: 'row', alignItems: 'center', gap: 12 }}>
            <Text style={{
              color: getDepartmentColor(agent.department),
              fontSize: 12,
              fontWeight: '600',
              textTransform: 'uppercase'
            }}>
              {agent.department}
            </Text>
            <Text style={{
              color: theme.color.mutedForeground,
              fontSize: 12
            }}>
              {agent.tools.length} tools
            </Text>
          </View>
        </View>

        <TouchableOpacity
          style={{
            width: 32,
            height: 32,
            borderRadius: 16,
            backgroundColor: theme.color.muted,
            alignItems: 'center',
            justifyContent: 'center'
          }}
        >
          <MoreHorizontal size={16} color={theme.color.mutedForeground} />
        </TouchableOpacity>
      </View>

      {/* Stats */}
      <View style={{
        flexDirection: 'row',
        justifyContent: 'space-between',
        marginBottom: 16,
        paddingVertical: 12,
        borderTopWidth: 1,
        borderTopColor: theme.color.border
      }}>
        <View style={{ alignItems: 'center', flex: 1 }}>
          <View style={{ flexDirection: 'row', alignItems: 'center', gap: 4, marginBottom: 4 }}>
            <MessageCircle size={14} color={theme.color.primary} />
            <Text style={{
              color: theme.color.cardForeground,
              fontSize: 16,
              fontWeight: '600'
            }}>
              {agent.conversations}
            </Text>
          </View>
          <Text style={{
            color: theme.color.mutedForeground,
            fontSize: 12
          }}>
            Conversations
          </Text>
        </View>

        <View style={{ alignItems: 'center', flex: 1 }}>
          <View style={{ flexDirection: 'row', alignItems: 'center', gap: 4, marginBottom: 4 }}>
            <Clock size={14} color={theme.color.success} />
            <Text style={{
              color: theme.color.cardForeground,
              fontSize: 16,
              fontWeight: '600'
            }}>
              {agent.responseTime}
            </Text>
          </View>
          <Text style={{
            color: theme.color.mutedForeground,
            fontSize: 12
          }}>
            Response Time
          </Text>
        </View>
      </View>

      {/* Actions */}
      <View style={{ flexDirection: 'row', gap: 8 }}>
        <Button
          title={agent.status === 'active' ? 'Deactivate' : 'Activate'}
          variant={agent.status === 'active' ? 'outline' : 'default'}
          size="sm"
          onPress={() => onToggleStatus(agent.id)}
        />
        <Button
          title="Edit"
          variant="ghost"
          size="sm"
          onPress={() => onEdit(agent)}
        />
        <TouchableOpacity
          onPress={() => onDelete(agent.id)}
          style={{
            width: 36,
            height: 36,
            borderRadius: 18,
            backgroundColor: theme.color.error + '20',
            alignItems: 'center',
            justifyContent: 'center'
          }}
        >
          <Trash2 size={16} color={theme.color.error} />
        </TouchableOpacity>
      </View>
    </Card>
  )
}
