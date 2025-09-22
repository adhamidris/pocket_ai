import React, { useState } from 'react'
import { View, Text, SafeAreaView, ScrollView, TouchableOpacity, Alert } from 'react-native'
import { useTranslation } from 'react-i18next'
import { useTheme } from '../../providers/ThemeProvider'
import { Card } from '../../components/ui/Card'
import { Button } from '../../components/ui/Button'
import { Search, Plus, Users, BarChart3, Tag, Filter, Crown, CheckCircle2, DollarSign } from 'lucide-react-native'
import { CustomerCard } from './CustomerCard'
import { CustomerDetail } from './CustomerDetail'

interface Customer {
  id: string
  name: string
  email: string
  phone?: string
  address?: string
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

export const CRMScreen: React.FC = () => {
  const { t } = useTranslation()
  const { theme } = useTheme()
  const [activeTab, setActiveTab] = useState<'customers' | 'segments' | 'insights'>('customers')
  const [selectedCustomer, setSelectedCustomer] = useState<Customer | null>(null)
  const [showCustomerDetail, setShowCustomerDetail] = useState(false)
  
  // Mock customer data
  const [customers] = useState<Customer[]>([
    {
      id: '1',
      name: 'Sarah Johnson',
      email: 'sarah.johnson@company.com',
      phone: '+1 (555) 123-4567',
      address: '123 Business St, New York, NY 10001',
      status: 'vip',
      channel: 'whatsapp',
      lastContact: '2024-01-15T10:30:00Z',
      totalConversations: 24,
      satisfaction: 98,
      tags: ['Enterprise', 'Priority', 'Technical'],
      totalValue: 15750,
      joinedDate: '2023-06-15T00:00:00Z'
    },
    {
      id: '2',
      name: 'Michael Chen',
      email: 'mchen@startup.io',
      phone: '+1 (555) 987-6543',
      status: 'active',
      channel: 'web',
      lastContact: '2024-01-14T14:20:00Z',
      totalConversations: 8,
      satisfaction: 92,
      tags: ['Startup', 'Integration'],
      totalValue: 4200,
      joinedDate: '2023-11-20T00:00:00Z'
    },
    {
      id: '3',
      name: 'Emma Rodriguez',
      email: 'emma.r@smallbiz.com',
      status: 'active',
      channel: 'instagram',
      lastContact: '2024-01-12T09:15:00Z',
      totalConversations: 12,
      satisfaction: 87,
      tags: ['Small Business', 'Billing'],
      totalValue: 2800,
      joinedDate: '2023-09-08T00:00:00Z'
    },
    {
      id: '4',
      name: 'David Park',
      email: 'dpark@tech.co',
      phone: '+1 (555) 456-7890',
      status: 'inactive',
      channel: 'email',
      lastContact: '2023-12-28T16:45:00Z',
      totalConversations: 5,
      satisfaction: 74,
      tags: ['Trial', 'Technical Support'],
      totalValue: 0,
      joinedDate: '2023-12-01T00:00:00Z'
    }
  ])

  const tabs = [
    { key: 'customers' as const, label: t('crm.customers'), icon: Users },
    { key: 'segments' as const, label: t('crm.segments'), icon: Tag },
    { key: 'insights' as const, label: t('crm.insights'), icon: BarChart3 },
  ]

  const handleCustomerPress = (customer: Customer) => {
    setSelectedCustomer(customer)
    setShowCustomerDetail(true)
  }

  const handleCustomerMore = (customer: Customer) => {
    Alert.alert(
      customer.name,
      'Choose an action',
      [
        { text: 'View Details', onPress: () => handleCustomerPress(customer) },
        { text: 'Send Message', onPress: () => {} },
        { text: 'Edit Customer', onPress: () => {} },
        { text: 'Cancel', style: 'cancel' }
      ]
    )
  }

  const getCustomerStats = () => {
    const total = customers.length
    const active = customers.filter(c => c.status === 'active').length
    const vip = customers.filter(c => c.status === 'vip').length
    const totalValue = customers.reduce((sum, c) => sum + c.totalValue, 0)
    
    return { total, active, vip, totalValue }
  }

  const stats = getCustomerStats()

  const getTagColor = (tag: string) => {
    const t = tag.toLowerCase()
    if (t === 'billing') return theme.color.warning
    if (t === 'technical') return theme.color.primary
    if (t === 'integration') return 'hsl(190,90%,45%)' // teal
    if (t === 'enterprise') return 'hsl(262,83%,58%)' // purple
    if (t === 'startup') return 'hsl(200,90%,50%)' // cyan
    return theme.color.mutedForeground
  }

  const getTagStyle = (tag: string) => {
    const color = getTagColor(tag)
    const withAlpha = (c: string, a: number) =>
      c.startsWith('hsl(')
        ? c.replace('hsl(', 'hsla(').replace(')', `,${a})`)
        : c
    const bg = withAlpha(color, theme.dark ? 0.28 : 0.12)
    return { color, bg }
  }

  return (
    <SafeAreaView style={{ flex: 1, backgroundColor: theme.color.background }}>
      <View style={{ flex: 1 }}>
        {/* Header */}
        <View style={{ paddingHorizontal: 24, paddingTop: 12, paddingBottom: 16 }}>
          <View style={{ flexDirection: 'row', justifyContent: 'space-between', alignItems: 'center', marginBottom: 16 }}>
            <Text style={{
              color: theme.color.foreground,
              fontSize: 32,
              fontWeight: '700'
            }}>
              {t('crm.title')}
            </Text>
            <TouchableOpacity style={{
              width: 44,
              height: 44,
              backgroundColor: theme.color.card,
              borderRadius: 22,
              alignItems: 'center',
              justifyContent: 'center'
            }}>
              <Plus color={theme.color.cardForeground as any} size={20} />
            </TouchableOpacity>
          </View>

          {/* Stats Row removed per request */}

          {/* Tabs (match Conversations ordering: toggles above search) */}
          <View style={{ flexDirection: 'row' }}>
            {tabs.map((tab) => (
              <TouchableOpacity
                key={tab.key}
                onPress={() => setActiveTab(tab.key)}
                style={{
                  flex: 1,
                  paddingVertical: 12,
                  paddingHorizontal: 16,
                  borderRadius: theme.radius.md,
                  backgroundColor: activeTab === tab.key 
                    ? (theme.color.primary as any) 
                    : (theme.dark ? theme.color.secondary : theme.color.accent),
                  borderWidth: 0,
                  borderColor: 'transparent',
                  marginRight: tab.key !== 'insights' ? 8 : 0,
                  flexDirection: 'row',
                  alignItems: 'center',
                  justifyContent: 'center',
                  gap: 8
                }}
              >
                <tab.icon
                  color={activeTab === tab.key 
                    ? ('#ffffff' as any) 
                    : (theme.color.mutedForeground as any)}
                  size={18}
                />
                <Text style={{
                  color: activeTab === tab.key 
                    ? ('#ffffff' as any) 
                    : (theme.color.mutedForeground as any),
                  fontWeight: '700',
                  fontSize: 13
                }}>
                  {tab.label}
                </Text>
              </TouchableOpacity>
            ))}
          </View>
        </View>

        {/* Search Bar (moved below header, same spacing as tabs had) */}
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
              {t('crm.searchPlaceholder')}
            </Text>
            <TouchableOpacity style={{ padding: 2 }}>
              <Filter color={theme.color.mutedForeground} size={22} />
            </TouchableOpacity>
          </View>
        </View>

        {/* Content */}
        <ScrollView style={{ flex: 1, paddingHorizontal: 24 }}>
          {activeTab === 'customers' && (
            <>
              {customers.length > 0 ? (
                customers.map((customer) => (
                  <CustomerCard
                    key={customer.id}
                    customer={customer}
                    onPress={handleCustomerPress}
                    onMore={handleCustomerMore}
                  />
                ))
              ) : (
                <Card variant="flat" style={{ backgroundColor: theme.dark ? theme.color.secondary : theme.color.accent }}>
                  <Text style={{
                    color: theme.color.mutedForeground,
                    textAlign: 'center',
                    fontStyle: 'italic'
                  }}>
                    {t('crm.noCustomers')}
                  </Text>
                </Card>
              )}
            </>
          )}

          {activeTab === 'segments' && (
            <Card variant="flat" style={{ backgroundColor: theme.dark ? theme.color.secondary : theme.color.accent }}>
              <Text style={{
                color: theme.color.cardForeground,
                fontSize: 16,
                fontWeight: '600',
                marginBottom: 16
              }}>
                Customer Segments
              </Text>
              <View style={{ gap: 12 }}>
                <View style={{
                  flexDirection: 'row',
                  justifyContent: 'space-between',
                  alignItems: 'center',
                  paddingVertical: 12,
                  borderBottomWidth: 1,
                  borderBottomColor: theme.color.border
                }}>
                  <Text style={{ color: theme.color.cardForeground, fontWeight: '500' }}>
                    VIP Customers
                  </Text>
                  <Text style={{ color: theme.color.warning, fontWeight: '600' }}>
                    {stats.vip}
                  </Text>
                </View>
                <View style={{
                  flexDirection: 'row',
                  justifyContent: 'space-between',
                  alignItems: 'center',
                  paddingVertical: 12,
                  borderBottomWidth: 1,
                  borderBottomColor: theme.color.border
                }}>
                  <Text style={{ color: theme.color.cardForeground, fontWeight: '500' }}>
                    Enterprise
                  </Text>
                  <Text style={{ color: theme.color.primary, fontWeight: '600' }}>
                    1
                  </Text>
                </View>
                <View style={{
                  flexDirection: 'row',
                  justifyContent: 'space-between',
                  alignItems: 'center',
                  paddingVertical: 12
                }}>
                  <Text style={{ color: theme.color.cardForeground, fontWeight: '500' }}>
                    Small Business
                  </Text>
                  <Text style={{ color: theme.color.success, fontWeight: '600' }}>
                    1
                  </Text>
                </View>
              </View>
            </Card>
          )}

          {activeTab === 'insights' && (
            <Card variant="flat" style={{ backgroundColor: theme.dark ? theme.color.secondary : theme.color.accent }}>
              <Text style={{
                color: theme.color.cardForeground,
                fontSize: 16,
                fontWeight: '600',
                marginBottom: 16
              }}>
                Customer Insights
              </Text>
              <View style={{ gap: 16 }}>
                <View>
                  <Text style={{ 
                    color: theme.color.mutedForeground, 
                    fontSize: 14, 
                    marginBottom: 4 
                  }}>
                    Average Satisfaction
                  </Text>
                  <Text style={{ 
                    color: theme.color.cardForeground, 
                    fontSize: 24, 
                    fontWeight: '700' 
                  }}>
                    {Math.round(customers.reduce((sum, c) => sum + c.satisfaction, 0) / customers.length)}%
                  </Text>
                </View>
                
                <View>
                  <Text style={{ 
                    color: theme.color.mutedForeground, 
                    fontSize: 14, 
                    marginBottom: 4 
                  }}>
                    Total Customer Value
                  </Text>
                  <Text style={{ 
                    color: theme.color.success, 
                    fontSize: 24, 
                    fontWeight: '700' 
                  }}>
                    ${stats.totalValue.toLocaleString()}
                  </Text>
                </View>
                
                <View>
                  <Text style={{ 
                    color: theme.color.mutedForeground, 
                    fontSize: 14, 
                    marginBottom: 4 
                  }}>
                    Most Common Tags
                  </Text>
                  <View style={{ flexDirection: 'row', flexWrap: 'wrap', gap: 10, marginTop: 8 }}>
                    {['Technical', 'Enterprise', 'Billing', 'Integration'].map((tag) => {
                      const { color, bg } = getTagStyle(tag)
                      return (
                        <View
                          key={tag}
                          style={{
                            backgroundColor: bg as any,
                            paddingHorizontal: 8,
                            paddingVertical: 3,
                            borderRadius: theme.radius.sm,
                            flexDirection: 'row',
                            alignItems: 'center'
                          }}
                        >
                          <Text style={{ color: (theme.dark ? ('#ffffff' as any) : (color as any)), fontSize: 11, fontWeight: '600' }}>
                            {tag}
                          </Text>
                        </View>
                      )
                    })}
                  </View>
                </View>
              </View>
            </Card>
          )}
        </ScrollView>

        {/* Customer Detail Modal */}
        <CustomerDetail
          visible={showCustomerDetail}
          customer={selectedCustomer}
          onClose={() => {
            setShowCustomerDetail(false)
            setSelectedCustomer(null)
          }}
        />
      </View>
    </SafeAreaView>
  )
}
