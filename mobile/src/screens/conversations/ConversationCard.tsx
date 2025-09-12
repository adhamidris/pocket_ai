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
  CheckCircle2,
  MoreHorizontal,
  Crown
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

  const getDisplayName = (name: string) => {
    const parts = name.trim().split(/\s+/)
    if (parts.length === 0) return name
    const first = parts[0]
    const second = parts.length > 1 ? parts[1][0].toUpperCase() + '.' : ''
    return second ? `${first} ${second}` : first
  }

  const formatCustomerId = (id: string) => {
    const padded = id.toString().padStart(4, '0')
    return `CUST-${padded}`
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

  const isVip = conversation.tags?.some(t => t.toLowerCase() === 'vip')
  const displayTags = (conversation.tags || [])
    .filter(t => t.toLowerCase() !== 'vip')
    .filter(t => t.toLowerCase() !== 'resolved')

  const getTagStyle = (tag: string) => {
    const t = tag.toLowerCase()
    // Fine-grained palette for better variety
    const exactMap: Record<string, string> = {
      // Ops/Severity
      priority: theme.color.error,
      urgent: theme.color.error,
      escalated: theme.color.error,
      // Finance
      billing: theme.color.warning,
      payment: theme.color.warning,
      invoice: theme.color.warning,
      // Tech flavors for variety
      technical: theme.color.primary,                 // brand blue
      integration: 'hsl(190,90%,45%)',               // teal
      api: 'hsl(200,90%,50%)',                       // cyan
      // Questions/support with distinct hues
      question: 'hsl(262,83%,58%)',                  // purple
      support: 'hsl(210,10%,60%)',                   // gray
      help: 'hsl(210,10%,60%)',                      // gray
      general: 'hsl(210,10%,60%)',                   // gray
      // Product/feature requests
      feature: 'hsl(291,70%,55%)',                   // violet/pink
      request: 'hsl(291,70%,55%)',                   // violet/pink
      // Positive/complete (generally filtered out above but safe default)
      resolved: theme.color.success,
      done: theme.color.success,
      closed: theme.color.success,
    }

    const color = exactMap[t] || theme.color.mutedForeground

    const withAlpha = (c: string, a: number) =>
      c.startsWith('hsl(')
        ? c.replace('hsl(', 'hsla(').replace(')', `,${a})`)
        : c

    const bg = withAlpha(color, theme.dark ? 0.28 : 0.12)
    return { color, bg }
  }

  const getTagFamily = (tag: string) => {
    const t = tag.toLowerCase()
    if (['priority', 'urgent', 'escalated'].includes(t)) return 'error'
    if (['billing', 'payment', 'invoice'].includes(t)) return 'warning'
    if (['technical', 'integration', 'api'].includes(t)) return 'primary'
    if (['resolved', 'closed', 'done'].includes(t)) return 'success'
    return 'neutral'
  }

  const orderedTags = [...displayTags].sort((a, b) => {
    const order = ['error', 'warning', 'primary', 'neutral', 'success'] as const
    const ai = order.indexOf(getTagFamily(a) as any)
    const bi = order.indexOf(getTagFamily(b) as any)
    if (ai !== bi) return ai - bi
    return a.localeCompare(b)
  })
  const visibleTags = orderedTags.slice(0, 2)
  const remainingCount = orderedTags.length - visibleTags.length

  return (
    <TouchableOpacity onPress={() => onPress(conversation)}>
      <Card variant="premium" style={{ 
        marginBottom: 12,
        backgroundColor: theme.dark ? theme.color.secondary : theme.color.accent,
        paddingHorizontal: 16,
        paddingVertical: 12
      }}>
        {/* Header */}
        <View style={{
          flexDirection: 'row',
          justifyContent: 'space-between',
          alignItems: 'flex-start',
          marginBottom: 12
        }}>
          <View style={{ flexDirection: 'row', alignItems: 'center', flex: 1, marginRight: 8 }}>
            {/* Avatar */}
            <View style={{
              width: 44,
              height: 44,
              backgroundColor: theme.dark ? theme.color.secondary : theme.color.card,
              borderRadius: 22,
              alignItems: 'center',
              justifyContent: 'center',
              marginRight: 12,
              borderWidth: 0,
              borderColor: 'transparent'
            }}>
              <Text style={{
                color: theme.color.cardForeground,
                fontSize: 14,
                fontWeight: '600'
              }}>
                {getInitials(conversation.customerName)}
              </Text>
            </View>

            {/* Info */}
            <View style={{ flex: 1 }}>
              <View style={{ flexDirection: 'row', alignItems: 'center', gap: 8, marginBottom: 4 }}>
                <View style={{ flex: 1, flexDirection: 'row', alignItems: 'center', gap: 6, minWidth: 0 }}>
                  <Text style={{
                    color: theme.color.cardForeground,
                    fontSize: 16,
                    fontWeight: '600',
                    flexShrink: 1
                  }} numberOfLines={1}>
                    {getDisplayName(conversation.customerName)}
                  </Text>
                  {isVip && (
                    <View style={{
                      flexDirection: 'row',
                      alignItems: 'center',
                      backgroundColor: '#f2c84b',
                      borderRadius: 10,
                      paddingHorizontal: 6,
                      paddingVertical: 2,
                      gap: 4
                    }}>
                      <Crown size={10} color={'#7a5d00'} fill={'#7a5d00'} />
                      <Text style={{ color: '#7a5d00', fontSize: 10, fontWeight: '700' }}>VIP</Text>
                    </View>
                  )}
                </View>
                {/* Removed inline urgent exclamation marker for cleaner look */}
              </View>
              <Text style={{
                color: theme.color.mutedForeground,
                fontSize: 11,
                marginBottom: 4
              }} numberOfLines={1}>
                Customer ID: {formatCustomerId(conversation.id)}
              </Text>
            </View>
          </View>

          {/* Status & Time */}
          <View style={{ alignItems: 'flex-end', gap: 6 }}>
            <View style={{ flexDirection: 'row', alignItems: 'center', gap: 8 }}>
              <View style={{
                flexDirection: 'row',
                alignItems: 'center',
                gap: 6,
                backgroundColor: theme.dark ? theme.color.secondary : theme.color.card,
                borderWidth: 0,
                borderColor: 'transparent',
                borderRadius: theme.radius.sm,
                paddingHorizontal: 8,
                paddingVertical: 4
              }}>
                <View style={{
                  width: 6,
                  height: 6,
                  borderRadius: 3,
                  backgroundColor: (
                    conversation.status === 'active' ? theme.color.success :
                    conversation.status === 'waiting' ? theme.color.warning :
                    theme.color.mutedForeground
                  )
                }} />
                <Text style={{ color: theme.color.mutedForeground, fontSize: 12, fontWeight: '600' }}>
                  {conversation.status.toUpperCase()}
                </Text>
              </View>
              
              <TouchableOpacity
                onPress={() => onMore(conversation)}
                style={{
                  width: 24,
                  height: 24,
                  borderRadius: 12,
                  backgroundColor: theme.dark ? theme.color.secondary : theme.color.card,
                  alignItems: 'center',
                  justifyContent: 'center',
                  borderWidth: 0,
                  borderColor: 'transparent'
                }}
              >
                <MoreHorizontal size={12} color={theme.color.mutedForeground} />
              </TouchableOpacity>
            </View>

            <Text style={{
              color: theme.color.mutedForeground,
              fontSize: 11
            }}>
              {formatTime(conversation.lastMessage.timestamp)}
            </Text>
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
              color: theme.color.mutedForeground,
              fontSize: 13,
              fontWeight: '400'
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
        {visibleTags.length > 0 && (
          <View style={{ flexDirection: 'row', flexWrap: 'wrap', gap: 6, marginTop: 8 }}>
            {visibleTags.map((tag, index) => {
              const { color, bg } = getTagStyle(tag)
              return (
                <View
                  key={index}
                  style={{
                    backgroundColor: bg as any,
                    paddingHorizontal: 6,
                    paddingVertical: 3,
                    borderRadius: theme.radius.sm,
                    flexDirection: 'row',
                    alignItems: 'center'
                  }}
                >
                  <Text style={{
                    color: (theme.dark ? ('#ffffff' as any) : (color as any)),
                    fontSize: 10,
                    fontWeight: '600'
                  }}>
                    {tag}
                  </Text>
                </View>
              )
            })}
            {remainingCount > 0 && (
              <View style={{
                backgroundColor: theme.color.muted,
                paddingHorizontal: 6,
                paddingVertical: 3,
                borderRadius: theme.radius.sm
              }}>
                <Text style={{
                  color: theme.color.mutedForeground,
                  fontSize: 10,
                  fontWeight: '500'
                }}>
                  +{remainingCount}
                </Text>
              </View>
            )}
          </View>
        )}
      </Card>
    </TouchableOpacity>
  )
}
