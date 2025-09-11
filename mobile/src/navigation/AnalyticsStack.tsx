import React from 'react'
import { createStackNavigator } from '@react-navigation/stack'
import AnalyticsHome from '../screens/Analytics/AnalyticsHome'
import TrendsScreen from '../screens/Analytics/TrendsScreen'
import SavedReports from '../screens/Analytics/SavedReports'
import ExportScreen from '../screens/Analytics/ExportScreen'
import Definitions from '../screens/Analytics/Definitions'
import DailyBriefScreen from '../screens/Assistant/DailyBriefScreen'

const Stack = createStackNavigator()

const AnalyticsStack: React.FC = () => {
  return (
    <Stack.Navigator screenOptions={{ headerShown: false }}>
      <Stack.Screen name="AnalyticsHome" component={AnalyticsHome} />
      <Stack.Screen name="Trends" component={TrendsScreen} />
      <Stack.Screen name="SavedReports" component={SavedReports} />
      <Stack.Screen name="Export" component={ExportScreen} />
      <Stack.Screen name="Definitions" component={Definitions} />
      <Stack.Screen name="DailyBrief" component={DailyBriefScreen} />
    </Stack.Navigator>
  )
}

export default AnalyticsStack


