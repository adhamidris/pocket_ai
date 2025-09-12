import React, { useState } from 'react'
import { View, Text, ScrollView, TouchableOpacity, Platform, Alert } from 'react-native'
import { useTheme } from '../../providers/ThemeProvider'
import { Card } from '../../components/ui/Card'
import { Badge } from '../../components/ui/Badge'
import { Button } from '../../components/ui/Button'
import { Modal } from '../../components/ui/Modal'
import { 
  User, 
  Mail, 
  Phone, 
  MapPin,
  Calendar,
  MessageCircle,
  Crown,
  Edit,
  Plus,
  Tag,
  Activity,
  Briefcase,
  AlertTriangle,
  CheckCircle2,
  Bot,
  ClipboardList,
  Smile,
  Meh,
  Frown,
  Clock,
  Timer,
  Flame
} from 'lucide-react-native'
import { Copy } from 'lucide-react-native'

interface Customer {
  id: string
  name: string
  email: string
  phone?: string
  address?: string
  avatar?: string
  status: 'active' | 'inactive' | 'vip'
  lastContact: string
  totalConversations: number
  satisfaction: number
  tags: string[]
  totalValue: number
  joinedDate: string
}

interface CustomerDetailProps {
  visible: boolean
  customer: Customer | null
  onClose: () => void
}

