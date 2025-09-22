import React, { useState } from 'react'
import { View, Text, SafeAreaView, ScrollView, TouchableOpacity, Alert, Platform } from 'react-native'
import { useTranslation } from 'react-i18next'
import { useTheme } from '../../providers/ThemeProvider'
import { Card } from '../../components/ui/Card'
import { Search, Calendar, MessageCircle, AlertTriangle, ClipboardList, FolderOpen } from 'lucide-react-native'
import { Modal } from '../../components/ui/Modal'
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
  // Align with Dashboard header spacing (avoid double safe-area padding)
  const [activeTab, setActiveTab] = useState<'active' | 'all'>('active')
  // Date picker state
  const [showDatePicker, setShowDatePicker] = useState(false)
  const [selectedRange, setSelectedRange] = useState<'all' | 'today' | '7d' | '30d'>('all')
  const [caseCategory, setCaseCategory] = useState<'all' | 'inquiries' | 'requests' | 'complaints'>('all')
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
    { key: 'active' as const, label: 'Open' },
    { key: 'all' as const, label: 'Resolved' },
  ]

  const getFilteredConversations = () => {
    const byStatus = (() => {
      switch (activeTab) {
        case 'active': // "Open"
          return conversations.filter(c => c.status === 'active' || c.status === 'waiting')
        case 'all': // "Resolved"
          return conversations.filter(c => c.status === 'resolved')
        default:
          return conversations
      }
    })()

    // Apply date range filter if set
    const byDate = (() => {
      if (selectedRange === 'all') return byStatus
      const now = new Date().getTime()
      const ms = selectedRange === 'today' ? 24*60*60*1000 : selectedRange === '7d' ? 7*24*60*60*1000 : 30*24*60*60*1000
      return byStatus.filter(c => {
        const t = new Date(c.startedAt).getTime()
        return now - t <= ms
      })
    })()

    if (caseCategory === 'all') return byDate

    const pickCaseType = (tags: string[]): 'inquiry' | 'request' | 'complaint' | 'other' => {
      const lower = (tags || []).map(t => t.toLowerCase())
      if (lower.some(t => ['question', 'support', 'general', 'inquiry'].includes(t))) return 'inquiry'
      if (lower.some(t => ['request', 'feature'].includes(t))) return 'request'
      if (lower.some(t => ['complaint'].includes(t))) return 'complaint'
      return 'other'
    }

    return byDate.filter(c => {
      const type = pickCaseType(c.tags || [])
      if (caseCategory === 'inquiries') return type === 'inquiry'
      if (caseCategory === 'requests') return type === 'request'
      if (caseCategory === 'complaints') return type === 'complaint'
      return true
    })
  }

  const filteredConversations = getFilteredConversations()

  const getConversationStats = () => {
    const total = conversations.length
    const active = conversations.filter(c => c.status === 'active').length
    const waiting = conversations.filter(c => c.status === 'waiting').length
    const urgent = conversations.filter(c => c.priority === 'urgent').length
    const resolved = conversations.filter(c => c.status === 'resolved').length
    const totalUnread = conversations.reduce((sum, c) => sum + c.unreadCount, 0)
    
    return { total, active, waiting, urgent, resolved, totalUnread }
  }

  const stats = getConversationStats()

  // Category counts for header indicators
  const pickCaseTypeHeader = (tags: string[]): 'inquiry' | 'request' | 'complaint' | 'other' => {
    const lower = (tags || []).map(t => t.toLowerCase())
    if (lower.some(t => ['question', 'support', 'general', 'inquiry'].includes(t))) return 'inquiry'
    if (lower.some(t => ['request', 'feature'].includes(t))) return 'request'
    if (lower.some(t => ['complaint'].includes(t))) return 'complaint'
    return 'other'
  }
  const categoryCounts = conversations.reduce(({ inquiry, request, complaint }, c) => {
    const type = pickCaseTypeHeader(c.tags || [])
    if (type === 'inquiry') inquiry += 1
    if (type === 'request') request += 1
    if (type === 'complaint') complaint += 1
    return { inquiry, request, complaint }
  }, { inquiry: 0, request: 0, complaint: 0 })
  const openCount = conversations.filter(c => c.status === 'active' || c.status === 'waiting').length

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
        <View style={{ paddingHorizontal: 24, paddingTop: 12, paddingBottom: 16 }}>
          <Text style={{
            color: theme.color.foreground,
            fontSize: 32,
            fontWeight: '700',
            marginBottom: 16
          }}>
            {t('conversations.title')}
          </Text>

          {/* Stats Row removed per request */}

          {/* Date Filter Bar removed per request */}

          {/* Tabs */}
          <View style={{ flexDirection: 'row', marginBottom: 12 }}>
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
                    ? (theme.color.primary as any)
                    : (theme.dark ? theme.color.secondary : theme.color.accent),
                  borderWidth: 0,
                  borderColor: 'transparent',
                  marginRight: tab.key !== 'all' ? 8 : 0
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

          {/* Case Category Toggles (equal width) */}
          <View style={{ flexDirection: 'row' }}>
            {([
              { key: 'inquiries', label: 'Inquiries' },
              { key: 'requests', label: 'Requests' },
              { key: 'complaints', label: 'Complaints' },
            ] as const).map((c) => (
              <TouchableOpacity
                key={c.key}
                onPress={() => setCaseCategory(prev => prev === c.key ? 'all' : c.key)}
                style={{
                  flex: 1,
                  paddingVertical: 12,
                  paddingHorizontal: 16,
                  borderRadius: theme.radius.md,
                  backgroundColor: caseCategory === c.key
                    ? (theme.color.primary as any)
                    : (theme.dark ? theme.color.secondary : theme.color.accent),
                  borderWidth: 0,
                  borderColor: 'transparent',
                  marginRight: c.key !== 'complaints' ? 8 : 0
                }}
              >
                <Text style={{
                  color: caseCategory === c.key ? ('#ffffff' as any) : (theme.color.mutedForeground as any),
                  textAlign: 'center',
                  fontWeight: '700',
                  fontSize: 13
                }}>
                  {c.label}
                </Text>
              </TouchableOpacity>
            ))}
          </View>
        </View>

        {/* Search Bar */}
        <View style={{ paddingHorizontal: 24, marginBottom: 16 }}>
          <View style={{
            flexDirection: 'row',
            backgroundColor: theme.dark ? theme.color.secondary : theme.color.accent,
            borderRadius: theme.radius.md,
            padding: 12,
            alignItems: 'center',
            gap: 12,
            borderWidth: 0,
            borderColor: 'transparent'
          }}>
            <Search color={theme.color.mutedForeground} size={22} />
            <Text style={{
              flex: 1,
              color: theme.color.mutedForeground,
              fontSize: 16
            }}>
              {t('conversations.searchPlaceholder')}
            </Text>
            <TouchableOpacity style={{ padding: 2 }} onPress={() => setShowDatePicker(true)}>
              <Calendar color={theme.color.mutedForeground} size={22} />
            </TouchableOpacity>
          </View>
        </View>

        {/* Content */}
        <ScrollView style={{ flex: 1, paddingHorizontal: 24 }}>
          {filteredConversations.length > 0 ? (
            filteredConversations.map((conversation) => (
              <ConversationCard
                key={conversation.id}
                conversation={conversation}
                onPress={(c) => handleConversationPress(c as any)}
                onMore={(c) => handleConversationMore(c as any)}
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

        {/* Date Picker Modal (lightweight presets) */}
        <Modal visible={showDatePicker} onClose={() => setShowDatePicker(false)} size="sm" autoHeight>
          <View style={{ gap: 8 }}>
            {[{key:'today',label:'Today'},{key:'7d',label:'Last 7 days'},{key:'30d',label:'Last 30 days'},{key:'all',label:'All time'}].map(opt => (
              <TouchableOpacity
                key={opt.key}
                onPress={() => { setSelectedRange(opt.key as any); setShowDatePicker(false) }}
                style={{
                  paddingVertical: 12,
                  paddingHorizontal: 12,
                  borderRadius: theme.radius.md,
                  backgroundColor: selectedRange === opt.key ? (theme.color.primary as any) : (theme.dark ? theme.color.secondary : theme.color.accent)
                }}
              >
                <Text style={{ color: selectedRange === opt.key ? ('#ffffff' as any) : (theme.color.mutedForeground as any), fontWeight: '700', textAlign: 'center' }}>
                  {opt.label}
                </Text>
              </TouchableOpacity>
            ))}
          </View>
        </Modal>
      </View>
    </SafeAreaView>
  )
}
