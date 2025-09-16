import React from 'react'
import { createBottomTabNavigator } from '@react-navigation/bottom-tabs'
import { Home, MessageCircle, Users, Bot, Settings } from 'lucide-react-native'
import { useTranslation } from 'react-i18next'
import { useTheme } from '../providers/ThemeProvider'

// Screen imports
import { DashboardScreen } from '../screens/dashboard/Dashboard'
import { ConversationsScreen } from '../screens/conversations/Conversations'
import { CRMScreen } from '../screens/crm/CRM'
import { AgentsScreen } from '../screens/agents/Agents'
import { SettingsScreen } from '../screens/settings/Settings'

const Tab = createBottomTabNavigator()

export const TabNavigator: React.FC = () => {
  const { t } = useTranslation()
  const { theme } = useTheme()

  return (
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
          paddingHorizontal: 12,
        },
        tabBarActiveTintColor: theme.color.primary,
        tabBarInactiveTintColor: theme.color.mutedForeground,
        tabBarItemStyle: {
          paddingHorizontal: 8,
        },
        tabBarLabelStyle: {
          fontSize: 12,
          fontWeight: '600',
          marginTop: 4,
        },
        tabBarIconStyle: {
          marginTop: 4,
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
        component={ConversationsScreen}
        options={{
          title: t('nav.conversations'),
          tabBarIcon: ({ color, size }) => <MessageCircle color={color} size={size} />
        }}
      />
      <Tab.Screen
        name="CRM"
        component={CRMScreen}
        options={{
          title: t('nav.crm'),
          tabBarIcon: ({ color, size }) => <Users color={color} size={size} />
        }}
      />
      <Tab.Screen
        name="Agents"
        component={AgentsScreen}
        options={{
          title: t('nav.agents'),
          tabBarIcon: ({ color, size }) => <Bot color={color} size={size} />
        }}
      />
      <Tab.Screen
        name="Settings"
        component={SettingsScreen}
        options={{
          title: t('nav.settings'),
          tabBarIcon: ({ color, size }) => <Settings color={color} size={size} />
        }}
      />
    </Tab.Navigator>
  )
}