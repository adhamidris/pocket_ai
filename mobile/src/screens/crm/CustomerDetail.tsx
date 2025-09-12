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
  Activity
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
  const [activeTab, setActiveTab] = useState<'overview' | 'activity' | 'notes'>('overview')

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
      <ScrollView showsVerticalScrollIndicator={false} contentContainerStyle={{ paddingBottom: 12 }}>
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
        <View style={{ flexDirection: 'row', marginBottom: 12, backgroundColor: theme.color.muted, borderRadius: theme.radius.md, padding: 4 }}>
          {tabs.map((tab) => (
            <TouchableOpacity
              key={tab.key}
              onPress={() => setActiveTab(tab.key)}
              style={{
                flex: 1,
                flexDirection: 'row',
                alignItems: 'center',
                justifyContent: 'center',
                gap: 6,
                paddingVertical: 8,
                paddingHorizontal: 12,
                borderRadius: theme.radius.sm,
                backgroundColor: activeTab === tab.key 
                  ? theme.color.card 
                  : 'transparent'
              }}
            >
              <tab.icon 
                size={16} 
                color={activeTab === tab.key 
                  ? theme.color.primary 
                  : theme.color.mutedForeground
                } 
              />
              <Text style={{
                color: activeTab === tab.key 
                  ? theme.color.primary 
                  : theme.color.mutedForeground,
                fontSize: 14,
                fontWeight: '600'
              }}>
                {tab.label}
              </Text>
            </TouchableOpacity>
          ))}
        </View>

        {/* Overview content */}
        {activeTab === 'overview' && (
        <>
        <Card style={{ marginBottom: 16, padding: 16 }}>
          <View style={{ flexDirection: 'row', alignItems: 'center', gap: 8, marginBottom: 16 }}>
            <Mail size={16} color={theme.color.mutedForeground as any} />
            <Text style={{ color: theme.color.cardForeground, fontSize: 16, fontWeight: '600' }}>
              Contact Information
            </Text>
          </View>
          
          <View style={{ gap: 10 }}>
            {/* Email (full width) */}
            <View style={{ flexDirection: 'row', alignItems: 'center', gap: 12 }}>
              <Mail size={14} color={theme.color.mutedForeground} />
              <Text style={{ color: theme.color.cardForeground, fontSize: 13, flex: 1 }} numberOfLines={1}>
                {customer.email}
              </Text>
            </View>
            {/* Phone (full width) */}
            {customer.phone ? (
              <View style={{ flexDirection: 'row', alignItems: 'center', gap: 12 }}>
                <Phone size={14} color={theme.color.mutedForeground} />
                <Text style={{ color: theme.color.cardForeground, fontSize: 13, flex: 1 }} numberOfLines={1}>
                  {customer.phone}
                </Text>
              </View>
            ) : null}
            {/* Address full width below */}
            {customer.address && (
              <View style={{ flexDirection: 'row', alignItems: 'center', gap: 8 }}>
                <MapPin size={14} color={theme.color.mutedForeground} />
                <Text style={{ color: theme.color.cardForeground, fontSize: 13, flex: 1 }}>
                  {customer.address}
                </Text>
              </View>
            )}
          </View>
        </Card>

        {/* Stats */}
        <Card style={{ marginBottom: 16, padding: 16 }}>
          <View style={{ flexDirection: 'row', alignItems: 'center', gap: 8, marginBottom: 16 }}>
            <Activity size={16} color={theme.color.mutedForeground as any} />
            <Text style={{ color: theme.color.cardForeground, fontSize: 16, fontWeight: '600' }}>
              Customer Stats
            </Text>
          </View>
          
          <View style={{ flexDirection: 'row', justifyContent: 'space-between' }}>
            <View style={{ alignItems: 'center', flex: 1 }}>
              <Text style={{
                color: theme.color.primary,
                fontSize: 20,
                fontWeight: '700',
                marginBottom: 2
              }}>
                {customer.totalConversations}
              </Text>
              <Text style={{
                color: theme.color.mutedForeground,
                fontSize: 12,
                textAlign: 'center'
              }}>
                Conversations
              </Text>
            </View>

            <View style={{ alignItems: 'center', flex: 1 }}>
              <Text style={{
                color: theme.color.warning,
                fontSize: 20,
                fontWeight: '700',
                marginBottom: 2
              }}>
                {customer.satisfaction}%
              </Text>
              <Text style={{
                color: theme.color.mutedForeground,
                fontSize: 12,
                textAlign: 'center'
              }}>
                Satisfaction
              </Text>
            </View>

            <View style={{ alignItems: 'center', flex: 1 }}>
              <Text style={{
                color: theme.color.success,
                fontSize: 20,
                fontWeight: '700',
                marginBottom: 2
              }}>
                {formatValue(customer.totalValue)}
              </Text>
              <Text style={{
                color: theme.color.mutedForeground,
                fontSize: 12,
                textAlign: 'center'
              }}>
                Value
              </Text>
            </View>
          </View>
        </Card>

        {/* Tags */}
        <Card style={{ marginBottom: 16, padding: 16 }}>
          <View style={{ flexDirection: 'row', justifyContent: 'space-between', alignItems: 'center', marginBottom: 12 }}>
            <View style={{ flexDirection: 'row', alignItems: 'center', gap: 8 }}>
              <Tag size={16} color={theme.color.mutedForeground as any} />
              <Text style={{ color: theme.color.cardForeground, fontSize: 16, fontWeight: '600' }}>
                Tags
              </Text>
            </View>
            <TouchableOpacity style={{
              width: 32,
              height: 32,
              backgroundColor: theme.dark ? theme.color.secondary : theme.color.card,
              borderRadius: 16,
              alignItems: 'center',
              justifyContent: 'center'
            }}>
              <Plus size={16} color={theme.color.mutedForeground as any} />
            </TouchableOpacity>
          </View>
          
          <View style={{ flexDirection: 'row', flexWrap: 'wrap', gap: 8 }}>
            {customer.tags.map((tag, index) => {
              const color = getTagColor(tag)
              return (
                <View key={index} style={{
                  backgroundColor: theme.color.card,
                  paddingHorizontal: 8,
                  paddingVertical: 3,
                  borderRadius: theme.radius.sm,
                  flexDirection: 'row',
                  alignItems: 'center',
                  gap: 6,
                  borderWidth: 0,
                  borderColor: 'transparent'
                }}>
                  <View style={{ width: 5, height: 5, borderRadius: 2.5, backgroundColor: color as any }} />
                  <Text style={{ color: theme.color.mutedForeground, fontSize: 11, fontWeight: '500' }}>
                    {tag}
                  </Text>
                </View>
              )
            })}
          </View>
        </Card>
        </>
        )}

        {/* Tabs moved above */}

        {/* Tab Content */}
        {activeTab === 'activity' && (
          <Card>
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
          <Card>
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

        {/* Action Buttons */}
        <View style={{ gap: 12, marginTop: 16, paddingBottom: 8 }}>
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
        </View>
      </ScrollView>
    </Modal>
  )
}
