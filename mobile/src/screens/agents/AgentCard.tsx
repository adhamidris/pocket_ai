import React from 'react'
import { View, Text, TouchableOpacity } from 'react-native'
import { useTheme } from '../../providers/ThemeProvider'
import { Card } from '../../components/ui/Card'
import { Bot, ChevronRight } from 'lucide-react-native'

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
  role?: string
  roles?: string[]
  openCases?: number
  resolvedCases?: number
  satisfaction?: number
}

interface AgentCardProps {
  agent: Agent
  onToggleStatus: (id: string) => void
  onEdit: (agent: Agent) => void
  onDelete: (id: string) => void
  onPress?: (agent: Agent) => void
}

export const AgentCard: React.FC<AgentCardProps> = ({ 
  agent, 
  onToggleStatus, 
  onEdit, 
  onDelete,
  onPress,
}) => {
  const { theme } = useTheme()

  // Short role descriptions (match Agent Detail modal tone)
  const roleDescriptions: Record<string, string> = {
    'Support Agent': 'Handles customer issues and questions efficiently.',
    'Sales Associate': 'Engages leads and highlights value to convert.',
    'Technical Specialist': 'Guides users through technical workflows and fixes.',
    'Billing Assistant': 'Clarifies invoices, refunds, and payment matters.',
  }

  const getPersonaIcon = (persona: string) => {
    switch (persona) {
      case 'friendly': return '😊'
      case 'technical': return '🔧'
      case 'sales': return '💼'
      default: return '👔'
    }
  }

  // Department color no longer used after removing department/tools row

  const withAlpha = (c: string, a: number) =>
    c.startsWith('hsl(')
      ? c.replace('hsl(', 'hsla(').replace(')', `,${a})`)
      : c
  const dangerBg = withAlpha(theme.color.error, theme.dark ? 0.22 : 0.12)
  const hasRole = Boolean(agent.role || (agent.roles && agent.roles.length > 0))
  const hasDescription = Boolean((agent.description || '').trim().length > 0)
  // Demo usage (tokens→$): estimate spend from conversations or id; cap by total
  const totalBudget = 50 // $ demo budget
  const estimateSpent = () => {
    const conv = (agent.conversations as any) ?? 0
    const base = Math.min(totalBudget * 0.8, conv * 0.12)
    if (base > 0) return parseFloat(base.toFixed(2))
    const digits = ((agent.id as any).toString().match(/\d+/g) || ['0']).join('')
    const num = parseInt(digits || '0', 10)
    const deriv = ((num % 3000) / 3000) * (totalBudget * 0.6)
    return parseFloat(deriv.toFixed(2))
  }
  const spent = estimateSpent()
  const remaining = parseFloat(Math.max(0, totalBudget - spent).toFixed(2))

  const capitalizeFirst = (s: string) => {
    if (!s) return s
    const trimmed = s.trim()
    return trimmed.charAt(0).toUpperCase() + trimmed.slice(1)
  }

  // ID meta removed on card per request

  const CardContainer: React.FC<{ children: React.ReactNode }> = ({ children }) => {
    const content = (
      <Card
        variant="flat"
        style={{ marginBottom: 12, backgroundColor: theme.dark ? (theme.color.secondary as any) : (theme.color.accent as any), paddingHorizontal: 16, paddingVertical: 14 }}
      >
        {children}
      </Card>
    )
    if (onPress) {
      return (
        <TouchableOpacity onPress={() => onPress(agent)}>
          {content}
        </TouchableOpacity>
      )
    }
    return content
  }

  return (
    <CardContainer>
      {/* Agent ID removed from card */}
      {/* Header */}
      <View style={{
        flexDirection: 'row',
        justifyContent: 'flex-start',
        alignItems: 'flex-start',
        marginBottom: (hasRole || hasDescription) ? 8 : 4
      }}>
        <View style={{ flex: 1, marginRight: 12 }}>
          <View style={{ flexDirection: 'row', alignItems: 'center', gap: 8, marginBottom: 2 }}>
            <View style={{ flex: 1, minWidth: 0 }}>
              <View style={{ flexDirection: 'row', alignItems: 'center', gap: 6 }}>
                <Bot size={14} color={theme.color.primary as any} />
                <Text style={{
                  color: theme.color.cardForeground,
                  fontSize: 15,
                  fontWeight: '700',
                  flex: 1
                }} numberOfLines={1}>
                  {capitalizeFirst(agent.name)}
                </Text>
                <View style={{
                  flexDirection: 'row',
                  alignItems: 'center',
                  gap: 6,
                  backgroundColor: theme.dark ? theme.color.secondary : theme.color.card,
                  borderRadius: theme.radius.sm,
                  paddingHorizontal: 8,
                  paddingVertical: 3
                }}>
                  <View style={{
                    width: 6,
                    height: 6,
                    borderRadius: 3,
                    backgroundColor: theme.color.success
                  }} />
                  <Text style={{ color: theme.color.mutedForeground, fontSize: 12, fontWeight: '600' }}>
                    Active
                  </Text>
                </View>
                {onPress ? <ChevronRight size={16} color={theme.color.mutedForeground as any} /> : null}
              </View>
              {hasRole && (
                 <>
                   <Text style={{ color: theme.color.mutedForeground, fontSize: 12, marginTop: 2 }} numberOfLines={1}>
                     {agent.role || (agent.roles || []).slice(0, 2).join(' • ')}
                   </Text>
                   {/* Compact role description below the role name(s) */}
                   <Text style={{ color: theme.color.mutedForeground, fontSize: 13, lineHeight: 20, marginTop: 4 }} numberOfLines={3}>
                     {roleDescriptions[(agent.role || (agent.roles || [])[0] || '')] || 'Configured role.'}
                   </Text>
                 </>
               )}
             </View>
           </View>

        {hasDescription && (
          <Text style={{
            color: theme.color.mutedForeground,
            fontSize: 13,
            lineHeight: 20,
            marginTop: hasRole ? 4 : 2,
            marginBottom: 8
          }}>
            {agent.description}
          </Text>
        )}

        </View>
      </View>

      {/* Removed analytics summary (Chats & CSAT) per request */}

      {/* Divider before usage */}
      <View style={{ height: 1, backgroundColor: theme.color.border, marginTop: 8, marginBottom: 8 }} />

      {/* Usage summary (demo) */}
      <View style={{ flexDirection: 'row', justifyContent: 'space-between', alignItems: 'center', marginBottom: 8 }}>
        <View style={{ alignItems: 'center', flex: 1 }}>
          <Text style={{ color: theme.color.cardForeground, fontSize: 16, fontWeight: '700' }}>
            ${spent.toFixed(2)}
          </Text>
          <Text style={{ color: theme.color.mutedForeground, fontSize: 11 }}>Spent</Text>
        </View>
        <View style={{ alignItems: 'center', flex: 1 }}>
          <Text style={{ color: theme.color.cardForeground, fontSize: 16, fontWeight: '700' }}>
            ${remaining.toFixed(2)}
          </Text>
          <Text style={{ color: theme.color.mutedForeground, fontSize: 11 }}>Remaining</Text>
        </View>
      </View>
      {/* Progress bar removed per request */}

      {/* Actions removed (Deactivate, Edit, Delete) per request */}
    </CardContainer>
  )
}
