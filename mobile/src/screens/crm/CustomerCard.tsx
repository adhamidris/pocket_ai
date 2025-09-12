import React from 'react'
import { View, Text, TouchableOpacity } from 'react-native'
import { useTheme } from '../../providers/ThemeProvider'
import { Card } from '../../components/ui/Card'
import { Badge } from '../../components/ui/Badge'
import { 
  User, 
  Mail, 
  Phone, 
  MessageCircle, 
  Calendar,
  Star,
  MoreHorizontal
} from 'lucide-react-native'

interface Customer {
  id: string
  name: string
  email: string
  phone?: string
  avatar?: string
  status: 'active' | 'inactive' | 'vip'
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

  const getInitials = (name: string) => {
    return name.split(' ').map(n => n[0]).join('').toUpperCase().slice(0, 2)
  }

  const formatValue = (value: number) => {
    if (value >= 1000) {
      return `$${(value / 1000).toFixed(1)}k`
    }
    return `$${value}`
  }

  const formatDate = (dateString: string) => {
    const date = new Date(dateString)
    const now = new Date()
    const diffTime = Math.abs(now.getTime() - date.getTime())
    const diffDays = Math.ceil(diffTime / (1000 * 60 * 60 * 24))
    
    if (diffDays === 1) return '1 day ago'
    if (diffDays < 7) return `${diffDays} days ago`
    if (diffDays < 30) return `${Math.ceil(diffDays / 7)} weeks ago`
    return `${Math.ceil(diffDays / 30)} months ago`
  }

  return (
    <TouchableOpacity onPress={() => onPress(customer)}>
      <Card style={{ marginBottom: 12 }}>
        {/* Header */}
        <View style={{
          flexDirection: 'row',
          justifyContent: 'space-between',
          alignItems: 'flex-start',
          marginBottom: 12
        }}>
          <View style={{ flexDirection: 'row', alignItems: 'center', flex: 1, marginRight: 12 }}>
            {/* Avatar */}
            <View style={{
              width: 48,
              height: 48,
              backgroundColor: theme.dark ? theme.color.secondary : theme.color.card,
              borderRadius: 24,
              alignItems: 'center',
              justifyContent: 'center',
              marginRight: 12
            }}>
              <Text style={{
                color: theme.color.cardForeground,
                fontSize: 16,
                fontWeight: '600'
              }}>
                {getInitials(customer.name)}
              </Text>
            </View>

            {/* Info */}
            <View style={{ flex: 1 }}>
              <View style={{ flexDirection: 'row', alignItems: 'center', gap: 8, marginBottom: 4 }}>
                <Text style={{
                  color: theme.color.cardForeground,
                  fontSize: 16,
                  fontWeight: '600',
                  flex: 1
                }} numberOfLines={1}>
                  {customer.name}
                </Text>
                {customer.status === 'vip' && (
                  <Star size={14} color={theme.color.warning} fill={theme.color.warning} />
                )}
              </View>
              
              <View style={{ flexDirection: 'row', alignItems: 'center', gap: 8, marginBottom: 6 }}>
                <Mail size={12} color={theme.color.mutedForeground} />
                <Text style={{
                  color: theme.color.mutedForeground,
                  fontSize: 14,
                  flex: 1
                }} numberOfLines={1}>
                  {customer.email}
                </Text>
              </View>

              {customer.phone && (
                <View style={{ flexDirection: 'row', alignItems: 'center', gap: 8 }}>
                  <Phone size={12} color={theme.color.mutedForeground} />
                  <Text style={{
                    color: theme.color.mutedForeground,
                    fontSize: 14
                  }}>
                    {customer.phone}
                  </Text>
                </View>
              )}
            </View>
          </View>

          {/* Status & Actions */}
          <View style={{ alignItems: 'flex-end', gap: 8 }}>
            <View style={{
              flexDirection: 'row',
              alignItems: 'center',
              gap: 6,
              backgroundColor: theme.dark ? theme.color.secondary : theme.color.card,
              borderRadius: theme.radius.sm,
              paddingHorizontal: 8,
              paddingVertical: 4
            }}>
              <View style={{
                width: 6,
                height: 6,
                borderRadius: 3,
                backgroundColor: (
                  customer.status === 'vip' ? theme.color.warning :
                  customer.status === 'active' ? theme.color.success :
                  theme.color.mutedForeground
                )
              }} />
              <Text style={{ color: theme.color.mutedForeground, fontSize: 12, fontWeight: '600' }}>
                {customer.status.toUpperCase()}
              </Text>
            </View>
            <TouchableOpacity
              onPress={() => onMore(customer)}
              style={{
                width: 28,
                height: 28,
                borderRadius: 14,
                backgroundColor: theme.dark ? theme.color.secondary : theme.color.card,
                alignItems: 'center',
                justifyContent: 'center'
              }}
            >
              <MoreHorizontal size={14} color={theme.color.mutedForeground} />
            </TouchableOpacity>
          </View>
        </View>

        {/* Tags */}
        {customer.tags.length > 0 && (
          <View style={{ flexDirection: 'row', flexWrap: 'wrap', gap: 6, marginBottom: 12 }}>
            {customer.tags.slice(0, 3).map((tag, index) => (
              <View key={index} style={{
                backgroundColor: theme.color.muted,
                paddingHorizontal: 8,
                paddingVertical: 4,
                borderRadius: theme.radius.sm
              }}>
                <Text style={{
                  color: theme.color.mutedForeground,
                  fontSize: 11,
                  fontWeight: '500'
                }}>
                  {tag}
                </Text>
              </View>
            ))}
            {customer.tags.length > 3 && (
              <View style={{
                backgroundColor: theme.color.muted,
                paddingHorizontal: 8,
                paddingVertical: 4,
                borderRadius: theme.radius.sm
              }}>
                <Text style={{
                  color: theme.color.mutedForeground,
                  fontSize: 11,
                  fontWeight: '500'
                }}>
                  +{customer.tags.length - 3}
                </Text>
              </View>
            )}
          </View>
        )}

        {/* Stats */}
        <View style={{
          flexDirection: 'row',
          justifyContent: 'space-between',
          paddingTop: 12,
          borderTopWidth: 1,
          borderTopColor: theme.color.border
        }}>
          <View style={{ alignItems: 'center', flex: 1 }}>
            <View style={{ flexDirection: 'row', alignItems: 'center', gap: 4, marginBottom: 2 }}>
              <MessageCircle size={12} color={theme.color.primary} />
              <Text style={{
                color: theme.color.cardForeground,
                fontSize: 14,
                fontWeight: '600'
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
            <View style={{ flexDirection: 'row', alignItems: 'center', gap: 4, marginBottom: 2 }}>
              <Star size={12} color={theme.color.warning} />
              <Text style={{
                color: theme.color.cardForeground,
                fontSize: 14,
                fontWeight: '600'
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

          <View style={{ alignItems: 'center', flex: 1 }}>
            <Text style={{
              color: theme.color.success,
              fontSize: 14,
              fontWeight: '600',
              marginBottom: 2
            }}>
              {formatValue(customer.totalValue)}
            </Text>
            <Text style={{
              color: theme.color.mutedForeground,
              fontSize: 11
            }}>
              Total Value
            </Text>
          </View>

          <View style={{ alignItems: 'center', flex: 1 }}>
            <View style={{ flexDirection: 'row', alignItems: 'center', gap: 4, marginBottom: 2 }}>
              <Calendar size={12} color={theme.color.mutedForeground} />
              <Text style={{
                color: theme.color.cardForeground,
                fontSize: 12,
                fontWeight: '500'
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
