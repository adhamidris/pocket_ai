import React, { useState } from 'react'
import { View, Text, SafeAreaView, ScrollView, TouchableOpacity, Alert } from 'react-native'
import { useSafeAreaInsets } from 'react-native-safe-area-context'
import { useTranslation } from 'react-i18next'
import { useTheme } from '../../providers/ThemeProvider'
import { Card } from '../../components/ui/Card'
import { Button } from '../../components/ui/Button'
import { ProfileSection } from './ProfileSection'
import { SubscriptionSection } from './SubscriptionSection'
import { 
  User, 
  Bell, 
  Shield, 
  HelpCircle, 
  LogOut,
  CreditCard,
  Globe,
  FileText,
  Info,
  ChevronRight,
  Star,
  MessageCircle
} from 'lucide-react-native'

export const SettingsScreen: React.FC = () => {
  const { t } = useTranslation()
  const { theme, isDark, toggle } = useTheme()
  const insets = useSafeAreaInsets()
  const [activeSection, setActiveSection] = useState<'profile' | 'subscription' | 'main'>('main')

  const handleSignOut = () => {
    Alert.alert(
      'Sign Out',
      'Are you sure you want to sign out?',
      [
        { text: 'Cancel', style: 'cancel' },
        { 
          text: 'Sign Out', 
          style: 'destructive',
          onPress: () => Alert.alert('Signed Out', 'You have been signed out successfully.')
        }
      ]
    )
  }

  const settingsGroups = [
    {
      title: t('settings.sections.account'),
      items: [
        { 
          icon: User, 
          label: t('settings.account.profile'), 
          subtitle: 'Manage your personal information',
          onPress: () => setActiveSection('profile')
        },
        { 
          icon: CreditCard, 
          label: t('settings.subscription.currentPlan'), 
          subtitle: 'Pro Plan • $29/month',
          badge: 'Pro',
          onPress: () => setActiveSection('subscription')
        },
        { 
          icon: Bell, 
          label: t('settings.sections.notifications'), 
          subtitle: 'Configure notification preferences',
          onPress: () => Alert.alert('Notifications', 'Notification settings coming soon!')
        },
      ]
    },
    {
      title: t('settings.sections.preferences'),
      items: [
        { 
          icon: Globe, 
          label: t('settings.sections.language'), 
          subtitle: 'English (US)',
          onPress: () => Alert.alert('Language', 'Language settings coming soon!')
        },
      ]
    },
    {
      title: t('settings.sections.support'),
      items: [
        { 
          icon: HelpCircle, 
          label: t('settings.sections.help'), 
          subtitle: 'Get help and support',
          onPress: () => Alert.alert('Help', 'Help center coming soon!')
        },
        { 
          icon: Shield, 
          label: t('settings.sections.privacy'), 
          subtitle: 'Privacy policy and terms',
          onPress: () => Alert.alert('Privacy', 'Privacy settings coming soon!')
        },
        { 
          icon: FileText, 
          label: 'Documentation', 
          subtitle: 'API docs and guides',
          onPress: () => Alert.alert('Documentation', 'Documentation coming soon!')
        },
        { 
          icon: Star, 
          label: 'Rate App', 
          subtitle: 'Rate us on the App Store',
          onPress: () => Alert.alert('Rate App', 'Thank you for your feedback!')
        },
        { 
          icon: MessageCircle, 
          label: 'Contact Support', 
          subtitle: 'Get in touch with our team',
          onPress: () => Alert.alert('Contact', 'Support chat coming soon!')
        },
      ]
    },
    {
      title: 'App Info',
      items: [
        { 
          icon: Info, 
          label: 'About', 
          subtitle: 'Version 1.0.0 (Build 1)',
          onPress: () => Alert.alert('About', 'Perfect UI Mobile App v1.0.0')
        },
      ]
    }
  ]

  // Render different sections
  if (activeSection === 'profile') {
    return (
      <SafeAreaView style={{ flex: 1, backgroundColor: theme.color.background }}>
        <ScrollView style={{ flex: 1 }} contentContainerStyle={{ paddingBottom: insets.bottom }}>
          {/* Header */}
          <View style={{ paddingHorizontal: 24, paddingTop: 12, paddingBottom: 16 }}>
            <TouchableOpacity 
              onPress={() => setActiveSection('main')}
              style={{
                flexDirection: 'row',
                alignItems: 'center',
                marginBottom: 16
              }}
            >
              <Text style={{
                color: theme.color.primary,
                fontSize: 16,
                fontWeight: '500'
              }}>
                ← Back to Settings
              </Text>
            </TouchableOpacity>
            
            <Text style={{
              color: theme.color.foreground,
              fontSize: 32,
              fontWeight: '700',
              marginBottom: 8
            }}>
              Profile
            </Text>
            <Text style={{
              color: theme.color.mutedForeground,
              fontSize: 16
            }}>
              Manage your account and preferences
            </Text>
          </View>

          <View style={{ paddingHorizontal: 24 }}>
            <ProfileSection onThemeToggle={toggle} isDark={isDark} />
          </View>
        </ScrollView>
      </SafeAreaView>
    )
  }

  if (activeSection === 'subscription') {
    return (
      <SafeAreaView style={{ flex: 1, backgroundColor: theme.color.background }}>
        <ScrollView style={{ flex: 1 }} contentContainerStyle={{ paddingBottom: insets.bottom }}>
          {/* Header */}
          <View style={{ paddingHorizontal: 24, paddingTop: 12, paddingBottom: 16 }}>
            <TouchableOpacity 
              onPress={() => setActiveSection('main')}
              style={{
                flexDirection: 'row',
                alignItems: 'center',
                marginBottom: 16
              }}
            >
              <Text style={{
                color: theme.color.primary,
                fontSize: 16,
                fontWeight: '500'
              }}>
                ← Back to Settings
              </Text>
            </TouchableOpacity>
            
            <Text style={{
              color: theme.color.foreground,
              fontSize: 32,
              fontWeight: '700',
              marginBottom: 8
            }}>
              Subscription
            </Text>
            <Text style={{
              color: theme.color.mutedForeground,
              fontSize: 16
            }}>
              Manage your plan and billing
            </Text>
          </View>

          <View style={{ paddingHorizontal: 24 }}>
            <SubscriptionSection />
          </View>
        </ScrollView>
      </SafeAreaView>
    )
  }

  // Main settings screen
  return (
    <SafeAreaView style={{ flex: 1, backgroundColor: theme.color.background }}>
      <ScrollView style={{ flex: 1 }} contentContainerStyle={{ paddingBottom: insets.bottom }}>
        {/* Header */}
        <View style={{ paddingHorizontal: 24, paddingTop: 12, paddingBottom: 16 }}>
          <Text style={{
            color: theme.color.foreground,
            fontSize: 32,
            fontWeight: '700',
            marginBottom: 8
          }}>
            {t('settings.title')}
          </Text>
          {/* Removed subtitle due to missing i18n key */}
        </View>

        {/* Settings Groups */}
        <View style={{ paddingHorizontal: 24, gap: 24 }}>
          {settingsGroups.map((group, groupIndex) => (
            <View key={groupIndex}>
              <Text style={{
                color: theme.color.foreground,
                fontSize: 18,
                fontWeight: '600',
                marginBottom: 12
              }}>
                {group.title}
              </Text>
              
              <Card
                variant="flat"
                style={{
                  backgroundColor: theme.dark ? (theme.color.secondary as any) : (theme.color.accent as any),
                  paddingHorizontal: 12,
                  paddingVertical: 8
                }}
              >
                {group.items.map((item, itemIndex) => (
                  <TouchableOpacity
                    key={itemIndex}
                    onPress={item.onPress}
                    style={{
                      flexDirection: 'row',
                      alignItems: 'center',
                      paddingVertical: 12,
                      borderBottomWidth: itemIndex < group.items.length - 1 ? 1 : 0,
                      borderBottomColor: theme.color.border
                    }}
                  >
                    <View style={{
                      width: 36,
                      height: 36,
                      backgroundColor: theme.dark ? theme.color.secondary : theme.color.card,
                      borderRadius: 18,
                      alignItems: 'center',
                      justifyContent: 'center',
                      marginRight: 10
                    }}>
                      <item.icon color={theme.color.mutedForeground} size={20} />
                    </View>
                    
                    <View style={{ flex: 1 }}>
                      <View style={{ flexDirection: 'row', alignItems: 'center', gap: 8, marginBottom: 2 }}>
                        <Text style={{
                          color: theme.color.cardForeground,
                          fontSize: 16,
                          fontWeight: '500'
                        }}>
                          {item.label}
                        </Text>
                        {item.badge && (
                          <View style={{
                            backgroundColor: theme.dark ? theme.color.secondary : theme.color.card,
                            paddingHorizontal: 6,
                            paddingVertical: 2,
                            borderRadius: 4
                          }}>
                            <Text style={{
                              color: theme.color.mutedForeground,
                              fontSize: 10,
                              fontWeight: '600'
                            }}>
                              {item.badge}
                            </Text>
                          </View>
                        )}
                      </View>
                      {item.subtitle && (
                        <Text style={{
                          color: theme.color.mutedForeground,
                          fontSize: 13
                        }}>
                          {item.subtitle}
                        </Text>
                      )}
                    </View>

                    <ChevronRight size={16} color={theme.color.mutedForeground} />
                  </TouchableOpacity>
                ))}
              </Card>
            </View>
          ))}
        </View>

        {/* Sign Out */}
        <View style={{ paddingHorizontal: 24, paddingVertical: 12 }}>
          <TouchableOpacity
            onPress={handleSignOut}
            style={{
              flexDirection: 'row',
              alignItems: 'center',
              justifyContent: 'center',
              gap: 12,
              backgroundColor: theme.color.card,
              borderWidth: 0,
              borderColor: 'transparent',
              borderRadius: theme.radius.lg,
              paddingVertical: 16,
              paddingHorizontal: 24
            }}
          >
            <LogOut size={20} color={theme.color.mutedForeground} />
            <Text style={{
              color: theme.color.mutedForeground,
              fontSize: 16,
              fontWeight: '600'
            }}>
              Sign Out
            </Text>
          </TouchableOpacity>
        </View>
      </ScrollView>
    </SafeAreaView>
  )
}
