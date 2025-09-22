import React from 'react'
import { View, Text, TouchableOpacity } from 'react-native'
import { useTheme } from '../../providers/ThemeProvider'
import { Card } from '../../components/ui/Card'
import { 
  Mail, 
  Phone, 
  MessageCircle, 
  Calendar,
  Star,
  Crown
} from 'lucide-react-native'
import { ChevronRight } from 'lucide-react-native'

interface Customer {
  id: string
  name: string
  email: string
  phone?: string
  avatar?: string
  status: 'active' | 'inactive' | 'vip'
  channel: 'email' | 'web' | 'whatsapp' | 'instagram' | 'twitter' | 'messenger'
  lastContact: string
  totalConversations: number
  satisfaction: number
  tags: string[]
  totalValue: number
  joinedDate: string
}

interface CustomerCardProps {
  customer: Customer
  onPress: (customer: Customer) => void
  onMore: (customer: Customer) => void
}

export const CustomerCard: React.FC<CustomerCardProps> = ({ 
  customer, 
  onPress,
  onMore 
}) => {
  const { theme } = useTheme()

  const getStatusVariant = (status: string) => {
    switch (status) {
      case 'vip': return 'warning'
      case 'active': return 'success'
      default: return 'secondary'
    }
  }

  // Removed avatar initials

  const formatValue = (value: number) => {
    if (value >= 1000) {
      return `${(value / 1000).toFixed(1)}k`
    }
    return `${value}`
  }

  const formatDate = (dateString: string) => {
    const date = new Date(dateString)
    const now = new Date()
    const diffTime = Math.abs(now.getTime() - date.getTime())
    const diffDays = Math.ceil(diffTime / (1000 * 60 * 60 * 24))
    
    if (diffDays < 1) return 'today'
    if (diffDays === 1) return '1d'
    if (diffDays < 7) return `${diffDays}d`
    const weeks = Math.ceil(diffDays / 7)
    if (diffDays < 30) return `${weeks}w`
    const months = Math.ceil(diffDays / 30)
    return `${months}mo`
  }

  const getDisplayName = (name: string) => {
    const parts = name.trim().split(/\s+/)
    if (parts.length === 0) return name
    const first = parts[0]
    const second = parts.length > 1 ? parts[1][0].toUpperCase() + '.' : ''
    return second ? `${first} ${second}` : first
  }

  // Removed tags helpers

  const getChannelChip = (channel: Customer['channel']) => {
    const labelMap: Record<Customer['channel'], string> = {
      email: 'EM', web: 'WEB', whatsapp: 'WA', instagram: 'IG', twitter: 'TW', messenger: 'MS'
    }
    const colorMap: Record<Customer['channel'], string> = {
      email: theme.color.primary,
      web: 'hsl(210,10%,60%)',
      whatsapp: 'hsl(142,71%,45%)',
      instagram: 'hsl(291,70%,55%)',
      twitter: 'hsl(200,90%,50%)',
      messenger: 'hsl(240,75%,48%)'
    }
    const bg = theme.dark ? `${colorMap[channel].replace('hsl(', 'hsla(').replace(')', ',0.20)')}` : `${colorMap[channel].replace('hsl(', 'hsla(').replace(')', ',0.12)')}`
    const fg = colorMap[channel]
    return (
      <View style={{ flexDirection: 'row', alignItems: 'center', backgroundColor: bg as any, borderRadius: 12, paddingHorizontal: 8, paddingVertical: 3 }}>
        <Text style={{ color: fg as any, fontSize: 11, fontWeight: '800' }}>{labelMap[channel]}</Text>
      </View>
    )
  }

  return (
    <TouchableOpacity onPress={() => onPress(customer)}>
      <Card variant="flat" style={{ marginBottom: 12, backgroundColor: theme.dark ? theme.color.secondary : theme.color.accent, paddingHorizontal: 16, paddingVertical: 14 }}>
        {/* Header */}
        <View style={{
          flexDirection: 'row',
          justifyContent: 'space-between',
          alignItems: 'flex-start',
          marginBottom: 12
        }}>
          <View style={{ flexDirection: 'row', alignItems: 'center', flex: 1, marginRight: 12 }}>
            {/* Info */}
            <View style={{ flex: 1 }}>
              <View style={{ flexDirection: 'row', alignItems: 'center', gap: 8, marginBottom: 4 }}>
                <View style={{ flex: 1, flexDirection: 'row', alignItems: 'center', gap: 6, minWidth: 0 }}>
                  <Text style={{
                    color: theme.color.cardForeground,
                    fontSize: 17,
                    fontWeight: '700',
                    flexShrink: 1
                  }} numberOfLines={1}>
                    {getDisplayName(customer.name)}
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
                  {/* Channel chip next to name/VIP */}
                  {getChannelChip(customer.channel)}
                </View>
              </View>
              
              <View style={{ flexDirection: 'row', alignItems: 'center', gap: 10, marginBottom: 6 }}>
                <Mail size={14} color={theme.color.mutedForeground} />
                <Text style={{
                  color: theme.color.mutedForeground,
                  fontSize: 13,
                  flex: 1
                }} numberOfLines={1}>
                  {customer.email}
                </Text>
              </View>

              {customer.phone && (
                <View style={{ flexDirection: 'row', alignItems: 'center', gap: 10 }}>
                  <Phone size={14} color={theme.color.mutedForeground} />
                  <Text style={{
                    color: theme.color.mutedForeground,
                    fontSize: 13
                  }}>
                    {customer.phone}
                  </Text>
                </View>
              )}
            </View>
          </View>

          {/* Right anchor chevron (matches other cards) */}
          <View style={{ alignItems: 'center', justifyContent: 'center' }}>
            <ChevronRight size={16} color={theme.color.mutedForeground as any} />
          </View>
        </View>

        {/* Tags removed per request */}

        {/* Stats */}
        <View style={{
          flexDirection: 'row',
          justifyContent: 'space-between',
          paddingTop: 12,
          borderTopWidth: 1,
          borderTopColor: theme.color.border
        }}>
          <View style={{ alignItems: 'center', flex: 1 }}>
            <View style={{ flexDirection: 'row', alignItems: 'center', gap: 4, marginBottom: 4 }}>
              <MessageCircle size={14} color={theme.color.primary} />
              <Text style={{
                color: theme.color.cardForeground,
                fontSize: 14,
                fontWeight: '700'
              }}>
                {customer.totalConversations}
              </Text>
            </View>
            <Text style={{
              color: theme.color.mutedForeground,
              fontSize: 11
            }}>
              Conversations
            </Text>
          </View>

          <View style={{ alignItems: 'center', flex: 1 }}>
            <View style={{ flexDirection: 'row', alignItems: 'center', gap: 4, marginBottom: 4 }}>
              <Star size={14} color={theme.color.warning} />
              <Text style={{
                color: theme.color.cardForeground,
                fontSize: 14,
                fontWeight: '700'
              }}>
                {customer.satisfaction}%
              </Text>
            </View>
            <Text style={{
              color: theme.color.mutedForeground,
              fontSize: 11
            }}>
              Satisfaction
            </Text>
          </View>

          {/* Removed Value analytical indicator per request */}

          <View style={{ alignItems: 'center', flex: 1 }}>
            <View style={{ flexDirection: 'row', alignItems: 'center', gap: 4, marginBottom: 4 }}>
              <Calendar size={14} color={theme.color.mutedForeground} />
              <Text style={{
                color: theme.color.cardForeground,
                fontSize: 14,
                fontWeight: '700'
              }}>
                {formatDate(customer.lastContact)}
              </Text>
            </View>
            <Text style={{
              color: theme.color.mutedForeground,
              fontSize: 11
            }}>
              Last Contact
            </Text>
          </View>
        </View>
      </Card>
    </TouchableOpacity>
  )
}
