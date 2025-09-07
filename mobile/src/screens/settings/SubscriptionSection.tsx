import React, { useState } from 'react'
import { View, Text, TouchableOpacity, Alert } from 'react-native'
import { useTheme } from '../../providers/ThemeProvider'
import { Card } from '../../components/ui/Card'
import { Button } from '../../components/ui/Button'
import { Badge } from '../../components/ui/Badge'
import { Modal } from '../../components/ui/Modal'
import { 
  Crown,
  CreditCard,
  Calendar,
  TrendingUp,
  Zap,
  Users,
  MessageCircle,
  Database,
  Bot,
  Shield,
  Star,
  Check,
  ArrowRight
} from 'lucide-react-native'

interface Subscription {
  id: string
  plan: 'starter' | 'pro' | 'enterprise'
  status: 'active' | 'cancelled' | 'expired'
  currentPeriodEnd: string
  nextBillingDate: string
  amount: number
  currency: string
  paymentMethod: {
    type: 'card'
    last4: string
    brand: string
  }
  usage: {
    conversations: { used: number; limit: number }
    agents: { used: number; limit: number }
    apiCalls: { used: number; limit: number }
    storage: { used: number; limit: number } // in GB
  }
}

interface Plan {
  id: 'starter' | 'pro' | 'enterprise'
  name: string
  price: number
  currency: string
  period: string
  description: string
  features: string[]
  limits: {
    conversations: number
    agents: number
    apiCalls: number
    storage: number
  }
  popular?: boolean
}

