import React from 'react'
import { View, Text, SafeAreaView, ScrollView, TouchableOpacity } from 'react-native'
import { useTranslation } from 'react-i18next'
import { useNavigation } from '@react-navigation/native'
import { useTheme } from '../../providers/ThemeProvider'
import { Card } from '../../components/ui/Card'
import { AnimatedCard } from '../../components/ui/AnimatedCard'
import { Button } from '../../components/ui/Button'
import { Badge } from '../../components/ui/Badge'
import { PulseAnimation } from '../../components/ui/PulseAnimation'
import { 
  MessageCircle, 
  TrendingUp, 
  Clock, 
  Star,
  Bot,
  Users,
  AlertTriangle,
  ArrowRight,
  ChevronRight
} from 'lucide-react-native'

export const DashboardScreen: React.FC = () => {
  const { t } = useTranslation()
  const { theme } = useTheme()
  const navigation = useNavigation<any>()

  const stats = [
    { 
      label: t('dashboard.stats.activeConversations'), 
      value: '2', 
      icon: MessageCircle, 
      color: theme.color.primary,
      trend: '+5 from yesterday'
    },
    { 
      label: t('dashboard.stats.todayMessages'), 
      value: '43', 
      icon: TrendingUp, 
      color: theme.color.success,
      trend: '+12% vs last week'
    },
    { 
      label: t('dashboard.stats.responseTime'), 
      value: '1.2s', 
      icon: Clock, 
      color: theme.color.warning,
      trend: '45% faster'
    },
    { 
      label: t('dashboard.stats.satisfaction'), 
      value: '92%', 
      icon: Star, 
      color: theme.color.warning,
      trend: '+3% this month'
    },
  ]

  const recentActivity = [
    {
      id: '1',
      type: 'conversation',
      title: 'Sarah Johnson started a conversation',
      description: 'Billing inquiry - Urgent priority',
      timestamp: '5 minutes ago',
      status: 'urgent'
    },
    {
      id: '2',
      type: 'agent',
      title: 'Nancy AI resolved 3 conversations',
      description: 'Average response time: 1.1s',
      timestamp: '1 hour ago',
      status: 'success'
    },
    {
      id: '3',
      type: 'customer',
      title: 'New customer: Michael Chen',
      description: 'Technical integration support',
      timestamp: '2 hours ago',
      status: 'info'
    },
    {
      id: '4',
      type: 'satisfaction',
      title: 'Emma Rodriguez rated 5 stars',
      description: 'Resolution: Dashboard access issue',
      timestamp: '3 hours ago',
      status: 'success'
    }
  ]

  const getActivityIcon = (type: string) => {
    switch (type) {
      case 'conversation': return MessageCircle
      case 'agent': return Bot
      case 'customer': return Users
      case 'satisfaction': return Star
      default: return MessageCircle
    }
  }

  const getActivityColor = (status: string) => {
    switch (status) {
      case 'urgent': return theme.color.error
      case 'success': return theme.color.success
      case 'info': return theme.color.primary
      default: return theme.color.mutedForeground
    }
  }

  const quickActions = [
    {
      title: 'View All Conversations',
      description: '2 active, 1 waiting',
      icon: MessageCircle,
      color: theme.color.primary,
      onPress: () => navigation.navigate('Conversations')
    },
    {
      title: 'Manage Agents',
      description: 'Configure AI assistants',
      icon: Bot,
      color: theme.color.success,
      onPress: () => navigation.navigate('Agents')
    },
    {
      title: 'Customer Profiles',
      description: '4 customers, 1 VIP',
      icon: Users,
      color: theme.color.warning,
      onPress: () => navigation.navigate('CRM')
    }
  ]

  return (
    <SafeAreaView style={{ flex: 1, backgroundColor: theme.color.background }}>
      <ScrollView style={{ flex: 1 }}>
        {/* Header */}
        <View style={{ padding: 24, paddingBottom: 16 }}>
          <Text style={{
            color: theme.color.foreground,
            fontSize: 32,
            fontWeight: '700',
            marginBottom: 8
          }}>
            {t('dashboard.title')}
          </Text>
          <Text style={{
            color: theme.color.mutedForeground,
            fontSize: 16
          }}>
            {t('dashboard.welcome')} 👋
          </Text>
        </View>

        {/* Alert Banner */}
        <View style={{ paddingHorizontal: 24, marginBottom: 24 }}>
          <PulseAnimation duration={2000} minOpacity={0.8} maxOpacity={1}>
            <AnimatedCard 
              animationType="slideUp"
              delay={100}
              style={{ 
                backgroundColor: theme.color.error + '10',
                borderColor: theme.color.error + '30'
              }}
              onPress={() => navigation.navigate('Conversations')}
            >
              <View style={{ flexDirection: 'row', alignItems: 'center', gap: 12 }}>
                <AlertTriangle size={20} color={theme.color.error} />
                <View style={{ flex: 1 }}>
                  <Text style={{
                    color: theme.color.error,
                    fontSize: 14,
                    fontWeight: '600',
                    marginBottom: 2
                  }}>
                    Urgent Conversation
                  </Text>
                  <Text style={{
                    color: theme.color.error,
                    fontSize: 12
                  }}>
                    Sarah Johnson needs immediate billing assistance
                  </Text>
                </View>
                <TouchableOpacity 
                  onPress={() => navigation.navigate('Conversations')}
                  style={{
                    backgroundColor: theme.color.error,
                    paddingHorizontal: 12,
                    paddingVertical: 6,
                    borderRadius: theme.radius.sm
                  }}
                >
                  <Text style={{ color: '#fff', fontSize: 12, fontWeight: '600' }}>
                    View
                  </Text>
                </TouchableOpacity>
              </View>
            </AnimatedCard>
          </PulseAnimation>
        </View>

        {/* Stats Grid */}
        <View style={{
          paddingHorizontal: 24,
          flexDirection: 'row',
          flexWrap: 'wrap',
          gap: 12,
          marginBottom: 32
        }}>
          {stats.map((stat, index) => (
            <AnimatedCard 
              key={index} 
              animationType="fadeIn"
              delay={200 + (index * 100)}
              style={{ 
                flex: 1, 
                minWidth: '47%',
                padding: 16
              }}
            >
              <View style={{ flexDirection: 'row', alignItems: 'flex-start', gap: 12 }}>
                <View style={{
                  width: 40,
                  height: 40,
                  backgroundColor: stat.color + '20',
                  borderRadius: 20,
                  alignItems: 'center',
                  justifyContent: 'center'
                }}>
                  <stat.icon color={stat.color} size={20} />
                </View>
                <View style={{ flex: 1 }}>
                  <Text style={{
                    color: theme.color.cardForeground,
                    fontSize: 24,
                    fontWeight: '700',
                    marginBottom: 2
                  }}>
                    {stat.value}
                  </Text>
                  <Text style={{
                    color: theme.color.mutedForeground,
                    fontSize: 11,
                    marginBottom: 4
                  }}>
                    {stat.label}
                  </Text>
                  <Text style={{
                    color: theme.color.success,
                    fontSize: 10,
                    fontWeight: '500'
                  }}>
                    {stat.trend}
                  </Text>
                </View>
              </View>
            </AnimatedCard>
          ))}
        </View>

        {/* Quick Actions */}
        <View style={{ paddingHorizontal: 24, marginBottom: 32 }}>
          <Text style={{
            color: theme.color.foreground,
            fontSize: 20,
            fontWeight: '600',
            marginBottom: 16
          }}>
            {t('dashboard.quickActions')}
          </Text>
          
          <View style={{ gap: 12 }}>
            {quickActions.map((action, index) => (
              <AnimatedCard 
                key={index} 
                animationType="slideUp"
                delay={600 + (index * 100)}
                onPress={action.onPress}
                style={{ padding: 16 }}
              >
                <View style={{ flexDirection: 'row', alignItems: 'center', gap: 16 }}>
                  <View style={{
                    width: 44,
                    height: 44,
                    backgroundColor: action.color + '20',
                    borderRadius: 22,
                    alignItems: 'center',
                    justifyContent: 'center'
                  }}>
                    <action.icon color={action.color} size={22} />
                  </View>
                  
                  <View style={{ flex: 1 }}>
                    <Text style={{
                      color: theme.color.cardForeground,
                      fontSize: 16,
                      fontWeight: '600',
                      marginBottom: 2
                    }}>
                      {action.title}
                    </Text>
                    <Text style={{
                      color: theme.color.mutedForeground,
                      fontSize: 14
                    }}>
                      {action.description}
                    </Text>
                  </View>
                  
                  <ChevronRight size={20} color={theme.color.mutedForeground} />
                </View>
              </AnimatedCard>
            ))}
          </View>
        </View>

        {/* Recent Activity */}
        <View style={{ paddingHorizontal: 24, marginBottom: 32 }}>
          <View style={{
            flexDirection: 'row',
            justifyContent: 'space-between',
            alignItems: 'center',
            marginBottom: 16
          }}>
            <Text style={{
              color: theme.color.foreground,
              fontSize: 20,
              fontWeight: '600'
            }}>
              {t('dashboard.recentActivity')}
            </Text>
            <TouchableOpacity>
              <Text style={{
                color: theme.color.primary,
                fontSize: 14,
                fontWeight: '500'
              }}>
                View All
              </Text>
            </TouchableOpacity>
          </View>
          
          <Card>
            <View style={{ gap: 16 }}>
              {recentActivity.map((activity, index) => {
                const ActivityIcon = getActivityIcon(activity.type)
                const color = getActivityColor(activity.status)
                
                return (
                  <View key={activity.id} style={{
                    flexDirection: 'row',
                    alignItems: 'flex-start',
                    gap: 12,
                    paddingBottom: index < recentActivity.length - 1 ? 16 : 0,
                    borderBottomWidth: index < recentActivity.length - 1 ? 1 : 0,
                    borderBottomColor: theme.color.border
                  }}>
                    <View style={{
                      width: 32,
                      height: 32,
                      backgroundColor: color + '20',
                      borderRadius: 16,
                      alignItems: 'center',
                      justifyContent: 'center'
                    }}>
                      <ActivityIcon size={16} color={color} />
                    </View>
                    
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
                        fontSize: 11
                      }}>
                        {activity.timestamp}
                      </Text>
                    </View>
                  </View>
                )
              })}
            </View>
          </Card>
        </View>
      </ScrollView>
    </SafeAreaView>
  )
}
