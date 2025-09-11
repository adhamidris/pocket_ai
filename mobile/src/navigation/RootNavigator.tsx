import React, { useEffect, useState } from 'react'
import { NavigationContainer } from '@react-navigation/native'
import { createStackNavigator } from '@react-navigation/stack'
import AsyncStorage from '@react-native-async-storage/async-storage'
import { StatusBar } from 'expo-status-bar'
import { useTheme } from '../providers/ThemeProvider'
import AssistantFab from '../components/assistant/AssistantFab'
import AssistantOverlay from '../screens/Assistant/AssistantOverlay'
import { DeviceEventEmitter } from 'react-native'
import HelpCenter from '../screens/Help/HelpCenter'
import TourRunner from '../screens/Help/TourRunner'
import CommandPaletteOverlay from '../screens/Help/CommandPaletteOverlay'
import WhatsNew from '../screens/Help/WhatsNew'
import SurveyManager from '../screens/Help/SurveyManager'
import FeedbackScreen from '../screens/Help/FeedbackScreen'
import ComponentGallery from '../screens/QA/ComponentGallery'
import DeepLinkAudit from '../screens/QA/DeepLinkAudit'
import AccessibilityAudit from '../screens/QA/AccessibilityAudit'
import I18nAudit from '../screens/QA/I18nAudit'
import ThemeAudit from '../screens/QA/ThemeAudit'
import OfflineAudit from '../screens/QA/OfflineAudit'
import PerfAudit from '../screens/QA/PerfAudit'
import StateCoverageAudit from '../screens/QA/StateCoverageAudit'
import TelemetryAudit from '../screens/QA/TelemetryAudit'
import EntitlementAudit from '../screens/QA/EntitlementAudit'
import SecPrivacyAudit from '../screens/QA/SecPrivacyAudit'
import ScriptedWalks from '../screens/QA/ScriptedWalks'
import RC_Preflight from '../screens/QA/RC_Preflight'
import LaunchRollout from '../screens/QA/LaunchRollout'

// Screens
import { OnboardingNavigator } from './OnboardingNavigator'
import { TabNavigator } from './TabNavigator'

const Stack = createStackNavigator()

export const RootNavigator: React.FC = () => {
  const { theme } = useTheme()
  const [isReady, setIsReady] = useState(false)
  const [showOnboarding, setShowOnboarding] = useState(true)
  const [assistantOpen, setAssistantOpen] = useState(false)
  const [assistantPrefill, setAssistantPrefill] = useState<{ text?: string; persona?: any } | undefined>()
  const [paletteOpen, setPaletteOpen] = useState(false)
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

  useEffect(() => {
    const sub = DeviceEventEmitter.addListener('assistant.open', (payload: any) => {
      setAssistantOpen(true)
      setAssistantPrefill({ text: payload?.text, persona: payload?.persona })
    })
    const sub2 = DeviceEventEmitter.addListener('palette.open', () => setPaletteOpen(true))
    return () => { sub.remove(); sub2.remove() }
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
        <Stack.Screen name="HelpCenter" component={HelpCenter} />
        <Stack.Screen name="TourRunner" component={TourRunner} />
        <Stack.Screen name="WhatsNew" component={WhatsNew} />
        <Stack.Screen name="Feedback" component={FeedbackScreen} />
        {__DEV__ && <Stack.Screen name="ComponentGallery" component={ComponentGallery} />}
        {__DEV__ && <Stack.Screen name="DeepLinkAudit" component={DeepLinkAudit} />}
        {__DEV__ && <Stack.Screen name="AccessibilityAudit" component={AccessibilityAudit} />}
        {__DEV__ && <Stack.Screen name="I18nAudit" component={I18nAudit} />}
        {__DEV__ && <Stack.Screen name="ThemeAudit" component={ThemeAudit} />}
        {__DEV__ && <Stack.Screen name="OfflineAudit" component={OfflineAudit} />}
        {__DEV__ && <Stack.Screen name="PerfAudit" component={PerfAudit} />}
        {__DEV__ && <Stack.Screen name="StateCoverageAudit" component={StateCoverageAudit} />}
        {__DEV__ && <Stack.Screen name="TelemetryAudit" component={TelemetryAudit} />}
        {__DEV__ && <Stack.Screen name="EntitlementAudit" component={EntitlementAudit} />}
        {__DEV__ && <Stack.Screen name="SecPrivacyAudit" component={SecPrivacyAudit} />}
        {__DEV__ && <Stack.Screen name="ScriptedWalks" component={ScriptedWalks} />}
        {__DEV__ && <Stack.Screen name="RC_Preflight" component={RC_Preflight} />}
        {__DEV__ && <Stack.Screen name="LaunchRollout" component={LaunchRollout} />}
      </Stack.Navigator>
      {/* Global Assistant */}
      {!showOnboarding && (
        <>
          <AssistantFab onPress={() => setAssistantOpen(true)} />
          <AssistantOverlay visible={assistantOpen} onClose={() => setAssistantOpen(false)} prefill={assistantPrefill as any} />
          <CommandPaletteOverlay visible={paletteOpen} onClose={() => setPaletteOpen(false)} />
          <SurveyManager />
        </>
      )}
    </NavigationContainer>
  )
}
