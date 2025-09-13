import React, { useState, useRef, useEffect } from 'react'
import { 
  View, 
  Text, 
  SafeAreaView, 
  ScrollView, 
  TouchableOpacity,
  KeyboardAvoidingView,
  Platform,
  Alert
} from 'react-native'
import { useTheme } from '../../providers/ThemeProvider'
import { Modal } from '../../components/ui/Modal'
import { Card } from '../../components/ui/Card'
import { Button } from '../../components/ui/Button'
import { TypingDots } from '../../components/ui/PulseAnimation'
import { 
  Bot,
  User,
  MoreHorizontal,
  Clock,
  CheckCircle2,
  AlertTriangle,
  Crown,
  Flame,
  Timer,
  Smile,
  Meh,
  Frown,
  Copy
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
  messages: Message[]
}

interface ChatScreenProps {
  visible: boolean
  conversation: Conversation | null
  onClose: () => void
}

export const ChatScreen: React.FC<ChatScreenProps> = ({ 
  visible, 
  conversation, 
  onClose 
}) => {
  const { theme } = useTheme()
  const [isTyping, setIsTyping] = useState(false)
  const [messages, setMessages] = useState<Message[]>([])
  const scrollViewRef = useRef<ScrollView>(null)

  useEffect(() => {
    if (conversation) {
      setMessages(conversation.messages || [])
    }
  }, [conversation])

  if (!conversation) return null

  const formatCustomerId = (id: string) => {
    const padded = id.toString().padStart(4, '0')
    return `CUST-${padded}`
  }

  const isVip = (conversation.tags || []).some(t => t.toLowerCase() === 'vip')

  // Case header helpers (aligns with CustomerDetail styling)
  const formatShortDate = (iso: string) => new Date(iso).toLocaleString('en-US', { month: 'short', day: 'numeric' })
  const getCaseIdRef = (id: string) => `C-${id.toString().padStart(4, '0')}`
  const pickCaseType = (tags: string[]): 'billing' | 'bug' | 'request' | 'inquiry' | 'complaint' | 'feedback' => {
    const lower = tags.map(t => t.toLowerCase())
    if (lower.includes('billing')) return 'billing'
    if (lower.includes('technical') || lower.includes('integration') || lower.includes('api')) return 'bug'
    if (lower.includes('question') || lower.includes('support') || lower.includes('general')) return 'inquiry'
    if (lower.includes('resolved') || lower.includes('closed') || lower.includes('done')) return 'feedback'
    return 'inquiry'
  }
  const getCaseTypeColor = (type: string) => {
    switch (type) {
      case 'billing': return theme.color.warning
      case 'bug': return theme.color.error
      case 'complaint': return theme.color.error
      case 'request': return theme.color.primary
      case 'inquiry': return 'hsl(200,90%,50%)'
      case 'feedback': return 'hsl(262,83%,58%)'
      default: return theme.color.mutedForeground
    }
  }
  const getPriorityMeta = (p: string) => {
    switch (p) {
      case 'urgent': return { color: theme.color.error, Icon: Flame }
      case 'high': return { color: theme.color.error, Icon: Flame }
      case 'medium': return { color: theme.color.warning, Icon: Timer }
      case 'low': return { color: theme.color.mutedForeground, Icon: Clock }
      default: return { color: theme.color.mutedForeground, Icon: Clock }
    }
  }
  const getToneMeta = (priority: string) => {
    switch (priority) {
      case 'urgent': return { color: theme.color.warning, Icon: AlertTriangle, label: 'FRUSTRATED' }
      case 'high': return { color: theme.color.error, Icon: Frown, label: 'NEGATIVE' }
      case 'medium': return { color: theme.color.mutedForeground, Icon: Meh, label: 'NEUTRAL' }
      default: return { color: theme.color.success, Icon: Smile, label: 'POSITIVE' }
    }
  }

  // Input removed: conversation is view-only in this UI

  const getInitials = (name: string) => {
    return name.split(' ').map(n => n[0]).join('').toUpperCase().slice(0, 2)
  }

  const formatTime = (timestamp: string) => {
    return new Date(timestamp).toLocaleTimeString('en-US', { 
      hour: 'numeric', 
      minute: '2-digit',
      hour12: true 
    })
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

  return (
    <Modal visible={visible} onClose={onClose} size="lg">
      <KeyboardAvoidingView 
        behavior={Platform.OS === 'ios' ? 'padding' : 'height'}
        style={{ flex: 1 }}
      >
        {/* Chat Header (match CustomerDetail) */}
        <View style={{
          flexDirection: 'row',
          alignItems: 'center',
          paddingBottom: 4,
          borderBottomWidth: 1,
          borderBottomColor: theme.color.border,
          marginBottom: 4
        }}>
          {/* Customer Avatar */}
          <View style={{
            width: 56,
            height: 56,
            backgroundColor: theme.dark ? theme.color.secondary : theme.color.card,
            borderRadius: 28,
            alignItems: 'center',
            justifyContent: 'center',
            marginRight: 12
          }}>
            <Text style={{
              color: theme.color.cardForeground,
              fontSize: 22,
              fontWeight: '700'
            }}>
              {getInitials(conversation.customerName)}
            </Text>
          </View>

          {/* Customer Info (similar to CustomerDetail header) */}
          <View style={{ flex: 1 }}>
            <View style={{ flexDirection: 'row', alignItems: 'center', justifyContent: 'space-between', gap: 8, marginBottom: 2 }}>
              <View style={{ flexDirection: 'row', alignItems: 'center', gap: 8, flexShrink: 1 }}>
              <Text style={{
                color: theme.color.cardForeground,
                fontSize: 18,
                fontWeight: '700',
                flexShrink: 1,
                minWidth: 0
              }} numberOfLines={1}>
                {conversation.customerName}
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
              {/* Actions (compact) */}
              <TouchableOpacity style={{
                width: 32,
                height: 32,
                borderRadius: 16,
                backgroundColor: theme.dark ? theme.color.secondary : theme.color.card,
                alignItems: 'center',
                justifyContent: 'center'
              }}>
                <MoreHorizontal size={18} color={theme.color.mutedForeground} />
              </TouchableOpacity>
            </View>
            <View style={{ flexDirection: 'row', alignItems: 'center', gap: 8, marginBottom: 4 }}>
              <Text style={{ color: theme.color.mutedForeground, fontSize: 12 }} numberOfLines={1}>
                Customer ID: {formatCustomerId(conversation.id)}
              </Text>
              <TouchableOpacity
                onPress={() => {
                  const id = formatCustomerId(conversation.id)
                  // Best-effort copy on web
                  // @ts-ignore navigator exists on web
                  if (Platform.OS === 'web' && typeof navigator !== 'undefined' && navigator.clipboard?.writeText) {
                    // @ts-ignore
                    navigator.clipboard.writeText(id)
                  }
                  Alert.alert('Copied', 'Customer ID copied to clipboard')
                }}
                style={{ padding: 4 }}
              >
                <Copy size={14} color={theme.color.mutedForeground as any} />
              </TouchableOpacity>
            </View>
            {conversation.agentName ? (
              <Text style={{ color: theme.color.mutedForeground, fontSize: 12 }}>
                with {conversation.agentName}
              </Text>
            ) : null}
          </View>
        </View>

        {/* Case sub-header (match CustomerDetail selected case) */}
        {(() => {
          const caseId = getCaseIdRef(conversation.id)
          const date = formatShortDate(conversation.startedAt || conversation.lastMessage.timestamp)
          const caseType = pickCaseType(conversation.tags || [])
          const typeColor = getCaseTypeColor(caseType)
          const { color: prColor, Icon: PrIcon } = getPriorityMeta(conversation.priority)
          const { color: toneColor, Icon: ToneIcon, label: toneLabel } = getToneMeta(conversation.priority)
          const title = (() => {
            if ((conversation.tags || []).length > 0) {
              const t = conversation.tags[0].toLowerCase()
              if (t === 'billing') return 'Billing issue'
              if (t === 'technical' || t === 'integration') return 'Integration/technical issue'
              if (t === 'question') return 'Product question'
              if (t === 'support') return 'Support request'
            }
            const txt = conversation.lastMessage?.text || ''
            return txt.length > 60 ? txt.slice(0, 57) + '…' : txt || 'Customer case'
          })()
          const bgAlpha = (c: string) => c.startsWith('hsl(') ? c.replace('hsl(', 'hsla(').replace(')', `,${theme.dark ? '0.20' : '0.12'})`) : (theme.dark ? 'rgba(255,255,255,0.12)' : 'rgba(0,0,0,0.06)')
          return (
            <Card
              variant="flat"
              style={{
                paddingTop: 8,
                paddingHorizontal: 16,
                paddingBottom: 16,
                marginBottom: 0,
                ...(Platform.select({
                  ios: { shadowColor: 'transparent', shadowOpacity: 0, shadowRadius: 0, shadowOffset: { width: 0, height: 0 } },
                  android: { elevation: 0 },
                  default: {},
                }) as any),
              }}
            >
              <View style={{ gap: 6, marginBottom: 10 }}>
                {/* ID left • Date right */}
                <View style={{ flexDirection: 'row', alignItems: 'center', justifyContent: 'space-between' }}>
                  <Text style={{ color: theme.color.mutedForeground, fontSize: 12 }}>{caseId}</Text>
                  <Text style={{ color: theme.color.mutedForeground, fontSize: 12 }}>{date}</Text>
                </View>
                {/* Title + chips */}
                <View style={{ flexDirection: 'row', alignItems: 'center', justifyContent: 'space-between', gap: 10 }}>
                  <Text style={{ color: theme.color.cardForeground, fontSize: 16, fontWeight: '800', flex: 1, minWidth: 0 }} numberOfLines={2}>
                    {title}
                  </Text>
                  <View style={{ flexDirection: 'row', alignItems: 'center', gap: 8, flexShrink: 0 }}>
                    <View style={{ backgroundColor: typeColor as any, borderRadius: 12, paddingHorizontal: 6, paddingVertical: 3 }}>
                      <Text style={{ color: '#fff', fontSize: 11, fontWeight: '800' }}>{caseType.toUpperCase()}</Text>
                    </View>
                    <View style={{ flexDirection: 'row', alignItems: 'center', gap: 6, backgroundColor: bgAlpha(prColor as any) as any, borderRadius: 12, paddingHorizontal: 6, paddingVertical: 3 }}>
                      <PrIcon size={12} color={prColor as any} />
                      <Text style={{ color: prColor as any, fontSize: 11, fontWeight: '700' }}>{conversation.priority.toUpperCase()}</Text>
                    </View>
                  </View>
                </View>
                {/* Tone + Satisfaction */}
                <View style={{ flexDirection: 'row', alignItems: 'center', gap: 12 }}>
                  <View style={{ flexDirection: 'row', alignItems: 'center', gap: 6 }}>
                    <ToneIcon size={14} color={toneColor as any} />
                    <Text style={{ color: theme.color.cardForeground, fontSize: 13, fontWeight: '600' }}>Tone</Text>
                    <Text style={{ color: toneColor as any, fontSize: 13, fontWeight: '700' }}>{toneLabel}</Text>
                  </View>
                  <View style={{ width: 1, height: 16, backgroundColor: theme.color.border }} />
                  <View style={{ flexDirection: 'row', alignItems: 'center', gap: 6 }}>
                    <Smile size={14} color={theme.color.warning as any} />
                    <Text style={{ color: theme.color.cardForeground, fontSize: 13, fontWeight: '600' }}>Satisfaction</Text>
                    <Text style={{ color: theme.color.warning, fontSize: 13, fontWeight: '700' }}>{typeof conversation.satisfaction === 'number' ? `${conversation.satisfaction}%` : '—'}</Text>
                  </View>
                </View>
              </View>
            </Card>
          )
        })()}

        {/* Messages */}
        <ScrollView 
          ref={scrollViewRef}
          style={{ flex: 1, marginBottom: 16 }}
          showsVerticalScrollIndicator={false}
        >
          {messages.map((message, index) => (
            <View key={message.id} style={{ marginBottom: 16 }}>
              <View style={{
                alignItems: message.isBot ? 'flex-start' : 'flex-end',
                marginBottom: 4
              }}>
                <View style={{
                  maxWidth: '80%',
                  backgroundColor: message.isBot 
                    ? theme.color.muted 
                    : theme.color.primary,
                  borderRadius: 16,
                  paddingHorizontal: 16,
                  paddingVertical: 12
                }}>
                  <Text style={{
                    color: message.isBot 
                      ? theme.color.cardForeground 
                      : '#fff',
                    fontSize: 15,
                    lineHeight: 20
                  }}>
                    {message.text}
                  </Text>
                </View>
              </View>
              
              {/* Message Info */}
              <View style={{
                flexDirection: 'row',
                alignItems: 'center',
                justifyContent: message.isBot ? 'flex-start' : 'flex-end',
                gap: 6,
                paddingHorizontal: 4
              }}>
                {message.isBot ? (
                  <Bot size={12} color={theme.color.primary} />
                ) : (
                  <User size={12} color={theme.color.mutedForeground} />
                )}
                
                <Text style={{
                  color: theme.color.mutedForeground,
                  fontSize: 11
                }}>
                  {formatTime(message.timestamp)}
                </Text>
                
                {!message.isBot && getMessageStatusIcon(message.status)}
              </View>
            </View>
          ))}

          {/* Typing Indicator */}
          {isTyping && (
            <View style={{
              alignItems: 'flex-start',
              marginBottom: 16
            }}>
              <View style={{
                backgroundColor: theme.color.muted,
                borderRadius: 16,
                paddingHorizontal: 16,
                paddingVertical: 12,
                flexDirection: 'row',
                alignItems: 'center',
                gap: 8
              }}>
                <Bot size={16} color={theme.color.primary} />
                <TypingDots color={theme.color.mutedForeground} />
                <Text style={{
                  color: theme.color.mutedForeground,
                  fontSize: 14,
                  fontStyle: 'italic'
                }}>
                  AI is typing...
                </Text>
              </View>
            </View>
          )}
        </ScrollView>

        {/* Message Input removed intentionally */}

        {/* Quick Actions: three equal-width buttons across full row */}
        <View style={{ flexDirection: 'row', gap: 8, marginTop: 12 }}>
          <View style={{ flex: 1 }}>
            <Button
              title="Resolve"
              variant="outline"
              size="md"
              fullWidth
              onPress={() => Alert.alert('Resolve', 'Mark this conversation as resolved?')}
            />
          </View>
          <View style={{ flex: 1 }}>
            <Button
              title="Transfer"
              variant="outline"
              size="md"
              fullWidth
              onPress={() => Alert.alert('Transfer', 'Transfer to another agent?')}
            />
          </View>
          <View style={{ flex: 1 }}>
            <Button
              title="Escalate"
              variant="outline"
              size="md"
              fullWidth
              onPress={() => Alert.alert('Escalate', 'Escalate to human agent?')}
            />
          </View>
        </View>
      </KeyboardAvoidingView>
    </Modal>
  )
}