export const SubscriptionSection: React.FC = () => {
  const { theme } = useTheme()
  const [showUpgradeModal, setShowUpgradeModal] = useState(false)
  
  // Mock subscription data
  const [subscription] = useState<Subscription>({
    id: 'sub_1',
    plan: 'pro',
    status: 'active',
    currentPeriodEnd: '2024-02-15T00:00:00Z',
    nextBillingDate: '2024-02-15T00:00:00Z',
    amount: 29,
    currency: 'USD',
    paymentMethod: {
      type: 'card',
      last4: '4242',
      brand: 'Visa'
    },
    usage: {
      conversations: { used: 156, limit: 1000 },
      agents: { used: 3, limit: 10 },
      apiCalls: { used: 8500, limit: 50000 },
      storage: { used: 2.4, limit: 10 }
    }
  })

  const plans: Plan[] = [
    {
      id: 'starter',
      name: 'Starter',
      price: 9,
      currency: 'USD',
      period: 'month',
      description: 'Perfect for small teams getting started',
      features: [
        'Up to 100 conversations/month',
        '2 AI agents',
        'Basic analytics',
        'Email support',
        '1GB storage'
      ],
      limits: {
        conversations: 100,
        agents: 2,
        apiCalls: 5000,
        storage: 1
      }
    },
    {
      id: 'pro',
      name: 'Pro',
      price: 29,
      currency: 'USD',
      period: 'month',
      description: 'Most popular for growing businesses',
      features: [
        'Up to 1,000 conversations/month',
        '10 AI agents',
        'Advanced analytics',
        'Priority support',
        '10GB storage',
        'Custom integrations',
        'Team collaboration'
      ],
      limits: {
        conversations: 1000,
        agents: 10,
        apiCalls: 50000,
        storage: 10
      },
      popular: true
    },
    {
      id: 'enterprise',
      name: 'Enterprise',
      price: 99,
      currency: 'USD',
      period: 'month',
      description: 'For large organizations with advanced needs',
      features: [
        'Unlimited conversations',
        'Unlimited AI agents',
        'Enterprise analytics',
        'Dedicated support',
        '100GB storage',
        'Custom AI training',
        'SSO & advanced security',
        'API access'
      ],
      limits: {
        conversations: -1, // unlimited
        agents: -1,
        apiCalls: -1,
        storage: 100
      }
    }
  ]

  const currentPlan = plans.find(p => p.id === subscription.plan)!

  const formatDate = (dateString: string) => {
    return new Date(dateString).toLocaleDateString('en-US', {
      year: 'numeric',
      month: 'long',
      day: 'numeric'
    })
  }

  const getUsagePercentage = (used: number, limit: number) => {
    if (limit === -1) return 0 // unlimited
    return Math.min((used / limit) * 100, 100)
  }

  const getUsageColor = (percentage: number) => {
    if (percentage >= 90) return theme.color.error
    if (percentage >= 75) return theme.color.warning
    return theme.color.success
  }

  const formatUsage = (used: number, limit: number, unit: string = '') => {
    if (limit === -1) return `${used.toLocaleString()}${unit} used`
    return `${used.toLocaleString()}${unit} / ${limit.toLocaleString()}${unit}`
  }

  const getPlanIcon = (planId: string) => {
    switch (planId) {
      case 'enterprise': return Crown
      case 'pro': return Star
      default: return Zap
    }
  }

  const getPlanColor = (planId: string) => {
    switch (planId) {
      case 'enterprise': return theme.color.warning
      case 'pro': return theme.color.primary
      default: return theme.color.success
    }
  }

  const handleUpgrade = (planId: string) => {
    Alert.alert(
      'Upgrade Plan',
      `Upgrade to ${plans.find(p => p.id === planId)?.name} plan?`,
      [
        { text: 'Cancel', style: 'cancel' },
        { 
          text: 'Upgrade', 
          onPress: () => {
            setShowUpgradeModal(false)
            Alert.alert('Success', 'Plan upgrade initiated! Changes will take effect immediately.')
          }
        }
      ]
    )
  }

  return (
    <View>
      {/* Current Plan */}
      <Card style={{ marginBottom: 16 }}>
        <View style={{ flexDirection: 'row', alignItems: 'center', gap: 12, marginBottom: 16 }}>
          <View style={{
            width: 44,
            height: 44,
            backgroundColor: getPlanColor(currentPlan.id) + '20',
            borderRadius: 22,
            alignItems: 'center',
            justifyContent: 'center'
          }}>
            {React.createElement(getPlanIcon(currentPlan.id), {
              size: 22,
              color: getPlanColor(currentPlan.id)
            })}
          </View>
          
          <View style={{ flex: 1 }}>
            <View style={{ flexDirection: 'row', alignItems: 'center', gap: 8, marginBottom: 2 }}>
              <Text style={{
                color: theme.color.cardForeground,
                fontSize: 18,
                fontWeight: '700'
              }}>
                {currentPlan.name} Plan
              </Text>
              {currentPlan.popular && (
                <Badge variant="default" size="sm">Popular</Badge>
              )}
            </View>
            <Text style={{
              color: theme.color.mutedForeground,
              fontSize: 14
            }}>
              ${subscription.amount}/{currentPlan.period}
            </Text>
          </View>

          <Badge variant="success" size="sm">
            {subscription.status.toUpperCase()}
          </Badge>
        </View>

        <View style={{
          backgroundColor: theme.color.muted,
          borderRadius: theme.radius.md,
          padding: 12,
          marginBottom: 16
        }}>
          <Text style={{
            color: theme.color.cardForeground,
            fontSize: 14,
            fontWeight: '500',
            marginBottom: 4
          }}>
            Next billing date
          </Text>
          <Text style={{
            color: theme.color.mutedForeground,
            fontSize: 13
          }}>
            {formatDate(subscription.nextBillingDate)} • {subscription.paymentMethod.brand} •••• {subscription.paymentMethod.last4}
          </Text>
        </View>

        <Button
          title="Manage Subscription"
          variant="outline"
          size="md"
          onPress={() => setShowUpgradeModal(true)}
        />
      </Card>

      {/* Usage Stats */}
      <Card style={{ marginBottom: 16 }}>
        <Text style={{
          color: theme.color.cardForeground,
          fontSize: 16,
          fontWeight: '600',
          marginBottom: 16
        }}>
          Current Usage
        </Text>
        
        <View style={{ gap: 16 }}>
          {/* Conversations */}
          <View>
            <View style={{ flexDirection: 'row', justifyContent: 'space-between', alignItems: 'center', marginBottom: 8 }}>
              <View style={{ flexDirection: 'row', alignItems: 'center', gap: 8 }}>
                <MessageCircle size={16} color={theme.color.primary} />
                <Text style={{
                  color: theme.color.cardForeground,
                  fontSize: 14,
                  fontWeight: '500'
                }}>
                  Conversations
                </Text>
              </View>
              <Text style={{
                color: theme.color.mutedForeground,
                fontSize: 12
              }}>
                {formatUsage(subscription.usage.conversations.used, subscription.usage.conversations.limit)}
              </Text>
            </View>
            <View style={{
              height: 6,
              backgroundColor: theme.color.muted,
              borderRadius: 3,
              overflow: 'hidden'
            }}>
              <View style={{
                height: '100%',
                width: `${getUsagePercentage(subscription.usage.conversations.used, subscription.usage.conversations.limit)}%`,
                backgroundColor: getUsageColor(getUsagePercentage(subscription.usage.conversations.used, subscription.usage.conversations.limit))
              }} />
            </View>
          </View>

          {/* Agents */}
          <View>
            <View style={{ flexDirection: 'row', justifyContent: 'space-between', alignItems: 'center', marginBottom: 8 }}>
              <View style={{ flexDirection: 'row', alignItems: 'center', gap: 8 }}>
                <Bot size={16} color={theme.color.success} />
                <Text style={{
                  color: theme.color.cardForeground,
                  fontSize: 14,
                  fontWeight: '500'
                }}>
                  AI Agents
                </Text>
              </View>
              <Text style={{
                color: theme.color.mutedForeground,
                fontSize: 12
              }}>
                {formatUsage(subscription.usage.agents.used, subscription.usage.agents.limit)}
              </Text>
            </View>
            <View style={{
              height: 6,
              backgroundColor: theme.color.muted,
              borderRadius: 3,
              overflow: 'hidden'
            }}>
              <View style={{
                height: '100%',
                width: `${getUsagePercentage(subscription.usage.agents.used, subscription.usage.agents.limit)}%`,
                backgroundColor: getUsageColor(getUsagePercentage(subscription.usage.agents.used, subscription.usage.agents.limit))
              }} />
            </View>
          </View>

          {/* API Calls */}
          <View>
            <View style={{ flexDirection: 'row', justifyContent: 'space-between', alignItems: 'center', marginBottom: 8 }}>
              <View style={{ flexDirection: 'row', alignItems: 'center', gap: 8 }}>
                <TrendingUp size={16} color={theme.color.warning} />
                <Text style={{
                  color: theme.color.cardForeground,
                  fontSize: 14,
                  fontWeight: '500'
                }}>
                  API Calls
                </Text>
              </View>
              <Text style={{
                color: theme.color.mutedForeground,
                fontSize: 12
              }}>
                {formatUsage(subscription.usage.apiCalls.used, subscription.usage.apiCalls.limit)}
              </Text>
            </View>
            <View style={{
              height: 6,
              backgroundColor: theme.color.muted,
              borderRadius: 3,
              overflow: 'hidden'
            }}>
              <View style={{
                height: '100%',
                width: `${getUsagePercentage(subscription.usage.apiCalls.used, subscription.usage.apiCalls.limit)}%`,
                backgroundColor: getUsageColor(getUsagePercentage(subscription.usage.apiCalls.used, subscription.usage.apiCalls.limit))
              }} />
            </View>
          </View>

          {/* Storage */}
          <View>
            <View style={{ flexDirection: 'row', justifyContent: 'space-between', alignItems: 'center', marginBottom: 8 }}>
              <View style={{ flexDirection: 'row', alignItems: 'center', gap: 8 }}>
                <Database size={16} color={theme.color.mutedForeground} />
                <Text style={{
                  color: theme.color.cardForeground,
                  fontSize: 14,
                  fontWeight: '500'
                }}>
                  Storage
                </Text>
              </View>
              <Text style={{
                color: theme.color.mutedForeground,
                fontSize: 12
              }}>
                {formatUsage(subscription.usage.storage.used, subscription.usage.storage.limit, 'GB')}
              </Text>
            </View>
            <View style={{
              height: 6,
              backgroundColor: theme.color.muted,
              borderRadius: 3,
              overflow: 'hidden'
            }}>
              <View style={{
                height: '100%',
                width: `${getUsagePercentage(subscription.usage.storage.used, subscription.usage.storage.limit)}%`,
                backgroundColor: getUsageColor(getUsagePercentage(subscription.usage.storage.used, subscription.usage.storage.limit))
              }} />
            </View>
          </View>
        </View>
      </Card>

      {/* Quick Actions */}
      <Card>
        <Text style={{
          color: theme.color.cardForeground,
          fontSize: 16,
          fontWeight: '600',
          marginBottom: 16
        }}>
          Billing & Plans
        </Text>
        
        <View style={{ gap: 12 }}>
          <TouchableOpacity 
            onPress={() => setShowUpgradeModal(true)}
            style={{
              flexDirection: 'row',
              alignItems: 'center',
              gap: 12,
              paddingVertical: 8
            }}
          >
            <Crown size={16} color={theme.color.warning} />
            <Text style={{
              color: theme.color.cardForeground,
              fontSize: 14,
              flex: 1
            }}>
              Upgrade Plan
            </Text>
            <ArrowRight size={16} color={theme.color.mutedForeground} />
          </TouchableOpacity>
          
          <TouchableOpacity style={{
            flexDirection: 'row',
            alignItems: 'center',
            gap: 12,
            paddingVertical: 8
          }}>
            <CreditCard size={16} color={theme.color.mutedForeground} />
            <Text style={{
              color: theme.color.cardForeground,
              fontSize: 14,
              flex: 1
            }}>
              Payment Methods
            </Text>
            <ArrowRight size={16} color={theme.color.mutedForeground} />
          </TouchableOpacity>
          
          <TouchableOpacity style={{
            flexDirection: 'row',
            alignItems: 'center',
            gap: 12,
            paddingVertical: 8
          }}>
            <Calendar size={16} color={theme.color.mutedForeground} />
            <Text style={{
              color: theme.color.cardForeground,
              fontSize: 14,
              flex: 1
            }}>
              Billing History
            </Text>
            <ArrowRight size={16} color={theme.color.mutedForeground} />
          </TouchableOpacity>
        </View>
      </Card>

      {/* Upgrade Modal */}
      <Modal visible={showUpgradeModal} onClose={() => setShowUpgradeModal(false)} title="Choose Your Plan" size="lg">
        <View style={{ gap: 16 }}>
          {plans.map((plan) => {
            const PlanIcon = getPlanIcon(plan.id)
            const isCurrentPlan = plan.id === subscription.plan
            
            return (
              <Card key={plan.id} style={{
                borderWidth: 2,
                borderColor: plan.popular 
                  ? theme.color.primary 
                  : isCurrentPlan 
                    ? theme.color.success 
                    : theme.color.border
              }}>
                <View style={{ flexDirection: 'row', alignItems: 'flex-start', gap: 12, marginBottom: 12 }}>
                  <View style={{
                    width: 40,
                    height: 40,
                    backgroundColor: getPlanColor(plan.id) + '20',
                    borderRadius: 20,
                    alignItems: 'center',
                    justifyContent: 'center'
                  }}>
                    <PlanIcon size={20} color={getPlanColor(plan.id)} />
                  </View>
                  
                  <View style={{ flex: 1 }}>
                    <View style={{ flexDirection: 'row', alignItems: 'center', gap: 8, marginBottom: 2 }}>
                      <Text style={{
                        color: theme.color.cardForeground,
                        fontSize: 16,
                        fontWeight: '700'
                      }}>
                        {plan.name}
                      </Text>
                      {plan.popular && (
                        <Badge variant="default" size="sm">Popular</Badge>
                      )}
                      {isCurrentPlan && (
                        <Badge variant="success" size="sm">Current</Badge>
                      )}
                    </View>
                    
                    <Text style={{
                      color: theme.color.primary,
                      fontSize: 20,
                      fontWeight: '700',
                      marginBottom: 4
                    }}>
                      ${plan.price}/{plan.period}
                    </Text>
                    
                    <Text style={{
                      color: theme.color.mutedForeground,
                      fontSize: 12,
                      marginBottom: 12
                    }}>
                      {plan.description}
                    </Text>
                  </View>
                </View>

                <View style={{ gap: 6, marginBottom: 16 }}>
                  {plan.features.slice(0, 4).map((feature, index) => (
                    <View key={index} style={{ flexDirection: 'row', alignItems: 'center', gap: 8 }}>
                      <Check size={14} color={theme.color.success} />
                      <Text style={{
                        color: theme.color.cardForeground,
                        fontSize: 13
                      }}>
                        {feature}
                      </Text>
                    </View>
                  ))}
                  {plan.features.length > 4 && (
                    <Text style={{
                      color: theme.color.mutedForeground,
                      fontSize: 12,
                      fontStyle: 'italic',
                      marginLeft: 22
                    }}>
                      +{plan.features.length - 4} more features
                    </Text>
                  )}
                </View>

                <Button
                  title={isCurrentPlan ? 'Current Plan' : `Upgrade to ${plan.name}`}
                  variant={isCurrentPlan ? 'ghost' : plan.popular ? 'premium' : 'outline'}
                  size="md"
                  onPress={() => isCurrentPlan ? null : handleUpgrade(plan.id)}
                  disabled={isCurrentPlan}
                  loading={false}
                />
              </Card>
            )
          })}
        </View>
      </Modal>
    </View>
  )
}
