import React, { useState } from 'react'
import { View, Text, SafeAreaView, ScrollView, TouchableOpacity, Alert } from 'react-native'
import { useSafeAreaInsets } from 'react-native-safe-area-context'
import { useTranslation } from 'react-i18next'
import { useTheme } from '../../providers/ThemeProvider'
import { Card } from '../../components/ui/Card'
import { Search, Filter, MessageCircle, Clock, Users, AlertTriangle } from 'lucide-react-native'
import { ConversationCard } from './ConversationCard'
import { ChatScreen } from './ChatScreen'

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

export const ConversationsScreen: React.FC = () => {
  const { t } = useTranslation()
  const { theme } = useTheme()
  const insets = useSafeAreaInsets()
  const [activeTab, setActiveTab] = useState<'active' | 'archived' | 'all'>('active')
  const [selectedConversation, setSelectedConversation] = useState<Conversation | null>(null)
  const [showChat, setShowChat] = useState(false)

  // Mock conversation data
  const [conversations] = useState<Conversation[]>([
    {
      id: '1',
      customerName: 'Sarah Johnson',
      customerEmail: 'sarah.johnson@company.com',
      agentName: 'Nancy AI',
      status: 'active',
      priority: 'urgent',
      lastMessage: {
        id: 'm1',
        text: 'I need immediate help with my billing issue. My payment was charged twice this month.',
        isBot: false,
        timestamp: '2024-01-15T14:30:00Z',
        status: 'read'
      },
      unreadCount: 3,
      startedAt: '2024-01-15T14:25:00Z',
      tags: ['Billing', 'Priority', 'VIP'],
      satisfaction: 98,
      messages: [
        {
          id: 'm1',
          text: 'Hello! I need help with my billing.',
          isBot: false,
          timestamp: '2024-01-15T14:25:00Z',
          status: 'read'
        },
        {
          id: 'm2',
          text: 'Hi Sarah! I\'m Nancy, your AI assistant. I\'d be happy to help you with your billing inquiry. Let me look into your account.',
          isBot: true,
          timestamp: '2024-01-15T14:26:00Z',
          status: 'read'
        },
        {
          id: 'm3',
          text: 'I see you were charged twice this month. That\'s definitely not right. Let me process a refund for you right away.',
          isBot: true,
          timestamp: '2024-01-15T14:28:00Z',
          status: 'read'
        },
        {
          id: 'm4',
          text: 'Thank you! When should I expect to see the refund?',
          isBot: false,
          timestamp: '2024-01-15T14:29:00Z',
          status: 'read'
        },
        {
          id: 'm5',
          text: 'I need immediate help with my billing issue. My payment was charged twice this month.',
          isBot: false,
          timestamp: '2024-01-15T14:30:00Z',
          status: 'read'
        }
      ]
    },
    {
      id: '2',
      customerName: 'Michael Chen',
      customerEmail: 'mchen@startup.io',
      agentName: 'Jack AI',
      status: 'waiting',
      priority: 'high',
      lastMessage: {
        id: 'm2',
        text: 'Perfect! The integration is working now. Thanks for the quick support.',
        isBot: false,
        timestamp: '2024-01-15T12:45:00Z',
        status: 'delivered'
      },
      unreadCount: 0,
      startedAt: '2024-01-15T12:30:00Z',
      tags: ['Technical', 'Integration'],
      messages: [
        {
          id: 'm1',
          text: 'Hi, I\'m having trouble setting up the API integration.',
          isBot: false,
          timestamp: '2024-01-15T12:30:00Z',
          status: 'read'
        },
        {
          id: 'm2',
          text: 'Hello Michael! I\'m Jack. I can help you with the API setup. Let me guide you through the process.',
          isBot: true,
          timestamp: '2024-01-15T12:32:00Z',
          status: 'read'
        },
        {
          id: 'm3',
          text: 'Perfect! The integration is working now. Thanks for the quick support.',
          isBot: false,
          timestamp: '2024-01-15T12:45:00Z',
          status: 'delivered'
        }
      ]
    },
    {
      id: '3',
      customerName: 'Emma Rodriguez',
      customerEmail: 'emma.r@smallbiz.com',
      status: 'resolved',
      priority: 'medium',
      lastMessage: {
        id: 'm3',
        text: 'Thank you so much! The issue has been resolved. Have a great day!',
        isBot: false,
        timestamp: '2024-01-14T16:20:00Z',
        status: 'read'
      },
      unreadCount: 0,
      startedAt: '2024-01-14T15:45:00Z',
      tags: ['Support', 'Resolved'],
      satisfaction: 95,
      messages: [
        {
          id: 'm1',
          text: 'I can\'t access my dashboard. It keeps showing an error.',
          isBot: false,
          timestamp: '2024-01-14T15:45:00Z',
          status: 'read'
        },
        {
          id: 'm2',
          text: 'I\'ve identified the issue and fixed it for you. Please try logging in again.',
          isBot: true,
          timestamp: '2024-01-14T16:15:00Z',
          status: 'read'
        },
        {
          id: 'm3',
          text: 'Thank you so much! The issue has been resolved. Have a great day!',
          isBot: false,
          timestamp: '2024-01-14T16:20:00Z',
          status: 'read'
        }
      ]
    },
    {
      id: '4',
      customerName: 'David Park',
      customerEmail: 'dpark@tech.co',
      status: 'archived',
      priority: 'low',
      lastMessage: {
        id: 'm4',
        text: 'Great! I understand now. Thanks for the explanation.',
        isBot: false,
        timestamp: '2024-01-12T10:30:00Z',
        status: 'read'
      },
      unreadCount: 0,
      startedAt: '2024-01-12T10:00:00Z',
      tags: ['Question', 'General'],
      satisfaction: 87,
      messages: [
        {
          id: 'm1',
          text: 'How do I upgrade my plan?',
          isBot: false,
          timestamp: '2024-01-12T10:00:00Z',
          status: 'read'
        },
        {
          id: 'm2',
          text: 'You can upgrade your plan by going to Settings > Subscription in your dashboard.',
          isBot: true,
          timestamp: '2024-01-12T10:05:00Z',
          status: 'read'
        },
        {
          id: 'm3',
          text: 'Great! I understand now. Thanks for the explanation.',
          isBot: false,
          timestamp: '2024-01-12T10:30:00Z',
          status: 'read'
        }
      ]
    }
  ])

  const tabs = [
    { key: 'active' as const, label: t('conversations.active') },
    { key: 'archived' as const, label: t('conversations.archived') },
    { key: 'all' as const, label: t('conversations.all') },
  ]

  const getFilteredConversations = () => {
    switch (activeTab) {
      case 'active':
        return conversations.filter(c => c.status === 'active' || c.status === 'waiting')
      case 'archived':
        return conversations.filter(c => c.status === 'archived' || c.status === 'resolved')
      case 'all':
        return conversations
      default:
        return conversations
    }
  }

  const filteredConversations = getFilteredConversations()

  const getConversationStats = () => {
    const total = conversations.length
    const active = conversations.filter(c => c.status === 'active').length
    const waiting = conversations.filter(c => c.status === 'waiting').length
    const urgent = conversations.filter(c => c.priority === 'urgent').length
    const totalUnread = conversations.reduce((sum, c) => sum + c.unreadCount, 0)
    
    return { total, active, waiting, urgent, totalUnread }
  }

  const stats = getConversationStats()

  const handleConversationPress = (conversation: Conversation) => {
    setSelectedConversation(conversation)
    setShowChat(true)
  }

  const handleConversationMore = (conversation: Conversation) => {
    Alert.alert(
      conversation.customerName,
      'Choose an action',
      [
        { text: 'Open Chat', onPress: () => handleConversationPress(conversation) },
        { text: 'Mark as Read', onPress: () => {} },
        { text: 'Archive', onPress: () => {} },
        { text: 'Assign Agent', onPress: () => {} },
        { text: 'Cancel', style: 'cancel' }
      ]
    )
  }

  return (
    <SafeAreaView style={{ flex: 1, backgroundColor: theme.color.background }}>
      <View style={{ flex: 1 }}>
        {/* Header */}
        <View style={{ paddingHorizontal: 24, paddingTop: insets.top + 12, paddingBottom: 16 }}>
          <Text style={{
            color: theme.color.foreground,
            fontSize: 32,
            fontWeight: '700',
            marginBottom: 16
          }}>
            {t('conversations.title')}
          </Text>

          {/* Stats Row */}
          <View style={{ flexDirection: 'row', gap: 12, marginBottom: 16 }}>
            {[{icon: MessageCircle, color: theme.color.primary, value: stats.total, label: 'Total'},
              {icon: Users, color: theme.color.success, value: stats.active, label: 'Active'},
              {icon: Clock, color: theme.color.warning, value: stats.waiting, label: 'Waiting'},
              {icon: AlertTriangle, color: theme.color.error, value: stats.urgent, label: 'Urgent'}].map((s, idx) => (
              <View key={idx} style={{
                backgroundColor: theme.color.card,
                borderRadius: theme.radius.md,
                paddingHorizontal: 12,
                paddingVertical: 8,
                flex: 1,
                alignItems: 'center'
              }}>
                <s.icon size={16} color={s.color as any} />
                <Text style={{
                  color: theme.color.cardForeground,
                  fontSize: 16,
                  fontWeight: '700',
                  marginTop: 4
                }}>
                  {s.value}
                </Text>
                <Text style={{
                  color: theme.color.mutedForeground,
                  fontSize: 10
                }}>
                  {s.label}
                </Text>
              </View>
            ))}
          </View>

          {/* Search Bar */}
          <View style={{
            flexDirection: 'row',
            backgroundColor: theme.color.card,
            borderRadius: theme.radius.lg,
            padding: 12,
            alignItems: 'center',
            gap: 12,
            borderWidth: 1,
            borderColor: theme.color.border
          }}>
            <Search color={theme.color.mutedForeground} size={20} />
            <Text style={{
              flex: 1,
              color: theme.color.mutedForeground,
              fontSize: 16
            }}>
              {t('conversations.searchPlaceholder')}
            </Text>
            <TouchableOpacity>
              <Filter color={theme.color.mutedForeground} size={20} />
            </TouchableOpacity>
          </View>
        </View>

        {/* Tabs */}
        <View style={{
          flexDirection: 'row',
          paddingHorizontal: 24,
          marginBottom: 16
        }}>
          {tabs.map((tab) => (
            <TouchableOpacity
              key={tab.key}
              onPress={() => setActiveTab(tab.key)}
              style={{
                flex: 1,
                paddingVertical: 10,
                paddingHorizontal: 14,
                borderRadius: theme.radius.md,
                backgroundColor: activeTab === tab.key 
                  ? (theme.dark ? theme.color.secondary : theme.color.card) 
                  : 'transparent',
                borderWidth: 0,
                borderColor: 'transparent',
                marginRight: tab.key !== 'all' ? 8 : 0
              }}
            >
              <Text style={{
                color: activeTab === tab.key 
                  ? theme.color.cardForeground 
                  : theme.color.mutedForeground,
                textAlign: 'center',
                fontWeight: '600'
              }}>
                {tab.label}
              </Text>
            </TouchableOpacity>
          ))}
        </View>

        {/* Content */}
        <ScrollView style={{ flex: 1, paddingHorizontal: 24 }}>
          {filteredConversations.length > 0 ? (
            filteredConversations.map((conversation) => (
              <ConversationCard
                key={conversation.id}
                conversation={conversation}
                onPress={handleConversationPress}
                onMore={handleConversationMore}
              />
            ))
          ) : (
            <Card>
              <Text style={{
                color: theme.color.mutedForeground,
                textAlign: 'center',
                fontStyle: 'italic'
              }}>
                {t('conversations.noConversations')}
              </Text>
            </Card>
          )}
        </ScrollView>

        {/* Chat Screen Modal */}
        <ChatScreen
          visible={showChat}
          conversation={selectedConversation}
          onClose={() => {
            setShowChat(false)
            setSelectedConversation(null)
          }}
        />
      </View>
    </SafeAreaView>
  )
}
