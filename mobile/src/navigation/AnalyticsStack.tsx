import React from 'react'
import { createStackNavigator } from '@react-navigation/stack'
import AnalyticsHome from '../screens/Analytics/AnalyticsHome'
import TrendsScreen from '../screens/Analytics/TrendsScreen'

const Stack = createStackNavigator()

const AnalyticsStack: React.FC = () => {
  return (
    <Stack.Navigator screenOptions={{ headerShown: false }}>
      <Stack.Screen name="AnalyticsHome" component={AnalyticsHome} />
      <Stack.Screen name="Trends" component={TrendsScreen} />
    </Stack.Navigator>
  )
}

export default AnalyticsStack


