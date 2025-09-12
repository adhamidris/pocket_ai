import React, { useEffect, useState } from 'react'
import { NavigationContainer } from '@react-navigation/native'
import { createStackNavigator } from '@react-navigation/stack'
import AsyncStorage from '@react-native-async-storage/async-storage'
import { StatusBar } from 'expo-status-bar'
import { useTheme } from '../providers/ThemeProvider'

// Screens
import { OnboardingNavigator } from './OnboardingNavigator'
import { TabNavigator } from './TabNavigator'

const Stack = createStackNavigator()

export const RootNavigator: React.FC = () => {
  const { theme } = useTheme()
  const [isReady, setIsReady] = useState(false)
  const [showOnboarding, setShowOnboarding] = useState(true)
  const ALWAYS_SHOW_ONBOARDING = true // set to false after testing

  useEffect(() => {
    (async () => {
      try {
        const completed = await AsyncStorage.getItem('onboardingCompleted')
        setShowOnboarding(ALWAYS_SHOW_ONBOARDING || completed !== '1')
      } catch (error) {
        console.log('Error checking onboarding status:', error)
      } finally {
        setIsReady(true)
      }
    })()
  }, [])

  if (!isReady) {
    return null // You could add a loading screen here
  }

  return (
    <NavigationContainer>
      <StatusBar style={theme.dark ? 'light' : 'dark'} backgroundColor={theme.color.background} />
      <Stack.Navigator screenOptions={{ headerShown: false }}>
        {showOnboarding ? (
          <Stack.Screen name="Onboarding">
            {() => (
              <OnboardingNavigator onComplete={() => setShowOnboarding(false)} />
            )}
          </Stack.Screen>
        ) : (
          <Stack.Screen name="Main" component={TabNavigator} />
        )}
      </Stack.Navigator>
    </NavigationContainer>
  )
}
