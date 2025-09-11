import React from 'react'
import { createBottomTabNavigator } from '@react-navigation/bottom-tabs'
import { Home, MessageCircle, Users, Bot, Settings as SettingsIcon, Workflow, BarChart3, Shield, Globe, HelpCircle } from 'lucide-react-native'
import { useTranslation } from 'react-i18next'
import { useTheme } from '../providers/ThemeProvider'

// Screen imports
import { DashboardScreen } from '../screens/dashboard/Dashboard'
import ConversationsStack from './ConversationsStack'
import CRMStack from './CRMStack'
import AgentsStack from './AgentsStack'
import ChannelsStack from './ChannelsStack'
import KnowledgeStack from './KnowledgeStack'
import AutomationsStack from './AutomationsStack'
import AnalyticsStack from './AnalyticsStack'
import SettingsStack from './SettingsStack'
import PrivacyCenter from '../screens/security/PrivacyCenter'
import SecurityHome from '../screens/Security/SecurityHome'
import PortalPreview from '../screens/Portal/PortalPreview'
import ActionsStack from './ActionsStack'
import { HelpButton } from '../components/help'
import { useNavigation } from '@react-navigation/native'

const Tab = createBottomTabNavigator()

export const TabNavigator: React.FC = () => {
  const { t } = useTranslation()
  const { theme } = useTheme()
  const navigation = useNavigation<any>()

  return (
    <>
    <Tab.Navigator
      screenOptions={{
        headerShown: false,
        tabBarStyle: {
          backgroundColor: theme.color.card,
          borderTopColor: 'transparent',
          borderTopWidth: 0,
          height: 85,
          paddingBottom: 10,
          paddingTop: 10,
        },
        tabBarActiveTintColor: theme.color.primary,
        tabBarInactiveTintColor: theme.color.mutedForeground,
        tabBarLabelStyle: {
          fontSize: 12,
          fontWeight: '600',
          marginTop: 4,
        },
        tabBarIconStyle: {
          marginTop: 4,
        },
        tabBarLongPress: () => {
          // Open command palette on long-press anywhere on tab bar
          // @ts-ignore
          const { DeviceEventEmitter } = require('react-native')
          DeviceEventEmitter.emit('palette.open')
        }
      }}
    >
      <Tab.Screen
        name="Dashboard"
        component={DashboardScreen}
        options={{
          title: t('nav.dashboard'),
          tabBarIcon: ({ color, size }) => <Home color={color} size={size} />
        }}
      />
      <Tab.Screen
        name="Conversations"
        component={ConversationsStack}
        options={{
          title: t('nav.conversations'),
          tabBarIcon: ({ color, size }) => <MessageCircle color={color} size={size} />
        }}
      />
      <Tab.Screen
        name="CRM"
        component={CRMStack}
        options={{
          title: t('nav.crm'),
          tabBarIcon: ({ color, size }) => <Users color={color} size={size} />
        }}
      />
      <Tab.Screen
        name="Agents"
        component={AgentsStack}
        options={{
          title: t('nav.agents'),
          tabBarIcon: ({ color, size }) => <Bot color={color} size={size} />
        }}
      />
      <Tab.Screen
        name="Channels"
        component={ChannelsStack}
        options={{
          title: t('nav.channels', { defaultValue: 'Channels' }),
          tabBarIcon: ({ color, size }) => <MessageCircle color={color} size={size} />
        }}
      />
      <Tab.Screen
        name="Knowledge"
        component={KnowledgeStack}
        options={{
          title: t('nav.knowledge', { defaultValue: 'Knowledge' }),
          tabBarIcon: ({ color, size }) => <MessageCircle color={color} size={size} />
        }}
      />
      <Tab.Screen
        name="Automations"
        component={AutomationsStack}
        options={{
          title: 'Automations',
          tabBarIcon: ({ color, size }) => <Workflow color={color} size={size} />
        }}
      />
      <Tab.Screen
        name="Analytics"
        component={AnalyticsStack}
        options={{
          title: 'Analytics',
          tabBarIcon: ({ color, size }) => <BarChart3 color={color} size={size} />
        }}
      />
      <Tab.Screen
        name="Actions"
        component={ActionsStack}
        options={{
          title: 'Actions',
          tabBarIcon: ({ color, size }) => <Workflow color={color} size={size} />
        }}
      />
      <Tab.Screen
        name="Security"
        component={SecurityHome}
        options={{
          title: 'Security',
          tabBarIcon: ({ color, size }) => <Shield color={color} size={size} />
        }}
      />
      <Tab.Screen
        name="Portal"
        component={PortalPreview}
        options={{
          title: 'Portal',
          tabBarIcon: ({ color, size }) => <Globe color={color} size={size} />
        }}
      />
      <Tab.Screen
        name="Settings"
        component={SettingsStack}
        options={{
          title: t('nav.settings'),
          tabBarIcon: ({ color, size }) => <SettingsIcon color={color} size={size} />
        }}
      />
    </Tab.Navigator>
    {/* Floating Help */}
    <HelpButton onPress={() => navigation.navigate('HelpCenter')} />
    </>
  )
}
