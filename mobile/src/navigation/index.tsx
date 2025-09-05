import React, { useEffect, useState } from 'react'
import { NavigationContainer } from '@react-navigation/native'
import { createStackNavigator } from '@react-navigation/stack'
import AsyncStorage from '@react-native-async-storage/async-storage'
import { OnboardingPager } from '@/screens/onboarding/OnboardingPager'
import { Home } from '@/screens/home/Home'
import { PricingScreen } from '@/screens/pricing/Pricing'
import { addNotificationResponseListener, initNotifications } from '@/notifications/setup'
import { ChatScreen } from '@/screens/chat/Chat'
import { navigationRef, navigate } from '@/navigation/navRef'

const Stack = createStackNavigator()

// Home screen imported above

export const RootNav: React.FC = () => {
  const [ready, setReady] = useState(false)
  const [showOnboarding, setShowOnboarding] = useState(true)
  useEffect(() => {
    (async () => {
      const v = await AsyncStorage.getItem('onboardingCompleted')
      setShowOnboarding(v !== '1')
      setReady(true)
    })()
    try {
      // Avoid noisy warnings and unsupported behavior inside Expo Go
      // eslint-disable-next-line @typescript-eslint/no-var-requires
      const Constants = require('expo-constants').default
      if (Constants?.appOwnership !== 'expo') {
        initNotifications()
      }
    } catch {}
    const sub = addNotificationResponseListener((route, extras) => {
      if (!route) return
      // If onboarding is showing, close it first then navigate
      if (showOnboarding) {
        setShowOnboarding(false)
        setTimeout(() => {
          if (route === 'chat') navigate('Chat', { agentId: extras?.agentId, initialReply: extras?.userText })
          if (route === 'pricing') navigate('Pricing')
        }, 100)
      } else {
        if (route === 'chat') navigate('Chat', { agentId: extras?.agentId, initialReply: extras?.userText })
        if (route === 'pricing') navigate('Pricing')
      }
    })
    return () => { sub.remove() }
  }, [])
  if (!ready) return null
  const linking = {
    prefixes: ['aisupport://', 'https://yourwebsite.com/app'],
    config: {
      screens: {
        Home: 'home',
        Pricing: 'pricing',
        Chat: 'chat/:agentId'
      }
    }
  } as const
  return (
    <NavigationContainer linking={linking} ref={navigationRef}>
      <Stack.Navigator screenOptions={{ headerShown: false }}>
        {showOnboarding ? (
          <Stack.Screen name="Onboarding">
            {() => <OnboardingPager onComplete={() => setShowOnboarding(false)} />}
          </Stack.Screen>
        ) : (
          <>
            <Stack.Screen name="Home" component={Home} />
            <Stack.Screen name="Pricing" component={PricingScreen} />
            <Stack.Screen name="Chat" component={ChatScreen} />
          </>
        )}
      </Stack.Navigator>
    </NavigationContainer>
  )
}
