import React from 'react'
import { View, Text, SafeAreaView, ScrollView, TouchableOpacity, Animated, Platform } from 'react-native'
import { useTranslation } from 'react-i18next'
import { useNavigation } from '@react-navigation/native'
import { useTheme } from '../../providers/ThemeProvider'
import { Card } from '../../components/ui/Card'
import { AnimatedCard } from '../../components/ui/AnimatedCard'
import { Button } from '../../components/ui/Button'
import { Badge } from '../../components/ui/Badge'
// Removed useSafeAreaInsets to avoid double top padding
import { 
  MessageCircle, 
  TrendingUp, 
  Clock, 
  Star,
  Bot,
  Users,
  AlertTriangle,
  ArrowRight,
  ChevronRight,
  Sun,
  Moon
} from 'lucide-react-native'

export const DashboardScreen: React.FC = () => {
  const { t } = useTranslation()
  const { theme, toggle } = useTheme()
  const navigation = useNavigation<any>()
  const pulse = React.useRef(new Animated.Value(0)).current
  const demoCompany = 'Pocket AI'

  React.useEffect(() => {
    const loop = Animated.loop(
      Animated.sequence([
        Animated.timing(pulse, { toValue: 1, duration: 800, useNativeDriver: true }),
        Animated.timing(pulse, { toValue: 0, duration: 800, useNativeDriver: true })
      ])
    )
    loop.start()
    return () => loop.stop()
  }, [pulse])

  const stats = [
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

  const urgentCount = recentActivity.filter(a => a.status === 'urgent').length

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
        <View style={{ paddingHorizontal: 24, paddingTop: 12, paddingBottom: 16 }}>
          <View style={{ flexDirection: 'row', alignItems: 'center', justifyContent: 'space-between' }}>
            <Text style={{
              color: theme.color.foreground,
              fontSize: 32,
              fontWeight: '700'
            }}>
              {t('dashboard.title')}
            </Text>
            <TouchableOpacity
              onPress={toggle}
              activeOpacity={0.85}
              style={{ padding: 8, borderRadius: 16 }}
              hitSlop={{ top: 8, bottom: 8, left: 8, right: 8 }}
            >
              {theme.dark ? <Sun size={18} color={theme.color.cardForeground as any} /> : <Moon size={18} color={theme.color.cardForeground as any} />}
            </TouchableOpacity>
          </View>
          <Text style={{
            color: theme.color.foreground,
            fontSize: 16,
            fontWeight: '600'
          }}>
            Welcome, Adham.
          </Text>
          <Text style={{
            color: theme.color.mutedForeground,
            fontSize: 12,
            marginTop: 2
          }}>
            {demoCompany}
          </Text>
        </View>

        {/* Alert Banner */}
        <View style={{ paddingHorizontal: 24, marginBottom: 12 }}>
          <AnimatedCard 
              animationType="slideUp"
              delay={100}
              style={{ 
                // Neutral background with left accent for urgency
                backgroundColor: theme.dark ? theme.color.secondary : theme.color.accent,
                borderColor: 'transparent',
                borderWidth: 0,
                padding: 12,
                borderRadius: 0,
                position: 'relative',
                ...(Platform.select({
                  ios: {
                    shadowColor: theme.shadow.premium.ios.color,
                    shadowOpacity: theme.shadow.premium.ios.opacity,
                    shadowRadius: theme.shadow.premium.ios.radius,
                    shadowOffset: { width: 0, height: theme.shadow.premium.ios.offsetY },
                  },
                  android: {
                    elevation: theme.shadow.premium.androidElevation,
                  },
                  default: {}
                }) as any)
              }}
              onPress={() => navigation.navigate('Conversations')}
            >
              {/* Left urgency accent */}
              <View style={{ position: 'absolute', left: 0, top: 0, bottom: 0, width: 4, backgroundColor: theme.color.warning }} />
              <View style={{ flexDirection: 'row', alignItems: 'center', gap: 10 }}>
                <AlertTriangle size={24} color={theme.color.warning} />
                <View style={{ flex: 1 }}>
                  <View style={{ flexDirection: 'row', alignItems: 'center', gap: 8, marginTop: 0, marginBottom: 6 }}>
                    <Animated.View
                      style={{
                        width: 8,
                        height: 8,
                        borderRadius: 4,
                        backgroundColor: theme.color.warning,
                        transform: [
                          {
                            scale: pulse.interpolate({ inputRange: [0, 1], outputRange: [1, 1.35] }) as any
                          }
                        ],
                        opacity: pulse.interpolate({ inputRange: [0, 1], outputRange: [0.6, 1] }) as any
                      }}
                    />
                    <Text style={{ color: theme.color.warning, fontSize: 12, fontWeight: '700' }}>Urgent</Text>
                  </View>
                  <Text style={{
                    color: theme.dark ? (theme.color.cardForeground as any) : (theme.color.mutedForeground as any),
                    opacity: theme.dark ? 0.85 : 1,
                    fontSize: 12,
                    lineHeight: 16,
                    marginTop: 0
                  }}>
                    {urgentCount > 0 ? `${urgentCount} unresolved conversations` : 'No urgent conversations right now'}
                  </Text>
                </View>
                <TouchableOpacity onPress={() => navigation.navigate('Conversations')} style={{ paddingHorizontal: 6, paddingVertical: 4 }}>
                  <View style={{ flexDirection: 'row', alignItems: 'center', gap: 4 }}>
                    <Text style={{ color: theme.color.warning, fontSize: 12, fontWeight: '700' }}>Review</Text>
                    <ChevronRight size={12} color={theme.color.warning} />
                  </View>
                </TouchableOpacity>
              </View>
          </AnimatedCard>
        </View>

        {/* Stats Grid */}
        <View style={{
          paddingHorizontal: 24,
          flexDirection: 'row',
          flexWrap: 'wrap',
          gap: 10,
          marginBottom: 28
        }}>
          {stats.map((stat, index) => (
            <AnimatedCard 
              key={index} 
              animationType="fadeIn"
              delay={200 + (index * 100)}
              style={{ 
                flex: 1, 
                minWidth: '47%',
                padding: 12,
                backgroundColor: theme.dark ? theme.color.secondary : theme.color.accent,
                borderColor: 'transparent',
                borderWidth: 0,
                ...(Platform.select({
                  ios: {
                    shadowColor: theme.shadow.md.ios.color,
                    shadowOpacity: theme.shadow.md.ios.opacity,
                    shadowRadius: theme.shadow.md.ios.radius,
                    shadowOffset: { width: 0, height: theme.shadow.md.ios.offsetY },
                  },
                  android: {
                    elevation: theme.shadow.md.androidElevation,
                  },
                  default: {}
                }) as any)
              }}
            >
              <View style={{ flexDirection: 'row', alignItems: 'flex-start', gap: 12 }}>
                <View style={{
                  width: 36,
                  height: 36,
                  backgroundColor: theme.color.primary,
                  borderRadius: 18,
                  alignItems: 'center',
                  justifyContent: 'center',
                  borderWidth: 0,
                  borderColor: 'transparent'
                }}>
                  <stat.icon color={'#ffffff' as any} size={18} />
                </View>
                <View style={{ flex: 1 }}>
                  <View style={{ flexDirection: 'row', alignItems: 'baseline', gap: 6 }}>
                    <Text style={{
                      color: theme.color.cardForeground,
                      fontSize: 20,
                      fontWeight: '700'
                    }}>
                      {stat.value}
                    </Text>
                    <Text style={{
                      color: theme.dark ? (theme.color.cardForeground as any) : (theme.color.mutedForeground as any),
                      opacity: theme.dark ? 0.85 : 1,
                      fontSize: 13
                    }}>
                      {stat.label}
                    </Text>
                  </View>
                  <Text style={{
                    color: theme.color.success,
                    fontSize: 10,
                    fontWeight: '500',
                    marginTop: 4
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
                style={{
                  padding: 16,
                  backgroundColor: theme.dark ? theme.color.secondary : theme.color.accent,
                  borderWidth: 0,
                  borderColor: 'transparent',
                  ...(Platform.select({
                    ios: {
                      shadowColor: theme.shadow.premium.ios.color,
                      shadowOpacity: theme.shadow.premium.ios.opacity,
                      shadowRadius: theme.shadow.premium.ios.radius,
                      shadowOffset: { width: 0, height: theme.shadow.premium.ios.offsetY },
                    },
                    android: {
                      elevation: theme.shadow.premium.androidElevation,
                    },
                    default: {}
                  }) as any)
                }}
              >
                <View style={{ flexDirection: 'row', alignItems: 'center', gap: 16 }}>
                  <View style={{
                    width: 44,
                    height: 44,
                    backgroundColor: theme.dark ? theme.color.secondary : theme.color.card,
                    borderRadius: 22,
                    alignItems: 'center',
                    justifyContent: 'center',
                    borderWidth: 0,
                    borderColor: 'transparent'
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
                      color: theme.dark ? (theme.color.cardForeground as any) : (theme.color.mutedForeground as any),
                      opacity: theme.dark ? 0.85 : 1,
                      fontSize: 13
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
          
          <Card style={{ backgroundColor: theme.dark ? theme.color.secondary : theme.color.accent }}>
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
                      backgroundColor: theme.dark ? theme.color.secondary : theme.color.card,
                      borderRadius: 16,
                      alignItems: 'center',
                      justifyContent: 'center',
                      borderWidth: 1,
                      borderColor: theme.color.border
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
                        color: theme.dark ? (theme.color.cardForeground as any) : (theme.color.mutedForeground as any),
                        opacity: theme.dark ? 0.85 : 1,
                        fontSize: 13,
                        marginBottom: 4
                      }}>
                        {activity.description}
                      </Text>
                      <Text style={{
                        color: theme.dark ? (theme.color.cardForeground as any) : (theme.color.mutedForeground as any),
                        opacity: theme.dark ? 0.7 : 1,
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
