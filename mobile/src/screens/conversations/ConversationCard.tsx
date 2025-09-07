import React from 'react'
import { View, Text, TouchableOpacity } from 'react-native'
import { useTheme } from '../../providers/ThemeProvider'
import { Card } from '../../components/ui/Card'
import { Badge } from '../../components/ui/Badge'
import { 
  User, 
  Bot, 
  Clock, 
  MessageCircle,
  AlertCircle,
  CheckCircle2,
  MoreHorizontal
} from 'lucide-react-native'

interface Message {
  id: string
  text: string
  isBot: boolean
  timestamp: string
  status?: 'sending' | 'sent' | 'delivered' | 'read'
}

interface Conversation {
  id: string
  customerName: string
  customerEmail: string
  agentName?: string
  status: 'active' | 'waiting' | 'resolved' | 'archived'
  priority: 'low' | 'medium' | 'high' | 'urgent'
  lastMessage: Message
  unreadCount: number
  startedAt: string
  tags: string[]
  satisfaction?: number
}

interface ConversationCardProps {
  conversation: Conversation
  onPress: (conversation: Conversation) => void
  onMore: (conversation: Conversation) => void
}

export const ConversationCard: React.FC<ConversationCardProps> = ({ 
  conversation, 
  onPress,
  onMore 
}) => {
  const { theme } = useTheme()

  const getStatusVariant = (status: string) => {
    switch (status) {
      case 'active': return 'success'
      case 'waiting': return 'warning'
      case 'resolved': return 'secondary'
      case 'archived': return 'secondary'
      default: return 'default'
    }
  }

  const getPriorityColor = (priority: string) => {
    switch (priority) {
      case 'urgent': return theme.color.error
      case 'high': return theme.color.warning
      case 'medium': return theme.color.primary
      default: return theme.color.mutedForeground
    }
  }

  const getInitials = (name: string) => {
    return name.split(' ').map(n => n[0]).join('').toUpperCase().slice(0, 2)
  }

  const formatTime = (timestamp: string) => {
    const date = new Date(timestamp)
    const now = new Date()
    const diffTime = Math.abs(now.getTime() - date.getTime())
    const diffHours = Math.ceil(diffTime / (1000 * 60 * 60))
    const diffDays = Math.ceil(diffTime / (1000 * 60 * 60 * 24))
    
    if (diffHours < 24) {
      return date.toLocaleTimeString('en-US', { 
        hour: 'numeric', 
        minute: '2-digit',
        hour12: true 
      })
    } else if (diffDays < 7) {
      return `${diffDays}d ago`
    } else {
      return date.toLocaleDateString('en-US', { 
        month: 'short', 
        day: 'numeric' 
      })
    }
  }

  const getMessageStatusIcon = (status?: string) => {
    switch (status) {
      case 'sending': return <Clock size={12} color={theme.color.mutedForeground} />
      case 'sent': return <CheckCircle2 size={12} color={theme.color.mutedForeground} />
      case 'delivered': return <CheckCircle2 size={12} color={theme.color.primary} />
      case 'read': return <CheckCircle2 size={12} color={theme.color.success} />
      default: return null
    }
  }

  return (
    <TouchableOpacity onPress={() => onPress(conversation)}>
      <Card style={{ 
        marginBottom: 12,
        backgroundColor: conversation.unreadCount > 0 
          ? theme.color.primary + '05' 
          : theme.color.card
      }}>
        {/* Header */}
        <View style={{
          flexDirection: 'row',
          justifyContent: 'space-between',
          alignItems: 'flex-start',
          marginBottom: 12
        }}>
          <View style={{ flexDirection: 'row', alignItems: 'center', flex: 1, marginRight: 12 }}>
            {/* Avatar */}
            <View style={{
              width: 44,
              height: 44,
              backgroundColor: conversation.priority === 'urgent' 
                ? theme.color.error + '20'
                : theme.color.primary + '20',
              borderRadius: 22,
              alignItems: 'center',
              justifyContent: 'center',
              marginRight: 12
            }}>
              <Text style={{
                color: conversation.priority === 'urgent' ? theme.color.error : theme.color.primary,
                fontSize: 14,
                fontWeight: '600'
              }}>
                {getInitials(conversation.customerName)}
              </Text>
            </View>

            {/* Info */}
            <View style={{ flex: 1 }}>
              <View style={{ flexDirection: 'row', alignItems: 'center', gap: 8, marginBottom: 4 }}>
                <Text style={{
                  color: theme.color.cardForeground,
                  fontSize: 16,
                  fontWeight: '600',
                  flex: 1
                }} numberOfLines={1}>
                  {conversation.customerName}
                </Text>
                {conversation.priority === 'urgent' && (
                  <AlertCircle size={14} color={theme.color.error} />
                )}
              </View>
              
              <Text style={{
                color: theme.color.mutedForeground,
                fontSize: 12,
                marginBottom: 4
              }} numberOfLines={1}>
                {conversation.customerEmail}
              </Text>

              {conversation.agentName && (
                <View style={{ flexDirection: 'row', alignItems: 'center', gap: 6 }}>
                  <Bot size={12} color={theme.color.mutedForeground} />
                  <Text style={{
                    color: theme.color.mutedForeground,
                    fontSize: 12
                  }}>
                    {conversation.agentName}
                  </Text>
                </View>
              )}
            </View>
          </View>

          {/* Status & Time */}
          <View style={{ alignItems: 'flex-end', gap: 6 }}>
            <Text style={{
              color: theme.color.mutedForeground,
              fontSize: 11
            }}>
              {formatTime(conversation.lastMessage.timestamp)}
            </Text>
            
            <View style={{ flexDirection: 'row', alignItems: 'center', gap: 8 }}>
              <Badge variant={getStatusVariant(conversation.status)} size="sm">
                {conversation.status.toUpperCase()}
              </Badge>
              
              <TouchableOpacity
                onPress={() => onMore(conversation)}
                style={{
                  width: 24,
                  height: 24,
                  borderRadius: 12,
                  backgroundColor: theme.color.muted,
                  alignItems: 'center',
                  justifyContent: 'center'
                }}
              >
                <MoreHorizontal size={12} color={theme.color.mutedForeground} />
              </TouchableOpacity>
            </View>
          </View>
        </View>

        {/* Last Message */}
        <View style={{
          flexDirection: 'row',
          alignItems: 'flex-start',
          gap: 8,
          paddingTop: 8,
          borderTopWidth: 1,
          borderTopColor: theme.color.border
        }}>
          {conversation.lastMessage.isBot ? (
            <Bot size={14} color={theme.color.primary} style={{ marginTop: 2 }} />
          ) : (
            <User size={14} color={theme.color.mutedForeground} style={{ marginTop: 2 }} />
          )}
          
          <View style={{ flex: 1 }}>
            <Text style={{
              color: conversation.unreadCount > 0 
                ? theme.color.cardForeground 
                : theme.color.mutedForeground,
              fontSize: 14,
              fontWeight: conversation.unreadCount > 0 ? '500' : '400'
            }} numberOfLines={2}>
              {conversation.lastMessage.text}
            </Text>
          </View>

          <View style={{ alignItems: 'flex-end', gap: 4 }}>
            {getMessageStatusIcon(conversation.lastMessage.status)}
            
            {conversation.unreadCount > 0 && (
              <View style={{
                backgroundColor: theme.color.primary,
                borderRadius: 10,
                minWidth: 20,
                height: 20,
                alignItems: 'center',
                justifyContent: 'center',
                paddingHorizontal: 6
              }}>
                <Text style={{
                  color: '#fff',
                  fontSize: 11,
                  fontWeight: '600'
                }}>
                  {conversation.unreadCount > 99 ? '99+' : conversation.unreadCount}
                </Text>
              </View>
            )}
          </View>
        </View>

        {/* Tags */}
        {conversation.tags.length > 0 && (
          <View style={{ flexDirection: 'row', flexWrap: 'wrap', gap: 6, marginTop: 8 }}>
            {conversation.tags.slice(0, 3).map((tag, index) => (
              <View key={index} style={{
                backgroundColor: theme.color.muted,
                paddingHorizontal: 6,
                paddingVertical: 2,
                borderRadius: theme.radius.sm
              }}>
                <Text style={{
                  color: theme.color.mutedForeground,
                  fontSize: 10,
                  fontWeight: '500'
                }}>
                  {tag}
                </Text>
              </View>
            ))}
            {conversation.tags.length > 3 && (
              <View style={{
                backgroundColor: theme.color.muted,
                paddingHorizontal: 6,
                paddingVertical: 2,
                borderRadius: theme.radius.sm
              }}>
                <Text style={{
                  color: theme.color.mutedForeground,
                  fontSize: 10,
                  fontWeight: '500'
                }}>
                  +{conversation.tags.length - 3}
                </Text>
              </View>
            )}
          </View>
        )}
      </Card>
    </TouchableOpacity>
  )
}
