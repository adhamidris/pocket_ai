import React, { useState, useRef, useEffect } from 'react'
import { 
  View, 
  Text, 
  SafeAreaView, 
  ScrollView, 
  TextInput,
  TouchableOpacity,
  KeyboardAvoidingView,
  Platform,
  Alert
} from 'react-native'
import { useTheme } from '../../providers/ThemeProvider'
import { Modal } from '../../components/ui/Modal'
import { Badge } from '../../components/ui/Badge'
import { Button } from '../../components/ui/Button'
import { TypingDots } from '../../components/ui/PulseAnimation'
import { 
  ArrowLeft,
  Send,
  Bot,
  User,
  Phone,
  Mail,
  MoreHorizontal,
  Clock,
  CheckCircle2,
  AlertTriangle
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
  const [newMessage, setNewMessage] = useState('')
  const [isTyping, setIsTyping] = useState(false)
  const [messages, setMessages] = useState<Message[]>([])
  const scrollViewRef = useRef<ScrollView>(null)

  useEffect(() => {
    if (conversation) {
      setMessages(conversation.messages || [])
    }
  }, [conversation])

  if (!conversation) return null

  const handleSendMessage = () => {
    if (!newMessage.trim()) return

    const message: Message = {
      id: Date.now().toString(),
      text: newMessage.trim(),
      isBot: false,
      timestamp: new Date().toISOString(),
      status: 'sending'
    }

    setMessages(prev => [...prev, message])
    setNewMessage('')
    
    // Simulate message delivery
    setTimeout(() => {
      setMessages(prev => prev.map(m => 
        m.id === message.id ? { ...m, status: 'sent' } : m
      ))
    }, 1000)

    // Simulate bot response
    setIsTyping(true)
    setTimeout(() => {
      const botResponse: Message = {
        id: (Date.now() + 1).toString(),
        text: "Thanks for your message! I'm processing your request and will get back to you shortly.",
        isBot: true,
        timestamp: new Date().toISOString(),
        status: 'sent'
      }
      setMessages(prev => [...prev, botResponse])
      setIsTyping(false)
    }, 2500)

    // Scroll to bottom
    setTimeout(() => {
      scrollViewRef.current?.scrollToEnd({ animated: true })
    }, 100)
  }

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
        {/* Chat Header */}
        <View style={{
          flexDirection: 'row',
          alignItems: 'center',
          paddingBottom: 16,
          borderBottomWidth: 1,
          borderBottomColor: theme.color.border,
          marginBottom: 16
        }}>
          <TouchableOpacity 
            onPress={onClose}
            style={{
              width: 32,
              height: 32,
              borderRadius: 16,
              backgroundColor: theme.color.muted,
              alignItems: 'center',
              justifyContent: 'center',
              marginRight: 12
            }}
          >
            <ArrowLeft size={16} color={theme.color.mutedForeground} />
          </TouchableOpacity>

          {/* Customer Avatar */}
          <View style={{
            width: 40,
            height: 40,
            backgroundColor: conversation.priority === 'urgent' 
              ? theme.color.error + '20'
              : theme.color.primary + '20',
            borderRadius: 20,
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

          {/* Customer Info */}
          <View style={{ flex: 1 }}>
            <View style={{ flexDirection: 'row', alignItems: 'center', gap: 8, marginBottom: 2 }}>
              <Text style={{
                color: theme.color.cardForeground,
                fontSize: 16,
                fontWeight: '600',
                flex: 1
              }} numberOfLines={1}>
                {conversation.customerName}
              </Text>
              {conversation.priority === 'urgent' && (
                <AlertTriangle size={14} color={theme.color.error} />
              )}
            </View>
            
            <View style={{ flexDirection: 'row', alignItems: 'center', gap: 8 }}>
              <Badge variant={getStatusVariant(conversation.status)} size="sm">
                {conversation.status}
              </Badge>
              {conversation.agentName && (
                <Text style={{
                  color: theme.color.mutedForeground,
                  fontSize: 12
                }}>
                  with {conversation.agentName}
                </Text>
              )}
            </View>
          </View>

          {/* Actions */}
          <View style={{ flexDirection: 'row', gap: 8 }}>
            <TouchableOpacity style={{
              width: 36,
              height: 36,
              borderRadius: 18,
              backgroundColor: theme.color.success + '20',
              alignItems: 'center',
              justifyContent: 'center'
            }}>
              <Phone size={16} color={theme.color.success} />
            </TouchableOpacity>
            
            <TouchableOpacity style={{
              width: 36,
              height: 36,
              borderRadius: 18,
              backgroundColor: theme.color.primary + '20',
              alignItems: 'center',
              justifyContent: 'center'
            }}>
              <Mail size={16} color={theme.color.primary} />
            </TouchableOpacity>
            
            <TouchableOpacity style={{
              width: 36,
              height: 36,
              borderRadius: 18,
              backgroundColor: theme.color.muted,
              alignItems: 'center',
              justifyContent: 'center'
            }}>
              <MoreHorizontal size={16} color={theme.color.mutedForeground} />
            </TouchableOpacity>
          </View>
        </View>

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

        {/* Message Input */}
        <View style={{
          flexDirection: 'row',
          alignItems: 'flex-end',
          gap: 12,
          paddingTop: 16,
          borderTopWidth: 1,
          borderTopColor: theme.color.border
        }}>
          <View style={{ flex: 1 }}>
            <TextInput
              value={newMessage}
              onChangeText={setNewMessage}
              placeholder="Type your message..."
              placeholderTextColor={theme.color.mutedForeground}
              multiline
              maxLength={1000}
              style={{
                backgroundColor: theme.color.background,
                borderWidth: 1,
                borderColor: theme.color.border,
                borderRadius: theme.radius.lg,
                paddingHorizontal: 16,
                paddingVertical: 12,
                color: theme.color.foreground,
                fontSize: 16,
                maxHeight: 120,
                minHeight: 44
              }}
            />
          </View>
          
          <TouchableOpacity
            onPress={handleSendMessage}
            disabled={!newMessage.trim()}
            style={{
              width: 44,
              height: 44,
              backgroundColor: newMessage.trim() 
                ? theme.color.primary 
                : theme.color.muted,
              borderRadius: 22,
              alignItems: 'center',
              justifyContent: 'center'
            }}
          >
            <Send 
              size={20} 
              color={newMessage.trim() ? '#fff' : theme.color.mutedForeground} 
            />
          </TouchableOpacity>
        </View>

        {/* Quick Actions */}
        <View style={{
          flexDirection: 'row',
          gap: 8,
          marginTop: 12
        }}>
          <Button
            title="Resolve"
            variant="outline"
            size="sm"
            onPress={() => Alert.alert('Resolve', 'Mark this conversation as resolved?')}
          />
          <Button
            title="Transfer"
            variant="ghost"
            size="sm"
            onPress={() => Alert.alert('Transfer', 'Transfer to another agent?')}
          />
          <Button
            title="Escalate"
            variant="ghost"
            size="sm"
            onPress={() => Alert.alert('Escalate', 'Escalate to human agent?')}
          />
        </View>
      </KeyboardAvoidingView>
    </Modal>
  )
}
