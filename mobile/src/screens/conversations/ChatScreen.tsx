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
  Copy,
  ClipboardList,
  FileText,
  Download,
  MessageCircle,
  ChevronRight
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
  onOpenCustomer?: (customerId: string) => void
}

export const ChatScreen: React.FC<ChatScreenProps> = ({ 
  visible, 
  conversation, 
  onClose,
  onOpenCustomer
}) => {
  const { theme } = useTheme()
  const [isTyping, setIsTyping] = useState(false)
  const [messages, setMessages] = useState<Message[]>([])
  const [caseDetailsTab, setCaseDetailsTab] = useState<'chat' | 'overview' | 'documents' | 'history'>('overview')
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
  // Case meta (align with CustomerDetail)
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
  const bgAlpha = (c: string) => c.startsWith('hsl(')
    ? c.replace('hsl(', 'hsla(').replace(')', `,${theme.dark ? '0.20' : '0.12'})`)
    : (theme.dark ? 'rgba(255,255,255,0.12)' : 'rgba(0,0,0,0.06)')

  // Case details helpers (cloned from CustomerDetail)
  const formatShortDateTime = (iso: string) => {
    const d = new Date(iso)
    return d.toLocaleString('en-US', { month: 'short', day: 'numeric' })
  }
  const getCaseSummary = (t: string) => {
    const byType: Record<string, string[]> = {
      billing: [
        'Duplicate transaction on the latest invoice.',
        'Statement total reflects the unintended repeat charge.'
      ],
      request: [
        'CSV export for analytics requested.',
        'Needed to share reports with stakeholders.'
      ],
      bug: [
        'Integration webhook experienced timeouts.',
        'Event delivery intermittently failed in the affected window.'
      ],
      complaint: [
        'Service outcome did not meet expectations.',
        'Dissatisfaction reported with the recent experience.'
      ],
      inquiry: [
        'Clarification requested on a product capability.',
        'Information needed before proceeding.'
      ],
      feedback: [
        'Constructive feedback shared on product/support.',
        'Captured for future improvement planning.'
      ],
    }
    const lines = byType[t] || []
    return lines.slice(0, 3).join('\n')
  }
  const getChannelStyle = (channel: string) => {
    const abbrMap: Record<string, string> = {
      email: 'EM', whatsapp: 'WA', web: 'WEB', instagram: 'IG', messenger: 'MSG', twitter: 'TW',
    }
    const label = abbrMap[channel] || channel.slice(0, 3).toUpperCase()
    let color: string = theme.color.mutedForeground as any
    switch (channel) {
      case 'email': color = theme.color.primary as any; break
      case 'whatsapp': color = 'hsl(142,71%,45%)'; break
      case 'web': color = 'hsl(200,90%,50%)'; break
      case 'instagram': color = 'hsl(291,70%,55%)'; break
      case 'messenger': color = 'hsl(262,83%,58%)'; break
      case 'twitter': color = 'hsl(210,10%,60%)'; break
    }
    const withAlpha = (c: string, a: number) => c.startsWith('hsl(')
      ? c.replace('hsl(', 'hsla(').replace(')', `,${a})`)
      : c
    const bg = withAlpha(color, (theme.dark ? 0.28 : 0.12))
    return { label, color, bg }
  }
  

  return (
    <Modal visible={visible} onClose={onClose} size="lg">
      <KeyboardAvoidingView 
        behavior={Platform.OS === 'ios' ? 'padding' : 'height'}
        style={{ flex: 1, position: 'relative' }}
      >
        {/* Case Header promoted to main header */}
        <View style={{ paddingTop: 4, paddingBottom: 12, paddingHorizontal: 8, marginBottom: 6, borderBottomWidth: 1, borderBottomColor: theme.color.border }}>
          {/* ID left • Date right */}
          <View style={{ flexDirection: 'row', alignItems: 'center', justifyContent: 'space-between' }}>
            <Text style={{ color: theme.color.mutedForeground, fontSize: 12 }}>{caseId}</Text>
            <Text style={{ color: theme.color.mutedForeground, fontSize: 12 }}>{date}</Text>
          </View>
          {/* Title + chips */}
          <View style={{ flexDirection: 'row', alignItems: 'center', justifyContent: 'space-between', gap: 10, marginTop: 6 }}>
            <Text style={{ color: theme.color.cardForeground, fontSize: 16, fontWeight: '800', flex: 1, minWidth: 0 }} numberOfLines={2}>
              {title}
            </Text>
            <View style={{ flexDirection: 'row', alignItems: 'center', gap: 8, flexShrink: 0 }}>
              <View style={{ backgroundColor: typeColor as any, borderRadius: 10, paddingHorizontal: 5, paddingVertical: 2 }}>
                <Text style={{ color: '#fff', fontSize: 10, fontWeight: '800' }}>{caseType.toUpperCase()}</Text>
              </View>
              <View style={{ flexDirection: 'row', alignItems: 'center', gap: 6, backgroundColor: bgAlpha(prColor as any) as any, borderRadius: 10, paddingHorizontal: 5, paddingVertical: 2 }}>
                <PrIcon size={11} color={prColor as any} />
                <Text style={{ color: prColor as any, fontSize: 10, fontWeight: '700' }}>{conversation.priority.toUpperCase()}</Text>
              </View>
            </View>
          </View>
          {/* Meta & Customer row: tone/satisfaction (left) | divider | customer (right) */}
          <View style={{ flexDirection: 'row', alignItems: 'center', gap: 12, marginTop: 10 }}>
            {/* Left: stacked Tone above Satisfaction */}
            <View style={{ flex: 1, flexDirection: 'column', alignItems: 'flex-start', gap: 6 }}>
              <View style={{ flexDirection: 'row', alignItems: 'center', gap: 6 }}>
                <ToneIcon size={14} color={toneColor as any} />
                <Text style={{ color: theme.color.cardForeground, fontSize: 13, fontWeight: '600' }}>Tone</Text>
                <Text style={{ color: toneColor as any, fontSize: 13, fontWeight: '700' }}>{toneLabel}</Text>
              </View>
              <View style={{ flexDirection: 'row', alignItems: 'center', gap: 6 }}>
                <Smile size={14} color={theme.color.warning as any} />
                <Text style={{ color: theme.color.cardForeground, fontSize: 13, fontWeight: '600' }}>Satisfaction</Text>
                <Text style={{ color: theme.color.warning, fontSize: 13, fontWeight: '700' }}>{typeof conversation.satisfaction === 'number' ? `${conversation.satisfaction}%` : '—'}</Text>
              </View>
            </View>

            {/* Divider */}
            <View style={{ width: 1, height: 32, backgroundColor: theme.color.border }} />

            {/* Right: Minimal customer details */}
            <View style={{ flex: 1, minWidth: 0, flexDirection: 'row', alignItems: 'center', gap: 8 }}>
              <View style={{ flex: 1, minWidth: 0 }}>
                <Text numberOfLines={1} style={{ color: theme.color.cardForeground, fontSize: 12, fontWeight: '600' }}>
                  {conversation.customerName}
                </Text>
                <Text numberOfLines={1} style={{ color: theme.color.mutedForeground, fontSize: 11 }}>
                  +1 (415) 555-0137
                </Text>
                <Text numberOfLines={1} style={{ color: theme.color.mutedForeground, fontSize: 11 }}>
                  {formatCustomerId(conversation.id)}
                </Text>
              </View>
              <TouchableOpacity
                onPress={() => {
                  if (onOpenCustomer) return onOpenCustomer(conversation.id)
                  Alert.alert('Customer Profile', `Open profile for ${conversation.customerName}`)
                }}
                style={{ padding: 6, borderRadius: 8, backgroundColor: theme.dark ? 'rgba(255,255,255,0.06)' : 'rgba(0,0,0,0.04)' }}
                accessibilityRole="button"
                accessibilityLabel={`Open profile for ${conversation.customerName}`}
              >
                <ChevronRight size={14} color={theme.color.mutedForeground as any} />
              </TouchableOpacity>
            </View>
          </View>
        </View>

        {/* Case sub-header removed (merged into tabs card for parity with CustomerDetail) */}

        {/* Case details sub-tabs (from CustomerDetail) */}
        <Card
          variant="flat"
          style={{
            paddingTop: 4,
            paddingHorizontal: 12,
            paddingBottom: 16,
            marginBottom: 0,
            flex: 1,
            ...(Platform.select({
              ios: { shadowColor: 'transparent', shadowOpacity: 0, shadowRadius: 0, shadowOffset: { width: 0, height: 0 } },
              android: { elevation: 0 },
              default: {},
            }) as any),
          }}
        >
        {/* Case header moved above; content starts with tabs */}
        {/* Slightly wider content area after header */}
        <View style={{ marginHorizontal: -6, flex: 1, minHeight: 0 }}>
          <View style={{ backgroundColor: theme.color.muted, borderRadius: theme.radius.md, padding: 6, marginTop: 0, marginBottom: 10, flexDirection: 'row' }}>
            {([
              { key: 'overview', label: 'Overview' },
              { key: 'documents', label: 'Documents' },
              { key: 'history', label: 'History' },
              { key: 'chat', label: 'Chat' },
            ] as const).map(t => (
              <TouchableOpacity
                key={t.key}
                onPress={() => setCaseDetailsTab(t.key)}
                style={{
                  flexDirection: 'row',
                  alignItems: 'center',
                  justifyContent: 'center',
                  paddingVertical: 9,
                  borderRadius: theme.radius.sm,
                  backgroundColor: caseDetailsTab === t.key ? theme.color.card : 'transparent',
                  marginBottom: 0,
                  ...(t.key !== 'chat' ? { marginRight: 6 } : {}),
                  flex: 1
                }}
              >
                <Text style={{ color: caseDetailsTab === t.key ? theme.color.primary : theme.color.mutedForeground, fontSize: 13, fontWeight: '700' }}>{t.label}</Text>
              </TouchableOpacity>
            ))}
          </View>

          {/* Tab content area */}
          {caseDetailsTab === 'chat' ? (
          <View style={{ flex: 1, minHeight: 0 }}>
            <ScrollView 
              ref={scrollViewRef}
              nestedScrollEnabled
              showsVerticalScrollIndicator={false}
              keyboardShouldPersistTaps="always"
              style={{ flex: 1 }}
              contentContainerStyle={{ paddingBottom: 8 }}
            >
            {messages.map((message) => (
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
          </View>
          ) : (
          <View style={{ flex: 1, minHeight: 0 }}>
            <ScrollView
              nestedScrollEnabled
              showsVerticalScrollIndicator
              keyboardShouldPersistTaps="always"
              style={{ flex: 1 }}
              contentContainerStyle={{ paddingBottom: 8 }}
            >
              {(() => {
                const caseType = pickCaseType(conversation.tags || [])
                if (caseDetailsTab === 'overview') {
                  const aiActionsByType: Record<string, string[]> = {
                    billing: ['Collected invoice IDs from user', 'Summarized billing history to the customer'],
                    request: ['Captured user use-case and frequency'],
                    bug: ['Guided user through workaround', 'Checked rate limits and recent errors'],
                    complaint: ['Analyzed queue wait times'],
                    inquiry: ['Provided feature overview and examples'],
                    feedback: ['Tagged product team with summary'],
                  }
                  const requiredActionsByType: Record<string, string[]> = {
                    billing: ['Verify last two invoices', 'Issue refund for duplicate charge'],
                    request: ['Create product ticket and tag as feature'],
                    bug: ['Escalate to the engineering team', 'Verify recent errors and logs'],
                    complaint: ['Apologize and share SLA timeline'],
                    inquiry: ['Share documentation and clarify usage'],
                    feedback: [],
                  }
                  const aiActions = aiActionsByType[caseType] || []
                  const requiredActions = requiredActionsByType[caseType] || []
                  return (
                    <View>
                      {/* AI Diagnoses */}
                      <View style={{ marginBottom: 0 }}>
                        <View style={{ flexDirection: 'row', alignItems: 'center', gap: 8, marginBottom: 6 }}>
                          <ClipboardList size={14} color={theme.color.mutedForeground as any} />
                          <Text style={{ color: theme.color.cardForeground, fontSize: 14, fontWeight: '700' }}>AI Diagnoses</Text>
                        </View>
                        <Text style={{ color: theme.color.mutedForeground, fontSize: 13, lineHeight: 20 }}>
                          {getCaseSummary(caseType)}
                        </Text>
                      </View>
                      {/* Separator */}
                      <View style={{ height: 1, backgroundColor: theme.color.border, marginTop: 8, marginBottom: 8 }} />
                      {/* AI Actions Taken */}
                      <View style={{ marginBottom: 0 }}>
                        <View style={{ flexDirection: 'row', alignItems: 'center', gap: 8, marginBottom: 6 }}>
                          <Bot size={14} color={theme.color.mutedForeground as any} />
                          <Text style={{ color: theme.color.cardForeground, fontSize: 14, fontWeight: '700' }}>AI Actions Taken</Text>
                        </View>
                        <View style={{ gap: 8 }}>
                          {aiActions.length === 0 ? (
                            <Text style={{ color: theme.color.mutedForeground, fontSize: 13 }}>No AI actions recorded.</Text>
                          ) : (
                            aiActions.map((a, i) => (
                              <View key={i} style={{ flexDirection: 'row', alignItems: 'flex-start' }}>
                                <Text style={{ color: theme.color.cardForeground, fontSize: 13, flex: 1 }}>{a}</Text>
                              </View>
                            ))
                          )}
                        </View>
                      </View>
                      {/* Separator */}
                      <View style={{ height: 1, backgroundColor: theme.color.border, marginTop: 8, marginBottom: 8 }} />
                      {/* Suggested Actions */}
                      <View>
                        <View style={{ flexDirection: 'row', alignItems: 'center', gap: 8, marginBottom: 6 }}>
                          <ClipboardList size={14} color={theme.color.mutedForeground as any} />
                          <Text style={{ color: theme.color.cardForeground, fontSize: 14, fontWeight: '700' }}>Suggested Actions</Text>
                        </View>
                        <View style={{ gap: 8 }}>
                          {requiredActions.length === 0 ? (
                            <Text style={{ color: theme.color.mutedForeground, fontSize: 13 }}>No pending actions.</Text>
                          ) : (
                            requiredActions.map((a, i) => (
                              <View key={i} style={{ flexDirection: 'row', alignItems: 'flex-start' }}>
                                <Text style={{ color: theme.color.cardForeground, fontSize: 13, flex: 1 }}>{a}</Text>
                              </View>
                            ))
                          )}
                        </View>
                      </View>
                    </View>
                  )
                }
                if (caseDetailsTab === 'documents') {
                  return (
                    <View>
                      {/* Grabbed Documents */}
                      <View>
                        <View style={{ flexDirection: 'row', alignItems: 'center', gap: 8, marginBottom: 6 }}>
                          <FileText size={14} color={theme.color.mutedForeground as any} />
                          <Text style={{ color: theme.color.cardForeground, fontSize: 14, fontWeight: '700' }}>Grabbed Documents</Text>
                        </View>
                        <View style={{ gap: 2 }}>
                          {['Invoice_2024-01-13_1025.pdf','Chat_Transcript_2024-01-15.txt','Screenshot_Account_Settings.png'].map((name, i) => (
                            <View key={i} style={{ flexDirection: 'row', alignItems: 'center', justifyContent: 'space-between' }}>
                              <Text style={{ color: theme.color.cardForeground, fontSize: 13, flex: 1, marginRight: 12 }} numberOfLines={1}>
                                {name}
                              </Text>
                              <View style={{ flexDirection: 'row', alignItems: 'center', gap: 2 }}>
                                <TouchableOpacity
                                  onPress={() => Alert.alert('Open Document', name)}
                                  style={{ padding: 6, borderRadius: 8, backgroundColor: theme.dark ? 'rgba(255,255,255,0.06)' : 'rgba(0,0,0,0.04)' }}
                                  accessibilityRole="button"
                                  accessibilityLabel={`Open ${name}`}
                                >
                                  <FileText size={16} color={theme.color.primary as any} />
                                </TouchableOpacity>
                                <TouchableOpacity
                                  onPress={() => Alert.alert('Download Document', name)}
                                  style={{ padding: 6, borderRadius: 8, backgroundColor: theme.dark ? 'rgba(255,255,255,0.06)' : 'rgba(0,0,0,0.04)' }}
                                  accessibilityRole="button"
                                  accessibilityLabel={`Download ${name}`}
                                >
                                  <Download size={16} color={theme.color.success as any} />
                                </TouchableOpacity>
                              </View>
                            </View>
                          ))}
                        </View>
                      </View>
                    </View>
                  )
                }
                if (caseDetailsTab === 'history') {
                  return (
                    <View>
                      {/* Case Updates */}
                      <View>
                        <View style={{ flexDirection: 'row', alignItems: 'center', gap: 8, marginBottom: 6 }}>
                          <MessageCircle size={14} color={theme.color.mutedForeground as any} />
                          <Text style={{ color: theme.color.cardForeground, fontSize: 14, fontWeight: '700' }}>Case Updates</Text>
                        </View>
                        <View style={{ gap: 8 }}>
                          {[
                            { id: 'u1', date: 'Jan 15, 10:32 AM', channel: 'whatsapp', text: 'Customer requested status on duplicate charge and expressed dissatisfaction; escalation requested.' },
                            { id: 'u2', date: 'Jan 15, 11:05 AM', channel: 'email', text: 'Refund timeline communicated (3–5 business days); case escalated to billing for expedited review.' },
                            { id: 'u3', date: 'Jan 16, 09:02 AM', channel: 'web', text: 'Customer requested daily status updates pending refund confirmation.' },
                            { id: 'u4', date: 'Jan 17, 08:45 AM', channel: 'email', text: 'Refund initiated; transaction reference provided; advised to verify statement within 24–48 hours.' },
                          ].map((u, idx, arr) => (
                            <View key={u.id}>
                              <View style={{ flexDirection: 'row', alignItems: 'center', justifyContent: 'space-between', marginBottom: 2 }}>
                                <View style={{ flexDirection: 'row', alignItems: 'center', gap: 6 }}>
                                  <Text style={{ color: theme.color.mutedForeground, fontSize: 12 }}>{u.date}</Text>
                                  {(() => { const cs = getChannelStyle(u.channel); return (
                                    <View style={{ backgroundColor: cs.bg as any, paddingHorizontal: 6, paddingVertical: 2, borderRadius: theme.radius.sm }}>
                                      <Text style={{ color: (theme.dark ? ('#ffffff' as any) : (cs.color as any)), fontSize: 10, fontWeight: '700' }}>{cs.label}</Text>
                                    </View>
                                  )})()}
                                </View>
                                <Text style={{ color: theme.color.mutedForeground, fontSize: 12 }}>Session Summary</Text>
                              </View>
                              <Text style={{ color: theme.color.cardForeground, fontSize: 13 }}>
                                {u.text}
                              </Text>
                              {idx !== arr.length - 1 && (
                                <View style={{ height: 1, backgroundColor: theme.color.border, marginTop: 8, marginBottom: 8 }} />
                              )}
                            </View>
                          ))}
                        </View>
                      </View>
                    </View>
                  )
                }
                return null
              })()}
            </ScrollView>
          </View>
        )}
        </View>
        </Card>

        {/* Back/Close button (full width) */}
        <Button title="Close" variant="default" size="lg" fullWidth onPress={onClose} />
      </KeyboardAvoidingView>
    </Modal>
  )
}
