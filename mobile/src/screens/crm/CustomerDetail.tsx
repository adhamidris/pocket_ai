import React, { useState } from 'react'
import { View, Text, ScrollView, TouchableOpacity } from 'react-native'
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
  Star,
  Edit,
  Plus,
  Tag,
  Activity
} from 'lucide-react-native'

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

  return (
    <Modal visible={visible} onClose={onClose} size="lg">
      <ScrollView showsVerticalScrollIndicator={false}>
        {/* Customer Header */}
        <View style={{
          flexDirection: 'row',
          alignItems: 'center',
          marginBottom: 20,
          paddingBottom: 20,
          borderBottomWidth: 1,
          borderBottomColor: theme.color.border
        }}>
          {/* Avatar */}
          <View style={{
            width: 64,
            height: 64,
            backgroundColor: customer.status === 'vip' 
              ? theme.color.warning + '20'
              : theme.color.primary + '20',
            borderRadius: 32,
            alignItems: 'center',
            justifyContent: 'center',
            marginRight: 16
          }}>
            <Text style={{
              color: customer.status === 'vip' ? theme.color.warning : theme.color.primary,
              fontSize: 24,
              fontWeight: '700'
            }}>
              {getInitials(customer.name)}
            </Text>
          </View>

          {/* Info */}
          <View style={{ flex: 1 }}>
            <View style={{ flexDirection: 'row', alignItems: 'center', gap: 8, marginBottom: 4 }}>
              <Text style={{
                color: theme.color.cardForeground,
                fontSize: 20,
                fontWeight: '700',
                flex: 1
              }}>
                {customer.name}
              </Text>
              {customer.status === 'vip' && (
                <Star size={16} color={theme.color.warning} fill={theme.color.warning} />
              )}
            </View>
            
            <Badge variant={getStatusVariant(customer.status)} size="sm" style={{ marginBottom: 8 }}>
              {customer.status.toUpperCase()}
            </Badge>
            
            <Text style={{
              color: theme.color.mutedForeground,
              fontSize: 14
            }}>
              Customer since {formatDate(customer.joinedDate)}
            </Text>
          </View>

          {/* Actions */}
          <TouchableOpacity style={{
            width: 40,
            height: 40,
            backgroundColor: theme.color.primary,
            borderRadius: 20,
            alignItems: 'center',
            justifyContent: 'center'
          }}>
            <Edit size={18} color="#fff" />
          </TouchableOpacity>
        </View>

        {/* Contact Info */}
        <Card style={{ marginBottom: 20 }}>
          <Text style={{
            color: theme.color.cardForeground,
            fontSize: 16,
            fontWeight: '600',
            marginBottom: 16
          }}>
            Contact Information
          </Text>
          
          <View style={{ gap: 12 }}>
            <View style={{ flexDirection: 'row', alignItems: 'center', gap: 12 }}>
              <Mail size={16} color={theme.color.mutedForeground} />
              <Text style={{
                color: theme.color.cardForeground,
                fontSize: 14,
                flex: 1
              }}>
                {customer.email}
              </Text>
            </View>
            
            {customer.phone && (
              <View style={{ flexDirection: 'row', alignItems: 'center', gap: 12 }}>
                <Phone size={16} color={theme.color.mutedForeground} />
                <Text style={{
                  color: theme.color.cardForeground,
                  fontSize: 14,
                  flex: 1
                }}>
                  {customer.phone}
                </Text>
              </View>
            )}
            
            {customer.address && (
              <View style={{ flexDirection: 'row', alignItems: 'center', gap: 12 }}>
                <MapPin size={16} color={theme.color.mutedForeground} />
                <Text style={{
                  color: theme.color.cardForeground,
                  fontSize: 14,
                  flex: 1
                }}>
                  {customer.address}
                </Text>
              </View>
            )}
          </View>
        </Card>

        {/* Stats */}
        <Card style={{ marginBottom: 20 }}>
          <Text style={{
            color: theme.color.cardForeground,
            fontSize: 16,
            fontWeight: '600',
            marginBottom: 16
          }}>
            Customer Stats
          </Text>
          
          <View style={{
            flexDirection: 'row',
            justifyContent: 'space-between'
          }}>
            <View style={{ alignItems: 'center', flex: 1 }}>
              <Text style={{
                color: theme.color.primary,
                fontSize: 20,
                fontWeight: '700',
                marginBottom: 4
              }}>
                {customer.totalConversations}
              </Text>
              <Text style={{
                color: theme.color.mutedForeground,
                fontSize: 12,
                textAlign: 'center'
              }}>
                Total{'\n'}Conversations
              </Text>
            </View>

            <View style={{ alignItems: 'center', flex: 1 }}>
              <Text style={{
                color: theme.color.warning,
                fontSize: 20,
                fontWeight: '700',
                marginBottom: 4
              }}>
                {customer.satisfaction}%
              </Text>
              <Text style={{
                color: theme.color.mutedForeground,
                fontSize: 12,
                textAlign: 'center'
              }}>
                Satisfaction{'\n'}Rating
              </Text>
            </View>

            <View style={{ alignItems: 'center', flex: 1 }}>
              <Text style={{
                color: theme.color.success,
                fontSize: 20,
                fontWeight: '700',
                marginBottom: 4
              }}>
                {formatValue(customer.totalValue)}
              </Text>
              <Text style={{
                color: theme.color.mutedForeground,
                fontSize: 12,
                textAlign: 'center'
              }}>
                Total{'\n'}Value
              </Text>
            </View>
          </View>
        </Card>

        {/* Tags */}
        <Card style={{ marginBottom: 20 }}>
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
              Tags
            </Text>
            <TouchableOpacity style={{
              width: 32,
              height: 32,
              backgroundColor: theme.color.primary + '20',
              borderRadius: 16,
              alignItems: 'center',
              justifyContent: 'center'
            }}>
              <Plus size={16} color={theme.color.primary} />
            </TouchableOpacity>
          </View>
          
          <View style={{ flexDirection: 'row', flexWrap: 'wrap', gap: 8 }}>
            {customer.tags.map((tag, index) => (
              <View key={index} style={{
                backgroundColor: theme.color.muted,
                paddingHorizontal: 12,
                paddingVertical: 6,
                borderRadius: theme.radius.md,
                flexDirection: 'row',
                alignItems: 'center',
                gap: 6
              }}>
                <Tag size={12} color={theme.color.mutedForeground} />
                <Text style={{
                  color: theme.color.mutedForeground,
                  fontSize: 12,
                  fontWeight: '500'
                }}>
                  {tag}
                </Text>
              </View>
            ))}
          </View>
        </Card>

        {/* Tabs */}
        <View style={{
          flexDirection: 'row',
          marginBottom: 16,
          backgroundColor: theme.color.muted,
          borderRadius: theme.radius.md,
          padding: 4
        }}>
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
                    backgroundColor: theme.color.primary,
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
        <View style={{ gap: 12, marginTop: 20 }}>
          <Button
            title="Start Conversation"
            variant="premium"
            size="lg"
            onPress={() => {}}
          />
          <View style={{ flexDirection: 'row', gap: 12 }}>
            <Button
              title="Send Email"
              variant="outline"
              size="lg"
              onPress={() => {}}
            />
            <Button
              title="Call Customer"
              variant="outline"
              size="lg"
              onPress={() => {}}
            />
          </View>
        </View>
      </ScrollView>
    </Modal>
  )
}
