import React from 'react'
import { View, Text, TouchableOpacity } from 'react-native'
import { useTheme } from '../../providers/ThemeProvider'
import { Card } from '../../components/ui/Card'
import { Badge } from '../../components/ui/Badge'
import { 
  Clock,
  CheckCircle2,
  Crown,
  Flame,
  Timer,
  ChevronRight,
  ClipboardList,
  User
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

  // Case helpers (align with CustomerDetail/ChatScreen)
  const formatShortDateTime = (iso: string) => new Date(iso).toLocaleString('en-US', { month: 'short', day: 'numeric' })
  const getCaseIdRef = (id: string) => `C-${id.toString().padStart(4, '0')}`
  const pickCaseType = (tags: string[]): 'billing' | 'bug' | 'request' | 'inquiry' | 'complaint' | 'feedback' => {
    const lower = (tags || []).map(t => t.toLowerCase())
    if (lower.includes('billing')) return 'billing'
    if (lower.includes('technical') || lower.includes('integration') || lower.includes('api')) return 'bug'
    if (lower.includes('complaint')) return 'complaint'
    if (lower.includes('request') || lower.includes('feature')) return 'request'
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
  const bgAlpha = (c: string) =>
    (typeof c === 'string' && c.startsWith('hsl('))
      ? c.replace('hsl(', 'hsla(').replace(')', `,${theme.dark ? '0.20' : '0.12'})`)
      : (theme.dark ? 'rgba(255,255,255,0.12)' : 'rgba(0,0,0,0.06)')

  const caseId = getCaseIdRef(conversation.id)
  const date = formatShortDateTime(conversation.startedAt)
  const caseType = pickCaseType(conversation.tags || [])
  const typeColor = getCaseTypeColor(caseType)
  const { color: prColor, Icon: PrIcon } = getPriorityMeta(conversation.priority)
  const title = (() => {
    if ((conversation.tags || []).length > 0) {
      const t = (conversation.tags || [])[0].toLowerCase()
      if (t === 'billing') return 'Billing issue'
      if (t === 'technical' || t === 'integration') return 'Integration/technical issue'
      if (t === 'question') return 'Product question'
      if (t === 'support') return 'Support request'
    }
    const txt = conversation.lastMessage?.text || ''
    return txt.length > 60 ? txt.slice(0, 57) + '…' : txt || 'Customer case'
  })()
  const phone = (conversation as any).customerPhone || '+1 (415) 555-0137'
  
  type Channel = 'email' | 'web' | 'whatsapp' | 'instagram' | 'twitter' | 'messenger'
  const detectChannel = (): Channel => {
    const lower = (conversation.tags || []).map(t => t.toLowerCase())
    if (lower.includes('whatsapp')) return 'whatsapp'
    if (lower.includes('instagram')) return 'instagram'
    if (lower.includes('messenger')) return 'messenger'
    if (lower.includes('twitter')) return 'twitter'
    if (lower.includes('email')) return 'email'
    if (lower.includes('web')) return 'web'
    return conversation.customerEmail ? 'email' : 'web'
  }
  const getChannelChip = (channel: Channel) => {
    const labelMap: Record<Channel, string> = {
      email: 'EM', web: 'WEB', whatsapp: 'WA', instagram: 'IG', twitter: 'TW', messenger: 'MS'
    }
    const colorMap: Record<Channel, string> = {
      email: theme.color.primary as any,
      web: 'hsl(210,10%,60%)',
      whatsapp: 'hsl(142,71%,45%)',
      instagram: 'hsl(291,70%,55%)',
      twitter: 'hsl(200,90%,50%)',
      messenger: 'hsl(240,75%,48%)'
    }
    const withAlpha = (c: string, a: number) =>
      c.startsWith('hsl(')
        ? c.replace('hsl(', 'hsla(').replace(')', `,${a})`)
        : c
    const bg = withAlpha(colorMap[channel], theme.dark ? 0.20 : 0.12)
    const fg = colorMap[channel]
    return (
      <View style={{ flexDirection: 'row', alignItems: 'center', backgroundColor: bg as any, borderRadius: 12, paddingHorizontal: 8, paddingVertical: 3 }}>
        <Text style={{ color: fg as any, fontSize: 11, fontWeight: '800' }}>{labelMap[channel]}</Text>
      </View>
    )
  }

  const getCaseSummary = (type: string) => {
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
    const lines = byType[type] || []
    return lines.slice(0, 2).join('\n')
  }

  return (
    <TouchableOpacity onPress={() => onPress(conversation)}>
      <Card variant="flat" style={{ 
        marginBottom: 12,
        backgroundColor: theme.dark ? (theme.color.secondary as any) : (theme.color.accent as any),
        paddingHorizontal: 16,
        paddingVertical: 14
      }}>
        {/* ID left • Date right */}
        <View style={{ flexDirection: 'row', alignItems: 'center', justifyContent: 'space-between', marginBottom: 4 }}>
          <Text style={{ color: theme.color.mutedForeground, fontSize: 12 }} numberOfLines={1}>
            {caseId}
          </Text>
          <Text style={{ color: theme.color.mutedForeground, fontSize: 12 }}>
            {date}
          </Text>
        </View>

        {/* Title + chips */}
        <View style={{ flexDirection: 'row', alignItems: 'center', justifyContent: 'space-between', gap: 10 }}>
          <Text style={{ color: theme.color.cardForeground, fontSize: 15, fontWeight: '700', flex: 1, minWidth: 0 }} numberOfLines={2}>
            {title}
          </Text>
          <View style={{ flexDirection: 'row', alignItems: 'center', gap: 8, flexShrink: 0 }}>
            <View style={{ backgroundColor: typeColor as any, borderRadius: 12, paddingHorizontal: 6, paddingVertical: 3 }}>
              <Text style={{ color: '#ffffff', fontSize: 11, fontWeight: '800' }}>{caseType.toUpperCase()}</Text>
            </View>
            <View style={{ flexDirection: 'row', alignItems: 'center', gap: 6, backgroundColor: bgAlpha(prColor as any) as any, borderRadius: 12, paddingHorizontal: 6, paddingVertical: 3 }}>
              <PrIcon size={12} color={prColor as any} />
              <Text style={{ color: prColor as any, fontSize: 11, fontWeight: '700' }}>{conversation.priority.toUpperCase()}</Text>
            </View>
            <ChevronRight size={16} color={theme.color.mutedForeground as any} />
          </View>
        </View>

        {/* AI Diagnoses */}
        <View style={{ marginTop: 8 }}>
          <View style={{ flexDirection: 'row', alignItems: 'center', gap: 8, marginBottom: 6 }}>
            <ClipboardList size={14} color={theme.color.mutedForeground as any} />
            <Text style={{ color: theme.color.cardForeground, fontSize: 14, fontWeight: '700' }}>AI Diagnoses</Text>
          </View>
          <Text style={{ color: theme.color.mutedForeground, fontSize: 13, lineHeight: 20 }} numberOfLines={3}>
            {getCaseSummary(caseType)}
          </Text>
        </View>

        {/* Divider under description */}
        <View style={{ height: 1, backgroundColor: theme.color.border, marginTop: 8, marginBottom: 8 }} />

        {/* Minimal Customer Details inline: [User] Name | Phone   [Channel chip right-aligned] */}
        <View style={{ flexDirection: 'row', alignItems: 'center' }}>
          {/* Left group: name + phone share space */}
          <View style={{ flex: 1, minWidth: 0, flexDirection: 'row', alignItems: 'center' }}>
            <User size={14} color={theme.color.mutedForeground as any} style={{ marginRight: 6 }} />
            <Text
              numberOfLines={1}
              style={{ color: theme.color.cardForeground, fontSize: 13, fontWeight: '600', flexShrink: 1, minWidth: 0, marginRight: 8 }}
            >
              {conversation.customerName}
            </Text>
            <Text
              numberOfLines={1}
              style={{ color: theme.color.mutedForeground, fontSize: 12, flexShrink: 1, minWidth: 0 }}
            >
              {phone}
            </Text>
          </View>
          {/* Right: channel chip */}
          <View style={{ flexShrink: 0, marginLeft: 12 }}>
            {getChannelChip(detectChannel())}
          </View>
        </View>
      </Card>
    </TouchableOpacity>
  )
}