export const CustomerDetail: React.FC<CustomerDetailProps> = ({ 
  visible, 
  customer, 
  onClose 
}) => {
  const { theme } = useTheme()
  const [activeTab, setActiveTab] = useState<'overview' | 'activity' | 'notes' | 'cases'>('overview')
  const [caseFilter, setCaseFilter] = useState<'needs' | 'resolved'>('needs')

  if (!customer) return null

  const getInitials = (name: string) => {
    return name.split(' ').map(n => n[0]).join('').toUpperCase().slice(0, 2)
  }

  const getStatusVariant = (status: string) => {
    switch (status) {
      case 'vip': return 'warning'
      case 'active': return 'success'
      default: return 'secondary'
    }
  }

  const formatValue = (value: number) => {
    if (value >= 1000) {
      return `$${(value / 1000).toFixed(1)}k`
    }
    return `$${value}`
  }

  const formatDate = (dateString: string) => {
    return new Date(dateString).toLocaleDateString('en-US', {
      year: 'numeric',
      month: 'short',
      day: 'numeric'
    })
  }

  const tabs = [
    { key: 'overview' as const, label: 'Overview', icon: User },
    { key: 'cases' as const, label: 'Cases', icon: Briefcase },
    { key: 'activity' as const, label: 'Activity', icon: Activity },
    { key: 'notes' as const, label: 'Notes', icon: Edit },
  ]

  const mockActivity = [
    {
      id: '1',
      type: 'conversation',
      title: 'Started chat conversation',
      description: 'Customer initiated support chat about billing inquiry',
      timestamp: '2 hours ago',
      agent: 'Nancy'
    },
    {
      id: '2', 
      type: 'note',
      title: 'Added note',
      description: 'Customer prefers email communication over phone calls',
      timestamp: '1 day ago',
      agent: 'You'
    },
    {
      id: '3',
      type: 'conversation',
      title: 'Resolved technical issue',
      description: 'Successfully helped customer with account access problem',
      timestamp: '3 days ago',
      agent: 'Jack'
    }
  ]

  type CaseType = 'inquiry' | 'request' | 'lead' | 'complaint' | 'bug' | 'billing' | 'feedback'
  type CaseTone = 'positive' | 'neutral' | 'negative' | 'frustrated'
  type CaseStatus = 'needs' | 'resolved'
  interface CaseItem {
    id: string
    title: string
    type: CaseType
    tone: CaseTone
    status: CaseStatus
    priority: 'low' | 'medium' | 'high'
    requiredActions: string[]
    aiActions: string[]
    createdAt: string
    updatedAt?: string
  }

  const mockCases: CaseItem[] = [
    {
      id: 'C-1024',
      title: 'Double charge',
      type: 'billing',
      tone: 'frustrated',
      status: 'needs',
      priority: 'high',
      requiredActions: [
        'Verify last two invoices',
        'Issue refund for duplicate charge',
      ],
      aiActions: [
        'Collected invoice IDs from user',
        'Summarized billing history to the customer',
      ],
      createdAt: '2024-01-15T10:36:00Z',
    },
    {
      id: 'C-1025',
      title: 'Export analytics',
      type: 'request',
      tone: 'neutral',
      status: 'needs',
      priority: 'medium',
      requiredActions: [
        'Create product ticket and tag as feature',
      ],
      aiActions: [
        'Captured user use-case and frequency',
      ],
      createdAt: '2024-01-14T14:10:00Z',
    },
    {
      id: 'C-1026',
      title: 'Password reset failing',
      type: 'bug',
      tone: 'negative',
      status: 'needs',
      priority: 'high',
      requiredActions: [
        'Escalate to auth team',
        'Verify reset email delivery & logs',
      ],
      aiActions: [
        'Guided user through reset flow',
        'Checked rate limits and recent errors',
      ],
      createdAt: '2024-01-15T08:05:00Z',
    },
    {
      id: 'C-1027',
      title: 'Upgrade plan inquiry',
      type: 'inquiry',
      tone: 'neutral',
      status: 'needs',
      priority: 'low',
      requiredActions: [
        'Share pricing tiers and limits',
      ],
      aiActions: [
        'Summarized Pro vs. Enterprise features',
      ],
      createdAt: '2024-01-13T11:22:00Z',
    },
    {
      id: 'C-1028',
      title: 'Slack integration request',
      type: 'request',
      tone: 'positive',
      status: 'needs',
      priority: 'medium',
      requiredActions: [
        'Provide early-access steps',
      ],
      aiActions: [
        'Collected workspace URL and scope needs',
      ],
      createdAt: '2024-01-13T09:40:00Z',
    },
    {
      id: 'C-1019',
      title: 'Webhook timeout',
      type: 'bug',
      tone: 'positive',
      status: 'resolved',
      priority: 'low',
      requiredActions: [],
      aiActions: [
        'Restarted integration connector via MCP',
        'Replayed failed events (last 50)',
      ],
      createdAt: '2024-01-12T09:20:00Z',
      updatedAt: '2024-01-12T10:05:00Z',
    },
    {
      id: 'C-1018',
      title: 'Billing address update',
      type: 'billing',
      tone: 'neutral',
      status: 'resolved',
      priority: 'low',
      requiredActions: [
        'Confirm address change with finance',
      ],
      aiActions: [
        'Validated new address format',
      ],
      createdAt: '2024-01-10T15:00:00Z',
      updatedAt: '2024-01-10T16:12:00Z',
    },
    {
      id: 'C-1017',
      title: 'Feature feedback on dashboard',
      type: 'feedback',
      tone: 'positive',
      status: 'resolved',
      priority: 'medium',
      requiredActions: [],
      aiActions: [
        'Summarized feedback and tagged product',
      ],
      createdAt: '2024-01-09T13:35:00Z',
      updatedAt: '2024-01-09T14:10:00Z',
    },
    {
      id: 'C-1029',
      title: 'Response delay complaint',
      type: 'complaint',
      tone: 'negative',
      status: 'needs',
      priority: 'medium',
      requiredActions: [
        'Apologize and share SLA timeline',
      ],
      aiActions: [
        'Analyzed queue wait times',
      ],
      createdAt: '2024-01-15T07:55:00Z',
    },
  ]

  const formatShortDateTime = (iso: string) => {
    const d = new Date(iso)
    return d.toLocaleString('en-US', { month: 'short', day: 'numeric' })
  }

  const getCaseTypeColor = (t: CaseType) => {
    switch (t) {
      case 'billing': return theme.color.warning
      case 'bug': return theme.color.error
      case 'complaint': return theme.color.error
      case 'request': return theme.color.primary
      case 'inquiry': return 'hsl(200,90%,50%)' // cyan
      case 'lead': return 'hsl(142,71%,45%)' // green
      case 'feedback': return 'hsl(262,83%,58%)' // purple
      default: return theme.color.mutedForeground
    }
  }

  const getToneMeta = (tone: CaseTone) => {
    switch (tone) {
      case 'positive': return { color: theme.color.success, Icon: Smile }
      case 'neutral': return { color: theme.color.mutedForeground, Icon: Meh }
      case 'negative': return { color: theme.color.error, Icon: Frown }
      case 'frustrated': return { color: theme.color.warning, Icon: AlertTriangle }
      default: return { color: theme.color.mutedForeground, Icon: Meh }
    }
  }

  const getPriorityMeta = (p: 'low' | 'medium' | 'high') => {
    switch (p) {
      case 'low': return { color: theme.color.mutedForeground, Icon: Clock }
      case 'medium': return { color: theme.color.warning, Icon: Timer }
      case 'high': return { color: theme.color.error, Icon: Flame }
      default: return { color: theme.color.mutedForeground, Icon: Clock }
    }
  }

  const getCaseSummary = (c: CaseItem) => {
    // 2–3 straight-to-the-point lines describing the case only
    const byType: Record<CaseType, string[]> = {
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
      lead: [
        'Prospective customer expressed interest.',
        'Assessing solution fit for their use case.'
      ],
      feedback: [
        'Constructive feedback shared on product/support.',
        'Captured for future improvement planning.'
      ],
    }
    const lines = byType[c.type] || []
    return lines.slice(0, 3).join('\n')
  }

  const formatCustomerId = (id: string) => {
    const padded = id.toString().padStart(4, '0')
    return `CUST-${padded}`
  }

  const getTagColor = (tag: string) => {
    const t = tag.toLowerCase()
    if (t === 'billing') return theme.color.warning
    if (t === 'technical') return theme.color.primary
    if (t === 'integration') return 'hsl(190,90%,45%)' // teal
    if (t === 'enterprise') return 'hsl(262,83%,58%)' // purple
    if (t === 'startup') return 'hsl(200,90%,50%)' // cyan
    return theme.color.mutedForeground
  }

  return (
    <Modal visible={visible} onClose={onClose} size="lg">
      <View style={{ flex: 1 }}>
        <View style={{ flex: 1, minHeight: 0, paddingBottom: 12 }}>
          {/* Customer Header */}
        <View style={{
          flexDirection: 'row',
          alignItems: 'center',
          marginBottom: 12,
          paddingBottom: 12,
          borderBottomWidth: 1,
          borderBottomColor: theme.color.border
        }}>
          {/* Avatar */}
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
              {getInitials(customer.name)}
            </Text>
          </View>

          {/* Info */}
          <View style={{ flex: 1 }}>
            <View style={{ flexDirection: 'row', alignItems: 'center', justifyContent: 'space-between', gap: 8, marginBottom: 2 }}>
              <View style={{ flexDirection: 'row', alignItems: 'center', gap: 8, flexShrink: 1 }}>
                <Text style={{
                  color: theme.color.cardForeground,
                  fontSize: 18,
                  fontWeight: '700',
                  flexShrink: 1
                }} numberOfLines={1}>
                  {customer.name}
                </Text>
                {customer.status === 'vip' && (
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
              <TouchableOpacity style={{
                width: 32,
                height: 32,
                backgroundColor: theme.dark ? theme.color.secondary : theme.color.card,
                borderRadius: 16,
                alignItems: 'center',
                justifyContent: 'center'
              }}>
                <Edit size={16} color={theme.color.mutedForeground as any} />
              </TouchableOpacity>
            </View>
            
            <View style={{ flexDirection: 'row', alignItems: 'center', gap: 8, marginBottom: 4 }}>
              <Text style={{ color: theme.color.mutedForeground, fontSize: 12 }}>
                Customer ID: {formatCustomerId(customer.id)}
              </Text>
              <TouchableOpacity
                onPress={() => {
                  const id = formatCustomerId(customer.id)
                  // Best-effort copy: works on web, shows confirmation elsewhere
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
            
            <Text style={{
              color: theme.color.mutedForeground,
              fontSize: 12
            }}>
              Customer since {formatDate(customer.joinedDate)}
            </Text>
          </View>

          {/* Actions moved into name row */}
        </View>

        {/* Tabs (moved above Contact Info) */}
        <View style={{ flexDirection: 'row', marginBottom: 12, backgroundColor: theme.color.muted, borderRadius: theme.radius.md, padding: 6 }}>
          {tabs.map((tab) => (
            <TouchableOpacity
              key={tab.key}
              onPress={() => setActiveTab(tab.key)}
              style={{
                flex: 1,
                flexDirection: 'column',
                alignItems: 'center',
                justifyContent: 'center',
                gap: 4,
                paddingVertical: 10,
                paddingHorizontal: 12,
                borderRadius: theme.radius.sm,
                backgroundColor: activeTab === tab.key 
                  ? theme.color.card 
                  : 'transparent'
              }}
            >
              <tab.icon 
                size={18} 
                color={activeTab === tab.key 
                  ? theme.color.primary 
                  : theme.color.mutedForeground
                } 
              />
              <Text numberOfLines={1} ellipsizeMode="clip" allowFontScaling={false} style={{
                color: activeTab === tab.key 
                  ? theme.color.primary 
                  : theme.color.mutedForeground,
                fontSize: 12,
                fontWeight: '600'
              }}>
                {tab.label}
              </Text>
            </TouchableOpacity>
          ))}
        </View>

        {/* Overview content */}
        {activeTab === 'overview' && (
          <Card
            variant="flat"
            style={{
              paddingHorizontal: 16,
              paddingBottom: 16,
              paddingTop: 8,
              marginBottom: 16,
              ...(Platform.select({
                ios: {
                  shadowColor: 'transparent',
                  shadowOpacity: 0,
                  shadowRadius: 0,
                  shadowOffset: { width: 0, height: 0 },
                },
                android: { elevation: 0 },
                default: {},
              }) as any),
            }}
          >
            {/* Contact Information */}
            <View style={{ marginBottom: 8 }}>
              <View style={{ flexDirection: 'row', alignItems: 'center', gap: 8, marginBottom: 8 }}>
                <User size={16} color={theme.color.mutedForeground as any} />
                <Text style={{ color: theme.color.cardForeground, fontSize: 16, fontWeight: '600' }}>
                  Contact Information
                </Text>
              </View>
              {(() => {
                const rows: Array<{ key: string; icon: any; text: string }> = [
                  { key: 'email', icon: Mail, text: customer.email },
                  ...(customer.phone ? [{ key: 'phone', icon: Phone, text: customer.phone }] : []),
                  ...(customer.address ? [{ key: 'address', icon: MapPin, text: customer.address }] : []),
                ]
                return rows.map((row) => {
                  const IconComp = row.icon
                  return (
                    <View key={row.key} style={{ flexDirection: 'row', alignItems: 'center', gap: 12, paddingVertical: 6 }}>
                      <IconComp size={14} color={theme.color.mutedForeground as any} />
                      <Text style={{ color: theme.color.cardForeground, fontSize: 13, flex: 1 }} numberOfLines={1}>
                        {row.text}
                      </Text>
                    </View>
                  )
                })
              })()}
            </View>

            {/* Divider between sections */}
            <View style={{ height: 1, backgroundColor: theme.color.border, marginVertical: 6 }} />

            {/* Customer Stats */}
            <View style={{ paddingTop: 2, marginBottom: 8 }}>
              <View style={{ flexDirection: 'row', alignItems: 'center', gap: 8, marginBottom: 10 }}>
                <Activity size={16} color={theme.color.mutedForeground as any} />
                <Text style={{ color: theme.color.cardForeground, fontSize: 16, fontWeight: '600' }}>
                  Customer Stats
                </Text>
              </View>
              <View style={{ flexDirection: 'row', justifyContent: 'space-between' }}>
                <View style={{ alignItems: 'center', flex: 1 }}>
                  <Text style={{ color: theme.color.primary, fontSize: 18, fontWeight: '700', marginBottom: 2 }}>
                    {customer.totalConversations}
                  </Text>
                  <Text style={{ color: theme.color.mutedForeground, fontSize: 12, textAlign: 'center' }}>
                    Conversations
                  </Text>
                </View>
                <View style={{ alignItems: 'center', flex: 1 }}>
                  <Text style={{ color: theme.color.warning, fontSize: 18, fontWeight: '700', marginBottom: 2 }}>
                    {customer.satisfaction}%
                  </Text>
                  <Text style={{ color: theme.color.mutedForeground, fontSize: 12, textAlign: 'center' }}>
                    Satisfaction
                  </Text>
                </View>
                <View style={{ alignItems: 'center', flex: 1 }}>
                  <Text style={{ color: theme.color.success, fontSize: 18, fontWeight: '700', marginBottom: 2 }}>
                    {formatValue(customer.totalValue)}
                  </Text>
                  <Text style={{ color: theme.color.mutedForeground, fontSize: 12, textAlign: 'center' }}>
                    Value
                  </Text>
                </View>
              </View>
            </View>

            {/* Divider between sections */}
            <View style={{ height: 1, backgroundColor: theme.color.border, marginVertical: 6 }} />

            {/* Tags */}
            <View>
              <View style={{ flexDirection: 'row', justifyContent: 'space-between', alignItems: 'center', marginBottom: 10 }}>
                <View style={{ flexDirection: 'row', alignItems: 'center', gap: 8 }}>
                  <Tag size={16} color={theme.color.mutedForeground as any} />
                  <Text style={{ color: theme.color.cardForeground, fontSize: 16, fontWeight: '600' }}>
                    Tags
                  </Text>
                </View>
                <TouchableOpacity style={{ width: 32, height: 32, backgroundColor: theme.dark ? theme.color.secondary : theme.color.card, borderRadius: 16, alignItems: 'center', justifyContent: 'center' }}>
                  <Plus size={16} color={theme.color.mutedForeground as any} />
                </TouchableOpacity>
              </View>
              <View style={{ flexDirection: 'row', flexWrap: 'wrap', gap: 8 }}>
                {customer.tags.map((tag, index) => {
                  const color = getTagColor(tag)
                  return (
                    <View
                      key={index}
                      style={{
                        backgroundColor: theme.color.card,
                        paddingHorizontal: 8,
                        paddingVertical: 3,
                        borderRadius: theme.radius.sm,
                        flexDirection: 'row',
                        alignItems: 'center',
                        gap: 6,
                        borderWidth: 0,
                        borderColor: 'transparent'
                      }}
                    >
                      <View style={{ width: 5, height: 5, borderRadius: 2.5, backgroundColor: color as any }} />
                      <Text style={{ color: theme.color.mutedForeground, fontSize: 11, fontWeight: '500' }}>
                        {tag}
                      </Text>
                    </View>
                  )
                })}
              </View>
            </View>
          </Card>
        )}

        {/* Tabs moved above */}

        {/* Tab Content */}
        {activeTab === 'cases' && (
          <Card
            variant="flat"
            style={{
              padding: 16,
              marginBottom: 0,
              flex: 1,
              ...(Platform.select({
                ios: {
                  shadowColor: 'transparent',
                  shadowOpacity: 0,
                  shadowRadius: 0,
                  shadowOffset: { width: 0, height: 0 },
                },
                android: { elevation: 0 },
                default: {},
              }) as any),
            }}
          >

            {/* Sub-toggle: Needs Action / Resolved */}
            <View style={{ flexDirection: 'row', backgroundColor: theme.color.muted, borderRadius: theme.radius.md, padding: 6, marginBottom: 10, marginHorizontal: -16, marginTop: -4 }}>
              {([
                { key: 'needs', label: 'Needs Action' },
                { key: 'resolved', label: 'Resolved' },
              ] as const).map(t => (
                <TouchableOpacity
                  key={t.key}
                  onPress={() => setCaseFilter(t.key)}
                  style={{
                    flex: 1,
                    flexDirection: 'row',
                    alignItems: 'center',
                    justifyContent: 'center',
                    gap: 6,
                    paddingVertical: 10,
                    borderRadius: theme.radius.sm,
                    backgroundColor: caseFilter === t.key ? theme.color.card : 'transparent'
                  }}
                >
                  {t.key === 'needs' ? (
                    <AlertTriangle size={16} color={(caseFilter === t.key ? theme.color.primary : theme.color.mutedForeground) as any} />
                  ) : (
                    <CheckCircle2 size={16} color={(caseFilter === t.key ? theme.color.primary : theme.color.mutedForeground) as any} />
                  )}
                  <Text numberOfLines={1} ellipsizeMode="clip" allowFontScaling={false} style={{ color: caseFilter === t.key ? theme.color.primary : theme.color.mutedForeground, fontSize: 13, fontWeight: '700' }}>{t.label}</Text>
                </TouchableOpacity>
              ))}
            </View>

            {/* Vertically scrollable cases wrapper (inner only) */}
            <View style={{ flex: 1, minHeight: 0 }}>
              <ScrollView
                nestedScrollEnabled
                showsVerticalScrollIndicator
                keyboardShouldPersistTaps="always"
                onStartShouldSetResponderCapture={() => true}
                onMoveShouldSetResponderCapture={() => true}
                scrollEventThrottle={16}
                bounces
                decelerationRate="fast"
                style={{ flex: 1 }}
                contentContainerStyle={{ paddingBottom: 4 }}
              >
              {mockCases.filter(c => c.status === caseFilter).map((c, idx, arr) => (
                <View key={c.id} style={{ paddingVertical: 14, ...(idx !== arr.length - 1 ? { borderBottomWidth: 1, borderBottomColor: theme.color.border } : {}) }}>
                  {/* Date above title */}
                  <View style={{ marginBottom: 6 }}>
                    <Text style={{ color: theme.color.mutedForeground, fontSize: 12 }}>
                      {formatShortDateTime(c.createdAt)}
                    </Text>
                  </View>
                  {/* Title (left) + chips (right) */}
                  <View style={{ flexDirection: 'row', alignItems: 'center', justifyContent: 'space-between', gap: 10, marginBottom: 8 }}>
                    <Text style={{ color: theme.color.cardForeground, fontSize: 15, fontWeight: '700', flex: 1, minWidth: 0 }} numberOfLines={2} ellipsizeMode="tail">
                      {c.title}
                    </Text>
                    <View style={{ flexDirection: 'row', alignItems: 'center', gap: 8, flexShrink: 0 }}>
                      {/* Classification chip (type) */}
                      {(() => { const color = getCaseTypeColor(c.type); return (
                        <View style={{ backgroundColor: color as any, borderRadius: 12, paddingHorizontal: 6, paddingVertical: 3 }}>
                          <Text style={{ color: '#ffffff', fontSize: 11, fontWeight: '800' }}>{c.type.toUpperCase()}</Text>
                        </View>
                      )})()}
                      {/* Urgency chip (priority) */}
                      {(() => { const { color, Icon } = getPriorityMeta(c.priority); const bg = `hsla(${(color as string).includes('hsl(') ? (color as string).slice(4,-1) : '0,0%,0%'},${theme.dark ? '0.20' : '0.12'})`; return (
                        <View style={{ flexDirection: 'row', alignItems: 'center', gap: 6, backgroundColor: bg as any, borderRadius: 12, paddingHorizontal: 6, paddingVertical: 3 }}>
                          <Icon size={12} color={color as any} />
                          <Text style={{ color: color as any, fontSize: 11, fontWeight: '700' }}>{c.priority.toUpperCase()}</Text>
                        </View>
                      )})()}
                    </View>
                  </View>

                  {/* AI Diagnoses */}
                  <View>
                    <View style={{ flexDirection: 'row', alignItems: 'center', gap: 8, marginBottom: 6 }}>
                      <ClipboardList size={14} color={theme.color.mutedForeground as any} />
                      <Text style={{ color: theme.color.cardForeground, fontSize: 14, fontWeight: '700' }}>AI Diagnoses</Text>
                    </View>
                    <Text style={{ color: theme.color.mutedForeground, fontSize: 13, lineHeight: 20 }}>
                      {getCaseSummary(c)}
                    </Text>
                  </View>
                </View>
              ))}
              </ScrollView>
            </View>
          </Card>
        )}
        {activeTab === 'activity' && (
          <Card variant="flat">
            <Text style={{
              color: theme.color.cardForeground,
              fontSize: 16,
              fontWeight: '600',
              marginBottom: 16
            }}>
              Recent Activity
            </Text>
            
            <View style={{ gap: 16 }}>
              {mockActivity.map((activity) => (
                <View key={activity.id} style={{
                  flexDirection: 'row',
                  gap: 12,
                  paddingBottom: 16,
                  borderBottomWidth: activity.id !== mockActivity[mockActivity.length - 1].id ? 1 : 0,
                  borderBottomColor: theme.color.border
                }}>
                  <View style={{
                    width: 8,
                    height: 8,
                    backgroundColor: theme.color.mutedForeground,
                    borderRadius: 4,
                    marginTop: 6
                  }} />
                  <View style={{ flex: 1 }}>
                    <Text style={{
                      color: theme.color.cardForeground,
                      fontSize: 14,
                      fontWeight: '600',
                      marginBottom: 2
                    }}>
                      {activity.title}
                    </Text>
                    <Text style={{
                      color: theme.color.mutedForeground,
                      fontSize: 13,
                      marginBottom: 4
                    }}>
                      {activity.description}
                    </Text>
                    <Text style={{
                      color: theme.color.mutedForeground,
                      fontSize: 12
                    }}>
                      {activity.timestamp} • by {activity.agent}
                    </Text>
                  </View>
                </View>
              ))}
            </View>
          </Card>
        )}

        {activeTab === 'notes' && (
          <Card variant="flat">
            <View style={{
              flexDirection: 'row',
              justifyContent: 'space-between',
              alignItems: 'center',
              marginBottom: 16
            }}>
              <Text style={{
                color: theme.color.cardForeground,
                fontSize: 16,
                fontWeight: '600'
              }}>
                Notes
              </Text>
              <Button
                title="Add Note"
                variant="outline"
                size="sm"
                onPress={() => {}}
              />
            </View>
            
            <Text style={{
              color: theme.color.mutedForeground,
              textAlign: 'center',
              fontStyle: 'italic'
            }}>
              No notes yet. Add the first note to track important information about this customer.
            </Text>
          </Card>
        )}

        </View>

        {/* Fixed Action Footer */}
        <View style={{ gap: 12, paddingTop: 12, paddingBottom: 8, borderTopWidth: 1, borderTopColor: theme.color.border }}>
          <View style={{ flexDirection: 'row', gap: 12 }}>
            <TouchableOpacity
              onPress={() => {}}
              activeOpacity={0.85}
              style={{
                flex: 1,
                height: 48,
                borderRadius: 16,
                backgroundColor: theme.dark ? theme.color.secondary : 'hsl(240,6%,92%)',
                alignItems: 'center',
                justifyContent: 'center'
              }}
            >
              <View style={{ flexDirection: 'row', alignItems: 'center', gap: 8 }}>
                <Mail size={18} color={theme.color.primary as any} />
                <Text style={{ color: theme.color.cardForeground, fontSize: 16, fontWeight: '600' }}>Send Email</Text>
              </View>
            </TouchableOpacity>
            <TouchableOpacity
              onPress={() => {}}
              activeOpacity={0.85}
              style={{
                flex: 1,
                height: 48,
                borderRadius: 16,
                backgroundColor: theme.dark ? theme.color.secondary : 'hsl(240,6%,92%)',
                alignItems: 'center',
                justifyContent: 'center'
              }}
            >
              <View style={{ flexDirection: 'row', alignItems: 'center', gap: 8 }}>
                <Phone size={18} color={theme.color.success as any} />
                <Text style={{ color: theme.color.cardForeground, fontSize: 16, fontWeight: '600' }}>Call Customer</Text>
              </View>
            </TouchableOpacity>
          </View>

          {/* Exit button (full width) */}
          <Button
            title="Close"
            variant="default"
            size="lg"
            fullWidth
            onPress={onClose}
          />
        </View>
      </View>
    </Modal>
  )
}
